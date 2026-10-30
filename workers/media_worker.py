"""
Media Worker — Bộ xử lý đa phương tiện cho video YouTube.

Worker chạy liên tục, poll RabbitMQ để lấy video cần xử lý:
- Whisper: Gửi audio tới Whisper API để chuyển giọng nói thành văn bản
- OCR: Trích xuất text từ các frame ảnh
- Vision: Phân tích sâu frame chứa code hoặc lỗi
- Push message tới knowledge queue khi hoàn tất
- Dọn dẹp file tạm sau khi xử lý xong

Entry point: run_media_worker()
"""

import json
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx
import pika
import pika.exceptions
import pika.spec

# Đảm bảo project root nằm trong sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.config import config
from utils.logger import get_logger
from core.ocr_engine import extract_text_from_frame
from core.ai_analyzer import analyze_frame_vision
from core.media_processor import cleanup_temp_files, TEMP_MEDIA_DIR
from workers.rabbitmq_utils import connect_rabbitmq, ensure_connection, Channel
from database.connection import init_db
from database.queries import (
    get_unprocessed_frames,
    save_ocr_results_batch,
    mark_frames_processed_batch,
    save_transcript,
    create_job,
    update_job_status,
)

logger = get_logger(__name__)

# Cờ báo hiệu tắt worker gracefully
_shutdown_requested = False

# Xử lý 1 video tại một thời điểm (mỗi video mất khá lâu)
BATCH_SIZE = 1


def _signal_handler(signum: int, frame: Any) -> None:
    """Xử lý tín hiệu SIGINT/SIGTERM để tắt worker an toàn."""
    global _shutdown_requested
    logger.info("🛑 Nhận tín hiệu tắt (signal=%s). Đang dừng Media Worker...", signum)
    _shutdown_requested = True


# Các queue Media Worker cần: media (consume) + knowledge (publish)
_MEDIA_QUEUES = [
    getattr(config, "RABBITMQ_MEDIA_QUEUE", "video_media"),
    getattr(config, "RABBITMQ_KNOWLEDGE_QUEUE", "video_knowledge"),
]


def _connect_rabbitmq() -> tuple[pika.BlockingConnection, Channel]:
    """Kết nối RabbitMQ qua helper chung. Thoát tiến trình nếu thất bại lúc khởi động."""
    try:
        return connect_rabbitmq(declare_queues=_MEDIA_QUEUES, heartbeat=0)
    except pika.exceptions.AMQPConnectionError:
        logger.critical("❌ Không thể kết nối RabbitMQ. Thoát Media Worker.")
        sys.exit(1)


def _ensure_rabbitmq_connection(
    connection: pika.BlockingConnection | None,
    channel: Channel | None,
) -> tuple[pika.BlockingConnection, Channel]:
    """Kiểm tra và tái kết nối RabbitMQ nếu connection/channel bị đứt."""
    return ensure_connection(connection, channel, declare_queues=_MEDIA_QUEUES, heartbeat=0)


# ─────────────────────────────────────────────────────────────────
# WHISPER TRANSCRIPTION
# ─────────────────────────────────────────────────────────────────

def _process_whisper(video_id: str) -> bool:
    """Gửi audio tới Whisper API để chuyển giọng nói thành văn bản.

    Đọc file audio.wav từ temp_media/{video_id}/ và gửi tới
    Whisper API endpoint qua multipart file upload.

    Args:
        video_id: ID video YouTube.

    Returns:
        True nếu transcription thành công, False nếu thất bại hoặc bỏ qua.
    """
    whisper_url = config.WHISPER_API_URL
    if not whisper_url:
        logger.warning("⚠️ WHISPER_API_URL chưa được cấu hình. Bỏ qua Whisper.")
        return False

    audio_path = TEMP_MEDIA_DIR / video_id / "audio.wav"
    if not audio_path.exists():
        logger.warning("⚠️ Không tìm thấy file audio: %s. Bỏ qua Whisper.", audio_path)
        return False

    transcribe_endpoint = f"{whisper_url.rstrip('/')}/transcribe"
    logger.info("🎤 Đang gửi audio tới Whisper API: %s", transcribe_endpoint)

    try:
        with open(audio_path, "rb") as f:
            files = {"file": ("audio.wav", f, "audio/wav")}
            # Timeout 600s (10 phút) cho video dài
            with httpx.Client(timeout=600.0) as client:
                response = client.post(transcribe_endpoint, files=files)
                response.raise_for_status()

        result = response.json()
        logger.info("✅ Whisper API trả về transcript thành công cho video %s", video_id)

        # Chuẩn bị dữ liệu transcript để lưu DB
        transcript_data = {
            "source": "whisper",
            "language": result.get("language", "vi"),
            "raw_text": result.get("text", ""),
            "segments": result.get("segments", []),
            "duration_sec": result.get("duration"),
        }
        saved = save_transcript(video_id, transcript_data)
        if saved:
            logger.info("📝 Đã lưu Whisper transcript cho video %s", video_id)
        return saved

    except httpx.TimeoutException:
        logger.error("❌ Whisper API timeout sau 600s cho video %s", video_id)
        return False
    except httpx.HTTPStatusError as e:
        logger.error("❌ Whisper API lỗi HTTP %d cho video %s: %s", e.response.status_code, video_id, e)
        return False
    except Exception as e:
        logger.error("❌ Lỗi không xác định khi gọi Whisper API: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────
# OCR / VISION PROCESSING
# ─────────────────────────────────────────────────────────────────

def _save_debug_frame(video_id: str, file_path: str, ocr_result: dict) -> None:
    """Vẽ bbox/nhãn CODE lên frame và lưu vào temp_media/{video_id}/output/.

    Chỉ dùng cho mục đích debug (bật qua config.DEBUG_OCR). Không gọi trong
    luồng xử lý bình thường vì imread + imwrite mỗi frame rất tốn I/O.
    """
    import cv2

    out_dir = TEMP_MEDIA_DIR / video_id / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    img = cv2.imread(file_path)
    if img is None:
        return

    contains_code = ocr_result.get("contains_code")
    status_txt = f"CODE: {'YES' if contains_code else 'NO'}"
    cv2.putText(
        img, status_txt, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1,
        (0, 0, 255) if contains_code else (255, 0, 0), 2,
    )

    for region in ocr_result.get("regions", []):
        bbox = region["bbox"]
        p1 = (int(bbox[0][0]), int(bbox[0][1]))
        p2 = (int(bbox[2][0]), int(bbox[2][1]))
        cv2.rectangle(img, p1, p2, (0, 255, 0), 2)

    out_path = out_dir / f"debug_{Path(file_path).name}"
    cv2.imwrite(str(out_path), img)


def _process_frames(video_id: str) -> int:
    """Chạy OCR và Vision analysis cho tất cả frames chưa xử lý của video.

    Quy trình cho mỗi frame:
    1. Chạy OCR (extract_text_from_frame) để lấy text
    2. Lưu kết quả OCR vào DB
    3. Đánh dấu frame đã xử lý
    4. Nếu frame chứa code hoặc text có chứa 'error' → chạy Vision analysis

    Args:
        video_id: ID video YouTube.

    Returns:
        Số lượng frames đã xử lý thành công.
    """
    frames = get_unprocessed_frames(video_id)
    if not frames:
        logger.info("📭 Không có frames chưa xử lý cho video %s", video_id)
        return 0

    total = len(frames)
    logger.info("🖼️ Bắt đầu OCR/Vision cho %d frames (video: %s)", total, video_id)

    # Lọc frame còn file trên đĩa; frame thiếu file vẫn đánh dấu processed.
    valid_frames = [f for f in frames if f.get("file_path") and Path(f["file_path"]).exists()]
    missing_ids = [f["id"] for f in frames if f not in valid_frames]
    if missing_ids:
        logger.warning("⚠️ %d frame thiếu file, sẽ đánh dấu processed và bỏ qua.", len(missing_ids))

    # ── Giai đoạn 1: OCR SONG SONG (ThreadPoolExecutor) ──
    # ONNX Runtime nhả GIL khi inference nên thread giúp che lấp độ trễ đọc ảnh
    # và tận dụng nhiều nhân CPU. Instance OCR được bảo vệ bằng lock ở tầng engine.
    max_workers = max(1, getattr(config, "OCR_MAX_WORKERS", 4))
    ocr_by_frame_id: dict[int, dict] = {}
    completed = 0

    def _run_ocr(frame: dict) -> tuple[int, dict]:
        return frame["id"], extract_text_from_frame(frame["file_path"])

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_run_ocr, f): f for f in valid_frames}
        for future in as_completed(future_map):
            if _shutdown_requested:
                logger.info("🛑 Shutdown requested. Dừng OCR song song.")
                break
            frame = future_map[future]
            try:
                _, ocr_result = future.result()
            except Exception as e:
                logger.error("❌ Lỗi OCR frame %d: %s", frame["id"], e)
                ocr_result = {}
            ocr_by_frame_id[frame["id"]] = ocr_result

            if ocr_result and getattr(config, "DEBUG_OCR", False):
                _save_debug_frame(video_id, frame["file_path"], ocr_result)

            completed += 1
            if completed % 20 == 0 or completed == len(valid_frames):
                logger.info("📊 Tiến trình OCR: %d/%d frames", completed, len(valid_frames))

    # ── Giai đoạn 2: Vision (theo thứ tự để Cooldown đúng) + gom batch DB ──
    ocr_batch: list[dict] = []
    processed_ids: list[int] = list(missing_ids)
    last_vision_frame_idx = -999  # Cooldown tracker cho Vision API

    for idx, frame in enumerate(valid_frames, start=1):
        frame_id = frame["id"]
        ocr_result = ocr_by_frame_id.get(frame_id)
        if ocr_result is None:  # bị dừng giữa chừng do shutdown
            continue

        ocr_batch.append({"frame_id": frame_id, "video_id": video_id, "ocr_data": ocr_result})
        processed_ids.append(frame_id)

        # Confidence Router: quyết định có gọi Vision analysis sâu không
        detected_text = ocr_result.get("detected_text", "").lower()
        contains_code = ocr_result.get("contains_code", False)
        avg_confidence = ocr_result.get("confidence", 0.0)

        needs_vision = False
        if contains_code or "error" in detected_text:
            if idx - last_vision_frame_idx >= 15:  # Cooldown 15 frame/cảnh, tránh spam API
                needs_vision = True
                last_vision_frame_idx = idx
        elif 0.0 < avg_confidence < 0.6 and len(detected_text) > 10:
            if idx - last_vision_frame_idx >= 15:
                needs_vision = True
                last_vision_frame_idx = idx

        if needs_vision:
            try:
                vision_result = analyze_frame_vision(frame["file_path"])
                if vision_result:
                    logger.info(
                        "🧠 Vision analysis hoàn tất cho frame %d: %s",
                        frame_id, vision_result.get("summary", "N/A")[:100],
                    )
            except Exception as ve:
                logger.warning("⚠️ Vision analysis thất bại cho frame %d: %s", frame_id, ve)

    # ── Ghi DB theo BATCH (2 round-trip thay vì 2N) ──
    saved = save_ocr_results_batch(ocr_batch)
    mark_frames_processed_batch(processed_ids)

    logger.info("✅ Hoàn tất OCR/Vision: %d/%d frames cho video %s", saved, total, video_id)
    return saved


# ─────────────────────────────────────────────────────────────────
# MESSAGE PROCESSING
# ─────────────────────────────────────────────────────────────────

def _process_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    method_frame: pika.spec.Basic.GetOk,
    body: bytes,
) -> None:
    """Xử lý 1 message từ hàng đợi video_media.

    Luồng xử lý:
    1. Giải mã dữ liệu từ Queue
    2. Whisper transcription (nếu cần)
    3. OCR/Vision cho tất cả frames
    4. Push message tới knowledge queue
    5. Dọn dẹp file tạm
    6. Tạo job record
    7. ACK thành công hoặc NACK nếu lỗi

    Args:
        channel: RabbitMQ channel.
        method_frame: Method frame cho ACK/NACK.
        body: Nội dung message dạng bytes.
    """
    logger.info("==============================================")

    try:
        message = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error("❌ Lỗi giải mã message: %s", e)
        channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=False)
        return

    video_id = message.get("video_id")
    needs_whisper = message.get("needs_whisper", False)
    frame_count = message.get("frame_count", 0)
    has_transcript = message.get("has_transcript", False)

    if not video_id:
        logger.error("❌ Message thiếu video_id. Bỏ qua.")
        channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=False)
        return

    logger.info(
        "🔥 BẮT ĐẦU XỬ LÝ MEDIA CHO VIDEO: %s "
        "(needs_whisper=%s, frames=%d, has_transcript=%s)",
        video_id, needs_whisper, frame_count, has_transcript,
    )

    # Tạo job record
    job_id = create_job(video_id, "MEDIA_EXTRACT")

    try:
        items_processed = 0

        # ── Bước 1: Whisper Transcription ──
        if needs_whisper:
            logger.info("🎤 Bước 1: Whisper transcription cho video %s", video_id)
            whisper_ok = _process_whisper(video_id)
            if whisper_ok:
                items_processed += 1
                logger.info("✅ Whisper transcription thành công")
            else:
                logger.warning("⚠️ Whisper transcription thất bại hoặc bỏ qua")
        else:
            logger.info("⏭️ Bỏ qua Whisper (video đã có transcript hoặc không cần)")

        # ── Bước 1.5: Trích xuất frames ──
        needs_frames = message.get("needs_frames", False)
        if needs_frames:
            from database.queries import save_frames_metadata
            from core.media_processor import extract_frames
            
            logger.info("✂️ Bước 1.5: Trích xuất frames cho video %s", video_id)
            frames = extract_frames(video_id, interval_sec=1)
            if frames:
                save_frames_metadata(video_id, frames)
                logger.info("✅ Đã lưu %d frames vào DB", len(frames))

        # ── Bước 2: OCR/Vision Processing ──
        logger.info("🖼️ Bước 2: OCR/Vision processing cho video %s", video_id)
        frames_processed = _process_frames(video_id)
        items_processed += frames_processed

        # ── Bước 3: Push tới Knowledge Queue ──
        knowledge_queue = getattr(config, "RABBITMQ_KNOWLEDGE_QUEUE", "video_knowledge")
        knowledge_message = json.dumps({"video_id": video_id}, ensure_ascii=False)
        channel.basic_publish(
            exchange="",
            routing_key=knowledge_queue,
            body=knowledge_message.encode("utf-8"),
            properties=pika.BasicProperties(delivery_mode=2),  # persistent
        )
        logger.info("📤 Đã push message tới knowledge queue: %s", knowledge_queue)

        # ── Bước 4: Dọn dẹp file tạm ──
        # Bật/tắt qua config.CLEANUP_TEMP_FILES (mặc định BẬT để tránh đầy ổ đĩa).
        # Khi cần giữ frame/ảnh debug để kiểm chứng, đặt CLEANUP_TEMP_FILES=false.
        if getattr(config, "CLEANUP_TEMP_FILES", True):
            cleanup_temp_files(video_id)

        # ── Cập nhật job thành công ──
        update_job_status(job_id, "COMPLETED", items_processed)

        # ── ACK message ──
        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
        logger.info(
            "✅ ĐÃ XỬ LÝ XONG MEDIA CHO VIDEO %s (%d items processed)",
            video_id, items_processed,
        )

    except Exception as e:
        logger.error("❌ Xử lý media thất bại cho video %s: %s", video_id, e)
        update_job_status(job_id, "FAILED", 0)

        # Trả lại (NACK) để RabbitMQ giữ lại tin nhắn, không bị mất data
        try:
            channel.basic_nack(
                delivery_tag=method_frame.delivery_tag, requeue=True,
            )
        except Exception:
            pass
        # Nghỉ chút trước khi rút message mới
        time.sleep(15)


# ─────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────

def run_media_worker() -> None:
    """Main entry point cho Media Worker process.

    Chạy vòng lặp vô hạn:
    1. Rút 1 message từ RabbitMQ (video_media queue)
    2. Whisper → OCR → Vision → Push knowledge queue → Cleanup
    3. Ngủ 5 giây nếu hàng đợi trống
    """
    # Đăng ký signal handlers cho graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("🎬 MEDIA WORKER ĐÃ KHỞI ĐỘNG - ĐANG LẮNG NGHE RABBITMQ...")

    # Đảm bảo DB sẵn sàng
    try:
        init_db()
    except Exception as e:
        logger.error("❌ Lỗi khởi tạo Database: %s", e)

    # Kết nối RabbitMQ
    connection, channel = _connect_rabbitmq()
    queue_name = getattr(config, "RABBITMQ_MEDIA_QUEUE", "video_media")

    try:
        while not _shutdown_requested:
            # Đảm bảo RabbitMQ connection còn sống
            connection, channel = _ensure_rabbitmq_connection(connection, channel)

            # Rút 1 message (BATCH_SIZE = 1)
            try:
                method_frame, properties, body = channel.basic_get(
                    queue=queue_name, auto_ack=False,
                )
            except pika.exceptions.AMQPError:
                logger.warning("🔄 Lỗi RabbitMQ khi rút message. Reconnecting...")
                connection, channel = _connect_rabbitmq()
                continue

            if method_frame:
                _process_message(channel, method_frame, body)
            else:
                # Hàng đợi trống, đi ngủ 5 giây rồi kiểm tra lại
                logger.debug("💤 Hàng đợi media trống. Worker đang ngủ chờ dữ liệu mới...")
                time.sleep(5)

    except KeyboardInterrupt:
        logger.info("🛑 Đã nhận KeyboardInterrupt.")
    finally:
        logger.info("🛑 Đang tắt Media Worker...")
        if connection and connection.is_open:
            try:
                connection.close()
                logger.info("✅ Đã đóng kết nối RabbitMQ.")
            except Exception:
                pass


if __name__ == "__main__":
    run_media_worker()

"""
Scraper Worker — Máy cào bình luận YouTube.

Worker chạy liên tục, poll bảng videos để tìm video chờ cào (status='PROCESSING'),
sau đó cào bình luận mới nhất bằng YouTube API với thuật toán:
- Smart Pagination: Lật trang tuần tự, bốc tối đa 100 bình luận mỗi trang
- Early Exit: Dừng sớm khi đụng bình luận cũ đã xử lý (done) trong Redis
- Redis Dedup: Bỏ qua bình luận đang nằm trong Queue chờ AI xử lý (seen)
- RabbitMQ: Đẩy bình luận mới vào hàng đợi để AI worker xử lý

Entry point: run_scraper_worker()
"""

import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pika
import pika.exceptions

# Đảm bảo project root nằm trong sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.config import config
from utils.logger import get_logger
from utils.cache import (
    get_redis_client,
    is_comment_seen,
    mark_comment_seen,
    is_comment_done,
)
from database.queries import (
    get_processing_videos,
    insert_video,
    update_video_status,
    create_job,
    update_job_status,
    save_transcript,
    save_frames_metadata,
    has_transcript,
)
from integrations.youtube_client import get_youtube_service, fetch_video_comments
from core.media_processor import (
    get_transcript_from_youtube,
    download_audio,
    extract_frames,
)

logger = get_logger(__name__)

# Cờ báo hiệu tắt worker gracefully
_shutdown_requested = False


def _signal_handler(signum: int, frame: Any) -> None:
    """Xử lý tín hiệu SIGINT/SIGTERM để tắt worker an toàn."""
    global _shutdown_requested
    logger.info("🛑 Nhận tín hiệu tắt (signal=%s). Đang dừng worker...", signum)
    _shutdown_requested = True


def _connect_rabbitmq() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """Kết nối tới RabbitMQ với logic reconnect.

    Returns:
        Tuple (connection, channel) đã sẵn sàng sử dụng.

    Raises:
        SystemExit: Nếu không thể kết nối sau nhiều lần thử.
    """
    max_retries = 5
    queue_name = getattr(config, "RABBITMQ_QUEUE", "youtube_comments")
    rabbitmq_user = getattr(config, "RABBITMQ_USER", "guest")
    rabbitmq_pass = getattr(config, "RABBITMQ_PASS", "guest")

    for attempt in range(1, max_retries + 1):
        try:
            credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_pass)
            params = pika.ConnectionParameters(
                host=config.RABBITMQ_HOST,
                port=config.RABBITMQ_PORT,
                credentials=credentials,
                heartbeat=0,
                blocked_connection_timeout=300,
            )
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            # Tạo cái ống (Queue) tên là 'youtube_comments'
            channel.queue_declare(queue=queue_name, durable=True)
            # Tạo queue mới cho Media Pipeline (Phase 1)
            channel.queue_declare(queue=config.RABBITMQ_MEDIA_QUEUE, durable=True)
            logger.info("✅ Đã kết nối thành công với RabbitMQ (Hồ chứa Message Queue)")
            return connection, channel
        except pika.exceptions.AMQPConnectionError as e:
            logger.error(
                "❌ Lỗi kết nối RabbitMQ (lần %d/%d): %s",
                attempt, max_retries, e,
            )
            if attempt < max_retries:
                wait_time = min(2 ** attempt, 30)
                logger.info("⏳ Chờ %d giây trước khi thử lại...", wait_time)
                time.sleep(wait_time)
            else:
                logger.critical("❌ Không thể kết nối RabbitMQ sau %d lần thử. Thoát.", max_retries)
                sys.exit(1)

    # Unreachable, nhưng để type checker vui
    sys.exit(1)


def _ensure_rabbitmq_connection(
    connection: pika.BlockingConnection | None,
    channel: pika.adapters.blocking_connection.BlockingChannel | None,
) -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """Kiểm tra và tái kết nối RabbitMQ nếu connection bị đứt."""
    if connection is None or connection.is_closed:
        logger.warning("🔄 RabbitMQ connection bị đứt. Đang reconnect...")
        return _connect_rabbitmq()
    return connection, channel  # type: ignore[return-value]


def scrape_video_comments(
    video_id: str,
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> int:
    """Cào các bình luận mới nhất cho 1 video.

    Thuật toán: Smart Pagination + Dừng sớm (Early Exit) khi đụng bình luận cũ trong Redis.

    Args:
        video_id: ID của video YouTube cần cào.
        channel: RabbitMQ channel để đẩy bình luận mới.

    Returns:
        Số lượng bình luận mới đã cào được.
    """
    logger.info("▶ BẮT ĐẦU CÀO BÌNH LUẬN MỚI CHO VIDEO: %s", video_id)
    queue_name = getattr(config, "RABBITMQ_QUEUE", "youtube_comments")

    # Ghi danh Video vào hệ thống
    insert_video(video_id)
    job_id = None

    youtube_service = get_youtube_service(use_oauth=False)

    new_comments_count = 0
    stop_scraping = False  # Cờ báo hiệu đụng tường Redis
    page_token: str | None = None

    while not stop_scraping:
        if _shutdown_requested:
            logger.info("🛑 Shutdown requested — dừng cào video %s", video_id)
            break

        try:
            comments, next_page_token = fetch_video_comments(
                youtube_service, video_id, max_results=100, page_token=page_token
            )
        except Exception as e:
            logger.error("❌ Lỗi khi gọi YouTube API cho video %s: %s", video_id, e)
            break

        if not comments:
            break

        for comment_data in comments:
            comment_id = comment_data["comment_id"]
            author = comment_data.get("author", "Unknown")
            text = comment_data.get("text", "")

            # 🛑 ĐỤNG TƯỜNG REDIS: Kích hoạt thuật toán Early Exit
            if is_comment_done(comment_id):
                stop_scraping = True
                logger.info(
                    "🛑 Chạm tường Redis (DONE) tại comment của [%s]. "
                    "NGỪNG QUÉT (Early Exit) để tiết kiệm API!",
                    author,
                )
                break

            # Đang trong hàng đợi nhưng chưa xử lý xong, bỏ qua để không đẩy đúp
            if is_comment_seen(comment_id):
                logger.debug(
                    "⏭️ Bỏ qua comment [%s] (Đang nằm trong Queue chờ AI xử lý).",
                    author,
                )
                continue

            # Nếu chưa có trong Redis -> Đây là Bình luận mới!
            new_comments_count += 1
            if new_comments_count == 1:
                # Chỉ tạo Job khi thực sự có ít nhất 1 bình luận mới
                job_id = create_job(video_id, "SCRAPE_JOB")

            # Đóng gói dữ liệu
            payload = {
                "video_id": video_id,
                "comment_id": comment_id,
                "author": author,
                "text": text,
                "published_at": comment_data.get("published_at", ""),
            }

            # Đẩy vào Hàng đợi RabbitMQ
            channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=json.dumps(payload, ensure_ascii=False),
                properties=pika.BasicProperties(delivery_mode=2),
            )

            # Ghi danh vào Redis (seen với TTL ngắn: 1 giờ) - chống Race Condition
            mark_comment_seen(comment_id, ttl=3600)
            logger.info("📦 Đã đẩy vào Queue: [%s] %s...", author, text[:30])

        # Chuẩn bị lật sang trang sau (Nếu chưa bị đụng tường Redis)
        page_token = next_page_token
        if not page_token:
            break

    logger.info("📊 TỔNG KẾT CÀO VIDEO %s:", video_id)
    logger.info("   - Đã cào thêm (Mới): %d", new_comments_count)

    # Cập nhật Job thành công
    if job_id is not None:
        update_job_status(job_id, "COMPLETED", new_comments_count)

    return new_comments_count


def _ingest_media(
    video_id: str,
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    """Bước Ingest Media: Tải transcript + audio + frames cho video.

    Đây là Phase 1 của Multimodal Pipeline:
    1. Lấy transcript từ YouTube API (Lớp 1 — miễn phí)
    2. Nếu không có transcript → tải audio cho Whisper (Lớp 2 — Giai đoạn 2)
    3. Trích xuất frames cho OCR/Vision (Giai đoạn 2)
    4. Lưu metadata vào DB
    5. Push message "media_ready" vào queue video_media

    Args:
        video_id: ID video YouTube.
        channel: RabbitMQ channel để push message media_ready.
    """
    logger.info("📹 INGEST MEDIA cho video %s...", video_id)

    # Tạo job record cho Media Ingest
    job_id = create_job(video_id, "MEDIA_INGEST")

    got_transcript = False
    frame_count = 0

    try:
        # Bước 1: Lấy transcript từ YouTube API (miễn phí, < 1 giây)
        if not has_transcript(video_id):
            transcript_data = get_transcript_from_youtube(video_id)
            if transcript_data:
                save_transcript(video_id, transcript_data)
                got_transcript = True
                logger.info("✅ Đã lấy được phụ đề từ YouTube cho video %s", video_id)
            else:
                logger.info("⚠️ Video %s không có phụ đề. Sẽ cần Whisper (Giai đoạn 2).", video_id)
        else:
            got_transcript = True
            logger.info("♻️ Video %s đã có transcript trong DB.", video_id)

        # Bước 2: Tải audio (cho Whisper ở Giai đoạn 2, CHỈ khi không có transcript)
        audio_path = None
        if not got_transcript:
            audio_path = download_audio(video_id)
            if audio_path:
                logger.info("🎵 Đã tải audio cho Whisper: %s", audio_path)

        # Bước 3: Đã di chuyển trích xuất frames sang media_worker.py để đồng bộ với Whisper
        
        # Bước 4: Push message "media_ready" vào queue video_media
        media_message = {
            "video_id": video_id,
            "has_transcript": got_transcript,
            "needs_whisper": not got_transcript and audio_path is not None,
            "needs_frames": True,  # Yêu cầu media_worker cắt frame
        }
        channel.basic_publish(
            exchange="",
            routing_key=config.RABBITMQ_MEDIA_QUEUE,
            body=json.dumps(media_message, ensure_ascii=False),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        logger.info("📤 Đã push message media_ready cho video %s vào queue.", video_id)

        # Cập nhật job (frames sẽ được cập nhật lại bên media_worker)
        if job_id:
            update_job_status(job_id, "COMPLETED", 0)

    except Exception as e:
        logger.error("❌ Lỗi Ingest Media cho video %s: %s", video_id, e)
        if job_id:
            update_job_status(job_id, "FAILED", 0)


def run_scraper_worker() -> None:
    """Main entry point cho scraper worker process.

    Chạy vòng lặp vô hạn:
    1. Poll bảng videos để tìm video có status='PROCESSING'
    2. Cào bình luận mới cho từng video
    3. Đánh dấu video đã cào xong (status='COMPLETED')
    4. Ngủ 3 giây nếu không có video mới
    """
    # Đăng ký signal handlers cho graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("🚀 Máy cào (Scraper Worker) đã khởi động!")
    logger.info("👀 Đang dòm ngó vào Kho Video xem có con mồi nào không...")

    # Kết nối Redis
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        logger.info("✅ Đã kết nối thành công với Redis (Khiên khử trùng lặp)")
    except Exception as e:
        logger.critical("❌ Lỗi kết nối Redis: %s", e)
        sys.exit(1)

    # Kết nối RabbitMQ
    connection, channel = _connect_rabbitmq()

    try:
        while not _shutdown_requested:
            # Đảm bảo RabbitMQ connection còn sống
            connection, channel = _ensure_rabbitmq_connection(connection, channel)

            try:
                processing_videos = get_processing_videos()
            except Exception as e:
                logger.error("❌ Lỗi khi lấy danh sách video chờ cào: %s", e)
                time.sleep(3)
                continue

            if processing_videos:
                for video in processing_videos:
                    if _shutdown_requested:
                        break

                    target_video_id = video["video_id"]
                    logger.info(
                        "🎯 Phát hiện Video chờ cào: %s. LÊN ĐẠN!",
                        target_video_id,
                    )

                    try:
                        scrape_video_comments(target_video_id, channel)
                        # === PHASE 1: INGEST MEDIA ===
                        _ingest_media(target_video_id, channel)
                        update_video_status(target_video_id, "COMPLETED")
                        logger.info("✅ Đã giải quyết xong video %s. Đi tuần tra tiếp...", target_video_id)
                    except pika.exceptions.AMQPError:
                        # RabbitMQ bị đứt giữa chừng — reconnect
                        logger.warning("🔄 RabbitMQ lỗi giữa chừng. Reconnecting...")
                        connection, channel = _connect_rabbitmq()
                    except Exception as e:
                        logger.error(
                            "❌ Lỗi xử lý video %s: %s",
                            target_video_id, e,
                        )
            else:
                # Ngủ 3 giây nếu chưa có video mới
                time.sleep(3)

    except KeyboardInterrupt:
        logger.info("🛑 Đã nhận KeyboardInterrupt.")
    finally:
        logger.info("🛑 Đang tắt cỗ máy Scraper...")
        if connection and connection.is_open:
            try:
                connection.close()
                logger.info("✅ Đã đóng kết nối RabbitMQ.")
            except Exception:
                pass


if __name__ == "__main__":
    run_scraper_worker()

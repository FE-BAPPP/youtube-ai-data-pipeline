"""
Media Processor — Tải audio, trích xuất frames, lấy transcript.

Module này là bước đầu tiên trong Ingest Pipeline (Giai đoạn 1):
- Lấy transcript có sẵn từ YouTube (Lớp 1 — miễn phí, nhanh)
- Tải file audio từ YouTube bằng yt-dlp (cho Whisper ở Giai đoạn 2)
- Trích xuất frames từ video bằng ffmpeg (cho OCR/Vision ở Giai đoạn 2)
- Quản lý thư mục tạm (temp_media/)
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import cv2
import numpy as np

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Thư mục gốc lưu file tạm (nằm trong project root)
TEMP_MEDIA_DIR = Path(__file__).resolve().parent.parent / "temp_media"


def _ensure_dir(path: Path) -> Path:
    """Tạo thư mục nếu chưa tồn tại."""
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────────────────────────
# LỚP 1: LẤY TRANSCRIPT TỪ YOUTUBE API (Miễn phí, < 1 giây)
# ─────────────────────────────────────────────────────────────────

def get_transcript_from_youtube(video_id: str, language: str = "vi") -> dict | None:
    """Lấy phụ đề (transcript) có sẵn từ YouTube.

    Đây là Lớp 1 trong chiến thuật Hybrid:
    - Nếu video có phụ đề tự động hoặc phụ đề người tạo → Lấy luôn (miễn phí).
    - Nếu không có → return None → Chuyển sang Whisper (Lớp 2, Giai đoạn 2).

    Args:
        video_id: ID video YouTube.
        language: Ngôn ngữ ưu tiên (mặc định: 'vi' cho tiếng Việt).

    Returns:
        Dict chứa transcript hoặc None nếu không có phụ đề.
        Format: {
            "source": "youtube_api",
            "language": "vi",
            "raw_text": "Toàn bộ nội dung...",
            "segments": [{"start": 0.0, "end": 3.5, "text": "..."}, ...],
            "duration_sec": 600.0
        }
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()

        # Thử lấy phụ đề theo thứ tự ưu tiên: vi → en → bất kỳ ngôn ngữ nào
        transcript_data = None
        used_language = language

        for lang_code in [language, "en"]:
            try:
                fetched = ytt_api.fetch(video_id, languages=[lang_code])
                transcript_data = fetched.to_raw_data()
                used_language = lang_code
                break
            except Exception:
                continue

        # Fallback: lấy bất kỳ ngôn ngữ nào có sẵn
        if transcript_data is None:
            try:
                transcript_list = ytt_api.list(video_id)
                for transcript_info in transcript_list:
                    fetched = ytt_api.fetch(video_id, languages=[transcript_info.language_code])
                    transcript_data = fetched.to_raw_data()
                    used_language = transcript_info.language_code
                    break
            except Exception:
                pass

        if not transcript_data:
            logger.info("⚠️ Video %s không có phụ đề trên YouTube.", video_id)
            return None

        # Chuyển đổi sang format chuẩn
        segments = []
        raw_parts = []
        for entry in transcript_data:
            start = float(entry.get("start", 0))
            duration = float(entry.get("duration", 0))
            text = entry.get("text", "").strip()
            if text:
                segments.append({
                    "start": round(start, 2),
                    "end": round(start + duration, 2),
                    "text": text,
                })
                raw_parts.append(text)

        raw_text = " ".join(raw_parts)
        duration_sec = segments[-1]["end"] if segments else 0.0

        logger.info(
            "✅ Đã lấy được phụ đề YouTube cho video %s (%d segments, %.0f giây)",
            video_id, len(segments), duration_sec,
        )
        return {
            "source": "youtube_api",
            "language": used_language,
            "raw_text": raw_text,
            "segments": segments,
            "duration_sec": duration_sec,
        }

    except ImportError:
        logger.error("❌ Chưa cài youtube-transcript-api. Chạy: pip install youtube-transcript-api")
        return None
    except Exception as e:
        logger.warning("⚠️ Không lấy được transcript YouTube cho %s: %s", video_id, e)
        return None


# ─────────────────────────────────────────────────────────────────
# TẢI AUDIO (cho Whisper ở Giai đoạn 2)
# ─────────────────────────────────────────────────────────────────

def download_audio(video_id: str) -> str | None:
    """Tải file audio từ YouTube bằng yt-dlp.

    Cấu hình tối ưu cho Whisper: mono, 16kHz, WAV format.
    File được lưu tại: temp_media/{video_id}/audio.wav

    Args:
        video_id: ID video YouTube.

    Returns:
        Đường dẫn tuyệt đối tới file audio, hoặc None nếu thất bại.
    """
    video_dir = _ensure_dir(TEMP_MEDIA_DIR / video_id)
    output_path = video_dir / "audio.wav"

    if output_path.exists():
        logger.info("♻️ Audio đã tồn tại: %s", output_path)
        return str(output_path)

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # yt-dlp: chỉ tải audio, convert sang WAV mono 16kHz (tối ưu cho Whisper)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "wav",
        "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
        "--output", str(video_dir / "audio.%(ext)s"),
        video_url,
    ]

    logger.info("⏬ Đang tải audio cho video %s...", video_id)

    try:
        # Cấu hình biến môi trường để ép yt-dlp dùng thư mục temp trên ổ D (hoặc thư mục hiện tại)
        env = os.environ.copy()
        env["TEMP"] = str(video_dir)
        env["TMP"] = str(video_dir)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # Timeout 5 phút
            env=env
        )

        if result.returncode != 0:
            logger.error("❌ yt-dlp lỗi: %s", result.stderr[:500])
            return None

        if output_path.exists():
            file_size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(
                "✅ Đã tải audio thành công: %s (%.1f MB)",
                output_path, file_size_mb,
            )
            return str(output_path)
        else:
            logger.error("❌ File audio không tìm thấy sau khi tải.")
            return None

    except FileNotFoundError:
        logger.error(
            "❌ Không tìm thấy yt-dlp. Cài đặt: pip install yt-dlp\n"
            "   Và cần có FFmpeg: https://ffmpeg.org/download.html"
        )
        return None
    except subprocess.TimeoutExpired:
        logger.error("❌ Timeout khi tải audio cho video %s (quá 5 phút).", video_id)
        return None
    except Exception as e:
        logger.error("❌ Lỗi không xác định khi tải audio: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────
# TRÍCH XUẤT FRAMES & DEDUPLICATE (STEP 4 & 5)
# ─────────────────────────────────────────────────────────────────

def calculate_dhash(image, hash_size=8):
    """Tính mã băm dhash (Difference Hash) của ảnh.
    
    Chuyển ảnh sang grayscale, resize về (hash_size + 1, hash_size),
    sau đó so sánh pixel liền kề để tạo thành chuỗi bit.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size))
    # So sánh giá trị pixel kề nhau
    diff = resized[:, 1:] > resized[:, :-1]
    return diff


def hamming_distance(hash1, hash2):
    """Tính khoảng cách Hamming giữa 2 mã băm."""
    return np.count_nonzero(hash1 != hash2)


def extract_frames(video_id: str, interval_sec: int = 1) -> list[dict]:
    """Trích xuất các frame hình ảnh từ video YouTube (Sử dụng OpenCV + Scene Detection).

    Dùng yt-dlp tải video 720p, sau đó dùng OpenCV đọc frame tuần tự
    (grab/retrieve) và lấy mẫu mỗi `interval_sec` giây.
    Chỉ lưu frame nếu có sự khác biệt (dhash) > 10% so với frame trước đó.
    """
    video_dir = _ensure_dir(TEMP_MEDIA_DIR / video_id)
    frames_dir = _ensure_dir(video_dir / "frames")
    video_path = video_dir / "video.mp4"

    # Bước 1: Tải video chất lượng khá (720p) nếu chưa có
    if not video_path.exists():
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        cmd_download = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "--format", "bestvideo[height<=720][ext=mp4]/best[height<=720]/best",
            "--output", str(video_path),
            video_url,
        ]

        logger.info("⏬ Đang tải video 720p cho %s...", video_id)
        try:
            env = os.environ.copy()
            env["TEMP"] = str(video_dir)
            env["TMP"] = str(video_dir)

            result = subprocess.run(
                cmd_download, capture_output=True, text=True, timeout=600, env=env
            )
            if result.returncode != 0:
                logger.error("❌ yt-dlp tải video lỗi: %s", result.stderr[:500])
                return []
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.error("❌ Lỗi tải video: %s", e)
            return []

    if not video_path.exists():
        logger.error("❌ File video không tìm thấy: %s", video_path)
        return []

    # Bước 2: Cắt frame bằng OpenCV và so sánh mã băm (dhash)
    logger.info("🖼️ Đang trích xuất frame bằng OpenCV + dhash từ video %s...", video_id)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error("❌ Không thể mở video bằng OpenCV.")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0  # Fallback

    frames_skip = max(1, int(round(fps * interval_sec)))
    frame_count = 0          # chỉ số frame hiện tại trong video
    next_target = 0          # chỉ số frame kế tiếp cần lấy mẫu
    saved_frames = []

    prev_hash = None
    hash_size = 8
    # 64 bit hash, > 10% khác biệt => > 6.4 => distance >= 7
    threshold_distance = int((hash_size * hash_size) * 0.10)

    # Đọc TUẦN TỰ và bỏ qua frame bằng grab() (chỉ đọc, KHÔNG decode).
    # Chỉ decode (retrieve) đúng frame cần lấy mẫu theo interval.
    # Cách này tránh cap.set(POS_FRAMES) vốn buộc decoder seek về keyframe
    # rồi decode lại tuần tự -> chậm gấp nhiều lần trên video H.264 dài.
    while True:
        ret = cap.grab()
        if not ret:
            break

        if frame_count == next_target:
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                frame_count += 1
                continue

            current_hash = calculate_dhash(frame, hash_size=hash_size)
            save_this_frame = False

            if prev_hash is None:
                save_this_frame = True
            else:
                distance = hamming_distance(prev_hash, current_hash)
                if distance > threshold_distance:
                    save_this_frame = True

            if save_this_frame:
                # Lưu frame ra đĩa
                frame_name = f"frame_{len(saved_frames):04d}.jpg"
                out_path = frames_dir / frame_name
                cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

                saved_frames.append({
                    "frame_index": len(saved_frames),
                    "timestamp_sec": round(frame_count / fps, 2),
                    "file_path": str(out_path),
                })
                prev_hash = current_hash

            next_target += frames_skip

        frame_count += 1

    cap.release()
    logger.info(
        "✅ Đã trích xuất và lọc (Scene Detect) xong: giữ lại %d frames cho video %s",
        len(saved_frames), video_id,
    )
    return saved_frames


# ─────────────────────────────────────────────────────────────────
# DỌN DẸP FILE TẠM
# ─────────────────────────────────────────────────────────────────

def cleanup_temp_files(video_id: str) -> None:
    """Xóa toàn bộ thư mục temp_media/{video_id}/.

    Gọi sau khi đã xử lý xong OCR/Vision/Embedding để giải phóng ổ cứng.
    """
    video_dir = TEMP_MEDIA_DIR / video_id
    if video_dir.exists():
        shutil.rmtree(video_dir)
        logger.info("🧹 Đã dọn sạch file tạm cho video %s", video_id)


def get_media_status(video_id: str) -> dict:
    """Kiểm tra trạng thái media đã tải cho 1 video.

    Returns:
        Dict: {has_audio, has_video, frame_count, audio_path, video_path}
    """
    video_dir = TEMP_MEDIA_DIR / video_id
    audio_path = video_dir / "audio.wav"
    video_path = video_dir / "video.mp4"
    frames_dir = video_dir / "frames"

    frame_count = 0
    if frames_dir.exists():
        frame_count = len(list(frames_dir.glob("frame_*.jpg")))

    return {
        "has_audio": audio_path.exists(),
        "has_video": video_path.exists(),
        "frame_count": frame_count,
        "audio_path": str(audio_path) if audio_path.exists() else None,
        "video_path": str(video_path) if video_path.exists() else None,
    }

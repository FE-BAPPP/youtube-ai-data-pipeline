"""
Centralized Configuration Module.

Tải và quản lý tất cả biến môi trường từ file .env.
Cung cấp một singleton `config` để dùng chung trong toàn bộ hệ thống.
"""

import os
from dotenv import load_dotenv

# Tải biến môi trường từ file .env (nếu có)
load_dotenv()

# Đặt đường dẫn tải Model (HuggingFace) về ổ D (trong dự án) để tránh đầy ổ C
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HF_CACHE_DIR = os.path.join(PROJECT_ROOT, "hf_cache")
os.environ["HF_HOME"] = os.getenv("HF_HOME", HF_CACHE_DIR)

class Config:
    """Centralized configuration from environment variables.

    Tất cả các thiết lập hệ thống được tập trung tại đây,
    bao gồm YouTube API, GitHub Tokens, Database, Redis, RabbitMQ, và Webhook.
    """

    # --- YouTube API ---
    YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")

    # --- GitHub Tokens (Token Rotation cho AI) ---
    # Hỗ trợ 2 cách cấu hình:
    #   1. GITHUB_TOKENS=token1,token2,token3 (phân cách bằng dấu phẩy)
    #   2. GITHUB_TOKEN=single_token (fallback cho hệ thống cũ)
    _tokens_str: str = os.getenv("GITHUB_TOKENS") or os.getenv("GITHUB_TOKEN") or ""
    GITHUB_TOKENS: list[str] = [t.strip() for t in _tokens_str.split(",") if t.strip()]

    # --- Database (PostgreSQL) ---
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "youtube_ai_db")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASS: str = os.getenv("DB_PASS", "123456")

    # --- Redis (Bộ nhớ đệm RAM — Khử trùng lặp) ---
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

    # --- RabbitMQ (Hàng đợi tin nhắn) ---
    RABBITMQ_HOST: str = os.getenv("RABBITMQ_HOST", "localhost")
    RABBITMQ_PORT: int = int(os.getenv("RABBITMQ_PORT", "5672"))
    RABBITMQ_USER: str = os.getenv("RABBITMQ_USER", "guest")
    RABBITMQ_PASS: str = os.getenv("RABBITMQ_PASS", "guest")
    RABBITMQ_QUEUE: str = os.getenv("RABBITMQ_QUEUE", "youtube_comments")
    RABBITMQ_MEDIA_QUEUE: str = os.getenv("RABBITMQ_MEDIA_QUEUE", "video_media")
    RABBITMQ_KNOWLEDGE_QUEUE: str = os.getenv("RABBITMQ_KNOWLEDGE_QUEUE", "video_knowledge")

    # --- Webhook Integrations ---
    N8N_WEBHOOK_URL: str | None = os.getenv("N8N_WEBHOOK_URL")

    # --- Whisper API (Google Colab endpoint) ---
    WHISPER_API_URL: str | None = os.getenv("WHISPER_API_URL")

    # --- Qdrant (Vector Database — Giai đoạn 4) ---
    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))

    # --- Media Processing ---
    FRAME_INTERVAL_SEC: int = int(os.getenv("FRAME_INTERVAL_SEC", "5"))
    # Bật/tắt việc vẽ bbox và ghi ảnh debug ra thư mục output/ (chỉ dùng khi test).
    # Mặc định TẮT vì thao tác imread/imwrite mỗi frame rất tốn I/O và làm chậm pipeline.
    DEBUG_OCR: bool = os.getenv("DEBUG_OCR", "false").lower() in ("1", "true", "yes")
    # Bật/tắt dọn file tạm sau khi xử lý xong 1 video. Mặc định BẬT để tránh đầy ổ đĩa.
    CLEANUP_TEMP_FILES: bool = os.getenv("CLEANUP_TEMP_FILES", "true").lower() in ("1", "true", "yes")
    # Số luồng ONNX Runtime cho RapidOCR (0 = để thư viện tự quyết theo số nhân CPU).
    OCR_NUM_THREADS: int = int(os.getenv("OCR_NUM_THREADS", "0"))
    # Số worker song song xử lý OCR frame. ONNX Runtime nhả GIL khi inference nên
    # dùng thread giúp tận dụng nhiều nhân CPU. Mặc định 4.
    OCR_MAX_WORKERS: int = int(os.getenv("OCR_MAX_WORKERS", "4"))

    # --- FastAPI Application Configuration (Sếp thêm đoạn này vào nhé) ---
    API_PORT: int = int(os.getenv("API_PORT", "8000"))

    # --- Legacy (Giữ lại để tương thích ngược) ---
    VIDEO_ID: str = os.getenv("VIDEO_ID", "fSyw-jZ_f18")

    @property
    def db_dsn(self) -> str:
        """Trả về DSN connection string cho PostgreSQL."""
        return (
            f"host={self.DB_HOST} port={self.DB_PORT} "
            f"dbname={self.DB_NAME} user={self.DB_USER} password={self.DB_PASS}"
        )

    def __repr__(self) -> str:
        return (
            f"Config(DB={self.DB_USER}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}, "
            f"Redis={self.REDIS_HOST}:{self.REDIS_PORT}, "
            f"RabbitMQ={self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}, "
            f"Tokens={len(self.GITHUB_TOKENS)})"
        )


# Module-level singleton — import và dùng ngay: `from utils.config import config`
config = Config()

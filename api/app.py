"""
Main FastAPI Application — YouTube AI Auto Ops Command Center.

Ứng dụng web chính tích hợp tất cả router (dashboard, videos, auth),
phục vụ frontend tĩnh, và khởi tạo database khi startup.
Thay thế cả web_app.py (port 8080) và oauth_server.py (port 8000) bằng 1 app duy nhất.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Đảm bảo project root nằm trong sys.path để import các module nội bộ
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.logger import get_logger
from utils.config import config
from database.connection import init_db

from api.routes.dashboard import router as dashboard_router
from api.routes.videos import router as videos_router
from api.routes.auth import router as auth_router
from api.routes.ideas import router as ideas_router
from api.routes.flaws import router as flaws_router
from api.routes.knowledge import router as knowledge_router

logger = get_logger(__name__)

# Đường dẫn tới thư mục frontend
FRONTEND_DIR = Path(PROJECT_ROOT) / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle handler — khởi tạo database khi startup."""
    logger.info("🚀 Đang khởi tạo Database schema...")
    try:
        init_db()
        logger.info("✅ Database đã sẵn sàng!")
    except Exception as e:
        logger.error("❌ Lỗi khởi tạo Database: %s", e)
    yield
    logger.info("🛑 Đang tắt ứng dụng...")


app = FastAPI(
    title="YouTube AI Auto Ops",
    description="Bảng điều khiển AI phân tích bình luận YouTube",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware — chỉ cho phép origin đáng tin (mặc định localhost).
# Nếu CORS_ORIGINS="*" thì phải tắt credentials (bắt buộc theo CORS spec).
_allow_all = "*" in config.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đăng ký tất cả routers
app.include_router(dashboard_router)
app.include_router(videos_router)
app.include_router(auth_router)
app.include_router(ideas_router, prefix="/api/ideas", tags=["Ideas"])
app.include_router(flaws_router, prefix="/api/flaws", tags=["Flaws"])
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["Knowledge"])


@app.get("/", include_in_schema=False)
def serve_ui() -> FileResponse:
    """Trả về giao diện HTML chính (Command Center UI)."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    # Fallback: thử tìm index.html ở project root (tương thích ngược)
    legacy_path = Path(PROJECT_ROOT) / "index.html"
    if legacy_path.exists():
        return FileResponse(str(legacy_path))
    logger.warning("Không tìm thấy file index.html trong frontend/ hoặc project root")
    return FileResponse(str(index_path))  # Để FastAPI trả lỗi 404 chuẩn


# Mount thư mục frontend cho static files (CSS, JS, images)
if FRONTEND_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="static",
    )


def main() -> None:
    """Entry point — chạy server bằng uvicorn."""
    import uvicorn

    logger.info("🚀 BẢNG ĐIỀU KHIỂN (COMMAND CENTER) ĐÃ SẴN SÀNG!")
    logger.info("👉 Hướng dẫn: Mở trình duyệt và truy cập: http://localhost:%d", config.API_PORT)
    logger.info("👉 OAuth Login: http://localhost:%d/auth/login", config.API_PORT)
    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT)


if __name__ == "__main__":
    main()

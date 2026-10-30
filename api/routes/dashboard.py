"""
Router cho Dashboard API.

Cung cấp dữ liệu Real-time cho giao diện Web (videos + jobs).
Trích xuất từ web_app.py gốc.
"""

from fastapi import APIRouter

from utils.logger import get_logger
from database.queries import get_dashboard_data

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/data")
def api_get_dashboard_data() -> dict:
    """API cung cấp dữ liệu Real-time cho giao diện.

    Trả về danh sách video mới nhất và tiến trình công việc (Workers).
    """
    try:
        data = get_dashboard_data()
        return data
    except Exception as e:
        logger.error("Lỗi truy xuất DB cho dashboard: %s", e)
        return {"videos": [], "jobs": []}

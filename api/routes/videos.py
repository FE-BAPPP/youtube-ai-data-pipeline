"""
Router cho Video Management API.

Cung cấp các endpoint quản lý video:
- Tìm kiếm video theo từ khóa
- Do thám kênh đối thủ
- Đồng bộ kênh YouTube cá nhân qua OAuth
"""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from utils.logger import get_logger
from core.video_sources import (
    search_videos_by_keyword,
    get_competitor_videos,
    sync_own_channel,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["videos"])


class SearchQuery(BaseModel):
    """Schema cho request tìm kiếm video."""
    keyword: str


class CompetitorQuery(BaseModel):
    """Schema cho request do thám kênh đối thủ."""
    channel_handle: str
    max_results: int = 10


class SyncRequest(BaseModel):
    """Schema cho request đồng bộ kênh cá nhân."""
    token_file: str = "user_token.json"


@router.post("/search")
def run_search(query: SearchQuery, background_tasks: BackgroundTasks) -> dict:
    """API nhận lệnh tìm kiếm từ UI và đẩy vào Background Task để UI không bị đơ.

    Đẩy lệnh tìm kiếm chạy ngầm, giao diện Web sẽ báo thành công ngay lập tức.
    """
    logger.info("Nhận lệnh tìm kiếm từ khóa: '%s'", query.keyword)
    background_tasks.add_task(search_videos_by_keyword, query.keyword)
    return {
        "status": "success",
        "message": f"Đang tung radar quét: '{query.keyword}'...",
    }


@router.post("/competitor")
def run_competitor_watch(
    query: CompetitorQuery, background_tasks: BackgroundTasks
) -> dict:
    """API khởi động do thám kênh đối thủ.

    Quét video mới nhất của kênh đối thủ và đưa vào hệ thống.
    """
    logger.info(
        "Nhận lệnh do thám kênh đối thủ: '%s'", query.channel_handle
    )
    background_tasks.add_task(
        get_competitor_videos, query.channel_handle, query.max_results
    )
    return {
        "status": "success",
        "message": f"Đang khởi động radar do thám kênh: '{query.channel_handle}'...",
    }


@router.post("/sync")
def run_channel_sync(
    request: SyncRequest, background_tasks: BackgroundTasks
) -> dict:
    """API đồng bộ kênh YouTube cá nhân qua OAuth.

    Tự động lấy video mới nhất từ kênh của chủ sở hữu.
    """
    logger.info("Nhận lệnh đồng bộ kênh cá nhân (token_file=%s)", request.token_file)
    background_tasks.add_task(sync_own_channel, request.token_file)
    return {
        "status": "success",
        "message": "Đang đồng bộ kênh YouTube cá nhân...",
    }

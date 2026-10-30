from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.queries import get_comments_for_ideation
from core.ai_analyzer import generate_ideas_from_comments
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

class IdeationRequest(BaseModel):
    video_ids: list[str]
    limit: int = 40

import re

def extract_video_id(url_or_id: str) -> str:
    """Trích xuất video_id từ link YouTube, nếu là ID thì giữ nguyên."""
    if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
        match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url_or_id)
        if match:
            return match.group(1)
    return url_or_id

@router.post("/generate")
async def generate_ideas(request: IdeationRequest):
    """Phân tích bình luận từ các video và đẻ ra ý tưởng làm video mới."""
    # Trích xuất ID từ link nếu người dùng nhập link
    clean_video_ids = [extract_video_id(vid) for vid in request.video_ids]
    
    # Ép cứng số lượng tối đa là 20 để tránh vỡ Token API (413 Payload Too Large)
    safe_limit = min(request.limit, 20)
    
    logger.info(f"💡 Đang kích hoạt Cỗ Máy Đẻ Ý Tưởng cho {len(clean_video_ids)} video (Limit: {safe_limit})...")
    
    # Lấy bình luận từ database
    comments = get_comments_for_ideation(clean_video_ids, limit=safe_limit)
    if not comments:
        raise HTTPException(
            status_code=404, 
            detail="Không tìm thấy bình luận phù hợp nào từ các video này (hoặc chưa cào xong)."
        )
        
    # Gọi AI đẻ ý tưởng
    ideas = generate_ideas_from_comments(comments)
    if not ideas:
        raise HTTPException(
            status_code=500, 
            detail="AI từ chối đẻ ý tưởng (Có thể do lỗi Rate Limit hoặc hết token)."
        )
        
    return {
        "message": "Đẻ ý tưởng thành công!",
        "analyzed_comments_count": len(comments),
        "ideas": ideas
    }

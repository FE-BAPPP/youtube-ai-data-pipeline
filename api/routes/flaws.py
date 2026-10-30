from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.queries import get_negative_comments
from core.ai_analyzer import generate_flaw_report
from utils.logger import get_logger
import re

logger = get_logger(__name__)

router = APIRouter()

class FlawRequest(BaseModel):
    video_id: str
    limit: int = 40

def extract_video_id(url_or_id: str) -> str:
    if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
        match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url_or_id)
        if match:
            return match.group(1)
    return url_or_id

@router.post("/generate")
async def generate_flaws(request: FlawRequest):
    """Bắt lỗi đối thủ dựa trên bình luận tiêu cực."""
    clean_id = extract_video_id(request.video_id)
    safe_limit = min(request.limit, 20)
    
    logger.info(f"🚨 Đang kích hoạt Radar Bắt Lỗi cho video {clean_id}...")
    
    comments = get_negative_comments([clean_id], limit=safe_limit)
    if not comments:
        raise HTTPException(
            status_code=404, 
            detail="Không tìm thấy bình luận chê bai nào (Hoặc đối thủ làm quá tốt, hoặc chưa cào xong)."
        )
        
    report = generate_flaw_report(comments)
    if not report:
        raise HTTPException(
            status_code=500, 
            detail="AI từ chối phân tích (Có thể do lỗi Rate Limit)."
        )
        
    return {
        "message": "Bắt lỗi thành công!",
        "negative_comments_found": len(comments),
        "report": report
    }

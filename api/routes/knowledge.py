from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.rag_agent import ask_youtube_knowledge

router = APIRouter()

class AskRequest(BaseModel):
    question: str
    video_id: Optional[str] = None
    limit: Optional[int] = 5

@router.post("/ask", summary="Đặt câu hỏi RAG", description="Hỏi chatbot RAG về tri thức trong video (hoặc toàn bộ hệ thống).")
def ask_question(request: AskRequest):
    """
    Tìm kiếm thông tin trong video và trả lời bằng AI (GPT-4o).
    """
    try:
        if not request.question or not request.question.strip():
            raise HTTPException(status_code=400, detail="Câu hỏi không được để trống")
            
        result = ask_youtube_knowledge(
            question=request.question,
            video_id=request.video_id,
            limit=request.limit
        )
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

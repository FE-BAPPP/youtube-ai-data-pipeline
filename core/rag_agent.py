"""
RAG Agent — Trợ lý ảo truy xuất tri thức YouTube.

Nhận câu hỏi từ người dùng -> Nhúng câu hỏi thành vector -> Tìm kiếm trong Qdrant -> Lấy Context.
Dùng GPT-4o tổng hợp Context để trả lời câu hỏi kèm theo Trích dẫn (Citations) chính xác.
"""

import json
from qdrant_client.models import Filter, FieldCondition, MatchValue
from core.embedding_engine import get_qdrant_client, COLLECTION_NAME
from core.knowledge_builder import get_embedding_model
from core.ai_analyzer import _get_current_client
from utils.logger import get_logger

logger = get_logger(__name__)

def ask_youtube_knowledge(question: str, video_id: str = None, limit: int = 5) -> dict:
    """RAG: Đặt câu hỏi và tìm câu trả lời từ kho tri thức video.
    
    Args:
        question: Câu hỏi của người dùng.
        video_id: Tùy chọn lọc tìm kiếm trong 1 video cụ thể.
        limit: Số lượng chunk tối đa muốn lấy làm context.
        
    Returns:
        Dict chứa câu trả lời của AI và danh sách context đã dùng.
    """
    logger.info(f"🔎 Nhận câu hỏi: '{question}' (video_id={video_id})")
    
    # 1. Nhúng câu hỏi thành vector
    embed_model = get_embedding_model()
    question_vector = embed_model.encode([question])[0].tolist()
    
    # 2. Tìm kiếm (Semantic Search) trong Qdrant
    client = get_qdrant_client()
    query_filter = None
    if video_id:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="video_id",
                    match=MatchValue(value=video_id)
                )
            ]
        )
        
    search_results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=question_vector,
        query_filter=query_filter,
        limit=limit
    )
    
    if not search_results:
        return {
            "answer": "Rất tiếc, tôi không tìm thấy thông tin nào trong hệ thống video liên quan đến câu hỏi này.",
            "contexts": []
        }
        
    # 3. Chuẩn bị Context
    contexts = []
    context_text = ""
    for idx, hit in enumerate(search_results, start=1):
        payload = hit.payload
        ctx = {
            "score": hit.score,
            "video_id": payload.get("video_id"),
            "timestamp": f"{payload.get('start_sec', 0)}s - {payload.get('end_sec', 0)}s",
            "topic": payload.get("topic")
        }
        contexts.append(ctx)
        
        context_text += f"\n[Tài liệu {idx}] (Video: {ctx['video_id']}, Thời gian: {ctx['timestamp']}, Chủ đề: {ctx['topic']})\n"
        context_text += f"Lời thoại: {payload.get('raw_transcript', '')}\n"
        if payload.get("raw_ocr"):
            context_text += f"Trên màn hình có: {payload.get('raw_ocr')}\n"
            
    # 4. Gửi Prompt cho GPT-4o
    prompt = f"""Bạn là một chuyên gia hỗ trợ giải đáp thông tin từ các video YouTube.
Người dùng có một câu hỏi. Dưới đây là các tài liệu (context) trích xuất từ video (bao gồm lời nói và nội dung trên màn hình).

CÂU HỎI: {question}

TÀI LIỆU (CONTEXT):
{context_text}

YÊU CẦU:
1. Trả lời câu hỏi chi tiết, chính xác dựa TRÊN TÀI LIỆU ĐƯỢC CUNG CẤP.
2. NẾU trong tài liệu không có thông tin, hãy nói rõ là "Trong video không đề cập đến vấn đề này", đừng bịa ra.
3. Khi đưa ra thông tin, HÃY TRÍCH DẪN RÕ RÀNG. 
   Ví dụ: "Tại giây 10.5 đến 45.0 của video xyz, tác giả đã hướng dẫn cài đặt... [Tài liệu 1]"
4. Chú ý kết hợp cả thông tin lời nói và những gì xuất hiện trên màn hình (để cung cấp cả lệnh code nếu có).
"""
    
    gpt_client = _get_current_client()
    try:
        response = gpt_client.chat.completions.create(
            model="openai/gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ Lỗi khi gọi GPT-4o trong RAG Agent: {e}")
        answer = f"Lỗi xử lý câu trả lời: {str(e)}"
        
    return {
        "answer": answer,
        "contexts": contexts
    }

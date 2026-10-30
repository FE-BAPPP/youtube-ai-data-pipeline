"""
Embedding Engine — Giai đoạn 4: Đẩy dữ liệu vào Vector Database (Qdrant)

Module này chịu trách nhiệm:
1. Khởi tạo kết nối Qdrant.
2. Embed các knowledge chunks thành Vector (BAAI/bge-m3).
3. Upsert vào Qdrant để sẵn sàng cho tính năng Semantic Search & RAG.
"""

import json
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from core.knowledge_builder import get_embedding_model
from utils.logger import get_logger
from utils.config import config

logger = get_logger(__name__)

# Qdrant Collection Name
COLLECTION_NAME = "youtube_knowledge"
# BAAI/bge-m3 trả về vector có dimension = 1024
VECTOR_SIZE = 1024

_qdrant_client = None

def get_qdrant_client() -> QdrantClient:
    """Khởi tạo và trả về singleton Qdrant Client."""
    global _qdrant_client
    if _qdrant_client is None:
        logger.info(f"🔌 Đang kết nối Qdrant tại {config.QDRANT_HOST}:{config.QDRANT_PORT}...")
        _qdrant_client = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)
        
        # Đảm bảo Collection tồn tại
        collections = _qdrant_client.get_collections().collections
        if not any(c.name == COLLECTION_NAME for c in collections):
            logger.info(f"🆕 Tạo mới collection '{COLLECTION_NAME}' (Size: {VECTOR_SIZE})")
            _qdrant_client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        else:
            logger.info(f"✅ Collection '{COLLECTION_NAME}' đã sẵn sàng.")
            
    return _qdrant_client

def index_knowledge_chunks(video_id: str, chunks: list[dict]) -> int:
    """Upsert danh sách knowledge chunks vào Qdrant.
    
    Args:
        video_id: ID của video.
        chunks: Danh sách các chunk lấy từ PostgreSQL.
        
    Returns:
        Số lượng chunks được index thành công.
    """
    if not chunks:
        return 0
        
    client = get_qdrant_client()
    embed_model = get_embedding_model()
    
    # Tạo payload text để nhúng vector
    # Ta nhúng (embed) nội dung tổng hợp của chunk:
    texts_to_embed = []
    points = []
    
    for chunk in chunks:
        # Tổng hợp ngữ nghĩa để nhúng:
        topic = chunk.get('topic', '')
        summary = chunk.get('summary', '')
        transcript = chunk.get('raw_transcript', '')
        ocr = chunk.get('raw_ocr') or ''
        
        combined_text = f"Chủ đề: {topic}\nTóm tắt: {summary}\nNội dung: {transcript}\nMàn hình: {ocr}"
        texts_to_embed.append(combined_text)

    logger.info(f"🧠 Đang tính toán Vector cho {len(chunks)} Chunks của video {video_id}...")
    vectors = embed_model.encode(texts_to_embed, batch_size=4)
    
    for i, chunk in enumerate(chunks):
        chunk_idx = chunk.get('chunk_index', i)
        # ID của Point trên Qdrant bắt buộc phải là UUID hoặc int.
        # Ta tạo một ID int dựa vào hash của video_id + chunk_idx để luôn duy nhất.
        point_id = abs(hash(f"{video_id}_{chunk_idx}")) % (10 ** 15)
        
        # Lưu các metadata cần thiết phục vụ tìm kiếm & RAG
        payload = {
            "video_id": video_id,
            "chunk_index": chunk_idx,
            "topic": chunk.get('topic'),
            "summary": chunk.get('summary'),
            "start_sec": chunk.get('start_sec'),
            "end_sec": chunk.get('end_sec'),
            "raw_transcript": chunk.get('raw_transcript'),
            "raw_ocr": chunk.get('raw_ocr'),
            "contains_code": chunk.get('contains_code', False),
            "code_language": chunk.get('code_language')
        }
        
        points.append(
            PointStruct(
                id=point_id,
                vector=vectors[i].tolist(),
                payload=payload
            )
        )
        
    logger.info(f"📥 Đang đẩy {len(points)} vector vào Qdrant Collection '{COLLECTION_NAME}'...")
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points
    )
    
    logger.info(f"✅ Hoàn tất Indexing cho video {video_id}!")
    return len(points)

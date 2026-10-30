"""
Integration Test cho Giai đoạn 3 (Knowledge Builder) và Giai đoạn 4 (RAG).

Sử dụng:
- Qdrant In-Memory (không cần chạy Qdrant thật).
- Mock GPT-4o (không tốn tiền API).
- BAAI/bge-m3 thật (kiểm tra khả năng tính toán Vector).
"""

import pytest
from unittest.mock import patch, MagicMock
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance

from core.knowledge_builder import (
    semantic_chunk_transcript,
    merge_ocr_into_chunks
)
from core.embedding_engine import COLLECTION_NAME, VECTOR_SIZE

# Dữ liệu giả định (Dummy Data)
DUMMY_VIDEO_ID = "test_video_123"

DUMMY_SEGMENTS = [
    {"start": 0.0, "end": 5.0, "text": "Xin chào các bạn."},
    {"start": 5.0, "end": 10.0, "text": "Hôm nay mình sẽ hướng dẫn cài đặt Redis."},
    {"start": 10.0, "end": 15.0, "text": "Đầu tiên mở terminal lên."},
    {"start": 15.0, "end": 20.0, "text": "Gõ lệnh apt install redis nhé."},
    {"start": 20.0, "end": 25.0, "text": "Tiếp theo là phần code Python."},
    {"start": 25.0, "end": 30.0, "text": "Chúng ta import redis và kết nối."},
    {"start": 30.0, "end": 35.0, "text": "Redis chạy ở port 6379."}
]

DUMMY_OCR = [
    {"timestamp_sec": 16.0, "detected_text": "sudo apt install redis", "contains_code": True, "code_language": "bash"},
    {"timestamp_sec": 26.0, "detected_text": "import redis\nr = redis.Redis()", "contains_code": True, "code_language": "python"},
]

@pytest.fixture
def memory_qdrant():
    """Khởi tạo Qdrant chạy trên RAM (In-Memory) để test độc lập."""
    client = QdrantClient(location=":memory:")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    return client

def test_semantic_chunking():
    """Test Giai đoạn 3: Phân rã ngữ nghĩa (Semantic Chunking)."""
    # 1. Gọi hàm chunking với threshold cao để chắc chắn nó cắt
    chunks = semantic_chunk_transcript(DUMMY_SEGMENTS, threshold=0.90)
    
    assert len(chunks) > 0
    assert chunks[0]["start_sec"] == 0.0
    assert "raw_transcript" in chunks[0]
    
    # 2. Ghép OCR vào Chunk
    merged_chunks = merge_ocr_into_chunks(chunks, DUMMY_OCR)
    
    # Kiểm tra xem OCR có được lồng vào đúng chunk không
    found_code = any(c.get("contains_code") for c in merged_chunks)
    assert found_code is True

@patch("core.rag_agent._get_current_client")
@patch("core.embedding_engine.get_qdrant_client")
def test_full_rag_pipeline(mock_get_qdrant, mock_get_gpt_client, memory_qdrant):
    """Test Giai đoạn 4: Đẩy Vector và RAG Query."""
    # 1. Bơm Qdrant In-Memory vào engine
    mock_get_qdrant.return_value = memory_qdrant
    
    from core.embedding_engine import index_knowledge_chunks
    from core.rag_agent import ask_youtube_knowledge

    # Chunks giả định đã qua Giai đoạn 3
    fake_chunks = [
        {
            "chunk_index": 0,
            "start_sec": 0, "end_sec": 20,
            "raw_transcript": "Xin chào, hướng dẫn cài đặt Redis. Mở terminal gõ apt install redis.",
            "raw_ocr": "sudo apt install redis",
            "topic": "Cài đặt Redis",
            "summary": "Cách cài đặt Redis trên Ubuntu",
            "contains_code": True,
            "code_language": "bash"
        },
        {
            "chunk_index": 1,
            "start_sec": 20, "end_sec": 35,
            "raw_transcript": "Tiếp theo là code Python, import redis và kết nối. Port 6379.",
            "raw_ocr": "import redis",
            "topic": "Code Python Redis",
            "summary": "Viết code kết nối Redis",
            "contains_code": True,
            "code_language": "python"
        }
    ]

    # 2. Index dữ liệu vào Qdrant (Phase 4)
    indexed_count = index_knowledge_chunks(DUMMY_VIDEO_ID, fake_chunks)
    assert indexed_count == 2
    
    # 3. Mock GPT-4o để không gọi API thật
    mock_gpt_instance = MagicMock()
    mock_gpt_response = MagicMock()
    mock_gpt_response.choices[0].message.content = "Để cài đặt Redis, bạn mở terminal và gõ lệnh apt install redis. [Tài liệu 1]"
    mock_gpt_instance.chat.completions.create.return_value = mock_gpt_response
    mock_get_gpt_client.return_value = mock_gpt_instance
    
    # 4. Thực hiện RAG query (Hỏi chatbot)
    question = "Làm sao để cài đặt Redis?"
    result = ask_youtube_knowledge(question, video_id=DUMMY_VIDEO_ID, limit=1)
    
    assert "answer" in result
    assert "contexts" in result
    assert len(result["contexts"]) == 1 # Vì limit = 1
    
    # Kiểm tra xem Vector Search có tìm đúng chunk "Cài đặt Redis" không
    best_context = result["contexts"][0]
    assert best_context["topic"] == "Cài đặt Redis"
    assert best_context["video_id"] == DUMMY_VIDEO_ID

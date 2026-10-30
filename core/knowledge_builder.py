"""
Knowledge Builder — Xây dựng Tri thức (Semantic Chunking & AI Enrich).

Module này là trái tim của Giai đoạn 3:
- Chia nhỏ transcript thành các đoạn (chunks) theo ngữ nghĩa (Semantic) thay vì thời gian.
- Gộp các kết quả OCR vào đúng chunk tương ứng.
- Dùng AI (GPT-4o) để trích xuất metadata (topic, keywords, entities) từ mỗi chunk.
- Dùng AI (GPT-4o) để gộp các chunk lại thành các sự kiện (Events) lớn.
"""

import json
import time
import numpy as np

# IMPORT CONFIG FIRST TO SET HF_HOME
from utils.config import config

from sentence_transformers import SentenceTransformer

from core.ai_analyzer import _get_current_client, _rotate_token
from database.queries import (
    get_transcript,
    get_ocr_results,
    save_knowledge_chunks,
    save_events,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Singleton cho mô hình Embedding
_embedding_model = None

def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info("🔄 Đang tải model BAAI/bge-m3 cho Semantic Chunking (chạy local)...")
        _embedding_model = SentenceTransformer("BAAI/bge-m3")
        logger.info("✅ Tải model Embedding thành công!")
    return _embedding_model


def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Tính độ tương đồng cosine giữa 2 vector 1D."""
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(dot_product / (norm1 * norm2))


def semantic_chunk_transcript(segments: list[dict], threshold: float = 0.65) -> list[dict]:
    """Phân chia transcript thành các chunk dựa trên độ tương đồng ngữ nghĩa.
    
    1. Gom các segment liền kề thành các "cửa sổ" (window).
    2. Nhúng (embed) các cửa sổ thành vector.
    3. Cắt (split) tại các vị trí mà độ tương đồng cosine giữa 2 cửa sổ kề nhau < threshold.
    """
    if not segments:
        return []

    # Bước 1: Tạo các cửa sổ (windows) ~5 segments mỗi cửa sổ để đủ ngữ cảnh
    windows = []
    temp_segs = []
    
    for seg in segments:
        temp_segs.append(seg)
        if len(temp_segs) >= 5 or seg == segments[-1]:
            text = " ".join([s.get('text', '') for s in temp_segs])
            windows.append({
                'start_sec': temp_segs[0]['start'],
                'end_sec': temp_segs[-1]['end'],
                'text': text,
                'segments': temp_segs.copy()
            })
            temp_segs = []

    if len(windows) <= 1:
        # Quá ngắn, tạo thành 1 chunk duy nhất
        return [{
            'chunk_index': 0,
            'start_sec': windows[0]['start_sec'] if windows else 0,
            'end_sec': windows[0]['end_sec'] if windows else 0,
            'raw_transcript': windows[0]['text'] if windows else "",
            'segments': windows[0]['segments'] if windows else []
        }]

    # Bước 2: Tính vector Embedding
    logger.info(f"🧠 Đang nhúng (embedding) {len(windows)} cửa sổ ngữ cảnh...")
    model = get_embedding_model()
    texts = [w['text'] for w in windows]
    embeddings = model.encode(texts)

    # Bước 3: Tính Cosine Similarity và Cắt chunk
    chunks = []
    current_chunk = {
        'start_sec': windows[0]['start_sec'],
        'end_sec': windows[0]['end_sec'],
        'raw_transcript': windows[0]['text'],
        'segments': windows[0]['segments'].copy()
    }
    
    chunk_index = 0
    for i in range(1, len(windows)):
        sim = _cosine_similarity(embeddings[i-1], embeddings[i])
        
        if sim < threshold:
            # Chủ đề đã thay đổi, cắt chunk hiện tại và bắt đầu chunk mới
            current_chunk['chunk_index'] = chunk_index
            chunks.append(current_chunk)
            chunk_index += 1
            
            current_chunk = {
                'start_sec': windows[i]['start_sec'],
                'end_sec': windows[i]['end_sec'],
                'raw_transcript': windows[i]['text'],
                'segments': windows[i]['segments'].copy()
            }
        else:
            # Vẫn cùng chủ đề, gộp vào chunk hiện tại
            current_chunk['end_sec'] = windows[i]['end_sec']
            current_chunk['raw_transcript'] += " " + windows[i]['text']
            current_chunk['segments'].extend(windows[i]['segments'])

    # Thêm chunk cuối cùng
    current_chunk['chunk_index'] = chunk_index
    chunks.append(current_chunk)

    logger.info(f"🔪 Đã cắt {len(segments)} segments thành {len(chunks)} Semantic Chunks (Ngưỡng {threshold}).")
    return chunks


def merge_ocr_into_chunks(chunks: list[dict], ocr_results: list[dict]) -> list[dict]:
    """Gộp dữ liệu OCR vào các chunk tương ứng dựa trên timestamp.
    
    Args:
        chunks: Danh sách chunks đã tạo từ semantic_chunk_transcript.
        ocr_results: Kết quả truy vấn bảng ocr_results join với frames (có cột timestamp_sec).
    """
    for chunk in chunks:
        start = chunk['start_sec']
        end = chunk['end_sec']
        
        # Tìm các OCR results nằm trong khoảng thời gian của chunk này
        relevant_ocrs = []
        has_code = False
        code_langs = set()
        
        for ocr in ocr_results:
            t = float(ocr.get('timestamp_sec', 0))
            if start <= t <= end:
                if ocr.get('detected_text'):
                    relevant_ocrs.append(f"[{t}s]: {ocr.get('detected_text')}")
                if ocr.get('contains_code'):
                    has_code = True
                    if ocr.get('code_language'):
                        code_langs.add(ocr.get('code_language'))
                        
        chunk['raw_ocr'] = "\n".join(relevant_ocrs) if relevant_ocrs else None
        chunk['contains_code'] = has_code
        chunk['code_language'] = ", ".join(code_langs) if code_langs else None

    return chunks


def enrich_chunk_metadata(chunks: list[dict]) -> list[dict]:
    """Dùng GPT-4o để phân tích sâu (enrich) metadata cho từng chunk.
    
    Sử dụng batch processing nếu có nhiều chunks để tiết kiệm thời gian.
    Vì số lượng chunks có thể lớn, ta sẽ duyệt từng chunk hoặc gom nhóm.
    """
    if not config.GITHUB_TOKENS:
        logger.error("❌ CHƯA CẤU HÌNH GITHUB_TOKENS TRONG FILE .ENV")
        return chunks

    logger.info(f"✨ Kích hoạt AI Enrich cho {len(chunks)} Chunks...")
    client = _get_current_client()
    max_retries = max(5, len(config.GITHUB_TOKENS) * 2)
    
    for i, chunk in enumerate(chunks):
        prompt = f"""
Bạn là chuyên gia phân tích dữ liệu video.
Dưới đây là phần Transcript và nội dung màn hình (OCR) của MỘT đoạn video.
Hãy phân tích và TRẢ VỀ JSON hợp lệ (không kèm markdown) chứa thông tin metadata.

Cấu trúc JSON yêu cầu:
{{
    "topic": "<Chủ đề chính ngắn gọn (1-5 từ)>",
    "summary": "<Tóm tắt nội dung chính (1-3 câu)>",
    "keywords": ["từ khóa 1", "từ khóa 2", "từ khóa 3"],
    "entities": ["công cụ/framework 1", "khái niệm 2"]
}}

[DỮ LIỆU ĐOẠN VIDEO]:
Transcript: {chunk.get('raw_transcript')}
Màn hình (OCR): {chunk.get('raw_ocr') or 'Không có thông tin trên màn hình'}
"""
        success = False
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model="openai/gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                raw_text = response.choices[0].message.content.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:-3].strip()
                elif raw_text.startswith("```"):
                    raw_text = raw_text[3:-3].strip()
                
                result = json.loads(raw_text)
                
                chunk['topic'] = result.get('topic')
                chunk['summary'] = result.get('summary')
                chunk['keywords'] = result.get('keywords', [])
                chunk['entities'] = result.get('entities', [])
                success = True
                break
                
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "Too many requests" in error_msg:
                    logger.warning(f"⚠️ Rate Limit khi enrich chunk {i}. Đang xoay token...")
                    if len(config.GITHUB_TOKENS) > 1:
                        client = _rotate_token()
                        time.sleep(1)
                    else:
                        time.sleep(10)
                else:
                    logger.error(f"❌ Lỗi AI Enrich cho chunk {i}: {e}")
                    break
        
        if not success:
            logger.warning(f"⚠️ Chunk {i} bị bỏ qua do lỗi liên tục.")
            chunk['topic'] = "Lỗi phân tích"
            chunk['summary'] = "Lỗi phân tích"
            chunk['keywords'] = []
            chunk['entities'] = []

    return chunks


def detect_events(chunks: list[dict]) -> list[dict]:
    """Gom nhóm các chunk liên quan thành các SỰ KIỆN (Events) lớn.
    
    VD: Chunks nói về tải Redis, cấu hình Redis sẽ được gom thành sự kiện "Cài đặt Redis".
    """
    if not chunks:
        return []

    logger.info("🎬 Đang phân tích các Sự kiện (Events) của video...")
    
    # Tạo payload rút gọn
    chunks_info = []
    for c in chunks:
        chunks_info.append({
            "chunk_id": c.get("chunk_index"),
            "topic": c.get("topic"),
            "summary": c.get("summary"),
            "start": c.get("start_sec"),
            "end": c.get("end_sec"),
        })

    prompt = f"""
Bạn là một Đạo diễn Nội dung. Dưới đây là danh sách các "Chunks" (chủ đề theo dòng thời gian) của một video.
Hãy phân tích sự liên kết và nhóm các Chunks liên quan lại thành các SỰ KIỆN LỚN (Events).
Ví dụ: Các chunk 1, 2, 3 đều hướng dẫn cài đặt -> Gom lại thành sự kiện "Hướng dẫn Cài đặt".

Trả về MỘT MẢNG JSON, không dùng markdown. Cấu trúc mỗi Event:
{{
    "event_type": "<tutorial_step | explanation | debug | comparison | opinion | other>",
    "event_name": "<Tên sự kiện>",
    "description": "<Mô tả chi tiết 1-2 câu>",
    "chunk_ids": [<danh sách các chunk_id thuộc event này>]
}}

[DANH SÁCH CHUNKS]:
{json.dumps(chunks_info, ensure_ascii=False)}
"""

    client = _get_current_client()
    max_retries = max(5, len(config.GITHUB_TOKENS) * 2)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3].strip()
            
            events_data = json.loads(raw_text)
            
            # Gắn timestamp cho events dựa trên chunk_ids
            for ev in events_data:
                c_ids = ev.get("chunk_ids", [])
                start_sec = 999999
                end_sec = 0
                for c in chunks:
                    if c["chunk_index"] in c_ids:
                        start_sec = min(start_sec, c["start_sec"])
                        end_sec = max(end_sec, c["end_sec"])
                ev["start_sec"] = start_sec if start_sec != 999999 else 0
                ev["end_sec"] = end_sec
            
            return events_data

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Too many requests" in error_msg:
                if len(config.GITHUB_TOKENS) > 1:
                    client = _rotate_token()
                    time.sleep(1)
                else:
                    time.sleep(10)
            else:
                logger.error(f"❌ Lỗi tạo Events: {e}")
                return []
                
    return []


def build_knowledge_for_video(video_id: str) -> dict:
    """Orchestrate toàn bộ tiến trình Knowledge Builder (Giai đoạn 3)."""
    logger.info(f"🚀 BẮT ĐẦU XÂY DỰNG TRI THỨC CHO VIDEO: {video_id}")
    
    # Lấy transcript
    transcript = get_transcript(video_id)
    if not transcript or not transcript.get("segments"):
        logger.warning(f"⚠️ Video {video_id} không có transcript. Không thể xây dựng tri thức.")
        return {"chunks": 0, "events": 0}
        
    segments = transcript["segments"]
    if isinstance(segments, str):
        segments = json.loads(segments)
        
    # Bước 1: Chia Semantic Chunks
    chunks = semantic_chunk_transcript(segments)
    
    # Bước 2: Ghép OCR vào Chunks
    ocr_results = get_ocr_results(video_id)
    chunks = merge_ocr_into_chunks(chunks, ocr_results)
    
    # Bước 3: Gọi AI Enrich Metadata cho từng chunk
    chunks = enrich_chunk_metadata(chunks)
    
    # Bước 4: Gọi AI phân tích Events
    events = detect_events(chunks)
    
    # Bước 5: Lưu kết quả vào PostgreSQL
    saved_chunks = save_knowledge_chunks(video_id, chunks)
    saved_events = save_events(video_id, events)
    
    logger.info(f"✅ XÂY DỰNG TRI THỨC THÀNH CÔNG: {saved_chunks} chunks, {saved_events} events!")
    
    return {
        "chunks": saved_chunks,
        "events": saved_events
    }

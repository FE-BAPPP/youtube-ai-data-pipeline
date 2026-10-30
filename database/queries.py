"""
Database Query Functions.

Tất cả các hàm truy vấn cơ sở dữ liệu, trích xuất từ database.py gốc.
Sử dụng connection pool thông qua get_db_connection() context manager.
"""

import psycopg2.extras

from database.connection import get_db_connection
from utils.logger import get_logger

logger = get_logger(__name__)


def save_analyzed_comments(comments: list[dict]) -> int:
    """Lưu danh sách bình luận đã phân tích vào Database.

    Sử dụng execute_values với ON CONFLICT DO NOTHING (chống ghi đúp).

    Args:
        comments: Danh sách dict chứa dữ liệu bình luận và kết quả AI.

    Returns:
        Số lượng bình luận đã INSERT thành công.
    """
    if not comments:
        return 0

    # Sử dụng execute_values với UNIQUE(comment_id) và ON CONFLICT DO NOTHING (chống ghi đúp)
    insert_query = """
    INSERT INTO ai_analyzed_comments 
    (comment_id, video_id, author, comment_text, published_at, category, sentiment_score, toxicity_level, user_intent, key_insight, suggested_action, thinking_process)
    VALUES %s
    ON CONFLICT (comment_id) DO NOTHING
    """

    values_list = []
    for item in comments:
        ai_data = item.get("ai_analysis", {})
        values_list.append((
            item.get("comment_id"),                                              # Thêm comment_id từ payload
            item.get("video_id", "Unknown"),
            item.get("author", "Unknown"),
            item.get("text", ""),
            item.get("published_at") if item.get("published_at") else None,      # Chống lỗi empty string khi insert vào TIMESTAMP
            ai_data.get("category"),
            ai_data.get("sentiment_score"),
            ai_data.get("toxicity_level"),
            ai_data.get("user_intent"),
            ai_data.get("key_insight"),
            ai_data.get("suggested_action"),
            ai_data.get("thinking_process"),
        ))

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                psycopg2.extras.execute_values(cursor, insert_query, values_list)
                conn.commit()
                inserted = cursor.rowcount
                logger.info(
                    f"🗄️ Đã INSERT mới thành công {inserted}/{len(comments)} "
                    f"bình luận vào Database PostgreSQL!"
                )
                return inserted
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi khi Insert dữ liệu: {e}")
        return 0


def get_pending_videos() -> list[dict]:
    """Lấy danh sách video có trạng thái PENDING.

    Returns:
        Danh sách dict chứa thông tin video đang chờ xử lý.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(
                    "SELECT video_id, title, channel_name, status, created_at "
                    "FROM videos WHERE status = 'PENDING' ORDER BY created_at ASC"
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy danh sách video PENDING: {e}")
        return []


def get_processing_videos() -> list[dict]:
    """Lấy danh sách video có trạng thái PROCESSING.

    Returns:
        Danh sách dict chứa thông tin video đang được xử lý.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(
                    "SELECT video_id, title, channel_name, status, created_at "
                    "FROM videos WHERE status = 'PROCESSING' ORDER BY created_at ASC"
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy danh sách video PROCESSING: {e}")
        return []


def insert_video(video_id: str, title: str = "Unknown", channel_name: str = "Unknown") -> None:
    """Ghi nhận video mới vào bảng quản lý.

    Sử dụng ON CONFLICT DO UPDATE SET status = 'PROCESSING' để cập nhật
    video đã tồn tại sang trạng thái đang xử lý.

    Args:
        video_id: ID video YouTube.
        title: Tiêu đề video.
        channel_name: Tên kênh YouTube.
    """
    query = """
    INSERT INTO videos (video_id, title, channel_name, status)
    VALUES (%s, %s, %s, 'PROCESSING')
    ON CONFLICT (video_id) DO UPDATE SET status = 'PROCESSING'
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, (video_id, title, channel_name))
                conn.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi ghi nhận Video: {e}")


def update_video_status(video_id: str, status: str) -> None:
    """Cập nhật trạng thái của video.

    Args:
        video_id: ID video YouTube.
        status: Trạng thái mới (PENDING / PROCESSING / COMPLETED).
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE videos SET status = %s WHERE video_id = %s",
                    (status, video_id),
                )
                conn.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi cập nhật trạng thái Video: {e}")


def create_job(video_id: str, job_type: str) -> int | None:
    """Tạo 1 công việc (Job) mới và trả về ID của Job đó.

    Args:
        video_id: ID video YouTube liên quan.
        job_type: Loại job (SCRAPE_JOB / AI_JOB).

    Returns:
        ID của job vừa tạo, hoặc None nếu thất bại.
    """
    query = """
    INSERT INTO processing_jobs (video_id, job_type, status)
    VALUES (%s, %s, 'RUNNING') RETURNING id;
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, (video_id, job_type))
                job_id = cursor.fetchone()[0]
                conn.commit()
                return job_id
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi tạo Job: {e}")
        return None


def update_job_status(job_id: int, status: str, items_processed: int = 0) -> None:
    """Cập nhật trạng thái và số lượng sau khi chạy xong.

    Args:
        job_id: ID của job cần cập nhật.
        status: Trạng thái mới (RUNNING / COMPLETED / FAILED).
        items_processed: Số lượng item đã xử lý.
    """
    if not job_id:
        return

    query = """
    UPDATE processing_jobs 
    SET status = %s, items_processed = %s, finished_at = CURRENT_TIMESTAMP
    WHERE id = %s
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, (status, items_processed, job_id))
                conn.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi cập nhật Job: {e}")


def get_dashboard_data() -> dict:
    """Lấy dữ liệu tổng hợp cho Dashboard.

    Returns:
        Dict chứa danh sách videos và jobs để hiển thị trên Dashboard.
    """
    result: dict = {"videos": [], "jobs": []}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                # Lấy danh sách video
                cursor.execute(
                    "SELECT video_id, title, channel_name, quota_used, status, created_at "
                    "FROM videos ORDER BY created_at DESC LIMIT 50"
                )
                result["videos"] = [dict(row) for row in cursor.fetchall()]

                # Lấy danh sách job
                cursor.execute(
                    "SELECT id, video_id, job_type, status, items_processed, "
                    "started_at, finished_at "
                    "FROM processing_jobs ORDER BY started_at DESC LIMIT 50"
                )
                result["jobs"] = [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy dữ liệu Dashboard: {e}")

    return result


def get_comments_for_ideation(video_ids: list[str], limit: int = 40) -> list[dict]:
    """Lấy danh sách bình luận đã phân tích để làm dữ liệu đẻ ý tưởng video.
    
    Ưu tiên lấy các bình luận có category là KHACH_HOI_DAP hoặc TIEU_CUC_GOP_Y
    để biết khán giả đang thắc mắc gì hoặc chưa hài lòng ở điểm nào.
    
    Args:
        video_ids: Danh sách ID video YouTube để lấy bình luận.
        limit: Số lượng tối đa bình luận trả về.
        
    Returns:
        Danh sách dict chứa nội dung và phân tích AI của bình luận.
    """
    if not video_ids:
        return []
        
    # Tạo chuỗi placeholder %s cho mệnh đề IN
    placeholders = ', '.join(['%s'] * len(video_ids))
    
    query = f"""
    SELECT comment_id, author, comment_text, category, user_intent, key_insight
    FROM ai_analyzed_comments 
    WHERE video_id IN ({placeholders})
      AND category IN ('KHACH_HOI_DAP', 'TIEU_CUC_GOP_Y', 'TICH_CUC')
      AND toxicity_level < 8
    ORDER BY sentiment_score ASC, published_at DESC
    LIMIT %s
    """
    
    params = tuple(video_ids) + (limit,)
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy bình luận để tạo ý tưởng: {e}")
        return []


def get_negative_comments(video_ids: list[str], limit: int = 40) -> list[dict]:
    """Lấy danh sách các bình luận chê bai, tiêu cực để bắt lỗi đối thủ.
    
    Chỉ tập trung vào TIEU_CUC_GOP_Y và DOC_HAI_CHUI_BOI, 
    sắp xếp từ cay nghiệt nhất (sentiment thấp nhất) lên đầu.
    """
    if not video_ids:
        return []
        
    placeholders = ', '.join(['%s'] * len(video_ids))
    
    query = f"""
    SELECT comment_id, author, comment_text, category, user_intent, key_insight, sentiment_score
    FROM ai_analyzed_comments 
    WHERE video_id IN ({placeholders})
      AND category IN ('TIEU_CUC_GOP_Y', 'DOC_HAI_CHUI_BOI')
    ORDER BY sentiment_score ASC, published_at DESC
    LIMIT %s
    """
    
    params = tuple(video_ids) + (limit,)
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy bình luận tiêu cực: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# TRANSCRIPT & FRAME QUERIES (Phase 1 — Ingest Pipeline)
# ─────────────────────────────────────────────────────────────────

def save_transcript(video_id: str, transcript_data: dict) -> bool:
    """Lưu transcript vào database.

    Sử dụng ON CONFLICT DO UPDATE để cập nhật nếu đã tồn tại.

    Args:
        video_id: ID video YouTube.
        transcript_data: Dict từ media_processor.get_transcript_from_youtube()
            hoặc từ Whisper API. Phải có: source, raw_text, segments.

    Returns:
        True nếu lưu thành công, False nếu thất bại.
    """
    query = """
    INSERT INTO transcripts (video_id, source, language, raw_text, segments, duration_sec)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (video_id, source)
    DO UPDATE SET raw_text = EXCLUDED.raw_text,
                  segments = EXCLUDED.segments,
                  duration_sec = EXCLUDED.duration_sec
    """
    try:
        import json
        segments_json = json.dumps(transcript_data.get("segments", []), ensure_ascii=False)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, (
                    video_id,
                    transcript_data.get("source", "unknown"),
                    transcript_data.get("language", "vi"),
                    transcript_data.get("raw_text", ""),
                    segments_json,
                    transcript_data.get("duration_sec"),
                ))
                conn.commit()
                logger.info(
                    "📝 Đã lưu transcript cho video %s (source: %s)",
                    video_id, transcript_data.get("source"),
                )
                return True
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lưu transcript: {e}")
        return False


def save_frames_metadata(video_id: str, frames: list[dict]) -> int:
    """Lưu metadata của các frames đã trích xuất vào database.

    Args:
        video_id: ID video YouTube.
        frames: Danh sách dict từ media_processor.extract_frames().

    Returns:
        Số lượng frames đã lưu thành công.
    """
    if not frames:
        return 0

    query = """
    INSERT INTO frames (video_id, frame_index, timestamp_sec, file_path)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (video_id, frame_index) DO NOTHING
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                count = 0
                for f in frames:
                    cursor.execute(query, (
                        video_id,
                        f["frame_index"],
                        f["timestamp_sec"],
                        f.get("file_path"),
                    ))
                    count += cursor.rowcount
                conn.commit()
                logger.info("🖼️ Đã lưu %d/%d frames metadata cho video %s", count, len(frames), video_id)
                return count
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lưu frames metadata: {e}")
        return 0


def get_transcript(video_id: str) -> dict | None:
    """Lấy transcript của video từ database.

    Ưu tiên lấy transcript từ 'whisper' trước (chính xác hơn),
    nếu không có thì lấy từ 'youtube_api'.

    Returns:
        Dict transcript hoặc None nếu chưa có.
    """
    query = """
    SELECT id, video_id, source, language, raw_text, segments, duration_sec
    FROM transcripts
    WHERE video_id = %s
    ORDER BY CASE source WHEN 'whisper' THEN 0 ELSE 1 END
    LIMIT 1
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, (video_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy transcript: {e}")
        return None


def get_frames(video_id: str, processed_only: bool = False) -> list[dict]:
    """Lấy danh sách frames của video từ database.

    Args:
        video_id: ID video YouTube.
        processed_only: Nếu True, chỉ lấy frames đã qua OCR/Vision.

    Returns:
        Danh sách dict thông tin frame.
    """
    condition = "AND processed = TRUE" if processed_only else ""
    query = f"""
    SELECT id, video_id, frame_index, timestamp_sec, file_path, processed
    FROM frames
    WHERE video_id = %s {condition}
    ORDER BY frame_index ASC
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, (video_id,))
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy frames: {e}")
        return []


def has_transcript(video_id: str) -> bool:
    """Kiểm tra video đã có transcript trong DB chưa."""
    query = "SELECT 1 FROM transcripts WHERE video_id = %s LIMIT 1"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, (video_id,))
                return cursor.fetchone() is not None
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi kiểm tra transcript: {e}")
        return False


# ─────────────────────────────────────────────────────────────────
# OCR RESULTS QUERIES (Phase 2 — Extract Modalities)
# ─────────────────────────────────────────────────────────────────

def save_ocr_result(frame_id: int, video_id: str, ocr_data: dict) -> bool:
    """Lưu kết quả OCR cho 1 frame vào database.

    Args:
        frame_id: ID frame trong bảng frames.
        video_id: ID video YouTube.
        ocr_data: Dict kết quả OCR từ ocr_engine.extract_text_from_frame().
            Phải có: detected_text, contains_code, code_language, confidence.

    Returns:
        True nếu lưu thành công, False nếu thất bại.
    """
    query = """
    INSERT INTO ocr_results (frame_id, video_id, detected_text, contains_code, code_language, confidence)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (frame_id) DO UPDATE SET
        detected_text = EXCLUDED.detected_text,
        contains_code = EXCLUDED.contains_code,
        code_language = EXCLUDED.code_language,
        confidence = EXCLUDED.confidence
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, (
                    frame_id,
                    video_id,
                    ocr_data.get("detected_text", ""),
                    ocr_data.get("contains_code", False),
                    ocr_data.get("code_language"),
                    ocr_data.get("confidence", 0.0),
                ))
                conn.commit()
                logger.info(
                    "🔍 Đã lưu OCR result cho frame %d (video: %s, has_code: %s)",
                    frame_id, video_id, ocr_data.get("contains_code", False),
                )
                return True
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lưu OCR result: {e}")
        return False


def save_ocr_results_batch(results: list[dict]) -> int:
    """Lưu kết quả OCR cho nhiều frame trong MỘT round-trip DB.

    Dùng execute_values để insert hàng loạt thay vì gọi save_ocr_result()
    từng frame (giảm số round-trip DB từ N xuống 1).

    Args:
        results: Danh sách dict, mỗi dict gồm:
            {"frame_id": int, "video_id": str, "ocr_data": dict}

    Returns:
        Số lượng bản ghi đã ghi thành công.
    """
    if not results:
        return 0

    query = """
    INSERT INTO ocr_results (frame_id, video_id, detected_text, contains_code, code_language, confidence)
    VALUES %s
    ON CONFLICT (frame_id) DO UPDATE SET
        detected_text = EXCLUDED.detected_text,
        contains_code = EXCLUDED.contains_code,
        code_language = EXCLUDED.code_language,
        confidence = EXCLUDED.confidence
    """
    values_list = []
    for item in results:
        ocr = item.get("ocr_data", {})
        values_list.append((
            item.get("frame_id"),
            item.get("video_id"),
            ocr.get("detected_text", ""),
            ocr.get("contains_code", False),
            ocr.get("code_language"),
            ocr.get("confidence", 0.0),
        ))

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                psycopg2.extras.execute_values(cursor, query, values_list)
                conn.commit()
                logger.info("🔍 Đã lưu batch %d OCR results", len(values_list))
                return len(values_list)
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lưu batch OCR results: {e}")
        return 0


def mark_frames_processed_batch(frame_ids: list[int]) -> None:
    """Đánh dấu nhiều frame đã xử lý trong MỘT round-trip DB.

    Args:
        frame_ids: Danh sách ID frame cần đánh dấu processed = TRUE.
    """
    if not frame_ids:
        return
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE frames SET processed = TRUE WHERE id = ANY(%s)",
                    (list(frame_ids),),
                )
                conn.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi đánh dấu batch frames processed: {e}")


def mark_frame_processed(frame_id: int) -> None:
    """Đánh dấu frame đã được xử lý qua OCR/Vision.

    Args:
        frame_id: ID frame trong bảng frames.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE frames SET processed = TRUE WHERE id = %s",
                    (frame_id,),
                )
                conn.commit()
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi đánh dấu frame processed: {e}")


def get_unprocessed_frames(video_id: str) -> list[dict]:
    """Lấy danh sách frames chưa qua OCR/Vision cho 1 video.

    Args:
        video_id: ID video YouTube.

    Returns:
        Danh sách dict thông tin frame chưa được xử lý.
    """
    query = """
    SELECT id, video_id, frame_index, timestamp_sec, file_path
    FROM frames
    WHERE video_id = %s AND processed = FALSE
    ORDER BY frame_index ASC
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, (video_id,))
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy unprocessed frames: {e}")
        return []


def get_ocr_results(video_id: str) -> list[dict]:
    """Lấy tất cả kết quả OCR cho 1 video.

    Args:
        video_id: ID video YouTube.

    Returns:
        Danh sách dict kết quả OCR, sắp xếp theo frame_id.
    """
    query = """
    SELECT o.id, o.frame_id, o.video_id, o.detected_text, o.contains_code,
           o.code_language, o.confidence, o.created_at, f.timestamp_sec
    FROM ocr_results o
    JOIN frames f ON o.frame_id = f.id
    WHERE o.video_id = %s
    ORDER BY f.timestamp_sec ASC
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, (video_id,))
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy OCR results: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# KNOWLEDGE CHUNKS & EVENTS QUERIES (Phase 3 — Knowledge Builder)
# ─────────────────────────────────────────────────────────────────

def save_knowledge_chunks(video_id: str, chunks: list[dict]) -> int:
    """Lưu danh sách knowledge chunks vào database.
    
    Args:
        video_id: ID video YouTube.
        chunks: Danh sách dict thông tin chunk đã được enrich.
        
    Returns:
        Số lượng chunk đã lưu thành công.
    """
    if not chunks:
        return 0

    query = """
    INSERT INTO knowledge_chunks (
        video_id, chunk_index, start_sec, end_sec, raw_transcript, raw_ocr, 
        topic, summary, keywords, contains_code, code_language, entities
    ) VALUES %s
    ON CONFLICT (video_id, chunk_index) DO UPDATE SET
        start_sec = EXCLUDED.start_sec,
        end_sec = EXCLUDED.end_sec,
        raw_transcript = EXCLUDED.raw_transcript,
        raw_ocr = EXCLUDED.raw_ocr,
        topic = EXCLUDED.topic,
        summary = EXCLUDED.summary,
        keywords = EXCLUDED.keywords,
        contains_code = EXCLUDED.contains_code,
        code_language = EXCLUDED.code_language,
        entities = EXCLUDED.entities
    """
    
    import json
    values_list = []
    for chunk in chunks:
        values_list.append((
            video_id,
            chunk.get("chunk_index"),
            chunk.get("start_sec"),
            chunk.get("end_sec"),
            chunk.get("raw_transcript", ""),
            chunk.get("raw_ocr"),
            chunk.get("topic"),
            chunk.get("summary"),
            json.dumps(chunk.get("keywords", []), ensure_ascii=False) if chunk.get("keywords") else None,
            chunk.get("contains_code", False),
            chunk.get("code_language"),
            json.dumps(chunk.get("entities", []), ensure_ascii=False) if chunk.get("entities") else None
        ))
        
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                psycopg2.extras.execute_values(cursor, query, values_list)
                conn.commit()
                logger.info(f"📚 Đã lưu {cursor.rowcount} knowledge chunks cho video {video_id}")
                return cursor.rowcount
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lưu knowledge chunks: {e}")
        return 0


def save_events(video_id: str, events: list[dict]) -> int:
    """Lưu danh sách events vào database.
    
    Xóa events cũ của video và thêm mới để đồng bộ.
    
    Args:
        video_id: ID video YouTube.
        events: Danh sách dict events.
        
    Returns:
        Số lượng events đã lưu thành công.
    """
    if not events:
        return 0

    delete_query = "DELETE FROM events WHERE video_id = %s"
    insert_query = """
    INSERT INTO events (video_id, event_type, event_name, start_sec, end_sec, chunk_ids, description)
    VALUES %s
    """
    
    import json
    values_list = []
    for event in events:
        values_list.append((
            video_id,
            event.get("event_type"),
            event.get("event_name"),
            event.get("start_sec"),
            event.get("end_sec"),
            json.dumps(event.get("chunk_ids", []), ensure_ascii=False) if event.get("chunk_ids") else None,
            event.get("description")
        ))
        
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # Xóa dữ liệu cũ
                cursor.execute(delete_query, (video_id,))
                
                # Insert dữ liệu mới
                psycopg2.extras.execute_values(cursor, insert_query, values_list)
                conn.commit()
                logger.info(f"🎬 Đã lưu {cursor.rowcount} events cho video {video_id}")
                return cursor.rowcount
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lưu events: {e}")
        return 0


def get_knowledge_chunks(video_id: str) -> list[dict]:
    """Lấy danh sách knowledge chunks của video.
    
    Args:
        video_id: ID video YouTube.
        
    Returns:
        Danh sách dict chứa knowledge chunks.
    """
    query = "SELECT * FROM knowledge_chunks WHERE video_id = %s ORDER BY chunk_index ASC"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cursor.execute(query, (video_id,))
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi lấy knowledge chunks: {e}")
        return []



"""
SQL Schema Definitions.

Chứa tất cả câu lệnh SQL để tạo bảng và view cho hệ thống.
Giữ nguyên 100% cấu trúc SQL từ database.py gốc.
"""

# ---------------------------------------------------------------------------
# Bảng Data chính — ai_analyzed_comments
# ĐÃ THÊM 2 CỘT MỚI: toxicity_level và user_intent
# Đã sửa published_at thành TIMESTAMP WITH TIME ZONE
# ---------------------------------------------------------------------------
CREATE_COMMENTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS ai_analyzed_comments (
    id SERIAL PRIMARY KEY,
    comment_id VARCHAR(100) UNIQUE,
    video_id VARCHAR(50),
    author VARCHAR(255),
    comment_text TEXT,
    published_at TIMESTAMP WITH TIME ZONE,
    category VARCHAR(50),
    sentiment_score INT,
    toxicity_level INT,
    user_intent TEXT,
    key_insight TEXT,
    suggested_action VARCHAR(50),
    thinking_process TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Bảng quản lý chiến dịch Video (Metadata & Tracking)
# ---------------------------------------------------------------------------
CREATE_VIDEOS_TABLE: str = """
CREATE TABLE IF NOT EXISTS videos (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) UNIQUE,
    title VARCHAR(255),
    channel_name VARCHAR(255),
    quota_used INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Bảng theo dõi tiến độ Job (Scraping / AI Processing)
# ---------------------------------------------------------------------------
CREATE_JOBS_TABLE: str = """
CREATE TABLE IF NOT EXISTS processing_jobs (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    job_type VARCHAR(50),
    status VARCHAR(50) DEFAULT 'RUNNING',
    items_processed INT DEFAULT 0,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Khởi tạo các View cho Metabase
# ---------------------------------------------------------------------------
CREATE_VIEWS: str = """
CREATE OR REPLACE VIEW vw_top_toxic_users AS
SELECT author, COUNT(*) as toxic_comment_count, ROUND(AVG(toxicity_level), 2) as avg_toxicity
FROM ai_analyzed_comments WHERE toxicity_level >= 7
GROUP BY author ORDER BY toxic_comment_count DESC;

CREATE OR REPLACE VIEW vw_sentiment_trend AS
SELECT DATE(published_at) as comment_date, ROUND(AVG(sentiment_score), 2) as avg_sentiment, COUNT(*) as total_comments
FROM ai_analyzed_comments GROUP BY DATE(published_at) ORDER BY comment_date ASC;

CREATE OR REPLACE VIEW vw_toxic_ratio_by_hour AS
SELECT DATE_TRUNC('hour', published_at) as comment_hour, COUNT(*) as total_comments,
SUM(CASE WHEN toxicity_level >= 7 THEN 1 ELSE 0 END) as toxic_comments,
ROUND(SUM(CASE WHEN toxicity_level >= 7 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 2) as toxic_percentage
FROM ai_analyzed_comments GROUP BY DATE_TRUNC('hour', published_at) ORDER BY comment_hour ASC;
"""

# ---------------------------------------------------------------------------
# Bảng lưu Transcript (nguồn chân lý — raw data)
# Source: 'youtube_api' (phụ đề có sẵn) hoặc 'whisper' (AI dịch audio)
# ---------------------------------------------------------------------------
CREATE_TRANSCRIPTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS transcripts (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    source VARCHAR(20) NOT NULL,
    language VARCHAR(10) DEFAULT 'vi',
    raw_text TEXT NOT NULL,
    segments JSONB,
    duration_sec FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, source)
);
"""

# ---------------------------------------------------------------------------
# Bảng lưu thông tin từng Frame đã trích xuất
# ---------------------------------------------------------------------------
CREATE_FRAMES_TABLE: str = """
CREATE TABLE IF NOT EXISTS frames (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    frame_index INT NOT NULL,
    timestamp_sec FLOAT NOT NULL,
    file_path VARCHAR(500),
    processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, frame_index)
);
"""

# ---------------------------------------------------------------------------
# Bảng kết quả OCR từ mỗi frame
# ---------------------------------------------------------------------------
CREATE_OCR_RESULTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS ocr_results (
    id SERIAL PRIMARY KEY,
    frame_id INT REFERENCES frames(id) UNIQUE,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    detected_text TEXT,
    contains_code BOOLEAN DEFAULT FALSE,
    code_language VARCHAR(20),
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Bảng Knowledge Chunks (semantic chunks + AI enrichment)
# raw_transcript & raw_ocr = Source of Truth, AI chỉ enrich metadata
# ---------------------------------------------------------------------------
CREATE_KNOWLEDGE_CHUNKS_TABLE: str = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    chunk_index INT NOT NULL,
    start_sec FLOAT NOT NULL,
    end_sec FLOAT NOT NULL,

    raw_transcript TEXT NOT NULL,
    raw_ocr TEXT,

    topic VARCHAR(255),
    summary TEXT,
    keywords JSONB,
    contains_code BOOLEAN DEFAULT FALSE,
    code_language VARCHAR(20),
    entities JSONB,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, chunk_index)
);
"""

# ---------------------------------------------------------------------------
# Bảng Events (chuỗi hành động có ngữ cảnh, gom nhiều chunks)
# ---------------------------------------------------------------------------
CREATE_EVENTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    event_type VARCHAR(50),
    event_name VARCHAR(255),
    start_sec FLOAT NOT NULL,
    end_sec FLOAT NOT NULL,
    chunk_ids JSONB,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

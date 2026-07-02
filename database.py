import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "123456")
DB_NAME = os.getenv("DB_NAME", "youtube_ai_db")

def get_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname=DB_NAME
        )
    except Exception as e:
        print(f"❌ Không thể kết nối tới Database. Lỗi: {e}")
        return None

def init_db():
    conn = get_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        # ĐÃ THÊM 2 CỘT MỚI: toxicity_level và user_intent
        create_table_query = """
        CREATE TABLE IF NOT EXISTS ai_analyzed_comments (
            id SERIAL PRIMARY KEY,
            video_id VARCHAR(50),
            author VARCHAR(255),
            comment_text TEXT,
            published_at VARCHAR(50),
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
        cursor.execute(create_table_query)
        conn.commit()
    except Exception as e:
        print(f"❌ Lỗi khi tạo bảng: {e}")
    finally:
        cursor.close()
        conn.close()

def save_comments_to_db(comments):
    conn = get_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        # Cập nhật câu lệnh INSERT để chèn 2 cột mới
        insert_query = """
        INSERT INTO ai_analyzed_comments 
        (video_id, author, comment_text, published_at, category, sentiment_score, toxicity_level, user_intent, key_insight, suggested_action, thinking_process)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        count = 0
        for item in comments:
            ai_data = item.get("ai_analysis", {})
            values = (
                item.get("video_id", "Unknown"),
                item.get("author", "Unknown"),
                item.get("text", ""),
                item.get("published_at", ""),
                ai_data.get("category"),
                ai_data.get("sentiment_score"),
                ai_data.get("toxicity_level"),  # <-- NEW
                ai_data.get("user_intent"),     # <-- NEW
                ai_data.get("key_insight"),
                ai_data.get("suggested_action"),
                ai_data.get("thinking_process")
            )
            cursor.execute(insert_query, values)
            count += 1
            
        conn.commit()
        print(f"🗄️ Đã INSERT thành công {count} bình luận vào Database PostgreSQL!")
    except Exception as e:
        print(f"❌ Lỗi khi Insert dữ liệu: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    init_db()

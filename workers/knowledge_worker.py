"""
Knowledge Worker — Giai đoạn 3: Phân tích & Cấu trúc hóa Tri thức.

Lắng nghe queue `video_knowledge`. 
- Nhận lệnh chứa `video_id`.
- Chạy Knowledge Builder: Semantic Chunking, OCR merging, Enrich AI, Event Detection.
- Cập nhật tiến độ `processing_jobs`.
- Gửi lệnh sang `video_embedding` để Giai đoạn 4 nạp vào Vector DB.
"""

import json
import os
import signal
import sys
import pika
import pika.spec

# Thêm root path để import được thư viện trong dự án
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.queries import create_job, update_job_status, get_knowledge_chunks
from core.knowledge_builder import build_knowledge_for_video
from core.embedding_engine import index_knowledge_chunks
from workers.rabbitmq_utils import connect_rabbitmq
from utils.logger import get_logger
from utils.config import config

logger = get_logger("knowledge_worker")

# Cờ để xử lý tắt worker an toàn
_is_running = True

def handle_sigterm(*args):
    """Xử lý tín hiệu tắt (Ctrl+C, Docker stop) an toàn."""
    global _is_running
    logger.info("⚠️ Nhận tín hiệu tắt. Đang hoàn tất công việc cuối...")
    _is_running = False

def process_knowledge_message(ch, method, properties, body):
    """Hàm xử lý khi có message mới trong queue `video_knowledge`."""
    job_id = None
    try:
        msg = json.loads(body.decode("utf-8"))
        video_id = msg.get("video_id")

        if not video_id:
            # Message hỏng không thể xử lý lại → ack để tháo khỏi queue.
            logger.error("❌ Message không hợp lệ (thiếu video_id). Bỏ qua.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        logger.info(f"📥 [KNOWLEDGE WORKER] Nhận yêu cầu xây dựng tri thức cho Video: {video_id}")

        # 1. Tạo/Cập nhật Job
        job_id = create_job(video_id, "KNOWLEDGE_BUILD")

        # 2. Xây dựng Knowledge (Giai đoạn 3)
        result = build_knowledge_for_video(video_id)

        # 3. Hoàn tất Job
        if result.get("chunks", 0) > 0:
            update_job_status(job_id, "COMPLETED", result.get("chunks", 0))
            logger.info(f"✅ Đã cấu trúc hóa xong {result['chunks']} Chunks cho {video_id}")

            # === BẮT ĐẦU PHẦN GỘP (EMBEDDING) ===
            logger.info(f"🚀 Bắt đầu nạp Vector trực tiếp (Gộp Giai đoạn 4)...")
            embed_job_id = create_job(video_id, "VECTOR_EMBEDDING")
            
            # Lấy data và nhúng luôn
            chunks = get_knowledge_chunks(video_id)
            indexed_count = index_knowledge_chunks(video_id, chunks)
            
            # Hoàn tất Embedding
            update_job_status(embed_job_id, "COMPLETED", indexed_count)
            logger.info(f"✅ Đã nạp thành công {indexed_count} Chunks vào Qdrant!")
            # === KẾT THÚC PHẦN GỘP ===

            # Xử lý thành công → ack.
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # Không tạo được chunk (thường do thiếu transcript) → không thể xử lý lại.
            # Ack để tránh loop vô hạn, nhưng đánh dấu job FAILED.
            update_job_status(job_id, "FAILED")
            logger.error(f"❌ Xây dựng tri thức thất bại cho video {video_id} (không có chunk).")
            ch.basic_ack(delivery_tag=method.delivery_tag)

    except json.JSONDecodeError as e:
        # Message không giải mã được → ack, không requeue (sẽ hỏng mãi).
        logger.error(f"❌ Message hỏng, không giải mã được: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        # Lỗi tạm thời (DB/API) → nack + requeue để thử lại, KHÔNG mất message.
        logger.error(f"❌ Lỗi xử lý message: {e}")
        if job_id:
            update_job_status(job_id, "FAILED")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def main():
    """Hàm khởi chạy chính của Knowledge Worker."""
    global _is_running
    signal.signal(signal.SIGINT, handle_sigterm)
    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        # Kết nối RabbitMQ qua helper chung (retry + declare queue + prefetch=1)
        connection, channel = connect_rabbitmq(
            declare_queues=["video_knowledge"],
            heartbeat=0,
            prefetch_count=1,
        )

        # Đăng ký hàm xử lý
        channel.basic_consume(
            queue="video_knowledge",
            on_message_callback=process_knowledge_message
        )

        logger.info("🚀 KNOWLEDGE WORKER ĐÃ KHỞI ĐỘNG! (Đang chờ video ở queue: video_knowledge)")
        
        # Vòng lặp lắng nghe ngắt mở thủ công để bắt signal
        while _is_running:
            connection.process_data_events(time_limit=1)

    except pika.exceptions.AMQPConnectionError as e:
        logger.error(f"❌ Không thể kết nối tới RabbitMQ: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Knowledge Worker gặp lỗi nghiêm trọng: {e}")
        sys.exit(1)
    finally:
        if 'connection' in locals() and connection.is_open:
            connection.close()
        logger.info("🛑 Knowledge Worker đã tắt.")

if __name__ == "__main__":
    main()

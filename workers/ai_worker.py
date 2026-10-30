"""
AI Worker — Bộ xử lý AI phân tích bình luận YouTube.

Worker chạy liên tục, poll RabbitMQ để lấy bình luận:
- Batch processing: Rút tối đa 10 tin nhắn mỗi lần
- Gọi AI GPT-4o phân tích chuyên sâu NLP
- Lưu kết quả vào PostgreSQL
- Kích hoạt Webhook n8n để gửi thông báo
- ACK/NACK: Xác nhận thành công hoặc trả lại hàng đợi khi lỗi
- Redis: Đánh dấu done khi xử lý xong, xóa seen

Entry point: run_ai_worker()
"""

import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pika
import pika.exceptions
import pika.spec

# Đảm bảo project root nằm trong sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.config import config
from utils.logger import get_logger
from utils.cache import get_redis_client, mark_comment_done
from database.connection import init_db
from database.queries import save_analyzed_comments
from core.ai_analyzer import analyze_comments_batch
from integrations.n8n_webhook import send_to_n8n

logger = get_logger(__name__)

# Cờ báo hiệu tắt worker gracefully
_shutdown_requested = False

# Batch size — Rút tối đa 10 tin nhắn mỗi lần để tiết kiệm API
BATCH_SIZE = 10

# Số lần tối đa một batch được requeue khi AI/API thất bại.
# Vượt ngưỡng sẽ bị đẩy vào Dead Letter Queue (DLQ) thay vì quấn vòng vô hạn.
MAX_RETRIES = 3


def _signal_handler(signum: int, frame: Any) -> None:
    """Xử lý tín hiệu SIGINT/SIGTERM để tắt worker an toàn."""
    global _shutdown_requested
    logger.info("🛑 Nhận tín hiệu tắt (signal=%s). Đang dừng AI worker...", signum)
    _shutdown_requested = True


def _connect_rabbitmq() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """Kết nối tới RabbitMQ với logic reconnect.

    Returns:
        Tuple (connection, channel) đã sẵn sàng sử dụng.

    Raises:
        SystemExit: Nếu không thể kết nối sau nhiều lần thử.
    """
    max_retries = 5
    queue_name = getattr(config, "RABBITMQ_QUEUE", "youtube_comments")
    rabbitmq_user = getattr(config, "RABBITMQ_USER", "guest")
    rabbitmq_pass = getattr(config, "RABBITMQ_PASS", "guest")

    for attempt in range(1, max_retries + 1):
        try:
            credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_pass)
            params = pika.ConnectionParameters(
                host=config.RABBITMQ_HOST,
                port=config.RABBITMQ_PORT,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300,
            )
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue=queue_name, durable=True)
            # DLQ: đựng các batch lỗi vĩnh viễn (vượt MAX_RETRIES) để vận hành xem lại
            channel.queue_declare(queue=f"{queue_name}_dlq", durable=True)
            logger.info("✅ Đã kết nối thành công với RabbitMQ")
            return connection, channel
        except pika.exceptions.AMQPConnectionError as e:
            logger.error(
                "❌ Lỗi kết nối RabbitMQ (lần %d/%d): %s",
                attempt, max_retries, e,
            )
            if attempt < max_retries:
                wait_time = min(2 ** attempt, 30)
                logger.info("⏳ Chờ %d giây trước khi thử lại...", wait_time)
                time.sleep(wait_time)
            else:
                logger.critical("❌ Không thể kết nối RabbitMQ sau %d lần thử. Thoát.", max_retries)
                sys.exit(1)

    sys.exit(1)


def _ensure_rabbitmq_connection(
    connection: pika.BlockingConnection | None,
    channel: pika.adapters.blocking_connection.BlockingChannel | None,
) -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """Kiểm tra và tái kết nối RabbitMQ nếu connection bị đứt."""
    if connection is None or connection.is_closed:
        logger.warning("🔄 RabbitMQ connection bị đứt. Đang reconnect...")
        return _connect_rabbitmq()
    return connection, channel  # type: ignore[return-value]


def _process_batch(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    method_frames: list[pika.spec.Basic.GetOk],
    properties_list: list[pika.BasicProperties],
    bodies: list[bytes],
) -> None:
    """Xử lý một cụm (batch) bình luận lấy từ RabbitMQ.

    Luồng xử lý:
    1. Giải mã dữ liệu từ Queue
    2. Gọi AI phân tích chuyên sâu NLP
    3. Lưu vào Database PostgreSQL
    4. Kích hoạt Webhook n8n
    5. Đánh dấu done vào Redis
    6. ACK thành công — hoặc requeue có giới hạn (MAX_RETRIES) rồi rơi vào DLQ

    Args:
        channel: RabbitMQ channel.
        method_frames: Danh sách method frames cho ACK/NACK.
        properties_list: Danh sách properties của từng message (chứa headers).
        bodies: Danh sách nội dung message dạng bytes.
    """
    logger.info("==============================================")
    logger.info("🔥 BẮT ĐẦU RÚT %d BÌNH LUẬN TỪ HÀNG ĐỢI ĐỂ XỬ LÝ", len(bodies))

    queue_name = getattr(config, "RABBITMQ_QUEUE", "youtube_comments")
    dlq_name = f"{queue_name}_dlq"

    # Giải mã dữ liệu từ Queue
    raw_comments: list[dict] = []
    for body in bodies:
        comment_data = json.loads(body.decode("utf-8"))
        raw_comments.append(comment_data)

    try:
        # 1. Gọi AI phân tích (Nếu bị lỗi Rate Limit, nó sẽ văng Exception hoặc trả về rỗng)
        analyzed_comments = analyze_comments_batch(raw_comments)

        if not analyzed_comments:
            raise RuntimeError(
                "AI không trả về kết quả (Có thể do Rate Limit). Sẽ xử lý lại sau."
            )

        # 2. Lưu vào Database PostgreSQL
        saved_count = save_analyzed_comments(analyzed_comments)
        logger.info("🗄️ Đã lưu %d bình luận vào Database", saved_count)

        # 3. Kích hoạt Webhook n8n
        # Đóng gói nguyên 1 batch vào chung 1 mảng 'comments' để n8n dùng .map() tách ra
        if analyzed_comments:
            payload = {
                "video_id": analyzed_comments[0].get("video_id", "Unknown"),
                "comments": analyzed_comments
            }
            send_to_n8n(payload)

        # 4. Đánh dấu "done" vào Redis để Scraper biết AI đã xử lý thành công
        redis_client = get_redis_client()
        for c in raw_comments:
            c_id = c.get("comment_id")
            if c_id:
                # done với TTL 7 ngày
                mark_comment_done(c_id, ttl=7 * 24 * 60 * 60)
                # Xóa trạng thái seen vì đã xử lý xong
                redis_client.delete(f"seen:{c_id}")

        # 5. Xác nhận thành công (ACK) để RabbitMQ xóa tin nhắn khỏi Hàng đợi
        for method_frame in method_frames:
            channel.basic_ack(delivery_tag=method_frame.delivery_tag)

        logger.info(
            "✅ ĐÃ XỬ LÝ XONG, CẬP NHẬT REDIS (DONE) VÀ XÓA BATCH KHỎI HÀNG ĐỢI!"
        )

    except Exception as e:
        logger.error("❌ Xử lý thất bại: %s", e)

        # Số lần batch này đã bị requeue trước đó (đếm qua header x-retry-count)
        retry_count = max(
            ((props.headers or {}).get("x-retry-count", 0) for props in properties_list),
            default=0,
        )

        if retry_count < MAX_RETRIES:
            # Requeue CÓ GIỚI HẠN: publish bản mới kèm header tăng số lần thử rồi ACK
            # bản gốc. (NACK requeue thuần không sửa được header nên phải republish.)
            logger.info(
                "🔄 Requeue lần %d/%d — nghỉ 15 giây rồi thử lại...",
                retry_count + 1, MAX_RETRIES,
            )
            time.sleep(15)
            try:
                for props, body in zip(properties_list, bodies):
                    headers = dict(props.headers or {})
                    headers["x-retry-count"] = retry_count + 1
                    channel.basic_publish(
                        exchange="",
                        routing_key=queue_name,
                        body=body,
                        properties=pika.BasicProperties(
                            delivery_mode=2, headers=headers
                        ),
                    )
                for method_frame in method_frames:
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
            except pika.exceptions.AMQPError:
                # Publish không thành công (connection đứt) — NACK giữ nguyên message
                # để RabbitMQ redeliver, không mất dữ liệu.
                logger.warning("🔄 Publish requeue lỗi. NACK giữ message nguyên vị.")
                for method_frame in method_frames:
                    try:
                        channel.basic_nack(
                            delivery_tag=method_frame.delivery_tag, requeue=True
                        )
                    except Exception:
                        pass
        else:
            # Hết lượt retry → đẩy vào DLQ kèm thông tin lỗi, rồi ACK khỏi queue chính.
            logger.critical(
                "☠️ Batch thất bại %d lần liên tiếp. Đẩy %d message vào DLQ (%s). "
                "Xem lại tại đó!",
                retry_count, len(bodies), dlq_name,
            )
            for props, method_frame, body in zip(
                properties_list, method_frames, bodies
            ):
                headers = dict(props.headers or {})
                headers["x-retry-count"] = retry_count
                headers["x-last-error"] = str(e)[:500]
                channel.basic_publish(
                    exchange="",
                    routing_key=dlq_name,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2, headers=headers
                    ),
                )
                channel.basic_ack(delivery_tag=method_frame.delivery_tag)
            # Nghỉ xả hơi chút trước khi rút lô mới
            time.sleep(15)


def run_ai_worker() -> None:
    """Main entry point cho AI worker process.

    Chạy vòng lặp vô hạn:
    1. Rút tối đa 10 tin nhắn từ RabbitMQ (batch processing)
    2. Gọi AI phân tích → Lưu DB → Gửi n8n → ACK
    3. Ngủ 5 giây nếu hàng đợi trống
    """
    # Đăng ký signal handlers cho graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("🤖 AI WORKER ĐÃ KHỞI ĐỘNG - ĐANG LẮNG NGHE RABBITMQ...")

    # Đảm bảo DB sẵn sàng
    try:
        init_db()
    except Exception as e:
        logger.error("❌ Lỗi khởi tạo Database: %s", e)

    # Kết nối Redis
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        logger.info("✅ Đã kết nối thành công với Redis")
    except Exception as e:
        logger.critical("❌ Lỗi kết nối Redis: %s", e)
        sys.exit(1)

    # Kết nối RabbitMQ
    connection, channel = _connect_rabbitmq()
    queue_name = getattr(config, "RABBITMQ_QUEUE", "youtube_comments")

    try:
        while not _shutdown_requested:
            # Đảm bảo RabbitMQ connection còn sống
            connection, channel = _ensure_rabbitmq_connection(connection, channel)

            method_frames: list[pika.spec.Basic.GetOk] = []
            properties_list: list[pika.BasicProperties] = []
            bodies: list[bytes] = []

            # Rút tối đa BATCH_SIZE tin nhắn mỗi lần (Batch processing) để tiết kiệm API
            try:
                for _ in range(BATCH_SIZE):
                    method_frame, properties, body = channel.basic_get(
                        queue=queue_name, auto_ack=False
                    )
                    if method_frame:
                        method_frames.append(method_frame)
                        properties_list.append(properties)
                        bodies.append(body)
                    else:
                        break  # Hàng đợi đã trống
            except pika.exceptions.AMQPError:
                logger.warning("🔄 Lỗi RabbitMQ khi rút message. Reconnecting...")
                connection, channel = _connect_rabbitmq()
                continue

            if bodies:
                _process_batch(channel, method_frames, properties_list, bodies)
            else:
                # Hàng đợi trống, đi ngủ 5 giây rồi kiểm tra lại
                logger.debug("💤 Hàng đợi trống. AI đang ngủ chờ dữ liệu mới...")
                time.sleep(5)

    except KeyboardInterrupt:
        logger.info("🛑 Đã nhận KeyboardInterrupt.")
    finally:
        logger.info("🛑 Đang tắt AI Worker...")
        if connection and connection.is_open:
            try:
                connection.close()
                logger.info("✅ Đã đóng kết nối RabbitMQ.")
            except Exception:
                pass


if __name__ == "__main__":
    run_ai_worker()

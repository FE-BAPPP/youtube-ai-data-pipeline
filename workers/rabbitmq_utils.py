"""
RabbitMQ Utilities — Helper kết nối dùng chung cho tất cả worker.

Trước đây mỗi worker (media/knowledge/embedding) tự viết logic kết nối RabbitMQ
với tham số khác nhau (heartbeat, retry, reconnect), gây khó bảo trì và không
nhất quán. Module này tập trung hóa việc:
- Kết nối RabbitMQ với retry + exponential backoff.
- Khai báo queue (declare) an toàn.
- Đảm bảo connection còn sống (dùng cho worker poll bằng basic_get).
"""

import time

import pika
import pika.exceptions

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Kiểu trả về cho gọn
Channel = pika.adapters.blocking_connection.BlockingChannel


def connect_rabbitmq(
    declare_queues: list[str] | None = None,
    heartbeat: int = 600,
    max_retries: int = 5,
    prefetch_count: int | None = None,
) -> tuple[pika.BlockingConnection, Channel]:
    """Kết nối tới RabbitMQ với retry + exponential backoff.

    Args:
        declare_queues: Danh sách tên queue cần declare (durable=True).
        heartbeat: Heartbeat interval (giây).
        max_retries: Số lần thử tối đa trước khi raise.
        prefetch_count: Nếu đặt, gọi basic_qos(prefetch_count) trên channel.

    Returns:
        Tuple (connection, channel) đã sẵn sàng.

    Raises:
        pika.exceptions.AMQPConnectionError: Nếu không kết nối được sau max_retries lần.
    """
    credentials = pika.PlainCredentials(config.RABBITMQ_USER, config.RABBITMQ_PASS)
    params = pika.ConnectionParameters(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        credentials=credentials,
        heartbeat=heartbeat,
        blocked_connection_timeout=300,
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()

            for queue_name in (declare_queues or []):
                channel.queue_declare(queue=queue_name, durable=True)

            if prefetch_count is not None:
                channel.basic_qos(prefetch_count=prefetch_count)

            logger.info("✅ Đã kết nối thành công với RabbitMQ")
            return connection, channel

        except pika.exceptions.AMQPConnectionError as e:
            last_error = e
            logger.error("❌ Lỗi kết nối RabbitMQ (lần %d/%d): %s", attempt, max_retries, e)
            if attempt < max_retries:
                wait_time = min(2 ** attempt, 30)
                logger.info("⏳ Chờ %d giây trước khi thử lại...", wait_time)
                time.sleep(wait_time)

    logger.critical("❌ Không thể kết nối RabbitMQ sau %d lần thử.", max_retries)
    raise last_error if last_error else pika.exceptions.AMQPConnectionError()


def ensure_connection(
    connection: pika.BlockingConnection | None,
    channel: Channel | None,
    declare_queues: list[str] | None = None,
    heartbeat: int = 600,
    prefetch_count: int | None = None,
) -> tuple[pika.BlockingConnection, Channel]:
    """Kiểm tra và tái kết nối RabbitMQ nếu connection/channel bị đứt."""
    if (
        connection is None
        or connection.is_closed
        or channel is None
        or channel.is_closed
    ):
        logger.warning("🔄 RabbitMQ connection/channel bị đứt. Đang reconnect...")
        return connect_rabbitmq(
            declare_queues=declare_queues,
            heartbeat=heartbeat,
            prefetch_count=prefetch_count,
        )
    return connection, channel

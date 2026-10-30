"""
Redis Cache Module.

Quản lý kết nối Redis singleton và các hàm tiện ích
để khử trùng lặp bình luận (deduplication).

Key patterns (giữ nguyên từ hệ thống cũ):
    - seen:{comment_id}  → Bình luận đang nằm trong queue chờ AI xử lý (TTL: 1 giờ)
    - done:{comment_id}  → Bình luận đã được AI xử lý thành công (TTL: 7 ngày)
"""

import redis

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Singleton Redis client
_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """Lấy Redis client singleton.

    Tạo kết nối mới nếu chưa có, hoặc trả về kết nối hiện tại.
    Tự động ping để kiểm tra kết nối.

    Returns:
        redis.Redis: Client đã kết nối và sẵn sàng sử dụng.

    Raises:
        ConnectionError: Nếu không thể kết nối tới Redis.
    """
    global _redis_client

    if _redis_client is not None:
        try:
            _redis_client.ping()
            return _redis_client
        except redis.ConnectionError:
            logger.warning("⚠️ Mất kết nối Redis, đang thử kết nối lại...")
            _redis_client = None

    try:
        _redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=0,
            decode_responses=True,
        )
        _redis_client.ping()
        logger.info("✅ Đã kết nối thành công với Redis (Khiên khử trùng lặp)")
        return _redis_client
    except redis.ConnectionError as e:
        logger.error(f"❌ Lỗi kết nối Redis: {e}")
        raise


# ---------------------------------------------------------------------------
# Các hàm tiện ích: SEEN (đang trong queue, chờ AI xử lý)
# ---------------------------------------------------------------------------

def is_comment_seen(comment_id: str) -> bool:
    """Kiểm tra bình luận có đang nằm trong queue chờ AI xử lý không.

    Args:
        comment_id: ID của bình luận YouTube.

    Returns:
        True nếu bình luận đang có trong queue (key seen:{comment_id} tồn tại).
    """
    client = get_redis_client()
    return bool(client.exists(f"seen:{comment_id}"))


def mark_comment_seen(comment_id: str, ttl: int = 3600) -> None:
    """Đánh dấu bình luận đã được đẩy vào queue (chống Race Condition).

    Args:
        comment_id: ID của bình luận YouTube.
        ttl: Thời gian sống của key (giây). Mặc định: 3600 (1 giờ).
    """
    client = get_redis_client()
    client.setex(f"seen:{comment_id}", ttl, "in_queue")





# ---------------------------------------------------------------------------
# Các hàm tiện ích: DONE (đã xử lý thành công)
# ---------------------------------------------------------------------------

def is_comment_done(comment_id: str) -> bool:
    """Kiểm tra bình luận đã được AI xử lý thành công chưa.

    Dùng bởi Scraper để kích hoạt Early Exit khi đụng bình luận cũ.

    Args:
        comment_id: ID của bình luận YouTube.

    Returns:
        True nếu bình luận đã được xử lý (key done:{comment_id} tồn tại).
    """
    client = get_redis_client()
    return bool(client.exists(f"done:{comment_id}"))


def mark_comment_done(comment_id: str, ttl: int = 604800) -> None:
    """Đánh dấu bình luận đã được AI xử lý thành công.

    Args:
        comment_id: ID của bình luận YouTube.
        ttl: Thời gian sống của key (giây). Mặc định: 604800 (7 ngày).
    """
    client = get_redis_client()
    client.setex(f"done:{comment_id}", ttl, "processed")

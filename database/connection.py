"""
Database Connection Module.

Quản lý connection pool (ThreadedConnectionPool) và khởi tạo schema.
Cung cấp context manager `get_db_connection()` để lấy/trả connection tự động.
"""

from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Connection pool singleton
_pool: ThreadedConnectionPool | None = None


def _get_pool() -> ThreadedConnectionPool:
    """Lấy hoặc khởi tạo connection pool singleton.

    Returns:
        ThreadedConnectionPool: Pool đã sẵn sàng sử dụng.

    Raises:
        psycopg2.OperationalError: Nếu không thể kết nối tới Database.
    """
    global _pool

    if _pool is not None and not _pool.closed:
        return _pool

    try:
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASS,
            dbname=config.DB_NAME,
        )
        logger.info(
            f"✅ Đã khởi tạo Connection Pool tới PostgreSQL "
            f"({config.DB_USER}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME})"
        )
        return _pool
    except psycopg2.OperationalError as e:
        logger.error(f"❌ Không thể kết nối tới Database. Lỗi: {e}")
        raise


@contextmanager
def get_db_connection() -> Generator:
    """Context manager để lấy connection từ pool.

    Connection sẽ tự động được trả về pool khi thoát khỏi block `with`.
    Nếu có exception, connection sẽ được rollback trước khi trả về.

    Yields:
        psycopg2.connection: Database connection từ pool.

    Example::

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db() -> None:
    """Khởi tạo schema database (tạo bảng + view).

    An toàn để gọi nhiều lần — sử dụng IF NOT EXISTS và CREATE OR REPLACE.
    """
    from database.models import (
        CREATE_COMMENTS_TABLE,
        CREATE_JOBS_TABLE,
        CREATE_VIDEOS_TABLE,
        CREATE_VIEWS,
        CREATE_TRANSCRIPTS_TABLE,
        CREATE_FRAMES_TABLE,
        CREATE_OCR_RESULTS_TABLE,
        CREATE_KNOWLEDGE_CHUNKS_TABLE,
        CREATE_EVENTS_TABLE,
    )

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # Bảng gốc (Phase 0)
                cursor.execute(CREATE_COMMENTS_TABLE)
                cursor.execute(CREATE_VIDEOS_TABLE)
                cursor.execute(CREATE_JOBS_TABLE)
                cursor.execute(CREATE_VIEWS)
                # Bảng mới — Multimodal Pipeline (Phase 1+)
                cursor.execute(CREATE_TRANSCRIPTS_TABLE)
                cursor.execute(CREATE_FRAMES_TABLE)
                cursor.execute(CREATE_OCR_RESULTS_TABLE)
                cursor.execute(CREATE_KNOWLEDGE_CHUNKS_TABLE)
                cursor.execute(CREATE_EVENTS_TABLE)
                conn.commit()
                logger.info("✅ Đã khởi tạo schema Database thành công (8 bảng + 3 views)")
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"❌ Lỗi khi tạo bảng: {e}")
        raise

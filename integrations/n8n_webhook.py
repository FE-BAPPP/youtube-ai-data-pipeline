"""
n8n Webhook Integration — Gửi dữ liệu đã phân tích sang n8n workflow.

Module này chịu trách nhiệm:
- Sanitize dữ liệu (loại bỏ ký tự đặc biệt gây lỗi Telegram API)
- Gửi payload qua HTTP POST tới n8n webhook endpoint
"""

import re

import requests

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)




def _sanitize_data(data):
    """Đệ quy sanitize toàn bộ cấu trúc dữ liệu (str, dict, list).

    CÁCH TỐI ƯU NHẤT: XỬ LÝ (SANITIZE) DATA NGAY TỪ BACKEND PYTHON
    Lọc sạch các ký tự dễ làm văng API Telegram (<, >, _, *, [, ]) ra khỏi toàn bộ data.

    Args:
        data: Dữ liệu cần sanitize (str, dict, list, hoặc kiểu khác).

    Returns:
        Dữ liệu đã sanitize, giữ nguyên cấu trúc gốc.
    """
    if isinstance(data, str):
        return re.sub(r'[<>_*\[\]]', '', data)
    elif isinstance(data, dict):
        return {k: _sanitize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_data(item) for item in data]
    return data


def send_to_n8n(comment_data: dict) -> bool:
    """Gửi dữ liệu bình luận đã phân tích sang n8n thông qua Webhook POST.

    Dữ liệu sẽ được sanitize trước khi gửi để tránh lỗi Telegram formatting.

    Args:
        comment_data: Dict chứa dữ liệu bình luận đã phân tích bởi AI.
            Thường bao gồm video_id, comments, total_comments, v.v.

    Returns:
        True nếu gửi thành công (HTTP 200), False nếu thất bại.
    """
    webhook_url = config.N8N_WEBHOOK_URL
    if not webhook_url:
        logger.warning(
            "⚠️ Bỏ qua kích hoạt n8n: Chưa cấu hình N8N_WEBHOOK_URL trong file .env"
        )
        return False

    logger.info("▶ KÍCH HOẠT WORKFLOW TRÊN n8n (Webhook)...")

    # Sanitize toàn bộ dữ liệu trước khi gửi
    clean_data = _sanitize_data(comment_data)

    try:
        # Bắn dữ liệu đi bằng method POST
        response = requests.post(
            webhook_url,
            json=clean_data,
            headers={"Content-Type": "application/json"},
        )

        # Nếu n8n trả về 200 OK (Thành công)
        if response.status_code == 200:
            logger.info(
                "✅ Đã bắn dữ liệu sang n8n thành công! Workflow của n8n đang chạy..."
            )
            return True
        else:
            logger.error(
                f"❌ n8n từ chối nhận dữ liệu. Mã lỗi: {response.status_code}"
            )
            logger.error(response.text)
            return False

    except requests.exceptions.ConnectionError:
        logger.error(
            "❌ Lỗi: Không thể kết nối tới n8n. "
            "Bạn đã bật Docker Desktop và chạy n8n chưa?"
        )
        return False
    except Exception as e:
        logger.error(f"❌ Có lỗi xảy ra khi bắn Webhook: {e}")
        return False

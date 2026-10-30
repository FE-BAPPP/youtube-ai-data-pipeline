"""Tests cho logic requeue có giới hạn + DLQ của AI worker.

_to _process_batch được mock toàn bộ dependency ngoài (AI, DB, Redis, n8n)
nên chạy được mà không cần hạ tầng RabbitMQ thật.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pika

import workers.ai_worker as ai_worker


QUEUE = getattr(ai_worker.config, "RABBITMQ_QUEUE", "youtube_comments")
DLQ = f"{QUEUE}_dlq"


def _make_batch(comment_id="c1"):
    """Tạo 1 batch giả: 1 message với method frame + properties + body."""
    method_frame = MagicMock()
    method_frame.delivery_tag = 1
    properties = SimpleNamespace(headers=None)
    body = json.dumps(
        {"video_id": "v1", "comment_id": comment_id, "author": "A", "text": "hi"}
    ).encode("utf-8")
    return [method_frame], [properties], [body]


def _headers_of_publish(call):
    return call.kwargs["properties"].headers


def _run_batch(channel, frames, props, bodies):
    ai_worker._process_batch(channel, frames, props, bodies)


def test_success_path_acks_all_messages():
    """AI xử lý thành công → lưu DB, gửi n8n, đánh dấu done, ACK toàn bộ."""
    channel = MagicMock()
    frames, props, bodies = _make_batch()

    with patch.object(ai_worker, "analyze_comments_batch", side_effect=lambda c: c), \
         patch.object(ai_worker, "save_analyzed_comments", return_value=1) as mock_save, \
         patch.object(ai_worker, "send_to_n8n") as mock_n8n, \
         patch.object(ai_worker, "get_redis_client"), \
         patch.object(ai_worker, "mark_comment_done"), \
         patch.object(ai_worker.time, "sleep"):
        _run_batch(channel, frames, props, bodies)

    mock_save.assert_called_once()
    mock_n8n.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_publish.assert_not_called()


def test_failure_requeues_with_incremented_retry_header():
    """AI fail lần 1 → republish vào queue chính kèm header x-retry-count=1, ACK bản gốc."""
    channel = MagicMock()
    frames, props, bodies = _make_batch()

    with patch.object(ai_worker, "analyze_comments_batch",
                      side_effect=RuntimeError("AI fail")), \
         patch.object(ai_worker.time, "sleep"):
        _run_batch(channel, frames, props, bodies)

    channel.basic_publish.assert_called_once()
    call = channel.basic_publish.call_args
    assert call.kwargs["routing_key"] == QUEUE
    assert _headers_of_publish(call)["x-retry-count"] == 1
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


def test_failure_respects_existing_retry_count():
    """Message đã retry 1 lần (header x-retry-count=1) → lần này tăng lên 2."""
    channel = MagicMock()
    frames, _, bodies = _make_batch()
    props = [SimpleNamespace(headers={"x-retry-count": 1})]

    with patch.object(ai_worker, "analyze_comments_batch",
                      side_effect=RuntimeError("AI fail")), \
         patch.object(ai_worker.time, "sleep"):
        _run_batch(channel, frames, props, bodies)

    call = channel.basic_publish.call_args
    assert call.kwargs["routing_key"] == QUEUE
    assert _headers_of_publish(call)["x-retry-count"] == 2


def test_exhausted_retries_goes_to_dlq():
    """Đã retry đủ MAX_RETRIES → message rơi vào DLQ kèm x-last-error, không quay lại queue."""
    channel = MagicMock()
    max_retries = ai_worker.MAX_RETRIES
    frames, _, bodies = _make_batch()
    props = [SimpleNamespace(headers={"x-retry-count": max_retries})]

    with patch.object(ai_worker, "analyze_comments_batch",
                      side_effect=RuntimeError("loi vi~n chuyen")), \
         patch.object(ai_worker.time, "sleep"):
        _run_batch(channel, frames, props, bodies)

    channel.basic_publish.assert_called_once()
    call = channel.basic_publish.call_args
    assert call.kwargs["routing_key"] == DLQ
    headers = _headers_of_publish(call)
    assert headers["x-retry-count"] == max_retries
    assert "loi vi~n chuyen" in headers["x-last-error"]
    # Message gốc phải được ACK khỏi queue chính
    channel.basic_ack.assert_called_once_with(delivery_tag=1)


def test_requeue_loop_terminates_at_max_retries():
    """Mô phỏng đủ chu kỳ: fail liên tục → sau MAX_RETRIES lần requeue thì vào DLQ.

    Đây là test chống regression cho bug requeue vô hạn cũ.
    """
    channel = MagicMock()
    max_retries = ai_worker.MAX_RETRIES

    # Bắt đầu như message mới toanh (chưa có header)
    frames, props, bodies = _make_batch()
    current_props = props

    with patch.object(ai_worker, "analyze_comments_batch",
                      side_effect=RuntimeError("AI fail")), \
         patch.object(ai_worker.time, "sleep"):
        for i in range(max_retries + 1):
            ai_worker._process_batch(channel, frames, current_props, bodies)
            if channel.basic_publish.call_args is None:
                break  # NACK fallback (không xảy ra với mock) — dừng phòng hờ
            last_call = channel.basic_publish.call_args
            # Lần publish tiếp theo dùng header của lần publish vừa rồi
            current_props = [SimpleNamespace(headers=dict(_headers_of_publish(last_call)))]

    # Publish đúng MAX_RETRIES lần vào queue + 1 lần vào DLQ
    routing_keys = [
        c.kwargs["routing_key"] for c in channel.basic_publish.call_args_list
    ]
    assert routing_keys.count(QUEUE) == max_retries
    assert routing_keys.count(DLQ) == 1
    assert routing_keys[-1] == DLQ  # Lượt cuối cùng là DLQ

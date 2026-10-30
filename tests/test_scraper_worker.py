"""Tests cho thuật toán Smart Pagination + Early Exit + Dedup của Scraper Worker.

Mock toàn bộ YouTube API / Redis / RabbitMQ nên không cần hạ tầng thật.
"""

import json
from unittest.mock import MagicMock, patch

import workers.scraper_worker as scraper_worker


def _comment(cid, author="Nguyen Van A", text="bình luận thử"):
    return {
        "comment_id": cid,
        "author": author,
        "text": text,
        "published_at": "2026-07-01T00:00:00Z",
    }


def _mock_youtube(comments_pages):
    """Trả về (service, fetch_mock) với fetch_mock lần lượt trả từng trang.

    comments_pages: list các tuple (comments, next_page_token).
    """
    service = MagicMock()
    fetch = MagicMock(side_effect=list(comments_pages))
    return service, fetch


def test_early_exit_when_first_comment_already_done():
    """Comment đầu tiên đã 'done' → dừng ngay, không đẩy gì vào queue, không tạo job."""
    channel = MagicMock()
    service, fetch = _mock_youtube([([_comment("c_done")], None)])

    with patch.object(scraper_worker, "get_youtube_service", return_value=service), \
         patch.object(scraper_worker, "fetch_video_comments", fetch), \
         patch.object(scraper_worker, "is_comment_done", return_value=True), \
         patch.object(scraper_worker, "is_comment_seen", return_value=False), \
         patch.object(scraper_worker, "mark_comment_seen"), \
         patch.object(scraper_worker, "insert_video") as mock_insert_video, \
         patch.object(scraper_worker, "create_job") as mock_create_job, \
         patch.object(scraper_worker, "update_job_status") as mock_update_job:
        count = scraper_worker.scrape_video_comments("vid1", channel)

    assert count == 0
    channel.basic_publish.assert_not_called()
    mock_create_job.assert_not_called()
    mock_update_job.assert_not_called()
    # insert_video vẫn phải được gọi để ghi danh video vào hệ thống
    mock_insert_video.assert_called_once_with("vid1")


def test_new_comment_is_published_and_marked_seen():
    """Comment mới → publish vào queue, mark seen (TTL 1h), tạo job và COMPLETED."""
    channel = MagicMock()
    service, fetch = _mock_youtube([([_comment("c_new")], None)])

    with patch.object(scraper_worker, "get_youtube_service", return_value=service), \
         patch.object(scraper_worker, "fetch_video_comments", fetch), \
         patch.object(scraper_worker, "is_comment_done", return_value=False), \
         patch.object(scraper_worker, "is_comment_seen", return_value=False), \
         patch.object(scraper_worker, "mark_comment_seen") as mock_seen, \
         patch.object(scraper_worker, "insert_video"), \
         patch.object(scraper_worker, "create_job", return_value=42) as mock_create_job, \
         patch.object(scraper_worker, "update_job_status") as mock_update_job:
        count = scraper_worker.scrape_video_comments("vid1", channel)

    assert count == 1

    # Publish đúng 1 message với payload chuẩn
    call = channel.basic_publish.call_args
    assert call.kwargs["routing_key"] == getattr(
        scraper_worker.config, "RABBITMQ_QUEUE", "youtube_comments"
    )
    payload = json.loads(
        call.kwargs["body"].decode("utf-8")
        if isinstance(call.kwargs["body"], bytes)
        else call.kwargs["body"]
    )
    assert payload["comment_id"] == "c_new"
    assert payload["video_id"] == "vid1"

    # Chống race condition: mark seen với TTL 1 giờ
    mock_seen.assert_called_once_with("c_new", ttl=3600)

    # Job chỉ tạo khi có comment mới, và được đóng COMPLETED
    mock_create_job.assert_called_once_with("vid1", "SCRAPE_JOB")
    mock_update_job.assert_called_once_with(42, "COMPLETED", 1)


def test_seen_comment_is_skipped_without_publish():
    """Comment đang nằm trong queue (seen) → bỏ qua, không publish, không tạo job."""
    channel = MagicMock()
    service, fetch = _mock_youtube([([_comment("c_seen")], None)])

    with patch.object(scraper_worker, "get_youtube_service", return_value=service), \
         patch.object(scraper_worker, "fetch_video_comments", fetch), \
         patch.object(scraper_worker, "is_comment_done", return_value=False), \
         patch.object(scraper_worker, "is_comment_seen", return_value=True), \
         patch.object(scraper_worker, "mark_comment_seen"), \
         patch.object(scraper_worker, "insert_video"), \
         patch.object(scraper_worker, "create_job") as mock_create_job, \
         patch.object(scraper_worker, "update_job_status"):
        count = scraper_worker.scrape_video_comments("vid1", channel)

    assert count == 0
    channel.basic_publish.assert_not_called()
    mock_create_job.assert_not_called()


def test_early_exit_stops_pagination():
    """Đụng comment done ở trang 2 → KHÔNG lật trang 3 (tiết kiệm API quota)."""
    channel = MagicMock()
    service, fetch = _mock_youtube(
        [
            ([_comment("c_new")], "token_page_2"),
            ([_comment("c_old_done")], "token_page_3"),
        ]
    )

    with patch.object(scraper_worker, "get_youtube_service", return_value=service), \
         patch.object(scraper_worker, "fetch_video_comments", fetch), \
         patch.object(scraper_worker, "is_comment_done",
                      side_effect=lambda cid: cid == "c_old_done"), \
         patch.object(scraper_worker, "is_comment_seen", return_value=False), \
         patch.object(scraper_worker, "mark_comment_seen"), \
         patch.object(scraper_worker, "insert_video"), \
         patch.object(scraper_worker, "create_job", return_value=1), \
         patch.object(scraper_worker, "update_job_status"):
        count = scraper_worker.scrape_video_comments("vid1", channel)

    # Chỉ fetch đúng 2 trang, không bỏ token_page_3 ra dùng
    assert fetch.call_count == 2
    # Comment trang 1 vẫn được publish bình thường trước khi dừng
    assert count == 1

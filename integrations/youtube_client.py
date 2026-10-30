"""
YouTube API Client — Wrapper hợp nhất cho toàn bộ thao tác với YouTube Data API v3.

Tập trung tất cả các lệnh gọi YouTube API vào một module duy nhất:
- Xây dựng service (API Key hoặc OAuth2)
- Lấy bình luận video (phân trang)
- Tìm kiếm video theo từ khóa
- Lấy danh sách video đã tải lên của kênh
- Tra cứu Channel ID từ @handle
"""

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)


def get_youtube_service(use_oauth: bool = False, token_file: str = "user_token.json"):
    """Khởi tạo YouTube API service.

    Args:
        use_oauth: Nếu True, sử dụng OAuth2 token từ ``token_file``
            (có tự động refresh). Nếu False, dùng API Key.
        token_file: Đường dẫn tới file chứa OAuth2 token
            (mặc định ``user_token.json`` ở thư mục gốc project).

    Returns:
        googleapiclient.discovery.Resource: YouTube API service object.

    Raises:
        FileNotFoundError: Khi ``use_oauth=True`` nhưng không tìm thấy
            ``token_file``.
        Exception: Khi không thể xây dựng service.
    """
    if use_oauth:
        return _build_oauth_service(token_file)
    return _build_apikey_service()


def _build_apikey_service():
    """Xây dựng YouTube service bằng API Key."""
    api_key = config.YOUTUBE_API_KEY
    if not api_key or api_key == "your_api_key_here":
        logger.error("❌ LỖI: Chưa cấu hình YOUTUBE_API_KEY trong file .env")
        raise ValueError("YOUTUBE_API_KEY chưa được cấu hình")
    return build("youtube", "v3", developerKey=api_key)


def _build_oauth_service(token_file: str):
    """Xây dựng YouTube service bằng OAuth2.

    Tự động làm mới token nếu hết hạn (rất quan trọng để tool chạy 24/7).
    """
    if not os.path.exists(token_file):
        logger.error(
            "❌ Chưa có token! Sếp vui lòng chạy oauth_server.py và đăng nhập trước."
        )
        raise FileNotFoundError(f"Không tìm thấy file token: {token_file}")

    with open(token_file, "r") as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )

    # Tự động làm mới token nếu hết hạn (rất quan trọng để tool chạy 24/7)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Lưu lại token mới cập nhật
        token_data["token"] = creds.token
        with open(token_file, "w") as f:
            json.dump(token_data, f, indent=4)
        logger.info("🔄 Đã tự động refresh OAuth2 token thành công.")

    return build("youtube", "v3", credentials=creds)


def fetch_video_comments(
    service,
    video_id: str,
    max_results: int = 100,
    page_token: str | None = None,
) -> tuple[list[dict], str | None]:
    """Lấy bình luận của một video.

    Args:
        service: YouTube API service object.
        video_id: ID của video YouTube.
        max_results: Số bình luận tối đa mỗi trang (tối đa 100).
        page_token: Token phân trang (None nếu trang đầu).

    Returns:
        Tuple gồm:
        - Danh sách dict bình luận (video_id, comment_id, author, text, published_at)
        - next_page_token (None nếu hết trang)
    """
    try:
        request = service.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=max_results,
            order="time",
            textFormat="plainText",
            pageToken=page_token,
        )
        response = request.execute()
    except HttpError as e:
        logger.error(f"❌ Lỗi YouTube API khi lấy bình luận video {video_id}: {e}")
        return [], None
    except Exception as e:
        logger.error(f"❌ Có lỗi xảy ra khi gọi API bình luận: {e}")
        return [], None

    comments: list[dict] = []
    for item in response.get("items", []):
        comment_id = item["id"]
        snippet = item["snippet"]["topLevelComment"]["snippet"]
        comments.append(
            {
                "video_id": video_id,
                "comment_id": comment_id,
                "author": snippet.get("authorDisplayName", "Unknown"),
                "text": snippet.get("textOriginal", ""),
                "published_at": snippet.get("publishedAt", ""),
            }
        )

    next_page_token = response.get("nextPageToken")
    logger.info(
        f"✅ Đã lấy {len(comments)} bình luận từ video {video_id}"
        f" (next_page={'có' if next_page_token else 'hết'})"
    )
    return comments, next_page_token


def search_videos(
    service, query: str, max_results: int = 10
) -> list[dict]:
    """Tìm kiếm video trên YouTube theo từ khóa.

    Mô phỏng thanh Tìm kiếm của YouTube — xếp theo độ liên quan.

    Args:
        service: YouTube API service object.
        query: Từ khóa tìm kiếm.
        max_results: Số video tối đa trả về.

    Returns:
        Danh sách dict (video_id, title, channel_name).
    """
    logger.info(f"🔍 Đang tìm kiếm video với từ khóa: '{query}'")
    try:
        # Gọi API Search giống y hệt như lúc Sếp gõ trên web YouTube
        request = service.search().list(
            part="snippet",
            q=query,
            type="video",
            order="relevance",  # Xếp theo độ liên quan (chuẩn YouTube)
            maxResults=max_results,
        )
        response = request.execute()
    except HttpError as e:
        logger.error(f"❌ Lỗi YouTube API khi tìm kiếm '{query}': {e}")
        return []
    except Exception as e:
        logger.error(f"❌ Có lỗi khi kết nối với YouTube: {e}")
        return []

    videos: list[dict] = []
    for item in response.get("items", []):
        # YouTube đôi khi trả về Channel hoặc Playlist chen ngang dù đã dặn type="video"
        video_id = item["id"].get("videoId")
        if not video_id:
            continue
        videos.append(
            {
                "video_id": video_id,
                "title": item["snippet"]["title"],
                "channel_name": item["snippet"]["channelTitle"],
            }
        )

    logger.info(f"✅ Tìm thấy {len(videos)} video cho từ khóa '{query}'")
    return videos


def get_channel_uploads(
    service, channel_id: str, max_results: int = 10
) -> list[dict]:
    """Lấy danh sách video đã tải lên của một kênh.

    Sử dụng THỦ THUẬT HACK NÃO CỦA YOUTUBE:
    - Channel ID luôn bắt đầu bằng chữ 'UC' (Ví dụ: UC123)
    - Playlist ID chứa TẤT CẢ video tải lên sẽ bắt đầu bằng chữ 'UU' (Ví dụ: UU123)

    Args:
        service: YouTube API service object.
        channel_id: ID kênh YouTube (bắt đầu bằng ``UC``).
        max_results: Số video tối đa trả về.

    Returns:
        Danh sách dict (video_id, title).
    """
    # THỦ THUẬT HACK NÃO CỦA YOUTUBE:
    # Channel ID luôn bắt đầu bằng chữ 'UC' (Ví dụ: UC123)
    # Playlist ID chứa TẤT CẢ video của kênh đó sẽ bắt đầu bằng chữ 'UU' (Ví dụ: UU123)
    uploads_playlist_id = channel_id.replace("UC", "UU", 1)

    logger.info(f"🔍 Đang quét danh sách {max_results} Video mới nhất từ kênh {channel_id}...")
    try:
        playlist_response = service.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=max_results,
        ).execute()
    except HttpError as e:
        logger.error(f"❌ Lỗi YouTube API khi lấy video của kênh {channel_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ Có lỗi khi lấy video: {e}")
        return []

    videos: list[dict] = []
    for item in playlist_response.get("items", []):
        videos.append(
            {
                "video_id": item["snippet"]["resourceId"]["videoId"],
                "title": item["snippet"]["title"],
            }
        )

    logger.info(f"✅ Đã lấy {len(videos)} video từ kênh {channel_id}")
    return videos


def find_channel_by_handle(service, handle: str) -> str | None:
    """Tìm Channel ID từ @handle.

    Đổi handle (@username) thành Channel ID bằng cách tìm kiếm trên YouTube.

    Args:
        service: YouTube API service object.
        handle: Tên handle kênh (có hoặc không có dấu ``@``).

    Returns:
        Channel ID nếu tìm thấy, None nếu không.
    """
    # Nếu Sếp nhập có dấu @, tự động cắt bỏ
    clean_name = handle.replace("@", "")

    try:
        # Quăng cho YouTube API tìm kiếm
        request = service.search().list(
            part="snippet",
            q=clean_name,
            type="channel",
            maxResults=1,
        )
        response = request.execute()
    except HttpError as e:
        logger.error(f"❌ Lỗi YouTube API khi tìm kênh '{handle}': {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Có lỗi khi tìm kênh: {e}")
        return None

    items = response.get("items", [])
    if not items:
        logger.warning(f"❌ Không tìm thấy kênh nào với tên: {handle}")
        return None

    channel_id = items[0]["snippet"]["channelId"]
    channel_title = items[0]["snippet"]["title"]
    logger.info(f"🎯 Đã khóa mục tiêu: {channel_title} (ID: {channel_id})")
    return channel_id


def get_video_details(service, video_id: str) -> dict | None:
    """Lấy thông tin chi tiết của 1 video cụ thể bằng ID.

    Args:
        service: YouTube API service object.
        video_id: ID của video YouTube.

    Returns:
        Dict chứa thông tin (video_id, title, channel_name) hoặc None nếu lỗi/không tìm thấy.
    """
    try:
        request = service.videos().list(
            part="snippet",
            id=video_id
        )
        response = request.execute()
        items = response.get("items", [])
        if items:
            item = items[0]
            return {
                "video_id": video_id,
                "title": item["snippet"]["title"],
                "channel_name": item["snippet"]["channelTitle"],
            }
        else:
            logger.warning(f"❌ Không tìm thấy video nào với ID: {video_id}")
            return None
    except HttpError as e:
        logger.error(f"❌ Lỗi YouTube API khi lấy thông tin video {video_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Có lỗi khi kết nối với YouTube: {e}")
        return None

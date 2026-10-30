"""
Video Sources — Nguồn cung cấp video cho hệ thống phân tích.

Module này hợp nhất 3 chiến lược thu thập video:
1. Tìm kiếm theo từ khóa (youtube_search.py)
2. Do thám kênh đối thủ (competitor_watch.py)
3. Đồng bộ kênh riêng qua OAuth2 (youtube_sync.py)

Tất cả đều sử dụng ``integrations.youtube_client`` để gọi API
và ``database.queries.insert_video()`` để lưu vào PostgreSQL.
"""

from integrations.youtube_client import (
    find_channel_by_handle,
    get_channel_uploads,
    get_youtube_service,
    search_videos,
    get_video_details,
)
import re
from database.queries import insert_video
from utils.logger import get_logger

logger = get_logger(__name__)


def search_videos_by_keyword(
    query: str, max_results: int = 10
) -> list[dict]:
    """Tìm kiếm video theo từ khóa và lưu vào Database.

    Mô phỏng thanh Tìm kiếm của YouTube — lấy top video theo độ liên quan,
    sau đó bắn thẳng vào PostgreSQL để đưa vào luồng cào dữ liệu.

    Args:
        query: Từ khóa tìm kiếm (ví dụ: ``"review iPhone 16"``).
        max_results: Số video tối đa cần lấy (mặc định 10).

    Returns:
        Danh sách dict video đã được lưu vào DB
        (video_id, title, channel_name).
    """
    logger.info(f"🕵️ Đang mô phỏng thanh Tìm kiếm của YouTube...")
    logger.info(f"🔍 Từ khóa Sếp yêu cầu: '{query}'")

    try:
        service = get_youtube_service(use_oauth=False)
        
        # Kiểm tra xem query có phải là URL không
        youtube_regex = r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})"
        match = re.search(youtube_regex, query)
        
        if match:
            video_id = match.group(1)
            logger.info(f"🔗 Phát hiện URL YouTube, đang lấy thông tin video ID: {video_id}")
            video = get_video_details(service, video_id)
            videos = [video] if video else []
        else:
            videos = search_videos(service, query, max_results=max_results)
    except Exception as e:
        logger.error(f"❌ Có lỗi khi kết nối với YouTube: {e}")
        return []

    if not videos:
        logger.warning("❌ YouTube báo về: Không tìm thấy video nào cho từ khóa này!")
        return []

    synced_videos: list[dict] = []
    for video in videos:
        # Bắn thẳng vào PostgreSQL để đưa vào luồng cào dữ liệu
        try:
            insert_video(
                video_id=video["video_id"],
                title=video["title"],
                channel_name=video["channel_name"],
            )
            synced_videos.append(video)
            logger.info(
                f"   + Đã hốt về DB: [{video['title'][:45]}...] "
                f"(Kênh: {video['channel_name']})"
            )
        except Exception as e:
            logger.error(f"❌ Lỗi khi lưu video {video['video_id']} vào DB: {e}")

    logger.info(
        f"🎉 HOÀN TẤT TRUY QUÉT! Đã vớt {len(synced_videos)} video "
        f"top đầu của từ khóa '{query}' vào Hệ thống."
    )
    logger.info(
        "👉 Bây giờ Sếp chỉ việc mở Scraper Worker lên "
        "là nó sẽ đi cào sạch Dư luận xã hội về chủ đề này!"
    )
    return synced_videos


def get_competitor_videos(
    channel_handle: str, max_results: int = 10
) -> list[dict]:
    """Do thám kênh đối thủ — lấy video mới nhất và lưu vào Database.

    Tìm Channel ID từ @handle, sau đó lấy danh sách video mới nhất
    của đối thủ và bắn thẳng vào PostgreSQL.

    Args:
        channel_handle: Tên handle kênh đối thủ (ví dụ: ``"@MixiGaming"``).
        max_results: Số video tối đa cần lấy (mặc định 10).

    Returns:
        Danh sách dict video đã được lưu vào DB
        (video_id, title, channel_name).
    """
    logger.info(f"🕵️ Đang khởi động Radar do thám kênh: {channel_handle}...")

    try:
        service = get_youtube_service(use_oauth=False)
    except Exception as e:
        logger.error(f"❌ Có lỗi khi kết nối với YouTube: {e}")
        return []

    channel_id = find_channel_by_handle(service, channel_handle)
    if not channel_id:
        logger.error("❌ Không tìm thấy kênh nào với tên này!")
        return []

    # Lấy tên kênh từ handle để lưu DB (dùng clean name)
    channel_name = channel_handle.replace("@", "")

    logger.info(f"🔍 Đang quét danh sách {max_results} Video mới nhất của đối thủ...")
    try:
        videos = get_channel_uploads(service, channel_id, max_results=max_results)
    except Exception as e:
        logger.error(f"❌ Có lỗi khi lấy video: {e}")
        return []

    synced_videos: list[dict] = []
    for video in videos:
        try:
            # Bắn thẳng vào PostgreSQL
            insert_video(
                video_id=video["video_id"],
                title=video["title"],
                channel_name=channel_name,
            )
            video["channel_name"] = channel_name
            synced_videos.append(video)
            logger.info(
                f"   + Đã nạp Video: [{video['title'][:40]}...] "
                f"(ID: {video['video_id']})"
            )
        except Exception as e:
            logger.error(f"❌ Lỗi khi lưu video {video['video_id']} vào DB: {e}")

    logger.info(
        f"🎉 HOÀN TẤT DO THÁM! Đã nạp {len(synced_videos)} video "
        f"của đối thủ vào Database."
    )
    logger.info(
        "👉 Scraper Worker đã có mồi, "
        "Sếp có thể bật Scraper lên để cào bình luận kênh đối thủ!"
    )
    return synced_videos


def sync_own_channel(
    token_file: str = "user_token.json",
) -> list[dict]:
    """Đồng bộ kênh YouTube riêng qua OAuth2 — Tự động hóa bước đầu vào (Mode A).

    Sử dụng OAuth2 token để truy cập kênh riêng, lấy danh sách video
    mới nhất và bắn thẳng vào PostgreSQL.

    Args:
        token_file: Đường dẫn tới file chứa OAuth2 token
            (mặc định ``user_token.json``).

    Returns:
        Danh sách dict video đã được lưu vào DB
        (video_id, title, channel_name).
    """
    logger.info("🚀 Đang xâm nhập Kênh YouTube bằng Đặc quyền OAuth 2.0...")

    try:
        service = get_youtube_service(use_oauth=True, token_file=token_file)
    except FileNotFoundError:
        # Lỗi đã được log trong get_youtube_service
        return []
    except Exception as e:
        logger.error(f"❌ Có lỗi khi kết nối OAuth2: {e}")
        return []

    try:
        # BƯỚC 1: Lấy Playlist ID chứa toàn bộ video tải lên (UU...)
        channels_response = service.channels().list(
            part="contentDetails,snippet",
            mine=True,
        ).execute()

        if not channels_response.get("items"):
            logger.error(
                "❌ Không tìm thấy kênh YouTube nào liên kết với Gmail này!"
            )
            return []

        channel = channels_response["items"][0]
        channel_name = channel["snippet"]["title"]
        uploads_playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]

        logger.info(f"✅ Xác nhận Chủ Kênh: {channel_name}")
        logger.info(f"📥 Tìm thấy rương báu (Uploads Playlist): {uploads_playlist_id}")

        # BƯỚC 2: Cào mẻ lưới 10 video mới nhất không tốn Quota Search
        logger.info("🔍 Đang quét danh sách Video mới nhất...")
        playlist_response = service.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=10,  # Sếp có thể tăng lên 50 nếu muốn
        ).execute()

        synced_videos: list[dict] = []
        for item in playlist_response.get("items", []):
            video_id = item["snippet"]["resourceId"]["videoId"]
            title = item["snippet"]["title"]

            try:
                # BƯỚC 3: Bắn thẳng vào PostgreSQL
                # Trạng thái mặc định sẽ là 'PROCESSING' (Đang chờ xử lý)
                insert_video(
                    video_id=video_id,
                    title=title,
                    channel_name=channel_name,
                )
                synced_videos.append(
                    {
                        "video_id": video_id,
                        "title": title,
                        "channel_name": channel_name,
                    }
                )
                logger.info(
                    f"   + Đã nạp Video vào DB: [{title}] (ID: {video_id})"
                )
            except Exception as e:
                logger.error(f"❌ Lỗi khi lưu video {video_id} vào DB: {e}")

        logger.info(
            f"🎉 QUÁ KHỦNG KHIẾP! Đã tự động đồng bộ "
            f"{len(synced_videos)} video vào Hệ thống."
        )
        logger.info(
            "👉 Bây giờ hệ thống đã ngập tràn Video. "
            "Scraper Worker sẽ tự động rút từng cái ra để làm thịt!"
        )
        return synced_videos

    except Exception as e:
        logger.error(f"❌ Có lỗi xảy ra trong quá trình đồng bộ: {e}")
        return []

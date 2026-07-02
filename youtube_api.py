import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
import json

# Tải biến môi trường từ file .env
load_dotenv()

API_KEY = os.getenv('YOUTUBE_API_KEY')

def get_video_comments(video_id, max_results=20):
    """
    Hàm gọi YouTube API để lấy bình luận của một video.
    """
    if not API_KEY or API_KEY == "your_api_key_here":
        print("❌ LỖI: Bạn chưa cấu hình YOUTUBE_API_KEY trong file .env")
        return []

    print(f"🔄 Đang kết nối YouTube API để lấy bình luận cho video: {video_id}...")
    
    # Khởi tạo đối tượng youtube api
    youtube = build('youtube', 'v3', developerKey=API_KEY)

    try:
        # Gọi API lấy bình luận
        request = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=max_results,
            textFormat="plainText"
        )
        response = request.execute()

        comments_data = []

        # Bóc tách JSON trả về
        for item in response.get("items", []):
            comment = item["snippet"]["topLevelComment"]["snippet"]
            
            author = comment.get("authorDisplayName")
            text = comment.get("textDisplay")
            published_at = comment.get("publishedAt")
            
            comments_data.append({
                "video_id": video_id,  # Lưu vết Video ID
                "author": author,
                "text": text,
                "published_at": published_at
            })

        print(f"✅ Đã lấy thành công {len(comments_data)} bình luận từ video {video_id}!")
        return comments_data

    except Exception as e:
        print(f"❌ Có lỗi xảy ra khi gọi API: {e}")
        return []

if __name__ == "__main__":
    # Bạn có thể đổi Video ID này thành video khác và chạy lại nhiều lần
    VIDEO_ID = "cq3PDJ6p0sc" 
    
    comments = get_video_comments(VIDEO_ID, max_results=5)
    
    if comments:
        file_name = "comments_data.json"
        existing_data = []
        
        # BƯỚC 1: KIỂM TRA VÀ ĐỌC DỮ LIỆU CŨ TỪ FILE (Nếu có)
        if os.path.exists(file_name):
            try:
                with open(file_name, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except json.JSONDecodeError:
                # Trường hợp file có tồn tại nhưng trống hoặc bị lỗi định dạng
                existing_data = []
                
        # BƯỚC 2: NỐI (APPEND) DỮ LIỆU MỚI VÀO DỮ LIỆU CŨ
        # Hàm extend() giúp gộp danh sách mới vào cuối danh sách cũ
        existing_data.extend(comments)
        
        # BƯỚC 3: GHI LẠI TOÀN BỘ DỮ LIỆU ĐÃ GỘP VÀO FILE .JSON
        with open(file_name, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=4, ensure_ascii=False)
            
        print(f"\n💾 Đã lưu GỘP thêm {len(comments)} bình luận mới vào {file_name}")
        print(f"📊 Tổng số bình luận đang được lưu trữ trong file là: {len(existing_data)}")

import os
import json
import time
import redis
import pika
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# Kết nối tới Bộ nhớ Đệm RAM (Redis)
# Giúp tra cứu ID bình luận siêu tốc (Khử trùng lặp)
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    print("✅ Đã kết nối thành công với Redis (Khiên khử trùng lặp)")
except Exception as e:
    print(f"❌ Lỗi kết nối Redis: {e}")
    exit()

# Kết nối tới Hàng Đợi (RabbitMQ)
# Nơi lưu trữ tạm thời các bình luận chờ AI xử lý
try:
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    # Tạo cái ống (Queue) tên là 'youtube_comments'
    channel.queue_declare(queue='youtube_comments', durable=True)
    print("✅ Đã kết nối thành công với RabbitMQ (Hồ chứa Message Queue)")
except Exception as e:
    print(f"❌ Lỗi kết nối RabbitMQ: {e}")
    exit()

def get_realtime_comments(video_id):
    """
    Cào các bình luận mới nhất.
    Thuật toán: Phân trang + Dừng sớm (Early Exit) khi đụng bình luận cũ trong Redis.
    """
    print(f"\n▶ BẮT ĐẦU CÀO BÌNH LUẬN MỚI CHO VIDEO: {video_id}")
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    
    new_comments_count = 0
    duplicate_count = 0
    page_token = None
    stop_scraping = False # Cờ báo hiệu đụng tường Redis
    
    while True:
        if stop_scraping:
            break
            
        request = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=100, # Bốc tối đa 100 cái mỗi trang
            order="time",
            pageToken=page_token
        )
        response = request.execute()
        
        items = response.get("items", [])
        if not items:
            break
            
        for item in items:
            comment_id = item["id"]
            snippet = item["snippet"]["topLevelComment"]["snippet"]
            author = snippet.get("authorDisplayName", "Unknown")
            text = snippet.get("textOriginal", "")
            published_at = snippet.get("publishedAt", "")
            
            # 🛑 ĐỤNG TƯỜNG REDIS: Kích hoạt thuật toán Early Exit
            # Vì danh sách đang xếp từ Mới -> Cũ, nếu gặp 1 cái đã có trong Redis
            # Thì 100% tất cả các bình luận bên dưới nó ĐỀU LÀ ĐỒ CŨ! Không cần lật trang nữa!
            if redis_client.exists(comment_id):
                duplicate_count += 1
                stop_scraping = True 
                print(f"🛑 Chạm tường Redis tại comment của [{author}]. NGỪNG QUÉT (Early Exit) để tiết kiệm API!")
                break # Phá vỡ vòng lặp for
                
            # Nếu chưa có trong Redis -> Đây là Bình luận mới!
            new_comments_count += 1
            
            # Đóng gói dữ liệu
            payload = {
                "video_id": video_id,
                "comment_id": comment_id,
                "author": author,
                "text": text,
                "published_at": published_at
            }
            
            # Đẩy vào Hàng đợi RabbitMQ
            channel.basic_publish(
                exchange='',
                routing_key='youtube_comments',
                body=json.dumps(payload, ensure_ascii=False),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            
            # Ghi danh vào Bảng vàng Redis
            redis_client.setex(comment_id, 7 * 24 * 60 * 60, "processed")
            print(f"📦 Đã đẩy vào Queue: [{author}] {text[:30]}...")
            
        # Chuẩn bị lật sang trang sau (Nếu chưa bị đụng tường Redis)
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print(f"\n📊 TỔNG KẾT GIAI ĐOẠN 1:")
    print(f"   - Đã bỏ qua: Dừng lại ngay sau khi đụng cái cũ đầu tiên.")
    print(f"   - Đã cào thêm (Mới): {new_comments_count}")

if __name__ == "__main__":
    VIDEO_ID = "9fpazsv5S5k"
    
    print(f"🕵️ SCRAPER WORKER ĐÃ KHỞI ĐỘNG - SẼ QUÉT ĐỊNH KỲ MỖI 1 PHÚT CHO VIDEO: {VIDEO_ID}...")
    try:
        while True:
            # 1. Đi cào dữ liệu
            get_realtime_comments(VIDEO_ID)
            
            # 2. Ngủ 60 giây (1 phút) rồi mới cào lại để tránh bị YouTube khóa IP do spam
            print("⏳ Đang đợi 60 giây cho lần quét tiếp theo (Mô phỏng Thời gian thực)...")
            time.sleep(60)
            
    except KeyboardInterrupt:
        print("\n🛑 Đã tắt cỗ máy Scraper.")
    finally:
        # Đóng kết nối RabbitMQ an toàn khi tắt phần mềm
        if connection.is_open:
            connection.close()

import os
import json
import time
import pika
from dotenv import load_dotenv

# Tái sử dụng các module đỉnh cao mà chúng ta đã viết
from ai_processing import analyze_comments_pipeline
from database import init_db, save_comments_to_db
from n8n_integration import send_to_n8n

load_dotenv()

def process_batch_messages(channel, method_frames, properties_list, bodies):
    """
    Xử lý một cụm (batch) bình luận lấy từ RabbitMQ
    """
    print(f"\n==============================================")
    print(f"🔥 BẮT ĐẦU RÚT {len(bodies)} BÌNH LUẬN TỪ HÀNG ĐỢI ĐỂ XỬ LÝ")
    
    # Giải mã dữ liệu từ Queue
    raw_comments = []
    for body in bodies:
        comment_data = json.loads(body.decode('utf-8'))
        raw_comments.append(comment_data)
        
    try:
        # 1. Gọi AI phân tích (Nếu bị lỗi Rate Limit, nó sẽ văng Exception hoặc trả về rỗng)
        analyzed_comments = analyze_comments_pipeline(raw_comments)
        
        if not analyzed_comments or len(analyzed_comments) == 0:
            raise Exception("AI không trả về kết quả (Có thể do Rate Limit). Sẽ xử lý lại sau.")
            
        # 2. Lưu vào Database PostgreSQL
        save_comments_to_db(analyzed_comments)
        
        # 3. Kích hoạt Webhook n8n
        # (Lấy video_id từ phần tử đầu tiên để làm mẫu)
        video_id = analyzed_comments[0].get("video_id", "Unknown")
        send_to_n8n(analyzed_comments, video_id)
        
        # 4. Xác nhận thành công (ACK) để RabbitMQ xóa 10 tin nhắn này khỏi Hàng đợi
        for method_frame in method_frames:
            channel.basic_ack(delivery_tag=method_frame.delivery_tag)
            
        print("✅ ĐÃ XỬ LÝ VÀ XÓA BATCH NÀY KHỎI HÀNG ĐỢI!")
        
    except Exception as e:
        print(f"❌ Xử lý thất bại: {e}")
        print("🔄 Đang trả lại toàn bộ bình luận về Hàng đợi (Re-queue)...")
        # Trả lại (NACK) để RabbitMQ giữ lại tin nhắn, không bị mất data
        for method_frame in method_frames:
            channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
        time.sleep(15) # Nghỉ xả hơi chút trước khi rút lô mới

def start_ai_worker():
    print("🤖 AI WORKER ĐÃ KHỞI ĐỘNG - ĐANG LẮNG NGHE RABBITMQ...")
    init_db() # Đảm bảo DB sẵn sàng
    
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
        channel = connection.channel()
        channel.queue_declare(queue='youtube_comments', durable=True)
    except Exception as e:
        print(f"❌ Lỗi kết nối RabbitMQ: {e}")
        return

    while True:
        method_frames = []
        properties_list = []
        bodies = []
        
        # Rút tối đa 10 tin nhắn mỗi lần (Batch processing) để tiết kiệm API
        for _ in range(10):
            method_frame, properties, body = channel.basic_get(queue='youtube_comments', auto_ack=False)
            if method_frame:
                method_frames.append(method_frame)
                properties_list.append(properties)
                bodies.append(body)
            else:
                break # Hàng đợi đã trống
                
        if len(bodies) > 0:
            process_batch_messages(channel, method_frames, properties_list, bodies)
        else:
            # Hàng đợi trống, đi ngủ 5 giây rồi kiểm tra lại
            print("💤 Hàng đợi trống. AI đang ngủ chờ dữ liệu mới...")
            time.sleep(5)

if __name__ == "__main__":
    start_ai_worker()

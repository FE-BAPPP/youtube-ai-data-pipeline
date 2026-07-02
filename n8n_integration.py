import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Lấy địa chỉ Webhook từ file .env
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")

def send_to_n8n(comments_data, video_id):
    """
    Hàm đóng gói toàn bộ dữ liệu đã được AI phân tích 
    và bắn sang n8n thông qua giao thức Webhook POST.
    """
    if not N8N_WEBHOOK_URL:
        print("⚠️ Bỏ qua kích hoạt n8n: Chưa cấu hình N8N_WEBHOOK_URL trong file .env")
        return False
        
    print(f"\n▶ BƯỚC 4: KÍCH HOẠT WORKFLOW TRÊN n8n (Webhook)...")
    
    # CÁCH TỐI ƯU NHẤT: XỬ LÝ (SANITIZE) DATA NGAY TỪ BACKEND PYTHON
    # Lọc sạch các ký tự dễ làm văng API Telegram (<, >, _, *, [, ]) ra khỏi toàn bộ data
    import re
    def sanitize_data(data):
        if isinstance(data, str):
            return re.sub(r'[<>_*\[\]]', '', data)
        elif isinstance(data, dict):
            return {k: sanitize_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [sanitize_data(item) for item in data]
        return data

    clean_comments = sanitize_data(comments_data)
    
    # Gói dữ liệu siêu sạch để gửi đi
    payload = {
        "video_id": video_id,
        "total_comments": len(clean_comments),
        "comments": clean_comments
    }
    
    try:
        # Bắn dữ liệu đi bằng method POST
        response = requests.post(
            N8N_WEBHOOK_URL, 
            json=payload, 
            headers={'Content-Type': 'application/json'}
        )
        
        # Nếu n8n trả về 200 OK (Thành công)
        if response.status_code == 200:
            print("✅ Đã bắn dữ liệu sang n8n thành công! Workflow của n8n đang chạy...")
            return True
        else:
            print(f"❌ n8n từ chối nhận dữ liệu. Mã lỗi: {response.status_code}")
            print(response.text)
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Lỗi: Không thể kết nối tới n8n. Bạn đã bật Docker Desktop và chạy n8n chưa?")
        return False
    except Exception as e:
        print(f"❌ Có lỗi xảy ra khi bắn Webhook: {e}")
        return False

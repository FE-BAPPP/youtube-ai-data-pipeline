import json
import os
from datetime import datetime
from youtube_api import get_video_comments
from ai_processing import classify_comments_in_batch, OpenAI
from database import init_db, save_comments_to_db
from n8n_integration import send_to_n8n

# Lấy token ở đây để truyền vào client
from dotenv import load_dotenv
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

def run_automation_pipeline(video_id):
    print(f"🚀 BẮT ĐẦU XỬ LÝ CHO VIDEO ID: {video_id} 🚀\n")
    
    # LUÔN ĐẢM BẢO BẢNG TRONG DATABASE ĐÃ TỒN TẠI
    init_db()

    # BƯỚC 1: LẤY DỮ LIỆU TỪ YOUTUBE (Extract)
    print("\n▶ BƯỚC 1: KẾT NỐI YOUTUBE API")
    raw_comments = get_video_comments(video_id, max_results=5)
    
    if not raw_comments:
        print("❌ Không lấy được bình luận. Dừng chương trình.")
        return

    # BƯỚC 2: AI PHÂN TÍCH CHUYÊN SÂU - BATCH PROCESSING (Transform)
    print("\n▶ BƯỚC 2: KÍCH HOẠT TRÍ TUỆ NHÂN TẠO (GITHUB MODELS - GPT-5)")
    
    client = OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=GITHUB_TOKEN,
        max_retries=0 
    )
    
    ai_results = classify_comments_in_batch(client, raw_comments)
    
    # Gộp kết quả
    for result in ai_results:
        comment_id = result.get("id")
        if comment_id is not None and comment_id < len(raw_comments):
            raw_comments[comment_id]["ai_analysis"] = result
            # Gắn ID video để lưu DB không bị lạc
            raw_comments[comment_id]["video_id"] = video_id
            
            print(f"\n💬 Bình luận gốc: {raw_comments[comment_id]['text']}")
            print(f"🧠 Tư duy: {result.get('thinking_process', '')}")
            print(f"🏷️ Nhãn: {result.get('category', '')}")

    # BƯỚC 3: LƯU TRỮ VÀO POSTGRESQL (Load)
    print("\n▶ BƯỚC 3: LƯU TRỮ VÀO DATABASE (POSTGRESQL)")
    
    # Bắn thẳng dữ liệu vào SQL thay vì chỉ lưu JSON
    save_comments_to_db(raw_comments)
    
    # Vẫn giữ lại tính năng lưu JSON làm Backup (Best practice của Data Engineer)
    os.makedirs("data", exist_ok=True)
    thoi_gian = datetime.now().strftime("%Y%m%d_%H%M%S")
    ten_file = f"data/backup_{video_id}_{thoi_gian}.json"
    with open(ten_file, "w", encoding="utf-8") as f:
        json.dump(raw_comments, f, indent=4, ensure_ascii=False)
        
    # BƯỚC 4: KÍCH HOẠT N8N ĐỂ TỰ ĐỘNG HÓA VỚI CÁC APP KHÁC
    send_to_n8n(raw_comments, video_id)
        
    print(f"\n✅ QUY TRÌNH HOÀN TẤT! Dữ liệu đã an vị trong DBeaver (PostgreSQL). File backup tại: {ten_file}")

if __name__ == "__main__":
    run_automation_pipeline("DiR_knVy3Eg")

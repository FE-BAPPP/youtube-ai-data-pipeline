import sys
from pathlib import Path
import json

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.media_processor import extract_frames, cleanup_temp_files
from core.ocr_engine import batch_ocr_frames

def run_test():
    video_id = "aBCZNGeAqc4"
    print(f"=== BẮT ĐẦU TEST OCR CHO VIDEO {video_id} ===")
    
    try:
        # Bước 1: Lấy frames với khoảng cách 30s để test nhanh (tránh chạy toàn bộ mất thời gian)
        print("\n1. Trích xuất frames (Scene Detection)...")
        frames = extract_frames(video_id, interval_sec=10)
        
        if not frames:
            print("❌ Không trích xuất được frame nào!")
            return
            
        print(f"✅ Đã lưu {len(frames)} frames sau khi lọc trùng.")
        
        # Lấy danh sách path
        frame_paths = [f["file_path"] for f in frames]
        
        # Bước 2: Chạy OCR
        print("\n2. Bắt đầu OCR...")
        ocr_results = batch_ocr_frames(frame_paths)
        
        # In kết quả
        print("\n=== KẾT QUẢ OCR ===")
        for idx, (frame, res) in enumerate(zip(frames, ocr_results)):
            print(f"\n--- Frame [{idx}] lúc {frame['timestamp_sec']}s ---")
            if res['detected_text']:
                print(f"Text:\n{res['detected_text']}")
                print(f"Chứa Code: {res['contains_code']} | Ngôn ngữ: {res['code_language']}")
            else:
                print("Không nhận diện được text.")
                
    except Exception as e:
        print(f"Lỗi: {e}")
    finally:
        # Dọn dẹp
        print("\n3. Dọn dẹp file tạm...")
        # cleanup_temp_files(video_id) # Comment lại nếu muốn xem ảnh
        pass

if __name__ == "__main__":
    run_test()

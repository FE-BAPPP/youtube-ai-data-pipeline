import os
import json
import time
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# TÍNH NĂNG MỚI: VÒNG XOAY TOKEN (TOKEN ROTATION)
# Lấy danh sách Token từ file .env (Phân cách bằng dấu phẩy)
tokens_str = os.getenv("GITHUB_TOKENS") or os.getenv("GITHUB_TOKEN")
if not tokens_str:
    print("❌ CHƯA CẤU HÌNH GITHUB_TOKENS TRONG FILE .ENV")
    exit()

# Băm chuỗi thành mảng các Token
GITHUB_TOKENS = [t.strip() for t in tokens_str.split(',') if t.strip()]
current_token_index = 0

def get_current_client():
    """Tạo OpenAI Client với Token hiện tại"""
    return OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=GITHUB_TOKENS[current_token_index],
        max_retries=0 
    )

def rotate_token():
    """Chuyển sang Token tiếp theo trong danh sách"""
    global current_token_index
    current_token_index = (current_token_index + 1) % len(GITHUB_TOKENS)
    print(f"🔄 ĐÃ ĐỔI SÚNG: CHUYỂN SANG TOKEN SỐ {current_token_index + 1}/{len(GITHUB_TOKENS)}")
    return get_current_client()

def classify_comments_in_batch(comments_list):
    # Lấy súng (Client) hiện tại ra xài
    client = get_current_client()
    
    payload_data = [{"id": i, "text": c["text"]} for i, c in enumerate(comments_list)]
    json_payload = json.dumps(payload_data, ensure_ascii=False)

    prompt = f"""
Bạn là một Chuyên gia Phân tích Ngôn ngữ Tự nhiên (NLP Expert) xuất sắc.
Nhiệm vụ của bạn là phân tích MẢNG JSON chứa các bình luận YouTube dưới đây.

[YÊU CẦU ĐẶC BIỆT VỀ NGỮ CẢNH VÀ NGÔN NGỮ]:
1. Giải mã "Từ Lóng" và "Teencode": Dân mạng Việt Nam thường viết tắt, dùng từ lóng. Hãy dịch nghĩa thực sự của chúng.
2. Nhận diện "Cà khịa" (Sarcasm/Irony): Cực kỳ cẩn thận với những câu có vẻ là "khen" nhưng thực chất là đang mỉa mai, châm biếm, móc mỉa. Đừng bị lừa bởi mặt chữ.
3. Phân tích Ngữ Cảnh Ẩn: Nếu có nhắc đến mốc thời gian (vd: 33:55) hoặc các nhân vật cụ thể, hãy phân tích cảm xúc tại khoảnh khắc đó.

BẮT BUỘC trả về MỘT MẢNG JSON (JSON Array) hợp lệ. KHÔNG CÓ markdown code block, KHÔNG giải thích thêm.

Cấu trúc mỗi đối tượng trong mảng:
{{
    "id": <id tương ứng của bình luận>,
    "thinking_process": "<Suy luận đa chiều: Giải mã từ lóng (nếu có) -> Phân tích nghĩa đen & bóng -> Đánh giá giọng điệu (chân thành hay mỉa mai) -> Kết luận.>",
    "category": "<KHACH_HOI_DAP / TICH_CUC / TIEU_CUC_GOP_Y / DOC_HAI_CHUI_BOI / SPAM / KHAC>",
    "toxicity_level": <Thang độ độc hại bằng số từ 1 đến 10>,
    "sentiment_score": <Điểm cảm xúc 1-10 (1 là cực kỳ tiêu cực, 10 là cực kỳ tích cực)>,
    "user_intent": "<Mục đích cốt lõi>",
    "key_insight": "<Rút gọn 1 ý chính sâu sắc nhất>",
    "suggested_action": "<xoa_ngay / theo_doi / can_tra_loi / bo_qua>"
}}

[Danh sách bình luận]:
{json_payload}
"""
    
    # Cho phép số lần thử bằng chính số lượng Token đang có x 2 (Để xoay vòng đủ 2 vòng)
    max_retries = max(4, len(GITHUB_TOKENS) * 2)
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o", 
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
            return json.loads(raw_text)
            
        except Exception as e:
            error_msg = str(e)
            if "Too many requests" in error_msg or "429" in error_msg or "rate limit" in error_msg.lower():
                print(f"⚠️ BÁO ĐỘNG: Token số {current_token_index + 1} đã cạn kiệt thể lực (Rate Limit)!")
                
                # NẾU CÓ NHIỀU HƠN 1 TOKEN -> ĐỔI SÚNG VÀ BẮN TIẾP NGAY LẬP TỨC
                if len(GITHUB_TOKENS) > 1:
                    client = rotate_token()
                    time.sleep(2) # Nghỉ xíu để API khởi động
                else:
                    # NẾU CHỈ CÓ 1 TOKEN -> BẮT BUỘC PHẢI NGỦ ĐỂ CHỜ HỒI MÁU
                    print(f"⏳ Hệ thống chỉ có 1 Token. Đang ngủ 15 giây chờ hồi máu...")
                    time.sleep(15)
            else:
                print(f"❌ Lỗi API Batch: {e}")
                return []
                
    print("❌ Đã xoay vòng hết sạch Token nhưng vẫn bị Rate Limit. Trả về Hàng đợi để xử lý sau.")
    return []

def analyze_comments_pipeline(raw_comments):
    print(f"\n▶ BƯỚC 2: KÍCH HOẠT AI PHÂN TÍCH CHUYÊN SÂU NLP ({len(raw_comments)} BÌNH LUẬN)...")
    
    # Không cần truyền Client vào nữa, hàm tự động lấy súng
    ai_results = classify_comments_in_batch(raw_comments)
    
    if not ai_results:
        return raw_comments

    for result in ai_results:
        comment_id = result.get("id")
        if comment_id is not None and comment_id < len(raw_comments):
            raw_comments[comment_id]["ai_analysis"] = result
            if "id" in raw_comments[comment_id]["ai_analysis"]:
                del raw_comments[comment_id]["ai_analysis"]["id"]
            
            print(f"\n💬 Bình luận: {raw_comments[comment_id]['text']}")
            print(f"🧠 Tư duy NLP: {result.get('thinking_process', '')}")
            print(f"🎯 Ý định người dùng: {result.get('user_intent', '')}")
            print(f"🏷️ Nhãn: {result.get('category', '')} | Độ độc hại: {result.get('toxicity_level', 0)}/10")

    return raw_comments

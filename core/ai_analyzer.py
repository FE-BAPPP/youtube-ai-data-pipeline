"""
AI Analyzer — Phân tích bình luận YouTube bằng GPT-4o.

Module này xử lý:
- Xoay vòng token (Token Rotation) để vượt qua rate limit
- Gọi GPT-4o phân tích NLP chuyên sâu (phát hiện cà khịa, từ lóng, cảm xúc)
- Parse kết quả JSON trả về từ AI
- Retry tự động khi bị rate limit (429)
"""

import base64
import json
import threading
import time

from openai import OpenAI

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# TÍNH NĂNG MỚI: VÒNG XOAY TOKEN (TOKEN ROTATION)
# Lấy danh sách Token từ config (đã parse từ .env)
_token_lock = threading.Lock()
_current_token_index = 0


def _get_current_client() -> OpenAI:
    """Tạo OpenAI Client với Token hiện tại."""
    return OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=config.GITHUB_TOKENS[_current_token_index],
        max_retries=0,
    )


def _rotate_token() -> OpenAI:
    """Chuyển sang Token tiếp theo trong danh sách (thread-safe)."""
    global _current_token_index
    with _token_lock:
        _current_token_index = (_current_token_index + 1) % len(config.GITHUB_TOKENS)
        logger.info(
            f"🔄 ĐÃ ĐỔI SÚNG: CHUYỂN SANG TOKEN SỐ "
            f"{_current_token_index + 1}/{len(config.GITHUB_TOKENS)}"
        )
    return _get_current_client()


def _classify_comments_in_batch(comments_list: list[dict]) -> list[dict]:
    """Gọi GPT-4o phân tích một batch bình luận.

    Args:
        comments_list: Danh sách dict bình luận, mỗi dict phải có key ``text``.

    Returns:
        Danh sách dict kết quả phân tích từ AI. Trả về list rỗng nếu thất bại.
    """
    # Lấy súng (Client) hiện tại ra xài
    client = _get_current_client()

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
    max_retries = max(5, len(config.GITHUB_TOKENS) * 2)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3].strip()
            return json.loads(raw_text)

        except Exception as e:
            error_msg = str(e)
            if (
                "Too many requests" in error_msg
                or "429" in error_msg
                or "rate limit" in error_msg.lower()
            ):
                logger.warning(
                    f"⚠️ BÁO ĐỘNG: Token số {_current_token_index + 1} "
                    f"đã cạn kiệt thể lực (Rate Limit)!"
                )

                # NẾU CÓ NHIỀU HƠN 1 TOKEN -> ĐỔI SÚNG VÀ BẮN TIẾP NGAY LẬP TỨC
                if len(config.GITHUB_TOKENS) > 1:
                    client = _rotate_token()
                    time.sleep(2)  # Nghỉ xíu để API khởi động
                else:
                    # NẾU CHỈ CÓ 1 TOKEN -> BẮT BUỘC PHẢI NGỦ ĐỂ CHỜ HỒI MÁU
                    logger.info(
                        "⏳ Hệ thống chỉ có 1 Token. Đang ngủ 15 giây chờ hồi máu..."
                    )
                    time.sleep(15)
            else:
                logger.error(f"❌ Lỗi API Batch: {e}")
                return []

    logger.error(
        "❌ Đã xoay vòng hết sạch Token nhưng vẫn bị Rate Limit. "
        "Trả về Hàng đợi để xử lý sau."
    )
    return []


def analyze_comments_batch(comments: list[dict]) -> list[dict]:
    """Phân tích một batch bình luận bằng GPT-4o.

    Gọi AI phân tích NLP chuyên sâu cho từng bình luận:
    - Phân loại (category): KHACH_HOI_DAP, TICH_CUC, TIEU_CUC_GOP_Y, v.v.
    - Độ độc hại (toxicity_level): 1-10
    - Điểm cảm xúc (sentiment_score): 1-10
    - Mục đích người dùng, insight, và hành động đề xuất

    Args:
        comments: Danh sách dict bình luận, mỗi dict cần có key ``text``.

    Returns:
        Danh sách dict bình luận gốc, mỗi dict được bổ sung key ``ai_analysis``
        chứa kết quả phân tích từ AI. Nếu AI thất bại, trả về danh sách gốc
        không có ``ai_analysis``.
    """
    logger.info(
        f"▶ KÍCH HOẠT AI PHÂN TÍCH CHUYÊN SÂU NLP ({len(comments)} BÌNH LUẬN)..."
    )

    if not config.GITHUB_TOKENS:
        logger.error("❌ CHƯA CẤU HÌNH GITHUB_TOKENS TRONG FILE .ENV")
        return comments

    # Không cần truyền Client vào nữa, hàm tự động lấy súng
    ai_results = _classify_comments_in_batch(comments)

    if not ai_results:
        return comments

    for result in ai_results:
        comment_id = result.get("id")
        if comment_id is not None and comment_id < len(comments):
            comments[comment_id]["ai_analysis"] = result
            if "id" in comments[comment_id]["ai_analysis"]:
                del comments[comment_id]["ai_analysis"]["id"]

            logger.info(f"💬 Bình luận: {comments[comment_id]['text']}")
            logger.info(f"🧠 Tư duy NLP: {result.get('thinking_process', '')}")
            logger.info(f"🎯 Ý định người dùng: {result.get('user_intent', '')}")
            logger.info(
                f"🏷️ Nhãn: {result.get('category', '')} | "
                f"Độ độc hại: {result.get('toxicity_level', 0)}/10"
            )

    return comments


def generate_ideas_from_comments(comments: list[dict]) -> list[dict]:
    """Cỗ Máy Đẻ Ý Tưởng: Dùng AI tổng hợp bình luận và đề xuất nội dung video mới.
    
    Args:
        comments: Danh sách các bình luận đã được AI phân tích trước đó (có chứa intent, insight).
        
    Returns:
        Danh sách các ý tưởng video (title, rationale, key_points).
    """
    logger.info(f"▶ KÍCH HOẠT CỖ MÁY ĐẺ Ý Tưởng ({len(comments)} BÌNH LUẬN)...")
    
    if not comments:
        logger.warning("⚠️ Không có bình luận nào để phân tích ý tưởng.")
        return []

    if not config.GITHUB_TOKENS:
        logger.error("❌ CHƯA CẤU HÌNH GITHUB_TOKENS TRONG FILE .ENV")
        return []

    # Format the data for the prompt to save tokens (we don't need everything)
    payload_data = []
    for c in comments:
        payload_data.append({
            "text": c.get("comment_text"),
            "category": c.get("category"),
            "intent": c.get("user_intent"),
            "insight": c.get("key_insight")
        })
        
    json_payload = json.dumps(payload_data, ensure_ascii=False)

    prompt = f"""
Bạn là một Đạo diễn Nội dung (Content Strategist) cho kênh YouTube.
Nhiệm vụ của bạn là phân tích danh sách bình luận của khán giả từ các video đối thủ hoặc video cũ để ĐỀ XUẤT 5 Ý TƯỞNG VIDEO TIẾP THEO đảm bảo sẽ có nhiều lượt xem.

[PHÂN TÍCH NHU CẦU]:
Hãy chú ý đặc biệt đến các bình luận thuộc nhóm KHACH_HOI_DAP (Khán giả hỏi) và TIEU_CUC_GOP_Y (Khán giả chê video gốc).
Đó là những lỗ hổng nội dung (Content Gaps) mà bạn có thể khai thác để làm video giải quyết vấn đề tốt hơn.

BẮT BUỘC trả về MỘT MẢNG JSON (JSON Array) chứa đúng 5 ý tưởng, KHÔNG có text giải thích bên ngoài.

Cấu trúc mỗi ý tưởng:
{{
    "title": "<Tiêu đề video cực kỳ giật gân, hấp dẫn, chuẩn CTR cao>",
    "rationale": "<Lý do vì sao làm video này lại thắng? Dựa trên câu hỏi nào/sự phẫn nộ nào của khán giả trong bình luận?>",
    "key_points": [
        "<Ý chính 1 cần nói trong video>",
        "<Ý chính 2>",
        "<Ý chính 3>"
    ],
    "target_emotion": "<Cảm xúc muốn mang lại cho khán giả (ví dụ: Thỏa mãn, Bất ngờ, Đồng cảm)>"
}}

[Dữ liệu Khán giả]:
{json_payload}
"""

    max_retries = max(5, len(config.GITHUB_TOKENS) * 2)
    client = _get_current_client()

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3].strip()
                
            ideas = json.loads(raw_text)
            logger.info("🎉 Đã đẻ ra %d ý tưởng video siêu cháy!", len(ideas))
            return ideas

        except Exception as e:
            error_msg = str(e)
            if "Too many requests" in error_msg or "429" in error_msg or "rate limit" in error_msg.lower():
                logger.warning(f"⚠️ Rate Limit khi đẻ ý tưởng. Đang xoay token...")
                if len(config.GITHUB_TOKENS) > 1:
                    client = _rotate_token()
                    time.sleep(2)
                else:
                    time.sleep(15)
            else:
                logger.error(f"❌ Lỗi AI Idea Generation: {e}")
                return []

    logger.error("❌ Đã thử nhiều lần nhưng AI vẫn bị chặn rate limit.")
    return []


def generate_flaw_report(comments: list[dict]) -> dict:
    """Radar Bắt Lỗi: Dùng AI phân tích bình luận chê bai để tìm điểm yếu của đối thủ.
    
    Args:
        comments: Danh sách các bình luận tiêu cực/góp ý.
        
    Returns:
        Báo cáo điểm yếu (Dict chứa overall_sentiment, top_flaws, attack_strategy).
    """
    logger.info(f"🚨 KÍCH HOẠT RADAR BẮT LỖI ĐỐI THỦ ({len(comments)} LỜI CHÊ BAI)...")
    
    if not comments:
        logger.warning("⚠️ Không có lời chê bai nào đáng chú ý.")
        return {}

    if not config.GITHUB_TOKENS:
        logger.error("❌ CHƯA CẤU HÌNH GITHUB_TOKENS TRONG FILE .ENV")
        return {}

    # Lọc payload siêu nhẹ
    payload_data = []
    for c in comments:
        payload_data.append({
            "text": c.get("comment_text"),
            "intent": c.get("user_intent"),
            "score": c.get("sentiment_score")
        })
        
    json_payload = json.dumps(payload_data, ensure_ascii=False)

    prompt = f"""
Bạn là một Chuyên gia Phân tích Đối thủ (Competitor Analyst) cho kênh YouTube.
Nhiệm vụ của bạn là đọc các BÌNH LUẬN CHÊ BAI từ khán giả của đối thủ và viết một BÁO CÁO ĐIỂM YẾU.

BẮT BUỘC trả về MỘT OBJECT JSON duy nhất, KHÔNG có text giải thích bên ngoài.

Cấu trúc JSON:
{{
    "summary": "<Tóm tắt 1 câu về sự thất vọng chung của khán giả>",
    "flaws": [
        {{
            "issue": "<Điểm yếu 1 (VD: Nội dung quá lan man)>",
            "evidence": "<Trích dẫn ý chính mà khán giả chê>"
        }},
        ... (Tối đa 3 điểm yếu lớn nhất)
    ],
    "attack_strategy": {{
        "title_idea": "<Gợi ý Tiêu đề video của bạn để thu hút lượng fan đang thất vọng này>",
        "key_action": "<Bạn phải làm gì trong video để ăn đứt đối thủ? (VD: Đi thẳng vào vấn đề, thêm sub)>"
    }}
}}

[Lời chê của Khán giả]:
{json_payload}
"""

    max_retries = max(5, len(config.GITHUB_TOKENS) * 2)
    client = _get_current_client()

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3].strip()
                
            report = json.loads(raw_text)
            logger.info("🎯 Đã phân tích xong Điểm Yếu đối thủ!")
            return report

        except Exception as e:
            error_msg = str(e)
            if "Too many requests" in error_msg or "429" in error_msg or "rate limit" in error_msg.lower():
                logger.warning(f"⚠️ Rate Limit khi bắt lỗi. Đang xoay token...")
                if len(config.GITHUB_TOKENS) > 1:
                    client = _rotate_token()
                    time.sleep(2)
                else:
                    time.sleep(15)
            else:
                logger.error(f"❌ Lỗi AI Flaw Detector: {e}")
                return {}

    logger.error("❌ Bắt lỗi thất bại do Rate limit.")
    return {}


def analyze_frame_vision(frame_path: str, ocr_text: str = "") -> dict:
    """Phân tích frame video bằng GPT-4o Vision.

    Gửi hình ảnh frame (base64) kèm OCR text đã trích xuất trước đó
    đến GPT-4o Vision để phân tích cấu trúc nội dung hiển thị.

    Args:
        frame_path: Đường dẫn tới file ảnh frame cần phân tích.
        ocr_text: Text đã được OCR trích xuất trước (giúp Vision chính xác hơn).

    Returns:
        Dict chứa kết quả phân tích có cấu trúc (scene_type, contains_code, ...).
        Trả về dict mặc định nếu thất bại.
    """
    default_result = {
        "contains_code": False,
        "code_language": None,
        "has_terminal": False,
        "has_error": False,
        "error_message": None,
        "has_diagram": False,
        "has_ui": False,
        "ui_type": None,
        "has_chart": False,
        "has_table": False,
        "scene_type": "other",
        "description_short": "",
    }

    if not config.GITHUB_TOKENS:
        logger.error("❌ CHƯA CẤU HÌNH GITHUB_TOKENS TRONG FILE .ENV")
        return default_result

    # Đọc file ảnh và encode base64
    try:
        with open(frame_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
    except FileNotFoundError:
        logger.error(f"❌ Không tìm thấy file frame: {frame_path}")
        return default_result
    except Exception as e:
        logger.error(f"❌ Lỗi đọc file frame {frame_path}: {e}")
        return default_result

    ocr_context = (
        f"\n\n[Thông tin bổ sung]: OCR đã phát hiện text sau trong frame:\n{ocr_text}"
        if ocr_text
        else ""
    )

    prompt = f"""Bạn là một chuyên gia phân tích nội dung video (Video Content Analyst).
Hãy phân tích frame hình ảnh này và trả về KẾT QUẢ JSON có cấu trúc.

BẮT BUỘC trả về MỘT OBJECT JSON duy nhất, KHÔNG có markdown code block, KHÔNG giải thích thêm.

Cấu trúc JSON:
{{
    "contains_code": <true/false — frame có chứa source code không?>,
    "code_language": "<tên ngôn ngữ lập trình hoặc null nếu không có code>",
    "has_terminal": <true/false — frame có chứa terminal/command line không?>,
    "has_error": <true/false — frame có hiển thị lỗi/error không?>,
    "error_message": "<nội dung lỗi hoặc null>",
    "has_diagram": <true/false — frame có chứa sơ đồ/diagram không?>,
    "has_ui": <true/false — frame có chứa giao diện UI không?>,
    "ui_type": "<loại UI (web, mobile, desktop) hoặc null>",
    "has_chart": <true/false — frame có chứa biểu đồ/chart không?>,
    "has_table": <true/false — frame có chứa bảng dữ liệu không?>,
    "scene_type": "<một trong: code_editor, terminal, browser, slide, talking_head, diagram, other>",
    "description_short": "<mô tả ngắn 1-2 câu nội dung frame>"
}}{ocr_context}"""

    max_retries = max(5, len(config.GITHUB_TOKENS) * 2)
    client = _get_current_client()

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                },
                            },
                        ],
                    }
                ],
            )
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3].strip()

            result = json.loads(raw_text)
            logger.info(
                f"✅ Phân tích Vision thành công: scene={result.get('scene_type')} "
                f"| {result.get('description_short', '')}"
            )
            return result

        except Exception as e:
            error_msg = str(e)
            if (
                "Too many requests" in error_msg
                or "429" in error_msg
                or "rate limit" in error_msg.lower()
            ):
                logger.warning(
                    f"⚠️ Rate Limit khi phân tích Vision. Đang xoay token... "
                    f"(lần {attempt + 1}/{max_retries})"
                )
                if len(config.GITHUB_TOKENS) > 1:
                    client = _rotate_token()
                    time.sleep(2)
                else:
                    time.sleep(15)
            else:
                logger.error(f"❌ Lỗi AI Vision: {e}")
                return default_result

    logger.error("❌ Phân tích Vision thất bại do Rate Limit liên tục.")
    return default_result


def correct_vietnamese_ocr(text: str) -> str:
    """Dùng GPT-4o siêu tốc để sửa lỗi chính tả tiếng Việt do OCR đọc sai."""
    if not text or len(text) < 3:
        return text
        
    prompt = f"""Bạn là chuyên gia sửa lỗi chính tả tiếng Việt. 
Dòng chữ sau đây được trích xuất bằng thuật toán OCR từ phụ đề Video nên bị lỗi sai dấu nặng nề (ví dụ: 'ö' -> 'ơ/ố/ồ/ỏ', 'ä' -> 'a/ả/á', 'ü' -> 'ư/ứ', bị dính chữ, sai vần).
Hãy sửa nó thành câu tiếng Việt hoàn chỉnh chuẩn xác nhất có thể theo ngữ cảnh.
CHỈ TRẢ VỀ CÂU ĐÃ SỬA, KHÔNG ĐƯỢC GIẢI THÍCH THÊM BẤT CỨ TỪ NÀO.

Text gốc: {text}"""

    max_retries = max(2, len(config.GITHUB_TOKENS))
    client = _get_current_client()
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100
            )
            corrected = response.choices[0].message.content.strip()
            # Bỏ ngoặc kép nếu AI tự thêm vào
            if corrected.startswith('"') and corrected.endswith('"'):
                corrected = corrected[1:-1]
            logger.info(f"✨ AI Sửa lỗi OCR: '{text}' -> '{corrected}'")
            return corrected
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Too many requests" in error_msg:
                if len(config.GITHUB_TOKENS) > 1:
                    client = _rotate_token()
                else:
                    time.sleep(2)
            else:
                break
    return text

"""
OCR Engine — Trích xuất văn bản từ frame video bằng PaddleOCR.

Module này là thành phần OCR trong Ingest Pipeline (Giai đoạn 2):
- Khởi tạo PaddleOCR (singleton, chạy trên CPU)
- Trích xuất text từ từng frame ảnh
- Phát hiện code trong frame (Python, JS, Bash, SQL, v.v.)
- Xử lý batch nhiều frame cùng lúc
"""

import re
import threading
from pathlib import Path
import cv2

from utils.config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────
# SINGLETON: Giữ instance RapidOCR duy nhất (chỉ load model 1 lần)
# ─────────────────────────────────────────────────────────────────

_ocr_instance = None
# Khởi tạo singleton an toàn khi gọi từ nhiều luồng.
_ocr_init_lock = threading.Lock()
# RapidOCR không đảm bảo thread-safe khi nhiều luồng gọi cùng 1 instance.
# Bảo vệ lời gọi inference bằng lock để chạy song song an toàn.
_ocr_infer_lock = threading.Lock()


def init_ocr():
    """Khởi tạo RapidOCR engine (singleton pattern).

    Sử dụng thư viện rapidocr-onnxruntime siêu nhẹ, không rò rỉ bộ nhớ.
    Chạy trên CPU với tốc độ rất nhanh và ổn định. Số luồng ONNX được lấy
    từ config.OCR_NUM_THREADS (0 = để thư viện tự quyết).

    Returns:
        RapidOCR instance, hoặc None nếu chưa cài.
    """
    global _ocr_instance

    if _ocr_instance is not None:
        return _ocr_instance

    with _ocr_init_lock:
        # Kiểm tra lại bên trong lock (double-checked locking)
        if _ocr_instance is not None:
            return _ocr_instance

        try:
            from rapidocr_onnxruntime import RapidOCR

            logger.info("🔄 Đang khởi tạo RapidOCR engine (ONNX Runtime)...")
            num_threads = getattr(config, "OCR_NUM_THREADS", 0)
            if num_threads and num_threads > 0:
                # Cấu hình số luồng nội bộ cho từng model (det/cls/rec)
                _ocr_instance = RapidOCR(
                    det_use_cuda=False,
                    intra_op_num_threads=num_threads,
                )
                logger.info("✅ RapidOCR sẵn sàng (ONNX, intra_op_num_threads=%d)", num_threads)
            else:
                _ocr_instance = RapidOCR()
                logger.info("✅ RapidOCR đã sẵn sàng (ONNX mode)")
            return _ocr_instance

        except TypeError:
            # Một số phiên bản RapidOCR không nhận tham số intra_op_num_threads.
            from rapidocr_onnxruntime import RapidOCR
            _ocr_instance = RapidOCR()
            logger.info("✅ RapidOCR đã sẵn sàng (ONNX mode, tham số mặc định)")
            return _ocr_instance
        except ImportError:
            logger.error(
                "❌ Chưa cài RapidOCR. Chạy:\n"
                "   pip install rapidocr-onnxruntime"
            )
            return None


# ─────────────────────────────────────────────────────────────────
# CÁC PATTERN NHẬN DIỆN CODE TRONG TEXT
# ─────────────────────────────────────────────────────────────────

# Mỗi ngôn ngữ có danh sách regex pattern đặc trưng
_CODE_LANGUAGE_PATTERNS: dict[str, list[re.Pattern]] = {
    "python": [
        re.compile(r"\bdef\s+\w+\s*\("),
        re.compile(r"\bclass\s+\w+"),
        re.compile(r"\bimport\s+\w+"),
        re.compile(r"\bfrom\s+\w+\s+import\b"),
        re.compile(r"\bself\.\w+"),
        re.compile(r"\bprint\s*\("),
        re.compile(r"\belif\b"),
        re.compile(r"\bexcept\s+\w+"),
        re.compile(r">>>\s"),
        re.compile(r"__\w+__"),
    ],
    "javascript": [
        re.compile(r"\bfunction\s+\w+\s*\("),
        re.compile(r"\bconst\s+\w+\s*="),
        re.compile(r"\blet\s+\w+\s*="),
        re.compile(r"\bvar\s+\w+\s*="),
        re.compile(r"=>\s*[{(]"),
        re.compile(r"\bconsole\.\w+\s*\("),
        re.compile(r"\brequire\s*\("),
        re.compile(r"\bexport\s+(default\s+)?"),
        re.compile(r"\basync\s+function\b"),
        re.compile(r"\bawait\s+\w+"),
        re.compile(r"\bdocument\.\w+"),
    ],
    "bash": [
        re.compile(r"^\s*#!\s*/bin/(ba)?sh", re.MULTILINE),
        re.compile(r"\$\{\w+\}"),
        re.compile(r"\$\w+"),
        re.compile(r"\becho\s+"),
        re.compile(r"\bsudo\s+"),
        re.compile(r"\bapt(-get)?\s+install\b"),
        re.compile(r"\bpip\s+install\b"),
        re.compile(r"\bcd\s+[/~]"),
        re.compile(r"\|\s*grep\b"),
        re.compile(r"\bcurl\s+-"),
        re.compile(r"\bchmod\s+"),
    ],
    "sql": [
        re.compile(r"\bSELECT\s+.+\bFROM\b", re.IGNORECASE),
        re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE),
        re.compile(r"\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE),
        re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
        re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE),
        re.compile(r"\bWHERE\s+\w+\s*=", re.IGNORECASE),
        re.compile(r"\bJOIN\s+\w+\s+ON\b", re.IGNORECASE),
        re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE),
        re.compile(r"\bORDER\s+BY\b", re.IGNORECASE),
    ],
    "java": [
        re.compile(r"\bpublic\s+(static\s+)?void\s+main\b"),
        re.compile(r"\bpublic\s+class\s+\w+"),
        re.compile(r"\bSystem\.out\.print"),
        re.compile(r"\bprivate\s+\w+\s+\w+"),
        re.compile(r"\bnew\s+\w+\s*\("),
        re.compile(r"\b@Override\b"),
    ],
    "cpp": [
        re.compile(r"#include\s*<\w+>"),
        re.compile(r"\bstd::\w+"),
        re.compile(r"\bcout\s*<<"),
        re.compile(r"\bcin\s*>>"),
        re.compile(r"\bint\s+main\s*\("),
        re.compile(r"\bvoid\s+\w+\s*\("),
        re.compile(r"\busing\s+namespace\b"),
    ],
    "html": [
        re.compile(r"<\s*div[\s>]"),
        re.compile(r"<\s*/?\s*(html|body|head|p|span|a|img)\b", re.IGNORECASE),
        re.compile(r'<\s*\w+\s+class\s*=\s*"'),
        re.compile(r"<\s*script[\s>]"),
        re.compile(r"<\s*style[\s>]"),
    ],
    "css": [
        re.compile(r"\.\w+\s*\{"),
        re.compile(r"#\w+\s*\{"),
        re.compile(r"\bmargin\s*:\s*\d+"),
        re.compile(r"\bpadding\s*:\s*\d+"),
        re.compile(r"\bdisplay\s*:\s*(flex|grid|block|none)\b"),
        re.compile(r"\bbackground-color\s*:"),
    ],
}

# Pattern chung phát hiện "có code" (không phân biệt ngôn ngữ)
_GENERIC_CODE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bdef\s+\w+"),
    re.compile(r"\bclass\s+\w+"),
    re.compile(r"\bimport\s+\w+"),
    re.compile(r"\bfunction\s+\w+"),
    re.compile(r"\breturn\s+\w+"),
    re.compile(r"[a-zA-Z_]\w*\s*\(.*\)\s*[{:]"),       # Khai báo hàm
    re.compile(r"\w+\s*=\s*\w+\s*\("),                  # Gọi hàm
    re.compile(r"^\s*//\s*\w+", re.MULTILINE),           # Comment //
    re.compile(r"^\s*#\s*(include|define|!\s*/bin)", re.MULTILINE),
    re.compile(r"->\s*\w+"),                             # Return type annotation
    re.compile(r"\{\s*\n"),                              # Mở block {
    re.compile(r"\}\s*;?\s*$", re.MULTILINE),            # Đóng block }
    re.compile(r">>>\s"),                                # Python REPL
    re.compile(r"\b(if|else|for|while)\s*\("),           # Control flow
    re.compile(r"\bprint\s*\("),
    re.compile(r"console\.log\s*\("),
    re.compile(r"#include\s*<"),
]

# Ngưỡng tối thiểu pattern match để xác nhận "có code"
_MIN_GENERIC_MATCHES = 2
_MIN_LANGUAGE_MATCHES = 2


def _detect_code_language(text: str) -> str | None:
    """Phát hiện ngôn ngữ lập trình trong text dựa trên pattern matching.

    Đếm số pattern match cho từng ngôn ngữ, ngôn ngữ nào match nhiều nhất thắng.

    Args:
        text: Toàn bộ text đã OCR.

    Returns:
        Tên ngôn ngữ (python, javascript, ...) hoặc None nếu không phát hiện.
    """
    if not text or not text.strip():
        return None

    best_language = None
    best_score = 0

    for language, patterns in _CODE_LANGUAGE_PATTERNS.items():
        score = sum(1 for p in patterns if p.search(text))
        if score > best_score and score >= _MIN_LANGUAGE_MATCHES:
            best_score = score
            best_language = language

    return best_language


def _contains_code(text: str) -> bool:
    """Kiểm tra text có chứa code hay không dựa trên Mật độ Ký tự đặc biệt.

    Mọi ngôn ngữ lập trình đều dùng nhiều: { } [ ] ( ) = ; < > / \ *
    Cách này vượt trội hơn Regex vì không phụ thuộc ngôn ngữ và chịu lỗi OCR tốt.

    Args:
        text: Toàn bộ text đã OCR.

    Returns:
        True nếu text có khả năng cao là màn hình lập trình.
    """
    if not text or len(text.strip()) < 15:
        return False

    # Đếm số lượng ký tự đặc biệt thường có trong code
    special_chars = set("{}[]()=;<>/\*+-_")
    char_count = sum(1 for char in text if char in special_chars)
    
    # Tính tỷ lệ (Mật độ)
    density = char_count / len(text)
    
    # Nếu mật độ > 3.5%, chắc chắn là code!
    if density >= 0.035:
        return True

    # Fallback: Dùng Regex cũ vớt vát nếu mật độ thấp nhưng có từ khóa rõ ràng
    match_count = sum(1 for p in _GENERIC_CODE_PATTERNS if p.search(text))
    return match_count >= 1


# ─────────────────────────────────────────────────────────────────
# TRÍCH XUẤT TEXT TỪ 1 FRAME
# ─────────────────────────────────────────────────────────────────

def extract_text_from_frame(frame_path: str) -> dict:
    """Trích xuất text từ một frame ảnh bằng PaddleOCR.

    Args:
        frame_path: Đường dẫn tới file ảnh frame (vd: frame_0001.jpg).

    Returns:
        Dict chứa kết quả OCR:
        {
            'detected_text': str,      # Toàn bộ text phát hiện được
            'contains_code': bool,     # Có chứa code hay không
            'code_language': str|None, # Ngôn ngữ lập trình (nếu có)
            'confidence': float,       # Độ tin cậy trung bình (0.0-1.0)
            'regions': [               # Chi tiết từng vùng text
                {'bbox': [...], 'text': str, 'conf': float},
                ...
            ]
        }
        Nếu OCR thất bại, trả về dict mặc định với giá trị rỗng.
    """
    # Kết quả mặc định khi lỗi
    default_result = {
        "detected_text": "",
        "contains_code": False,
        "code_language": None,
        "confidence": 0.0,
        "regions": [],
    }

    # Kiểm tra file tồn tại
    frame_file = Path(frame_path)
    if not frame_file.exists():
        logger.warning("⚠️ File frame không tồn tại: %s", frame_path)
        return default_result

    # Khởi tạo OCR engine (singleton/auto-reload)
    ocr = init_ocr()
    if ocr is None:
        logger.error("❌ Không thể khởi tạo RapidOCR engine.")
        return default_result

    try:
        # Đọc ảnh để lấy kích thước (phục vụ lọc ROI)
        img = cv2.imread(str(frame_file))
        if img is None:
            return default_result
        img_h, img_w = img.shape[:2]

        # --- TỐI ƯU HÌNH ẢNH (Image Pre-processing) CHO CODE/IT ---
        # Chỉ áp dụng Grayscale & Tăng nhẹ tương phản
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.convertScaleAbs(gray, alpha=1.1, beta=0)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        # Chạy OCR trên ảnh bằng RapidOCR.
        # Bọc trong lock vì instance dùng chung không thread-safe; ONNX Runtime
        # vẫn tự song song hóa nội bộ qua intra_op_num_threads.
        with _ocr_infer_lock:
            result, _ = ocr(img)

        if not result:
            return default_result

        # Parse kết quả từng vùng text
        regions = []
        texts = []
        confidences = []

        for line in result:
            # RapidOCR format: [bbox, text, confidence]
            bbox = line[0]              # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            text = line[1].strip()      # Nội dung text
            conf = float(line[2])       # Độ tin cậy (0.0 - 1.0)
            
            # --- LỌC RÁC: Bỏ qua những cụm từ OCR quá mờ (Confidence < 0.50) ---
            if conf < 0.50 or not text:
                continue

            # Tính toán toạ độ để phân loại ROI
            x_min = min(p[0] for p in bbox)
            x_max = max(p[0] for p in bbox)
            y_min = min(p[1] for p in bbox)
            y_max = max(p[1] for p in bbox)
            
            # --- BƯỚC 6: ROI EXTRACTION (Lọc Logo/Watermark bằng Heuristic) ---
            is_logo = False
            # Góc trên trái (Logo đài, channel): Toàn bộ box phải nằm gọn ở góc trên trái
            if y_max < 0.20 * img_h and x_max < 0.25 * img_w:
                is_logo = True
            # Góc trên phải (Watermark): Toàn bộ box phải nằm gọn ở góc trên phải
            elif y_max < 0.20 * img_h and x_min > 0.75 * img_w:
                is_logo = True
            # Góc dưới phải (Logo subscribe thường gặp ở YouTube): Toàn bộ box phải ở góc dưới phải
            elif y_min > 0.80 * img_h and x_min > 0.75 * img_w:
                is_logo = True
                
            if is_logo:
                logger.debug(f"🗑️ Đã lọc bỏ Logo/Watermark: '{text}'")
                continue

            # Tạm thời lưu tất cả các text hợp lệ
            regions.append({
                "bbox": bbox,
                "text": text,
                "conf": round(conf, 4),
            })
            texts.append(text)
            confidences.append(conf)

        # Ghép toàn bộ text lại
        detected_text = "\n".join(texts)

        # Tính confidence trung bình
        avg_confidence = (
            round(sum(confidences) / len(confidences), 4)
            if confidences
            else 0.0
        )

        # Phát hiện code
        has_code = _contains_code(detected_text)
        code_lang = _detect_code_language(detected_text) if has_code else None

        return {
            "detected_text": detected_text,
            "contains_code": has_code,
            "code_language": code_lang,
            "confidence": avg_confidence,
            "regions": regions,
        }

    except Exception as e:
        logger.error("❌ Lỗi OCR khi xử lý frame %s: %s", frame_path, e)
        return default_result


# ─────────────────────────────────────────────────────────────────
# XỬ LÝ BATCH NHIỀU FRAME
# ─────────────────────────────────────────────────────────────────

def batch_ocr_frames(frame_paths: list[str]) -> list[dict]:
    """Trích xuất text từ nhiều frame ảnh (xử lý tuần tự).

    Gọi extract_text_from_frame() cho từng frame, log tiến độ mỗi 10 frame.

    Args:
        frame_paths: Danh sách đường dẫn tới các file ảnh frame.

    Returns:
        Danh sách dict kết quả OCR (cùng format với extract_text_from_frame).
    """
    total = len(frame_paths)
    if total == 0:
        logger.warning("⚠️ Danh sách frame rỗng, không có gì để OCR.")
        return []

    logger.info("🔄 Bắt đầu OCR batch: %d frame(s)...", total)

    results = []
    code_frame_count = 0

    for idx, frame_path in enumerate(frame_paths):
        result = extract_text_from_frame(frame_path)
        results.append(result)

        if result.get("contains_code"):
            code_frame_count += 1

        # Log tiến độ mỗi 10 frame
        processed = idx + 1
        if processed % 10 == 0 or processed == total:
            logger.info(
                "📊 OCR tiến độ: %d/%d frames (%.0f%%)",
                processed, total, (processed / total) * 100,
            )

    logger.info(
        "✅ OCR batch hoàn tất: %d frame(s), %d frame chứa code",
        total, code_frame_count,
    )
    return results

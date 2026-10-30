"""
Whisper Server — Faster-Whisper trên Google Colab.

Script này chạy trên Google Colab (GPU T4/V100) để cung cấp API Speech-to-Text.

CÁCH SỬ DỤNG TRÊN GOOGLE COLAB:
─────────────────────────────────────────────────────────────────
1. Tạo notebook mới trên Google Colab
2. Chuyển Runtime → GPU (T4 miễn phí hoặc V100 nếu có Pro)
3. Copy toàn bộ file này vào 1 cell
4. Chạy cell → Server sẽ khởi động và in ra public URL (ngrok)
5. Copy URL đó vào file .env trên máy local:
   WHISPER_API_URL=https://xxxx-xxxx.ngrok-free.app
─────────────────────────────────────────────────────────────────

API Endpoints:
    POST /transcribe    — Nhận file audio (.wav), trả về transcript JSON
    GET  /health        — Kiểm tra server còn sống
    GET  /              — Thông tin server

Thư viện cần cài (chạy trong cell riêng trên Colab):
    !pip install faster-whisper fastapi uvicorn python-multipart pyngrok
"""

# ═══════════════════════════════════════════════════════════════
# CELL 1: CÀI ĐẶT THƯ VIỆN (Chạy cell này trước)
# ═══════════════════════════════════════════════════════════════
# !pip install faster-whisper fastapi uvicorn python-multipart pyngrok

# ═══════════════════════════════════════════════════════════════
# CELL 2: CHẠY SERVER (Chạy cell này sau khi cài đặt xong)
# ═══════════════════════════════════════════════════════════════

import os
import tempfile
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse

# ─────────────────────────────────────────────────────────────
# CẤU HÌNH
# ─────────────────────────────────────────────────────────────

# Chọn model size:
#   "tiny"   — ~39MB VRAM, nhanh nhất, chất lượng thấp
#   "small"  — ~461MB VRAM, cân bằng tốc độ/chất lượng (KHUYẾN NGHỊ)
#   "medium" — ~1.5GB VRAM, chất lượng cao (cần Colab Pro)
#   "large-v3" — ~3GB VRAM, chất lượng tốt nhất (cần A100/V100)
MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")

# compute_type: "int8" (nhanh, nhẹ VRAM) hoặc "float16" (chính xác hơn)
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# Ngrok auth token (lấy từ https://dashboard.ngrok.com/get-started/your-authtoken)
NGROK_AUTH_TOKEN = os.getenv("NGROK_AUTH_TOKEN", "")

# Port cho FastAPI server
SERVER_PORT = int(os.getenv("WHISPER_PORT", "8000"))


# ─────────────────────────────────────────────────────────────
# LOAD WHISPER MODEL (Singleton — chỉ load 1 lần)
# ─────────────────────────────────────────────────────────────

_whisper_model = None


def get_whisper_model():
    """Lấy hoặc khởi tạo Whisper model (Singleton)."""
    global _whisper_model

    if _whisper_model is not None:
        return _whisper_model

    from faster_whisper import WhisperModel

    print(f"🔄 Đang tải Faster-Whisper model '{MODEL_SIZE}' (compute_type={COMPUTE_TYPE})...")
    start = time.time()

    _whisper_model = WhisperModel(
        MODEL_SIZE,
        device="cuda",          # Dùng GPU trên Colab
        compute_type=COMPUTE_TYPE,
    )

    elapsed = time.time() - start
    print(f"✅ Đã tải model thành công trong {elapsed:.1f} giây!")
    return _whisper_model


# ─────────────────────────────────────────────────────────────
# FASTAPI APPLICATION
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload Whisper model khi server khởi động."""
    print("🚀 Whisper Server đang khởi động...")
    get_whisper_model()  # Load model trước khi nhận request
    print("✅ Server sẵn sàng nhận request!")
    yield
    print("🛑 Server đang tắt...")


app = FastAPI(
    title="Faster-Whisper API Server",
    description="Speech-to-Text API chạy trên Google Colab GPU",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Thông tin server."""
    return {
        "service": "Faster-Whisper Transcription Server",
        "model": MODEL_SIZE,
        "compute_type": COMPUTE_TYPE,
        "status": "ready",
        "endpoints": {
            "POST /transcribe": "Gửi file audio (.wav) để dịch thành text",
            "GET /health": "Kiểm tra server còn sống",
        },
    }


@app.get("/health")
async def health_check():
    """Endpoint kiểm tra sức khỏe server."""
    model = get_whisper_model()
    return {
        "status": "healthy",
        "model_loaded": model is not None,
        "model_size": MODEL_SIZE,
    }


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Nhận file audio và trả về transcript có timestamp.

    Args:
        file: File audio (hỗ trợ .wav, .mp3, .m4a, .ogg, .flac)

    Returns:
        JSON với text, segments, language, processing_time
    """
    # Kiểm tra file có được gửi không
    if not file.filename:
        raise HTTPException(status_code=400, detail="Không có file audio trong request.")

    # Kiểm tra extension
    allowed_extensions = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Định dạng '{file_ext}' không được hỗ trợ. "
                   f"Chấp nhận: {', '.join(allowed_extensions)}",
        )

    # Lưu file tạm
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=file_ext, prefix="whisper_"
        ) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        file_size_mb = len(content) / (1024 * 1024)
        print(f"📥 Nhận file: {file.filename} ({file_size_mb:.1f} MB)")

        # Chạy Whisper
        model = get_whisper_model()
        start_time = time.time()

        segments_raw, info = model.transcribe(
            tmp_path,
            beam_size=5,
            language=None,          # Tự động phát hiện ngôn ngữ
            vad_filter=True,        # Voice Activity Detection — bỏ qua khoảng im lặng
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        # Thu thập segments
        segments = []
        full_text_parts = []

        for segment in segments_raw:
            seg_data = {
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": segment.text.strip(),
            }
            segments.append(seg_data)
            full_text_parts.append(segment.text.strip())

        full_text = " ".join(full_text_parts)
        processing_time = time.time() - start_time

        print(
            f"✅ Đã dịch xong: {len(segments)} segments, "
            f"ngôn ngữ: {info.language}, "
            f"thời gian xử lý: {processing_time:.1f}s"
        )

        return JSONResponse(content={
            "text": full_text,
            "segments": segments,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration_sec": round(segments[-1]["end"], 2) if segments else 0.0,
            "processing_time_sec": round(processing_time, 2),
            "model": MODEL_SIZE,
        })

    except Exception as e:
        print(f"❌ Lỗi transcription: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi xử lý audio: {str(e)}",
        )
    finally:
        # Dọn file tạm
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ─────────────────────────────────────────────────────────────
# KHỞI ĐỘNG SERVER + NGROK TUNNEL
# ─────────────────────────────────────────────────────────────

def start_server():
    """Khởi động FastAPI server với ngrok tunnel (cho Google Colab)."""

    # Tạo ngrok tunnel để có public URL
    public_url = None
    try:
        from pyngrok import ngrok

        if NGROK_AUTH_TOKEN:
            ngrok.set_auth_token(NGROK_AUTH_TOKEN)

        tunnel = ngrok.connect(SERVER_PORT)
        public_url = tunnel.public_url

        print("=" * 60)
        print("🌐 WHISPER API SERVER ĐÃ SẴN SÀNG!")
        print(f"   Public URL: {public_url}")
        print(f"   Local URL:  http://localhost:{SERVER_PORT}")
        print()
        print("📋 COPY DÒNG NÀY VÀO FILE .env TRÊN MÁY LOCAL:")
        print(f"   WHISPER_API_URL={public_url}")
        print("=" * 60)

    except ImportError:
        print("⚠️ pyngrok chưa được cài. Chạy: pip install pyngrok")
        print(f"   Server chỉ chạy local tại: http://localhost:{SERVER_PORT}")
    except Exception as e:
        print(f"⚠️ Không thể tạo ngrok tunnel: {e}")
        print(f"   Server chỉ chạy local tại: http://localhost:{SERVER_PORT}")

    # Chạy FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    start_server()
else:
    # Khi chạy trên Google Colab (không qua __main__)
    # Gọi hàm này trực tiếp trong cell:
    #   start_server()
    pass

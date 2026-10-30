# 🚀 YouTube AI Auto Ops

Hệ thống tự động hóa **Data Pipeline cấp độ doanh nghiệp**, được thiết kế theo kiến trúc Microservices để thu thập bình luận YouTube, phân tích NLP bằng GPT-4o (nhận diện từ lóng, teencode, cà khịa/mỉa mai), lưu trữ kết quả vào PostgreSQL, và cảnh báo tự động qua n8n webhook tới Telegram & Google Sheets — tất cả hoạt động trong thời gian thực (Real-time).

---

## 🏗️ Kiến Trúc Hệ Thống

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        YouTube AI Auto Ops                              │
├────────────────────┬────────────────────┬───────────────────────────────┤
│   GIAI ĐOẠN 1      │   GIAI ĐOẠN 2      │   GIAI ĐOẠN 3               │
│   Thu thập          │   Phân tích AI      │   Cảnh báo Tự động          │
│                    │                    │                               │
│  ┌──────────┐      │  ┌──────────┐      │  ┌──────────┐                │
│  │ YouTube  │      │  │ RabbitMQ │      │  │ Webhook  │                │
│  │   API    │─────▶│  │  Queue   │─────▶│  │  → n8n   │                │
│  └──────────┘      │  └──────────┘      │  └──────────┘                │
│       │            │       │            │       │                       │
│       ▼            │       ▼            │       ├──▶ Telegram           │
│  ┌──────────┐      │  ┌──────────┐      │       └──▶ Google Sheets     │
│  │  Redis   │      │  │  GPT-4o  │      │                               │
│  │  Cache   │      │  │ Analysis │      │                               │
│  └──────────┘      │  └──────────┘      │                               │
│                    │       │            │                               │
│                    │       ▼            │                               │
│                    │  ┌──────────┐      │                               │
│                    │  │PostgreSQL│      │                               │
│                    │  │ Database │      │                               │
│                    │  └──────────┘      │                               │
├────────────────────┴────────────────────┴───────────────────────────────┤
│  Command Center UI (FastAPI)  │  Metabase Dashboard  │  n8n Workflows  │
└───────────────────────────────┴──────────────────────┴─────────────────┘
```

---

## ⚡ Bắt Đầu Nhanh (Quick Start)

### 1. Clone và cài đặt

```bash
git clone https://github.com/YourUsername/youtube-ai-auto-ops.git
cd youtube-ai-auto-ops

# Tạo môi trường ảo (khuyến nghị)
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac

pip install -r requirements.txt
```

### 2. Cấu hình

```bash
copy .env.example .env
# Mở .env và điền: YOUTUBE_API_KEY, GITHUB_TOKENS
```

### 3. Khởi chạy hạ tầng Docker

```bash
cd docker
docker-compose up -d
cd ..
```

### 4. Vận hành (mở 3 Terminal)

```bash
# Terminal 1: API Server → http://localhost:8080
python run_api.py

# Terminal 2: Thu thập bình luận
python run_scraper.py

# Terminal 3: Phân tích AI
python run_ai_worker.py
```

---

## 📁 Cấu Trúc Dự Án

```
youtube_auto_ops/
├── run_api.py                  # Entry point: API Server
├── run_scraper.py              # Entry point: Scraper Worker
├── run_ai_worker.py            # Entry point: AI Worker
├── run_media_worker.py         # Entry point: Media Worker (Whisper, OCR, Frames)
├── requirements.txt            # Python dependencies
├── .env.example                # Template biến môi trường
│
├── api/                        # FastAPI web application
│   ├── app.py                  # FastAPI app + mount static files + CORS
│   └── routes/                 # dashboard, videos, auth, ideas, flaws, knowledge
│
├── core/                       # Business logic chính
│   ├── ai_analyzer.py          # Phân tích comment bằng GPT-4o (+ ideas, flaws, Vision)
│   ├── media_processor.py      # Transcript, tải audio (yt-dlp), trích xuất frames
│   ├── ocr_engine.py           # OCR frame (RapidOCR)
│   ├── embedding_engine.py     # Embedding tri thức (bge-m3)
│   ├── knowledge_builder.py    # Chunking + nạp tri thức vào Qdrant
│   ├── rag_agent.py            # Chatbot hỏi-đáp RAG
│   └── video_sources.py        # Tìm kiếm video từ nhiều nguồn
│
├── workers/                    # Background workers
│   ├── scraper_worker.py       # Producer: YouTube → Redis → RabbitMQ
│   ├── ai_worker.py            # Consumer: RabbitMQ → AI → PostgreSQL (+ DLQ)
│   ├── media_worker.py         # Whisper + OCR pipeline
│   ├── knowledge_worker.py     # Xây dựng knowledge base
│   └── rabbitmq_utils.py       # Tiện ích kết nối RabbitMQ
│
├── integrations/               # Tích hợp bên ngoài
│   ├── youtube_client.py       # YouTube Data API v3 + OAuth2 wrapper
│   └── n8n_webhook.py          # Gửi webhook tới n8n
│
├── database/                   # Tầng database
│   ├── connection.py           # Connection pool + schema init
│   ├── models.py               # Định nghĩa bảng + views
│   └── queries.py              # SQL queries (CRUD, parameterized)
│
├── utils/                      # Tiện ích dùng chung
│   ├── config.py               # Đọc biến môi trường (.env) — nguồn cấu hình duy nhất
│   ├── logger.py               # Cấu hình logging
│   └── cache.py                # Redis cache + deduplication (seen/done)
│
├── frontend/                   # Giao diện Command Center
│   ├── index.html
│   ├── css/
│   └── js/app.js
│
├── colab/                      # Whisper server chạy trên Google Colab
│   └── whisper_server.py
│
├── docker/                     # Docker configuration
│   ├── docker-compose.yml      # RabbitMQ, Redis, PostgreSQL, Metabase, n8n
│   ├── Dockerfile.api
│   └── Dockerfile.worker
│
├── docs/                       # Tài liệu
│   ├── architecture.md         # Kiến trúc hệ thống (mermaid diagrams)
│   └── setup.md                # Hướng dẫn cài đặt chi tiết
│
└── tests/                      # Unit tests (scraper, AI worker, JSON parser, RAG)
    └── ...
```

---

## 🌟 Tính Năng Cốt Lõi

| Tính năng | Mô tả |
|---|---|
| **Thu thập Real-time** | Smart Pagination + Early Exit, tối ưu API Quota |
| **Khử trùng lặp** | Redis In-Memory, loại bỏ 100% comment trùng (~1ms) |
| **Hàng đợi phân tán** | RabbitMQ Durable + Manual ACK, không mất dữ liệu khi crash |
| **Phân tích NLP** | GPT-4o nhận diện từ lóng, teencode, cà khịa/mỉa mai |
| **Token Rotation** | Xoay vòng API keys, hoạt động 24/7 không nghẽn |
| **Cảnh báo tự động** | n8n webhook → Telegram + Google Sheets |
| **Command Center** | Giao diện Glassmorphism theo dõi real-time |

---

## ⚙️ Cấu Hình (.env)

| Biến | Mô tả | Mặc định |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 key | *(bắt buộc)* |
| `GITHUB_TOKENS` | GitHub tokens cho GPT-4o (phân tách bằng `,`) | *(bắt buộc)* |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASS` | PostgreSQL password | `123456` |
| `DB_NAME` | Tên database | `youtube_ai_db` |
| `RABBITMQ_HOST` | RabbitMQ host | `localhost` |
| `REDIS_HOST` | Redis host | `localhost` |
| `N8N_WEBHOOK_URL` | URL webhook n8n | *(tùy chọn)* |
| `API_PORT` | Cổng API server | `8080` |
| `CORS_ORIGINS` | Origin được phép gọi API (phân cách `,`) | `http://localhost:8080,http://127.0.0.1:8080` |

> **Lưu ý**: Nếu chạy Python bên trong Docker, đổi `localhost` → tên container (`yt_rabbitmq`, `yt_redis`, `yt_postgres`).

---

## 🔌 API Endpoints

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/api/data` | Lấy dữ liệu dashboard (videos + jobs) |
| `POST` | `/api/search` | Tìm kiếm video theo từ khóa |
| `POST` | `/api/competitor` | Cào video kênh đối thủ |
| `POST` | `/api/sync` | Đồng bộ kênh của bạn (OAuth2) |
| `POST` | `/api/ideas/generate` | AI đề xuất ý tưởng video từ bình luận |
| `POST` | `/api/flaws/generate` | AI phân tích điểm yếu đối thủ |
| `POST` | `/api/knowledge/ask` | Hỏi đáp RAG trên knowledge base |
| `GET` | `/auth/login` | Đăng nhập Google OAuth2 (kết nối kênh) |
| `GET` | `/` | Giao diện Command Center (HTML) |

### Ví dụ sử dụng

```bash
# Lấy dữ liệu dashboard
curl http://localhost:8080/api/data

# Tìm kiếm video
curl -X POST http://localhost:8080/api/search \
  -H "Content-Type: application/json" \
  -d '{"keyword": "iphone 16"}'
```

---

## 🛠️ Công Nghệ Sử Dụng

- **Python 3.11+** — Ngôn ngữ chính
- **FastAPI + Uvicorn** — Web framework & ASGI server
- **RabbitMQ** — Message broker (hàng đợi phân tán)
- **Redis** — In-memory cache (khử trùng lặp)
- **PostgreSQL 15** — Cơ sở dữ liệu quan hệ
- **OpenAI GPT-4o** (qua GitHub Models) — Phân tích NLP
- **n8n** — Automation workflows
- **Metabase** — BI Dashboard
- **Docker Compose** — Orchestration hạ tầng

---

## 🧑‍💻 Hướng Dẫn Phát Triển

### Chạy tests

```bash
pytest tests/ -v
```

### Xóa cache Redis (cào lại từ đầu)

```bash
docker exec yt_redis redis-cli flushall
```

### Xem logs RabbitMQ

Truy cập http://localhost:15672 → Queues → `youtube_comments`

### Xử lý batch lỗi (Dead Letter Queue)

Khi AI worker thất bại xử lý 1 batch quá 3 lần liên tiếp (rate limit kéo dài, lỗi dữ liệu...),
batch sẽ bị đẩy sang queue `youtube_comments_dlq` kèm header `x-last-error`. Kiểm tra và xử lý lại:

```bash
# Xem số message đang kẹt trong DLQ
docker exec yt_rabbitmq rabbitmqctl list_queues name messages

# Xem nội dung lỗi của từng message
# Truy cập http://localhost:15672 → Queues → youtube_comments_dlq → Get Message(s)
```

### Thêm nguồn video mới

Sử dụng module `core/video_sources.py`:
- `search_videos_by_keyword()` — Tìm theo từ khóa
- `get_competitor_videos()` — Lấy video từ kênh đối thủ
- `sync_own_channel()` — Đồng bộ kênh của bạn (OAuth2)

---

## 📚 Tài Liệu Thêm

- [Kiến trúc hệ thống chi tiết](docs/architecture.md)
- [Hướng dẫn cài đặt từng bước](docs/setup.md)

---

## 📄 License

MIT License — Tự do sử dụng và phát triển.

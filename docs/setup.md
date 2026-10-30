# 📘 Hướng Dẫn Cài Đặt Chi Tiết — YouTube AI Auto Ops

## Yêu Cầu Hệ Thống

| Thành phần | Phiên bản tối thiểu | Ghi chú |
|---|---|---|
| Python | 3.10+ | Khuyến nghị 3.11 |
| Docker Desktop | 4.0+ | Bắt buộc cho hạ tầng (RabbitMQ, Redis, PostgreSQL) |
| Git | 2.0+ | Quản lý source code |
| RAM | 4GB+ | RabbitMQ + Redis + PostgreSQL + Metabase + n8n |
| Ổ cứng | 2GB trống | Cho Docker images và data volumes |

---

## Bước 1: Clone Dự Án

```bash
git clone https://github.com/YourUsername/youtube-ai-auto-ops.git
cd youtube-ai-auto-ops
```

---

## Bước 2: Cài Đặt Python Dependencies

Khuyến nghị sử dụng Virtual Environment:

```bash
# Tạo môi trường ảo
python -m venv venv

# Kích hoạt (Windows)
venv\Scripts\activate

# Kích hoạt (Linux/Mac)
source venv/bin/activate

# Cài đặt thư viện
pip install -r requirements.txt
```

---

## Bước 3: Cấu Hình Biến Môi Trường

Sao chép file mẫu và chỉnh sửa:

```bash
copy .env.example .env
```

Mở file `.env` và điền các giá trị thực:

```env
# BẮT BUỘC - Lấy từ Google Cloud Console
YOUTUBE_API_KEY=AIza...your_real_key

# BẮT BUỘC - Lấy từ GitHub Settings > Developer Settings > Personal Access Tokens
GITHUB_TOKENS=ghp_token1,ghp_token2,ghp_token3

# TÙY CHỌN - Thay đổi nếu bạn đã cấu hình khác trong Docker
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASS=123456
DB_NAME=youtube_ai_db

# TÙY CHỌN - Webhook n8n (bỏ trống nếu chưa setup)
N8N_WEBHOOK_URL=https://your-n8n.example.com/webhook/abc123
```

### Cách Lấy YouTube API Key

1. Truy cập [Google Cloud Console](https://console.cloud.google.com/)
2. Tạo Project mới (hoặc chọn project có sẵn)
3. Bật **YouTube Data API v3** trong Library
4. Vào **Credentials** → **Create Credentials** → **API Key**
5. Copy API Key vào `YOUTUBE_API_KEY` trong file `.env`

### Cách Lấy GitHub Tokens (cho GPT-4o)

1. Truy cập [GitHub Settings](https://github.com/settings/tokens)
2. **Generate new token (classic)**
3. Chọn scope: không cần scope nào (public access)
4. Copy token vào `GITHUB_TOKENS` (phân tách nhiều token bằng dấu phẩy)

---

## Bước 4: Khởi Chạy Hạ Tầng Docker

Đảm bảo Docker Desktop đang chạy, sau đó:

```bash
# Từ thư mục gốc dự án
cd docker
docker-compose up -d
```

Kiểm tra tất cả services đã chạy:

```bash
docker-compose ps
```

Kết quả mong đợi:

| Container | Trạng thái | Cổng |
|---|---|---|
| yt_rabbitmq | Up | 5672, 15672 |
| yt_redis | Up | 6379 |
| yt_postgres | Up | 5432 |
| yt_metabase | Up | 3000 |
| yt_n8n | Up | 5678 |

### Kiểm tra từng service:

- **RabbitMQ Dashboard**: http://localhost:15672 (đăng nhập: `guest` / `guest`)
- **Metabase Dashboard**: http://localhost:3000
- **n8n Workflow Editor**: http://localhost:5678 (đăng nhập: `admin` / `changeme`)

---

## Bước 5: Vận Hành Hệ Thống

Mở **3 Terminal riêng biệt** trong VS Code:

### Terminal 1 — API Server (Giao diện Command Center)
```bash
python run_api.py
```
→ Mở trình duyệt: http://localhost:8080

### Terminal 2 — Scraper Worker (Thu thập dữ liệu)
```bash
python run_scraper.py
```

### Terminal 3 — AI Worker (Phân tích bình luận)
```bash
python run_ai_worker.py
```

---

## Bước 6: Sử Dụng Giao Diện Web

1. Mở http://localhost:8080 trên trình duyệt
2. Nhập từ khóa vào ô tìm kiếm (VD: `iphone 16`, `Độ Mixi`)
3. Bấm **"Bắt đầu Quét"**
4. Theo dõi tiến độ trên bảng **Kho Video** và **Tiến độ Máy trạm**

---

## Xử Lý Sự Cố Thường Gặp

### ❌ Lỗi kết nối PostgreSQL
```
psycopg2.OperationalError: could not connect to server
```
**Nguyên nhân**: PostgreSQL chưa khởi chạy hoặc sai thông tin kết nối.
**Cách sửa**:
```bash
# Kiểm tra container đang chạy
docker ps | findstr postgres
# Nếu chưa chạy
cd docker && docker-compose up -d postgres
```

### ❌ Lỗi Rate Limit YouTube API
```
HttpError 403: The request cannot be completed because you have exceeded your quota
```
**Cách sửa**: Thêm nhiều API key vào `YOUTUBE_API_KEY` hoặc đợi quota reset (00:00 UTC).

### ❌ Lỗi Rate Limit GPT-4o
**Cách sửa**: Thêm nhiều GitHub tokens vào `GITHUB_TOKENS` (phân tách bằng dấu phẩy). Hệ thống sẽ tự động xoay vòng (Token Rotation).

### 🔄 Muốn cào lại toàn bộ từ đầu
Xóa trí nhớ Redis để thuật toán Early Exit không bị chặn:
```bash
docker exec yt_redis redis-cli flushall
```

---

## Cấu Trúc Thư Mục

```
youtube_auto_ops/
├── run_api.py              # Entry point: API Server
├── run_scraper.py          # Entry point: Scraper Worker
├── run_ai_worker.py        # Entry point: AI Worker
├── requirements.txt        # Python dependencies
├── .env.example            # Template biến môi trường
│
├── api/                    # FastAPI web application
│   ├── app.py              # FastAPI app + static files
│   └── routes.py           # API endpoints (/api/data, /api/search)
│
├── core/                   # Business logic chính
│   ├── ai_analyzer.py      # Phân tích comment bằng GPT-4o
│   └── video_sources.py    # Tìm kiếm video từ nhiều nguồn
│
├── workers/                # Background workers
│   ├── scraper_worker.py   # Thu thập comment từ YouTube
│   └── ai_worker.py        # Xử lý AI batch từ RabbitMQ
│
├── integrations/           # Tích hợp bên ngoài
│   ├── youtube_client.py   # YouTube Data API v3 wrapper
│   └── n8n_webhook.py      # Gửi webhook tới n8n
│
├── database/               # Tầng database
│   ├── connection.py       # Connection pool + schema init
│   └── queries.py          # SQL queries (CRUD)
│
├── utils/                  # Tiện ích dùng chung
│   ├── config.py           # Đọc biến môi trường (.env)
│   ├── logger.py           # Cấu hình logging
│   └── cache.py            # Redis cache + deduplication
│
├── frontend/               # Giao diện web
│   ├── index.html          # Trang Command Center
│   ├── css/style.css       # Stylesheet
│   └── js/app.js           # JavaScript logic
│
├── docker/                 # Docker configuration
│   ├── docker-compose.yml  # Orchestration hạ tầng
│   ├── Dockerfile.api      # Image cho API Server
│   └── Dockerfile.worker   # Image cho Workers
│
├── docs/                   # Tài liệu
│   ├── architecture.md     # Kiến trúc hệ thống
│   └── setup.md            # Hướng dẫn cài đặt
│
└── tests/                  # Unit tests
    └── test_placeholder.py # Placeholder tests
```

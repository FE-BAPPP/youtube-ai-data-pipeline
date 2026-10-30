# 🏗️ Kiến Trúc Hệ Thống — YouTube AI Auto Ops

## Tổng Quan

Hệ thống YouTube AI Auto Ops là một **Data Pipeline cấp độ doanh nghiệp** được thiết kế theo kiến trúc Microservices. Mục tiêu: tự động thu thập bình luận YouTube, phân tích bằng AI (GPT-4o), lưu trữ kết quả vào PostgreSQL, và cảnh báo qua n8n webhook (Telegram + Google Sheets).

---

## Luồng Dữ Liệu (Data Flow)

Dữ liệu đi qua **3 Giai đoạn (Phases)** độc lập, tách rời hoàn toàn:

```mermaid
flowchart LR
    subgraph Phase1["Giai đoạn 1: Thu thập"]
        YT["YouTube API"] --> SC["Scraper Worker"]
        SC --> RD["Redis\n(Khử trùng lặp)"]
        RD --> RQ["RabbitMQ\n(Hàng đợi)"]
    end

    subgraph Phase2["Giai đoạn 2: Phân tích"]
        RQ --> AI["AI Worker"]
        AI --> GPT["GPT-4o\n(GitHub Models)"]
        GPT --> PG["PostgreSQL\n(Lưu trữ)"]
    end

    subgraph Phase3["Giai đoạn 3: Cảnh báo"]
        PG --> WH["Webhook"]
        WH --> N8N["n8n"]
        N8N --> TG["Telegram"]
        N8N --> GS["Google Sheets"]
    end

    style Phase1 fill:#1e293b,stroke:#6366f1,color:#f8fafc
    style Phase2 fill:#1e293b,stroke:#ec4899,color:#f8fafc
    style Phase3 fill:#1e293b,stroke:#10b981,color:#f8fafc
```

---

## Chi Tiết Từng Giai Đoạn

### Giai đoạn 1: Producer (Scraper Worker)

```mermaid
sequenceDiagram
    participant API as YouTube API
    participant SW as Scraper Worker
    participant Redis as Redis Cache
    participant RMQ as RabbitMQ

    SW->>API: Gọi API tìm video (search/channel)
    API-->>SW: Trả về danh sách video
    SW->>API: Lấy comments cho từng video
    API-->>SW: Trả về batch comments
    
    loop Mỗi comment
        SW->>Redis: is_comment_seen(comment_id)?
        alt Chưa thấy
            Redis-->>SW: False
            SW->>Redis: mark_comment_seen(comment_id)
            SW->>RMQ: Publish comment (Durable)
        else Đã thấy
            Redis-->>SW: True
            Note over SW: Bỏ qua (Early Exit)
        end
    end
```

**Đặc điểm kỹ thuật:**
- **Smart Pagination**: Duyệt qua nhiều trang comment, dừng sớm khi gặp comment cũ
- **Early Exit**: Khi Redis báo comment đã xử lý → dừng lấy thêm trang mới
- **Token Rotation**: Tự động chuyển YouTube API key khi bị Rate Limit

### Giai đoạn 2: Consumer (AI Worker)

```mermaid
sequenceDiagram
    participant RMQ as RabbitMQ
    participant AW as AI Worker
    participant GPT as GPT-4o (GitHub Models)
    participant DB as PostgreSQL
    participant WH as n8n Webhook

    RMQ->>AW: Rút batch comments (Manual ACK)
    AW->>GPT: Gửi batch phân tích
    GPT-->>AW: Kết quả NLP (sentiment, toxicity, ...)
    AW->>DB: Lưu kết quả phân tích
    
    alt Comment độc hại / quan trọng
        AW->>WH: Bắn webhook cảnh báo
    end
    
    AW->>RMQ: ACK (Xác nhận đã xử lý xong)
```

**Đặc điểm kỹ thuật:**
- **Batch Processing**: Xử lý nhiều comment trong 1 lần gọi API, tiết kiệm chi phí
- **Manual ACK**: Chỉ xác nhận với RabbitMQ SAU KHI xử lý thành công → không mất dữ liệu
- **Token Rotation**: Xoay vòng GitHub tokens để tránh Rate Limit GPT-4o

### Giai đoạn 3: Automation (n8n)

- Nhận webhook từ AI Worker khi phát hiện comment quan trọng
- Ghi log chi tiết vào Google Sheets (lưu trữ lâu dài)
- Bắn cảnh báo khẩn cấp tới Telegram Bot

---

## Kiến Trúc Module

```mermaid
graph TB
    subgraph EntryPoints["Entry Points"]
        RA["run_api.py"]
        RS["run_scraper.py"]
        RAI["run_ai_worker.py"]
    end

    subgraph Core["core/"]
        AIA["ai_analyzer.py"]
        VS["video_sources.py"]
    end

    subgraph Workers["workers/"]
        SW["scraper_worker.py"]
        AW["ai_worker.py"]
    end

    subgraph API["api/"]
        APP["app.py"]
        RTS["routes.py"]
    end

    subgraph Integrations["integrations/"]
        YTC["youtube_client.py"]
        N8N["n8n_webhook.py"]
    end

    subgraph Database["database/"]
        CONN["connection.py"]
        QRY["queries.py"]
    end

    subgraph Utils["utils/"]
        CFG["config.py"]
        LOG["logger.py"]
        CACHE["cache.py"]
    end

    RA --> APP
    RS --> SW
    RAI --> AW

    SW --> VS --> YTC
    SW --> CACHE
    AW --> AIA
    AW --> N8N
    APP --> RTS --> QRY --> CONN
    
    AIA -.-> CFG
    SW -.-> CFG
    CONN -.-> CFG
    CACHE -.-> CFG

    style EntryPoints fill:#1e293b,stroke:#6366f1,color:#f8fafc
    style Core fill:#1e293b,stroke:#ec4899,color:#f8fafc
    style Workers fill:#1e293b,stroke:#f59e0b,color:#f8fafc
    style API fill:#1e293b,stroke:#10b981,color:#f8fafc
    style Integrations fill:#1e293b,stroke:#8b5cf6,color:#f8fafc
    style Database fill:#1e293b,stroke:#06b6d4,color:#f8fafc
    style Utils fill:#1e293b,stroke:#94a3b8,color:#f8fafc
```

---

## Công Nghệ Sử Dụng (Tech Stack)

| Thành phần | Công nghệ | Mục đích |
|---|---|---|
| Ngôn ngữ | Python 3.11+ | Logic chính toàn bộ hệ thống |
| Message Queue | RabbitMQ | Hàng đợi phân tán, đảm bảo không mất dữ liệu |
| Cache | Redis | Khử trùng lặp tốc độ cao (In-Memory) |
| Database | PostgreSQL 15 | Lưu trữ vĩnh viễn kết quả phân tích |
| AI/NLP | OpenAI GPT-4o (qua GitHub Models) | Phân tích sentiment, toxicity, sarcasm |
| Web Framework | FastAPI + Uvicorn | REST API và phục vụ giao diện Command Center |
| Automation | n8n | Workflow tự động: Google Sheets + Telegram |
| Dashboard | Metabase | BI dashboard trực quan |
| DevOps | Docker Compose | Orchestration toàn bộ hạ tầng |

---

## Quyết Định Thiết Kế Quan Trọng

### Tại sao dùng RabbitMQ thay vì gọi AI trực tiếp?
- **Kháng lỗi (Fault Tolerance)**: Nếu AI Worker crash, message vẫn an toàn trong queue
- **Mở rộng (Scalability)**: Có thể chạy nhiều AI Worker song song
- **Tách rời (Decoupling)**: Scraper và AI Worker hoạt động độc lập

### Tại sao dùng Redis cho Deduplication?
- **Tốc độ**: Kiểm tra trùng lặp chỉ mất ~1ms (In-Memory)
- **TTL tự động**: Comment tự hết hạn sau thời gian, giải phóng bộ nhớ
- **Atomic operations**: An toàn khi chạy nhiều Scraper Worker đồng thời

### Tại sao dùng GitHub Models thay vì OpenAI trực tiếp?
- **Miễn phí / Chi phí thấp**: GitHub Models có free tier
- **Token Rotation**: Dùng nhiều token xoay vòng để tránh Rate Limit
- **Cùng model**: Vẫn dùng GPT-4o, chất lượng tương đương

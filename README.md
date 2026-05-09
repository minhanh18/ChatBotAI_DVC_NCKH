# Trợ lý Thủ tục Hành chính Số

Chatbot AI hỗ trợ công dân tra cứu thủ tục hành chính, quy định pháp luật và dịch vụ công trực tuyến. Hệ thống sử dụng RAG (Retrieval-Augmented Generation) với Gemini AI, pgvector và web search song song.

> Xem **[ARCHITECTURE.md](ARCHITECTURE.md)** để hiểu luồng hoạt động và kiến trúc hệ thống.  
> Xem **[STRUCTURE.md](STRUCTURE.md)** để hiểu vai trò từng file trong dự án.

---

## 1. Đối tượng sử dụng

- **Công dân/người dùng cuối**: đặt câu hỏi bằng ngôn ngữ tự nhiên, gửi ảnh, dùng giọng nói, xem nguồn trích dẫn và link thủ tục.
- **Cán bộ/quản trị viên**: upload tài liệu, quản lý dataset, theo dõi hội thoại, xem phản hồi người dùng.
- **Nhóm phát triển/vận hành**: triển khai bằng Docker Compose, mở rộng RAG, tinh chỉnh prompt và luồng đánh giá.

---

## 2. Chức năng hệ thống

### 2.1 Người dùng cuối

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Chat văn bản | Hỏi đáp bằng ngôn ngữ tự nhiên tiếng Việt | ✅ |
| Gửi hình ảnh | Đính kèm ảnh, chatbot nhận dạng và phân tích | ✅ |
| Nhận dạng giọng nói | Ghi âm, chuyển speech-to-text | ✅ |
| Streaming real-time | Câu trả lời xuất hiện từng từ theo SSE | ✅ |
| Dừng phản hồi | Nút Stop khi câu trả lời đang được sinh | ✅ |
| Gửi lại câu hỏi | Nút Reload để hỏi lại câu trước | ✅ |
| Đánh giá câu trả lời | Like / Chưa đúng kèm lý do | ✅ |
| Nguồn tham khảo | Panel tài liệu nội bộ + nguồn web, click mở PDF | ✅ |
| Link dịch vụ công | Panel "Đường link thao tác / hồ sơ" khi có URL thực | ✅ |
| Căn cứ pháp lý | Hiển thị điều khoản liên quan cuối câu trả lời | ✅ |
| Click mở trang PDF | Số trang inline click được → mở modal PDF đúng trang | ✅ |
| Launcher kéo thả | Widget nổi có thể kéo, mở thu nhỏ/toàn màn hình | ✅ |
| Ghi nhớ phiên | Session cache lưu chunks + rolling summary trong 2 giờ | ✅ |

### 2.2 Xử lý AI & RAG

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| RAG-first | Mọi câu hỏi ưu tiên tìm trong tài liệu nội bộ trước | ✅ |
| HybridRetriever | Kết hợp vector search + lexical SQL + BM25 + RRF | ✅ |
| Agent Router | Tự phân loại câu hỏi → RAG / AI / AI+RAG | ✅ |
| Query rewriter | Chuẩn hóa query không dấu/viết tắt/sai chính tả | ✅ |
| Web search (Tavily) | Tìm kiếm song song nhiều query variants | ✅ |
| RAG fallback web | Tự động chuyển web khi tài liệu không đủ căn cứ | ✅ |
| Session memory cache | Lưu chunks, rolling summary, entities trong phiên | ✅ |
| Gemini Embedding | Model gemini-embedding-001, fallback tự động | ✅ |
| Xoay vòng API Key | Pool nhiều Gemini key, cooldown 65s khi hết quota | ✅ |

### 2.3 Quản trị Admin

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Đăng nhập admin | Xác thực bằng mật khẩu cấu hình | ✅ |
| Quản lý dataset | Tạo, xem, xoá dataset | ✅ |
| Upload tài liệu | Upload PDF/DOCX/TXT/MD/CSV/HTML, tự động index | ✅ |
| Lưu file Azure Blob | File upload lưu Azure khi deploy Render | ✅ |
| Re-index tài liệu | Chạy lại pipeline chunking + embedding | ✅ |
| Versioning tài liệu | Upload mới tự động deprecated phiên bản cũ | ✅ |
| Xem hội thoại | Duyệt lịch sử chat | ✅ |
| Dashboard thống kê | Biểu đồ lượt chat, mode RAG/AI, token | ✅ |
| Feedback logs | Xem đánh giá like/chưa đúng kèm lý do | ✅ |

### 2.4 Bảo mật

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Pseudonymisation session | session_key HMAC-SHA256 trước khi lưu DB | ✅ |
| Mask PII trong log | CCCD, SĐT, email tự động ẩn khi ghi UsageLog | ✅ |
| Không lưu IP | IP và user-agent không thu thập vào DB | ✅ |
| CORS strict | Chỉ cho phép origin được cấu hình | ✅ |

---

## 3. Kiến trúc hệ thống

```text
Người dùng
   |
   v
Frontend Nginx (React + static)
   |-- /                    Trang chủ DVC
   |-- /assistant           React Chat App (iframe embed)
   |-- /admin               Admin Dashboard
   |
   v
FastAPI Backend (:8000)
   |-- /api/chat/stream          SSE streaming chat
   |-- /api/chat/stream-image    Chat kèm ảnh
   |-- /api/documents/*          Quản lý dataset & tài liệu
   |-- /api/admin/*              Dashboard & thống kê
   |-- /health                   Health check
   |
   +---> PostgreSQL 16 + pgvector   Segments, embedding, hội thoại
   +---> Redis                      Broker Celery
   +---> Celery Worker              Index tài liệu bất đồng bộ
   +---> Azure Blob Storage         Lưu file upload khi deploy Render
   +---> Gemini API                 Sinh câu trả lời + embedding
   +---> Tavily API                 Web search bổ sung
```

---

## 4. Luồng hoạt động

### 4.1 Luồng chat

1. Người dùng mở launcher trên trang chủ.
2. `chatbot-inline.js` mở iframe `/assistant?embed=1`.
3. React `ChatWindow` gửi request đến `/api/chat/stream` hoặc `/api/chat/stream-image`.
4. Backend tạo/lấy conversation, lưu user message, tải lịch sử hội thoại.
5. `rag/query_rewriter.py` chuẩn hóa query → `agent/router.py` phân loại RAG/AI/AI+RAG.
6. `chat/engine.py` gọi retriever, lọc nguồn, gọi Gemini và stream từng token qua SSE.
7. Frontend tiêu thụ SSE, render markdown/citation, cho phép dừng hoặc phản hồi chất lượng.
8. Khi hoàn tất, backend lưu assistant message và UsageLog.

### 4.2 Luồng ingest tài liệu

1. Admin upload tài liệu vào dataset qua `DocumentsPanel`.
2. `api/documents.py` lưu file qua `utils/storage.py`:
   - **Khi có Azure**: lưu lên Azure Blob Storage.
   - **Local/Docker**: lưu vào `UPLOAD_DIR` trên disk.
3. Tạo bản ghi Document trong DB, đẩy task ingest chạy background.
4. `rag/ingestor.py`: extract → chunk → embed.
5. Segments + vectors lưu vào PostgreSQL/pgvector.
6. Document chuyển sang trạng thái `ready`.

---

## 5. Công nghệ sử dụng

| Lớp | Công nghệ |
|-----|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Trang chủ/launcher | HTML/CSS/Vanilla JS, iframe bridge, postMessage |
| Backend | FastAPI, Uvicorn, Pydantic Settings |
| Streaming | Server-Sent Events qua `StreamingResponse` |
| RAG | Chunking, pgvector cosine, BM25, Reciprocal Rank Fusion |
| Database | PostgreSQL 16 + pgvector, SQLAlchemy async |
| File storage | Azure Blob Storage (deploy) / local disk (dev) |
| Worker | Celery + Redis |
| LLM | Google Gemini (gemini-2.5-flash) |
| Embedding | gemini-embedding-001 |
| Web search | Tavily API |
| Deploy | Docker Compose, Nginx, Render.com |

---

## 6. Cài đặt local

### 6.1 Chuẩn bị

- Docker + Docker Compose
- Gemini API key tại [Google AI Studio](https://aistudio.google.com/app/apikey)
- Tavily API key (nếu bật web search)

### 6.2 Cấu hình

```bash
cp .env.example .env
# Chỉnh sửa .env với các giá trị thực
```

Các biến bắt buộc:

```env
GEMINI_API_KEY=...
SECRET_KEY=...        # openssl rand -base64 42
ADMIN_PASSWORD=...
```

### 6.3 Chạy

```bash
docker compose up --build
```

Sau khi chạy:
- Trang chủ: `http://localhost`
- Chat: `http://localhost/assistant`
- Admin: `http://localhost/admin`
- API docs: `http://localhost:8000/api/docs`

### 6.4 Dev frontend riêng

```bash
cd frontend
npm ci --legacy-peer-deps
npm run dev
```

### 6.5 Dev backend riêng

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

---

## 7. Deploy lên Render

### 7.1 Các service

| Service | Loại | Ghi chú |
|---------|------|---------|
| `chatbot-db` | PostgreSQL | Free tier |
| `chatbot-redis` | Key Value (Redis) | Free tier |
| `chatbot-backend` | Web Service (Docker) | rootDir: `backend/` |
| `chatbot-frontend` | Web Service (Docker) | rootDir: `frontend/` |

Dùng `render.yaml` để tạo tự động qua Render Blueprint.

### 7.2 Biến môi trường cần điền trên Render

| Biến | Nơi lấy |
|------|---------|
| `GEMINI_API_KEY` | Google AI Studio |
| `TAVILY_API_KEY` | Tavily dashboard |
| `ADMIN_PASSWORD` | Tự đặt |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Portal → Storage Account → Access keys |
| `AZURE_STORAGE_ACCOUNT_NAME` | Tên Storage Account (vd: `chatbotstorage123`) |

> `DATABASE_URL_RAW`, `REDIS_URL_RAW`, `SECRET_KEY`, `SESSION_HMAC_KEY` được Render tự inject.

### 7.3 Tạo Azure Storage Account

1. Vào [Azure Portal](https://portal.azure.com) → **Storage accounts** → **Create**.
2. Chọn Subscription, Resource group, đặt tên (vd: `chatbotuploads`).
3. Redundancy: LRS (rẻ nhất, đủ dùng).
4. Sau khi tạo → **Access keys** → Copy **Connection string**.
5. Điền vào `AZURE_STORAGE_CONNECTION_STRING` trên Render Dashboard.
6. Container `chatbot-uploads` sẽ tự tạo lần đầu upload.

---

## 8. Biến môi trường đầy đủ

| Biến | Ý nghĩa | Bắt buộc |
|------|---------|----------|
| `GEMINI_API_KEY` | Key chính gọi Gemini | ✅ |
| `GEMINI_API_KEYS` | Danh sách key phụ xoay vòng quota | |
| `TAVILY_API_KEY` | Key web search Tavily | |
| `DATABASE_URL_RAW` | URL database managed (Render/cloud) | ✅ deploy |
| `REDIS_URL_RAW` | URL Redis managed (Render/cloud) | ✅ deploy |
| `AZURE_STORAGE_CONNECTION_STRING` | Kết nối Azure Blob Storage | ✅ deploy |
| `AZURE_STORAGE_ACCOUNT_NAME` | Tên Storage Account | ✅ deploy |
| `AZURE_STORAGE_CONTAINER_NAME` | Tên container (mặc định: `chatbot-uploads`) | |
| `SECRET_KEY` | Khoá bảo mật app | ✅ |
| `SESSION_HMAC_KEY` | Khoá HMAC pseudonymise session | ✅ |
| `ADMIN_USERNAME` | Tài khoản admin | ✅ |
| `ADMIN_PASSWORD` | Mật khẩu admin | ✅ |
| `GEMINI_MODEL` | Model Gemini (mặc định: `gemini-2.5-flash`) | |
| `GEMINI_MAX_OUTPUT_TOKENS` | Giới hạn output token (mặc định: 4096) | |
| `EMBEDDING_MODEL` | Model embedding (mặc định: `gemini-embedding-001`) | |
| `CHUNK_SIZE` | Kích thước chunk (mặc định: 800) | |
| `CHUNK_OVERLAP` | Overlap chunk (mặc định: 120) | |
| `RETRIEVAL_TOP_K` | Số chunk retrieve (mặc định: 5) | |
| `RETRIEVAL_SCORE_THRESHOLD` | Ngưỡng score (mặc định: 0.30) | |
| `ENABLE_WEB_SEARCH` | Bật/tắt web search | |
| `CORS_ORIGINS` | Danh sách origin cho phép | ✅ |

---

## 9. Ghi chú vận hành

- Không commit `.env`, file upload thật hoặc dữ liệu nhạy cảm.
- Azure Blob Storage là storage persistent duy nhất khi deploy Render (file system Render là ephemeral).
- Khi thay đổi schema database, dùng Alembic migration.
- Khi thay đổi frontend static/launcher, tăng query version của `chatbot-inline.js` để tránh cache.

---

## 10. Kiểm tra nhanh sau deploy

- `GET /api/health` → `{"status": "ok"}`.
- Trang chủ load icon launcher ở góc dưới phải.
- Gửi câu hỏi text và ảnh đều stream được.
- Admin upload tài liệu → trạng thái chuyển sang `ready` (file lưu Azure Blob).
- Citation trong câu trả lời mở đúng tài liệu/trang.
- Feedback "Chưa đúng" hiển thị đúng trong Admin → Feedback logs.

---

## 11. Tài liệu tham khảo

- FastAPI: https://fastapi.tiangolo.com/
- pgvector: https://github.com/pgvector/pgvector
- Gemini API: https://ai.google.dev/gemini-api/docs
- Azure Blob Storage Python SDK: https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python
- Celery: https://docs.celeryq.dev/en/stable/
- Docker Compose: https://docs.docker.com/compose/
- Render Blueprint: https://render.com/docs/blueprint-spec

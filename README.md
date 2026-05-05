# Trợ lý Thủ tục Hành chính Số

Chatbot AI hỗ trợ công dân tra cứu thủ tục hành chính, quy định pháp luật và dịch vụ công trực tuyến.

> Xem **[ARCHITECTURE.md](ARCHITECTURE.md)** để hiểu luồng hoạt động và kiến trúc hệ thống.  
> Xem **[STRUCTURE.md](STRUCTURE.md)** để hiểu vai trò từng file trong dự án.

---

## 1. Đối tượng sử dụng

- **Công dân/người dùng cuối**: đặt câu hỏi bằng ngôn ngữ tự nhiên, gửi ảnh, dùng giọng nói, xem nguồn trích dẫn và link thủ tục.
- **Cán bộ/quản trị viên**: upload tài liệu, quản lý dataset, theo dõi hội thoại, xem phản hồi người dùng.
- **Nhóm phát triển/vận hành**: triển khai hệ thống bằng Docker Compose, mở rộng RAG, tinh chỉnh prompt, embedding và luồng đánh giá nguồn.

---

## 2. Bảng chức năng đầy đủ

### 2.1 Người dùng cuối (Chat Interface)

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Chat văn bản | Hỏi đáp bằng ngôn ngữ tự nhiên tiếng Việt | ✅ |
| Gửi hình ảnh | Đính kèm ảnh, chatbot nhận dạng và phân tích | ✅ |
| Nhận dạng giọng nói | Ghi âm, chuyển speech-to-text | ✅ |
| Streaming real-time | Câu trả lời xuất hiện từng từ theo SSE | ✅ |
| Dừng phản hồi | Nút Stop khi câu trả lời đang được sinh | ✅ |
| Gửi lại câu hỏi | Nút Reload để hỏi lại câu trước | ✅ |
| Đánh giá câu trả lời | Like / Dislike kèm lý do | ✅ |
| Nguồn tham khảo | Panel hiển thị tài liệu nội bộ + nguồn web (click mở PDF) | ✅ |
| Link dịch vụ công | Panel "Đường link thao tác / hồ sơ" khi có URL thực | ✅ |
| Căn cứ pháp lý | Hiển thị điều khoản liên quan ở gần cuối câu trả lời | ✅ |
| Trích dẫn blockquote | Trích nguyên văn điều khoản pháp luật đúng quy tắc | ✅ |
| Click mở trang PDF | Số trang inline click được → mở modal PDF đúng trang | ✅ |
| Launcher kéo thả | Widget chatbot nổi có thể kéo, mở thu nhỏ/toàn màn hình | ✅ |
| Ghi nhớ phiên | Session cache lưu chunks + rolling summary trong 2 giờ | ✅ |
| Hỏi tiếp theo ngữ cảnh | Câu hỏi follow-up sử dụng chunks đã cache, giảm token | ✅ |

### 2.2 Xử lý AI & RAG

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| RAG-first | Mọi câu hỏi ưu tiên tìm trong tài liệu nội bộ trước | ✅ |
| HybridRetriever v3 | Kết hợp vector search + lexical SQL + BM25 + RRF | ✅ |
| Agent Router | Tự phân loại câu hỏi → RAG / AI / AI+RAG | ✅ |
| Web search (Tavily) | Tìm kiếm song song nhiều query variants | ✅ |
| RAG fallback web | Tự động chuyển web khi tài liệu không đủ căn cứ | ✅ |
| Parallel fetch | Web search + page fetch chạy đồng thời, không tuần tự | ✅ |
| Session memory cache | Lưu chunks, rolling summary, entities trong phiên | ✅ |
| Gemini Embedding | Fallback tự động sang gemini-embedding-001 | ✅ |
| Trích dẫn số trang | Inline `(trang X)` click được khi có document_id | ✅ |
| Đánh số nguồn đúng | Tài liệu nội bộ [1], web [2]+ theo đúng thứ tự | ✅ |
| Chuẩn hoá post-process | Dọn link thừa, fix blockquote, loại bỏ lặp lại | ✅ |

### 2.3 Quản trị (Admin Dashboard)

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Đăng nhập admin | Xác thực bằng mật khẩu cấu hình | ✅ |
| Quản lý dataset | Tạo, xem, xoá dataset | ✅ |
| Upload tài liệu | Upload PDF/DOCX, tự động index bằng Celery | ✅ |
| Re-index tài liệu | Chạy lại pipeline chunking + embedding | ✅ |
| Xem hội thoại | Duyệt lịch sử chat của người dùng | ✅ |
| Dashboard thống kê | Biểu đồ lượt chat, phân bổ mode RAG/AI, token | ✅ |
| Xem log hệ thống | Theo dõi usage logs, latency, số chunks | ✅ |
| Feedback logs | Xem đánh giá like/dislike kèm lý do | ✅ |

### 2.4 Bảo mật & An ninh mạng

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Pseudonymisation session | session_key được HMAC-SHA256 trước khi lưu DB | ✅ |
| Mask PII trong log | CCCD, SĐT, email tự động được ẩn khi ghi UsageLog | ✅ |
| Không lưu IP/user-agent | IP và user-agent không được thu thập vào DB | ✅ |
| SESSION_HMAC_KEY | Khoá HMAC cấu hình riêng, tách khỏi SECRET_KEY | ✅ |
| CORS strict | Chỉ cho phép origin được cấu hình | ✅ |

### 2.5 Hạ tầng & Vận hành

| Chức năng | Mô tả | Trạng thái |
|-----------|-------|------------|
| Docker Compose | Toàn bộ stack chạy 1 lệnh | ✅ |
| Nginx reverse proxy | Frontend + backend qua cùng port | ✅ |
| PostgreSQL + pgvector | Lưu trữ chunks + vector embedding | ✅ |
| Celery worker | Index tài liệu bất đồng bộ | ✅ |
| Redis | Broker cho Celery | ✅ |
| Alembic migration | Quản lý schema DB | ✅ |
| Health check | `/api/health` cho load balancer | ✅ |
| GC session cache | Dọn session hết TTL tự động | ✅ |

---

## 3. Kiến trúc hệ thống

```text
Người dùng
   |
   v
Frontend Nginx
   |-- Trang chủ public: /index.html
   |-- Launcher chatbot: /static/chatbot-inline.js
   |-- React app: /assistant, /admin, /app
   |
   v
FastAPI Backend
   |-- /api/chat/stream          SSE streaming chat
   |-- /api/chat/stream-image    chat kèm ảnh
   |-- /api/documents/*          quản lý tài liệu/dataset
   |-- /api/admin/*              dashboard/admin
   |
   +--> PostgreSQL + pgvector    lưu tài liệu, segment, embedding, hội thoại
   +--> Redis                    broker/cache
   +--> Celery Worker            index tài liệu bất đồng bộ
   +--> Gemini API               sinh câu trả lời + embedding
   +--> Tavily/Web Search        bổ sung nguồn web khi cần
```

---

## 4. Luồng hoạt động

### 4.1. Luồng chat
1. Người dùng mở launcher trên trang chủ.
2. `chatbot-inline.js` mở iframe `/assistant?embed=1`.
3. React `ChatWindow` gửi request đến `/api/chat/stream` hoặc `/api/chat/stream-image`.
4. Backend tạo/lấy conversation, lưu user message, tải lịch sử hội thoại.
5. Agent router quyết định dùng RAG, AI trực tiếp hoặc web search bổ sung.
6. Chat engine gọi retriever, lọc nguồn, gọi Gemini và stream từng token qua SSE.
7. Frontend tiêu thụ SSE, render markdown/citation, cho phép dừng hoặc phản hồi chất lượng.
8. Khi hoàn tất, backend lưu assistant message và frontend reload messages/conversation.

### 4.2. Luồng ingest tài liệu
1. Admin upload tài liệu vào dataset.
2. Backend lưu file, tạo bản ghi Document.
3. Celery worker chạy `ingest_document_task`.
4. Ingestor trích xuất nội dung, chunk tài liệu, tạo metadata.
5. Embedder sinh vector embedding.
6. Segment + vector được lưu vào PostgreSQL/pgvector.
7. Tài liệu chuyển sang trạng thái `ready` để retriever sử dụng.

---

## 5. Công nghệ sử dụng

| Lớp | Công nghệ |
|---|---|
| Frontend app | React 18, TypeScript, Vite, Tailwind CSS |
| Trang chủ/launcher | HTML/CSS/Vanilla JS, iframe bridge, postMessage |
| Backend | FastAPI, Uvicorn, Pydantic Settings |
| Streaming | Server-Sent Events qua `StreamingResponse` |
| RAG | Chunking, embedding, pgvector, BM25, Reciprocal Rank Fusion |
| Database | PostgreSQL 16 + pgvector, SQLAlchemy async |
| Worker | Celery + Redis |
| LLM | Google Gemini API |
| Deploy | Docker Compose, Nginx reverse proxy/static serving |

---

## 6. Các lỗi đã sửa trong bản này

### 6.1. Lỗi `ReferenceError: Cannot access 'Gt' before initialization`
- Nguyên nhân: `handleReload` dùng `submitMessage` trong dependency array trước khi `submitMessage` được khởi tạo. Khi Vite/Rollup build/minify, `submitMessage` bị đổi tên thành một biến ngắn như `Gt`, dẫn đến lỗi TDZ.
- Cách sửa:
  - Di chuyển `handleReload` xuống sau khi `submitMessage` đã được khai báo.
  - Di chuyển state `streamPhase` lên đầu component trước các callback dùng `setStreamPhase`.
  - Thêm comment kỹ thuật để tránh tái phạm khi refactor.

### 6.2. Responsive khung chat
- Chuyển chiều cao app từ `100vh` sang `100dvh` để phù hợp trình duyệt mobile có thanh địa chỉ động.
- Thêm `min-h-0`, `overflow: hidden` cho app iframe để tránh tràn/lấp màn hình.
- Cập nhật JS launcher để tính kích thước panel theo viewport hiện tại.
- Đặt ngưỡng header clearance riêng cho desktop/mobile, giúp khung chat thu nhỏ không bị khuất header.

### 6.3. Launcher icon và hướng hover
- Icon chat khi chưa hover được căn giữa trong button tròn nền trắng.
- Khi hover/focus, button giữ mép phải và mở rộng sang trái.
- Text `Trợ lý hỗ trợ` nằm trong popup, bỏ border/nền riêng để hòa vào nền button.
- Giữ behavior kéo thả và tự căn panel theo mép phải FAB.

---

## 7. Cấu trúc thư mục

```text
chatbot_dvc/
├── backend/
│   ├── app/
│   │   ├── api/          # Chat, documents, admin APIs
│   │   ├── agent/        # Router quyết định mode trả lời
│   │   ├── chat/         # Chat engine, evaluator, session cache
│   │   ├── models/       # SQLAlchemy models
│   │   ├── rag/          # Chunker, embedder, retriever, ingestor
│   │   ├── tasks/        # Celery tasks
│   │   └── web/          # Live web search
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html        # Trang chủ public
│   ├── chatbot-inline.js # Launcher/iframe bridge
│   ├── src/              # React app
│   ├── assets/           # Icon, ảnh
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 8. Cài đặt và chạy local

### 8.1. Chuẩn bị
- Docker + Docker Compose.
- API key Gemini tại Google AI Studio.
- Tavily API key nếu bật web search.

### 8.2. Cấu hình môi trường

```bash
cp .env.example .env
```

Cập nhật tối thiểu:

```env
GEMINI_API_KEY=...
SECRET_KEY=...
ADMIN_USERNAME=admin
ADMIN_PASSWORD=...
```

### 8.3. Chạy bằng Docker Compose

```bash
docker compose up --build
```

Sau khi chạy:
- Trang chủ: `http://localhost`
- Chat iframe/app: `http://localhost/assistant`
- Admin: `http://localhost/admin`
- API docs: `http://localhost:8000/api/docs`

### 8.4. Chạy frontend riêng khi phát triển

```bash
cd frontend
npm ci --legacy-peer-deps
npm run dev
```

### 8.5. Chạy backend riêng khi phát triển

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

---

## 9. Biến môi trường quan trọng

| Biến | Ý nghĩa |
|---|---|
| `GEMINI_API_KEY` | Key chính gọi Gemini |
| `GEMINI_API_KEYS` | Danh sách key phụ để xoay vòng quota |
| `TAVILY_API_KEY` | Key web search |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Kết nối PostgreSQL |
| `DATABASE_URL_RAW` | URL database managed/deploy |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | Kết nối Redis |
| `CHUNK_SIZE`, `CHUNK_OVERLAP` | Cấu hình chunk tài liệu |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_SCORE_THRESHOLD` | Cấu hình retrieval |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Tài khoản admin |
| `ENABLE_WEB_SEARCH` | Bật/tắt web search bổ sung |

---

## 10. Ghi chú vận hành

- Không commit `.env`, file upload thật hoặc dữ liệu nhạy cảm.
- Với production, thay `SECRET_KEY`, `ADMIN_PASSWORD`, cấu hình HTTPS và domain CORS cụ thể.
- Nên cấu hình volume backup cho PostgreSQL và thư mục upload.
- Khi thay đổi frontend static/launcher, tăng query version của `/static/chatbot-inline.js` để tránh cache trình duyệt.
- Khi thay đổi schema database, dùng Alembic migration thay vì sửa bảng thủ công.

---

## 11. Kiểm tra nhanh sau khi deploy

- `GET /api/health` trả `status: ok`.
- Trang chủ load icon launcher ở góc dưới phải.
- Hover launcher mở sang trái, icon vẫn căn giữa khi chưa hover.
- Mở khung chat trên desktop không che header.
- Resize/mobile: panel nằm trong viewport, không bị lấp nửa màn hình.
- Gửi câu hỏi text và ảnh đều stream được.
- Admin upload tài liệu, worker chuyển trạng thái tài liệu sang `ready`.
- Citation trong câu trả lời mở đúng tài liệu/trang.

---

## 12. Tài liệu tham khảo

- FastAPI: https://fastapi.tiangolo.com/
- React Hooks: https://react.dev/reference/react
- Vite production build: https://vite.dev/guide/build
- MDN Server-Sent Events: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- MDN CSS viewport units: https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/length
- pgvector: https://github.com/pgvector/pgvector
- SQLAlchemy async: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Celery: https://docs.celeryq.dev/en/stable/
- Docker Compose: https://docs.docker.com/compose/
- Gemini API: https://ai.google.dev/gemini-api/docs
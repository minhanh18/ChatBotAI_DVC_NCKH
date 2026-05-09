# Cấu trúc thư mục dự án

```
chatbot_dvc/
├── .env                          # Biến môi trường local (KHÔNG commit)
├── .env.example                  # Mẫu biến môi trường (commit được)
├── .gitignore                    # File/thư mục không commit
├── docker-compose.yml            # Khởi động toàn bộ stack local
├── render.yaml                   # Cấu hình deploy lên Render.com (Blueprint)
├── README.md                     # Mô tả dự án, hướng dẫn cài đặt & deploy
├── ARCHITECTURE.md               # Kiến trúc hệ thống & luồng hoạt động
├── STRUCTURE.md                  # File này — cấu trúc thư mục
│
├── frontend/                     # Ứng dụng React (chat UI)
│   ├── Dockerfile                # Build image frontend + Nginx
│   ├── nginx.frontend.conf       # Config Nginx serve static + proxy /api
│   ├── nginx.frontend.conf.template  # Template Nginx cho Render/env vars
│   ├── index.html                # Entry HTML cho trang chủ DVC mô phỏng
│   ├── app.html                  # Entry HTML cho chat app React
│   ├── chatbot-inline.js         # Script nhúng chatbot dạng iframe launcher
│   ├── package.json              # Dependencies Node.js
│   ├── vite.config.ts            # Cấu hình Vite build
│   ├── tsconfig.json             # Cấu hình TypeScript
│   ├── tailwind.config.js        # Cấu hình TailwindCSS
│   │
│   ├── assets/                   # Static assets cho trang chủ DVC
│   │   ├── css/style.css         # CSS trang chủ
│   │   ├── icons/                # Icons (play, pause, mic...)
│   │   └── img/                  # Hình ảnh danh mục thủ tục
│   │
│   └── src/                      # Source code React
│       ├── main.tsx              # Entry point React app
│       ├── index.css             # Global CSS
│       ├── App.tsx               # Root component, routing, theme
│       │
│       ├── api/
│       │   └── client.ts         # API client: SSE streaming, fetch helpers,
│       │                         # Citation/Message types, buildDocumentPageUrl
│       │
│       └── components/
│           ├── ChatWindow.tsx    # Container chính: quản lý conversation,
│           │                     # streaming state, upload ảnh, voice input
│           ├── MessageBubble.tsx # Render từng message: markdown, citations,
│           │                     # linkifyInlinePageRefs, CitationsPanel,
│           │                     # ServiceLinksPanel, feedback buttons (Like/Chưa đúng)
│           ├── AdminDashboard.tsx# Màn hình giám sát: stats, logs, hội thoại,
│           │                     # feedback (hiển thị "Hữu ích"/"Chưa đúng"), reset
│           ├── DocumentsPanel.tsx# Quản lý dataset & tài liệu: upload, delete,
│           │                     # trạng thái index, re-index, versioning
│           └── PdfViewerModal.tsx# Modal xem PDF với iframe + page navigation
│
└── backend/                      # FastAPI Python backend
    ├── Dockerfile                # Build image backend Python
    ├── requirements.txt          # Python dependencies (bao gồm azure-storage-blob)
    ├── alembic.ini               # Cấu hình Alembic migrations
    ├── script.py.mako            # Template migration script
    │
    ├── versions/                 # Alembic migration files
    │   └── *.py                  # Từng migration DB schema
    │
    └── app/                      # Application code
        ├── main.py               # FastAPI app init, lifespan, CORS,
        │                         # router registration, DB startup check
        ├── config.py             # Pydantic Settings: đọc env vars,
        │                         # GEMINI_API_KEY, TAVILY_API_KEY,
        │                         # AZURE_STORAGE_* settings,
        │                         # USE_AZURE_STORAGE property
        │
        ├── models/
        │   └── db.py             # SQLAlchemy models: Conversation, Message,
        │                         # Document (file_content backup column),
        │                         # DocumentSegment, UsageLog,
        │                         # MessageFeedback, Dataset
        │
        ├── api/
        │   ├── chat.py           # Chat endpoints: POST /stream, /stream-image,
        │   │                     # GET conversations, POST feedback, DELETE conv.
        │   │                     # Orchestrate: routing → engine → SSE response.
        │   │                     # out-of-domain check, clarification check.
        │   ├── documents.py      # Document endpoints: upload, list, delete,
        │   │                     # dataset CRUD, serve file, re-index trigger,
        │   │                     # backfill-file-content admin endpoint.
        │   │                     # Dùng utils/storage.py cho save/delete/exists file.
        │   └── admin.py          # Admin endpoints: dashboard stats, usage logs,
        │                         # conversation list, feedback logs, reset
        │
        ├── agent/
        │   └── router.py         # Agent router: gọi HybridRetriever, chạy
        │                         # assess_retrieval(), quyết định RAG/AI/AI_RAG
        │
        ├── chat/
        │   ├── engine.py         # Core sinh câu trả lời (file lớn nhất):
        │   │                     # - _GeminiKeyPool: xoay vòng key, cooldown 65s
        │   │                     # - ChatEngine.stream_response(): luồng chính
        │   │                     # - _generate_rag_answer(): RAG path
        │   │                     # - _stream_ai(): AI+Web path
        │   │                     # - _build_rag_context/prompt(): xây prompt
        │   │                     # - _clean_response_text(): pipeline dọn output
        │   │                     # - _normalize_legal_answer_structure()
        │   ├── evaluator.py      # Đánh giá chất lượng RAG:
        │   │                     # - assess_retrieval(): scoring chunks
        │   │                     # - is_legal_query/is_procedure_query()
        │   │                     # - is_out_of_domain(): từ chối ngoài lĩnh vực
        │   │                     # - needs_freshness_check()
        │   ├── legal_enricher.py # Post-processing pháp lý:
        │   │                     # - extract_service_links(): trích link DVC
        │   │                     # - enrich(): căn cứ pháp lý, source refs
        │   └── session_cache.py  # In-memory session cache (TTL 2h):
        │                         # - get_cached_chunks(): overlap lookup
        │                         # - cache_chunks(): lưu chunks mới
        │                         # - maybe_update_summary(): rolling summary
        │                         # - GC tự động khi expired
        │
        ├── rag/
        │   ├── query_rewriter.py # Chuẩn hóa query trước RAG (LLM-based):
        │   │                     # - rewrite_query(): không dấu/viết tắt → chuẩn
        │   │                     # - _needs_rewrite(): bỏ qua nếu đã đủ dấu
        │   │                     # - in-process cache 512 entries, timeout 3s
        │   ├── retriever.py      # HybridRetriever:
        │   │                     # - vector search (pgvector cosine)
        │   │                     # - lexical search (PostgreSQL tsvector)
        │   │                     # - BM25 reranking + RRF merge
        │   ├── embedder.py       # Gemini embedding:
        │   │                     # - embed_query/embed_texts()
        │   │                     # - model: gemini-embedding-001 (dim 768)
        │   │                     # - fallback tự động nếu model không khả dụng
        │   ├── chunker.py        # LegalAwareChunker:
        │   │                     # - kích thước ~800 chars, overlap 120
        │   │                     # - smart split tại ranh giới câu/đoạn/điều khoản
        │   ├── extractor.py      # Trích text từ PDF/DOCX/TXT/MD/CSV/HTML:
        │   │                     # - tự nhận dạng định dạng qua extension
        │   │                     # - metadata: số trang với [[PAGE:N]] markers
        │   ├── ingestor.py       # Orchestrate toàn bộ pipeline index:
        │   │                     # - extract → chunk → embed → store
        │   │                     # - Khôi phục file từ Azure Blob hoặc DB bytes
        │   │                     # - timeout cứng 600s, không block event loop
        │   ├── lifecycle.py      # Quản lý vòng đời tài liệu:
        │   │                     # - versioning, lifecycle_status, merge_meta
        │   │                     # - compute_file_hash(), normalize_document_name()
        │   ├── legal_metadata.py # Trích metadata pháp lý từ text:
        │   │                     # - số hiệu, ngày ban hành, loại văn bản
        │   └── source_hints.py   # Map tên tài liệu → URL nguồn chính thức
        │
        ├── web/
        │   └── live_search.py    # Web search engine:
        │                         # - build_search_queries(): tạo query variants
        │                         # - tavily_search(): gọi Tavily API
        │                         # - fetch_page_context(): scrape nội dung trang
        │                         # - score_web_result(): ranking độ tươi + relevance
        │                         # - maybe_fetch_web_context(): entry point
        │
        ├── tasks/
        │   └── ingest.py         # Celery tasks:
        │                         # - ingest_document_task(): async index tài liệu
        │                         # - celery_app config kết nối Redis
        │
        └── utils/
            ├── storage.py        # Storage Abstraction Layer:
            │                     # - save_file(): Azure Blob hoặc local disk
            │                     # - load_file(): đọc từ Azure hoặc disk
            │                     # - file_exists(): kiểm tra tồn tại
            │                     # - delete_file(): xoá file
            │                     # - restore_to_local(): tải blob về disk tạm
            │                     # - USE_AZURE_STORAGE từ config.USE_AZURE_STORAGE
            └── data_crypto.py    # Mã hoá dữ liệu người dùng:
                                  # - pseudonymise_session_key(): HMAC-SHA256
                                  # - mask_pii(): ẩn CCCD/SĐT/email trong logs
                                  # - safe_log_query()
```

---

## Các file cấu hình quan trọng

| File | Mục đích |
|------|---------|
| `.env` | API keys, DB URL, Redis URL, Azure keys — **KHÔNG commit** |
| `.env.example` | Mẫu `.env` để tham khảo |
| `docker-compose.yml` | Định nghĩa services: backend, frontend, postgres, redis |
| `render.yaml` | Blueprint deploy Render.com: web + DB + Redis, Azure env vars |
| `backend/requirements.txt` | Python packages (bao gồm `azure-storage-blob==12.23.1`) |
| `frontend/package.json` | Node packages |

---

## Các biến môi trường cần bổ sung khi deploy Render

| Biến | Render field | Ghi chú |
|------|-------------|---------|
| `AZURE_STORAGE_CONNECTION_STRING` | `sync: false` | Điền thủ công trên Render Dashboard |
| `AZURE_STORAGE_ACCOUNT_NAME` | `sync: false` | Tên Storage Account (vd: `chatbotuploads`) |
| `AZURE_STORAGE_ACCOUNT_KEY` | `sync: false` | Access Key (chỉ nếu không dùng connection string) |
| `AZURE_STORAGE_CONTAINER_NAME` | `value: chatbot-uploads` | Tên container, đã có default trong render.yaml |
| `GEMINI_API_KEY` | `sync: false` | Bắt buộc |
| `ADMIN_PASSWORD` | `sync: false` | Bắt buộc |

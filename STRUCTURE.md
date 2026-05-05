# Cấu trúc thư mục dự án

```
chatbot_dvc/
├── .env                          # Biến môi trường local (KHÔNG commit)
├── .env.example                  # Mẫu biến môi trường (commit được)
├── .gitignore                    # Danh sách file/thư mục không commit
├── docker-compose.yml            # Khởi động toàn bộ stack local
├── render.yaml                   # Cấu hình deploy lên Render.com
├── README.md                     # Mô tả dự án, hướng dẫn cài đặt
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
│           │                     # ServiceLinksPanel, feedback buttons
│           ├── AdminDashboard.tsx# Màn hình giám sát: stats, logs, hội thoại,
│           │                     # feedback, reset monitoring
│           ├── DocumentsPanel.tsx# Quản lý dataset & tài liệu: upload, delete,
│           │                     # trạng thái index, re-index
│           └── PdfViewerModal.tsx# Modal xem PDF với iframe + page navigation
│
└── backend/                      # FastAPI Python backend
    ├── Dockerfile                # Build image backend Python
    ├── requirements.txt          # Python dependencies
    ├── alembic.ini               # Cấu hình Alembic migrations
    ├── script.py.mako            # Template migration script
    │
    ├── versions/                 # Alembic migration files
    │   └── *.py                  # Từng migration DB
    │
    └── app/                      # Application code
        ├── main.py               # FastAPI app init, lifespan, CORS,
        │                         # router registration, DB startup check
        ├── config.py             # Pydantic Settings: đọc env vars,
        │                         # GEMINI_API_KEY, TAVILY_API_KEY,
        │                         # GEMINI_MAX_OUTPUT_TOKENS, etc.
        │
        ├── models/
        │   └── db.py             # SQLAlchemy models: Conversation, Message,
        │                         # Document, DocumentChunk, UsageLog,
        │                         # MessageFeedback, Dataset
        │
        ├── api/
        │   ├── chat.py           # Chat endpoints: POST /stream, /stream-image,
        │   │                     # GET conversations, POST feedback, DELETE conv.
        │   │                     # Orchestrate: routing → engine → SSE response.
        │   │                     # out-of-domain check, clarification check.
        │   ├── documents.py      # Document endpoints: upload, list, delete,
        │   │                     # dataset CRUD, serve file, re-index trigger
        │   └── admin.py          # Admin endpoints: dashboard stats, usage logs,
        │                         # conversation list, feedback logs, reset
        │
        ├── agent/
        │   └── router.py         # Agent router: gọi HybridRetriever, chạy
        │                         # assess_retrieval(), quyết định RAG/AI/AI_RAG
        │
        ├── chat/
        │   ├── engine.py         # Core sinh câu trả lời (file lớn nhất):
        │   │                     # - _GeminiKeyPool: xoay vòng key, cooldown
        │   │                     # - ChatEngine.stream_response(): luồng chính
        │   │                     # - _generate_rag_answer(): RAG path
        │   │                     # - _stream_ai(): AI+Web path; LLM sinh ([N]) internally, stripped trước UI
        │   │                     # - _gemini_stream_in_thread(): Gemini stream
        │   │                     # - _build_rag_context/prompt(): xây prompt
        │   │                     # - _clean_response_text(): pipeline dọn output
        │   │                     # - _strip_inline_source_links(): strip ([N]) web khỏi UI output; giữ ([N], trang X) RAG
        │   │                     # - _normalize_legal_answer_structure()
        │   │                     # - _should_fallback_to_web_after_rag()
        │   ├── evaluator.py      # Đánh giá chất lượng RAG:
        │   │                     # - assess_retrieval(): scoring chunks
        │   │                     # - is_legal_query/is_procedure_query()
        │   │                     # - is_out_of_domain(): từ chối ngoài lĩnh vực
        │   │                     # - needs_freshness_check()
        │   │                     # - build_safe_fallback_answer()
        │   ├── legal_enricher.py # Post-processing pháp lý:
        │   │                     # - extract_service_links(): trích link DVC
        │   │                     # - enrich(): căn cứ pháp lý, source refs
        │   │                     # - kiểm tra hiệu lực văn bản qua Gemini
        │   └── session_cache.py  # In-memory session cache:
        │                         # - get_cached_chunks(): overlap lookup
        │                         # - cache_chunks(): lưu chunks mới
        │                         # - maybe_update_summary(): rolling summary
        │                         # - TTL 2h, GC tự động
        │
        ├── rag/
        │   ├── retriever.py      # HybridRetriever v3:
        │   │                     # - vector search (pgvector cosine)
        │   │                     # - lexical search (PostgreSQL tsvector)
        │   │                     # - BM25 reranking + RRF merge
        │   ├── embedder.py       # Gemini embedding:
        │   │                     # - embed_query/embed_chunks()
        │   │                     # - fallback tự động sang gemini-embedding-001
        │   ├── chunker.py        # Chia tài liệu thành chunks:
        │   │                     # - kích thước ~800 chars, overlap 120
        │   │                     # - smart split tại ranh giới câu/đoạn
        │   ├── extractor.py      # Trích text từ PDF/DOCX:
        │   │                     # - metadata extraction (số trang, tiêu đề)
        │   ├── ingestor.py       # Orchestrate toàn bộ pipeline index:
        │   │                     # extract → chunk → embed → store
        │   ├── lifecycle.py      # Quản lý vòng đời tài liệu:
        │   │                     # - delete chunks, update status
        │   ├── legal_metadata.py # Trích metadata pháp lý từ text:
        │   │                     # - số hiệu, ngày ban hành, loại văn bản
        │   └── source_hints.py   # Map tên tài liệu → URL nguồn chính thức
        │
        ├── web/
        │   └── live_search.py    # Web search engine:
        │                         # - build_search_queries(): tạo query variants
        │                         # - tavily_search(): gọi Tavily API
        │                         # - fetch_page_context(): scrape nội dung trang
        │                         # - score_web_result(): ranking theo độ tươi + relevance
        │                         # - maybe_fetch_web_context(): entry point chính
        │                         #   Context format "[N] Title" → LLM sinh ([N]) per câu (stripped trước khi ra UI)
        │                         # - should_search_web(): quyết định có search không
        │                         # - extract_focus_content(): trích đoạn liên quan
        │
        ├── tasks/
        │   └── ingest.py         # Celery tasks:
        │                         # - ingest_document_task(): async index tài liệu
        │                         # - celery_app config kết nối Redis
        │
        └── utils/
            └── data_crypto.py    # Mã hoá dữ liệu người dùng:
                                  # - pseudonymise_session_key(): HMAC-SHA256
                                  # - mask_pii(): ẩn CCCD/SĐT/email trong logs
                                  # - safe_log_query()
```

---

## Các file cấu hình quan trọng

| File | Mục đích |
|------|---------|
| `.env` | API keys, DB URL, Redis URL — **KHÔNG commit** |
| `.env.example` | Mẫu `.env` để tham khảo |
| `docker-compose.yml` | Định nghĩa services: backend, frontend, postgres, redis, worker |
| `render.yaml` | Blueprint deploy Render.com: web + worker + DB + Redis |
| `backend/requirements.txt` | Python packages |
| `frontend/package.json` | Node packages |

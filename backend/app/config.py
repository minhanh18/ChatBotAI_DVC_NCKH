"""
=============================================================
  CHATBOT CONFIGURATION — Chỉnh sửa tại đây, không qua UI
=============================================================
"""

import json
from typing import Any, Tuple, Type

from pydantic import field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource


class SafeEnvSettingsSource(EnvSettingsSource):
    """Tránh crash khi field list[str] nhận chuỗi rỗng hoặc comma-separated từ Render.
    pydantic-settings v2 gọi json.loads() trước field_validator nên cần xử lý tại đây:
    - Chuỗi rỗng  → trả None để pydantic dùng default
    - JSON hợp lệ → parse bình thường
    - comma-separated (key1,key2) → split thành list
    """
    def decode_complex_value(self, field_name: str, field_info: Any, value: Any) -> Any:
        if not isinstance(value, str):
            return super().decode_complex_value(field_name, field_info, value)
        v = value.strip()
        if not v:
            return None  # dùng default của field
        try:
            return json.loads(v)  # JSON hợp lệ: [...] hoặc {...}
        except (json.JSONDecodeError, ValueError):
            # Fallback: comma-separated  key1,key2,key3
            parts = [item.strip() for item in v.split(",") if item.strip()]
            return parts if parts else None


class Settings(BaseSettings):
    # ── App ────────────────────────────────────────────────
    APP_NAME: str = "InternalChatbot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-base64-42"
    # Khoá HMAC dùng để pseudonymise session_key trước khi lưu DB (Luật ANM 2018 / NĐ 13/2023)
    # Tạo bằng: python -c "import secrets; print(secrets.token_hex(32))"
    SESSION_HMAC_KEY: str = ""

    # ── LLM: Gemini Flash 2.5 (hardcoded, không đổi trên UI) ─
    GEMINI_API_KEY: str = ""
    GEMINI_API_KEYS: list[str] = []   # Danh sách key phụ để xoay vòng khi quota exhausted
    TAVILY_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TEMPERATURE: float = 0.3
    GEMINI_MAX_OUTPUT_TOKENS: int = 16384
    GEMINI_TOP_P: float = 0.8

    # ── Embedding ───────────────────────────────────────────
    # Ưu tiên lại model cũ để giữ chất lượng retrieve gần v8.
    # Nếu model cũ không còn khả dụng, embedder sẽ tự fallback sang model mới.
    EMBEDDING_MODEL: str = "text-embedding-004"
    EMBEDDING_FALLBACK_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIMENSION: int = 768

    # ── RAG / Chunking ─────────────────────────────────────
    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 120
    CHUNK_SEPARATORS: list[str] = ["\n\n", "\n", "。", ". ", " ", ""]
    RETRIEVAL_TOP_K: int = 5
    RETRIEVAL_SCORE_THRESHOLD: float = 0.30

    # ── Agent routing ──────────────────────────────────────
    INTERNAL_DOC_KEYWORDS: list[str] = [
        "tài liệu", "nội bộ", "quy định", "quy trình", "hướng dẫn",
        "document", "policy", "procedure", "guideline", "internal",
        "theo quy định", "theo quy trình",
    ]
    CONVERSATION_HISTORY_LIMIT: int = 10

    # ── Database ───────────────────────────────────────────
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    DB_USER: str = "chatbot"
    DB_PASSWORD: str = "chatbot123"
    DB_NAME: str = "chatbot"
    DATABASE_URL_RAW: str = ""

    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_RAW:
            return self.DATABASE_URL_RAW.replace("postgresql://", "postgresql+asyncpg://", 1)
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def SYNC_DATABASE_URL(self) -> str:
        if self.DATABASE_URL_RAW:
            return self.DATABASE_URL_RAW
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # ── Redis ──────────────────────────────────────────────
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    REDIS_URL_RAW: str = ""

    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_URL_RAW:
            return self.REDIS_URL_RAW
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER_URL(self) -> str:
        return self.REDIS_URL

    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        return self.REDIS_URL

    # ── File upload ────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 100
    MAX_CHAT_IMAGE_SIZE_MB: int = 10
    ALLOWED_EXTENSIONS: list[str] = ["pdf", "txt", "md", "docx", "csv", "html"]
    UPLOAD_DIR: str = "/tmp/uploads"
    APP_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    # ── Live web search / crawl ───────────────────────────
    ENABLE_WEB_SEARCH: bool = True
    WEB_SEARCH_RESULTS_LIMIT: int = 5
    WEB_SEARCH_FETCH_PAGES: int = 3
    WEB_SEARCH_TIMEOUT_SEC: float = 12.0
    WEB_SEARCH_MAX_CONTEXT_CHARS: int = 1800

    # ── Admin ──────────────────────────────────────────────
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    # ── CORS ───────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost", "http://localhost:80", "http://localhost:3000", "https://chatbot-frontend-oymx.onrender.com"]

    # ── Validators: pydantic-settings v2 đọc list từ .env dưới dạng JSON string ──
    @field_validator(
        "CHUNK_SEPARATORS", "INTERNAL_DOC_KEYWORDS",
        "ALLOWED_EXTENSIONS", "CORS_ORIGINS", "GEMINI_API_KEYS",
        mode="before",
    )
    @classmethod
    def _parse_list(cls, v: Any) -> Any:
        """Cho phép đặt list trong .env dưới dạng JSON: '["a","b"]' hoặc giữ nguyên default list."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                # Thử parse comma-separated
                return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Dùng SafeEnvSettingsSource thay vì EnvSettingsSource mặc định
        # để tránh crash khi list field được set thành chuỗi rỗng
        return (init_settings, SafeEnvSettingsSource(settings_cls), dotenv_settings, file_secret_settings)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

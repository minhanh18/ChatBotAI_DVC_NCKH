"""
storage.py — Lớp trừu tượng hoá lưu trữ file.

Khi AZURE_STORAGE_CONNECTION_STRING được cấu hình → dùng Azure Blob Storage.
Khi không có → dùng local disk (UPLOAD_DIR) như cũ.

Không thay đổi logic upload/ingest hiện tại; chỉ thay thế các lời gọi
Path(...).write_bytes() / Path(...).read_bytes() / Path(...).exists() /
Path(...).unlink() bằng các hàm trong module này.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Lazy import Azure SDK (chỉ import khi thực sự cần) ──────────────────────
_blob_service_client = None


def _get_blob_client(blob_name: str):
    """Trả về BlobClient cho một blob cụ thể."""
    from app.config import settings
    global _blob_service_client

    if _blob_service_client is None:
        from azure.storage.blob import BlobServiceClient
        _blob_service_client = BlobServiceClient.from_connection_string(
            settings.AZURE_STORAGE_CONNECTION_STRING
        )
        # Tạo container nếu chưa tồn tại
        try:
            _blob_service_client.create_container(settings.AZURE_STORAGE_CONTAINER_NAME)
            logger.info("Đã tạo Azure Blob container: %s", settings.AZURE_STORAGE_CONTAINER_NAME)
        except Exception:
            pass  # Container đã tồn tại — bỏ qua

    from app.config import settings as _s
    return _blob_service_client.get_blob_client(
        container=_s.AZURE_STORAGE_CONTAINER_NAME,
        blob=blob_name,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def save_file(file_path: str | Path, content: bytes) -> None:
    """
    Lưu file:
    - Azure Blob nếu USE_AZURE_STORAGE=True
    - Local disk nếu không
    file_path vẫn được dùng làm blob_name để giữ tương thích với logic cũ.
    """
    from app.config import settings

    if settings.USE_AZURE_STORAGE:
        blob_name = _to_blob_name(file_path)
        blob_client = _get_blob_client(blob_name)
        blob_client.upload_blob(content, overwrite=True)
        logger.debug("Đã upload lên Azure Blob: %s", blob_name)
    else:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def load_file(file_path: str | Path) -> Optional[bytes]:
    """
    Đọc nội dung file.
    Trả về None nếu file không tồn tại.
    """
    from app.config import settings

    if settings.USE_AZURE_STORAGE:
        blob_name = _to_blob_name(file_path)
        try:
            blob_client = _get_blob_client(blob_name)
            return blob_client.download_blob().readall()
        except Exception as e:
            logger.warning("Không đọc được Azure Blob %s: %s", blob_name, e)
            return None
    else:
        path = Path(file_path)
        if path.exists():
            return path.read_bytes()
        return None


def file_exists(file_path: str | Path) -> bool:
    """Kiểm tra file có tồn tại không."""
    from app.config import settings

    if settings.USE_AZURE_STORAGE:
        blob_name = _to_blob_name(file_path)
        try:
            blob_client = _get_blob_client(blob_name)
            blob_client.get_blob_properties()
            return True
        except Exception:
            return False
    else:
        return Path(file_path).exists()


def delete_file(file_path: str | Path) -> None:
    """Xoá file. Không raise lỗi nếu file không tồn tại."""
    from app.config import settings

    if settings.USE_AZURE_STORAGE:
        blob_name = _to_blob_name(file_path)
        try:
            blob_client = _get_blob_client(blob_name)
            blob_client.delete_blob()
            logger.debug("Đã xoá Azure Blob: %s", blob_name)
        except Exception as e:
            logger.warning("Không xoá được Azure Blob %s: %s", blob_name, e)
    else:
        path = Path(file_path)
        if path.exists():
            path.unlink()


def restore_to_local(file_path: str | Path) -> bool:
    """
    Khi USE_AZURE_STORAGE=True: tải blob về disk tạm (UPLOAD_DIR) để ingestor
    đọc được qua đường dẫn thông thường. Trả về True nếu thành công.
    Khi USE_AZURE_STORAGE=False: không cần làm gì — trả về file_exists().
    """
    from app.config import settings

    if not settings.USE_AZURE_STORAGE:
        return Path(file_path).exists()

    content = load_file(file_path)
    if content is None:
        return False

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    logger.info("Đã khôi phục file từ Azure Blob về local: %s", file_path)
    return True


def get_blob_url(file_path: str | Path) -> Optional[str]:
    """
    Trả về URL công khai của blob (chỉ hữu ích nếu container được cấu hình public read).
    Trong hệ thống này, chỉ dùng để debug — file luôn được serve qua API backend.
    """
    from app.config import settings

    if not settings.USE_AZURE_STORAGE:
        return None

    blob_name = _to_blob_name(file_path)
    return (
        f"https://{settings.AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        f"/{settings.AZURE_STORAGE_CONTAINER_NAME}/{blob_name}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_blob_name(file_path: str | Path) -> str:
    """
    Chuyển đường dẫn local thành blob name.
    Ví dụ: /tmp/uploads/abc123.pdf  →  uploads/abc123.pdf
    Giữ đường dẫn tương đối để tránh conflict khi UPLOAD_DIR thay đổi.
    """
    from app.config import settings

    path_str = str(file_path)
    upload_dir = settings.UPLOAD_DIR.rstrip("/") + "/"
    if path_str.startswith(upload_dir):
        return "uploads/" + path_str[len(upload_dir):]
    # Fallback: lấy tên file
    return "uploads/" + Path(file_path).name

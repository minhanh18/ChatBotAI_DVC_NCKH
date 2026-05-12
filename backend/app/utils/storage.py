from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Lazy import Boto3 SDK (chỉ import khi thực sự cần) ──────────────────────
_s3_client = None

def _get_s3_client():
    """Trả về S3 Client kết nối đến Cloudflare R2."""
    from app.config import settings
    global _s3_client

    if _s3_client is None:
        import boto3
        from botocore.config import Config

        _s3_client = boto3.client(
            's3',
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name='auto'  # R2 tự động định tuyến
        )
    return _s3_client


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def save_file(file_path: str | Path, content: bytes) -> None:
    """
    Lưu file:
    - Cloudflare R2 nếu USE_R2_STORAGE=True
    - Local disk nếu không
    file_path vẫn được dùng làm object key để giữ tương thích.
    """
    from app.config import settings

    if getattr(settings, 'USE_R2_STORAGE', False):
        s3_key = _to_s3_key(file_path)
        s3_client = _get_s3_client()
        s3_client.put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=s3_key,
            Body=content
        )
        logger.debug("Đã upload lên Cloudflare R2: %s", s3_key)
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

    if getattr(settings, 'USE_R2_STORAGE', False):
        s3_key = _to_s3_key(file_path)
        s3_client = _get_s3_client()
        try:
            response = s3_client.get_object(Bucket=settings.R2_BUCKET_NAME, Key=s3_key)
            return response['Body'].read()
        except Exception as e:
            logger.warning("Không đọc được file từ R2 %s: %s", s3_key, e)
            return None
    else:
        path = Path(file_path)
        if path.exists():
            return path.read_bytes()
        return None


def file_exists(file_path: str | Path) -> bool:
    """Kiểm tra file có tồn tại không."""
    from app.config import settings

    if getattr(settings, 'USE_R2_STORAGE', False):
        import botocore
        s3_key = _to_s3_key(file_path)
        s3_client = _get_s3_client()
        try:
            s3_client.head_object(Bucket=settings.R2_BUCKET_NAME, Key=s3_key)
            return True
        except botocore.exceptions.ClientError as e:
            # Mã lỗi 404 nghĩa là file không tồn tại
            if e.response['Error']['Code'] == "404":
                return False
            return False
        except Exception:
            return False
    else:
        return Path(file_path).exists()


def delete_file(file_path: str | Path) -> None:
    """Xoá file. Không raise lỗi nếu file không tồn tại."""
    from app.config import settings

    if getattr(settings, 'USE_R2_STORAGE', False):
        s3_key = _to_s3_key(file_path)
        s3_client = _get_s3_client()
        try:
            s3_client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=s3_key)
            logger.debug("Đã xoá file trên R2: %s", s3_key)
        except Exception as e:
            logger.warning("Không xoá được file R2 %s: %s", s3_key, e)
    else:
        path = Path(file_path)
        if path.exists():
            path.unlink()


def restore_to_local(file_path: str | Path) -> bool:
    """
    Khi USE_R2_STORAGE=True: tải file về disk tạm (UPLOAD_DIR) để ingestor
    đọc được qua đường dẫn thông thường. Trả về True nếu thành công.
    Khi USE_R2_STORAGE=False: không cần làm gì — trả về file_exists().
    """
    from app.config import settings

    if not getattr(settings, 'USE_R2_STORAGE', False):
        return Path(file_path).exists()

    content = load_file(file_path)
    if content is None:
        return False

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    logger.info("Đã khôi phục file từ R2 về local: %s", file_path)
    return True


def get_blob_url(file_path: str | Path) -> Optional[str]:
    """
    Trả về URL công khai của file.
    Chỉ hoạt động nếu bucket R2 của bạn được cấu hình Custom Domain (Public).
    """
    from app.config import settings

    if not getattr(settings, 'USE_R2_STORAGE', False):
        return None

    s3_key = _to_s3_key(file_path)
    public_domain = getattr(settings, 'R2_PUBLIC_DOMAIN', None)
    
    if public_domain:
        return f"https://{public_domain}/{s3_key}"
    
    # URL nội bộ (thường không cấp quyền truy cập public trực tiếp từ link này)
    return f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{settings.R2_BUCKET_NAME}/{s3_key}"


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_s3_key(file_path: str | Path) -> str:
    """
    Chuyển đường dẫn local thành S3 key.
    Đảm bảo dấu '/' đồng nhất cho dù đang chạy trên môi trường Windows hay Linux.
    Ví dụ: /tmp/uploads/abc123.pdf  →  uploads/abc123.pdf
    """
    from app.config import settings

    path_str = str(file_path).replace("\\", "/")
    upload_dir = str(settings.UPLOAD_DIR).replace("\\", "/").rstrip("/") + "/"
    
    if path_str.startswith(upload_dir):
        return "uploads/" + path_str[len(upload_dir):]
    
    # Fallback: lấy tên file
    return "uploads/" + Path(file_path).name
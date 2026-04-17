import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

from flask import current_app


ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
_catalog_cache: Dict[str, object] = {
    'directory': None,
    'signature': None,
    'catalog': None,
}


def normalize_chatbot_text(text: str) -> str:
    normalized = (text or '').lower().replace('đ', 'd')
    normalized = unicodedata.normalize('NFD', normalized)
    normalized = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Mn')
    return re.sub(r'[^a-z0-9\s]', ' ', normalized)


def slugify_chatbot_text(text: str) -> str:
    normalized = normalize_chatbot_text(text)
    return re.sub(r'[\s-]+', '-', normalized).strip('-')


def compact_chatbot_text(text: str) -> str:
    return re.sub(r'\s+', ' ', normalize_chatbot_text(text)).strip()


def tokenize_chatbot_text(text: str) -> List[str]:
    return [token for token in compact_chatbot_text(text).split() if token]


def _candidate_image_dirs() -> List[Path]:
    backend_dir = Path(current_app.root_path).parent
    app_dir = Path(current_app.root_path)
    return [
        backend_dir / 'static' / 'images' / 'anh',
        app_dir / 'static' / 'images' / 'anh',
        # Compatibility fallback for deployments still keeping images in uploads.
        backend_dir / 'static' / 'uploads',
        app_dir / 'static' / 'uploads',
    ]


def get_chatbot_image_dir() -> Optional[Path]:
    for candidate in _candidate_image_dirs():
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _list_image_files(image_dir: Path) -> List[Path]:
    return sorted(
        path for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
    )


def get_chatbot_image_catalog() -> Dict[str, object]:
    image_dir = get_chatbot_image_dir()
    if image_dir is None:
        return {'directory': None, 'images': [], 'by_slug': {}, 'by_legacy_id': {}}

    image_files = _list_image_files(image_dir)
    signature = (
        str(image_dir.resolve()),
        tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in image_files),
    )

    if (
        _catalog_cache['directory'] == signature[0]
        and _catalog_cache['signature'] == signature[1]
        and _catalog_cache['catalog'] is not None
    ):
        return _catalog_cache['catalog']  # type: ignore[return-value]

    images = []
    by_slug = {}
    by_legacy_id = {}
    slug_counts: Dict[str, int] = {}

    for index, image_path in enumerate(image_files, start=1):
        display_name = image_path.stem
        base_slug = slugify_chatbot_text(display_name) or f'image-{index}'
        slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
        slug = base_slug if slug_counts[base_slug] == 1 else f'{base_slug}-{slug_counts[base_slug]}'

        item = {
            'legacy_id': str(index),
            'slug': slug,
            'display_name': display_name,
            'filename': image_path.name,
            'normalized_name': normalize_chatbot_text(display_name),
            'path': image_path,
        }
        images.append(item)
        by_slug[slug] = item
        by_legacy_id[str(index)] = item

    catalog = {
        'directory': image_dir,
        'images': images,
        'by_slug': by_slug,
        'by_legacy_id': by_legacy_id,
    }
    _catalog_cache['directory'] = signature[0]
    _catalog_cache['signature'] = signature[1]
    _catalog_cache['catalog'] = catalog
    return catalog


def resolve_chatbot_image(identifier: str) -> Optional[Dict[str, object]]:
    catalog = get_chatbot_image_catalog()
    raw_identifier = (identifier or '').strip()
    if not raw_identifier:
        return None

    if raw_identifier.startswith('loc_'):
        try:
            from app.models.location import Location
            loc_id = int(raw_identifier.replace('loc_', ''))
            loc = Location.query.get(loc_id)
            if loc:
                primary = loc.images.filter_by(is_primary=True).first()
                if not primary:
                    primary = loc.images.first()
                if primary and primary.image_url:
                    from flask import current_app
                    import os
                    from pathlib import Path

                    # 1. Try UPLOAD_FOLDER + filename (Most robust for Docker)
                    filename = os.path.basename(primary.image_url)
                    upload_folder = current_app.config.get('UPLOAD_FOLDER')
                    if upload_folder:
                        abs_path = os.path.join(upload_folder, filename)
                        if os.path.exists(abs_path):
                            return {
                                'slug': raw_identifier,
                                'display_name': loc.name,
                                'path': Path(abs_path)
                            }
                    
                    # 2. Try direct mapping relative to backend root
                    rel_path = primary.image_url.lstrip('/')
                    base_dir = os.path.abspath(os.path.join(current_app.root_path, '..'))
                    abs_path = os.path.join(base_dir, rel_path)
                    
                    if os.path.exists(abs_path):
                        return {
                            'slug': raw_identifier,
                            'display_name': loc.name,
                            'path': Path(abs_path)
                        }
        except Exception as e:
            current_app.logger.error(f"Error resolving DB location image {raw_identifier}: {e}")
        return None

    if raw_identifier.startswith('dish_'):
        try:
            from app.models.dish import Dish
            dish_id = int(raw_identifier.replace('dish_', ''))
            dish = Dish.query.get(dish_id)
            if dish and dish.image_url:
                from flask import current_app
                import os
                from pathlib import Path

                # 1. Try UPLOAD_FOLDER + filename
                filename = os.path.basename(dish.image_url)
                upload_folder = current_app.config.get('UPLOAD_FOLDER')
                if upload_folder:
                    abs_path = os.path.join(upload_folder, filename)
                    if os.path.exists(abs_path):
                        return {
                            'slug': raw_identifier,
                            'display_name': dish.name,
                            'path': Path(abs_path)
                        }

                # 2. Try direct mapping
                rel_path = dish.image_url.lstrip('/')
                base_dir = os.path.abspath(os.path.join(current_app.root_path, '..'))
                abs_path = os.path.join(base_dir, rel_path)
                
                if os.path.exists(abs_path):
                    return {
                        'slug': raw_identifier,
                        'display_name': dish.name,
                        'path': Path(abs_path)
                    }
        except Exception as e:
            current_app.logger.error(f"Error resolving DB dish image {raw_identifier}: {e}")
        return None

    if raw_identifier.isdigit():
        return catalog['by_legacy_id'].get(raw_identifier)  # type: ignore[return-value]

    slug = slugify_chatbot_text(raw_identifier)
    if slug:
        item = catalog['by_slug'].get(slug)
        if item:
            return item  # type: ignore[return-value]

    for image in catalog['images']:  # type: ignore[index]
        if raw_identifier.lower() == str(image['filename']).lower():
            return image  # type: ignore[return-value]

    return None


def find_relevant_chatbot_images(message: str, keywords: Optional[List[str]] = None, max_images: int = 2) -> List[Dict[str, object]]:
    catalog = get_chatbot_image_catalog()
    images = catalog['images']  # type: ignore[assignment]
    if not images:
        return []

    message_keywords = keywords or [
        token for token in normalize_chatbot_text(message).split()
        if len(token) > 2
    ]
    if not message_keywords:
        return []

    selected = []
    for image in images:
        score = sum(str(image['normalized_name']).count(keyword) for keyword in message_keywords)
        if score > 0:
            selected.append((score, str(image['display_name']), image))

    selected.sort(key=lambda item: (-item[0], item[1]))
    return [item for _, _, item in selected[:max_images]]


def find_explicit_chatbot_images(text: str, max_images: int = 2) -> List[Dict[str, object]]:
    catalog = get_chatbot_image_catalog()
    images = catalog['images']  # type: ignore[assignment]
    if not images:
        return []

    compact_text = compact_chatbot_text(text)
    if not compact_text:
        return []

    token_set = set(tokenize_chatbot_text(compact_text))
    selected = []

    for image in images:
        normalized_name = compact_chatbot_text(str(image['display_name']))
        if not normalized_name:
            continue

        name_tokens = [token for token in normalized_name.split() if len(token) > 1]
        overlap = sum(1 for token in name_tokens if token in token_set)

        score = 0
        if normalized_name in compact_text:
            score = 100 + len(name_tokens)
        elif len(name_tokens) >= 2 and overlap == len(name_tokens):
            score = 80 + overlap
        elif overlap >= 2:
            score = 40 + overlap
        elif len(name_tokens) == 1 and overlap == 1:
            score = 20

        if score > 0:
            selected.append((score, len(normalized_name), str(image['display_name']), image))

    selected.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item for _, _, _, item in selected[:max_images]]

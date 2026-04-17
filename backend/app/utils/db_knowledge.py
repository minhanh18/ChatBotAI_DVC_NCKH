"""
db_knowledge.py
---------------
Build chatbot knowledge text and image list dynamically from the database.
This avoids hardcoded data in ai_service.py and keeps the chatbot up-to-date
as locations/dishes are added/edited in the admin panel.
"""

from typing import List, Dict
from flask import current_app


def _get_db():
    """Lazy import db to avoid circular imports."""
    from app import db
    return db


def _normalize(text: str) -> str:
    """Simple Vietnamese text normalization for matching."""
    import unicodedata, re
    if not text:
        return ''
    text = text.lower().replace('đ', 'd')
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return re.sub(r'[^a-z0-9\s]', ' ', text)


def build_db_knowledge_text() -> str:
    """
    Build a knowledge base text block from active Locations and Dishes in DB.
    Returns an empty string if DB is unavailable.
    """
    try:
        from app.models.location import Location, Category
        from app.models.dish import Dish

        sections: List[str] = []

        # ── Locations ───────────────────────────────────────────────────────
        try:
            locations = (
                Location.query
                .filter_by(status='ACTIVE')
                .order_by(Location.name)
                .all()
            )
            for loc in locations:
                parts = [f"Địa điểm: {loc.name}"]
                if loc.address:
                    parts.append(f"Địa chỉ: {loc.address}")
                if loc.description:
                    parts.append(f"Mô tả: {loc.description}")
                if loc.category:
                    parts.append(f"Loại hình: {loc.category.name}")
                if loc.price_range_min is not None and loc.price_range_max is not None:
                    parts.append(
                        f"Giá: {int(loc.price_range_min):,} – {int(loc.price_range_max):,} VNĐ"
                    )
                elif loc.price_range_min is not None:
                    parts.append(f"Giá từ: {int(loc.price_range_min):,} VNĐ")
                if loc.rating_avg and loc.rating_avg > 0:
                    parts.append(f"Đánh giá: {loc.rating_avg:.1f}/5")

                # Primary image URL (for AI reference)
                primary_img = (
                    loc.images
                    .filter_by(is_primary=True)
                    .first()
                )
                if not primary_img:
                    primary_img = loc.images.first()
                if primary_img and primary_img.image_url:
                    parts.append(f"Ảnh đại diện: /api/ai/img/loc_{loc.id}")

                sections.append('\n'.join(parts))
        except Exception as e:
            current_app.logger.warning(f"db_knowledge: could not load locations: {e}")

        # ── Dishes ────────────────────────────
        try:
            dishes = Dish.query.order_by(Dish.name).all()
            for dish in dishes:
                parts = [f"Món ăn đặc sản: {dish.name}"]
                if dish.description:
                    parts.append(f"Mô tả: {dish.description}")
                if dish.image_url:
                    parts.append(f"Ảnh: /api/ai/img/dish_{dish.id}")
                sections.append('\n'.join(parts))
        except Exception as e:
            current_app.logger.warning(f"db_knowledge: could not load dishes: {e}")

        knowledge = '\n\n'.join(sections)
        current_app.logger.info(
            f"db_knowledge: built {len(sections)} sections, {len(knowledge)} chars"
        )
        return knowledge

    except Exception as e:
        current_app.logger.error(f"db_knowledge: unexpected error: {e}")
        return ''


def get_db_image_list_for_prompt(message: str = '', max_images: int = 6) -> List[Dict]:
    """
    Return a list of {name, image_url} dicts from active locations that
    are relevant to *message* (or all if message is empty).
    Used to tell the AI which images it may embed.
    """
    try:
        from app.models.location import Location, LocationImage

        # Load all active locations with at least one image
        locations = (
            Location.query
            .filter_by(status='ACTIVE')
            .order_by(Location.name)
            .all()
        )

        if not locations:
            return []

        results: List[Dict] = []

        if message:
            norm_message = _normalize(message)
            message_tokens = [t for t in norm_message.split() if len(t) > 2]

            scored: List[tuple] = []
            for loc in locations:
                norm_name = _normalize(loc.name)
                norm_desc = _normalize(loc.description or '')
                score = sum(
                    norm_name.count(tok) * 3 + norm_desc.count(tok)
                    for tok in message_tokens
                )
                if score > 0:
                    scored.append((score, loc))
            scored.sort(key=lambda x: -x[0])
            candidate_locs = [loc for _, loc in scored[:max_images]]
        else:
            # No message: return first N locations that have images
            candidate_locs = locations

        for loc in candidate_locs:
            if len(results) >= max_images:
                break

            primary = loc.images.filter_by(is_primary=True).first()
            if not primary:
                primary = loc.images.first()
            if primary and primary.image_url:
                results.append({
                    'name': loc.name,
                    'image_url': f"/api/ai/img/loc_{loc.id}",
                    'location_id': loc.id,
                })

        return results

    except Exception as e:
        current_app.logger.error(f"db_knowledge get_db_image_list: {e}")
        return []


def get_db_knowledge_stats() -> Dict:
    """Return counts of DB entities used for knowledge building."""
    try:
        from app.models.location import Location, LocationImage
        from app.models.dish import Dish
        return {
            'locations': Location.query.filter_by(status='ACTIVE').count(),
            'location_images': LocationImage.query.count(),
            'dishes': Dish.query.count(),
        }
    except Exception as e:
        current_app.logger.error(f"db_knowledge stats error: {e}")
        return {}

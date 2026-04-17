import json
import os

from flask import current_app
from sqlalchemy import func, or_
from sqlalchemy.orm import load_only
from werkzeug.utils import secure_filename

from app import cache, db
from app.models.interaction import Review
from app.models.location import Category, Location, LocationImage


class PlacesService:
    PLACES_LIST_VERSION_KEY = 'places:list:version'
    PLACES_DETAIL_VERSION_KEY = 'places:detail:version'
    CATEGORIES_VERSION_KEY = 'places:categories:version'
    STATS_CACHE_KEY = 'main:stats'
    ADMIN_DASHBOARD_CACHE_KEY = 'admin:dashboard'

    @staticmethod
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config.get('ALLOWED_EXTENSIONS', set())

    @staticmethod
    def _normalize_category_id(raw_category_id):
        if raw_category_id in (None, '', 'null', 'undefined'):
            return None
        try:
            return int(raw_category_id)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_float(value):
        if value in (None, '', 'null', 'undefined'):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_path(value):
        if value in (None, '', 'null', 'undefined'):
            return None
        if isinstance(value, (list, dict)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    @staticmethod
    def _cache_version(key):
        return cache.get(key) or '1'

    @staticmethod
    def _cache_get_json(key):
        cached_value = cache.get(key)
        if not cached_value:
            return None
        try:
            return json.loads(cached_value)
        except (TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _cache_set_json(key, payload, ttl):
        cache.set(key, json.dumps(payload, ensure_ascii=False), ex=ttl)

    @staticmethod
    def _bump_cache_version(*keys):
        for key in keys:
            try:
                cache.incr(key)
            except Exception:
                cache.set(key, '2')

    @staticmethod
    def _invalidate_place_caches(include_categories=False):
        PlacesService._bump_cache_version(
            PlacesService.PLACES_LIST_VERSION_KEY,
            PlacesService.PLACES_DETAIL_VERSION_KEY,
        )
        if include_categories:
            PlacesService._bump_cache_version(PlacesService.CATEGORIES_VERSION_KEY)
        cache.delete(PlacesService.STATS_CACHE_KEY)
        cache.delete(PlacesService.ADMIN_DASHBOARD_CACHE_KEY)

    @staticmethod
    def _build_places_cache_key(page, per_page, category, search, sort_by, order, include_inactive):
        cache_params = {
            'page': page,
            'per_page': per_page,
            'category': category or '',
            'search': search or '',
            'sort_by': sort_by,
            'order': order,
            'include_inactive': include_inactive,
        }
        serialized_params = json.dumps(cache_params, sort_keys=True, separators=(',', ':'))
        return f"places:list:v{PlacesService._cache_version(PlacesService.PLACES_LIST_VERSION_KEY)}:{serialized_params}"

    @staticmethod
    def _build_place_detail_cache_key(place_id):
        version = PlacesService._cache_version(PlacesService.PLACES_DETAIL_VERSION_KEY)
        return f'places:detail:v{version}:{place_id}'

    @staticmethod
    def _build_categories_cache_key():
        version = PlacesService._cache_version(PlacesService.CATEGORIES_VERSION_KEY)
        return f'places:categories:v{version}'

    @staticmethod
    def _save_multiple_images(location, files):
        """Save multiple images. First image becomes primary if no primary exists."""
        image_files = []

        # Support both 'images[]' (multiple) and 'image'/'main_image' (single)
        multi = files.getlist('images[]') if hasattr(files, 'getlist') else []
        if multi:
            image_files = [f for f in multi if getattr(f, 'filename', '')]
        if not image_files:
            single = files.get('image') or files.get('main_image')
            if single and getattr(single, 'filename', ''):
                image_files = [single]

        if not image_files:
            return

        has_primary = location.images.filter_by(is_primary=True).first() is not None

        for idx, file in enumerate(image_files):
            if not PlacesService.allowed_file(file.filename):
                raise ValueError(f'Định dạng ảnh không hợp lệ: {file.filename}')

            filename = secure_filename(file.filename)
            # Add unique prefix to avoid collisions
            import time
            unique_filename = f"{int(time.time() * 1000)}_{idx}_{filename}"
            upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(upload_path)

            is_primary = (not has_primary and idx == 0)
            image = LocationImage(
                location=location,
                image_url=f'/uploads/{unique_filename}',
                is_primary=is_primary,
            )
            db.session.add(image)
            if is_primary:
                has_primary = True

    @staticmethod
    def add_images(place_id, files):
        """Add additional images to an existing location."""
        try:
            location = Location.query.get_or_404(place_id)
            PlacesService._save_multiple_images(location, files)
            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Thêm ảnh thành công', 'images': [img.to_dict() for img in location.images.all()]}, 200
        except ValueError as e:
            db.session.rollback()
            return {'error': str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def delete_image(place_id, image_id):
        """Delete a single image from a location."""
        try:
            image = LocationImage.query.filter_by(id=image_id, location_id=place_id).first_or_404()
            was_primary = image.is_primary
            db.session.delete(image)
            db.session.flush()

            # If deleted image was primary, promote the first remaining image
            if was_primary:
                remaining = LocationImage.query.filter_by(location_id=place_id).order_by(LocationImage.id.asc()).first()
                if remaining:
                    remaining.is_primary = True

            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Xóa ảnh thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def set_primary_image(place_id, image_id):
        """Set a specific image as the primary image for a location."""
        try:
            # Remove current primary
            LocationImage.query.filter_by(location_id=place_id, is_primary=True).update({'is_primary': False})
            # Set new primary
            image = LocationImage.query.filter_by(id=image_id, location_id=place_id).first_or_404()
            image.is_primary = True
            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Đã đặt ảnh chính thành công', 'image': image.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def serialize_places_summary(places):
        if not places:
            return []

        category_ids = {place.category_id for place in places if place.category_id}
        categories_by_id = {}
        if category_ids:
            categories_by_id = {
                category.id: category.to_dict()
                for category in Category.query.filter(Category.id.in_(category_ids)).all()
            }

        location_ids = [place.id for place in places]
        images_by_location_id = {}
        if location_ids:
            images = LocationImage.query.filter(LocationImage.location_id.in_(location_ids)).order_by(
                LocationImage.location_id.asc(),
                LocationImage.is_primary.desc(),
                LocationImage.id.asc(),
            ).all()
            for image in images:
                if image.location_id not in images_by_location_id:
                    images_by_location_id[image.location_id] = [image.to_dict()]

        return [
            place.to_summary_dict(
                category=categories_by_id.get(place.category_id),
                images=images_by_location_id.get(place.id, []),
            )
            for place in places
        ]

    @staticmethod
    def get_places(params):
        try:
            page = max(int(params.get('page', 1)), 1)
            default_per_page = current_app.config.get('ITEMS_PER_PAGE', 10)
            per_page = min(max(int(params.get('per_page', default_per_page)), 1), 50)
            category = (params.get('category') or '').strip()
            search = (params.get('search') or '').strip()
            sort_by = params.get('sort_by', 'created_at')
            order = params.get('order', 'desc')
            include_inactive = str(params.get('include_inactive', '')).lower() == 'true'

            cache_key = PlacesService._build_places_cache_key(
                page=page,
                per_page=per_page,
                category=category,
                search=search,
                sort_by=sort_by,
                order=order,
                include_inactive=include_inactive,
            )
            cached_payload = PlacesService._cache_get_json(cache_key)
            if cached_payload is not None:
                return cached_payload, 200

            query = Location.query.options(
                load_only(
                    Location.id,
                    Location.category_id,
                    Location.name,
                    Location.description,
                    Location.address,
                    Location.price_range_min,
                    Location.price_range_max,
                    Location.price,
                    Location.rating_avg,
                    Location.status,
                    Location.path,
                    Location.created_at,
                )
            )

            if not include_inactive:
                query = query.filter(Location.status == 'ACTIVE')
            if category:
                query = query.join(Category).filter(or_(
                    Category.type == category,
                    Category.name.ilike(f'%{category}%')
                ))
            if search:
                search_term = f'%{search}%'
                query = query.filter(or_(
                    Location.name.ilike(search_term),
                    Location.description.ilike(search_term),
                    Location.address.ilike(search_term),
                ))

            sort_column = Location.created_at
            if sort_by == 'name':
                sort_column = Location.name
            elif sort_by == 'rating':
                sort_column = Location.rating_avg

            query = query.order_by(sort_column.asc() if order == 'asc' else sort_column.desc())
            pagination = query.paginate(page=page, per_page=per_page, error_out=False)
            payload = {
                'places': PlacesService.serialize_places_summary(pagination.items),
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page,
                'per_page': per_page,
            }
            PlacesService._cache_set_json(cache_key, payload, ttl=120)
            return payload, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def get_dishes():
        """Lấy danh sách món ăn đặc sản"""
        try:
            from app.models.dish import Dish
            dishes = Dish.query.all()
            return [dish.to_dict() for dish in dishes], 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def get_place(place_id):
        try:
            location = Location.query.get_or_404(place_id)
            data = location.to_dict()
            # Thêm thông tin category vào kết quả trả về
            if location.category:
                data['category'] = location.category.to_dict()
            return data, 200
        except Exception as e:
            return {'error': str(e)}, 500


    @staticmethod
    def create_place(data, files):
        try:
            name = (data.get('name') or '').strip()
            address = (data.get('address') or '').strip()
            category_id = PlacesService._normalize_category_id(data.get('category_id'))
            status = data.get('status') if data.get('status') in ('ACTIVE', 'INACTIVE') else 'ACTIVE'

            if not name:
                return {'error': 'Tên địa điểm không được để trống'}, 400
            if not address:
                return {'error': 'Địa chỉ không được để trống'}, 400
            if category_id is None:
                return {'error': 'Vui lòng chọn danh mục'}, 400
            if not Category.query.get(category_id):
                return {'error': 'Danh mục không tồn tại'}, 400

            location = Location(
                name=name,
                category_id=category_id,
                description=(data.get('description') or '').strip() or None,
                address=address,
                price_range_min=PlacesService._normalize_float(data.get('price_range_min')),
                price_range_max=PlacesService._normalize_float(data.get('price_range_max')),
                price=PlacesService._normalize_float(data.get('price')),
                path=PlacesService._normalize_path(data.get('path')),
                status=status,
            )
            db.session.add(location)
            db.session.flush()
            PlacesService._save_multiple_images(location, files)
            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Tạo địa điểm thành công', 'place': location.to_dict()}, 201
        except ValueError as e:
            db.session.rollback()
            return {'error': str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def update_place(place_id, data, files):
        try:
            location = Location.query.get_or_404(place_id)
            if 'name' in data:
                name = (data.get('name') or '').strip()
                if not name:
                    return {'error': 'Tên địa điểm không được để trống'}, 400
                location.name = name
            if 'category_id' in data:
                category_id = PlacesService._normalize_category_id(data.get('category_id'))
                if category_id is None or not Category.query.get(category_id):
                    return {'error': 'Danh mục không tồn tại'}, 400
                location.category_id = category_id
            if 'description' in data:
                location.description = (data.get('description') or '').strip() or None
            if 'address' in data:
                address = (data.get('address') or '').strip()
                if not address:
                    return {'error': 'Địa chỉ không được để trống'}, 400
                location.address = address
            if 'price_range_min' in data:
                location.price_range_min = PlacesService._normalize_float(data.get('price_range_min'))
            if 'price_range_max' in data:
                location.price_range_max = PlacesService._normalize_float(data.get('price_range_max'))
            if 'price' in data:
                location.price = PlacesService._normalize_float(data.get('price'))
            if 'path' in data:
                location.path = PlacesService._normalize_path(data.get('path'))
            if 'status' in data and data.get('status') in ('ACTIVE', 'INACTIVE'):
                location.status = data.get('status')

            PlacesService._save_multiple_images(location, files)
            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Cập nhật thành công', 'place': location.to_dict()}, 200
        except ValueError as e:
            db.session.rollback()
            return {'error': str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def delete_place(place_id):
        try:
            location = Location.query.get_or_404(place_id)
            location.status = 'INACTIVE'
            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Xóa địa điểm thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def get_reviews(place_id):
        try:
            reviews = Review.query.filter_by(location_id=place_id).order_by(Review.created_at.desc()).all()
            return [review.to_dict() for review in reviews], 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def add_review(place_id, user_id, data):
        try:
            location = Location.query.get_or_404(place_id)
            rating = int(data.get('rating', 0))
            if rating < 1 or rating > 5:
                return {'error': 'Rating phải từ 1-5'}, 400

            existing_review = Review.query.filter_by(location_id=place_id, user_id=user_id).first()
            if existing_review:
                return {'error': 'Bạn đã đánh giá địa điểm này'}, 400

            review = Review(
                location_id=place_id,
                user_id=user_id,
                rating=rating,
                comment=data.get('comment') or data.get('content') or data.get('title'),
                images=data.get('images'),
            )
            db.session.add(review)
            db.session.flush()

            avg_rating = db.session.query(func.avg(Review.rating)).filter_by(location_id=place_id).scalar()
            location.rating_avg = round(avg_rating, 1) if avg_rating else 0
            db.session.commit()
            PlacesService._invalidate_place_caches()
            return {'message': 'Thêm đánh giá thành công', 'review': review.to_dict()}, 201
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def get_categories():
        try:
            cache_key = PlacesService._build_categories_cache_key()
            cached_payload = PlacesService._cache_get_json(cache_key)
            if cached_payload is not None:
                return cached_payload, 200

            payload = [category.to_dict() for category in Category.query.order_by(Category.name.asc()).all()]
            PlacesService._cache_set_json(cache_key, payload, ttl=300)
            return payload, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def get_category(category_id):
        try:
            return Category.query.get_or_404(category_id).to_dict(), 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def create_category(data):
        try:
            name = data.get('name')
            category_type = data.get('type')
            if not name or not category_type:
                return {'error': 'Thiếu thông tin name hoặc type'}, 400
            category = Category(name=name, type=category_type, icon=data.get('icon'))
            db.session.add(category)
            db.session.commit()
            PlacesService._invalidate_place_caches(include_categories=True)
            return {'message': 'Tạo danh mục thành công', 'category': category.to_dict()}, 201
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def update_category(category_id, data):
        try:
            category = Category.query.get_or_404(category_id)
            if 'name' in data:
                category.name = data['name']
            if 'type' in data:
                category.type = data['type']
            if 'icon' in data:
                category.icon = data['icon']
            db.session.commit()
            PlacesService._invalidate_place_caches(include_categories=True)
            return {'message': 'Cập nhật danh mục thành công', 'category': category.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def delete_category(category_id):
        try:
            category = Category.query.get_or_404(category_id)
            if category.locations.count() > 0:
                return {'error': 'Không thể xóa danh mục đang có địa điểm'}, 400
            db.session.delete(category)
            db.session.commit()
            PlacesService._invalidate_place_caches(include_categories=True)
            return {'message': 'Xóa danh mục thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500


def get_places_service():
    return PlacesService()

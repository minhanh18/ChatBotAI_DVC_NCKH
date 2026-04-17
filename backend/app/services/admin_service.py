import json
from datetime import datetime, timedelta

from sqlalchemy import func, or_

from app import cache, db
from app.models.ai import ChatSession
from app.models.interaction import Review, SavedItinerary
from app.models.location import Location
from app.models.post import Comment, Post
from app.models.user import User
from app.services.places_service import PlacesService


class AdminService:
    DASHBOARD_CACHE_KEY = 'admin:dashboard'

    @staticmethod
    def get_dashboard_stats():
        try:
            cached_payload = cache.get(AdminService.DASHBOARD_CACHE_KEY)
            if cached_payload:
                try:
                    return json.loads(cached_payload), 200
                except (TypeError, json.JSONDecodeError):
                    pass

            total_users = User.query.count()
            total_places = Location.query.count()
            active_places = Location.query.filter(Location.status == 'ACTIVE').count()
            total_itineraries = SavedItinerary.query.count()
            total_chat_sessions = ChatSession.query.count()
            total_posts = Post.query.count()
            total_comments = Comment.query.count()
            total_reviews = Review.query.count()

            recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
            recent_places = Location.query.order_by(Location.created_at.desc()).limit(5).all()
            popular_places = Location.query.order_by(Location.rating_avg.desc()).limit(10).all()

            categories = db.session.query(Location.category_id, func.count(Location.id)).filter(
                Location.status == 'ACTIVE'
            ).group_by(Location.category_id).all()
            category_stats = {str(category_id or 'uncategorized'): count for category_id, count in categories}

            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            new_users_count = User.query.filter(User.created_at >= thirty_days_ago).count()

            payload = {
                'stats': {
                    'total_users': total_users,
                    'total_places': total_places,
                    'active_places': active_places,
                    'total_itineraries': total_itineraries,
                    'total_chat_sessions': total_chat_sessions,
                    'new_users_30_days': new_users_count,
                    'category_stats': category_stats,
                    'total_posts': total_posts,
                    'total_comments': total_comments,
                    'total_reviews': total_reviews,
                },
                'recent_users': [user.to_dict() for user in recent_users],
                'recent_places': PlacesService.serialize_places_summary(recent_places),
                'popular_places': PlacesService.serialize_places_summary(popular_places),
            }
            cache.set(AdminService.DASHBOARD_CACHE_KEY, json.dumps(payload, ensure_ascii=False), ex=120)
            return payload, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def get_users(params):
        try:
            page = params.get('page', 1)
            per_page = params.get('per_page', 20)
            search = params.get('search')

            query = User.query
            if search:
                search_term = f'%{search}%'
                query = query.filter(or_(User.fullname.ilike(search_term), User.email.ilike(search_term)))

            pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            return {
                'users': [user.to_dict() for user in pagination.items],
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page,
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def toggle_user_active(user_id, admin_user):
        try:
            user = User.query.get_or_404(user_id)
            if user.role == 'ADMIN':
                return {'error': 'Không thể thay đổi trạng thái của admin'}, 400
            if user.id == admin_user.id:
                return {'error': 'Không thể tự vô hiệu hóa tài khoản của chính mình'}, 400

            user.is_active = not bool(user.is_active)
            db.session.commit()

            status = 'kích hoạt' if user.is_active else 'vô hiệu hóa'
            cache.delete(AdminService.DASHBOARD_CACHE_KEY)
            return {'message': f'Đã {status} người dùng thành công', 'user': user.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def make_admin(user_id):
        try:
            user = User.query.get_or_404(user_id)
            user.role = 'ADMIN'
            db.session.commit()
            cache.delete(AdminService.DASHBOARD_CACHE_KEY)
            return {'message': 'Đã cấp quyền admin thành công', 'user': user.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def create_admin(data):
        try:
            fullname = (data.get('fullname') or '').strip()
            email = (data.get('email') or '').strip().lower()
            password = data.get('password') or ''

            if not fullname:
                return {'error': 'Họ tên không được để trống'}, 400
            if not email:
                return {'error': 'Email không được để trống'}, 400
            if not password or len(password) < 6:
                return {'error': 'Mật khẩu phải có ít nhất 6 ký tự'}, 400

            if User.query.filter_by(email=email).first():
                return {'error': 'Email đã được sử dụng'}, 400

            user = User(fullname=fullname, email=email, role='ADMIN', is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            cache.delete(AdminService.DASHBOARD_CACHE_KEY)
            return {'message': 'Tạo tài khoản admin thành công', 'user': user.to_dict()}, 201
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def get_analytics():
        try:
            def get_growth_last_year(model):
                growth = []
                for months_back in range(12, 0, -1):
                    date = datetime.utcnow() - timedelta(days=30 * months_back)
                    count = model.query.filter(model.created_at <= date).count()
                    growth.append({'month': date.strftime('%Y-%m'), 'count': count})
                return growth

            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            active_users = db.session.query(
                User.id,
                User.fullname,
                User.email,
                func.max(ChatSession.updated_at).label('last_active'),
            ).join(ChatSession).filter(ChatSession.updated_at >= seven_days_ago).group_by(User.id).all()

            return {
                'user_growth': get_growth_last_year(User),
                'places_growth': get_growth_last_year(Location),
                'active_users': [
                    {
                        'id': user.id,
                        'fullname': user.fullname,
                        'email': user.email,
                        'last_active': user.last_active.isoformat() if user.last_active else None,
                    }
                    for user in active_users
                ],
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    # ─── Posts Management ───────────────────────────────────────────────────────

    @staticmethod
    def get_posts(params):
        try:
            page = int(params.get('page', 1))
            per_page = int(params.get('per_page', 20))
            search = (params.get('search') or '').strip()

            query = Post.query
            if search:
                term = f'%{search}%'
                query = query.filter(or_(
                    Post.title.ilike(term),
                    Post.content.ilike(term),
                ))

            pagination = query.order_by(Post.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            return {
                'posts': [p.to_dict() for p in pagination.items],
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page,
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def delete_post(post_id):
        try:
            post = Post.query.get(post_id)
            if not post:
                return {'error': 'Không tìm thấy bài viết'}, 404
            db.session.delete(post)
            db.session.commit()
            cache.delete(AdminService.DASHBOARD_CACHE_KEY)
            return {'message': 'Đã xóa bài viết thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    # ─── Comments Management ────────────────────────────────────────────────────

    @staticmethod
    def get_comments(params):
        try:
            page = int(params.get('page', 1))
            per_page = int(params.get('per_page', 20))
            search = (params.get('search') or '').strip()

            query = Comment.query
            if search:
                term = f'%{search}%'
                query = query.filter(Comment.content.ilike(term))

            pagination = query.order_by(Comment.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

            results = []
            for c in pagination.items:
                item = c.to_dict()
                item['post_title'] = c.post.title if c.post else None
                results.append(item)

            return {
                'comments': results,
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page,
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def delete_comment(comment_id):
        try:
            comment = Comment.query.get(comment_id)
            if not comment:
                return {'error': 'Không tìm thấy bình luận'}, 404
            db.session.delete(comment)
            db.session.commit()
            cache.delete(AdminService.DASHBOARD_CACHE_KEY)
            return {'message': 'Đã xóa bình luận thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    # ─── Reviews Management ─────────────────────────────────────────────────────

    @staticmethod
    def get_reviews(params):
        try:
            page = int(params.get('page', 1))
            per_page = int(params.get('per_page', 20))
            search = (params.get('search') or '').strip()
            location_id = params.get('location_id')

            query = Review.query
            if location_id:
                query = query.filter(Review.location_id == int(location_id))
            if search:
                term = f'%{search}%'
                query = query.filter(Review.comment.ilike(term))

            pagination = query.order_by(Review.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

            results = []
            for r in pagination.items:
                item = r.to_dict()
                item['location_name'] = r.location.name if r.location else None
                results.append(item)

            return {
                'reviews': results,
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page,
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def delete_review(review_id):
        try:
            review = Review.query.get(review_id)
            if not review:
                return {'error': 'Không tìm thấy đánh giá'}, 404

            location_id = review.location_id
            db.session.delete(review)
            db.session.flush()

            # Recalculate rating_avg for the location
            avg = db.session.query(func.avg(Review.rating)).filter_by(location_id=location_id).scalar()
            location = Location.query.get(location_id)
            if location:
                location.rating_avg = round(avg, 1) if avg else 0

            db.session.commit()
            cache.delete(AdminService.DASHBOARD_CACHE_KEY)
            return {'message': 'Đã xóa đánh giá thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500


def get_admin_service():
    return AdminService()

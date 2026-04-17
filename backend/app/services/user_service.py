from app import db
from app.models.ai import ChatSession
from app.models.interaction import Favorite, Review, SavedItinerary
from app.models.location import Location
from app.models.user import UserPreference


class UserService:
    @staticmethod
    def get_profile(user):
        try:
            return {
                'user': user.to_dict(),
                'stats': {
                    'itineraries': SavedItinerary.query.filter_by(user_id=user.id).count(),
                    'reviews': Review.query.filter_by(user_id=user.id).count(),
                    'chat_sessions': ChatSession.query.filter_by(user_id=user.id).count(),
                    'favorites': Favorite.query.filter_by(user_id=user.id).count(),
                },
                'preferences': [preference.to_dict() for preference in user.preferences.order_by(UserPreference.preference_level.desc()).all()],
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def update_profile(user, data):
        try:
            if 'full_name' in data:
                user.fullname = data['full_name']
            if 'fullname' in data:
                user.fullname = data['fullname']
            if 'phone' in data:
                user.phone = data['phone']
            if 'avatar_url' in data:
                user.avatar = data['avatar_url']
            if 'avatar' in data:
                user.avatar = data['avatar']
            db.session.commit()
            return {'message': 'Cập nhật profile thành công', 'user': user.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def get_preferences(user):
        try:
            preferences = [preference.to_dict() for preference in user.preferences.order_by(UserPreference.preference_level.desc()).all()]
            return {'preferences': preferences, 'total': len(preferences)}, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def update_preferences(user, data):
        try:
            preference_items = data.get('preferences', data if isinstance(data, list) else [])
            if not isinstance(preference_items, list):
                return {'error': 'Dữ liệu preferences không hợp lệ'}, 400

            user.preferences.delete()
            for item in preference_items:
                category_id = item.get('category_id')
                if not category_id:
                    continue
                db.session.add(UserPreference(
                    user_id=user.id,
                    category_id=int(category_id),
                    preference_level=int(item.get('preference_level', 1)),
                ))

            db.session.commit()
            return UserService.get_preferences(user)
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def get_favorites(user):
        try:
            favorites = Favorite.query.filter_by(user_id=user.id).order_by(Favorite.created_at.desc()).all()
            return {'favorites': [favorite.to_dict() for favorite in favorites], 'total': len(favorites)}, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def add_favorite(user, place_id):
        try:
            Location.query.get_or_404(place_id)
            favorite = Favorite.query.filter_by(user_id=user.id, location_id=place_id).first()
            if favorite:
                return {'message': 'Địa điểm đã có trong yêu thích', 'favorite': favorite.to_dict()}, 200

            favorite = Favorite(user_id=user.id, location_id=place_id)
            db.session.add(favorite)
            db.session.commit()
            return {'message': 'Đã thêm vào yêu thích', 'favorite': favorite.to_dict()}, 201
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def remove_favorite(user, place_id):
        try:
            favorite = Favorite.query.filter_by(user_id=user.id, location_id=place_id).first()
            if not favorite:
                return {'message': 'Địa điểm chưa có trong yêu thích'}, 200
            db.session.delete(favorite)
            db.session.commit()
            return {'message': 'Đã xóa khỏi yêu thích'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def toggle_favorite(user, place_id):
        favorite = Favorite.query.filter_by(user_id=user.id, location_id=place_id).first()
        if favorite:
            result, status_code = UserService.remove_favorite(user, place_id)
            result['favorited'] = False
            return result, status_code

        result, status_code = UserService.add_favorite(user, place_id)
        result['favorited'] = True
        return result, status_code

    @staticmethod
    def get_dashboard_data(user):
        try:
            recent_itineraries = SavedItinerary.query.filter_by(user_id=user.id).order_by(SavedItinerary.created_at.desc()).limit(5).all()
            recent_reviews = Review.query.filter_by(user_id=user.id).order_by(Review.created_at.desc()).limit(5).all()
            recent_chats = ChatSession.query.filter_by(user_id=user.id).order_by(ChatSession.started_at.desc()).limit(5).all()
            stats = {
                'itineraries_count': SavedItinerary.query.filter_by(user_id=user.id).count(),
                'reviews_count': Review.query.filter_by(user_id=user.id).count(),
                'chat_sessions_count': ChatSession.query.filter_by(user_id=user.id).count(),
                'favorites_count': Favorite.query.filter_by(user_id=user.id).count(),
            }
            return {
                'stats': stats,
                'recent_itineraries': [itinerary.to_dict() for itinerary in recent_itineraries],
                'recent_reviews': [review.to_dict() for review in recent_reviews],
                'recent_chats': [chat.to_dict() for chat in recent_chats],
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500



def get_user_service():
    return UserService()

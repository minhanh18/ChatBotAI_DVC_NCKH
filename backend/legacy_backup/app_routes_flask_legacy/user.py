from flask import Blueprint, jsonify, request
from flask_jwt_extended import current_user

from app.models.interaction import Review, SavedItinerary
from app.services.itinerary_service import get_itinerary_service
from app.services.user_service import get_user_service
from app.utils.auth import jwt_login_required

bp = Blueprint('user', __name__, url_prefix='/api/user')
user_service = get_user_service()
itinerary_service = get_itinerary_service()


@bp.route('/profile', methods=['GET'])
@jwt_login_required
def get_profile():
    result, status_code = user_service.get_profile(current_user)
    return jsonify(result), status_code


@bp.route('/profile', methods=['PUT'])
@jwt_login_required
def update_profile():
    data = request.get_json() or {}
    result, status_code = user_service.update_profile(current_user, data)
    return jsonify(result), status_code


@bp.route('/preferences', methods=['GET'])
@jwt_login_required
def get_preferences():
    result, status_code = user_service.get_preferences(current_user)
    return jsonify(result), status_code


@bp.route('/preferences', methods=['PUT'])
@jwt_login_required
def update_preferences():
    data = request.get_json() or {}
    result, status_code = user_service.update_preferences(current_user, data)
    return jsonify(result), status_code


@bp.route('/itineraries', methods=['GET'])
@jwt_login_required
def get_itineraries():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    query = SavedItinerary.query.filter_by(user_id=current_user.id)
    pagination = query.order_by(SavedItinerary.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'itineraries': [itinerary.to_dict() for itinerary in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
    })


@bp.route('/itineraries/<int:itinerary_id>', methods=['GET'])
@jwt_login_required
def get_itinerary(itinerary_id):
    itinerary = itinerary_service.get_itinerary(itinerary_id, current_user.id)
    if not itinerary:
        return jsonify({'error': 'Không tìm thấy lịch trình'}), 404
    return jsonify(itinerary)


@bp.route('/itineraries/<int:itinerary_id>', methods=['PUT'])
@jwt_login_required
def update_itinerary(itinerary_id):
    data = request.get_json() or {}
    result = itinerary_service.update_itinerary(itinerary_id, current_user.id, data)
    return jsonify(result), 200 if result['success'] else 400


@bp.route('/itineraries/<int:itinerary_id>', methods=['DELETE'])
@jwt_login_required
def delete_itinerary(itinerary_id):
    result = itinerary_service.delete_itinerary(itinerary_id, current_user.id)
    return jsonify(result), 200 if result['success'] else 400


@bp.route('/reviews', methods=['GET'])
@jwt_login_required
def get_reviews():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    pagination = Review.query.filter_by(user_id=current_user.id).order_by(Review.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'reviews': [review.to_dict() for review in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
    })


@bp.route('/reviews/<int:review_id>', methods=['PUT'])
@jwt_login_required
def update_review(review_id):
    review = Review.query.filter_by(id=review_id, user_id=current_user.id).first_or_404()
    data = request.get_json() or {}
    if 'rating' in data:
        review.rating = data['rating']
    if 'content' in data:
        review.comment = data['content']
    if 'comment' in data:
        review.comment = data['comment']
    db = __import__('app').db
    db.session.commit()
    return jsonify({'message': 'Cập nhật review thành công', 'review': review.to_dict()})


@bp.route('/reviews/<int:review_id>', methods=['DELETE'])
@jwt_login_required
def delete_review(review_id):
    review = Review.query.filter_by(id=review_id, user_id=current_user.id).first_or_404()
    db = __import__('app').db
    db.session.delete(review)
    db.session.commit()
    return jsonify({'message': 'Xóa review thành công'})


@bp.route('/favorites', methods=['GET'])
@jwt_login_required
def get_favorites():
    result, status_code = user_service.get_favorites(current_user)
    return jsonify(result), status_code


@bp.route('/favorites/<int:place_id>', methods=['POST'])
@jwt_login_required
def add_favorite(place_id):
    result, status_code = user_service.add_favorite(current_user, place_id)
    return jsonify(result), status_code


@bp.route('/favorites/<int:place_id>', methods=['DELETE'])
@jwt_login_required
def remove_favorite(place_id):
    result, status_code = user_service.remove_favorite(current_user, place_id)
    return jsonify(result), status_code


@bp.route('/dashboard', methods=['GET'])
@jwt_login_required
def dashboard():
    result, status_code = user_service.get_dashboard_data(current_user)
    return jsonify(result), status_code

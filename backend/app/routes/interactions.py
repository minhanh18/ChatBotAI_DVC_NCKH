from flask import Blueprint, jsonify, request
from flask_jwt_extended import current_user

from app.services.user_service import get_user_service
from app.utils.auth import jwt_login_required

bp = Blueprint('interactions', __name__, url_prefix='/api')
user_service = get_user_service()


@bp.route('/favorites', methods=['GET'])
@jwt_login_required
def get_favorites_alias():
    result, status_code = user_service.get_favorites(current_user)
    if status_code != 200:
        return jsonify(result), status_code
    return jsonify(result.get('favorites', [])), 200


@bp.route('/favorites/toggle', methods=['POST'])
@jwt_login_required
def toggle_favorite():
    data = request.get_json() or {}
    place_id = data.get('locationId') or data.get('place_id')
    if not place_id:
        return jsonify({'error': 'Thiếu locationId'}), 400
    result, status_code = user_service.toggle_favorite(current_user, int(place_id))
    return jsonify(result), status_code

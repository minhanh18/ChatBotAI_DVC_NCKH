from flask import Blueprint, jsonify, request
from flask_jwt_extended import current_user, jwt_required

from app.services.places_service import PlacesService

bp = Blueprint('places', __name__)


def admin_required():
    if not current_user or not getattr(current_user, 'id', None):
        return jsonify({'error': 'Vui lòng đăng nhập'}), 401
    if not getattr(current_user, 'is_active', True):
        return jsonify({'error': 'Tài khoản đã bị vô hiệu hóa'}), 403
    if current_user.role != 'ADMIN':
        return jsonify({'error': 'Không có quyền truy cập'}), 403
    return None


@bp.route('/api/locations', methods=['GET'])
@bp.route('/api/places', methods=['GET'])
def get_places():
    params = request.args.to_dict()
    if 'page' in params:
        params['page'] = int(params['page'])
    if 'per_page' in params:
        params['per_page'] = int(params['per_page'])
    result, status_code = PlacesService.get_places(params)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>', methods=['GET'])
@bp.route('/api/places/<int:place_id>', methods=['GET'])
def get_place(place_id):
    result, status_code = PlacesService.get_place(place_id)
    return jsonify(result), status_code


@bp.route('/api/locations', methods=['POST'])
@bp.route('/api/places', methods=['POST'])
@jwt_required()
def create_place():
    auth_error = admin_required()
    if auth_error:
        return auth_error
    data = request.form.to_dict() if not request.is_json else (request.get_json() or {})
    result, status_code = PlacesService.create_place(data, request.files)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>', methods=['PUT'])
@bp.route('/api/places/<int:place_id>', methods=['PUT'])
@jwt_required()
def update_place(place_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    data = request.form.to_dict() if not request.is_json else (request.get_json() or {})
    result, status_code = PlacesService.update_place(place_id, data, request.files)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>', methods=['DELETE'])
@bp.route('/api/places/<int:place_id>', methods=['DELETE'])
@jwt_required()
def delete_place(place_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    result, status_code = PlacesService.delete_place(place_id)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>/images', methods=['POST'])
@bp.route('/api/places/<int:place_id>/images', methods=['POST'])
@jwt_required()
def add_place_images(place_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    result, status_code = PlacesService.add_images(place_id, request.files)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>/images/<int:image_id>', methods=['DELETE'])
@bp.route('/api/places/<int:place_id>/images/<int:image_id>', methods=['DELETE'])
@jwt_required()
def delete_place_image(place_id, image_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    result, status_code = PlacesService.delete_image(place_id, image_id)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>/images/<int:image_id>/set-primary', methods=['PUT'])
@bp.route('/api/places/<int:place_id>/images/<int:image_id>/set-primary', methods=['PUT'])
@jwt_required()
def set_primary_place_image(place_id, image_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    result, status_code = PlacesService.set_primary_image(place_id, image_id)
    return jsonify(result), status_code




@bp.route('/api/locations/<int:place_id>/reviews', methods=['GET'])
@bp.route('/api/places/<int:place_id>/reviews', methods=['GET'])
def get_reviews(place_id):
    result, status_code = PlacesService.get_reviews(place_id)
    return jsonify(result), status_code


@bp.route('/api/locations/<int:place_id>/reviews', methods=['POST'])
@bp.route('/api/places/<int:place_id>/reviews', methods=['POST'])
@jwt_required()
def add_review(place_id):
    data = request.get_json() or {}
    result, status_code = PlacesService.add_review(place_id, current_user.id, data)
    return jsonify(result), status_code


@bp.route('/api/locations/categories', methods=['GET'])
@bp.route('/api/places/categories', methods=['GET'])
def get_categories():
    result, status_code = PlacesService.get_categories()
    return jsonify(result), status_code


@bp.route('/api/locations/categories/<int:category_id>', methods=['GET'])
@bp.route('/api/places/categories/<int:category_id>', methods=['GET'])
def get_category(category_id):
    result, status_code = PlacesService.get_category(category_id)
    return jsonify(result), status_code


@bp.route('/api/locations/categories', methods=['POST'])
@bp.route('/api/places/categories', methods=['POST'])
@jwt_required()
def create_category():
    auth_error = admin_required()
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    result, status_code = PlacesService.create_category(data)
    return jsonify(result), status_code


@bp.route('/api/locations/categories/<int:category_id>', methods=['PUT'])
@bp.route('/api/places/categories/<int:category_id>', methods=['PUT'])
@jwt_required()
def update_category(category_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    result, status_code = PlacesService.update_category(category_id, data)
    return jsonify(result), status_code


@bp.route('/api/locations/categories/<int:category_id>', methods=['DELETE'])
@bp.route('/api/places/categories/<int:category_id>', methods=['DELETE'])
@jwt_required()
def delete_category(category_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    result, status_code = PlacesService.delete_category(category_id)
    return jsonify(result), status_code


@bp.route('/dishes', methods=['GET'])
def get_dishes():
    """Lấy danh sách món ăn"""
    result, status_code = PlacesService.get_dishes()
    return jsonify(result), status_code

from flask import Blueprint, current_app, jsonify, make_response, request
from flask_jwt_extended import (
    create_access_token,
    current_user,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    unset_jwt_cookies,
)

from app import cache, jwt
from app.models.user import User
from app.services.auth_service import AuthService

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


@jwt.token_in_blocklist_loader
def check_if_token_is_revoked(jwt_header, jwt_payload) -> bool:
    jti = jwt_payload['jti']
    token_in_cache = cache.get(jti)
    return token_in_cache is not None


@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data) -> User:
    identity = jwt_data['sub']
    return User.query.get(identity)


@bp.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    phone = data.get('phone', '')
    fullname = data.get('fullname', '')
    password = data.get('password', '')
    result, status_code = AuthService.register_user(email, password, fullname, phone)
    return jsonify(result), status_code


@bp.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    result, status_code = AuthService.login_user_by_email(email, password)
    response = make_response(jsonify(result), status_code)

    if status_code == 200:
        response.set_cookie(
            'access_token_cookie',
            result.get('access_token'),
            httponly=True,
            samesite='Strict',
            max_age=current_app.config.get('JWT_ACCESS_TOKEN_EXPIRES', 900),
        )
        response.set_cookie(
            'refresh_token_cookie',
            result.get('refresh_token'),
            httponly=True,
            samesite='Strict',
            max_age=current_app.config.get('JWT_REFRESH_TOKEN_EXPIRES', 259200),
        )

    return response


@bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json() or {}
    result, status_code = AuthService.request_password_reset(data.get('email', '').strip().lower())
    return jsonify(result), status_code


@bp.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json() or {}
    token = data.get('token')
    new_password = data.get('new_password')
    result, status_code = AuthService.reset_password(token, new_password)
    return jsonify(result), status_code


@bp.route('/logout', methods=['POST'])
@jwt_required(verify_type=False)
def logout():
    token_data = get_jwt()
    result, status_code = AuthService.logout_user(token_data)
    response = make_response(jsonify(result), status_code)
    unset_jwt_cookies(response)
    return response


@bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    return jsonify(current_user.to_dict())


@bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    data = request.get_json() or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    result, status_code = AuthService.change_password(current_user, old_password, new_password)
    return jsonify(result), status_code


@bp.route('/update-profile', methods=['PUT'])
@jwt_required()
def update_profile():
    data = request.get_json() or {}
    result, status_code = AuthService.update_profile(current_user, data)
    return jsonify(result), status_code


@bp.route('/check-auth', methods=['GET'])
@jwt_required()
def check_auth():
    return jsonify({'authenticated': True, 'user': current_user.to_dict()})


@bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh_token():
    access_token = create_access_token(identity=get_jwt_identity())
    response = make_response({'access_token': access_token})
    response.set_cookie(
        'access_token_cookie',
        access_token,
        httponly=True,
        samesite='Strict',
        max_age=current_app.config.get('JWT_ACCESS_TOKEN_EXPIRES', 900),
    )
    return response, 201

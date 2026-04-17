from functools import wraps

from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, current_user


def jwt_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        if not current_user or not getattr(current_user, 'id', None):
            return jsonify({'error': 'Vui lòng đăng nhập'}), 401
        if hasattr(current_user, 'is_active') and not current_user.is_active:
            return jsonify({'error': 'Tài khoản đã bị vô hiệu hóa'}), 403
        return fn(*args, **kwargs)
    return wrapper


def jwt_admin_required(fn):
    @wraps(fn)
    @jwt_login_required
    def wrapper(*args, **kwargs):
        if getattr(current_user, 'role', None) != 'ADMIN':
            return jsonify({'error': 'Không có quyền truy cập'}), 403
        return fn(*args, **kwargs)
    return wrapper


def jwt_business_required(fn):
    @wraps(fn)
    @jwt_login_required
    def wrapper(*args, **kwargs):
        if getattr(current_user, 'role', None) not in ('BUSINESS', 'ADMIN'):
            return jsonify({'error': 'Chỉ doanh nghiệp đã được duyệt mới có quyền truy cập'}), 403
        return fn(*args, **kwargs)
    return wrapper

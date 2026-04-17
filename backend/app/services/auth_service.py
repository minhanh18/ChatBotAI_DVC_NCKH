import math
import re
import uuid
from datetime import datetime

from flask import current_app, render_template_string
from flask_jwt_extended import create_access_token, create_refresh_token
from flask_mail import Message

from app import cache, db, mail
from app.models.user import User


def validate_email(email) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_password(password) -> tuple[bool, str | None]:
    if len(password) < 6:
        return False, 'Mật khẩu phải có ít nhất 6 ký tự'
    return True, None


class AuthService:
    @staticmethod
    def register_user(email, password, fullname, phone):
        try:
            if not email or not password:
                return {'error': 'Vui lòng điền đầy đủ thông tin'}, 400
            if not validate_email(email):
                return {'error': 'Email không hợp lệ'}, 400

            is_valid, error_msg = validate_password(password)
            if not is_valid:
                return {'error': error_msg}, 400
            if User.query.filter_by(email=email).first():
                return {'error': 'Email đã được đăng ký'}, 400

            user = User(email=email, fullname=fullname, phone=(phone or '').strip(), is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            return {'message': 'Đăng ký thành công', 'user': user.to_dict()}, 201
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def login_user_by_email(email, password):
        try:
            if not email or not password:
                return {'error': 'Vui lòng điền đầy đủ thông tin'}, 400

            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                return {'error': 'Email hoặc mật khẩu không đúng'}, 401
            if not user.is_active:
                return {'error': 'Tài khoản đã bị vô hiệu hóa'}, 403

            access_token = create_access_token(identity=user.id)
            refresh_token = create_refresh_token(identity=user.id)
            return {
                'message': 'Đăng nhập thành công',
                'access_token': access_token,
                'refresh_token': refresh_token,
                'token': access_token,
                'user': user.to_dict(),
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def logout_user(token_data: dict):
        jti = token_data['jti']
        expires_at = token_data['exp']
        now = datetime.now().timestamp()
        time_left = math.ceil(expires_at - now)
        if time_left > 0:
            cache.set(jti, 1, ex=time_left)
        return {'message': 'Đăng xuất thành công'}, 200

    @staticmethod
    def request_password_reset(email):
        if not email or not validate_email(email):
            return {'error': 'Email không hợp lệ'}, 400

        user = User.query.filter_by(email=email).first()
        if user:
            # Generate token and store in Redis
            token = str(uuid.uuid4())
            # TTL 1 hour
            cache.set(f'reset_token:{token}', user.id, ex=3600)

            # Send Email
            try:
                # Frontend URL should be configurable, for now assume localhost:5173
                reset_url = f"http://localhost:5173/reset-password?token={token}"
                msg = Message(
                    "Đặt lại mật khẩu - Du lịch Khánh Hòa",
                    recipients=[email],
                    body=f"Xin chào {user.fullname},\n\nĐể đặt lại mật khẩu của bạn, vui lòng nhấp vào liên kết sau (có hiệu lực trong 1 giờ):\n{reset_url}\n\nNếu bạn không yêu cầu điều này, hãy bỏ qua email này."
                )
                mail.send(msg)
            except Exception as e:
                current_app.logger.error(f"Failed to send reset email: {e}")
                # We still return 200 for security reasons to not leak email existence,
                # but might want to log this.

        return {
            'message': 'Nếu email tồn tại trong hệ thống, hướng dẫn đặt lại mật khẩu sẽ được gửi tới hộp thư của bạn.'
        }, 200

    @staticmethod
    def reset_password(token, new_password):
        try:
            if not token:
                return {'error': 'Token không hợp lệ'}, 400

            user_id = cache.get(f'reset_token:{token}')
            if not user_id:
                return {'error': 'Token đã hết hạn hoặc không hợp lệ'}, 400

            is_valid, error_msg = validate_password(new_password)
            if not is_valid:
                return {'error': error_msg}, 400

            user = User.query.get(user_id)
            if not user:
                return {'error': 'Người dùng không tồn tại'}, 404

            user.set_password(new_password)
            db.session.commit()

            # Invalidate token
            cache.delete(f'reset_token:{token}')

            return {'message': 'Đổi mật khẩu thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def change_password(user, old_password, new_password):
        try:
            if not old_password or not new_password:
                return {'error': 'Vui lòng điền đầy đủ thông tin'}, 400
            if not user.check_password(old_password):
                return {'error': 'Mật khẩu cũ không đúng'}, 400

            is_valid, error_msg = validate_password(new_password)
            if not is_valid:
                return {'error': error_msg}, 400

            user.set_password(new_password)
            db.session.commit()
            return {'message': 'Đổi mật khẩu thành công'}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def update_profile(user, data):
        try:
            if 'fullname' in data:
                user.fullname = data['fullname']
            if 'full_name' in data:
                user.fullname = data['full_name']
            if 'phone' in data:
                user.phone = data['phone']
            if 'avatar' in data:
                user.avatar = data['avatar']
            if 'avatar_url' in data:
                user.avatar = data['avatar_url']

            db.session.commit()
            return {'message': 'Cập nhật thành công', 'user': user.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500


def get_auth_service():
    return AuthService()

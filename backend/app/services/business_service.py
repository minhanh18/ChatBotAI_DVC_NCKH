from app import db
from app.models.business_registration import BusinessRegistration
from app.models.user import User
from app.utils.helpers import save_uploaded_file, paginate_query
from app.services.mail_service import send_business_approval_email, send_business_rejection_email
from flask import current_app


class BusinessService:
    @staticmethod
    def register_business(user_id, data, files):
        try:
            business_name = data.get('business_name')
            tax_code = data.get('tax_code')
            headquarters_address = data.get('headquarters_address')
            representative_name = data.get('representative_name')
            business_type = data.get('business_type')
            description = data.get('description')

            if not all([business_name, tax_code, headquarters_address, representative_name, business_type]):
                return {'error': 'Vui lòng điền đầy đủ thông tin bắt buộc'}, 400

            # Handle file uploads
            business_license = files.get('business_license')
            representative_id_front = files.get('representative_id_front')
            representative_id_back = files.get('representative_id_back')

            if not business_license or not representative_id_front or not representative_id_back:
                return {'error': 'Vui lòng tải lên đầy đủ hồ sơ pháp lý'}, 400

            license_url = save_uploaded_file(business_license, folder='business_docs')
            id_front_url = save_uploaded_file(representative_id_front, folder='business_docs')
            id_back_url = save_uploaded_file(representative_id_back, folder='business_docs')

            if not all([license_url, id_front_url, id_back_url]):
                return {'error': 'Lỗi khi tải lên tệp tin. Vui lòng thử lại.'}, 500

            registration = BusinessRegistration(
                user_id=user_id,
                business_name=business_name,
                tax_code=tax_code,
                headquarters_address=headquarters_address,
                representative_name=representative_name,
                business_license_url=license_url,
                representative_id_front_url=id_front_url,
                representative_id_back_url=id_back_url,
                business_type=business_type,
                description=description
            )

            db.session.add(registration)
            db.session.commit()

            return {'message': 'Gửi đơn đăng ký thành công. Vui lòng chờ admin phê duyệt.', 'registration': registration.to_dict()}, 201
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500

    @staticmethod
    def get_registrations(params):
        try:
            page = int(params.get('page', 1))
            per_page = int(params.get('per_page', 20))
            status = params.get('status')

            query = BusinessRegistration.query
            if status:
                query = query.filter(BusinessRegistration.status == status)

            pagination = paginate_query(query.order_by(BusinessRegistration.created_at.desc()), page=page, per_page=per_page)

            return {
                'registrations': [r.to_dict() for r in pagination['items']],
                'total': pagination['total'],
                'pages': pagination['pages'],
                'current_page': pagination['current_page']
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def get_registration_detail(registration_id):
        try:
            registration = BusinessRegistration.query.get_or_404(registration_id)
            return registration.to_dict(), 200
        except Exception as e:
            return {'error': str(e)}, 500

    @staticmethod
    def process_registration(registration_id, status, admin_notes=None):
        try:
            if status not in ['APPROVED', 'REJECTED']:
                return {'error': 'Trạng thái không hợp lệ'}, 400

            registration = BusinessRegistration.query.get_or_404(registration_id)
            registration.status = status
            registration.admin_notes = admin_notes

            # ── Assign BUSINESS role when approved ──
            if status == 'APPROVED':
                user = User.query.get(registration.user_id)
                if user and user.role not in ('ADMIN',):
                    user.role = 'BUSINESS'

            db.session.commit()

            # ── Send email notification (non-blocking) ──
            try:
                user = User.query.get(registration.user_id)
                if user:
                    if status == 'APPROVED':
                        send_business_approval_email(
                            to=user.email,
                            business_name=registration.business_name,
                            representative_name=registration.representative_name,
                            admin_notes=admin_notes
                        )
                    else:
                        send_business_rejection_email(
                            to=user.email,
                            business_name=registration.business_name,
                            representative_name=registration.representative_name,
                            admin_notes=admin_notes
                        )
            except Exception as mail_err:
                current_app.logger.warning(f'Email notification failed: {mail_err}')
                # Don't fail the whole request just because email failed

            msg = 'Đã phê duyệt doanh nghiệp' if status == 'APPROVED' else 'Đã từ chối đơn đăng ký'
            return {'message': msg, 'registration': registration.to_dict()}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500


def get_business_service():
    return BusinessService()

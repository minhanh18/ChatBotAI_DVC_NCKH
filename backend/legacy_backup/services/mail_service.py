from flask import current_app, render_template_string
from flask_mail import Message
from app import mail


def send_mail(to: str, subject: str, html_body: str, text_body: str = None):
    """Send an email using Flask-Mail.

    Args:
        to: Recipient email address
        subject: Email subject line
        html_body: HTML content of the email
        text_body: Plain-text fallback (optional)
    """
    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') or current_app.config.get('MAIL_USERNAME')
        msg = Message(subject=subject, recipients=[to], sender=sender)
        msg.html = html_body
        if text_body:
            msg.body = text_body
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f'Failed to send email to {to}: {e}')
        return False


def send_business_approval_email(to: str, business_name: str, representative_name: str, admin_notes: str = None):
    """Notify a business owner that their registration was APPROVED."""
    subject = f'[Du lịch Khánh Hòa] Đơn đăng ký doanh nghiệp đã được duyệt!'
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f9fafb; padding: 32px; border-radius: 16px;">
        <div style="background: #2563eb; border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">🎉 Chúc mừng!</h1>
        </div>
        <div style="background: #ffffff; border-radius: 12px; padding: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.06);">
            <p style="font-size: 16px; color: #374151;">Xin chào <strong>{representative_name}</strong>,</p>
            <p style="color: #374151;">Đơn đăng ký doanh nghiệp <strong>"{business_name}"</strong> của bạn đã được <span style="color: #16a34a; font-weight: bold;">phê duyệt</span>.</p>
            <p style="color: #374151;">Tài khoản của bạn đã được nâng cấp lên <strong>Tài khoản Doanh nghiệp</strong>. Bạn có thể đăng nhập và sử dụng tính năng quản lý đặt chỗ ngay bây giờ.</p>
            {"<div style='background:#f0fdf4; border: 1px solid #bbf7d0; border-radius:8px; padding:16px; margin:16px 0;'><strong style='color:#15803d;'>Ghi chú từ Admin:</strong><p style='color:#374151; margin:4px 0 0;'>" + admin_notes + "</p></div>" if admin_notes else ""}
            <div style="text-align: center; margin-top: 24px;">
                <a href="http://localhost:5173/business" style="background: #2563eb; color: #fff; text-decoration: none; padding: 12px 28px; border-radius: 8px; font-weight: bold; display: inline-block;">Quản lý đặt chỗ ngay</a>
            </div>
        </div>
        <p style="text-align: center; color: #9ca3af; font-size: 12px; margin-top: 16px;">Du lịch Khánh Hòa – khanhhoa-travel.vn</p>
    </div>
    """
    return send_mail(to=to, subject=subject, html_body=html)


def send_business_rejection_email(to: str, business_name: str, representative_name: str, admin_notes: str = None):
    """Notify a business owner that their registration was REJECTED."""
    subject = f'[Du lịch Khánh Hòa] Thông báo về đơn đăng ký doanh nghiệp'
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f9fafb; padding: 32px; border-radius: 16px;">
        <div style="background: #dc2626; border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">Thông báo kết quả xét duyệt</h1>
        </div>
        <div style="background: #ffffff; border-radius: 12px; padding: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.06);">
            <p style="font-size: 16px; color: #374151;">Xin chào <strong>{representative_name}</strong>,</p>
            <p style="color: #374151;">Rất tiếc, đơn đăng ký doanh nghiệp <strong>"{business_name}"</strong> của bạn đã <span style="color: #dc2626; font-weight: bold;">không được phê duyệt</span> trong lần xét duyệt này.</p>
            {"<div style='background:#fef2f2; border: 1px solid #fecaca; border-radius:8px; padding:16px; margin:16px 0;'><strong style='color:#b91c1c;'>Lý do từ Admin:</strong><p style='color:#374151; margin:4px 0 0;'>" + admin_notes + "</p></div>" if admin_notes else ""}
            <p style="color: #374151;">Bạn có thể bổ sung và nộp lại hồ sơ sau khi đã hoàn thiện các yêu cầu trên.</p>
            <div style="text-align: center; margin-top: 24px;">
                <a href="http://localhost:5173/register-business" style="background: #374151; color: #fff; text-decoration: none; padding: 12px 28px; border-radius: 8px; font-weight: bold; display: inline-block;">Đăng ký lại</a>
            </div>
        </div>
        <p style="text-align: center; color: #9ca3af; font-size: 12px; margin-top: 16px;">Du lịch Khánh Hòa – khanhhoa-travel.vn</p>
    </div>
    """
    return send_mail(to=to, subject=subject, html_body=html)
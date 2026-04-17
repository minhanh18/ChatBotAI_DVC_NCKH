from datetime import datetime
from uuid import uuid4

from app import db


def generate_uuid():
    return uuid4().hex


class Booking(db.Model):
    __tablename__ = 'bookings'

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)

    # Which business registration this booking belongs to
    business_registration_id = db.Column(
        db.String(36),
        db.ForeignKey('business_registrations.id'),
        nullable=False
    )

    # The user who made this booking
    customer_user_id = db.Column(
        db.String(36),
        db.ForeignKey('users.id'),
        nullable=False
    )

    # Customer info explicitly provided during booking
    customer_name = db.Column(db.String(100), nullable=False, server_default='')
    customer_phone = db.Column(db.String(20), nullable=False, server_default='')

    # Type of service being booked
    service_type = db.Column(
        db.Enum('ROOM', 'TABLE', 'SEAT', name='booking_service_types'),
        nullable=False,
        default='TABLE'
    )

    booking_date = db.Column(db.Date, nullable=False)
    time_slot = db.Column(db.String(50))          # e.g. "18:00 - 20:00"
    guest_count = db.Column(db.Integer, default=1)
    notes = db.Column(db.Text)

    status = db.Column(
        db.Enum('PENDING', 'CONFIRMED', 'CANCELLED', name='booking_status'),
        default='PENDING',
        nullable=False
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    business = db.relationship(
        'BusinessRegistration',
        backref=db.backref('bookings', lazy=True)
    )
    customer = db.relationship(
        'User',
        backref=db.backref('bookings', lazy=True)
    )

    def to_dict(self):
        return {
            'id': self.id,
            'business_registration_id': self.business_registration_id,
            'customer_user_id': self.customer_user_id,
            'customer_name': self.customer_name,
            'customer_phone': self.customer_phone,
            'service_type': self.service_type,
            'booking_date': self.booking_date.isoformat() if self.booking_date else None,
            'time_slot': self.time_slot,
            'guest_count': self.guest_count,
            'notes': self.notes,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'business': {
                'id': self.business.id,
                'business_name': self.business.business_name,
                'business_type': self.business.business_type,
                'headquarters_address': self.business.headquarters_address,
            } if self.business else None,
            'customer': {
                'id': self.customer.id,
                'fullname': self.customer.fullname,
                'email': self.customer.email,
                'phone': self.customer.phone,
            } if self.customer else None,
        }

    def __repr__(self):
        return f'<Booking {self.id} - {self.status}>'

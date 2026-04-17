from datetime import datetime
from uuid import uuid4
from app import db

def generate_uuid():
    return uuid4().hex

class BusinessRegistration(db.Model):
    __tablename__ = 'business_registrations'

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    
    # Basic Info
    business_name = db.Column(db.String(255), nullable=False)
    tax_code = db.Column(db.String(50), nullable=False)
    headquarters_address = db.Column(db.String(255), nullable=False)
    representative_name = db.Column(db.String(100), nullable=False)
    
    # Legal Documents (URLs to uploaded files)
    business_license_url = db.Column(db.String(255), nullable=False)
    representative_id_front_url = db.Column(db.String(255), nullable=False)
    representative_id_back_url = db.Column(db.String(255), nullable=False)
    
    # Service Info
    business_type = db.Column(db.Enum('HOTEL', 'RESTAURANT', 'ATTRACTION', name='business_types'), nullable=False)
    description = db.Column(db.Text)
    
    # Status Management
    status = db.Column(db.Enum('PENDING', 'APPROVED', 'REJECTED', name='registration_status'), default='PENDING')
    admin_notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = db.relationship('User', backref=db.backref('business_registrations', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'business_name': self.business_name,
            'tax_code': self.tax_code,
            'headquarters_address': self.headquarters_address,
            'representative_name': self.representative_name,
            'business_license_url': self.business_license_url,
            'representative_id_front_url': self.representative_id_front_url,
            'representative_id_back_url': self.representative_id_back_url,
            'business_type': self.business_type,
            'description': self.description,
            'status': self.status,
            'admin_notes': self.admin_notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'user': self.user.to_dict() if self.user else None
        }

    def __repr__(self):
        return f'<BusinessRegistration {self.business_name}>'

from datetime import datetime
from app import db


class SystemStatistic(db.Model):
    """System analytics and reports"""
    __tablename__ = 'system_statistics'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, default=datetime.utcnow().date(), index=True)
    total_users = db.Column(db.Integer, default=0)
    total_chats = db.Column(db.Integer, default=0)
    most_visited_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat(),
            'total_users': self.total_users,
            'total_chats': self.total_chats,
            'most_visited_location_id': self.most_visited_location_id
        }

from datetime import datetime
from app import db
from uuid import uuid4

def generateUUID():
    return uuid4().hex

class PostImage(db.Model):
    __tablename__ = 'post_images'

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String(36), db.ForeignKey('posts.id'), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    order = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'post_id': self.post_id,
            'image_url': self.image_url,
            'order': self.order
        }

class Post(db.Model):
    __tablename__ = 'posts'
    
    id = db.Column(db.String(36), primary_key=True, default=generateUUID)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(255))  # kept for backward compat (first image)
    author_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    images = db.relationship('PostImage', backref='post', lazy='dynamic',
                              cascade='all, delete-orphan',
                              order_by='PostImage.order')
    comments = db.relationship('Comment', backref='post', lazy='dynamic', cascade='all, delete-orphan')
    likes = db.relationship('Like', backref='post', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self, user_id=None):
        user_liked = False
        if user_id:
            user_liked = Like.query.filter_by(post_id=self.id, user_id=user_id).first() is not None

        images_list = [img.to_dict() for img in self.images.order_by(PostImage.order).all()]
        # Backward compat: if no PostImage rows but image_url exists, create synthetic entry
        if not images_list and self.image_url:
            images_list = [{'id': None, 'post_id': self.id, 'image_url': self.image_url, 'order': 0}]

        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'image_url': images_list[0]['image_url'] if images_list else self.image_url,
            'images': images_list,
            'author_id': self.author_id,
            'author_name': self.author.fullname if self.author else "Unknown",
            'likes_count': self.likes.count(),
            'comments_count': self.comments.count(),
            'user_liked': user_liked,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

class Comment(db.Model):
    __tablename__ = 'post_comments'
    
    id = db.Column(db.String(36), primary_key=True, default=generateUUID)
    post_id = db.Column(db.String(36), db.ForeignKey('posts.id'), nullable=False)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    parent_id = db.Column(db.String(36), db.ForeignKey('post_comments.id'), nullable=True) # For nested replies
    content = db.Column(db.Text, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships for nested replies
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy='dynamic', cascade='all, delete-orphan')
    likes = db.relationship('CommentLike', backref='comment', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self, user_id=None):
        user_liked = False
        if user_id:
            user_liked = CommentLike.query.filter_by(comment_id=self.id, user_id=user_id).first() is not None

        return {
            'id': self.id,
            'post_id': self.post_id,
            'user_id': self.user_id,
            'user_name': self.user.fullname if self.user else "Unknown",
            'user_avatar': self.user.avatar if self.user else None,
            'parent_id': self.parent_id,
            'content': self.content,
            'likes_count': self.likes.count(),
            'user_liked': user_liked,
            'created_at': self.created_at.isoformat()
        }

class CommentLike(db.Model):
    __tablename__ = 'comment_likes'
    
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.String(36), db.ForeignKey('post_comments.id'), nullable=False)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('comment_id', 'user_id', name='_comment_user_like_uc'),)

class Like(db.Model):
    __tablename__ = 'post_likes'
    
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String(36), db.ForeignKey('posts.id'), nullable=False)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('post_id', 'user_id', name='_post_user_like_uc'),)

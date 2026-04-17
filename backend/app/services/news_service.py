from app.models.post import Post, PostImage, Comment, Like, CommentLike
from app import db
from datetime import datetime
import os
from werkzeug.utils import secure_filename
from flask import current_app

MAX_IMAGES = 5

class NewsService:
    @staticmethod
    def _normalize_image_url(value):
        raw = (value or '').strip()
        if not raw:
            return None

        if 'drive.google.com/file/d/' in raw:
            try:
                file_id = raw.split('/file/d/')[1].split('/')[0]
                return f'https://drive.google.com/uc?export=view&id={file_id}'
            except IndexError:
                return raw

        if 'drive.google.com/open?id=' in raw:
            try:
                file_id = raw.split('open?id=')[1].split('&')[0]
                return f'https://drive.google.com/uc?export=view&id={file_id}'
            except IndexError:
                return raw

        if raw.startswith('www.'):
            return f'https://{raw}'

        if raw.startswith('uploads/') or raw.startswith('static/'):
            return f'/{raw}'

        return raw

    @staticmethod
    def get_posts(params):
        query = Post.query

        page = params.get('page', 1)
        per_page = params.get('per_page', 10)
        search = (params.get('search') or '').strip()

        if search:
            from sqlalchemy import or_
            term = f'%{search}%'
            query = query.filter(or_(
                Post.title.ilike(term),
                Post.content.ilike(term),
            ))

        posts_pagination = query.order_by(Post.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)

        return {
            'posts': [post.to_dict() for post in posts_pagination.items],
            'total': posts_pagination.total,
            'pages': posts_pagination.pages,
            'current_page': posts_pagination.page
        }, 200

    @staticmethod
    def get_post(post_id, user_id=None):
        post = Post.query.get(post_id)
        if not post:
            return {'error': 'Không tìm thấy bài viết'}, 404

        post_data = post.to_dict(user_id=user_id)

        # Get top-level comments and nest their replies
        top_level_comments = (
            post.comments.filter_by(parent_id=None)
            .order_by(Comment.created_at.asc()).all()
        )

        def serialize_comment(c):
            cdict = c.to_dict(user_id=user_id)
            cdict['replies'] = [
                serialize_comment(r)
                for r in c.replies.order_by(Comment.created_at.asc()).all()
            ]
            # interaction_score: combinations of likes and replies count
            cdict['interaction_score'] = cdict['likes_count'] + len(cdict['replies'])
            return cdict

        serialized_comments = [serialize_comment(c) for c in top_level_comments]
        serialized_comments.sort(key=lambda x: (x.get('interaction_score', 0), x['created_at']), reverse=True)

        post_data['comments'] = serialized_comments

        return post_data, 200

    @staticmethod
    def create_post(user_id, data):
        title = (data.get('title') or '').strip()
        content = (data.get('content') or '').strip()
        image_url = NewsService._normalize_image_url(data.get('image_url'))

        if not title or not content:
            return {'error': 'Tiêu đề và nội dung là bắt buộc'}, 400

        # Collect uploaded images (support single 'image' or multiple 'images[]')
        uploaded_files = files.getlist('images[]') if hasattr(files, 'getlist') else []
        if not uploaded_files:
            single = files.get('image')
            if single and getattr(single, 'filename', ''):
                uploaded_files = [single]

        # Cap at MAX_IMAGES
        uploaded_files = [f for f in uploaded_files if getattr(f, 'filename', '')][:MAX_IMAGES]

        if len(uploaded_files) > MAX_IMAGES:
            return {'error': f'Chỉ được upload tối đa {MAX_IMAGES} ảnh'}, 400

        # Validate all files first
        for f in uploaded_files:
            if not NewsService.allowed_file(f.filename):
                return {'error': 'Định dạng ảnh không hợp lệ'}, 400

        # Save files and track URLs
        image_urls = []
        for f in uploaded_files:
            url = NewsService.save_image(f)
            if url:
                image_urls.append(url)

        # image_url = first image (backward compat)
        image_url = image_urls[0] if image_urls else data.get('image_url')

        post = Post(
            title=title,
            content=content,
            image_url=image_url,
            author_id=user_id
        )
        db.session.add(post)
        db.session.flush()  # get post.id before adding images

        for i, url in enumerate(image_urls):
            post_image = PostImage(post_id=post.id, image_url=url, order=i)
            db.session.add(post_image)

        db.session.commit()

        return post.to_dict(user_id=user_id), 201

    @staticmethod
    def add_comment(post_id, user_id, data):
        content = data.get('content')
        parent_id = data.get('parent_id')

        if not content:
            return {'error': 'Nội dung bình luận là bắt buộc'}, 400

        post = Post.query.get(post_id)
        if not post:
            return {'error': 'Không tìm thấy bài viết'}, 404

        # Validate parent comment belongs to this post
        if parent_id:
            parent = Comment.query.get(parent_id)
            if not parent or parent.post_id != post_id:
                return {'error': 'Bình luận gốc không hợp lệ'}, 400

        comment = Comment(
            post_id=post_id,
            user_id=user_id,
            content=content,
            parent_id=parent_id
        )

        db.session.add(comment)
        db.session.commit()

        comment_dict = comment.to_dict(user_id=user_id)
        comment_dict['replies'] = []
        return comment_dict, 201

    @staticmethod
    def toggle_comment_like(comment_id, user_id):
        comment = Comment.query.get(comment_id)
        if not comment:
            return {'error': 'Không tìm thấy bình luận'}, 404

        like = CommentLike.query.filter_by(comment_id=comment_id, user_id=user_id).first()
        if like:
            db.session.delete(like)
            db.session.commit()
            return {
                'message': 'Đã bỏ thích bình luận',
                'liked': False,
                'likes_count': comment.likes.count()
            }, 200
        else:
            like = CommentLike(comment_id=comment_id, user_id=user_id)
            db.session.add(like)
            db.session.commit()
            return {
                'message': 'Đã thích bình luận',
                'liked': True,
                'likes_count': comment.likes.count()
            }, 201

    @staticmethod
    def toggle_like(post_id, user_id):
        post = Post.query.get(post_id)
        if not post:
            return {'error': 'Không tìm thấy bài viết'}, 404

        like = Like.query.filter_by(post_id=post_id, user_id=user_id).first()
        if like:
            db.session.delete(like)
            db.session.commit()
            return {
                'message': 'Đã bỏ thích',
                'liked': False,
                'likes_count': post.likes.count()
            }, 200
        else:
            like = Like(post_id=post_id, user_id=user_id)
            db.session.add(like)
            db.session.commit()
            return {
                'message': 'Đã thích',
                'liked': True,
                'likes_count': post.likes.count()
            }, 201

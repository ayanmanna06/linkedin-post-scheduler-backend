import base64
import os
import uuid
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, Field, field_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from backend.db import get_db_session
from backend.models import LinkedInConnection
from backend.services.exceptions import ConflictError, ValidationError
from backend.services.post_service import post_service

# --- Request Validation Schemas ---

class MediaItemSchema(BaseModel):
    name: str
    type: str | None = None
    size: int | None = None
    data_url: str | None = None


class PostCreateSchema(BaseModel):
    content: str = Field(..., min_length=1, max_length=3000, description="Post body text")
    scheduled_at: str = Field(..., description="ISO 8601 formatted datetime string")
    timezone: str = Field(default="Asia/Kolkata", description="IANA timezone name")
    source: str = Field(default="manual", description="'manual' or 'ai'")
    ai_brief: dict[str, Any] | None = None
    media: list[MediaItemSchema] | None = None

    @field_validator("content")
    @classmethod
    def validate_content_not_empty(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Post content cannot be empty.")
        return trimmed

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        val = v.lower()
        if val not in ("manual", "ai"):
            raise ValueError("Source must be either 'manual' or 'ai'.")
        return val


class PostUpdateSchema(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=3000)
    scheduled_at: str | None = None
    timezone: str = Field(default="Asia/Kolkata")

    @field_validator("content")
    @classmethod
    def validate_content_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            trimmed = v.strip()
            if not trimmed:
                raise ValueError("Post content cannot be empty.")
            return trimmed
        return v


# --- Controller ---

class PostController:
    @staticmethod
    def create_post(user_id: int):
        data = request.get_json(silent=True) or {}
        try:
            schema = PostCreateSchema(**data)
        except PydanticValidationError as e:
            raise ValidationError("Invalid post payload.", details=e.errors()) from e

        with get_db_session() as session:
            stmt = select(LinkedInConnection).where(LinkedInConnection.user_id == user_id)
            conn = session.execute(stmt).scalar_one_or_none()
            if not conn:
                raise ConflictError("Please connect your LinkedIn account before scheduling posts.")
            if conn.is_expired:
                raise ConflictError("LinkedIn token expired, please reconnect your account.")

            # Persist media attachments to disk if provided
            saved_media = []
            if schema.media:
                upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
                os.makedirs(upload_dir, exist_ok=True)
                for item in schema.media:
                    file_name = item.name or "attachment"
                    safe_name = f"{uuid.uuid4().hex[:12]}_{os.path.basename(file_name)}"
                    file_path = os.path.join(upload_dir, safe_name)
                    if item.data_url and "base64," in item.data_url:
                        raw_b64 = item.data_url.split("base64,", 1)[1]
                        file_bytes = base64.b64decode(raw_b64)
                        with open(file_path, "wb") as f:
                            f.write(file_bytes)
                        saved_media.append({
                            "name": file_name,
                            "type": item.type or "application/octet-stream",
                            "size": len(file_bytes),
                            "file_path": file_path,
                        })
                    elif item.data_url:
                        saved_media.append({
                            "name": file_name,
                            "type": item.type or "application/octet-stream",
                            "size": item.size or 0,
                            "file_path": item.data_url,
                        })

            ai_brief = schema.ai_brief or {}
            if saved_media:
                ai_brief["media_items"] = saved_media
                ai_brief["media_count"] = len(saved_media)

            post = post_service.create_post(
                session=session,
                user_id=user_id,
                content=schema.content,
                scheduled_at_str=schema.scheduled_at,
                timezone_name=schema.timezone,
                source=schema.source,
                ai_brief=ai_brief,
            )
            return jsonify({"data": post.to_dict()}), 201

    @staticmethod
    def list_posts(user_id: int):
        status = request.args.get("status")
        search = request.args.get("search")
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 10, type=int)

        with get_db_session() as session:
            posts, total = post_service.list_posts(
                session=session,
                user_id=user_id,
                status=status,
                search=search,
                page=page,
                per_page=per_page,
            )
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1
            return jsonify(
                {
                    "data": {
                        "items": [p.to_dict() for p in posts],
                        "pagination": {
                            "page": page,
                            "per_page": per_page,
                            "total_items": total,
                            "total_pages": total_pages,
                        },
                    }
                }
            )

    @staticmethod
    def get_post(user_id: int, post_id: int):
        with get_db_session() as session:
            post = post_service.get_post(session, post_id, user_id)
            return jsonify({"data": post.to_dict(include_attempts=True)})

    @staticmethod
    def update_post(user_id: int, post_id: int):
        data = request.get_json(silent=True) or {}
        try:
            schema = PostUpdateSchema(**data)
        except PydanticValidationError as e:
            raise ValidationError("Invalid update payload.", details=e.errors()) from e

        with get_db_session() as session:
            post = post_service.update_post(
                session=session,
                post_id=post_id,
                user_id=user_id,
                content=schema.content,
                scheduled_at_str=schema.scheduled_at,
                timezone_name=schema.timezone,
            )
            return jsonify({"data": post.to_dict()})

    @staticmethod
    def cancel_post(user_id: int, post_id: int):
        with get_db_session() as session:
            post = post_service.cancel_post(session, post_id, user_id)
            return jsonify({"data": post.to_dict()})

    @staticmethod
    def get_attempts(user_id: int, post_id: int):
        with get_db_session() as session:
            attempts = post_service.get_post_attempts(session, post_id, user_id)
            return jsonify({"data": attempts})

    @staticmethod
    def get_metrics(user_id: int):
        with get_db_session() as session:
            metrics = post_service.get_metrics(session, user_id)
            return jsonify({"data": metrics})

    @staticmethod
    def get_suggested_topics(user_id: int):
        with get_db_session() as session:
            from backend.models import Post
            stmt = (
                select(Post)
                .where(Post.user_id == user_id)
                .order_by(Post.created_at.desc())
                .limit(10)
            )
            user_posts = session.execute(stmt).scalars().all()

            suggestions = []
            seen_topics = set()

            has_hiring = False
            has_database = False
            has_tech = False
            has_leadership = False

            import re
            from collections import Counter

            user_hashtags = []
            for p in user_posts:
                text = (p.content or "").lower()
                brief = p.ai_brief or {}
                b_topic = brief.get("topic")
                if b_topic and b_topic not in seen_topics:
                    seen_topics.add(b_topic)
                    display_topic = b_topic if len(b_topic) <= 40 else b_topic[:38] + "..."
                    suggestions.append({
                        "label": f"Recent Theme: {display_topic}",
                        "topic": b_topic,
                        "tone": brief.get("tone", "professional"),
                        "keywords": brief.get("keywords", []),
                    })

                # Collect hashtags
                if p.content:
                    tags = re.findall(r"#\w+", p.content)
                    user_hashtags.extend(tags)

                if any(w in text for w in ["hire", "hiring", "engineer", "developer", "role", "job"]):
                    has_hiring = True
                if any(w in text for w in ["mysql", "database", "sql", "postgres", "redis"]):
                    has_database = True
                if any(w in text for w in ["architect", "scale", "system", "performance", "code"]):
                    has_tech = True
                if any(w in text for w in ["leader", "culture", "team", "growth", "mentor"]):
                    has_leadership = True

            # If user has frequently used hashtags, suggest a post building on their top tags
            if user_hashtags:
                top_tags = [tag for tag, _ in Counter(user_hashtags).most_common(3)]
                top_tag_topic = f"Insights on {', '.join(top_tags)}"
                if top_tag_topic not in seen_topics:
                    suggestions.append({
                        "label": f"Frequent Tags: {' '.join(top_tags)}",
                        "topic": f"Key Lessons & Industry Best Practices in {', '.join(top_tags)}",
                        "tone": "thought-leadership",
                        "keywords": top_tags,
                    })

            if has_hiring or len(suggestions) < 2:
                suggestions.append({
                    "label": "🚀 Hiring: Senior Full Stack Engineer (Remote)",
                    "topic": "Hiring Senior Full Stack Engineer (React, Python, Cloud) — Remote / Hybrid",
                    "tone": "hiring",
                    "keywords": ["#Hiring", "#TechJobs", "#FullStack", "#SoftwareEngineering"],
                })

            if has_database or len(suggestions) < 3:
                suggestions.append({
                    "label": "💡 5 Architecture Lessons from Scaling MySQL",
                    "topic": "5 Architecture Lessons from Scaling MySQL with Zero Downtime",
                    "tone": "thought-leadership",
                    "keywords": ["#SystemDesign", "#MySQL", "#Backend", "#Architecture"],
                })

            if has_tech or len(suggestions) < 4:
                suggestions.append({
                    "label": "📈 99.99% Queue Reliability with Atomic Row Locks",
                    "topic": "How We Achieved 99.99% Queue Reliability with MySQL Row-Level Locking and Idempotency",
                    "tone": "thought-leadership",
                    "keywords": ["#Reliability", "#SoftwareEngineering", "#SystemDesign", "#Cloud"],
                })

            if has_leadership:
                suggestions.append({
                    "label": "🎯 Engineering Leadership: Scaling Teams without Breaking Culture",
                    "topic": "Key Lessons from Scaling Engineering Teams and Maintaining High Ownership",
                    "tone": "thought-leadership",
                    "keywords": ["#Leadership", "#Engineering", "#Culture", "#Management"],
                })
            else:
                suggestions.append({
                    "label": "🤖 Building Production AI Workflows with Open Source LLMs",
                    "topic": "Deploying Self-Hosted LLMs in Production: Cost, Latency, and Architecture Lessons",
                    "tone": "professional",
                    "keywords": ["#ArtificialIntelligence", "#MachineLearning", "#LLM", "#TechInnovation"],
                })

            return jsonify({"data": suggestions[:5]})


post_controller = PostController()

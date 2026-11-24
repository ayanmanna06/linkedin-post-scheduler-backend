from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.services.state_machine import PostStatus

__all__ = [
    "Base",
    "User",
    "LinkedInConnection",
    "Post",
    "PostAttempt",
    "PostSource",
    "AttemptOutcome",
    "PostStatus",
]


def utc_now() -> datetime:
    """Returns naive UTC datetime for MySQL storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class PostSource(str, Enum):
    MANUAL = "manual"
    AI = "ai"


class AttemptOutcome(str, Enum):
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    FATAL_FAILURE = "fatal_failure"
    UNCERTAIN = "uncertain"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    linkedin_sub: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    connection: Mapped["LinkedInConnection | None"] = relationship(
        "LinkedInConnection",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    posts: Mapped[list["Post"]] = relationship(
        "Post",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(Post.created_at)",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "linkedin_sub": self.linkedin_sub,
            "display_name": self.display_name,
            "email": self.email,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


class LinkedInConnection(Base):
    __tablename__ = "linkedin_connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    author_urn: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scopes: Mapped[str] = mapped_column(
        String(512), nullable=False, default="openid profile w_member_social"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="connection")

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return True
        return (self.expires_at - utc_now()).total_seconds() <= 60

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "author_urn": self.author_urn,
            "expires_at": self.expires_at.isoformat() + "Z" if self.expires_at else None,
            "is_expired": self.is_expired,
            "scopes": self.scopes,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        SQLEnum(PostSource, values_callable=lambda x: [e.value for e in x]),
        default=PostSource.MANUAL.value,
        nullable=False,
    )
    ai_brief: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False, index=True)
    scheduled_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    linkedin_post_urn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="posts")
    attempts: Mapped[list["PostAttempt"]] = relationship(
        "PostAttempt",
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="PostAttempt.attempt_no.asc()",
    )

    __table_args__ = (
        Index("ix_posts_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_posts_user_id_status", "user_id", "status"),
    )

    def to_dict(self, include_attempts: bool = False) -> dict:
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "content": self.content,
            "source": self.source,
            "ai_brief": self.ai_brief,
            "status": self.status,
            "scheduled_at_utc": self.scheduled_at_utc.isoformat() + "Z" if self.scheduled_at_utc else None,
            "next_attempt_at": self.next_attempt_at.isoformat() + "Z" if self.next_attempt_at else None,
            "attempt_count": self.attempt_count,
            "idempotency_key": self.idempotency_key,
            "linkedin_post_urn": self.linkedin_post_urn,
            "last_error": self.last_error,
            "locked_at": self.locked_at.isoformat() + "Z" if self.locked_at else None,
            "locked_by": self.locked_by,
            "published_at": self.published_at.isoformat() + "Z" if self.published_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }
        if include_attempts:
            data["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        return data


class PostAttempt(Base):
    __tablename__ = "post_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(
        SQLEnum(AttemptOutcome, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    # Relationship
    post: Mapped["Post"] = relationship("Post", back_populates="attempts")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "post_id": self.post_id,
            "attempt_no": self.attempt_no,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import AttemptOutcome, Post, PostAttempt, PostSource
from backend.services.exceptions import ConflictError, NotFoundError, ValidationError
from backend.services.state_machine import PostStatus, validate_post_transition
from backend.services.time_utils import parse_iso_to_utc, utc_now


class PostService:
    @staticmethod
    def validate_content(content: str) -> str:
        trimmed = content.strip()
        if not trimmed:
            raise ValidationError("Post content cannot be empty.")
        if len(trimmed) > 3000:
            raise ValidationError(
                f"Post content exceeds 3000 characters (length: {len(trimmed)})."
            )
        return trimmed

    @staticmethod
    def validate_scheduled_time(scheduled_at_utc: datetime) -> None:
        now = utc_now()
        if scheduled_at_utc <= now:
            raise ValidationError(
                "Scheduled time must be in the future.",
                details={"scheduled_at_utc": scheduled_at_utc.isoformat(), "now_utc": now.isoformat()},
            )

    def create_post(
        self,
        session: Session,
        user_id: int,
        content: str,
        scheduled_at_str: str,
        timezone_name: str = "Asia/Kolkata",
        source: str = PostSource.MANUAL.value,
        ai_brief: dict[str, Any] | None = None,
    ) -> Post:
        valid_content = self.validate_content(content)
        try:
            scheduled_at_utc = parse_iso_to_utc(scheduled_at_str, timezone_name)
        except ValueError as e:
            raise ValidationError(str(e)) from e

        self.validate_scheduled_time(scheduled_at_utc)

        post = Post(
            user_id=user_id,
            content=valid_content,
            source=source,
            ai_brief=ai_brief,
            status=PostStatus.SCHEDULED.value,
            scheduled_at_utc=scheduled_at_utc,
            next_attempt_at=scheduled_at_utc,
            attempt_count=0,
            idempotency_key=str(uuid.uuid4()),
        )
        session.add(post)
        session.flush()
        return post

    def get_post(self, session: Session, post_id: int, user_id: int) -> Post:
        stmt = select(Post).where(Post.id == post_id, Post.user_id == user_id)
        post = session.execute(stmt).scalar_one_or_none()
        if not post:
            raise NotFoundError(f"Post #{post_id} not found.")
        return post

    def list_posts(
        self,
        session: Session,
        user_id: int,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 10,
    ) -> tuple[list[Post], int]:
        stmt = select(Post).where(Post.user_id == user_id)
        if status and status.lower() != "all":
            stmt = stmt.where(Post.status == status.lower())
        if search:
            stmt = stmt.where(Post.content.ilike(f"%{search.strip()}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = session.execute(count_stmt).scalar() or 0

        stmt = stmt.order_by(Post.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        posts = list(session.execute(stmt).scalars().all())

        return posts, total

    def get_metrics(self, session: Session, user_id: int) -> dict[str, int]:
        stmt = (
            select(Post.status, func.count(Post.id))
            .where(Post.user_id == user_id)
            .group_by(Post.status)
        )
        rows = session.execute(stmt).all()
        counts = {
            "scheduled": 0,
            "processing": 0,
            "published": 0,
            "failed": 0,
            "cancelled": 0,
            "total": 0,
        }
        for status, count in rows:
            if status in counts:
                counts[status] = count
            counts["total"] += count
        return counts

    def update_post(
        self,
        session: Session,
        post_id: int,
        user_id: int,
        content: str | None = None,
        scheduled_at_str: str | None = None,
        timezone_name: str = "Asia/Kolkata",
    ) -> Post:
        post = self.get_post(session, post_id, user_id)
        if post.status != PostStatus.SCHEDULED.value:
            raise ConflictError(
                f"Cannot edit post in '{post.status}' state. Only scheduled posts can be modified."
            )

        if content is not None:
            post.content = self.validate_content(content)

        if scheduled_at_str is not None:
            try:
                scheduled_at_utc = parse_iso_to_utc(scheduled_at_str, timezone_name)
            except ValueError as e:
                raise ValidationError(str(e)) from e
            self.validate_scheduled_time(scheduled_at_utc)
            post.scheduled_at_utc = scheduled_at_utc
            post.next_attempt_at = scheduled_at_utc

        session.flush()
        return post

    def cancel_post(self, session: Session, post_id: int, user_id: int) -> Post:
        post = self.get_post(session, post_id, user_id)
        if post.status != PostStatus.SCHEDULED.value:
            raise ConflictError(
                f"Cannot cancel post in '{post.status}' state. Only scheduled posts can be cancelled."
            )
        validate_post_transition(post.status, PostStatus.CANCELLED)
        post.status = PostStatus.CANCELLED.value
        session.flush()
        return post

    def get_post_attempts(self, session: Session, post_id: int, user_id: int) -> list[dict]:
        self.get_post(session, post_id, user_id)
        stmt = (
            select(PostAttempt)
            .where(PostAttempt.post_id == post_id)
            .order_by(PostAttempt.attempt_no.asc())
        )
        attempts = list(session.execute(stmt).scalars().all())
        return [a.to_dict() for a in attempts]

    # --- Worker-specific queries ---

    @staticmethod
    def claim_due_posts(
        session: Session,
        now_utc: datetime,
        batch_size: int = 10,
        worker_id: str = "worker-1",
    ) -> list[Post]:
        """Atomically locks and claims due posts using SELECT ... FOR UPDATE SKIP LOCKED."""
        stmt = (
            select(Post)
            .where(
                Post.status == PostStatus.SCHEDULED.value,
                Post.next_attempt_at <= now_utc,
            )
            .order_by(Post.scheduled_at_utc.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        posts = list(session.execute(stmt).scalars().all())

        for p in posts:
            validate_post_transition(p.status, PostStatus.PROCESSING)
            p.status = PostStatus.PROCESSING.value
            p.locked_at = now_utc
            p.locked_by = worker_id

        if posts:
            session.flush()

        return posts

    @staticmethod
    def find_stuck_posts(
        session: Session,
        stuck_threshold_utc: datetime,
        limit: int = 20,
    ) -> list[Post]:
        """Finds posts stuck in 'processing' status longer than the timeout threshold."""
        stmt = (
            select(Post)
            .where(
                Post.status == PostStatus.PROCESSING.value,
                Post.locked_at <= stuck_threshold_utc,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def log_attempt(
        session: Session,
        post_id: int,
        attempt_no: int,
        outcome: str | AttemptOutcome,
        http_status: int | None = None,
        error_message: str | None = None,
    ) -> PostAttempt:
        outcome_val = outcome.value if isinstance(outcome, AttemptOutcome) else outcome
        attempt = PostAttempt(
            post_id=post_id,
            attempt_no=attempt_no,
            outcome=outcome_val,
            http_status=http_status,
            error_message=error_message,
        )
        session.add(attempt)
        session.flush()
        return attempt


post_service = PostService()

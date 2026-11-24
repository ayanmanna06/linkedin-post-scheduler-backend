from enum import Enum

from backend.services.exceptions import InvalidStateTransitionError


class PostStatus(str, Enum):
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[PostStatus, set[PostStatus]] = {
    PostStatus.SCHEDULED: {
        PostStatus.PROCESSING,
        PostStatus.CANCELLED,
    },
    PostStatus.PROCESSING: {
        PostStatus.PUBLISHED,
        PostStatus.FAILED,
        PostStatus.SCHEDULED,  # Allowed for retry requeue with backoff
    },
    PostStatus.PUBLISHED: set(),
    PostStatus.FAILED: set(),
    PostStatus.CANCELLED: set(),
}


def validate_post_transition(current_status: str | PostStatus, target_status: str | PostStatus) -> None:
    try:
        curr = PostStatus(current_status)
    except ValueError as e:
        raise InvalidStateTransitionError(f"Unknown current post status: '{current_status}'") from e

    try:
        target = PostStatus(target_status)
    except ValueError as e:
        raise InvalidStateTransitionError(f"Unknown target post status: '{target_status}'") from e

    if curr == target:
        return

    allowed_targets = VALID_TRANSITIONS.get(curr, set())
    if target not in allowed_targets:
        allowed_names = [s.value for s in allowed_targets]
        raise InvalidStateTransitionError(
            message=f"Cannot transition post status from '{curr.value}' to '{target.value}'. Allowed: {allowed_names}",
            details={"current_status": curr.value, "target_status": target.value, "allowed_transitions": allowed_names},
        )

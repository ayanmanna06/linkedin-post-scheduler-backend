import pytest

from backend.services.exceptions import InvalidStateTransitionError
from backend.services.state_machine import PostStatus, validate_post_transition


def test_valid_post_transitions():
    # scheduled -> processing (worker claim)
    validate_post_transition(PostStatus.SCHEDULED, PostStatus.PROCESSING)

    # scheduled -> cancelled (user cancel)
    validate_post_transition(PostStatus.SCHEDULED, PostStatus.CANCELLED)

    # processing -> published (success)
    validate_post_transition(PostStatus.PROCESSING, PostStatus.PUBLISHED)

    # processing -> failed (permanent failure)
    validate_post_transition(PostStatus.PROCESSING, PostStatus.FAILED)

    # processing -> scheduled (retry requeue with backoff)
    validate_post_transition(PostStatus.PROCESSING, PostStatus.SCHEDULED)

    # Idempotent same-state transitions
    validate_post_transition(PostStatus.SCHEDULED, PostStatus.SCHEDULED)
    validate_post_transition(PostStatus.PUBLISHED, PostStatus.PUBLISHED)


def test_invalid_post_transitions():
    # Terminal published state cannot transition
    with pytest.raises(InvalidStateTransitionError):
        validate_post_transition(PostStatus.PUBLISHED, PostStatus.SCHEDULED)

    with pytest.raises(InvalidStateTransitionError):
        validate_post_transition(PostStatus.PUBLISHED, PostStatus.PROCESSING)

    # Terminal failed state cannot transition
    with pytest.raises(InvalidStateTransitionError):
        validate_post_transition(PostStatus.FAILED, PostStatus.PROCESSING)

    # Terminal cancelled state cannot transition
    with pytest.raises(InvalidStateTransitionError):
        validate_post_transition(PostStatus.CANCELLED, PostStatus.PUBLISHED)

    # Scheduled cannot jump straight to published without processing
    with pytest.raises(InvalidStateTransitionError):
        validate_post_transition(PostStatus.SCHEDULED, PostStatus.PUBLISHED)


def test_invalid_status_string_raises():
    with pytest.raises(InvalidStateTransitionError):
        validate_post_transition("invalid_status", PostStatus.SCHEDULED)

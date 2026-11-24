from datetime import timedelta

from backend.models import Post, PostStatus
from backend.services.post_service import post_service
from backend.services.time_utils import utc_now
from backend.workers.publisher_worker import process_single_post


def test_concurrent_worker_claim_simulation(db_session, test_user):
    """
    Simulates two workers claiming due posts.
    Worker 1 claims the post and changes state to 'processing'.
    Worker 2 immediately attempts to claim due posts and gets 0 results.
    """
    now = utc_now()
    # Create due post
    post = Post(
        user_id=test_user.id,
        content="Testing concurrency lock safety",
        scheduled_at_utc=now - timedelta(minutes=1),
        next_attempt_at=now - timedelta(minutes=1),
        status=PostStatus.SCHEDULED.value,
        idempotency_key="unique-test-key-001",
    )
    db_session.add(post)
    db_session.flush()

    # Worker 1 claims due posts
    worker_1_claims = post_service.claim_due_posts(
        session=db_session,
        now_utc=now,
        batch_size=10,
        worker_id="worker-node-alpha-1",
    )
    assert len(worker_1_claims) == 1
    assert worker_1_claims[0].id == post.id
    assert worker_1_claims[0].status == PostStatus.PROCESSING.value
    assert worker_1_claims[0].locked_by == "worker-node-alpha-1"

    # Worker 2 now attempts to claim due posts
    worker_2_claims = post_service.claim_due_posts(
        session=db_session,
        now_utc=now,
        batch_size=10,
        worker_id="worker-node-beta-2",
    )
    # Must be 0 because status is already 'processing'
    assert len(worker_2_claims) == 0


def test_duplicate_protection_with_existing_urn(db_session, test_user, monkeypatch):
    """
    Verifies that if a post already has linkedin_post_urn populated,
    the worker skips calling LinkedIn again and marks status 'published'.
    """
    post = Post(
        user_id=test_user.id,
        content="Post with existing URN",
        scheduled_at_utc=utc_now(),
        next_attempt_at=utc_now(),
        status=PostStatus.PROCESSING.value,
        idempotency_key="unique-test-key-002",
        linkedin_post_urn="urn:li:share:already-published-999",
    )
    db_session.add(post)
    db_session.flush()

    # If linkedin_service.publish_post were called, this would raise
    def fail_if_called(*args, **kwargs):
        raise AssertionError("LinkedIn API should NOT have been called!")

    monkeypatch.setattr("backend.workers.publisher_worker.linkedin_service.publish_post", fail_if_called)

    process_single_post(post.id)

    db_session.refresh(post)
    assert post.status == PostStatus.PUBLISHED.value

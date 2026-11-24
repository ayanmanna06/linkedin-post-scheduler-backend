"""
Standalone Publisher Worker Process for LinkedIn Post Scheduler.
Polls MySQL using SELECT ... FOR UPDATE SKIP LOCKED to claim due posts atomically.
Handles idempotency, transient error retries with exponential backoff and jitter,
and recovers orphaned stuck jobs.
"""

import os
import signal
import socket
import sys
import time
from datetime import timedelta

# Ensure project root and backend dir are importable
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_root = os.path.dirname(current_dir)
project_root = os.path.dirname(backend_root)
for p in (project_root, backend_root):
    if p not in sys.path:
        sys.path.insert(0, p)

from sqlalchemy import select

from backend.config import settings
from backend.db import get_db_session
from backend.models import AttemptOutcome, LinkedInConnection, Post, PostStatus
from backend.services.linkedin_service import (
    LinkedInAuthError,
    LinkedInFatalError,
    LinkedInTransientError,
    linkedin_service,
)
from backend.services.logger import get_logger, setup_logging
from backend.services.post_service import post_service
from backend.services.retry_policy import (
    calculate_next_attempt_delay,
    should_retry,
)
from backend.services.time_utils import utc_now
from backend.services.token_service import token_service

logger = get_logger("publisher_worker")

# Worker Identity
HOSTNAME = os.environ.get("WORKER_HOSTNAME", "Ayan")
PID = os.getpid()
WORKER_ID = f"worker-{HOSTNAME}-{PID}"

_running = True


def _signal_handler(signum, frame):
    global _running
    logger.info("worker_shutdown_signal_received", signal=signum, worker_id=WORKER_ID)
    _running = False


def recover_stuck_jobs() -> None:
    """
    Finds jobs stuck in 'processing' longer than PROCESSING_TIMEOUT_MINUTES.
    Reconciles with LinkedIn API to verify if post succeeded before retrying.
    """
    threshold = utc_now() - timedelta(minutes=settings.PROCESSING_TIMEOUT_MINUTES)
    with get_db_session() as session:
        stuck_posts = post_service.find_stuck_posts(session, threshold, limit=10)
        if not stuck_posts:
            return

        logger.warn("stuck_jobs_detected", count=len(stuck_posts), threshold=threshold.isoformat())

        for post in stuck_posts:
            conn_stmt = select(LinkedInConnection).where(LinkedInConnection.user_id == post.user_id)
            conn = session.execute(conn_stmt).scalar_one_or_none()
            reconciled_urn: str | None = None

            if conn and not conn.is_expired:
                try:
                    raw_token = token_service.decrypt_token(conn.access_token_encrypted)
                    if raw_token:
                        reconciled_urn = linkedin_service.reconcile_ambiguous_post(
                            access_token=raw_token,
                            author_urn=conn.author_urn,
                            content_snippet=post.content,
                        )
                except Exception as e:
                    logger.error("stuck_job_reconciliation_error", post_id=post.id, error=str(e))

            if reconciled_urn:
                logger.info("stuck_job_reconciled_as_published", post_id=post.id, urn=reconciled_urn)
                post.linkedin_post_urn = reconciled_urn
                post.status = PostStatus.PUBLISHED.value
                post.published_at = utc_now()
                post.locked_at = None
                post.locked_by = None
                post_service.log_attempt(
                    session=session,
                    post_id=post.id,
                    attempt_no=post.attempt_count + 1,
                    outcome=AttemptOutcome.SUCCESS,
                    http_status=200,
                    error_message="Reconciled via LinkedIn API after recovery timeout.",
                )
            else:
                post.attempt_count += 1
                if post.attempt_count < settings.MAX_PUBLISH_ATTEMPTS:
                    delay = calculate_next_attempt_delay(post.attempt_count)
                    post.status = PostStatus.SCHEDULED.value
                    post.next_attempt_at = utc_now() + timedelta(seconds=delay)
                    post.last_error = "Recovered from stuck processing state. Re-queued."
                    outcome = AttemptOutcome.UNCERTAIN
                else:
                    post.status = PostStatus.FAILED.value
                    post.last_error = "Processing timed out and retry budget exhausted."
                    outcome = AttemptOutcome.FATAL_FAILURE

                post.locked_at = None
                post.locked_by = None
                post_service.log_attempt(
                    session=session,
                    post_id=post.id,
                    attempt_no=post.attempt_count,
                    outcome=outcome,
                    http_status=504,
                    error_message=post.last_error,
                )


def process_single_post(post_id: int) -> None:
    """Processes a single claimed post in an isolated database transaction."""
    with get_db_session() as session:
        post = session.get(Post, post_id)
        if not post:
            return

        # 1. Duplicate protection: if already has post URN, mark published immediately
        if post.linkedin_post_urn:
            logger.info("post_already_published_skip", post_id=post.id, urn=post.linkedin_post_urn)
            post.status = PostStatus.PUBLISHED.value
            post.locked_at = None
            post.locked_by = None
            return

        attempt_no = post.attempt_count + 1
        conn_stmt = select(LinkedInConnection).where(LinkedInConnection.user_id == post.user_id)
        conn = session.execute(conn_stmt).scalar_one_or_none()

        # 2. Check LinkedIn connection & token validity
        if not conn:
            err_msg = "LinkedIn account not connected. Please connect your account."
            logger.error("publish_aborted_no_connection", post_id=post.id, user_id=post.user_id)
            post.status = PostStatus.FAILED.value
            post.last_error = err_msg
            post.attempt_count = attempt_no
            post.locked_at = None
            post.locked_by = None
            post_service.log_attempt(
                session=session,
                post_id=post.id,
                attempt_no=attempt_no,
                outcome=AttemptOutcome.FATAL_FAILURE,
                http_status=401,
                error_message=err_msg,
            )
            return

        if conn.is_expired:
            err_msg = "LinkedIn token expired, please reconnect your LinkedIn account."
            logger.error("publish_aborted_token_expired", post_id=post.id, user_id=post.user_id)
            post.status = PostStatus.FAILED.value
            post.last_error = err_msg
            post.attempt_count = attempt_no
            post.locked_at = None
            post.locked_by = None
            post_service.log_attempt(
                session=session,
                post_id=post.id,
                attempt_no=attempt_no,
                outcome=AttemptOutcome.FATAL_FAILURE,
                http_status=401,
                error_message=err_msg,
            )
            return

        # 3. Decrypt OAuth access token
        try:
            access_token = token_service.decrypt_token(conn.access_token_encrypted)
        except Exception as e:
            err_msg = f"Token decryption failure: {str(e)}"
            logger.error("token_decryption_failed", post_id=post.id, error=str(e))
            post.status = PostStatus.FAILED.value
            post.last_error = err_msg
            post.attempt_count = attempt_no
            post.locked_at = None
            post.locked_by = None
            post_service.log_attempt(
                session=session,
                post_id=post.id,
                attempt_no=attempt_no,
                outcome=AttemptOutcome.FATAL_FAILURE,
                http_status=500,
                error_message=err_msg,
            )
            return

        # 4. Attempt publishing to LinkedIn REST API
        logger.info(
            "publishing_post_to_linkedin",
            post_id=post.id,
            attempt_no=attempt_no,
            worker_id=WORKER_ID,
        )

        media_items = None
        if post.ai_brief and isinstance(post.ai_brief, dict):
            media_items = post.ai_brief.get("media_items")

        try:
            post_urn, response_meta = linkedin_service.publish_post(
                access_token=access_token,
                author_urn=conn.author_urn,
                content=post.content,
                media_items=media_items,
            )

            # Success path
            post.linkedin_post_urn = post_urn
            post.status = PostStatus.PUBLISHED.value
            post.published_at = utc_now()
            post.attempt_count = attempt_no
            post.last_error = None
            post.locked_at = None
            post.locked_by = None

            post_service.log_attempt(
                session=session,
                post_id=post.id,
                attempt_no=attempt_no,
                outcome=AttemptOutcome.SUCCESS,
                http_status=201,
                error_message=None,
            )
            logger.info("post_published_success", post_id=post.id, post_urn=post_urn)

        except LinkedInTransientError as te:
            logger.warn(
                "transient_publish_error",
                post_id=post.id,
                attempt_no=attempt_no,
                error=te.message,
                http_status=te.http_status,
            )
            if should_retry(attempt_no, te):
                delay = calculate_next_attempt_delay(attempt_no, retry_after=te.retry_after)
                next_time = utc_now() + timedelta(seconds=delay)
                post.status = PostStatus.SCHEDULED.value
                post.next_attempt_at = next_time
                post.attempt_count = attempt_no
                post.last_error = f"Transient error: {te.message}. Retrying at {next_time.isoformat()}."
                outcome = AttemptOutcome.TRANSIENT_FAILURE
            else:
                post.status = PostStatus.FAILED.value
                post.attempt_count = attempt_no
                post.last_error = f"Max attempts reached. Last transient error: {te.message}"
                outcome = AttemptOutcome.FATAL_FAILURE

            post.locked_at = None
            post.locked_by = None
            post_service.log_attempt(
                session=session,
                post_id=post.id,
                attempt_no=attempt_no,
                outcome=outcome,
                http_status=te.http_status,
                error_message=te.message,
            )

        except (LinkedInAuthError, LinkedInFatalError, Exception) as fe:
            logger.error(
                "fatal_publish_error",
                post_id=post.id,
                attempt_no=attempt_no,
                error=str(fe),
            )
            http_status = getattr(fe, "http_status", 400)
            post.status = PostStatus.FAILED.value
            post.attempt_count = attempt_no
            post.last_error = f"Publish failed: {str(fe)}"
            post.locked_at = None
            post.locked_by = None
            post_service.log_attempt(
                session=session,
                post_id=post.id,
                attempt_no=attempt_no,
                outcome=AttemptOutcome.FATAL_FAILURE,
                http_status=http_status,
                error_message=str(fe),
            )


def run_worker_loop() -> None:
    """Main worker polling loop."""
    logger.info(
        "publisher_worker_started",
        worker_id=WORKER_ID,
        poll_interval=settings.WORKER_POLL_INTERVAL_SECONDS,
        max_attempts=settings.MAX_PUBLISH_ATTEMPTS,
    )

    while _running:
        try:
            # 1. Recover orphaned or timed-out stuck jobs
            recover_stuck_jobs()

            # 2. Claim due posts atomically with SELECT ... FOR UPDATE SKIP LOCKED
            claimed_post_ids: list[int] = []
            now = utc_now()

            with get_db_session() as session:
                claimed_posts = post_service.claim_due_posts(
                    session=session,
                    now_utc=now,
                    batch_size=10,
                    worker_id=WORKER_ID,
                )
                claimed_post_ids = [p.id for p in claimed_posts]

            if claimed_post_ids:
                logger.info("claimed_due_posts", count=len(claimed_post_ids), ids=claimed_post_ids)
                for pid in claimed_post_ids:
                    if not _running:
                        break
                    process_single_post(pid)

        except Exception as e:
            logger.error("worker_loop_iteration_error", error=str(e), exc_info=True)

        # Sleep with sub-second polling check for responsive shutdown
        sleep_elapsed = 0.0
        while sleep_elapsed < settings.WORKER_POLL_INTERVAL_SECONDS and _running:
            time.sleep(0.5)
            sleep_elapsed += 0.5

    logger.info("publisher_worker_stopped_cleanly", worker_id=WORKER_ID)


def run_worker_once() -> int:
    """Process due posts in a single pass and exit."""
    recover_stuck_jobs()
    now = utc_now()
    claimed_post_ids: list[int] = []

    with get_db_session() as session:
        claimed_posts = post_service.claim_due_posts(
            session=session,
            now_utc=now,
            batch_size=10,
            worker_id=WORKER_ID,
        )
        claimed_post_ids = [p.id for p in claimed_posts]

    if claimed_post_ids:
        logger.info("claimed_due_posts_single_pass", count=len(claimed_post_ids), ids=claimed_post_ids)
        for pid in claimed_post_ids:
            process_single_post(pid)
    else:
        logger.info("no_due_posts_to_process", now=now.isoformat())
    return len(claimed_post_ids)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LinkedIn Post Scheduler Publisher Worker")
    parser.add_argument("--once", action="store_true", help="Process due posts once and exit")
    args = parser.parse_args()

    setup_logging(log_level="INFO")
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.once:
        run_worker_once()
    else:
        run_worker_loop()


if __name__ == "__main__":
    main()

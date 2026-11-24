from backend.services.linkedin_service import (
    LinkedInAuthError,
    LinkedInFatalError,
    LinkedInTransientError,
)
from backend.services.retry_policy import (
    calculate_next_attempt_delay,
    should_retry,
)


def test_calculate_next_attempt_delay_schedule():
    # Base delays: [60, 300, 900, 3600, 14400] with ±20% jitter
    d1 = calculate_next_attempt_delay(1)
    assert 48 <= d1 <= 72

    d2 = calculate_next_attempt_delay(2)
    assert 240 <= d2 <= 360

    d3 = calculate_next_attempt_delay(3)
    assert 720 <= d3 <= 1080


def test_calculate_delay_respects_retry_after_header():
    # If LinkedIn returns 429 with Retry-After: 120
    delay = calculate_next_attempt_delay(attempt_no=1, retry_after=120)
    assert 96 <= delay <= 144


def test_should_retry_transient_vs_fatal():
    # Transient error within attempt budget -> True
    transient = LinkedInTransientError("HTTP 503 Service Unavailable")
    assert should_retry(attempt_no=1, error=transient) is True
    assert should_retry(attempt_no=4, error=transient) is True

    # Attempts exhausted (limit is 5) -> False
    assert should_retry(attempt_no=5, error=transient) is False

    # Fatal client error (e.g. 400 Bad Request) -> False
    fatal = LinkedInFatalError("Invalid commentary length", http_status=400)
    assert should_retry(attempt_no=1, error=fatal) is False

    # Auth error (e.g. 401 Unauthorized / Token Expired) -> False
    auth_err = LinkedInAuthError("Token expired")
    assert should_retry(attempt_no=1, error=auth_err) is False

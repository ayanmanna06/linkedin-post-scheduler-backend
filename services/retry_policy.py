import random

from backend.config import settings
from backend.services.linkedin_service import LinkedInTransientError

BACKOFF_SCHEDULE_SECONDS = [60, 300, 900, 3600, 14400]


def calculate_next_attempt_delay(attempt_no: int, retry_after: int | None = None) -> int:
    """Calculates retry backoff in seconds with full jitter (±20%)."""
    if retry_after is not None and retry_after > 0:
        base = retry_after
    else:
        index = min(max(0, attempt_no - 1), len(BACKOFF_SCHEDULE_SECONDS) - 1)
        base = BACKOFF_SCHEDULE_SECONDS[index]

    jitter = random.uniform(0.8, 1.2)
    return max(1, int(base * jitter))


def should_retry(attempt_no: int, error: Exception) -> bool:
    """Determines whether error is eligible for retry."""
    if attempt_no >= settings.MAX_PUBLISH_ATTEMPTS:
        return False

    if isinstance(error, LinkedInTransientError):
        return True

    err_str = str(error).lower()
    transient_indicators = ["timeout", "connection reset", "temporarily unavailable", "try again"]
    return any(ind in err_str for ind in transient_indicators)

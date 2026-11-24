from datetime import datetime, timezone

import pytz

DEFAULT_TIMEZONE = "Asia/Kolkata"


def utc_now() -> datetime:
    """Returns naive UTC datetime for MySQL storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc(dt: datetime, tz_name: str | None = None) -> datetime:
    """Converts a datetime to a naive UTC datetime."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    tz_str = tz_name or DEFAULT_TIMEZONE
    try:
        tz = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone(DEFAULT_TIMEZONE)

    localized = tz.localize(dt)
    return localized.astimezone(pytz.UTC).replace(tzinfo=None)


def parse_iso_to_utc(iso_str: str, tz_name: str | None = None) -> datetime:
    """Parses an ISO format string into a UTC naive datetime."""
    cleaned_str = iso_str.strip()
    if cleaned_str.endswith("Z") or cleaned_str.endswith("z"):
        cleaned_str = cleaned_str[:-1]
        if not ("+" in cleaned_str[-6:] or "-" in cleaned_str[-6:]):
            cleaned_str += "+00:00"

    try:
        dt = datetime.fromisoformat(cleaned_str)
    except ValueError as e:
        raise ValueError(f"Invalid ISO datetime string: '{iso_str}'") from e

    return to_utc(dt, tz_name)


def format_iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"

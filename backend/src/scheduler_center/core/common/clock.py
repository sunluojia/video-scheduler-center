from datetime import UTC, datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    return datetime.now(UTC)

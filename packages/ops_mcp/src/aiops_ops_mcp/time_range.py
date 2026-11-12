from datetime import UTC, datetime


def parse_timestamp(value: str) -> datetime:
    try:
        timestamp = float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone") from None
        return parsed.astimezone(UTC)
    return datetime.fromtimestamp(timestamp, tz=UTC)


def validate_range(
    *,
    start: str,
    end: str,
    max_seconds: float,
) -> tuple[datetime, datetime]:
    start_time = parse_timestamp(start)
    end_time = parse_timestamp(end)
    if end_time <= start_time:
        raise ValueError("end must be after start")
    if (end_time - start_time).total_seconds() > max_seconds:
        raise ValueError(f"time range must not exceed {int(max_seconds)} seconds")
    return start_time, end_time

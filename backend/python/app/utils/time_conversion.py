from datetime import datetime, timezone

MAX_TIMESTAMP_LENGTH = 13

def get_epoch_timestamp_in_ms() -> int:
    now = datetime.now(timezone.utc).timestamp()
    return int(now * 1000)

def parse_timestamp(timestamp_str: str) -> int:
    # Remove the 'Z' and add '+00:00' for UTC
    if timestamp_str.endswith("Z") or timestamp_str.endswith("z"):
        timestamp_str = timestamp_str[:-1] + "+00:00"

    dt = datetime.fromisoformat(timestamp_str)
    timestamp = int(dt.timestamp())

    # Check if timestamp is already in milliseconds (13 digits)
    if len(str(timestamp)) >= MAX_TIMESTAMP_LENGTH:
        return timestamp

    # Convert seconds to milliseconds
    return timestamp * 1000

def epoch_ms_to_iso(epoch_ms: int) -> str:
    """Convert epoch milliseconds to an ISO 8601 UTC datetime string."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()

def prepare_iso_timestamps(start_time: str, end_time: str) -> tuple[str, str]:
    """Converts start and end time strings to ISO 8601 formatted strings."""
    start_timestamp = parse_timestamp(start_time)
    end_timestamp = parse_timestamp(end_time)

    start_dt = datetime.fromtimestamp(start_timestamp / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_timestamp / 1000, tz=timezone.utc)

    return start_dt.isoformat(), end_dt.isoformat()

def datetime_to_epoch_ms(dt_obj: datetime | str | None) -> int | None:
    """Convert datetime object or ISO string to epoch timestamp in milliseconds.
    
    Args:
        dt_obj: datetime object, ISO string, or None
        
    Returns:
        Epoch timestamp in milliseconds, or None if input is None or invalid
    """
    if not dt_obj:
        return None
    try:
        if isinstance(dt_obj, str):
            return parse_timestamp(dt_obj)
        dt = dt_obj
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

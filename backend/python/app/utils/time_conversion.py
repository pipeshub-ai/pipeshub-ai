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

def prepare_iso_timestamps(start_time: str, end_time: str) -> tuple[str, str]:
    """Converts start and end time strings to ISO 8601 formatted strings."""
    start_timestamp = parse_timestamp(start_time)
    end_timestamp = parse_timestamp(end_time)

    start_dt = datetime.fromtimestamp(start_timestamp / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_timestamp / 1000, tz=timezone.utc)

    return start_dt.isoformat(), end_dt.isoformat()

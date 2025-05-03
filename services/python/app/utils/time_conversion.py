from datetime import datetime, timezone


def get_epoch_timestamp_in_ms():
    now = datetime.now(timezone.utc).timestamp()
    return int(now * 1000)

def parse_timestamp(timestamp_str: str) -> int:
    # Remove the 'Z' and add '+00:00' for UTC
    if timestamp_str.endswith("Z") or timestamp_str.endswith("z"):
        timestamp_str = timestamp_str[:-1] + "+00:00"

    dt = datetime.fromisoformat(timestamp_str)
    timestamp = int(dt.timestamp())

    # Check if timestamp is already in milliseconds (13 digits)
    if len(str(timestamp)) >= 13:
        return timestamp

    # Convert seconds to milliseconds
    return timestamp * 1000

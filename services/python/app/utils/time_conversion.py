from datetime import datetime, timezone


def get_epoch_timestamp_in_ms():
    now = datetime.now(timezone.utc).timestamp()
    return int(now * 1000)


def parse_timestamp(timestamp_str):
    # Remove the 'Z' and add '+00:00' for UTC
    if timestamp_str.endswith("Z"):
        timestamp_str = timestamp_str[:-1] + "+00:00"
    return datetime.fromisoformat(timestamp_str)

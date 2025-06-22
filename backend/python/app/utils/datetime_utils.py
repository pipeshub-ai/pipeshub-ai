from datetime import datetime, timezone


def get_current_date() -> str:
    return datetime.now().strftime("%B %d, %Y")


def get_current_time() -> str:
    return datetime.now().strftime("%H:%M:%S")

def get_current_datetime() -> str:
    return datetime.now().strftime("%B %d, %Y %H:%M:%S")

def get_current_datetime_with_timezone() -> str:
    return datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M:%S")

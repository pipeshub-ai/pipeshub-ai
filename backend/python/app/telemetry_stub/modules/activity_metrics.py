from ..backend import NOOP

ACTIVITY = NOOP


def record_service_activity(
    service: str,
    activity: str,
    connector: str = "none",
    status: str = "ok",
    org: str = "unknown",
    kb: str = "none",
    domain: str = "unknown",
    mimetype: str = "none",
) -> None:
    return None

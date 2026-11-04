from ..backend import NOOP

METRIC_COLLECTION_ENABLED = NOOP


def set_metric_collection_enabled(service: str, enabled: bool) -> None:  # noqa: FBT001
    return None

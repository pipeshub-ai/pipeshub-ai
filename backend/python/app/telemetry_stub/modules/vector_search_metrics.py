from ..backend import NOOP

SEARCH_RETRIES = NOOP


def record_search_retry(provider: str, outcome: str) -> None:
    return None

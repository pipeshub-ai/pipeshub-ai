from ..backend import NOOP

SEARCHES = NOOP
SEARCH_SECONDS = NOOP
FETCHES = NOOP
FETCH_SECONDS = NOOP


def record_search(provider: str, outcome: str, seconds: float) -> None:
    return None


def record_fetch(outcome: str, seconds: float) -> None:
    return None

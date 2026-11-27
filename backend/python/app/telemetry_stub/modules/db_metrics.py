from typing import TypeVar

from ..backend import NOOP

T = TypeVar("T")

DB_QUERY_SECONDS = NOOP


def instrument_store(store: T, name: str) -> T:
    return store

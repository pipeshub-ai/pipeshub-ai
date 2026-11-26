"""How long a rate-limited provider asked to be left alone (ERR-UX2, EU-O3).

A provider's 429 may carry ``Retry-After`` (seconds or an HTTP date) or OpenAI's ``retry-after-ms``. The wait honours
it, bounded by ``PROVIDER_WAIT_CAP_S`` so one header cannot park a run for an hour; without one the caller's default
applies. The header is read off the exception's response through the cause chain, as the SDKs attach it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

PROVIDER_WAIT_CAP_S: Final = 120.0


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _headers(exc: BaseException) -> Mapping[str, str] | None:
    for holder in (getattr(exc, "response", None), exc):
        headers = getattr(holder, "headers", None)
        if isinstance(headers, Mapping) or hasattr(headers, "get"):
            return headers  # pyright: ignore[reportReturnType]
    return None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name) or headers.get(name.title())
    return value.strip() if isinstance(value, str) and value.strip() else None


def _seconds(headers: Mapping[str, str], now: datetime) -> float | None:
    millis = _header(headers, "retry-after-ms")
    if millis is not None:
        try:
            return float(millis) / 1000
        except ValueError:
            pass
    value = _header(headers, "retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return (at.replace(tzinfo=UTC) if at.tzinfo is None else at).timestamp() - now.timestamp()


def provider_wait_s(exc: BaseException, *, now: datetime, default_s: float, cap_s: float = PROVIDER_WAIT_CAP_S) -> float:
    """The wait the provider asked for, within ``[0, cap_s]``; ``default_s`` when it asked for none we can read."""
    for link in _chain(exc):
        headers = _headers(link)
        if headers is None:
            continue
        seconds = _seconds(headers, now)
        if seconds is not None:
            return min(max(seconds, 0.0), cap_s)
    return min(default_s, cap_s)


__all__ = ["PROVIDER_WAIT_CAP_S", "provider_wait_s"]

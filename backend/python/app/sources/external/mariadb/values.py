"""MariaDB values in text: what JSON and stored profiles hold."""

from __future__ import annotations

import datetime
from typing import Any


def format_time(value: datetime.timedelta) -> str:
    """TIME as MariaDB writes it, ``[-]HH:MM:SS[.ffffff]``; ``str()`` of a negative one reads ``-1 day, …``."""
    sign = "-" if value < datetime.timedelta(0) else ""
    micros = abs(value) // datetime.timedelta(microseconds=1)
    seconds, micro = divmod(micros, 1_000_000)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{text}.{micro:06d}" if micro else text


def json_default(value: Any) -> Any:  # noqa: ANN401
    """Row values json cannot write: bytes as hex, TIME as MariaDB writes it, the rest as text."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, datetime.timedelta):
        return format_time(value)
    return str(value)

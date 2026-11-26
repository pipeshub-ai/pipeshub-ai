"""The AWS event-stream framing (``application/vnd.amazon.eventstream``), decoded incrementally.

Each message: a 12-byte prelude (total length, headers length, CRC32 of the first
8 bytes), typed headers, the payload, and a CRC32 of everything before it. A CRC
that does not match is an error, never a short answer.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass, field

_PRELUDE = 12
_TRAILER = 4
# Header value types: (fixed width, or None for a 2-byte length prefix).
_FIXED: dict[int, int] = {2: 1, 3: 2, 4: 4, 5: 8, 8: 8, 9: 16}


class EventStreamError(ValueError):
    """The stream's bytes are not a valid event-stream message."""


@dataclass(frozen=True)
class EventMessage:
    headers: dict[str, object]
    payload: bytes

    @property
    def message_type(self) -> str:
        return str(self.headers.get(":message-type", "event"))

    @property
    def event_type(self) -> str:
        return str(self.headers.get(":event-type") or self.headers.get(":exception-type") or "")


def _crc(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def _headers(raw: bytes) -> dict[str, object]:
    out: dict[str, object] = {}
    i = 0
    while i < len(raw):
        name_len = raw[i]
        name = raw[i + 1 : i + 1 + name_len].decode("utf-8")
        i += 1 + name_len
        kind = raw[i]
        i += 1
        if kind in (0, 1):
            out[name] = kind == 0
        elif kind in _FIXED:
            width = _FIXED[kind]
            out[name] = raw[i : i + width]
            i += width
        elif kind in (6, 7):
            (length,) = struct.unpack("!H", raw[i : i + 2])
            value = raw[i + 2 : i + 2 + length]
            out[name] = value.decode("utf-8") if kind == 7 else value
            i += 2 + length
        else:
            raise EventStreamError(f"unknown header value type {kind}")
    return out


@dataclass
class EventStreamDecoder:
    _buffer: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[EventMessage]:
        self._buffer.extend(data)
        messages: list[EventMessage] = []
        while len(self._buffer) >= _PRELUDE:
            total, headers_len, prelude_crc = struct.unpack("!III", bytes(self._buffer[:_PRELUDE]))
            if _crc(bytes(self._buffer[:8])) != prelude_crc:
                raise EventStreamError("prelude checksum mismatch")
            if total < _PRELUDE + headers_len + _TRAILER:
                raise EventStreamError("message shorter than its headers")
            if len(self._buffer) < total:
                break
            message = bytes(self._buffer[:total])
            del self._buffer[:total]
            (message_crc,) = struct.unpack("!I", message[-_TRAILER:])
            if _crc(message[:-_TRAILER]) != message_crc:
                raise EventStreamError("message checksum mismatch")
            body = message[_PRELUDE:-_TRAILER]
            messages.append(EventMessage(_headers(body[:headers_len]), body[headers_len:]))
        return messages

    def finish(self) -> None:
        """At the end of the body: a partial message left over means the stream was cut."""
        if self._buffer:
            raise EventStreamError("stream ended inside a message")


__all__ = ["EventMessage", "EventStreamDecoder", "EventStreamError"]

"""Streaming JSON string extractor for live ``final_answer`` delivery.

When a model calls ``final_answer(answer_markdown="...", ...)`` the answer
arrives as a stream of raw JSON argument fragments from the transport layer.
``StreamingJsonStringExtractor`` decodes the value of one named string key as
those fragments arrive, so the agent loop can emit ``TEXT_MESSAGE_CONTENT``
deltas in real time rather than waiting for the complete tool call.

Handles all JSON string escape sequences, including ``\\\\``, ``\\"``,
``\\n``/``\\r``/``\\t``/``\\b``/``\\f``, and ``\\uXXXX`` — each of which can
be split across fragment boundaries.

Ollama limitation: Ollama delivers tool call arguments as a single chunk
(no incremental streaming), so local models get exactly one delta.  This is
acceptable and requires no special handling here — ``feed()`` is simply called
once with the full JSON string.
"""

from __future__ import annotations

__all__ = ["StreamingJsonStringExtractor"]


class StreamingJsonStringExtractor:
    """Yield newly-decoded characters of one top-level JSON string key from
    a JSON document arriving in fragments.

    Usage::

        extractor = StreamingJsonStringExtractor("answer_markdown")
        for fragment in transport_deltas:
            decoded = extractor.feed(fragment)
            if decoded:
                emit(TEXT_MESSAGE_CONTENT, delta=decoded)
            if extractor.done:
                break

    Calling ``feed()`` after ``done`` is True is a no-op.
    """

    # Internal parser states
    _SCANNING_KEY = "scanning_key"
    _SCANNING_COLON = "scanning_colon"
    _SCANNING_VALUE_OPEN = "scanning_value_open"
    _IN_STRING = "in_string"
    _ESCAPE = "escape"
    _UNICODE_ESCAPE = "unicode_escape"
    _DONE = "done"

    def __init__(self, key: str) -> None:
        self._key = key
        # The literal substring we scan for: `"key":`
        # We match character by character so we handle partial delivery.
        self._target = f'"{key}"'
        self._target_pos = 0          # chars of _target matched so far
        self._state = self._SCANNING_KEY

        # Escape accumulation
        self._escape_char: str = ""
        self._unicode_buf: str = ""   # accumulates 4 hex digits for \uXXXX

    @property
    def done(self) -> bool:
        return self._state == self._DONE

    def feed(self, fragment: str) -> str:
        """Feed the next fragment, returning any newly decoded characters.

        Returns an empty string when the fragment carries no extractable
        characters (e.g. we are still scanning for the key or whitespace
        between the colon and the opening quote).
        """
        if self._state == self._DONE:
            return ""

        out: list[str] = []
        for ch in fragment:
            self._step(ch, out)
            if self._state == self._DONE:
                break
        return "".join(out)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _step(self, ch: str, out: list[str]) -> None:  # noqa: PLR0912, C901
        state = self._state

        if state == self._SCANNING_KEY:
            if ch == self._target[self._target_pos]:
                self._target_pos += 1
                if self._target_pos == len(self._target):
                    self._state = self._SCANNING_COLON
                    self._target_pos = 0
            else:
                # Mismatch — reset and try again from the start (greedy).
                # If the mismatching char itself starts the target, count it.
                self._target_pos = 1 if ch == self._target[0] else 0

        elif state == self._SCANNING_COLON:
            if ch == ":":
                self._state = self._SCANNING_VALUE_OPEN
            # else: whitespace between key and colon — just skip

        elif state == self._SCANNING_VALUE_OPEN:
            if ch == '"':
                self._state = self._IN_STRING
            # else: whitespace between colon and value — just skip

        elif state == self._IN_STRING:
            if ch == '"':
                # End of the string value
                self._state = self._DONE
            elif ch == "\\":
                self._state = self._ESCAPE
                self._escape_char = ""
            else:
                out.append(ch)

        elif state == self._ESCAPE:
            _SIMPLE = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
                       '"': '"', "\\": "\\", "/": "/"}
            if ch in _SIMPLE:
                out.append(_SIMPLE[ch])
                self._state = self._IN_STRING
            elif ch == "u":
                self._unicode_buf = ""
                self._state = self._UNICODE_ESCAPE
            else:
                # Unknown escape — emit as-is (lenient)
                out.append(ch)
                self._state = self._IN_STRING

        elif state == self._UNICODE_ESCAPE:
            self._unicode_buf += ch
            if len(self._unicode_buf) == 4:
                try:
                    out.append(chr(int(self._unicode_buf, 16)))
                except ValueError:
                    # Invalid hex — emit the raw escape sequence as-is
                    out.append(f"\\u{self._unicode_buf}")
                self._unicode_buf = ""
                self._state = self._IN_STRING

"""KB upload SSE parsing must not depend on where socket reads split the stream."""

from __future__ import annotations

import json

from kb_upload_sse import parse_kb_upload_response


class _Resp:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}
    url = "http://x/upload"

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.text = b"".join(chunks).decode()

    def iter_content(self, chunk_size: int | None = None):  # noqa: ANN201, ARG002
        yield from self._chunks


def test_back_to_back_events_split_on_the_separator_stay_separate() -> None:
    a = json.dumps({"recordId": "r1", "fileName": "a"})
    b = json.dumps({"recordId": "r2", "fileName": "b"})
    body = f": connected\n\nevent: file:succeeded\ndata: {a}\n\nevent: file:succeeded\ndata: {b}\n\nevent: done\ndata: {{}}\n\n"
    cut = body.index("\n\nevent: file:succeeded\ndata: " + b) + 1  # read ends between the two newlines
    parsed = parse_kb_upload_response(_Resp([body[:cut].encode(), body[cut:].encode()]))  # type: ignore[arg-type]
    assert [r["recordId"] for r in parsed["records"]] == ["r1", "r2"]

"""Reading Server-Sent Events back out of a stream we produced ourselves.

The non-streaming chat endpoints answer by running the streaming pipeline and
draining it, rather than keeping a second copy of that pipeline. Both of them
need the same frame parsing, so it lives here instead of in one of them.
"""

import json
from typing import Any


def parse_sse_events(chunk: str) -> list[tuple[str, Any]]:
    """Parses `event: X\\ndata: Y\\n\\n` frames out of a raw SSE text chunk.

    Tolerant of a chunk holding several frames or a partial trailing one: only
    whole frames are returned. Callers drain the whole stream before deciding
    anything, so a frame split across two chunks is completed by the next
    chunk's data before any frame is parsed here, rather than being lost.
    """
    events: list[tuple[str, Any]] = []
    for raw_block in chunk.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        event_name = None
        data_line = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        if event_name is None or data_line is None:
            continue
        try:
            events.append((event_name, json.loads(data_line)))
        except json.JSONDecodeError:
            continue
    return events

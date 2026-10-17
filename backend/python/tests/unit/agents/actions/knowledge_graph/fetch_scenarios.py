"""A fixed set of fetches whose combined output is pinned by hash, so a
change to how fetch sizes records cannot alter what the model reads with
FETCH_SIZE_BY_CHARS turned off."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tests.unit.agents.actions.knowledge_graph.test_fetch_sizing import (
    _context,
    _fetch,
    _hits,
    _sheet,
    _text_record,
)

_SCENARIOS: list[tuple[str, dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = [
    ("fits", {}, [_text_record("a", 300, 150)], {}),
    ("selection", {"retrieval_service": ("b", [350, 360, 370])}, [_text_record("b", 400, 400)], {}),
    ("many small", {"retrieval_service": ("c", [2500, 2600])}, [_text_record("c", 3000, 60)], {}),
    ("two records", {"retrieval_service": ("big", [100, 300])},
     [_text_record("big", 400, 400), _text_record("small", 10, 200)], {}),
    ("sheet", {"retrieval_service": ("s", [4200])}, [_sheet("s", 5000)], {}),
    ("max_blocks", {}, [_text_record(f"m{i}", 40, 150) for i in range(3)], {"max_blocks": 10}),
    ("continuation", {}, [_text_record("k", 100, 900)], {"start_block": 40, "max_blocks": 5,
                                                        "start_block_sent": True}),
    ("small window", {"context_length": 8_192, "query": ""}, [_text_record("w", 300, 400)], {}),
    ("large window", {"context_length": 1_000_000, "query": ""},
     [_text_record(f"L{i}", 300, 400) for i in range(4)], {}),
]


async def fetch_scenarios_digest() -> str:
    digest = hashlib.sha256()
    for name, overrides, records, kwargs in _SCENARIOS:
        values = dict(overrides)
        if "retrieval_service" in values:
            values["retrieval_service"] = _hits(*values["retrieval_service"])
        context = _context(**values)
        context.graph_provider = AsyncMock()
        context.graph_provider.config_service = MagicMock()
        text = await _fetch(context, records, **kwargs)
        outcomes = json.dumps(context.tool_state.get("fetch_render_outcomes"), sort_keys=True, default=str)
        digest.update(f"{name}\n{text}\n{outcomes}\n".encode())
    return digest.hexdigest()

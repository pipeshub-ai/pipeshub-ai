"""Golden contract for the legacy chat and agent SSE streams (P0.7).

Run with `UPDATE_GOLDENS=1` to re-record; a normal run only compares. The
recorded stream is also the upstream input Node's golden suite replays, so a
re-record writes both copies and a normal run fails if they drift apart.
"""

from __future__ import annotations

import json

import pytest

from tests.contracts.golden_support import (
    GOLDEN_DIR,
    NODE_UPSTREAM_DIR,
    Scenario,
    normalize_sse,
    record_stream,
    update_goldens,
)
from tests.contracts.scenarios import SCENARIOS

# Today's loop's SSE contract, recorded without the flow guard. Its golden toolset is untagged (an MCP server's tools
# are, too), so since FG2 a second call after the first one's result is refused there; the guard is tested on its own
# (test_ux3_legacy_flow_guard.py, test_fg2_legacy_flow.py).
pytestmark = pytest.mark.usefixtures("flow_guard_off")


def _frames(sse: str) -> list[tuple[str, dict]]:
    frames = []
    for block in sse.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        assert len(lines) == 2, f"frame is not exactly one event line and one data line: {block!r}"
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        event = lines[0][len("event: "):]
        data = json.loads(lines[1][len("data: "):])
        assert data.get("type") == event, f"data.type {data.get('type')!r} != event {event!r}"
        frames.append((event, data))
    return frames


def _compare_or_write(path, actual: str) -> None:
    if update_goldens():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path}; record it with UPDATE_GOLDENS=1"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{path.name} drifted from the legacy contract. If the change is intended, "
        "re-record with UPDATE_GOLDENS=1 and review the diff."
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
async def test_stream_matches_golden(scenario: Scenario) -> None:
    stream = normalize_sse(await record_stream(scenario))
    frames = _frames(stream)
    assert frames, "the stream produced no frames"
    terminal = frames[-1]
    assert terminal[0] in ("RUN_FINISHED", "RUN_ERROR", "CUSTOM"), terminal

    _compare_or_write(GOLDEN_DIR / f"{scenario.name}.sse", stream)
    if NODE_UPSTREAM_DIR.parent.exists():
        _compare_or_write(NODE_UPSTREAM_DIR / f"{scenario.name}.sse", stream)


def test_normalizer_only_replaces_ids_and_timestamps() -> None:
    raw = (
        'data: {"a": "7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11", "b": "7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11", '
        '"m": "msg_4b239d24f827", "t": "2026-09-26T12:14:45.743763+00:00", "n": 30, "x": "msg_short"}'
    )
    assert normalize_sse(raw) == (
        'data: {"a": "uuid-1", "b": "uuid-1", "m": "msg-2", "t": "<timestamp>", "n": 30, "x": "msg_short"}'
    )


def test_goldens_change_only_with_update_flag(tmp_path, monkeypatch) -> None:
    golden = tmp_path / "scenario.sse"
    golden.write_text("recorded", encoding="utf-8")

    monkeypatch.delenv("UPDATE_GOLDENS", raising=False)
    with pytest.raises(AssertionError):
        _compare_or_write(golden, "different")
    assert golden.read_text(encoding="utf-8") == "recorded"

    monkeypatch.setenv("UPDATE_GOLDENS", "1")
    _compare_or_write(golden, "different")
    assert golden.read_text(encoding="utf-8") == "different"

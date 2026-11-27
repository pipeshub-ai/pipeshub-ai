"""PX-3 T12: a JSON, YAML, HTML or EPUB record reserves exactly what it reserved on ``origin/main``.

Nothing in PX-3's loop-to-thread and yield work moves a parse into a worker, so no reservation may
change. The pinned figures (``fixtures/px3_reservation_main.json``) were computed on ``origin/main``
(5f4b1349c9) by ``kind_reservation_cost`` for each format, tier, working-set source and size. The same
figures must come out in every pool mode: lanes off and on, ``PARSE_ADMISSION=classes``,
``PIPESHUB_CAPACITY_PROFILE=auto``, with the offload flag set or not. A later slice that refits a
format's charge only when its hop actually ran must keep this test green for every case where the hop
did not run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.resource_governor.kinds import classify_kind
from app.services.resource_governor.memory_budget import kind_reservation_cost
from app.services.resource_governor.models import ParseTier
from app.services.resource_governor.policy import HeavyWorkingSet

PINNED = json.loads((Path(__file__).parent / "fixtures" / "px3_reservation_main.json").read_text())
LABELS = {
    "json": ("json", "application/json"),
    "yaml": ("yaml", "application/x-yaml"),
    "html": ("html", "text/html"),
    "epub": ("epub", "application/epub+zip"),
}
MODES = {
    "lanes-off": {"PARSE_LANES": "off"},
    "lanes-on": {"PARSE_LANES": "on"},
    "lanes-on-flag-on": {"PARSE_LANES": "on", "PARSE_STRUCTURED_OFFLOAD": "on"},
    "flag-on-lanes-off": {"PARSE_LANES": "off", "PARSE_STRUCTURED_OFFLOAD": "on"},
    "classes": {"PARSE_ADMISSION": "classes"},
    "capacity-auto": {"PIPESHUB_CAPACITY_PROFILE": "auto"},
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_reservation_equals_main_unless_hop_ran(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    for key, value in MODES[mode].items():
        monkeypatch.setenv(key, value)
    seen = 0
    for key, (kind_value, cost) in PINNED.items():
        fmt, tier, source, size = key.split("|")
        ext, mime = LABELS[fmt]
        kind = classify_kind(ext, mime)
        got = kind_reservation_cost(ParseTier[tier], int(size), kind, HeavyWorkingSet(0.5, source))
        assert (kind.value, got) == (kind_value, cost), key
        seen += 1
    assert seen == len(LABELS) * 2 * 2 * 4


def test_epub_keeps_the_unknown_floor() -> None:
    """EPUB has no kind of its own yet: it is charged the UNKNOWN fallback, as on main."""
    assert classify_kind("epub", "application/epub+zip").value == "unknown"

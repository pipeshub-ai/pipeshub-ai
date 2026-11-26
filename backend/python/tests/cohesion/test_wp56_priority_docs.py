"""WP56-CLASS-1: the operator documentation of when an answered run's class rises says what the code does.

The class rises at the run's next claim; the documentation also covers the engine path, whose Model Gateway calls read
the class from the run's model grant and so rise within the attempt at the grant cache's next read. FLAGS.md "Priority
classes in flight" names that bound; this pins it to ``CACHE_TTL_S`` and to the module docstring, so a change to the
cache's lifetime cannot leave the operator text behind.
"""

from __future__ import annotations

from pathlib import Path

import harness_core.priority as priority
from app.harness.access.grants import CACHE_TTL_S

FLAGS = Path(__file__).resolve().parents[4] / "docs" / "agent-harness-v2" / "FLAGS.md"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def test_wp56_class_1_the_engine_paths_class_rise_is_documented_with_the_grant_caches_bound() -> None:
    seconds = int(CACHE_TTL_S)
    section = _section(FLAGS.read_text(encoding="utf-8"), "## Priority classes in flight")
    assert f"up to {seconds} seconds per attempt" in section
    assert "`CACHE_TTL_S`" in section and "next claim" in section and "approval_resume" in section
    assert f"up to {seconds} s per attempt" in (priority.__doc__ or "")


def test_wp56_fair_8_the_shared_upstream_rule_is_documented() -> None:
    section = _section(FLAGS.read_text(encoding="utf-8"), "## Priority classes in flight")
    assert "org holding fewest" in section and "never its id" in section


def test_wp56_fair_10_the_fair_scan_bound_is_documented() -> None:
    from app.harness.provider_gate import FAIR_SCAN

    section = _section(FLAGS.read_text(encoding="utf-8"), "## Priority classes in flight")
    assert f"first {FAIR_SCAN} waiting calls" in section and "queue order" in section

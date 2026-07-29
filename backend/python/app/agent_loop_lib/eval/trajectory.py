"""Trajectory export (Phase 5, Hermes pattern): turn the timeline —
already recorded for every run via `obs.append_timeline()` — into JSONL
trajectories suitable for offline eval harnesses or RL fine-tuning
datasets. Deliberately standalone: no CLI wiring, no dependency on any
one TimelineStore implementation beyond the abstract interface, so it
can be pointed at `InMemoryTimelineStore` or anything else that
satisfies `TimelineStore`.

A "trajectory" here is one run's ordered sequence of timeline steps
(one JSON object per line, one line per run) — the natural unit for
both grading (Phase 5's rubric harness scores a run) and RL (a run is
an episode). Multi-agent traces (parent + spawned sub-agents sharing a
trace_id) export as one trajectory per run_id, with `parent_run_id`
preserved so relationships can be reconstructed downstream.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry, TimelineStore


def timeline_entry_to_step(entry: TimelineEntry) -> dict[str, Any]:
    """Flatten a single TimelineEntry into a plain-dict trajectory step."""
    return {
        "sequence_id": entry.sequence_id,
        "timestamp": entry.timestamp,
        "status": entry.status.value if hasattr(entry.status, "value") else str(entry.status),
        "event_type": entry.event_type,
        "summary": entry.summary,
        "detail": entry.detail,
    }


def entries_to_trajectory(entries: list[TimelineEntry]) -> dict[str, Any]:
    """Group one run's entries (already filtered to a single run_id) into
    a single trajectory record. Entries are sorted by sequence_id so the
    resulting `steps` list reflects actual execution order regardless of
    the order they were passed in."""
    if not entries:
        raise ValueError("entries_to_trajectory() requires at least one TimelineEntry")

    ordered = sorted(entries, key=lambda e: e.sequence_id)
    first = ordered[0]
    return {
        "run_id": first.run_id,
        "trace_id": first.trace_id,
        "agent_id": first.agent_id,
        "parent_run_id": first.parent_run_id,
        "role_name": first.role_name,
        "model": first.model,
        "final_status": ordered[-1].status.value if hasattr(ordered[-1].status, "value") else str(ordered[-1].status),
        "steps": [timeline_entry_to_step(e) for e in ordered],
    }


def entries_to_trajectories(entries: list[TimelineEntry]) -> list[dict[str, Any]]:
    """Split a flat list of entries (e.g. an entire trace, spanning a
    parent run and any spawned sub-agent runs) into one trajectory per
    run_id. Trajectories are ordered by each run's first sequence_id."""
    by_run: dict[str, list[TimelineEntry]] = {}
    for entry in entries:
        by_run.setdefault(entry.run_id, []).append(entry)

    trajectories = [entries_to_trajectory(run_entries) for run_entries in by_run.values()]
    trajectories.sort(key=lambda t: t["steps"][0]["sequence_id"])
    return trajectories


async def export_run_trajectory(timeline: TimelineStore, run_id: str) -> dict[str, Any]:
    """Fetch and convert a single run's timeline into one trajectory dict."""
    entries = await timeline.get_by_run(run_id)
    return entries_to_trajectory(entries)


async def export_trace_trajectories(timeline: TimelineStore, trace_id: str) -> list[dict[str, Any]]:
    """Fetch and convert an entire trace (parent run + any sub-agent runs
    sharing the same trace_id) into one trajectory per run."""
    entries = await timeline.get_by_trace(trace_id)
    return entries_to_trajectories(entries)


def write_jsonl(trajectories: list[dict[str, Any]], path: str | Path) -> None:
    """Write trajectories as JSONL, one JSON object per line — the
    standard format for eval/RL dataset ingestion."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for trajectory in trajectories:
            f.write(json.dumps(trajectory) + "\n")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read trajectories back from a JSONL file."""
    with Path(path).open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def export_trace_to_jsonl(timeline: TimelineStore, trace_id: str, path: str | Path) -> list[dict[str, Any]]:
    """Convenience: fetch a trace's trajectories and write them straight
    to a JSONL file. Returns the trajectories written."""
    trajectories = await export_trace_trajectories(timeline, trace_id)
    write_jsonl(trajectories, path)
    return trajectories

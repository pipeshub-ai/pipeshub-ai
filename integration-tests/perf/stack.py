"""Shared plumbing for the performance benchmarks: log in, seed a knowledge
base with a synthetic corpus, and watch records until the indexer is done.

``bench_indexing.py`` measures that seeding; ``bench_query.py`` only needs it
to finish before it starts asking questions. Both drive the same public API the
integration tests use, with their clients and their login bootstrap.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from corpus import Corpus, CorpusFile

SUCCESS_STATUS = "COMPLETED"
# Every status a record can rest in once the indexer is done with it
# (docs/indexing-service.md, section 2.1). The rest are still in flight.
FINAL_STATUSES = frozenset({
    "COMPLETED",
    "FAILED",
    "EMPTY",
    "FILE_TYPE_NOT_SUPPORTED",
    "AUTO_INDEX_OFF",
    "ENABLE_MULTIMODAL_MODELS",
})
RECORDS_PAGE_LIMIT = 200  # the most the Knowledge Hub listing accepts

_IT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class RunState:
    uploaded_at: dict[str, float] = field(default_factory=dict)
    file_of: dict[str, CorpusFile] = field(default_factory=dict)
    finished_at: dict[str, float] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    reason: dict[str, str] = field(default_factory=dict)
    poll_errors: int = 0
    ended_at: float = 0.0
    stopped_early: str = ""
    upload_failures: list[dict[str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (the same definition as numpy's default)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def run_command(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(_IT_DIR / ".env", override=False)
    if os.getenv("PIPESHUB_TEST_ENV", "").strip().lower() == "local":
        load_dotenv(_IT_DIR / ".env.local", override=False)


def ensure_client_credentials(base_url: str) -> None:
    if os.getenv("CLIENT_ID") and os.getenv("CLIENT_SECRET"):
        return
    from local_auth import obtain_local_oauth_credentials

    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret


def folder_id(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("folder") or {}, payload.get("data") or {}):
        if isinstance(container, dict):
            for key in ("id", "folderId", "_key"):
                if container.get(key):
                    return str(container[key])
    raise RuntimeError(f"No folder id in the create-folder response: {payload}")


def describe_org_models(client: Any) -> dict[str, str]:
    """The org's default LLM and embedding as ``provider/model``, for the result's label."""
    described = {}
    for model_type, when_missing in (("llm", "none"), ("embedding", "none (built-in local model)")):
        try:
            resp = client.request("GET", f"/api/v1/configurationManager/ai-models/{model_type}")
            models = resp.json().get("models") or []
        except Exception:  # noqa: BLE001 - a label, not a measurement
            described[model_type] = "unknown"
            continue
        chosen = next((m for m in models if m.get("isDefault")), models[0] if models else None)
        described[model_type] = (
            f"{chosen.get('provider')}/{(chosen.get('configuration') or {}).get('model')}"
            if chosen else when_missing
        )
    return described


def create_kb(kb_client: Any, name: str, wait_seconds: float = 120) -> str:
    """Create the benchmark KB, waiting out a just-created org.

    A new admin reaches the graph through an async event, and until it lands
    the create answers 404 "User not found". CI runs this minutes after
    creating the org, so the wait is expected there, not a fault.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return kb_client.create_kb(name)["id"]
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404 or time.monotonic() > deadline:
                raise
            time.sleep(5)


def create_folders(kb_client: Any, kb_id: str, corpus: Corpus) -> dict[tuple[str, ...], str]:
    ids: dict[tuple[str, ...], str] = {}
    for path in corpus.folders:  # parents always precede their children
        parent = ids.get(path[:-1]) if len(path) > 1 else None
        ids[path] = folder_id(kb_client.create_folder(kb_id, path[-1], parent_id=parent))
    return ids


def upload_file(kb_client: Any, kb_id: str, parent_id: str | None, f: CorpusFile, state: RunState) -> None:
    try:
        result = kb_client.upload_file(kb_id, f.name, f.content, folder_id=parent_id, mimetype=f.mimetype)
    except Exception as exc:  # noqa: BLE001 - one bad upload is a result, not a crash
        with state.lock:
            state.upload_failures.append({"file": f.rel_path, "error": str(exc)[:300]})
        return
    done = time.perf_counter()
    records = result.get("records") or []
    with state.lock:
        for rec in records:
            record_id = rec.get("recordId") or rec.get("id")
            if record_id:
                state.uploaded_at[record_id] = done
                state.file_of[record_id] = f
                # A poll saw it finish before this call returned; time it from now,
                # or a run that stops before the next poll has no finish time for it.
                if state.status.get(record_id) in FINAL_STATUSES and record_id not in state.finished_at:
                    state.finished_at[record_id] = done
        if not records or result.get("failed"):
            state.upload_failures.append({"file": f.rel_path, "error": json.dumps(result.get("failed"))[:300]})


def poll_records_once(kb_client: Any, kb_id: str, state: RunState) -> None:
    """Read every record's status once. A failed read is counted and retried on
    the next poll: a slow gateway under load is part of what is being measured,
    not a reason to throw the run away."""
    page = 1
    while True:
        try:
            body = kb_client.list_records(kb_id, page=page, limit=RECORDS_PAGE_LIMIT)
        except Exception as exc:  # noqa: BLE001
            with state.lock:
                state.poll_errors += 1
            print(f"  poll failed, will retry: {str(exc)[:200]}", file=sys.stderr, flush=True)
            return
        for rec in body.get("items") or []:
            record_id = rec.get("id")
            if not record_id:
                continue
            status = rec.get("indexingStatus") or "UNKNOWN"
            with state.lock:
                state.status[record_id] = status
                if rec.get("reason"):
                    state.reason[record_id] = str(rec["reason"])[:300]
                # A record can finish before its upload call returns. It is timed
                # from the first poll after that, never from before its upload.
                if (
                    status in FINAL_STATUSES
                    and record_id in state.uploaded_at
                    and record_id not in state.finished_at
                ):
                    state.finished_at[record_id] = time.perf_counter()
        total_pages = (body.get("pagination") or {}).get("totalPages") or 1
        if page >= total_pages:
            return
        page += 1


def unlisted_records(state: RunState, now: float, grace: float) -> list[str]:
    """Uploaded records the listing has never shown, ``grace`` seconds after their upload."""
    with state.lock:
        return [r for r, at in state.uploaded_at.items() if r not in state.status and now - at > grace]


def should_stop_waiting(state: RunState, uploads_done: bool, now: float, grace: float) -> str:
    """Why to stop polling, or ``""`` to keep going.

    Stops when every record is finished, and also when all that is left are
    records the listing has never shown: a status endpoint that has stopped
    returning them would otherwise look like slow indexing until the timeout.
    """
    if not uploads_done:
        return ""
    with state.lock:
        pending = {r for r in state.uploaded_at if r not in state.finished_at}
        nothing_uploaded = not state.uploaded_at
        upload_failures = len(state.upload_failures)
    if nothing_uploaded:
        # Every upload failed, so there is nothing to wait for. Without this the
        # empty pending set below reads as "everything finished".
        return (
            f"no file reached the knowledge base: all {upload_failures} upload(s) failed"
            if upload_failures else "no file was uploaded, so there was nothing to index"
        )
    if not pending:
        return "done"
    unlisted = set(unlisted_records(state, now, grace))
    if pending <= unlisted:
        return (
            f"{len(unlisted)} uploaded record(s) never appeared in the knowledge base's record "
            f"listing within {grace:.0f}s of upload; the listing endpoint is not reporting them"
        )
    return ""


def host_memory_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024**2, 1)
    except OSError:
        pass
    return None

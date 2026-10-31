"""Automatic quarantine: a test flaky in 3 of its last 10 runs stops blocking for 14 days.

Every entry links an issue. An entry is released early after 10 straight passes, and is
never renewed: once it expires the id waits out a cooldown, so only a human can make a
quarantine last (by adding it to quarantine.toml with an owner).

`update` is pure, so it can run inside a branchstore mutation. Create issues with
`ensure_issue` before the store write; it reuses an open issue, so a retry never duplicates one.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from typing import Any

from qa import history

FLAKES = 3
EXPIRY_DAYS = 14
COOLDOWN_DAYS = history.COOLDOWN_DAYS
LABEL = "qa-flaky"
OWNER = "qa-auto"

_labelled = False


def _last(letters: str) -> str:
    return letters[-history.WINDOW:]


def _held(entry: dict[str, Any], today: dt.date) -> bool:
    """Active, or expired but still in its cooldown."""
    return dt.date.fromisoformat(str(entry["expires"])) + dt.timedelta(days=COOLDOWN_DAYS) >= today


def candidates(windows: dict[str, str], current: list[dict[str, Any]], static_ids: set[str], today: dt.date) -> list[str]:
    held = {e["id"] for e in current if _held(e, today)} | static_ids
    return sorted(tid for tid, w in windows.items() if tid not in held and _last(w).count("k") >= FLAKES)


def update(current: list[dict[str, Any]], windows: dict[str, str], issues: dict[str, str],
           static_ids: set[str], today: dt.date) -> list[dict[str, Any]]:
    out = []
    for e in current:
        if not _held(e, today):
            continue
        if dt.date.fromisoformat(str(e["expires"])) < today:
            e = {**e, "expired": True}
        elif _last(windows.get(e["id"], "")) == "p" * history.WINDOW:
            continue
        out.append(e)
    for tid in candidates(windows, out, static_ids, today):
        if not issues.get(tid):
            continue  # no issue yet: the test waits for the next publish
        w = _last(windows[tid])
        out.append({
            "id": tid, "owner": OWNER, "issue": issues[tid], "auto": True,
            "expires": (today + dt.timedelta(days=EXPIRY_DAYS)).isoformat(), "added": today.isoformat(),
            "reason": f"flaky in {w.count('k')} of the last {len(w)} runs: {w}",
        })
    return out


def _gh(args: list[str]) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {r.stderr.strip() or f'exit {r.returncode}'}")
    return r.stdout


def ensure_issue(repo: str, test_id: str, letters: str) -> str | None:
    """The URL of the open `Flaky test: <id>` issue, created if missing; None on any gh error."""
    global _labelled
    title = f"Flaky test: {test_id}"
    try:
        if not _labelled:
            _labelled = True
            _gh(["label", "create", LABEL, "--force", "--repo", repo, "--color", "fbca04",
                 "--description", "Quarantined automatically as flaky"])
        # shortcut: at most 1000 open qa-flaky issues are checked; past that a duplicate is possible, so page with --paginate via gh api then.
        # A plain label listing, not search: the search index lags, and a missed issue would be duplicated.
        listed = json.loads(_gh(["issue", "list", "--repo", repo, "--label", LABEL, "--state", "open",
                                 "--limit", "1000", "--json", "title,url"]) or "[]")
        for issue in listed:
            if issue.get("title") == title:
                return issue["url"]
        w = _last(letters)
        body = (f"`{test_id}` was flaky in {w.count('k')} of its last {len(w)} runs (`{w}`, newest last).\n\n"
                f"It is quarantined for {EXPIRY_DAYS} days: it still runs, but cannot block a merge. "
                f"Ten straight passes release it early. It is not renewed automatically.")
        return _gh(["issue", "create", "--repo", repo, "--title", title, "--label", LABEL, "--body", body]).strip() or None
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        print(f"warning: no issue for {test_id}, quarantine deferred: {err}", file=sys.stderr)
        return None

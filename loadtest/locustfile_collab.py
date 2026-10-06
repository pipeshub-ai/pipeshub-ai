"""Collaborative-chats feed poll at scale (PH-12 PERF-02): the manual 2k-viewer, 5-replica run.

Every simulated viewer is a real account (`PIPESHUB_USERS`): the team-id cache and the per-user
`collab:feed` limiter are keyed by user, so a shared identity would measure a hit rate and a limit
nobody sees in production. A viewer logs in once at test start, lists the chats it can reach (its own
and the ones shared with it) and keeps one to eight of them open, each polling

    GET /api/v1/conversations/<id>/feed?afterSeq=<n>&rev=<rev>

every ~4 s while the window is active and every ~15 s otherwise (+-20% jitter), the browser's cadence.
Poll 304 is a success. Anything else (429 included) is a failure, so the 429s of the per-user limit show
up in the failure table.

    export PIPESHUB_USERS='u1@example.com:Pass1!,...'          # as many accounts as viewers
    locust -f locustfile_collab.py --headless -u 2000 -r 25 -t 15m -H http://<load balancer>

The run decides PERF-02: the exit code is 1 when the feed p95 is over `PIPESHUB_COLLAB_P95_MS` (50) or any
poll fails. Seed the chats first (`integration-tests/perf/collab_seed.py`, see loadtest/README.md).
"""

from __future__ import annotations

import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

import gevent
from locust import HttpUser, constant, events, task

sys.path.insert(0, str(Path(__file__).parent))
from pipeshub_auth import AuthError, credentials_from_env, resolve_tokens  # noqa: E402

ACTIVE_INTERVAL_S = float(os.environ.get("PIPESHUB_COLLAB_ACTIVE_S", "4"))
IDLE_INTERVAL_S = float(os.environ.get("PIPESHUB_COLLAB_IDLE_S", "15"))
JITTER = 0.20
ACTIVE_FRACTION = float(os.environ.get("PIPESHUB_COLLAB_ACTIVE_FRACTION", "0.5"))
P95_BUDGET_MS = float(os.environ.get("PIPESHUB_COLLAB_P95_MS", "50"))
LIST_PAGE_SIZE = 20
WINDOWS_PER_VIEWER = ((1, 0.50), (2, 0.25), (3, 0.12), (5, 0.08), (8, 0.05))
FEED_NAME = "GET /conversations/:id/feed"

TOKENS: list[str] = []
STATUS: Counter[int] = Counter()
_next_identity = iter(range(10**9))


@events.test_start.add_listener
def _resolve_identities(environment, **_kwargs) -> None:
    global TOKENS
    try:
        credentials = credentials_from_env()
    except AuthError as e:
        raise SystemExit(str(e)) from e
    if not credentials:
        raise SystemExit(
            "No accounts. Set PIPESHUB_USERS='email:password,...' (see seed_users.py): the team-id cache and the "
            "feed limiter are per user, so one shared token would not measure them."
        )
    host = environment.host or os.environ.get("PIPESHUB_HOST", "http://localhost:3000")
    try:
        TOKENS = resolve_tokens(host, credentials)
    except AuthError as e:
        raise SystemExit(f"Could not log in load-test users: {e}") from e
    print(f"[loadtest] {len(TOKENS)} accounts logged in")


@events.test_stop.add_listener
def _report(environment, **_kwargs) -> None:
    total = sum(STATUS.values())
    if not total:
        return
    print(
        f"[loadtest] feed polls {total}: 200={STATUS[200]} 304={STATUS[304]} ({STATUS[304] / total:.1%}) "
        f"429={STATUS[429]} other={total - STATUS[200] - STATUS[304] - STATUS[429]}"
    )


@events.quitting.add_listener
def _verdict(environment, **_kwargs) -> None:
    stats = environment.stats.get(FEED_NAME, "GET")
    p95 = stats.get_response_time_percentile(0.95) if stats.num_requests else None
    failed = environment.stats.total.num_failures
    ok = p95 is not None and p95 < P95_BUDGET_MS and failed == 0
    print(f"[loadtest] PERF-02: p95 {p95} ms (budget {P95_BUDGET_MS}), {failed} failed polls: {'PASS' if ok else 'FAIL'}")
    if not ok:
        environment.process_exit_code = 1


class Window:
    def __init__(self, conversation_id: str, active: bool, now: float, rng: random.Random) -> None:
        self.conversation_id = conversation_id
        self.interval = ACTIVE_INTERVAL_S if active else IDLE_INTERVAL_S
        self.rev: int | None = None
        self.after = -1
        self.due = now + rng.uniform(0, self.interval)

    def reschedule(self, now: float, rng: random.Random) -> None:
        self.due = now + self.interval * (1 + rng.uniform(-JITTER, JITTER))


class CollabViewer(HttpUser):
    """One account with several chat windows open; a single loop polls whichever is due next."""

    wait_time = constant(0)

    def on_start(self) -> None:
        self.rng = random.Random()
        self.headers = {"Authorization": f"Bearer {TOKENS[next(_next_identity) % len(TOKENS)]}"}
        reachable = self._reachable_chats()
        counts, weights = zip(*WINDOWS_PER_VIEWER)
        wanted = self.rng.choices(counts, weights=weights)[0]
        chosen = self.rng.sample(reachable, min(wanted, len(reachable)))
        active = max(1, round(len(chosen) * ACTIVE_FRACTION))
        now = time.monotonic()
        self.windows = [Window(cid, i < active, now, self.rng) for i, cid in enumerate(chosen)]

    def _reachable_chats(self) -> list[str]:
        """The chats this account owns plus the ones shared with it: what its sidebar would list."""
        ids: list[str] = []
        for source in ("owned", "shared"):
            with self.client.get(
                "/api/v1/conversations",
                params={"source": source, "page": 1, "limit": LIST_PAGE_SIZE},
                headers=self.headers,
                name="GET /conversations (setup)",
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"HTTP {resp.status_code}")
                    continue
                ids += [c["_id"] for c in resp.json().get("conversations", []) if c.get("_id")]
        if not ids:
            raise SystemExit("An account can reach no chat. Seed the chats and share them with the accounts first.")
        return ids

    @task
    def poll_next_window(self) -> None:
        window = min(self.windows, key=lambda w: w.due)
        wait = window.due - time.monotonic()
        if wait > 0:
            gevent.sleep(wait)
        params: dict[str, int] = {"afterSeq": window.after}
        if window.rev is not None:
            params["rev"] = window.rev
        with self.client.get(
            f"/api/v1/conversations/{window.conversation_id}/feed",
            params=params,
            headers=self.headers,
            name=FEED_NAME,
            catch_response=True,
        ) as resp:
            STATUS[resp.status_code] += 1
            if resp.status_code == 200:
                body = resp.json()
                window.rev = body.get("rev", window.rev)
                window.after = body.get("nextSeq", window.after)
                resp.success()
            elif resp.status_code == 304:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")
        window.reschedule(time.monotonic(), self.rng)

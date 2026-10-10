"""Feed-poll load for collaborative chats (PH-12 PERF-02, PERF-04).

Boots the lane (real Node API on its own Mongo replica set and Redis, plus the fake of the Python
service that serves team ids), seeds a background corpus and a viewer population, then has every
visible chat window poll ``GET /conversations/:id/feed?afterSeq=&rev=`` the way the browser does:

* a window polls every 4 s while it is the active one and every 15 s otherwise, each interval jittered by +-20%;
* each viewer has one to eight windows (``--active-fraction`` of them active), and every window sits on its own chat;
* the population is 60% owners or direct collaborators, 30% reachable only through a team and 10% through a project;
* a writer bumps a chat's ``rev`` and adds a message about every ``--change-interval`` seconds per chat, so
  most polls are 304 and some are 200s.

Reported: p50/p95/p99 (all, 200 and 304), the 304 share, 429s and other errors, Python team-id calls
(total and by population, from the fake's request log), and the deltas of ``collab_teamids_cache_total``
and ``collab_feed_poll_total``. PERF-04 is judged on load, not hit rate (a hit rate rises with the poll rate): upstream team-id
lookups per active user per minute, the p95 of ``collab_teamids_upstream_seconds`` and the cache error ratio. The metrics are read from the Node process through
``collab_metrics_probe.cjs`` because the API has no scrape endpoint.

``compare.py`` judges the result against ``baselines/ci-collab-neo4j-4cpu.json``. The per-user ``collab:feed``
limit is 60/min per replica, so a viewer with several active windows is refused: the 429 count is reported as measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IT_DIR = Path(__file__).resolve().parent.parent
if str(IT_DIR) not in sys.path:
    sys.path.insert(0, str(IT_DIR))
PERF_DIR = Path(__file__).resolve().parent
if str(PERF_DIR) not in sys.path:
    sys.path.insert(0, str(PERF_DIR))

import aiohttp  # noqa: E402
import requests  # noqa: E402
from bson import ObjectId  # noqa: E402

import collab_seed  # noqa: E402
from collab_seed import SeedPlan  # noqa: E402
from helper.collab_stack.identity import Actor, Directory  # noqa: E402
from helper.collab_stack.seeds import insert_project, insert_session, project_member, team_row, user_row  # noqa: E402

RESULTS_DIR = PERF_DIR / "results"
PROBE = PERF_DIR / "collab_metrics_probe.cjs"
FEED = "/api/v1/conversations/{sid}/feed"

ACTIVE_INTERVAL_S = 4.0
IDLE_INTERVAL_S = 15.0
JITTER = 0.20
MIX = {"owner_direct": 0.60, "team_only": 0.30, "project": 0.10}
WINDOWS_PER_VIEWER = ((1, 0.50), (2, 0.25), (3, 0.12), (5, 0.08), (8, 0.05))
BENCH_TEAMS = 20
TEAMS_PER_TEAM_VIEWER = 2
P95_BUDGET_S = 0.050
UPSTREAM_PER_USER_MIN_CEILING = 3.0
MISS_P95_BUDGET_S = 0.100
ERROR_RATIO_CEILING = 0.05


@dataclass
class Window:
    viewer: int
    session_id: str
    population: str
    active: bool
    rev: int | None = None
    after: int = -1


@dataclass
class Viewer:
    actor: Actor
    token: str
    population: str
    windows: list[Window] = field(default_factory=list)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def latency_summary(values: list[float]) -> dict[str, float | None]:
    return {"p50": percentile(values, 50), "p95": percentile(values, 95), "p99": percentile(values, 99), "max": max(values) if values else None}


# ---- population -----------------------------------------------------------------------------


def seed_population(stack: Any, viewers: int, rng: random.Random, active_fraction: float) -> list[Viewer]:
    """Viewers, their teams and projects, and one chat per window, written straight into Mongo."""
    db, fake = stack.db, stack.fake
    owner: Actor = stack.roster.owner
    directory = Directory(db, orgs={"acme": owner.org_id})
    populations = rng.choices(list(MIX), weights=list(MIX.values()), k=viewers)
    counts = [c for c, _ in WINDOWS_PER_VIEWER]
    weights = [w for _, w in WINDOWS_PER_VIEWER]
    team_members: dict[str, dict[str, str]] = defaultdict(dict)
    out: list[Viewer] = []
    for index, population in enumerate(populations):
        actor = directory.user(f"PollViewer{index:04d}")
        viewer = Viewer(actor, Directory.session_token(actor, expires_in=6 * 3600), population)
        n_windows = rng.choices(counts, weights=weights)[0]
        n_active = max(1, round(n_windows * active_fraction))
        teams = [f"bench-team-{t:02d}" for t in rng.sample(range(BENCH_TEAMS), TEAMS_PER_TEAM_VIEWER)]
        project_id = None
        if population == "team_only":
            for team in teams:
                team_members[team][actor.user_id] = "member"
        if population == "project":
            project_id = insert_project(db, f"bench-project-{index}", owner, [project_member(actor, "viewer", owner)], visibility="private")
        for w in range(n_windows):
            key = f"poll:{index}:{w}"
            if population == "owner_direct" and w % 2 == 0:
                seeded = insert_session(db, key, actor, messages=_turns(3))
            elif population == "owner_direct":
                seeded = insert_session(db, key, owner, shared_with=[user_row(actor, "write", principal_type=True)], messages=_turns(3))
            elif population == "team_only":
                seeded = insert_session(db, key, owner, shared_with=[team_row(teams[w % len(teams)])], messages=_turns(3))
            else:
                seeded = insert_session(db, key, owner, project=project_id, project_visibility="project", messages=_turns(3))
            viewer.windows.append(Window(index, seeded.sid, population, active=w < n_active))
        out.append(viewer)
    for team, members in team_members.items():
        fake.add_team(owner.org_id, team, members, team)
    return out


def _turns(n: int) -> list[tuple[str, str]]:
    return [("user_query" if i % 2 == 0 else "bot_response", f"turn {i}") for i in range(n)]


# ---- metrics --------------------------------------------------------------------------------

_SAMPLE = re.compile(r'^(?P<name>collab_[a-z_]+)\{(?P<label>[a-z]+)="(?P<value>[^"]*)"\} (?P<n>[0-9.e+]+)$', re.M)


def scrape(probe_url: str) -> dict[str, dict[str, float]]:
    text = requests.get(probe_url, timeout=10).text
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for m in _SAMPLE.finditer(text):
        out[m["name"]][m["value"]] = float(m["n"])
    return out


# prom-client writes `le` first (`{le="0.005",result="ok"}`); match the label anywhere in the set.
_BUCKET = re.compile(r'^collab_teamids_upstream_seconds_bucket\{(?P<labels>[^}]*)\} (?P<n>[0-9.e+]+)$', re.M)
_LE = re.compile(r'(?:^|,)le="(?P<le>[^"]+)"')


def parse_upstream_buckets(text: str) -> dict[float, float]:
    """Cumulative ``collab_teamids_upstream_seconds`` bucket counts summed over the result label, keyed by upper bound."""
    out: dict[float, float] = defaultdict(float)
    for m in _BUCKET.finditer(text):
        le = _LE.search(m["labels"])
        if le:
            out[float(le["le"])] += float(m["n"])
    return dict(out)


def scrape_upstream_buckets(probe_url: str) -> dict[float, float]:
    return parse_upstream_buckets(requests.get(probe_url, timeout=10).text)


def bucket_percentile(before: dict[float, float], after: dict[float, float], pct: float) -> float | None:
    """Upper bound of the first bucket holding ``pct`` of the observations made between two scrapes."""
    bounds = sorted(set(before) | set(after))
    counts = [(b, after.get(b, 0.0) - before.get(b, 0.0)) for b in bounds]
    total = counts[-1][1] if counts else 0.0
    if total <= 0:
        return None
    target = total * pct / 100
    for bound, cumulative in counts:
        if cumulative >= target:
            return None if bound == float("inf") else bound
    return None


def upstream_stats(before: dict, after: dict, buckets_before: dict[float, float], buckets_after: dict[float, float], active_users: int, elapsed: float, team_only_users: int = 0) -> dict[str, Any]:
    """Team-id lookups that reached the connector, per active user per minute, with the miss p95."""
    lookups = delta(before, after, "collab_teamids_upstream_seconds_count")
    total = sum(lookups.values())
    minutes = elapsed / 60
    return {
        "lookups": total,
        "errors": lookups.get("error", 0),
        "active_users": active_users,
        "per_active_user_per_minute": total / active_users / minutes if active_users and minutes else None,
        "team_only_users": team_only_users,
        "per_team_only_viewer_per_minute": total / team_only_users / minutes if team_only_users and minutes else None,
        "miss_p95_seconds": bucket_percentile(buckets_before, buckets_after, 95),
    }


def delta(before: dict[str, dict[str, float]], after: dict[str, dict[str, float]], name: str) -> dict[str, int]:
    keys = set(before.get(name, {})) | set(after.get(name, {}))
    return {k: int(after.get(name, {}).get(k, 0) - before.get(name, {}).get(k, 0)) for k in sorted(keys)}


# ---- polling --------------------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.samples: list[tuple[float, int, str, float]] = []  # (finished_at, status, population, latency)
        self.errors: Counter[str] = Counter()

    def add(self, status: int, population: str, latency: float) -> None:
        self.samples.append((time.monotonic(), status, population, latency))


async def poll_window(session: aiohttp.ClientSession, base: str, viewer: Viewer, window: Window, rec: Recorder, stop: asyncio.Event, rng: random.Random, measuring: asyncio.Event) -> None:
    interval = ACTIVE_INTERVAL_S if window.active else IDLE_INTERVAL_S
    url = base + FEED.format(sid=window.session_id)
    headers = {"Authorization": f"Bearer {viewer.token}"}
    await _sleep_or_stop(stop, rng.uniform(0, interval))
    while not stop.is_set():
        params: dict[str, Any] = {"afterSeq": window.after}
        if window.rev is not None:
            params["rev"] = window.rev
        started = time.perf_counter()
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                status = resp.status
                body = await resp.json(content_type=None) if status == 200 else await resp.read()
            latency = time.perf_counter() - started
            if status == 200 and isinstance(body, dict):
                window.rev = body.get("rev", window.rev)
                window.after = body.get("nextSeq", window.after)
            if measuring.is_set():
                rec.add(status, window.population, latency)
        except Exception as exc:  # noqa: BLE001 - counted, the run goes on
            if measuring.is_set():
                rec.errors[type(exc).__name__] += 1
                rec.add(0, window.population, time.perf_counter() - started)
        await _sleep_or_stop(stop, interval * (1 + rng.uniform(-JITTER, JITTER)))


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def writer(db: Any, windows: list[Window], change_interval_s: float, stop: threading.Event, rng: random.Random, counter: list[int]) -> None:
    """About one new turn per chat every ``change_interval_s`` seconds, spread over the whole population."""
    from datetime import datetime, timezone

    pause = change_interval_s / max(len(windows), 1)
    while not stop.wait(pause):
        window = rng.choice(windows)
        sid = ObjectId(window.session_id)
        doc = db["chatSessions"].find_one_and_update(
            {"_id": sid}, {"$inc": {"rev": 1, "nextSeq": 1}, "$set": {"lastActivityAt": int(time.time() * 1000)}}, projection={"nextSeq": 1, "orgId": 1, "userId": 1}, return_document=True
        )
        if doc is None:
            continue
        now = datetime.now(timezone.utc)
        db["chatSessionMessages"].insert_one(
            {
                "_id": ObjectId(),
                "sessionId": sid,
                "orgId": doc["orgId"],
                "seq": doc["nextSeq"],
                "schemaVersion": 1,
                "messageType": "bot_response",
                "content": f"live turn {doc['nextSeq']}",
                "contentFormat": "MARKDOWN",
                "citations": [],
                "followUpQuestions": [],
                "feedback": [],
                "requestedBy": doc["userId"],
                "createdAt": now,
                "updatedAt": now,
            }
        )
        counter[0] += 1


async def run_polling(base: str, viewers: list[Viewer], duration: float, warmup: float, rng: random.Random, rec: Recorder) -> float:
    stop, measuring = asyncio.Event(), asyncio.Event()
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [
            asyncio.create_task(poll_window(session, base, v, w, rec, stop, random.Random(rng.random()), measuring))
            for v in viewers
            for w in v.windows
        ]
        await asyncio.sleep(warmup)
        measuring.set()
        started = time.monotonic()
        await asyncio.sleep(duration)
        measuring.clear()
        elapsed = time.monotonic() - started
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    return elapsed


# ---- analysis -------------------------------------------------------------------------------


def team_call_stats(calls: list[Any], population_of: dict[str, str]) -> dict[str, Any]:
    by_population: Counter[str] = Counter()
    per_user: Counter[str] = Counter()
    stamps: dict[str, list[float]] = defaultdict(list)
    for call in calls:
        uid = call.user_id or "unknown"
        by_population[population_of.get(uid, "other")] += 1
        per_user[uid] += 1
        stamps[uid].append(call.at)
    most_in_one_second = 0
    for times in stamps.values():
        times.sort()
        lo = 0
        for hi, t in enumerate(times):
            while t - times[lo] > 1.0:
                lo += 1
            most_in_one_second = max(most_in_one_second, hi - lo + 1)
    return {
        "total": len(calls),
        "by_population": dict(by_population),
        "users_calling": len(per_user),
        "max_calls_by_one_user": max(per_user.values(), default=0),
        "max_calls_by_one_user_within_1s": most_in_one_second,
    }


def build_result(args: argparse.Namespace, plan: SeedPlan, viewers: list[Viewer], rec: Recorder, elapsed: float, before: dict, after: dict, calls: list[Any], writes: int, seed_seconds: float, buckets: tuple[dict[float, float], dict[float, float]] = ({}, {})) -> dict[str, Any]:
    status_counts = Counter(str(s) for _, s, _, _ in rec.samples)
    lat_all = [lat for _, s, _, lat in rec.samples if s in (200, 304)]
    lat_200 = [lat for _, s, _, lat in rec.samples if s == 200]
    lat_304 = [lat for _, s, _, lat in rec.samples if s == 304]
    ok = status_counts["200"] + status_counts["304"]
    population_of = {v.actor.user_id: v.population for v in viewers}
    teams = team_call_stats(calls, population_of)
    cache = delta(before, after, "collab_teamids_cache_total")
    lookups = cache.get("hit", 0) + cache.get("miss", 0) + cache.get("coalesced", 0)
    all_results = lookups + cache.get("error", 0)
    error_ratio = cache.get("error", 0) / all_results if all_results else None
    upstream = upstream_stats(before, after, buckets[0], buckets[1], len(viewers), elapsed, sum(1 for v in viewers if v.population == "team_only"))
    windows = [w for v in viewers for w in v.windows]
    by_pop: dict[str, Any] = {}
    for population in MIX:
        lats = [lat for _, s, p, lat in rec.samples if p == population and s in (200, 304)]
        by_pop[population] = {
            "viewers": sum(1 for v in viewers if v.population == population),
            "windows": sum(1 for w in windows if w.population == population),
            "polls": sum(1 for _, _, p, _ in rec.samples if p == population),
            "latency_seconds": latency_summary(lats),
            "status_counts": dict(Counter(str(s) for _, s, p, _ in rec.samples if p == population)),
        }
    metrics = {
        "requests": len(rec.samples),
        "requests_per_second": len(rec.samples) / elapsed if elapsed else None,
        "status_counts": dict(status_counts),
        "errors": sum(c for s, c in status_counts.items() if s not in ("200", "304", "429")),
        "client_exceptions": dict(rec.errors),
        "latency_seconds": latency_summary(lat_all),
        "latency_seconds_200": latency_summary(lat_200),
        "latency_seconds_304": latency_summary(lat_304),
        "not_modified_ratio": status_counts["304"] / ok if ok else None,
        "rate_limited": status_counts["429"],
        "python_team_id_calls": teams,
        "teamids_cache": {**cache, "error_ratio": error_ratio},
        "teamids_upstream": upstream,
        "feed_poll_total": delta(before, after, "collab_feed_poll_total"),
        "writer_turns_added": writes,
        "by_population": by_pop,
    }
    result = {
        "benchmark": "collab_poll",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "label": args.label,
            "graph_db": "none (the lane's fake of the Python service serves team ids)",
            "message_broker": "redis",
            "node_replicas": 1,
            "host": {"python": platform.python_version(), "cpus": __import__("os").cpu_count(), "machine": platform.machine()},
        },
        "profile": {
            "viewers": len(viewers),
            "windows": len(windows),
            "active_windows": sum(1 for w in windows if w.active),
            "duration_seconds": args.duration,
            "warmup_seconds": args.warmup,
            "cadence": {"active_seconds": ACTIVE_INTERVAL_S, "idle_seconds": IDLE_INTERVAL_S, "jitter": JITTER, "active_fraction": args.active_fraction},
            "mix": MIX,
            "windows_per_viewer": WINDOWS_PER_VIEWER,
            "change_interval_seconds": args.change_interval,
        },
        "corpus": {"sessions": plan.sessions, "teams": plan.teams, "seed": plan.seed, "seed_seconds": round(seed_seconds, 1)},
        "metrics": metrics,
    }
    result["checks"] = checks(metrics)
    return result


def checks(m: dict[str, Any]) -> list[dict[str, Any]]:
    p95 = m["latency_seconds"]["p95"]
    direct_calls = m["python_team_id_calls"]["by_population"].get("owner_direct", 0)
    per_user = m["teamids_upstream"]["per_team_only_viewer_per_minute"]
    miss_p95 = m["teamids_upstream"]["miss_p95_seconds"]
    error_ratio = m["teamids_cache"]["error_ratio"]
    return [
        {"id": "PERF-02 p95 under 50 ms", "ok": p95 is not None and p95 < P95_BUDGET_S, "detail": f"p95 {p95 * 1000:.1f} ms" if p95 is not None else "no samples"},
        {"id": "PERF-02 no Python team calls for owner and direct viewers", "ok": direct_calls == 0, "detail": f"{direct_calls} calls"},
        {
            "id": "PERF-02 no 429",
            "ok": m["rate_limited"] == 0,
            "detail": f"{m['rate_limited']} polls refused (per-user collab:feed limit, 60/min/replica; known, PR-12.7)",
            "informational": True,
        },
        {
            "id": f"PERF-04 upstream team-id lookups per team-only viewer per minute <= {UPSTREAM_PER_USER_MIN_CEILING:g}",
            "ok": per_user is not None and per_user <= UPSTREAM_PER_USER_MIN_CEILING,
            "detail": f"{per_user:.2f} per team-only viewer ({m['teamids_upstream']['per_active_user_per_minute']:.2f} per active user)" if per_user is not None else "no lookups",
        },
        {
            "id": f"PERF-04 team-id miss p95 < {MISS_P95_BUDGET_S * 1000:.0f} ms",
            "ok": miss_p95 is not None and miss_p95 < MISS_P95_BUDGET_S,
            "detail": f"p95 <= {miss_p95 * 1000:.0f} ms (histogram bucket bound)" if miss_p95 is not None else "no upstream lookups",
        },
        {
            "id": f"PERF-04 team-id cache error ratio <= {ERROR_RATIO_CEILING:.0%}",
            "ok": error_ratio is None or error_ratio <= ERROR_RATIO_CEILING,
            "detail": f"{error_ratio:.3f}" if error_ratio is not None else "no lookups",
        },
        {
            "id": "PERF-04 single-flight: one user never has two Python calls in flight within a second",
            "ok": m["python_team_id_calls"]["max_calls_by_one_user_within_1s"] <= 1,
            "detail": f"max {m['python_team_id_calls']['max_calls_by_one_user_within_1s']} per user per second",
        },
        {"id": "no failed polls", "ok": m["errors"] == 0, "detail": f"{m['errors']} failed"},
    ]


def render_summary(result: dict[str, Any]) -> str:
    m, p = result["metrics"], result["profile"]
    ms = lambda v: "n/a" if v is None else f"{v * 1000:.1f} ms"  # noqa: E731
    lines = [
        "### Collaborative-chats feed poll",
        "",
        f"{p['viewers']} viewers, {p['windows']} windows ({p['active_windows']} active), {p['duration_seconds']} s, {result['corpus']['sessions']:,} sessions seeded.",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Requests | {m['requests']} ({m['requests_per_second']:.0f}/s) |",
        f"| p50 / p95 / p99 | {ms(m['latency_seconds']['p50'])} / {ms(m['latency_seconds']['p95'])} / {ms(m['latency_seconds']['p99'])} |",
        f"| 304 share | {m['not_modified_ratio']:.1%} |" if m["not_modified_ratio"] is not None else "| 304 share | n/a |",
        f"| 429 | {m['rate_limited']} |",
        f"| Failed | {m['errors']} |",
        f"| Python team-id calls | {m['python_team_id_calls']['total']} (owner/direct: {m['python_team_id_calls']['by_population'].get('owner_direct', 0)}) |",
        f"| Team-id cache | {m['teamids_cache']} |",
        f"| Upstream team-id lookups | {m['teamids_upstream']} |",
        f"| collab_feed_poll_total | {m['feed_poll_total']} |",
        "",
    ]
    lines += [f"- {'PASS' if c['ok'] else 'INFO' if c.get('informational') else 'FAIL'} {c['id']}: {c['detail']}" for c in result["checks"]]
    return "\n".join(lines) + "\n"


# ---- entry ----------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--viewers", type=int, default=200)
    parser.add_argument("--duration", type=float, default=120.0, help="measured seconds")
    parser.add_argument("--warmup", type=float, default=15.0, help="seconds of polling before measuring starts")
    parser.add_argument("--sessions", type=int, default=200_000, help="background chatSessions (the viewers' chats are extra)")
    parser.add_argument("--teams", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--active-fraction", type=float, default=0.5, help="share of a viewer's windows that poll at the 4 s cadence (at least one)")
    parser.add_argument("--change-interval", type=float, default=120.0, help="mean seconds between new turns, per chat")
    parser.add_argument("--label", default="ci-collab-neo4j-4cpu")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--fail-on-violation", action="store_true")
    args = parser.parse_args()
    plan = SeedPlan.from_env(sessions=args.sessions, teams=args.teams, seed=args.seed)
    rng = random.Random(plan.seed)

    from helper.collab_stack.node_api import free_port
    from helper.collab_stack.stack import CollabStack, install_signal_cleanup

    probe_port = free_port()
    stack = CollabStack(node_env={"NODE_OPTIONS": f"--require {PROBE}", "COLLAB_METRICS_PROBE_PORT": str(probe_port)})
    install_signal_cleanup(stack)
    probe_url = f"http://127.0.0.1:{probe_port}/"
    try:
        stack.start()
        started = time.monotonic()
        report = collab_seed.seed(stack.db, stack.fake, stack.roster.owner.org_id, plan, log=lambda *_: None)  # type: ignore[union-attr]
        viewers = seed_population(stack, args.viewers, rng, args.active_fraction)
        seed_seconds = time.monotonic() - started
        stack.flags.set(True)  # type: ignore[union-attr]
        windows = [w for v in viewers for w in v.windows]
        print(f"seeded {report.sessions:,} background sessions, {len(viewers)} viewers, {len(windows)} windows in {seed_seconds:.0f}s", flush=True)

        stop_writer, writes = threading.Event(), [0]
        thread = threading.Thread(target=writer, args=(stack.db, windows, args.change_interval, stop_writer, random.Random(plan.seed + 1), writes), daemon=True)
        thread.start()
        rec = Recorder()
        mark_holder: dict[str, Any] = {}

        async def drive() -> float:
            async def snapshot() -> None:
                await asyncio.sleep(args.warmup)
                mark_holder["before"] = scrape(probe_url)
                mark_holder["buckets_before"] = scrape_upstream_buckets(probe_url)
                mark_holder["mark"] = stack.fake.mark()  # type: ignore[union-attr]

            snap = asyncio.create_task(snapshot())
            elapsed = await run_polling(stack.node.base_url, viewers, args.duration, args.warmup, rng, rec)  # type: ignore[union-attr]
            await snap
            return elapsed

        elapsed = asyncio.run(drive())
        after = scrape(probe_url)
        buckets_after = scrape_upstream_buckets(probe_url)
        stop_writer.set()
        thread.join(5)
        calls = stack.fake.since(mark_holder["mark"], "team_ids")  # type: ignore[union-attr]
        result = build_result(args, plan, viewers, rec, elapsed, mark_holder["before"], after, calls, writes[0], seed_seconds, (mark_holder["buckets_before"], buckets_after))
    finally:
        stack.stop()

    output = args.output or RESULTS_DIR / f"collab-poll-{args.viewers}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    summary = render_summary(result)
    print(summary)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(summary)
    print(f"written {output}")
    failed = [c for c in result["checks"] if not c["ok"] and not c.get("informational")]
    return 1 if failed and args.fail_on_violation else 0


if __name__ == "__main__":
    sys.exit(main())

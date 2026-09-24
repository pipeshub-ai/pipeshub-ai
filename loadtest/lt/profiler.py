"""py-spy profiling of the services under load.

Profiling perturbs what it measures, so it never runs during the measured
window. The runner profiles a dedicated extra iteration instead, and the
throughput/latency numbers in the report always come from unprofiled runs.

Beyond the flame graph, the ordered sample stream gives something a flame graph
cannot: the longest stretch the interpreter spent in one frame without moving.
On a single-loop service that stretch *is* the stall, and it names the function
responsible — which is what an event-loop-lag counter would only hint at.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import flamegraph
from .target import Target


@dataclass
class ProfileResult:
    service: str
    pid: int
    #: SVG rendered from `speedscope`, so both describe the same capture
    flamegraph: Path | None
    speedscope: Path | None
    blocking: list[dict] | None = None
    #: py-spy's own SVG, from a SEPARATE capture immediately after the first —
    #: so it covers an adjacent window, not the same one
    pyspy_flamegraph: Path | None = None
    error: str | None = None
    #: {module: percent of main-thread samples} — where the CPU actually went
    modules: dict[str, float] | None = None
    #: application frames by cumulative time, the actionable view
    hot_frames: list[dict] | None = None
    #: seconds of main-thread samples in the capture window
    busy_s: float = 0.0


def ensure_py_spy(target: Target) -> str | None:
    """Return the py-spy binary path inside the container, or None on failure."""
    probe = target.exec(["sh", "-lc", "command -v py-spy || true"], check=False).strip()
    if probe:
        return probe.splitlines()[0].strip()
    target.exec(
        ["sh", "-lc", "python3 -m pip install --quiet --disable-pip-version-check py-spy"],
        timeout=300,
        check=False,
    )
    probe = target.exec(["sh", "-lc", "command -v py-spy || true"], check=False).strip()
    return probe.splitlines()[0].strip() if probe else None


def record(
    target: Target,
    service: str,
    pid: int,
    duration: int,
    rate: int,
    out_dir: Path,
    binary: str,
    *,
    native_flamegraph: bool = False,
) -> ProfileResult:
    """Attach py-spy to one PID for `duration` seconds.

    Filenames include the pid so multi-worker captures do not overwrite each
    other. When `native_flamegraph` is set, the py-spy SVG is recorded *first*
    so it overlaps the sync; speedscope (for our SVG + blocking table) follows.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{service}-{pid}"
    pyspy_svg = None
    if native_flamegraph:
        pyspy_svg = _record_native_flamegraph(
            target, service, pid, duration, rate, out_dir, binary
        )

    remote_json = f"/tmp/lt-{stem}.speedscope.json"
    json_path = out_dir / f"{stem}.speedscope.json"

    cmd = [
        binary,
        "record",
        "--pid",
        str(pid),
        "--duration",
        str(duration),
        "--rate",
        str(rate),
        # The indexing service does its heavy work on a dedicated worker thread;
        # the default main-thread-only view would miss it entirely.
        "--threads",
        "--format",
        "speedscope",
        "--output",
        remote_json,
    ]
    # --privileged on exec grants SYS_PTRACE without restarting the stack, which
    # matters because the measured and profiled iterations must run against the
    # same processes.
    try:
        target.exec(cmd, privileged=True, timeout=duration + 180)
        target.copy_out(remote_json, json_path)
    except Exception as exc:
        return ProfileResult(
            service, pid, None, None, pyspy_flamegraph=pyspy_svg, error=str(exc)
        )

    try:
        # errors="replace": py-spy occasionally emits a frame name with bytes
        # that are not valid UTF-8 (mangled native symbols). Those names are
        # display-only, so a stray byte must not cost us the capture.
        doc = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return ProfileResult(
            service,
            pid,
            None,
            json_path,
            pyspy_flamegraph=pyspy_svg,
            error=f"unreadable capture: {exc}",
        )

    svg_path = out_dir / f"{stem}.flamegraph.svg"
    svg_path.write_text(
        flamegraph.render(doc, title=f"{service} (pid {pid}, {duration}s @ {rate}Hz)"),
        encoding="utf-8",
    )

    modules, busy_s = module_breakdown(doc)
    return ProfileResult(
        service,
        pid,
        svg_path,
        json_path,
        pyspy_flamegraph=pyspy_svg,
        blocking=analyse_blocking_doc(doc),
        modules=modules,
        hot_frames=hot_app_frames(doc),
        busy_s=busy_s,
    )


def _record_native_flamegraph(
    target: Target,
    service: str,
    pid: int,
    duration: int,
    rate: int,
    out_dir: Path,
    binary: str,
) -> Path | None:
    """py-spy's built-in flame graph. Best-effort: never fail the profile for it."""
    remote = f"/tmp/lt-{service}-{pid}.pyspy.svg"
    local = out_dir / f"{service}-{pid}.pyspy-flamegraph.svg"
    try:
        target.exec(
            [
                binary, "record", "--pid", str(pid),
                "--duration", str(duration), "--rate", str(rate),
                "--threads", "--format", "flamegraph", "--output", remote,
            ],
            privileged=True,
            timeout=duration + 180,
        )
        target.copy_out(remote, local)
    except Exception:
        return None
    return local if local.exists() else None


def analyse_blocking(speedscope_path: Path, top: int = 10) -> list[dict]:
    try:
        doc = json.loads(speedscope_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    return analyse_blocking_doc(doc, top)


#: Frames where a thread is parked waiting for work rather than holding it up.
#: Without this, the longest "uninterrupted frame" on any healthy service is
#: always an idle worker sitting in queue.get() or the event loop in epoll,
#: which is the opposite of the stall the metric is looking for.
IDLE_FRAMES = (
    ("concurrent/futures/thread.py", "_worker"),
    ("queue.py", "get"),
    ("selectors.py", "select"),
    ("asyncio/base_events.py", "_run_once"),
    ("threading.py", "wait"),
    ("threading.py", "_wait_for_tstate_lock"),
    ("socket.py", "accept"),
)


def _is_idle(frame: dict) -> bool:
    file = frame.get("file") or ""
    name = frame.get("name") or ""
    return any(f in file and name == fn for f, fn in IDLE_FRAMES)


def analyse_blocking_doc(doc: dict, top: int = 10) -> list[dict]:
    """Longest contiguous run of samples with an unchanged leaf frame.

    py-spy's speedscope export keeps samples in wall-clock order, so a run of N
    consecutive identical leaves at sample rate R means the interpreter sat in
    that frame for about N/R seconds without yielding. Sorted descending, the
    head of this list is the stall inventory for the run.

    Runs whose leaf is a known parking spot (see IDLE_FRAMES) are marked
    `idle` and sorted last: they are threads waiting for work, not blocking it.
    """
    frames = doc.get("shared", {}).get("frames", [])

    def describe(index: int) -> tuple[str, bool]:
        try:
            frame = frames[index]
        except (IndexError, TypeError):
            return "<unknown>", False
        name = frame.get("name", "<unknown>")
        file = frame.get("file")
        line = frame.get("line")
        label = f"{name} ({file}:{line})" if file else name
        return label, _is_idle(frame)

    runs: list[dict] = []
    for profile in doc.get("profiles", []):
        if profile.get("type") != "sampled":
            continue
        samples = profile.get("samples") or []
        weights = profile.get("weights") or []
        thread = profile.get("name", "?")
        scale = flamegraph.weight_scale(profile)

        current: str | None = None
        current_idle = False
        count = 0
        weight = 0.0

        def flush() -> None:
            if current is not None and count > 1:
                runs.append(
                    {
                        "thread": thread,
                        "frame": current,
                        "samples": count,
                        "duration_ms": round(weight, 2),
                        "idle": current_idle,
                    }
                )

        for i, stack in enumerate(samples):
            leaf, idle = describe(stack[-1]) if stack else ("<empty>", False)
            w = (weights[i] if i < len(weights) else 0.0) * scale
            if leaf == current:
                count += 1
                weight += w
            else:
                flush()
                current, current_idle, count, weight = leaf, idle, 1, w
        flush()

    # Idle parking spots last: a worker waiting on its queue is not a stall,
    # and it would otherwise always top the list on a healthy service.
    runs.sort(key=lambda r: (r["idle"], -r["duration_ms"]))
    return runs[:top]


def _module_of(file: str) -> str:
    """Coarse bucket for a frame's file: the installed package, or `app/...`.

    Third-party frames collapse to the distribution name; application frames
    keep their first two path segments, which is enough to tell
    `app/connectors` from `app/services` without exploding the table.
    """
    if not file:
        return "?"
    if "site-packages/" in file:
        return file.split("site-packages/", 1)[1].split("/", 1)[0]
    if "/app/" in file:
        parts = file.split("/app/", 1)[1].split("/")
        return "app/" + "/".join(parts[:2]) if len(parts) > 1 else "app"
    if file.startswith("<"):
        return "builtin"
    return file.rsplit("/", 1)[-1]


def _main_thread_profiles(doc: dict) -> list[dict]:
    """Only the main thread: on an asyncio service that is the event loop, and
    it is the thread whose saturation determines API responsiveness."""
    return [
        p
        for p in doc.get("profiles", [])
        if p.get("type") == "sampled" and "MainThread" in (p.get("name") or "")
    ]


def module_breakdown(doc: dict, top: int = 12) -> tuple[dict[str, float], float]:
    """Self time by module: percent of samples whose *leaf* is in that module.

    Self time, not cumulative. Cumulative counting makes every structural root
    (runpy, uvicorn, the app entrypoint) show 100% and buries the real cost;
    self time sums to 100% and answers "what was the interpreter actually
    executing", which is the question an optimiser needs.

    Use `cumulative_share` when the question is instead "how much time involved
    this library at all" — that is the shape of the jsonschema 60% figure.
    """
    frames = doc.get("shared", {}).get("frames", [])
    totals: dict[str, float] = {}
    total = 0.0
    for profile in _main_thread_profiles(doc):
        weights = profile.get("weights") or []
        scale = flamegraph.weight_scale(profile)
        for i, stack in enumerate(profile.get("samples") or []):
            weight = (weights[i] if i < len(weights) else 0.0) * scale
            total += weight
            if not stack:
                continue
            try:
                module = _module_of(frames[stack[-1]].get("file") or "")
            except (IndexError, TypeError):
                continue
            totals[module] = totals.get(module, 0.0) + weight
    if total <= 0:
        return {}, 0.0
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return {m: round(w / total * 100, 1) for m, w in ranked}, round(total / 1000, 1)


def cumulative_share(doc: dict, modules: tuple[str, ...]) -> dict[str, float]:
    """Percent of samples with `module` anywhere in the stack, de-duplicated.

    Answers "how much of the run involved this library", which is what tells
    you whether removing it entirely is worth anything.
    """
    frames = doc.get("shared", {}).get("frames", [])
    hits = dict.fromkeys(modules, 0.0)
    total = 0.0
    for profile in _main_thread_profiles(doc):
        weights = profile.get("weights") or []
        scale = flamegraph.weight_scale(profile)
        for i, stack in enumerate(profile.get("samples") or []):
            weight = (weights[i] if i < len(weights) else 0.0) * scale
            total += weight
            seen = set()
            for frame_index in stack:
                try:
                    module = _module_of(frames[frame_index].get("file") or "")
                except (IndexError, TypeError):
                    continue
                if module in hits:
                    seen.add(module)
            for module in seen:
                hits[module] += weight
    if total <= 0:
        return dict.fromkeys(modules, 0.0)
    return {m: round(w / total * 100, 1) for m, w in hits.items()}


def hot_app_frames(doc: dict, top: int = 15) -> list[dict]:
    """Application frames ranked by cumulative main-thread time.

    Third-party frames tell you *what* is expensive; these tell you *which line
    of ours asked for it*, which is the only actionable form.
    """
    frames = doc.get("shared", {}).get("frames", [])
    totals: dict[str, float] = {}
    total = 0.0
    for profile in _main_thread_profiles(doc):
        weights = profile.get("weights") or []
        scale = flamegraph.weight_scale(profile)
        for i, stack in enumerate(profile.get("samples") or []):
            weight = (weights[i] if i < len(weights) else 0.0) * scale
            total += weight
            seen = set()
            for frame_index in stack:
                try:
                    frame = frames[frame_index]
                except (IndexError, TypeError):
                    continue
                file = frame.get("file") or ""
                if "/app/" not in file or "site-packages" in file:
                    continue
                label = f"{frame.get('name')} ({file.split('/app/', 1)[1]}:{frame.get('line')})"
                if label not in seen:
                    seen.add(label)
                    totals[label] = totals.get(label, 0.0) + weight
    if total <= 0:
        return []
    # Drop structural roots: a frame present in essentially every sample is the
    # entrypoint or the request loop, never the thing to optimise.
    ranked = [
        (label, weight)
        for label, weight in totals.items()
        if weight / total <= 0.95
    ]
    ranked.sort(key=lambda kv: kv[1], reverse=True)
    return [
        {"frame": f, "percent": round(w / total * 100, 1), "ms": round(w)}
        for f, w in ranked[:top]
    ]


def kill_leftovers(target: Target) -> None:
    """py-spy detaches cleanly on exit; this is belt-and-braces after a timeout."""
    subprocess.run(
        [target.docker, "exec", target.container, "sh", "-lc", "pkill -f py-spy || true"],
        capture_output=True,
        text=True,
        check=False,
    )

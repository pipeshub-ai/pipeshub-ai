"""In-container resource sampler.

Piped into `docker exec -i <container> python3 -` and emits one JSON object per
line on stdout. Deliberately stdlib-only and /proc-based: the app image's
interpreter is not guaranteed to have psutil on the path the services run under,
and a sampler that fails to start silently costs a whole test run.

Usage (argv): interval_seconds  duration_seconds  [stop_file]

The stop file is how the host ends a sample window. `docker exec` does not kill
the process it started when the client goes away, so terminating the local CLI
would otherwise leave an agent sampling inside the container for the whole
duration ceiling — and its output would land in the next iteration's window.
"""

import json
import os
import sys
import time

# Guarded so the module imports on non-Linux for its parsing tests; the values
# only ever matter inside the container, where sysconf is present.
TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

# cmdline substring -> service label. Order matters: first match wins, so the
# more specific patterns must come first.
PATTERNS = [
    # Before app.connectors_main so the executor is not mistaken for the API
    # process; its CPU and memory belong in their own bucket.
    ("app.connector_sync_main", "connector-sync"),
    # The connector API is launched via connectors_serve (a launcher that
    # exists so uvicorn multi-worker children do not re-import the app
    # module). connectors_main stays listed so older images still resolve.
    ("app.connectors_serve", "connector"),
    ("app.connectors_main", "connector"),
    ("app.indexing_main", "indexing"),
    ("app.query_main", "query"),
    ("app.docling_main", "docling"),
    ("app.embedding_main", "embedding"),
    ("apps/src/index", "nodejs"),
    ("next/dist", "frontend"),
]

# Uvicorn --workers children show up as multiprocessing.spawn with no app
# module in argv. Attribute them to their parent service via /proc/<pid>/status.

# watchmedo is the hot-reload supervisor in the dev image; it carries the
# service module name in its own argv, so it would otherwise match as the
# service itself and double-count.
EXCLUDE = ("watchmedo",)


def read_cmdline(pid):
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def classify(cmdline):
    if not cmdline:
        return None
    for token in EXCLUDE:
        if token in cmdline:
            return None
    for needle, label in PATTERNS:
        if needle in cmdline:
            return label
    return None


def read_ppid(pid):
    try:
        with open("/proc/%s/status" % pid) as fh:
            for line in fh:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def discover():
    """Map pid -> service label for every process we care about.

    Also tags uvicorn worker children (multiprocessing.spawn) with their
    parent's service label, so multi-worker CPU/RSS is not invisible.
    """
    found = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        label = classify(read_cmdline(entry))
        if label:
            found[int(entry)] = label

    # Second pass: attribute spawn/fork workers to a known parent service.
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in found:
            continue
        cmdline = read_cmdline(entry)
        if "multiprocessing.spawn" not in cmdline and "multiprocessing.fork" not in cmdline:
            continue
        ppid = read_ppid(entry)
        # Walk a couple of levels in case of an intermediate tracker process.
        for _ in range(3):
            if ppid is None:
                break
            if ppid in found:
                found[pid] = found[ppid]
                break
            ppid = read_ppid(str(ppid))
    return found


def parse_stat(raw):
    """Parse /proc/<pid>/stat -> (cpu_ticks, rss_bytes, num_threads).

    comm (field 2) is parenthesised and may itself contain spaces and
    parentheses, so the split must start after the *last* ')'. After that,
    index 0 is field 3, which puts utime (14) at 11, stime (15) at 12,
    num_threads (20) at 17 and rss-in-pages (24) at 21.
    """
    fields = raw[raw.rfind(")") + 2 :].split()
    return int(fields[11]) + int(fields[12]), int(fields[21]) * PAGE, int(fields[17])


def read_stat(pid):
    """Return (cpu_ticks, rss_bytes, num_threads) or None if the pid is gone."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return parse_stat(fh.read())
    except (OSError, IndexError, ValueError):
        return None


def count_fds(pid):
    try:
        return len(os.listdir("/proc/%d/fd" % pid))
    except OSError:
        return -1


def main():
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 3600.0
    stop_file = sys.argv[3] if len(sys.argv) > 3 else None

    deadline = time.time() + duration
    prev = {}
    prev_wall = time.time()
    # Re-discover periodically: the dev image restarts services on file change,
    # which changes their pids mid-run.
    pids = discover()
    last_discovery = time.time()

    print(json.dumps({"type": "discovery", "pids": {str(k): v for k, v in pids.items()}}), flush=True)

    while time.time() < deadline:
        time.sleep(interval)
        if stop_file and os.path.exists(stop_file):
            return
        now = time.time()
        elapsed = now - prev_wall
        prev_wall = now

        if now - last_discovery > 15:
            pids = discover()
            last_discovery = now

        for pid, label in list(pids.items()):
            stat = read_stat(pid)
            if stat is None:
                pids.pop(pid, None)
                continue
            ticks, rss, threads = stat
            cpu = None
            if pid in prev and elapsed > 0:
                cpu = (ticks - prev[pid]) / TICKS / elapsed * 100.0
            prev[pid] = ticks
            if cpu is None:
                continue
            print(
                json.dumps(
                    {
                        "type": "sample",
                        "t": round(now, 3),
                        "pid": pid,
                        "service": label,
                        "cpu_percent": round(cpu, 2),
                        "rss_bytes": rss,
                        "threads": threads,
                        "fds": count_fds(pid),
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()

"""The accessible-records cache across two processes under REDIS_MODE=postgres: a revoke made in one is seen by the other at once."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def _env(runtime, **extra: str) -> dict[str, str]:
    root = str(Path(__file__).resolve().parents[3])
    return {
        **os.environ,
        "PYTHONPATH": root,
        "KV_STORE_TYPE": "postgres",
        "MESSAGE_BROKER": "postgres",
        "PG_CORE_URL": runtime.cfg.dsn,
        "PG_POOLER": "none",
        "PG_RUNTIME_SERVICE": "connectors",
        "REDIS_MODE": "",
        **extra,
    }


def _lines(proc: subprocess.Popen, until: str, limit: float = 30) -> list[dict]:
    out, end = [], time.time() + limit
    while time.time() < end:
        line = proc.stdout.readline()
        if not line:
            break
        msg = json.loads(line)
        out.append(msg)
        if until in msg:
            return out
    raise AssertionError(f"never saw {until!r}: {out}")


def test_a_revoke_reaches_the_other_process_in_under_a_second(
    runtime, tmp_path
) -> None:
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"v1": "r1"}))
    env = _env(runtime)
    query = subprocess.Popen(
        [sys.executable, str(HERE / "acl_cache_child.py"), "query", str(graph)],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        ready = _lines(query, "READY")[-1]
        assert (
            ready["first"] == {"v1": "r1"}
            and ready["cached"] is True
            and ready["enabled"] is True
        )
        graph.write_text("{}")  # the permission sync changed the graph...
        conn = subprocess.run(
            [
                sys.executable,
                str(HERE / "acl_cache_child.py"),
                "connectors",
                str(graph),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )  # ...and the connectors process invalidates
        done = json.loads(conn.stdout.strip().splitlines()[-1])["done"]
        seen = _lines(query, "seen_at")[-1]["seen_at"]
        assert seen - done < 1.0, seen - done
    finally:
        query.kill()
        query.wait()


def test_without_a_direct_address_to_listen_on_no_cache_is_kept(
    runtime, tmp_path
) -> None:
    """Fail closed: a pooler and no PG_LISTEN_URL means no invalidation could arrive, so nothing is cached."""
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"v1": "r1"}))
    env = _env(runtime, PG_POOLER="pgbouncer_txn")
    env.pop("PG_LISTEN_URL", None)
    p = subprocess.run(
        [sys.executable, str(HERE / "acl_cache_child.py"), "connectors", str(graph)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert json.loads(p.stdout.splitlines()[0])["cache"] == "NoopAccessibleRecordsCache"

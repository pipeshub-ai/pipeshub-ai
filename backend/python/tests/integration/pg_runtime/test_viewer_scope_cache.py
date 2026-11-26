"""The viewer-scope cache across processes under REDIS_MODE=postgres: a permission write in one moves the ACL version in another.

Each process keeps its own cache, so without the NOTIFY a query process would keep serving the scopes it computed before a share or
a revoke until the entries expired."""

import json
import subprocess
import sys
import uuid

from .test_acl_cache import HERE, _env, _lines

CHILD = str(HERE / "viewer_scope_child.py")


def test_a_bump_reaches_the_other_process_in_under_a_second(runtime) -> None:
    org = f"org-{uuid.uuid4().hex[:8]}"
    env = _env(runtime)
    query = subprocess.Popen([sys.executable, CHILD, "query", org], env=env, stdout=subprocess.PIPE, text=True)
    try:
        started = _lines(query, "client")[0]
        ready = _lines(query, "READY")[-1]
        assert ready["first"] is not None and ready["again"] == ready["first"]      # cached under one version until a bump
        conn = subprocess.run([sys.executable, CHILD, "connectors", org], env=env, capture_output=True, text=True, timeout=60,
                              check=True)
        last = json.loads(conn.stdout.strip().splitlines()[-1])
        assert last["pending"] == []        # the bump was published
        seen = _lines(query, "seen_at")[-1]["seen_at"]
        assert seen - last["done"] < 1.0, seen - last["done"]
        assert started == {"client": True, "bus": True}
    finally:
        query.kill()
        query.wait()


def test_without_a_direct_address_to_listen_on_no_cache_is_kept(runtime) -> None:
    """Fail closed: a pooler and no PG_LISTEN_URL means no bump could reach the other processes, so nothing is cached."""
    env = _env(runtime, PG_POOLER="pgbouncer_txn")
    env.pop("PG_LISTEN_URL", None)
    p = subprocess.run([sys.executable, CHILD, "connectors", "org"], env=env, capture_output=True, text=True, timeout=60)
    assert json.loads(p.stdout.splitlines()[0]) == {"client": False, "bus": False}

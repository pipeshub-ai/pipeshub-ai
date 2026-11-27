"""Encrypted key-value entries across the two languages on real stores: what the Python services write the Node API reads, and
the reverse, including a compare-and-set by Node on a value Python wrote; and both stored forms of an encrypted value (the
JSON-encoded form earlier Python builds wrote, and the plain text form both languages write now) read by both.

Runs for every store that is configured: Redis (REDIS_IT_HOST, REDIS_IT_PORT), etcd (ETCD_IT_URL), as the other tests here, and
PostgreSQL (PG_CORE_URL and PG_NODE_URL of a migrated database, PG_POOLER=none). Node runs tests/helpers/kv-cross-language-driver.ts from
backend/nodejs/apps with its node_modules installed."""
import json
import logging
import os
import secrets
import subprocess
from pathlib import Path

import pytest

from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.utils.encryption.encryption_service import EncryptionService

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

NODE_APPS = Path(__file__).resolve().parents[4] / "nodejs" / "apps"
SECRET = "kv-cross-language-test-secret-0123456789"


def stores() -> list[str]:
    out = [s for s, var in (("redis", "REDIS_IT_HOST"), ("etcd", "ETCD_IT_URL")) if os.environ.get(var)]
    if os.environ.get("PG_CORE_URL") and os.environ.get("PG_NODE_URL"):
        out.append("postgres")
    return out or ["none"]


@pytest.fixture(params=stores())
def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> dict:
    if request.param == "none":
        pytest.fail("no store configured: set REDIS_IT_HOST, ETCD_IT_URL and/or the PG_* URLs")
    e = {"SECRET_KEY": SECRET, "KV_STORE_TYPE": request.param, "REDIS_KV_PREFIX": f"x{secrets.token_hex(3)}:"}
    if request.param == "redis":
        e |= {"REDIS_HOST": os.environ["REDIS_IT_HOST"], "REDIS_PORT": os.environ.get("REDIS_IT_PORT", "6379"), "REDIS_DB": "0"}
    if request.param == "etcd":
        scheme_host, port = os.environ["ETCD_IT_URL"].rsplit(":", 1)
        e |= {"ETCD_URL": os.environ["ETCD_IT_URL"], "ETCD_HOST": scheme_host, "ETCD_PORT": port}
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    return e


def node(env: dict, ops: list[dict]) -> list[dict]:
    r = subprocess.run(["npx", "ts-node", "tests/helpers/kv-cross-language-driver.ts", json.dumps(ops)], cwd=NODE_APPS,
                       env={**os.environ, **env, "TS_NODE_PROJECT": "tsconfig.test.json", "TS_NODE_TRANSPILE_ONLY": "1"},
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("KV_RESULT ")]
    assert len(lines) == 1, r.stdout[-2000:]
    return json.loads(lines[0].removeprefix("KV_RESULT "))


async def test_each_language_reads_and_compare_and_sets_what_the_other_wrote(env: dict) -> None:
    py = EncryptedKeyValueStore(logging.getLogger("kv-cross"))
    base = f"/services/connectors/kvcross{secrets.token_hex(4)}"   # a prefix the connectors service writes and Node reads
    await py.create_key(f"{base}/py", {"clientSecret": "from-python"})
    r = node(env, [{"op": "get", "key": f"{base}/py"}, {"op": "set", "key": f"{base}/node", "text": json.dumps({"clientSecret": "from-node"})}])
    assert r[0].get("text") and json.loads(r[0]["text"]) == {"clientSecret": "from-python"}, r       # Node reads Python's
    assert await py.get_key(f"{base}/node") == {"clientSecret": "from-node"}                          # Python reads Node's
    r = node(env, [{"op": "cas", "key": f"{base}/py", "expected": r[0]["raw"], "text": json.dumps({"clientSecret": "node-cas"})},
                   {"op": "cas", "key": f"{base}/py", "expected": r[0]["raw"], "text": json.dumps({"clientSecret": "stale"})}])
    assert r == [{"ok": True}, {"ok": False}], r                                                      # a stale expected value loses
    assert await py.get_key(f"{base}/py") == {"clientSecret": "node-cas"}


async def test_both_stored_forms_are_read_by_both_languages_and_by_the_earlier_read_paths(env: dict) -> None:
    py = EncryptedKeyValueStore(logging.getLogger("kv-cross"))
    base = f"/services/connectors/kvcross{secrets.token_hex(4)}"   # a prefix the connectors service writes and Node reads
    # the JSON-encoded form earlier Python builds wrote: the ciphertext handed to the store as a plain string
    await py.store.create_key(f"{base}/old", py.encryption_service.encrypt(json.dumps({"v": "old-python"})))
    assert await py.get_key(f"{base}/old") == {"v": "old-python"}
    r = node(env, [{"op": "get", "key": f"{base}/old"}])
    assert json.loads(r[0]["text"]) == {"v": "old-python"}, r
    r = node(env, [{"op": "cas", "key": f"{base}/old", "expected": r[0]["raw"], "text": json.dumps({"v": "rewritten-by-node"})}])
    assert r == [{"ok": True}], r
    assert await py.get_key(f"{base}/old") == {"v": "rewritten-by-node"}
    await py.create_key(f"{base}/old", {"v": "rewritten-by-python"})
    # what the services write now, read as earlier builds of both languages read: Node below the service, Python's store reader
    r = node(env, [{"op": "rawget", "key": f"{base}/old"}])
    assert json.loads(r[0]["text"]) == {"v": "rewritten-by-python"}, r
    kept = await py.store.get_key(f"{base}/old")
    assert isinstance(kept, str) and json.loads(py.encryption_service.decrypt(kept)) == {"v": "rewritten-by-python"}

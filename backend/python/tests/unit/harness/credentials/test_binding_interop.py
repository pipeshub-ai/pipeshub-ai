"""CPD-8 XL-B1, XL-B2: Node's binder writes bindings, Python's broker leases them, through the production stores.

Node's ``VaultCredentialBinder`` over the Mongo binding store, its Redis KV provider and the platform cipher
(``tests/modules/control-plane/support/binding-vault-cli.ts``) binds, refreshes and revokes; Python reads through
``EncryptedKeyValueStore`` -> ``ConfigurationService`` -> the vault -> ``BindingSecretSource`` ->
``HandleCredentialService`` -> ``BindingCredentialBroker``. Needs Node (``npx``), the installed Node workspace,
``REDIS_IT_URL`` and ``MONGO_IT_URI``; skipped otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.control_plane.contracts.generated.effective_set import PinnedBinding
from app.harness.credentials.bindings import BindingCredentialBroker, BindingDocuments
from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.vault import VaultSecretStore
from app.utils.encryption.encryption_service import EncryptionService
from harness_core.clock import SystemClock
from harness_core.credentials import CredentialPurpose, CredentialRevoked

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NODE_APP = Path(__file__).resolve().parents[5] / "nodejs" / "apps"
CLI = "tests/modules/control-plane/support/binding-vault-cli.ts"
REDIS_URL = os.environ.get("REDIS_IT_URL")
MONGO_URI = os.environ.get("MONGO_IT_URI")
SECRET = "cpd8-interop-secret-" + uuid.uuid4().hex
ORG, INSTALLATION = "org-xlb", "inst-xlb"
FIRST = "ghp_" + "InteropFirstBinding0123456789abcdefAB"
REFRESHED = "ghp_" + "InteropRefreshedBinding0123456789abcd"
PURPOSE = CredentialPurpose(ORG, "toolset:inst-xlb", "run-1", "attempt-1")
LOGGER = logging.getLogger("cpd8-interop")

pytestmark = pytest.mark.skipif(
    REDIS_URL is None or MONGO_URI is None or shutil.which("npx") is None or not (NODE_APP / "node_modules").is_dir(),
    reason="needs REDIS_IT_URL, MONGO_IT_URI, npx and the installed Node workspace",
)


def _redis() -> tuple[str, int]:
    parsed = urlparse(REDIS_URL or "")
    return parsed.hostname or "127.0.0.1", parsed.port or 6379


def _node(prefix: str, database: str, *args: str) -> dict[str, Any]:
    host, port = _redis()
    env = {
        **os.environ,
        "REDIS_HOST": host,
        "REDIS_PORT": str(port),
        "REDIS_KV_PREFIX": prefix,
        "SECRET_KEY": SECRET,
        "BINDINGS_DB": database,
        "TS_NODE_PROJECT": "tsconfig.test.json",
    }
    done = subprocess.run(
        ["npx", "ts-node", "--transpile-only", CLI, *args],
        cwd=NODE_APP,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, f"node binding cli failed: {done.stderr[-500:]}"
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[str, str, ConfigurationService]]:
    host, port = _redis()
    prefix = f"pxh-cpd8-{uuid.uuid4().hex[:12]}:"
    database = f"pxh_cpd8_xl_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(EncryptionService, "_instance", None)
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    monkeypatch.setenv("REDIS_HOST", host)
    monkeypatch.setenv("REDIS_PORT", str(port))
    monkeypatch.setenv("REDIS_KV_PREFIX", prefix)
    for name in ("REDIS_PASSWORD", "REDIS_DB"):
        monkeypatch.delenv(name, raising=False)
    service = ConfigurationService(LOGGER, EncryptedKeyValueStore(LOGGER))
    try:
        yield prefix, database, service
    finally:
        for key in await service.store.get_all_keys():
            _ = await service.store.delete_key(key)
        await service.close()
        _ = _node(prefix, database, "drop", ORG, "-", "-")


async def test_xl_b1_b2_python_leases_what_node_bound_follows_a_refresh_and_stops_at_a_revoke(
    world: tuple[str, str, ConfigurationService],
) -> None:
    prefix, database, config = world
    bound = _node(prefix, database, "bind", ORG, INSTALLATION, "user:u1", FIRST)
    assert bound["status"] == "active" and FIRST not in json.dumps(bound)
    clock = SystemClock()
    broker = BindingCredentialBroker(
        pipeshub_credential_service(config, clock=clock), BindingDocuments(VaultSecretStore(config, clock))
    )
    pin = PinnedBinding.model_validate({"ref": bound["id"], "owner": "user:u1", "versionDigest": bound["digest"]})
    lease = await broker.lease(pin, PURPOSE, 300)
    assert (await broker.resolve(lease, PURPOSE)).material == {"apiKey": FIRST}

    refreshed = _node(prefix, database, "refresh", ORG, bound["id"], str(bound["revision"]), REFRESHED)
    assert refreshed["digest"] != bound["digest"]
    again = await broker.lease(pin, PURPOSE, 300)
    assert (await broker.resolve(again, PURPOSE)).material == {"apiKey": REFRESHED}

    _ = _node(prefix, database, "revoke", ORG, bound["id"], str(refreshed["revision"]))
    with pytest.raises(CredentialRevoked):
        _ = await broker.resolve(again, PURPOSE)
    with pytest.raises(CredentialRevoked):
        _ = await broker.lease(pin, PURPOSE, 300)

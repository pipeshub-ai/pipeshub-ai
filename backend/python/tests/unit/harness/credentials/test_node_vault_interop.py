"""P2.4b XL: Node writes model accounts, Python resolves them, through the production stores both sides.

Node's model-account vault over its real Redis provider and cipher
(``tests/modules/agent-harness/support/model-account-vault-cli.ts``) writes and
revokes; Python reads through ``EncryptedKeyValueStore`` -> ``ConfigurationService``
-> the vault -> the credential service -> ``ConfiguredModelAccess``, against the
same Redis under a key prefix unique to the test. Needs Node (``npx``), the
installed Node workspace and ``REDIS_IT_URL``; skipped otherwise.
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
from app.harness.access.configured import ConfiguredModelAccess
from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.vault import VaultSecretStore
from app.utils.encryption.encryption_service import EncryptionService
from harness_core.access.errors import AccountRevoked
from harness_core.access.types import PinnedRoute, user_account_ref
from harness_core.clock import SystemClock
from harness_core.credentials import CredentialRevoked, CredentialScope
from tests.unit.harness.access.accounts_support import Builds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NODE_APP = Path(__file__).resolve().parents[5] / "nodejs" / "apps"
CLI = "tests/modules/agent-harness/support/model-account-vault-cli.ts"
REDIS_URL = os.environ.get("REDIS_IT_URL")
SECRET = "p24b-interop-secret-" + uuid.uuid4().hex
ORG, USER = "org-xl", "user-xl"
KEY = "sk-proj-" + "InteropFirstKey0123456789abcdefWXYZ"
ROTATED = "sk-proj-" + "InteropSecondKey0123456789abcdeQRST"
LOGGER = logging.getLogger("p24b-interop")

pytestmark = pytest.mark.skipif(
    REDIS_URL is None or shutil.which("npx") is None or not (NODE_APP / "node_modules").is_dir(),
    reason="needs REDIS_IT_URL, npx and the installed Node workspace",
)


def _redis() -> tuple[str, int]:
    parsed = urlparse(REDIS_URL or "")
    return parsed.hostname or "127.0.0.1", parsed.port or 6379


def _node(prefix: str, *args: str) -> dict[str, Any]:
    host, port = _redis()
    env = {
        **os.environ,
        "REDIS_HOST": host,
        "REDIS_PORT": str(port),
        "REDIS_KV_PREFIX": prefix,
        "SECRET_KEY": SECRET,
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
    assert done.returncode == 0, f"node vault cli failed: {done.stderr[-500:]}"
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture
async def config(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[str, ConfigurationService]]:
    host, port = _redis()
    prefix = f"pxh-p24b-{uuid.uuid4().hex[:12]}:"
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
        yield prefix, service
    finally:
        for key in await service.store.get_all_keys():
            _ = await service.store.delete_key(key)
        await service.close()


def _access(service: ConfigurationService, builds: Builds) -> ConfiguredModelAccess:
    credentials = pipeshub_credential_service(service, clock=SystemClock())
    return ConfiguredModelAccess(service, transports=builds, credentials=credentials, clock=SystemClock())


ROUTE = PinnedRoute("openai", "gpt-4o", config_model="gpt-4o", account=user_account_ref(USER, "openAI"))


async def test_xl1_xl2_python_resolves_what_node_wrote_and_refuses_what_node_revoked(
    config: tuple[str, ConfigurationService],
) -> None:
    prefix, service = config
    written = _node(prefix, "put", ORG, USER, "openAI", KEY)
    assert written["version"] == 1 and written["last4"] == "WXYZ" and KEY not in json.dumps(written)

    builds = Builds()
    resolved = await _access(service, builds).resolve(ORG, ROUTE, run_id="run-1")
    assert resolved.access.pays == "user"
    assert [e["configuration"]["apiKey"] for _, e, _, _ in builds.entries] == [KEY]  # type: ignore[index]

    assert _node(prefix, "revoke", ORG, USER, "openAI") == {"revoked": True}
    with pytest.raises(AccountRevoked):
        _ = await _access(service, Builds()).resolve(ORG, ROUTE, run_id="run-2")
    with pytest.raises(CredentialRevoked):
        _ = await VaultSecretStore(service, SystemClock()).read(ORG, CredentialScope("vault", ROUTE.account.account_id))  # type: ignore[union-attr]


async def test_xl3_after_a_python_key_rotation_node_lists_and_replaces_and_python_resolves(
    config: tuple[str, ConfigurationService],
) -> None:
    prefix, service = config
    _ = _node(prefix, "put", ORG, USER, "openAI", KEY)
    assert await VaultSecretStore(service, SystemClock()).rotate_key() == 1

    listed = _node(prefix, "list", ORG, USER)["accounts"]
    assert [(a["provider"], a["version"], a["last4"]) for a in listed] == [("openAI", 1, "WXYZ")]
    replaced = _node(prefix, "put", ORG, USER, "openAI", ROTATED)
    assert replaced["version"] == 2

    builds = Builds()
    _ = await _access(service, builds).resolve(ORG, ROUTE, run_id="run-3")
    assert [e["configuration"]["apiKey"] for _, e, _, _ in builds.entries] == [ROTATED]  # type: ignore[index]

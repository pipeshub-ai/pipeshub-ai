"""CPD-8 LW-1..LW-3: the legacy credential routes' writes through ``LegacyCredentialRecords``."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.credentials.bindings import BindingDocuments
from app.harness.credentials.legacy_records import LegacyCredentialRecords
from app.harness.credentials.sources import TOOLSET_KIND, ToolsetSecretSource
from app.harness.credentials.vault import VaultSecretStore
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from harness_core.credentials import CredentialRevoked, CredentialScope
from harness_core.testing.clock import ManualClock
from tests.unit.harness.credentials.binding_support import FakeBindingWriter
from tests.unit.harness.credentials.support import DictConfig

ORG = "org-1"
OLD = "/services/toolsets/ts-1/u1"
TOKEN = "xoxe.xoxp-1-" + "LegacySeamAccessToken0123456789ab"


def record(token: str = TOKEN, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"isAuthenticated": True, "authType": "API_TOKEN", "toolsetType": "slack", "auth": {"apiToken": token}, **extra}


def world() -> tuple[DictConfig, FakeBindingWriter, LegacyCredentialRecords, ToolsetSecretSource]:
    config = DictConfig({"/services/toolset-instances": [{"_id": "ts-1", "orgId": ORG, "toolsetType": "slack"}]})
    writer = FakeBindingWriter(config)

    async def node() -> FakeBindingWriter:
        return writer

    seam = LegacyCredentialRecords(config, node)  # type: ignore[arg-type]
    source = ToolsetSecretSource(config, BindingDocuments(VaultSecretStore(config, ManualClock())))
    return config, writer, seam, source


SCOPE = CredentialScope(TOOLSET_KIND, "ts-1/u1")


async def test_lw1_off_writes_and_deletes_the_old_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTROL_PLANE_CREDENTIALS, raising=False)
    config, writer, seam, _ = world()
    assert await seam.save("toolset", ORG, "ts-1", "u1", record()) is True
    assert config.values[OLD] == record() and writer.calls == []
    assert await seam.load("toolset", ORG, "ts-1", "u1") == record()
    await seam.remove("toolset", ORG, "ts-1", "u1")
    assert OLD not in config.values and writer.rows == {}


async def test_lw2_on_a_save_is_a_new_binding_version_and_the_old_path_is_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    config, writer, seam, source = world()
    config.values[OLD] = record("migrated-copy-token-0123456789ab")
    assert await seam.save("toolset", ORG, "ts-1", "u1", record(), user_id="u1")
    assert config.values[OLD] == record("migrated-copy-token-0123456789ab")
    row = writer.find(ORG, "ts-1", "user:u1")
    assert row is not None and writer.audited[-1] == (ORG, row["id"], "user:u1")
    found = await source.read(ORG, SCOPE)
    assert found is not None and found.material == record()
    assert await seam.load("toolset", ORG, "ts-1", "u1") == record()
    _ = await seam.save("toolset", ORG, "ts-1", "a7", record(agentKey="a7"))
    assert writer.find(ORG, "ts-1", "agent:a7") is not None


async def test_lw3_on_a_disconnect_revokes_and_no_reader_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    config, writer, seam, source = world()
    _ = await seam.save("toolset", ORG, "ts-1", "u1", record())
    config.values[OLD] = record("stale-old-path-token-0123456789")
    await seam.remove("toolset", ORG, "ts-1", "u1", user_id="u1")
    row = writer.find(ORG, "ts-1", "user:u1")
    assert row is not None and row["status"] == "revoked"
    assert OLD not in config.values
    with pytest.raises(CredentialRevoked):
        _ = await source.read(ORG, SCOPE)


async def test_lw2_on_a_narrowing_reaches_both_and_the_source_answers_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    config, _, seam, source = world()
    _ = await seam.save("toolset", ORG, "ts-1", "u1", record())
    withdrawn = {**record(), "isAuthenticated": False, "deauthReason": "admin_oauth_config_updated"}
    _ = await seam.save("toolset", ORG, "ts-1", "u1", withdrawn, narrowing=True)
    assert config.values[OLD] == withdrawn
    assert await source.read(ORG, SCOPE) is None

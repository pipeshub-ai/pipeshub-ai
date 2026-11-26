"""CPD-8 MG-1..MG-10 (TSC-24): the D2 migration of toolset and MCP credentials into bindings.

The binding writer is ``FakeBindingWriter`` (Node's binder semantics, BM-1..9); the documents are read back
through the real vault and binding source. The old paths' records are compared byte for byte.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

import pytest

from app.harness.credentials import migrate
from app.harness.credentials.binding_writer import Imported, ImportRequest, Material
from app.harness.credentials.bindings import BindingDocuments
from app.harness.credentials.migration import (
    LEDGER_PATH,
    MIGRATION_ID,
    CredentialBindingMigration,
    record_expiry_ms,
)
from app.harness.credentials.vault import VaultSecretStore
from app.harness.migrations import HARNESS_DATA_MIGRATIONS
from harness_core.credentials import CredentialScope
from harness_core.security.token_patterns import find_forbidden
from harness_core.testing.clock import ManualClock
from tests.unit.harness.credentials.binding_support import FakeBindingWriter
from tests.unit.harness.credentials.support import DictConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

ORG, OTHER = "org-1", "org-2"
SLACK = "xoxe.xoxp-1-" + "MigrationSlackAccess0123456789ab"
REFRESH = "xoxe-1-" + "MigrationSlackRefresh0123456789ab"
MCP_TOKEN = "ghp_" + "Migration0123456789abcdefABCDEFghij"


def toolset_record(token: str = SLACK, *, agent: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "isAuthenticated": True,
        "authType": "OAUTH",
        "credentials": {"access_token": token, "refresh_token": REFRESH, "expires_in": 3600, "created_at": "2026-10-10T12:00:00"},
    }
    if agent is not None:
        record["agentKey"] = agent
    return record


def mcp_record() -> dict[str, Any]:
    return {"isAuthenticated": True, "authMode": "api_token", "credentials": {"apiToken": MCP_TOKEN}}


class Owners:
    def __init__(self, orgs: Mapping[str, str]) -> None:
        self.orgs = dict(orgs)

    async def org_of(self, kind: Literal["user", "agent"], owner: str) -> str | None:
        return self.orgs.get(f"{kind}:{owner}")


def world(values: dict[str, Any] | None = None, owners: Mapping[str, str] | None = None) -> tuple[DictConfig, FakeBindingWriter, CredentialBindingMigration]:
    config = DictConfig(
        {
            "/services/toolset-instances": [
                {"_id": "ts-1", "orgId": ORG, "toolsetType": "slack"},
                {"_id": "ts-2", "orgId": OTHER, "toolsetType": "slack"},
            ],
            "/services/mcp/instances/mcp-1": {"_id": "mcp-1", "orgId": ORG, "authMode": "api_token", "createdBy": "u1"},
            "/services/toolsets/ts-1/u1": toolset_record(),
            "/services/toolsets/ts-1/a7": toolset_record(agent="a7"),
            "/services/toolsets/ts-1/u2": {"isAuthenticated": False},
            "/services/toolsets/ts-2/u1": toolset_record(),
            "/services/toolsets/ts-1/ghost": toolset_record(),
            "/services/mcp/credentials/mcp-1/u1": mcp_record(),
            "/services/mcp/credentials/mcp-1/u1/dcr-client": {"clientId": "c-1"},
            **(values or {}),
        }
    )
    writer = FakeBindingWriter(config)
    migration = CredentialBindingMigration(
        config,
        Owners(owners or {"user:u1": ORG, "user:u2": ORG, "agent:a7": ORG}),
        writer,
        BindingDocuments(VaultSecretStore(config, ManualClock())),
        now_ms=lambda: 1_000,
    )
    return config, writer, migration


def old_paths(config: DictConfig) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in config.values.items() if k.startswith(("/services/toolsets/", "/services/mcp/credentials/"))}


def keys(entries: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    return sorted((e["kind"], e["installation"], e["owner"]) for e in entries)


async def test_mg1_dry_run_reports_and_writes_nothing() -> None:
    config, writer, migration = world()
    before = copy.deepcopy(config.values)
    report = await migration.dry_run()
    assert keys(report.copied) == [("mcp", "mcp-1", "user:u1"), ("toolset", "ts-1", "agent:a7"), ("toolset", "ts-1", "user:u1")]
    assert {(e["installation"], e["owner"], e["reason"]) for e in report.held} == {
        ("ts-2", "user:u1", "not_visible"),
        ("ts-1", "user:ghost", "owner_unknown"),
    }
    assert config.values == before and writer.calls == []


async def test_mg2_apply_copies_and_verifies_each_record() -> None:
    config, writer, migration = world()
    before = old_paths(config)
    report = await migration.apply()
    assert keys(report.copied) == [("mcp", "mcp-1", "user:u1"), ("toolset", "ts-1", "agent:a7"), ("toolset", "ts-1", "user:u1")]
    documents = BindingDocuments(VaultSecretStore(config, ManualClock()))
    path, document = await documents.owner_document(ORG, "ts-1", "user:u1")
    assert document["binding"]["source"]["path"] == "/services/toolsets/ts-1/u1"
    copied = await documents.vault.read_at(path, ORG, CredentialScope("binding", document["binding"]["binding"]))
    assert copied is not None and copied.material == toolset_record()
    assert writer.find(ORG, "ts-1", "user:u1")["expiresAt"] == record_expiry_ms("toolset", toolset_record())
    assert old_paths(config) == before


class CorruptingWriter(FakeBindingWriter):
    """Stores something other than it was given, as a broken binder would."""

    async def import_record(self, org_id: str, request: ImportRequest, *, user_id: str | None = None) -> Imported:
        bad = Material(request.material.kind, {**request.material.value, "tampered": True})
        return await super().import_record(org_id, replace(request, material=bad), user_id=user_id)


async def test_mg2_a_digest_mismatch_removes_the_copy_and_leaves_the_old_path() -> None:
    config, _, _ = world()
    writer = CorruptingWriter(config)
    migration = CredentialBindingMigration(
        config, Owners({"user:u1": ORG, "agent:a7": ORG}), writer, BindingDocuments(VaultSecretStore(config, ManualClock())), now_ms=lambda: 1
    )
    before = old_paths(config)
    report = await migration.apply()
    assert keys(report.mismatched) == [("mcp", "mcp-1", "user:u1"), ("toolset", "ts-1", "agent:a7"), ("toolset", "ts-1", "user:u1")]
    assert report.copied == [] and writer.rows == {}
    assert old_paths(config) == before
    assert config.values[LEDGER_PATH]["imported"] == []
    assert await migrate.main(["--apply"], build=_build(migration)) == 1


def _build(migration: CredentialBindingMigration):  # noqa: ANN202
    async def build(_logger: logging.Logger) -> CredentialBindingMigration:
        return migration

    return build


async def test_mg3_apply_again_writes_nothing() -> None:
    config, writer, migration = world()
    _ = await migration.apply()
    calls, audited = list(writer.calls), list(writer.audited)
    report = await migration.apply()
    assert report.copied == [] and len(report.unchanged) == 3
    assert writer.audited == audited and len(writer.calls) == len(calls) + 3
    assert len((await migration.dry_run()).unchanged) == 3


async def test_mg4_an_old_record_changed_before_the_flip_is_copied_again() -> None:
    config, writer, migration = world()
    _ = await migration.apply()
    config.values["/services/toolsets/ts-1/u1"] = toolset_record("changed-before-the-flip-0123456789")
    report = await migration.apply()
    assert [(e["installation"], e["owner"], e["outcome"]) for e in report.copied] == [("ts-1", "user:u1", "updated")]
    row = writer.find(ORG, "ts-1", "user:u1")
    assert row is not None and row["version"] == 2


async def test_mg5_scoping_holds_what_the_owners_org_cannot_see() -> None:
    _, writer, migration = world()
    report = await migration.apply()
    assert writer.find(OTHER, "ts-2", "user:u1") is None and writer.find(ORG, "ts-2", "user:u1") is None
    assert {e["reason"] for e in report.held} == {"not_visible", "owner_unknown"}
    assert all(e["installation"] != "ts-1" or e["owner"] != "user:u2" for e in report.held + report.copied)


async def test_mg6_apply_never_writes_an_old_path() -> None:
    config, _, migration = world()
    before = old_paths(config)
    _ = await migration.apply()
    _ = await migration.apply()
    assert old_paths(config) == before
    assert not [k for k in config.writes if k.startswith(("/services/toolsets/", "/services/mcp/credentials/"))]


async def test_mg7_revert_removes_the_imports_and_restores_the_old_paths() -> None:
    config, writer, migration = world()
    _ = await migration.apply()
    refreshed = writer.find(ORG, "ts-1", "user:u1")
    assert refreshed is not None
    _ = await writer.refreshed(ORG, refreshed["id"], refreshed["revision"], Material("record", toolset_record("rotated-by-the-refresher-01234567")))
    revoked = writer.find(ORG, "mcp-1", "user:u1")
    assert revoked is not None
    _ = await writer.revoke_owner(ORG, "mcp-1", "user:u1")
    report = await migration.revert()
    assert len(report.reverted) == 3 and writer.rows == {}
    assert config.values["/services/toolsets/ts-1/u1"] == toolset_record("rotated-by-the-refresher-01234567")
    assert "/services/mcp/credentials/mcp-1/u1" not in config.values
    assert config.values["/services/toolsets/ts-1/a7"] == toolset_record(agent="a7")
    assert not [k for k in config.values if k.startswith("/services/controlPlane/credentials/")]
    again = await migration.revert()
    assert again.reverted == [] and again.held == []


async def test_mg7_revert_keeps_a_binding_it_did_not_import() -> None:
    config, writer, migration = world()
    _ = await migration.apply()
    native = writer.find(ORG, "ts-1", "user:u1")
    assert native is not None
    path = writer.vault(ORG).path(native["id"])
    config.values[path]["binding"]["source"] = None
    report = await migration.revert()
    assert [(e["installation"], e["reason"]) for e in report.held] == [("ts-1", "binding_not_imported")]
    assert writer.find(ORG, "ts-1", "user:u1") is not None
    assert await migrate.main(["--revert"], build=_build(migration)) == 1


async def test_mg8_no_secret_in_the_report_the_ledger_or_the_logs(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    config, _, migration = world()
    for mode in ("--apply", "", "--revert"):
        assert await migrate.main([mode] if mode else [], build=_build(migration)) == 0
    out = capsys.readouterr().out
    texts = [out, json.dumps(config.values.get(LEDGER_PATH)), caplog.text]
    for text in texts:
        for secret in (SLACK, REFRESH, MCP_TOKEN):
            assert secret not in text
        assert find_forbidden(text) == ()
    assert json.loads(out.split("\n}\n")[0] + "\n}")["mode"] == "apply"


def test_mg9_registered_with_its_ledger() -> None:
    assert HARNESS_DATA_MIGRATIONS[MIGRATION_ID] is CredentialBindingMigration
    assert CredentialBindingMigration.ledger_path == f"/migrations/{MIGRATION_ID}"


async def test_mg10_cli_modes(capsys: pytest.CaptureFixture[str]) -> None:
    _, writer, migration = world()
    assert migrate.parse_mode([]) == "dry_run"
    assert migrate.parse_mode(["--apply"]) == "apply"
    assert migrate.parse_mode(["--revert"]) == "revert"
    with pytest.raises(SystemExit):
        _ = migrate.parse_mode(["--apply", "--revert"])
    assert await migrate.main([], build=_build(migration)) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry_run" and writer.calls == []

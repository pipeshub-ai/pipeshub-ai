"""The world and the checks of the scoped code-path lookup, for every store's real-store test.

``repos`` repos of one code connector each hold ``src/main.py`` with ``function:main``, filed as a code host files them: a repo group named
``acme/repo<i>`` and, under it by its external id, a child group named "Code repository" that holds the record. One user owns every file.
``answers`` asks the neighbour tool the same question under every way a caller names a repo, so the old lookup's answers and the scoped one's
can be compared whole, the ambiguity report included.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.agents.actions.code_graph import ops
from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.migrations.code_path_scope_migration import run_code_path_scope_backfill
from app.models.entities import CodeFileRecord, RecordGroup, RecordGroupType, RecordType
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.parsers.code_parser import CodeFileParser
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

PATH = "src/main.py"
SOURCE = b"def main():\n    return 1\n"


async def write_world(g: IGraphDBProvider, org_id: str, connector_id: str, repos: int) -> dict[str, Any]:
    """Write the repos (in the caller's org context) and return their ids: ``groups`` (repo, code) per repo, ``records``, ``user_id``."""
    run = uuid.uuid4().hex[:8]
    await g.batch_upsert_nodes([{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "path-scope"}], CollectionNames.ORGS.value)
    tops = [RecordGroup(org_id=org_id, name=f"acme/repo{i}", external_group_id=f"ext-{run}-{i}", group_type=RecordGroupType.REPOSITORY,
                        connector_name=Connectors.GITHUB, connector_id=connector_id) for i in range(repos)]
    codes = [RecordGroup(org_id=org_id, name="Code repository", external_group_id=f"ext-{run}-{i}-code", parent_external_group_id=top.external_group_id,
                         group_type=RecordGroupType.REPOSITORY, connector_name=Connectors.GITHUB, connector_id=connector_id) for i, top in enumerate(tops)]
    await g.batch_upsert_record_groups(tops + codes)
    records = [CodeFileRecord(org_id=org_id, record_name="main.py", record_type=RecordType.CODE_FILE, external_record_id=f"file-{run}-{i}", version=1,
                              origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB, connector_id=connector_id, record_group_id=code.id,
                              mime_type="text/x-python", extension="py", file_path=PATH, file_hash=uuid.uuid4().hex, language="python",
                              indexing_status=ProgressStatus.COMPLETED.value, commit_sha="c0ffee") for i, code in enumerate(codes)]
    await g.batch_upsert_records(records)
    container = CodeFileParser().parse_to_blocks(SOURCE, "main.py", PATH, "python")
    for rec, code in zip(records, codes, strict=True):
        await write_code_file_blocks_to_graph(graph_provider=g, context=BlockProjectionContext(
            org_id=org_id, record_id=rec.id, record_group_id=code.id, connector_id=connector_id, language="python", file_path=PATH), block_containers=container)
    user_id, user_key, now = f"user-{run}", f"u-{run}", get_epoch_timestamp_in_ms()
    await g.batch_upsert_nodes([{"id": user_key, "userId": user_id, "orgId": org_id, "email": f"{user_id}@example.com", "fullName": "Path Tester", "isActive": True,
                                 "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.USERS.value)
    await g.batch_upsert_nodes([{"id": connector_id, "orgId": org_id, "name": "GitHub", "type": "GitHub", "appGroup": "GitHub", "scope": "team", "isActive": True,
                                 "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.APPS.value)

    def edge(to_id: str, to_collection: str, **extra: object) -> dict:
        return {"from_id": user_key, "from_collection": CollectionNames.USERS.value, "to_id": to_id, "to_collection": to_collection,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}
    await g.batch_create_edges([edge(r.id, CollectionNames.RECORDS.value, role="OWNER", type="USER") for r in records], collection=CollectionNames.PERMISSION.value)
    await g.batch_create_edges([edge(connector_id, CollectionNames.APPS.value, syncState="COMPLETED", lastSyncUpdate=now)],
                               collection=CollectionNames.USER_APP_RELATION.value)
    return {"groups": [(t.id, c.id) for t, c in zip(tops, codes, strict=True)], "records": [r.id for r in records], "user_id": user_id}


def repo_names(world: dict[str, Any]) -> list[str | None]:
    """Every way a caller names repo 3 (and none, and one that is not there): full name, bare name, another case, a trailing slash, the code group's id."""
    return ["acme/repo3", "repo3", "ACME/Repo3", "acme/repo3/", world["groups"][3][1], world["groups"][3][0], "nope", None]


async def answers(g: IGraphDBProvider, org_id: str, connector_id: str, world: dict[str, Any]) -> dict[str, Any]:
    """The neighbour tool's whole answer under each repo name (in the caller's org context)."""
    out = {}
    for repo in repo_names(world):
        try:
            out[str(repo)] = await get_neighbour_impl(graph_provider=g, connector_id=connector_id, org_id=org_id, user_id=world["user_id"], repo=repo,
                                                      file_path=PATH, qualified_name="function:main", direction="any", depth=1)
        except Exception as e:          # the ambiguity report is an error the caller shows
            out[str(repo)] = {"raised": type(e).__name__, "text": str(e)}
    return out


class RowCounter:
    """Counts the documents the tool reads by filter or by key (wraps the provider's two generic reads)."""

    def __init__(self, g: IGraphDBProvider) -> None:
        self.g, self.rows = g, 0
        self._filters, self._field_in = g.get_nodes_by_filters, g.get_nodes_by_field_in

    async def _by_filters(self, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        rows = await self._filters(*a, **k)
        self.rows += len(rows or [])
        return rows

    async def _by_field_in(self, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        rows = await self._field_in(*a, **k)
        self.rows += len(rows or [])
        return rows

    def install(self, monkeypatch: Any) -> None:  # noqa: ANN401
        monkeypatch.setattr(self.g, "get_nodes_by_filters", self._by_filters)
        monkeypatch.setattr(self.g, "get_nodes_by_field_in", self._by_field_in)


async def backfill(g: IGraphDBProvider, logger: Any) -> int:  # noqa: ANN401
    """The start-up backfill, and forget what this process knew of the marker."""
    ops._SCOPED_ORGS.clear()
    return await run_code_path_scope_backfill(g, logger)


async def records_at(g: IGraphDBProvider, org_id: str, connector_id: str, world: dict[str, Any], repo: str) -> list[str]:
    """The records the tools resolve ``PATH`` to in ``repo`` (in the caller's org context), as resolve_symbol asks."""
    from app.agents.actions.code_graph.ops import (
        RecordAccess,
        _choose_records,
        _records_for_path,
    )
    access = RecordAccess(g, org_id, world["user_id"])
    ids, _ = await _records_for_path(g, org_id, PATH, choose=lambda keys: _choose_records(
        g, org_id, keys, connector_id, file_path=PATH, repo=repo, access=access, ambiguous_ok=True), connector_id=connector_id, repo=repo)
    return sorted(ids)


async def early_zero_backfill(g: IGraphDBProvider, logger: Any, monkeypatch: Any, strip: Any) -> None:  # noqa: ANN401
    """A batch that updates nothing while rows remain (writers filled what it picked) must not end the backfill: the runner asks the store
    whether any row is left, and marks the org only when none is. ``strip`` takes the fields off an org's rows, as before the scope existed."""
    import uuid as _uuid

    from app.core.org_context import for_org
    from app.migrations import code_path_scope_migration as migration
    org, connector = _uuid.uuid4().hex[:24], f"conn-{_uuid.uuid4().hex[:6]}"
    with for_org(org, job="code-path-scope-test"):
        world = await write_world(g, org, connector, 3)
    await strip(org)
    real, calls = g.backfill_code_path_scope, {"n": 0}

    async def first_idle(limit: int = 5000, *, org_id: str | None = None) -> int:
        if org_id != org:
            return await real(limit=limit, org_id=org_id)
        calls["n"] += 1
        return 0 if calls["n"] == 1 else await real(limit=limit, org_id=org_id)
    monkeypatch.setattr(g, "backfill_code_path_scope", first_idle)
    await backfill(g, logger)
    with for_org(org, job="code-path-scope-test"):
        assert await migration.scope_ready(g, org)
        files = await g.get_nodes_by_field_in(CollectionNames.CODE_FILES.value, "_key", world["records"], raise_on_error=True)
    assert calls["n"] > 1 and all(f.get("connectorId") == connector for f in files), (calls, files)


async def external_org_is_marked(g: IGraphDBProvider, logger: Any, strip: Any) -> None:  # noqa: ANN401
    """An external org's rows are filled and the org is marked, as an internal org's are."""
    import uuid as _uuid

    from app.core.org_context import for_org
    from app.migrations import code_path_scope_migration as migration
    org, connector = _uuid.uuid4().hex[:24], f"conn-{_uuid.uuid4().hex[:6]}"
    with for_org(org, job="code-path-scope-test"):
        world = await write_world(g, org, connector, 2)
        await g.batch_upsert_nodes([{"_key": org, "accountType": "enterprise", "isActive": True, "name": "path-scope", "isExternal": True}],
                                   CollectionNames.ORGS.value)
    await strip(org)
    await backfill(g, logger)
    with for_org(org, job="code-path-scope-test"):
        assert await migration.scope_ready(g, org)
        files = await g.get_nodes_by_field_in(CollectionNames.CODE_FILES.value, "_key", world["records"], raise_on_error=True)
    assert all(f.get("connectorId") == connector for f in files), files


async def org_with_leftovers_does_not_block_another(g: IGraphDBProvider, logger: Any, monkeypatch: Any, strip: Any) -> None:  # noqa: ANN401
    """An org whose rows stay pending (the backfill cannot fill them) stays unmarked, and another org is still filled and marked."""
    import uuid as _uuid

    from app.core.org_context import for_org
    from app.migrations import code_path_scope_migration as migration
    (good, stuck), connector = (_uuid.uuid4().hex[:24] for _ in range(2)), f"conn-{_uuid.uuid4().hex[:6]}"
    for org in (good, stuck):
        with for_org(org, job="code-path-scope-test"):
            await write_world(g, org, connector, 1)
        await strip(org)
    real = g.code_path_scope_pending

    async def stuck_pending(*, org_id: str | None = None) -> bool:
        # Asked for every org at once, the stuck org's rows are among the answer.
        return True if org_id in (None, stuck) else await real(org_id=org_id)
    monkeypatch.setattr(g, "code_path_scope_pending", stuck_pending)
    monkeypatch.setattr(migration, "_IDLE_PASSES", 3)
    await backfill(g, logger)
    with for_org(good, job="code-path-scope-test"):
        assert await migration.scope_ready(g, good)
    with for_org(stuck, job="code-path-scope-test"):
        assert not await migration.scope_ready(g, stuck)

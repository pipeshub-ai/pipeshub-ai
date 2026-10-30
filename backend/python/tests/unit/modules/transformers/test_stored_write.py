"""The stored-write protocol without a graph or a storage service: the settings read that picks the
layout, the run it mints, the retired list, the retire mark, and the pins that keep blind mapping
writes out of every stored-write path."""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as blob_module
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.stored_write import (
    COW,
    IN_PLACE,
    MAX_RETIRED,
    StoredWrite,
    is_none_run,
    mint_stored_write,
    run_generation,
    with_retired,
)
from app.modules.transformers.transformer import TransformContext
from app.services.featureflag.platform_settings import (
    ARTIFACT_GENERATION_KEY,
    ArtifactSettings,
    artifact_read_mode,
    artifact_read_state,
    artifact_settings,
)

GENERATION = "gen-1"
FLAGS = ("ENABLE_ARTIFACT_READ", "ENABLE_ARTIFACT_READ_SHADOW", "ENABLE_ARTIFACT_WRITE")


def _config(blob: Any) -> MagicMock:  # noqa: ANN401
    service = MagicMock()
    service.get_config = AsyncMock(return_value=blob)
    return service


def _blobs() -> list[dict[str, Any]]:
    """Every combination of the three flags with and without a generation, and some broken blobs."""
    out: list[dict[str, Any]] = []
    for read in (False, True):
        for shadow in (False, True):
            for write in (False, True):
                for generation in (None, GENERATION, ""):
                    blob: dict[str, Any] = {"featureFlags": {
                        "ENABLE_ARTIFACT_READ": read, "ENABLE_ARTIFACT_READ_SHADOW": shadow,
                        "ENABLE_ARTIFACT_WRITE": write,
                    }}
                    if generation is not None:
                        blob[ARTIFACT_GENERATION_KEY] = generation
                    out.append(blob)
    out += [{}, {"featureFlags": None}, {"featureFlags": {"enable_artifact_read": True}}, {ARTIFACT_GENERATION_KEY: 7}]
    return out


class TestOneSettingsRead:
    """T43. The layout, the generation and the run come from one read of the blob."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("blob", _blobs())
    async def test_layout_generation_and_run_agree_for_every_settings_blob(self, blob: dict[str, Any]) -> None:
        config = _config(blob)

        settings = await artifact_settings(config)
        write = mint_stored_write(settings)

        assert config.get_config.await_count == 1
        assert config.get_config.await_args.kwargs["max_age_seconds"] == 30.0
        flags = {str(k).upper(): v for k, v in (blob.get("featureFlags") or {}).items()}
        on = bool(flags.get("ENABLE_ARTIFACT_READ") or flags.get("ENABLE_ARTIFACT_READ_SHADOW"))
        assert settings.layout == (COW if on else IN_PLACE)
        assert write.layout == settings.layout
        if write.layout == IN_PLACE:
            # An in-place write is never servable, whatever the blob carries.
            assert is_none_run(write.run) and write.generation is None and settings.generation is None
        else:
            expected = blob.get(ARTIFACT_GENERATION_KEY)
            expected = expected if isinstance(expected, str) and expected else None
            assert run_generation(write.run) == (expected or "none")
            assert write.generation == expected

    @pytest.mark.asyncio
    async def test_a_blob_that_cannot_be_read_writes_in_place_and_reads_legacy(self) -> None:
        config = MagicMock()
        config.get_config = AsyncMock(side_effect=RuntimeError("store down"))

        assert await artifact_settings(config) == ArtifactSettings(IN_PLACE, None)
        assert await artifact_read_mode(config) == "legacy"
        assert await artifact_settings(None) == ArtifactSettings(IN_PLACE, None)

    @pytest.mark.asyncio
    async def test_the_read_state_takes_mode_and_generation_from_one_read(self) -> None:
        config = _config({"featureFlags": {"ENABLE_ARTIFACT_READ": True}, ARTIFACT_GENERATION_KEY: GENERATION})

        state = await artifact_read_state(config)

        assert (state.mode, state.generation) == ("artifact", GENERATION)
        assert config.get_config.await_count == 1

    def test_runs_are_unique_and_name_their_generation(self) -> None:
        a, b = mint_stored_write(ArtifactSettings(COW, GENERATION)), mint_stored_write(ArtifactSettings(COW, GENERATION))
        assert a.run != b.run and run_generation(a.run) == GENERATION
        assert is_none_run(mint_stored_write(ArtifactSettings(COW, None)).run)

    @pytest.mark.asyncio
    async def test_the_sink_reads_the_settings_once_and_hands_the_same_write_down(self) -> None:
        reads = AsyncMock(return_value=ArtifactSettings(COW, GENERATION))
        orch = _sink()
        seen: list[StoredWrite] = []

        async def apply(ctx: TransformContext) -> None:
            seen.append(ctx.settings["stored_write"])

        orch.blob_storage.apply = AsyncMock(side_effect=apply)
        with patch.object(sink_module, "artifact_settings", reads):
            ctx = TransformContext(record=_record())
            await orch.index(ctx)

        reads.assert_awaited_once()
        assert seen == [ctx.settings["stored_write"]] and seen[0].layout == COW

    def test_nothing_below_the_sink_reads_the_settings(self) -> None:
        """The blob store never imports or calls the settings readers (control: a source that does is caught)."""
        source = inspect.getsource(blob_module)
        assert _reads_settings(source) == []
        broken = source + "\n\nasync def sneaky(cs):\n    return await artifact_settings(cs)\n"
        assert _reads_settings(broken) == ["artifact_settings"]
        broken_import = "from app.services.featureflag.platform_settings import artifact_read_mode\n" + source
        assert "platform_settings" in _reads_settings(broken_import)


def _reads_settings(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and "platform_settings" in node.module:
            found.append("platform_settings")
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in ("artifact_settings", "artifact_read_state", "artifact_read_mode", "is_artifact_write_enabled"):
                found.append(name)
    return found


def _record() -> Any:  # noqa: ANN401
    from app.config.constants.arangodb import Connectors
    from app.models.blocks import Block, BlocksContainer, BlockType
    from app.models.entities import Record, RecordType

    return Record(
        id="rec-1", org_id="org-1", virtual_record_id="vr-1", record_name="a.txt", record_type=RecordType.FILE,
        external_record_id="e", version=1, origin="UPLOAD", connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="c", block_containers=BlocksContainer(
            blocks=[Block(index=0, type=BlockType.TEXT, data="x")], block_groups=[],
        ),
    )


def _sink() -> Any:  # noqa: ANN401
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"indexingStatus": "NOT_STARTED", "contentRev": "0123456789abcdef"})
    graph.batch_update_nodes = AsyncMock(return_value=True)
    vectors = AsyncMock()
    vectors.apply = AsyncMock(return_value=True)
    orch = sink_module.SinkOrchestrator(
        graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=vectors, graph_provider=graph,
        logger=logging.getLogger("t"), config_service=MagicMock(),
    )
    orch._save_reconciliation_metadata = AsyncMock()
    orch._sync_record_identity_entities = AsyncMock()
    orch.image_describer.annotate = AsyncMock(return_value=0)
    orch.artifact_writer = MagicMock()
    orch.artifact_writer.write_revision = AsyncMock(return_value=None)
    return orch


# ---------------------------------------------------------------------------
# Pins: no blind mapping writes from a stored write
# ---------------------------------------------------------------------------

STORED_WRITE_FUNCTIONS = (
    "rewrite_semantic_metadata", "_rewrite_copy_on_write", "_rewrite_in_place", "_read_mapped_record",
    "apply", "_store_record_dict", "_store_copy_on_write", "_store_in_place", "_switch_mapping", "_switch_fields",
    "_put_buffer_with_retry", "_retire_document", "_shared_content_inputs",
)
BLIND = ("batch_upsert_nodes", "store_virtual_record_mapping", "_delete_replaced_document", "upload_next_version")


def _calls_in(source: str, functions: tuple[str, ...]) -> dict[str, set[str]]:
    tree = ast.parse(source)
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            names = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    names.add(func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else "")
            found[node.name] = names
    return found


class TestNoBlindMappingWrites:
    def test_no_stored_write_path_reaches_a_blind_write(self) -> None:
        """T44′."""
        calls = _calls_in(inspect.getsource(blob_module), STORED_WRITE_FUNCTIONS)
        assert set(calls) == set(STORED_WRITE_FUNCTIONS)
        for function, names in calls.items():
            assert not names & set(BLIND), (function, names & set(BLIND))

    def test_the_blind_mapping_upsert_no_longer_exists(self) -> None:
        assert not hasattr(BlobStorage, "store_virtual_record_mapping")
        for path in Path(blob_module.__file__).parents[2].rglob("*.py"):
            assert "store_virtual_record_mapping(" not in path.read_text(), path

    def test_the_pin_catches_a_blind_upsert_added_to_the_in_place_path(self) -> None:
        """Control."""
        source = inspect.getsource(blob_module).replace(
            "        run = stored_write.run\n        for _ in (1, 2):",
            "        run = stored_write.run\n        await self.graph_provider.batch_upsert_nodes([], 'x')\n        for _ in (1, 2):",
            1,
        )
        assert source != inspect.getsource(blob_module)
        calls = _calls_in(source, STORED_WRITE_FUNCTIONS)
        assert "batch_upsert_nodes" in calls["_store_in_place"]

    def test_only_the_reconciliation_adoption_upserts_the_mapping(self) -> None:
        tree = ast.parse(inspect.getsource(blob_module))
        users = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Attribute) and sub.attr == "batch_upsert_nodes":
                        users.add(node.name)
        assert users == {"_map_reconciliation_document"}


# ---------------------------------------------------------------------------
# The retired list
# ---------------------------------------------------------------------------


class TestRetiredList:
    def test_a_switch_appends_the_replaced_document(self) -> None:
        node = {"documentId": "d1", "storedRev": "r1", "storedMeta": "enrich/classification.ab@1.json", "retired": []}

        retired, overflow = with_retired(node, "d1", "d2", 5)

        assert retired == [{"doc": "d1", "rev": "r1", "sidecar": "enrich/classification.ab@1.json", "at": 5}]
        assert overflow == 0

    def test_a_first_write_or_a_switch_to_the_same_document_adds_nothing(self) -> None:
        assert with_retired(None, None, "d1", 1) == ([], 0)
        assert with_retired({"documentId": "d1"}, "d1", "d1", 1) == ([], 0)

    def test_the_list_keeps_the_newest_entries_and_counts_the_dropped(self) -> None:
        node = {"documentId": "dn", "retired": [{"doc": f"d{i}", "at": i} for i in range(MAX_RETIRED)]}

        retired, overflow = with_retired(node, "dn", "dnew", 99)

        assert len(retired) == MAX_RETIRED and overflow == 1
        assert retired[0]["doc"] == "d1" and retired[-1]["doc"] == "dn"


# ---------------------------------------------------------------------------
# The retire mark
# ---------------------------------------------------------------------------


def _post_session(status: int, body: Any = None, *, json_error: bool = False) -> MagicMock:  # noqa: ANN401
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(side_effect=ValueError("not json")) if json_error else AsyncMock(return_value=body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    return session


class TestRetireMark:
    @staticmethod
    def _blob() -> BlobStorage:
        bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
        bs._get_auth_and_config = AsyncMock(return_value=({"Authorization": "x"}, "http://node", "local"))  # type: ignore[method-assign]
        return bs

    @pytest.fixture(autouse=True)
    def _fresh_route_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        blob_module._RETIRE_ROUTE["down_until"] = 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("status", "body", "json_error", "outcome"), [
        (200, {"retiredAt": 1}, False, "marked"),
        (404, {"error": {"code": "HTTP_NOT_FOUND"}}, False, "gone"),
        (404, None, True, "unavailable"),
        (503, {"error": {}}, False, "failed"),
    ])
    async def test_outcomes(self, status: int, body: Any, json_error: bool, outcome: str) -> None:  # noqa: ANN401
        seen: list[str] = []
        with patch.object(blob_module, "get_shared_session", return_value=_post_session(status, body, json_error=json_error)), \
                patch.object(blob_module, "record_retire_mark", seen.append):
            await self._blob()._retire_document("org-1", "a" * 24)

        assert seen == [outcome]

    @pytest.mark.asyncio
    async def test_a_missing_route_is_remembered_and_not_called_again(self) -> None:
        seen: list[str] = []
        session = _post_session(404, None, json_error=True)
        bs = self._blob()
        with patch.object(blob_module, "get_shared_session", return_value=session), \
                patch.object(blob_module, "record_retire_mark", seen.append):
            await bs._retire_document("org-1", "a" * 24)
            await bs._retire_document("org-1", "b" * 24)

        assert seen == ["unavailable", "unavailable"]
        assert session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_a_network_error_never_fails_the_write(self) -> None:
        session = MagicMock()
        session.post = MagicMock(side_effect=OSError("down"))
        with patch.object(blob_module, "get_shared_session", return_value=session):
            await self._blob()._retire_document("org-1", "a" * 24)


# ---------------------------------------------------------------------------
# The writer deadline
# ---------------------------------------------------------------------------


class TestWriterDeadline:
    """T18. A writer measures its own time on a clock that counts suspend, from just before its create. Past the
    deadline it does not switch: its document becomes a stray the stray pass may collect."""

    @staticmethod
    def _blob(clock: list[float]) -> BlobStorage:
        bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
        bs._mapping_node = AsyncMock(return_value={  # type: ignore[method-assign]
            "id": "vr-1", "documentId": "d1", "record_doc_id": "d1", "storedRun": "none:p", "switchSeq": 2,
        })
        bs._get_document_row = AsyncMock(return_value={"documentPath": "org-1/PipesHub/records/x", "customMetadata": []})  # type: ignore[method-assign]
        bs._retire_document = AsyncMock(return_value="marked")  # type: ignore[method-assign]
        bs._record_still_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bs._switch_mapping = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bs._live_holders = AsyncMock(return_value=["rec-1"])  # type: ignore[method-assign]
        bs._keep_owner_identity = AsyncMock(side_effect=lambda *a, **k: a[3])  # type: ignore[method-assign]

        async def create(*a: Any, **k: Any) -> tuple[str, int]:  # noqa: ANN401
            clock[0] += clock[1]  # the machine was suspended for this long during the create
            return "d2", 10

        bs.save_record_to_storage = AsyncMock(side_effect=create)  # type: ignore[method-assign]
        return bs

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("suspended", "switches"), [(60.0, True), (31 * 60.0, False)])
    async def test_a_writer_past_its_deadline_does_not_switch(
        self, suspended: float, switches: bool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = [1000.0, suspended]
        bs = self._blob(clock)
        monkeypatch.setattr(blob_module, "boottime", lambda: clock[0])
        write = StoredWrite(COW, "gen", "gen:run")

        if switches:
            await bs._store_copy_on_write(
                "org-1", "rec-1", "vr-1", {"id": "rec-1"}, storage_path="records/x", connector_id=None,
                record_group_id=None, stored_rev="r", stored_write=write,
            )
            bs._switch_mapping.assert_awaited_once()
            return
        from app.modules.transformers.stored_write import RunSuperseded

        with pytest.raises(RunSuperseded):
            await bs._store_copy_on_write(
                "org-1", "rec-1", "vr-1", {"id": "rec-1"}, storage_path="records/x", connector_id=None,
                record_group_id=None, stored_rev="r", stored_write=write,
            )
        bs._switch_mapping.assert_not_awaited()

    def test_the_deadline_clock_counts_suspend_where_the_platform_has_it(self) -> None:
        import time as time_module

        from app.modules.transformers.stored_write import boottime

        if hasattr(time_module, "CLOCK_BOOTTIME"):
            assert abs(boottime() - time_module.clock_gettime(time_module.CLOCK_BOOTTIME)) < 1.0

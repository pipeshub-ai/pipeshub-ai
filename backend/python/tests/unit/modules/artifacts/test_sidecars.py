"""``ArtifactSidecars``: one stage output per stored write, at a path of its own, never failing its caller."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import SemanticMetadata
from app.modules.artifacts import sidecars as sidecars_module
from app.modules.artifacts import store as store_module
from app.modules.artifacts.sidecars import (
    ArtifactSidecars,
    classification_payload,
    sidecar_path,
)
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import canonical_json
from app.modules.transformers.stored_write import write_id
from tests.unit.modules.artifacts.fake_storage import SIGNATURE, FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REV = "0123456789abcdef"
RUN = "gen:run-1"
PAYLOAD = {"summary": "s", "topics": ["VPN", "Ünïcode"], "categories": ["Security"]}


@pytest.fixture(autouse=True)
def _routes_available() -> Iterator[None]:
    store_module.ARTIFACT_ROUTES.reset()
    yield
    store_module.ARTIFACT_ROUTES.reset()


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def outcomes() -> Iterator[list[str]]:
    seen: list[str] = []
    with patch.object(sidecars_module, "record_artifact_write", seen.append):
        yield seen


def _flag(on: bool) -> object:
    return patch.object(sidecars_module, "is_artifact_write_enabled", AsyncMock(return_value=on))


def _sidecars(storage: FakeStorage) -> ArtifactSidecars:
    return ArtifactSidecars(NodeArtifactStore(storage.blob_storage()), MagicMock(), logging.getLogger("sidecars"))


class TestNoOp:
    async def test_flag_off_writes_nothing(self, storage: FakeStorage, outcomes: list[str]) -> None:
        with _flag(False):
            assert await _sidecars(storage).write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=RUN) is False
        assert storage.presign_calls == [] and outcomes == []

    @pytest.mark.parametrize(("vrid", "rev"), [("vr-1", None), ("vr-1", ""), ("", REV)])
    async def test_no_revision_writes_nothing(
        self, storage: FakeStorage, outcomes: list[str], vrid: str, rev: str | None,
    ) -> None:
        flag = AsyncMock(return_value=True)
        with patch.object(sidecars_module, "is_artifact_write_enabled", flag):
            assert await _sidecars(storage).write("org-1", vrid, rev, "classification", 1, PAYLOAD, stored_run=RUN) is False
        assert storage.presign_calls == [] and outcomes == []


    @pytest.mark.parametrize("run", [None, "", "none:abc"])
    async def test_a_run_that_keeps_no_set_writes_nothing(
        self, storage: FakeStorage, outcomes: list[str], run: str | None,
    ) -> None:
        with _flag(True):
            assert await _sidecars(storage).write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=run) is False
        assert storage.presign_calls == [] and outcomes == []


class TestWrite:
    async def test_one_path_and_canonical_bytes(self, storage: FakeStorage, outcomes: list[str]) -> None:
        with _flag(True):
            assert await _sidecars(storage).write("org-1", "vr-1", REV, "classification", 3, PAYLOAD, stored_run=RUN) is True

        [call] = storage.presign_calls
        [file] = call.pop("files")
        assert call == {"virtualRecordId": "vr-1", "rev": REV, "writeId": write_id(RUN)}
        assert re.fullmatch(r"enrich/classification\.[0-9a-f]{32}@3\.json", file["path"])
        assert file["contentType"] == "application/json"
        put = storage.puts[f"{storage.root('vr-1', REV, write_id(RUN))}/{file['path']}"]
        assert put.data == canonical_json(PAYLOAD)
        assert put.data == '{"categories":["Security"],"summary":"s","topics":["VPN","Ünïcode"]}'.encode()
        assert put.headers["content-type"] == "application/json"
        assert outcomes == ["sidecar_written"]

    def test_the_path_names_the_stage_its_version_and_a_token_of_the_write(self) -> None:
        first, second = sidecar_path("entities", 2), sidecar_path("entities", 2)
        assert re.fullmatch(r"enrich/entities\.[0-9a-f]{32}@2\.json", first)
        assert first != second

    async def test_two_entities_jobs_on_one_stored_write_file_two_sidecars(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        """A duplicate delivery of one entities job: both extractions are filed, in the stored write's own set,
        and neither overwrites the other's file."""
        sidecars = _sidecars(storage)
        with _flag(True):
            written = await asyncio.gather(
                sidecars.write("org-1", "vr-1", REV, "entities", 1, {"entities": ["a"]}, stored_run=RUN),
                sidecars.write("org-1", "vr-1", REV, "entities", 1, {"entities": ["b"]}, stored_run=RUN),
            )
        assert written == [True, True]
        root = storage.root("vr-1", REV, write_id(RUN))
        assert len(storage.puts) == 2 and all(key.startswith(f"{root}/enrich/entities.") for key in storage.puts)
        assert sorted(put.data for put in storage.puts.values()) == [b'{"entities":["a"]}', b'{"entities":["b"]}']
        assert outcomes == ["sidecar_written", "sidecar_written"]

    def test_the_classification_leaves_the_named_entity_section_to_its_own_sidecar(self) -> None:
        payload = classification_payload(SemanticMetadata(summary="s", named_entities={"entities": []}))
        assert payload["summary"] == "s" and "named_entities" not in payload


class TestNeverRaises:
    async def test_a_failed_upload_is_a_warning_without_the_url(
        self, storage: FakeStorage, outcomes: list[str], caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = sidecar_path("classification", 1)
        storage.fail_paths.add(path)
        with _flag(True), caplog.at_level(logging.WARNING, logger="sidecars"):
            assert await _sidecars(storage).write_at("org-1", "vr-1", REV, path, PAYLOAD, stored_run=RUN) is False
        assert outcomes == ["sidecar_failed"]
        assert path in caplog.text
        assert SIGNATURE not in caplog.text and "http" not in caplog.text

    async def test_a_refused_presign_is_a_failure(self, storage: FakeStorage, outcomes: list[str]) -> None:
        storage.presign_status = 409
        with _flag(True):
            assert await _sidecars(storage).write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=RUN) is False
        assert outcomes == ["sidecar_failed"]

    @pytest.mark.parametrize(("rev", "name"), [("rev-a", "classification"), (REV, "Not/A/Name")])
    async def test_a_revision_or_name_the_storage_would_refuse_is_not_sent(
        self, storage: FakeStorage, outcomes: list[str], rev: str, name: str,
    ) -> None:
        with _flag(True):
            assert await _sidecars(storage).write("org-1", "vr-1", rev, name, 1, PAYLOAD, stored_run=RUN) is False
        assert storage.presign_calls == [] and outcomes == ["sidecar_failed"]

    async def test_storage_without_the_routes_turns_sidecars_off(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        storage.presign_status = 404
        sidecars = _sidecars(storage)
        with _flag(True):
            assert await sidecars.write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=RUN) is False
            assert await sidecars.write("org-1", "vr-1", REV, "entities", 1, PAYLOAD, stored_run=RUN) is False
        # The latch: the second write never asks.
        assert len(storage.presign_calls) == 1
        assert outcomes == ["sidecar_unavailable", "sidecar_unavailable"]

    async def test_storage_without_a_set_per_write_gets_no_sidecar_until_it_has_one(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        """A sidecar never goes into the revision's shared set; an upgraded storage service is used at the next write."""
        storage.honours_write_id = False
        sidecars = _sidecars(storage)
        with _flag(True):
            assert await sidecars.write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=RUN) is False
            assert storage.puts == {}
            storage.honours_write_id = True
            assert await sidecars.write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=RUN) is True
        [key] = storage.puts
        assert re.fullmatch(rf"{re.escape(storage.root('vr-1', REV, write_id(RUN)))}/enrich/classification\.[0-9a-f]{{32}}@1\.json", key)
        assert outcomes == ["sidecar_unavailable", "sidecar_written"]

    async def test_an_unexpected_error_is_swallowed(self, outcomes: list[str]) -> None:
        store = MagicMock()
        store.presign_revision = AsyncMock(side_effect=RuntimeError("boom"))
        sidecars = ArtifactSidecars(store, MagicMock())
        with _flag(True):
            assert await sidecars.write("org-1", "vr-1", REV, "classification", 1, PAYLOAD, stored_run=RUN) is False
        assert outcomes == ["sidecar_failed"]

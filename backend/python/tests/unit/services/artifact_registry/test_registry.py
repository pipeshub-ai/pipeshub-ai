"""Tests for `app.services.artifact_registry.registry.ArtifactRegistryService`
— the single façade every caller (agent tools, sandbox bridge, history
seeding, sub-agent propagation) goes through."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType, ArtifactVisibility
from app.services.artifact_registry.access import AccessDeniedError, ArtifactNotFoundError
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.artifact_registry.signed_urls import GrantVerificationError

from .fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
OTHER_ORG = "org-2"
USER = "user-1"
OTHER_USER = "user-2"


def _make_service() -> tuple[ArtifactRegistryService, FakeGraphProvider, FakeBlobStore]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    graph.add_user(OTHER_USER, key="ukey-2")
    blob = FakeBlobStore()
    return ArtifactRegistryService(graph, blob), graph, blob


class TestRegisterOutput:
    async def test_first_call_creates_a_new_artifact(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)

        metadata, version = await service.register_output(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"png-bytes", conversation_id="conv-1",
        )

        assert version is None  # no "bump" event for a brand-new artifact
        assert metadata.version == 1
        assert metadata.name == "chart.png"

    async def test_second_call_with_same_logical_name_bumps_version(self) -> None:
        """The `run_code`/`image_generator` re-run path: producing
        `chart.png` again in the SAME conversation must update the
        existing artifact, not create a disconnected duplicate."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)

        first, _ = await service.register_output(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"v1-bytes", conversation_id="conv-1",
        )
        second, version = await service.register_output(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"v2-bytes-longer", conversation_id="conv-1",
        )

        assert second.artifact_id == first.artifact_id
        assert second.version == 2
        assert version is not None and version.version == 2

    async def test_same_logical_name_in_different_conversation_creates_new_artifact(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)

        first, _ = await service.register_output(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"v1", conversation_id="conv-1",
        )
        second, version = await service.register_output(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"v1", conversation_id="conv-2",
        )

        assert second.artifact_id != first.artifact_id
        assert version is None

    async def test_content_over_max_bytes_is_rejected(self) -> None:
        service = ArtifactRegistryService(FakeGraphProvider(), FakeBlobStore(), max_bytes=10)
        with pytest.raises(ValueError):
            await service.register_output(
                actor=Actor(org_id=ORG, user_id=USER), name="big.bin", artifact_type=ArtifactType.OTHER,
                mime_type="application/octet-stream", content=b"x" * 11, conversation_id="conv-1",
            )


class TestRegisterOutputVisibilityBump:
    """`register_output`'s version-bump path must reconcile visibility —
    monotonic `STAGING` -> `VISIBLE` promotion, never a demotion — since
    `add_version()` itself never touches visibility (see
    `ArtifactRegistryService._reconcile_visibility`'s docstring)."""

    async def test_bump_with_visible_promotes_an_existing_staging_doc(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created, _ = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
            visibility=ArtifactVisibility.STAGING,
        )
        assert created.visibility == ArtifactVisibility.STAGING

        bumped, version = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v2-different", conversation_id="conv-1",
            visibility=ArtifactVisibility.VISIBLE,
        )

        assert bumped.artifact_id == created.artifact_id
        assert version is not None and version.version == 2
        assert bumped.visibility == ArtifactVisibility.VISIBLE

    async def test_bump_with_staging_never_demotes_an_existing_visible_doc(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created, _ = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )
        assert created.visibility == ArtifactVisibility.VISIBLE

        bumped, _ = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v2-different", conversation_id="conv-1",
            visibility=ArtifactVisibility.STAGING,
        )

        assert bumped.visibility == ArtifactVisibility.VISIBLE

    async def test_content_hash_dedup_still_promotes_visibility(self) -> None:
        """A re-run producing byte-identical content takes `add_version`'s
        early-return dedup path (no new version) — visibility reconciliation
        must still apply on that path, not just the "real bump" path."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created, _ = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"same-bytes", conversation_id="conv-1",
            visibility=ArtifactVisibility.STAGING,
        )
        assert created.visibility == ArtifactVisibility.STAGING

        bumped, version = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"same-bytes", conversation_id="conv-1",
            visibility=ArtifactVisibility.VISIBLE,
        )

        assert version is not None and version.deduplicated is True
        assert bumped.version == 1
        assert bumped.visibility == ArtifactVisibility.VISIBLE

    async def test_content_hash_dedup_does_not_demote_visible_doc(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created, _ = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"same-bytes", conversation_id="conv-1",
        )
        assert created.visibility == ArtifactVisibility.VISIBLE

        bumped, version = await service.register_output(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"same-bytes", conversation_id="conv-1",
            visibility=ArtifactVisibility.STAGING,
        )

        assert version is not None and version.deduplicated is True
        assert bumped.visibility == ArtifactVisibility.VISIBLE


class TestVersionRaces:
    """Covers the plan's "Version races" hardening item: `register_output`
    must not double-bump (or duplicate) an artifact when two callers race
    each other."""

    async def test_bump_bump_race_retries_once_then_succeeds(self) -> None:
        """A concurrent writer bumps the artifact between our
        `resolve_by_logical_name` and our `add_version` call — our first
        `add_version` attempt sees a stale `expected_version` and must
        transparently re-resolve (picking up the racer's bump) and retry
        rather than raising or silently overwriting."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"v1", conversation_id="conv-1",
        )
        # Simulate a racer's bump landing first, while we're still holding
        # a stale `existing` snapshot from before it happened.
        await service.add_version(actor=actor, artifact_id=created.artifact_id, content=b"racer-v2")

        real_resolve_by_logical_name = service.resolve_by_logical_name

        async def _stale_resolve_by_logical_name(actor, name, *, conversation_id):
            result = await real_resolve_by_logical_name(actor, name, conversation_id=conversation_id)
            return result.model_copy(update={"version": 1}) if result is not None else None

        with patch.object(service, "resolve_by_logical_name", side_effect=_stale_resolve_by_logical_name):
            metadata, version = await service.register_output(
                actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
                mime_type="image/png", content=b"v3-bytes", conversation_id="conv-1",
            )

        assert metadata.artifact_id == created.artifact_id
        assert metadata.version == 3  # racer's bump (v2) + our retried bump (v3)
        assert version is not None and version.version == 3

    async def test_bump_bump_race_exhausts_retry_and_raises(self) -> None:
        """If `expected_version` keeps being stale past the bounded retry
        budget, the conflict must surface rather than looping forever or
        silently overwriting."""
        from app.services.artifact_registry.versioning import VersionConflictError

        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        await service.register(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"v1", conversation_id="conv-1",
        )

        real_resolve_by_logical_name = service.resolve_by_logical_name

        async def _always_stale_resolve_by_logical_name(actor, name, *, conversation_id):
            result = await real_resolve_by_logical_name(actor, name, conversation_id=conversation_id)
            return result.model_copy(update={"version": 999}) if result is not None else None

        async def _always_stale_resolve(*, actor, ref, conversation_id=None):
            metadata = await ArtifactRegistryService.resolve(
                service, actor=actor, ref=ref, conversation_id=conversation_id,
            )
            return metadata.model_copy(update={"version": 999})

        with patch.object(service, "resolve_by_logical_name", side_effect=_always_stale_resolve_by_logical_name), \
             patch.object(service, "resolve", side_effect=_always_stale_resolve), \
             pytest.raises(VersionConflictError):
            await service.register_output(
                actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
                mime_type="image/png", content=b"v2-bytes", conversation_id="conv-1",
            )

    async def test_create_create_race_folds_loser_into_winner(self) -> None:
        """Two concurrent `register_output` calls for a brand-new logical
        name both see `existing is None` and both `create()`. Simulate the
        race by making a second "winner" artifact appear (via a real
        `register()` call) strictly between our own pre-create lookup
        (which must see nothing, like the real race) and our post-create
        reconcile lookup — then assert our content is folded into the
        winner as a new version and our own record is marked deleted."""
        service, graph, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)

        real_resolve_by_logical_name = service.resolve_by_logical_name
        winner_holder: dict[str, Any] = {}

        async def _resolve_with_delayed_winner(actor, name, *, conversation_id):
            if "winner" not in winner_holder:
                # First call is register_output's pre-create check — both
                # racers must see `existing is None` here.
                winner_holder["winner"] = await service.register(
                    actor=actor, name=name, artifact_type=ArtifactType.OTHER,
                    mime_type="application/pdf", content=b"winner-v1", conversation_id=conversation_id,
                )
                return None
            return await real_resolve_by_logical_name(actor, name, conversation_id=conversation_id)

        with patch.object(service, "resolve_by_logical_name", side_effect=_resolve_with_delayed_winner):
            metadata, version = await service.register_output(
                actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
                mime_type="application/pdf", content=b"loser-bytes", conversation_id="conv-1",
            )

        winner = winner_holder["winner"]
        assert metadata.artifact_id == winner.artifact_id
        assert metadata.version == 2
        assert version is not None and version.version == 2

        loser_records = [
            doc for doc in graph.nodes["records"].values()
            if doc.get("_key") != winner.artifact_id and doc.get("recordName") == "report.pdf"
        ]
        assert len(loser_records) == 1
        assert loser_records[0]["isDeleted"] is True

    async def test_resolve_by_logical_name_skips_deleted_race_losers(self) -> None:
        """Direct unit test of the mitigation in `resolve_by_logical_name`:
        even if a soft-deleted duplicate is returned by the underlying
        paginated query, the live artifact must still be resolved."""
        service, graph, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        winner = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"winner", conversation_id="conv-1",
        )
        loser = await service.register(
            actor=actor, name="report-loser.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"loser", conversation_id="conv-1",
        )
        # Force the loser to share the winner's logicalName (as a real
        # create-create race would) and mark it deleted, as
        # `_reconcile_create_race` does.
        graph.nodes["artifacts"][loser.artifact_id]["logicalName"] = "report.pdf"
        graph.nodes["records"][loser.artifact_id]["isDeleted"] = True

        resolved = await service.resolve_by_logical_name(actor, "report.pdf", conversation_id="conv-1")

        assert resolved is not None
        assert resolved.artifact_id == winner.artifact_id


class TestResolveAndPermissions:
    async def test_resolve_by_artifact_id(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"x", conversation_id="conv-1",
        )
        resolved = await service.resolve(actor=actor, ref=created.artifact_id)
        assert resolved.artifact_id == created.artifact_id

    async def test_resolve_by_logical_name_within_conversation(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"x", conversation_id="conv-1",
        )
        resolved = await service.resolve(actor=actor, ref="report.pdf", conversation_id="conv-1")
        assert resolved.artifact_id == created.artifact_id

    async def test_other_user_in_same_org_without_permission_edge_is_denied(self) -> None:
        service, _, _ = _make_service()
        owner = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=owner, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"x", conversation_id="conv-1",
        )
        stranger = Actor(org_id=ORG, user_id=OTHER_USER)
        with pytest.raises(AccessDeniedError):
            await service.resolve(actor=stranger, ref=created.artifact_id)

    async def test_cross_org_access_is_not_found_not_denied(self) -> None:
        service, _, _ = _make_service()
        owner = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=owner, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"x", conversation_id="conv-1",
        )
        outsider = Actor(org_id=OTHER_ORG, user_id=USER)
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=outsider, ref=created.artifact_id)

    async def test_unknown_logical_name_raises_not_found(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=actor, ref="ghost.pdf", conversation_id="conv-1")

    async def test_ref_without_extension_fuzzy_matches_registered_extension(self) -> None:
        """Regression: a model recalling an artifact from an earlier tool
        response's prose often drops the file extension (e.g.
        `taj_mahal_world_wonder` for an artifact actually registered as
        `taj_mahal_world_wonder.png`)."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="taj_mahal_world_wonder.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"png", conversation_id="conv-1",
        )

        resolved = await service.resolve(
            actor=actor, ref="taj_mahal_world_wonder", conversation_id="conv-1",
        )

        assert resolved.artifact_id == created.artifact_id

    async def test_exact_match_wins_over_fuzzy_match(self) -> None:
        """A same-stem artifact WITHOUT an extension, if it happens to
        exist, must never be shadowed by the fuzzy fallback — exact match
        is tried first and always wins."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        exact = await service.register(
            actor=actor, name="report", artifact_type=ArtifactType.OTHER,
            mime_type="application/octet-stream", content=b"exact", conversation_id="conv-1",
        )
        await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"fuzzy", conversation_id="conv-1",
        )

        resolved = await service.resolve(actor=actor, ref="report", conversation_id="conv-1")

        assert resolved.artifact_id == exact.artifact_id

    async def test_ref_with_wrong_extension_fuzzy_matches_bare_stem(self) -> None:
        """The model guesses the wrong extension (or none was ever
        registered) — falls back to the bare stem."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="notes", artifact_type=ArtifactType.OTHER,
            mime_type="text/plain", content=b"notes", conversation_id="conv-1",
        )

        resolved = await service.resolve(actor=actor, ref="notes.txt", conversation_id="conv-1")

        assert resolved.artifact_id == created.artifact_id

    async def test_unknown_name_with_no_fuzzy_match_still_raises_not_found(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=actor, ref="totally_unknown", conversation_id="conv-1")

    async def test_fuzzy_match_still_enforces_conversation_scoping(self) -> None:
        """The fuzzy fallback must not widen the authorization surface —
        an artifact in a DIFFERENT conversation is still invisible even
        with a matching fuzzy extension."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        await service.register(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"png", conversation_id="conv-other",
        )
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=actor, ref="chart", conversation_id="conv-1")


class TestListForConversation:
    async def test_lists_only_artifacts_in_that_conversation(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        await service.register(
            actor=actor, name="a.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"a", conversation_id="conv-1",
        )
        await service.register(
            actor=actor, name="b.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"b", conversation_id="conv-2",
        )

        results = await service.list_for_conversation(actor=actor, conversation_id="conv-1")
        assert [r.name for r in results] == ["a.pdf"]

    async def test_visibility_filter_keeps_artifacts_written_before_the_field_existed(self) -> None:
        """An artifact doc predating `visibility` has no such field, so an
        AQL `doc.visibility == "VISIBLE"` would drop it — and the reminder
        hook would stop showing a conversation's older deliverables."""
        service, graph, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        legacy = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"pdf", conversation_id="conv-1",
        )
        graph.nodes[CollectionNames.ARTIFACTS.value][legacy.artifact_id].pop("visibility")

        results = await service.list_for_conversation(
            actor=actor, conversation_id="conv-1", visibility_filter=ArtifactVisibility.VISIBLE,
        )
        assert [r.name for r in results] == ["report.pdf"]

    async def test_visibility_filter_still_excludes_staging(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        await service.register(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"png", conversation_id="conv-1",
        )
        await service.register(
            actor=actor, name="code_abc.py", artifact_type=ArtifactType.CODE,
            mime_type="text/x-python", content=b"print(1)", conversation_id="conv-1",
            visibility=ArtifactVisibility.STAGING,
        )

        results = await service.list_for_conversation(
            actor=actor, conversation_id="conv-1", visibility_filter=ArtifactVisibility.VISIBLE,
        )
        assert [r.name for r in results] == ["chart.png"]

    async def test_includes_lineage_when_derived_from_code(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        code = await service.register(
            actor=actor, name="script.py", artifact_type=ArtifactType.CODE,
            mime_type="text/x-python", content=b"print(1)", conversation_id="conv-1",
        )
        output = await service.register(
            actor=actor, name="chart.png", artifact_type=ArtifactType.IMAGE,
            mime_type="image/png", content=b"png", conversation_id="conv-1",
        )
        await service.record_derivation(
            output_artifact_id=output.artifact_id, code_artifact_id=code.artifact_id,
            code_version=1, output_version=1,
        )

        results = await service.list_for_conversation(actor=actor, conversation_id="conv-1")
        chart = next(r for r in results if r.name == "chart.png")
        assert chart.derived_from_code_artifact_id == code.artifact_id
        assert chart.derived_from_code_version == 1


class TestVersionPinnedRetrieval:
    """Covers the plan's suggested test #1: register v1, bump to v2,
    `get_content(version=1)` must return v1's bytes and `version=2`/latest
    must return v2's — the lazy-v0 storage mapping backfilled by
    `VersionManager.add_version` must resolve correctly for BOTH."""

    async def test_version_1_and_2_resolve_to_their_own_bytes(self) -> None:
        service, _, blob = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1-bytes", conversation_id="conv-1",
        )
        await service.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v2-bytes-longer")

        async def _fake_fetch(*, org_id, config_service, storage_document_id, version=None, **_kw):
            return await blob.get_buffer(org_id, storage_document_id, version)

        with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=_fake_fetch):
            v1_content = await service.get_content(actor=actor, artifact_id=created.artifact_id, version=1)
            v2_content = await service.get_content(actor=actor, artifact_id=created.artifact_id, version=2)
            latest_content = await service.get_content(actor=actor, artifact_id=created.artifact_id)

        assert v1_content == b"v1-bytes"
        assert v2_content == b"v2-bytes-longer"
        assert latest_content == b"v2-bytes-longer"

    async def test_unmapped_non_current_version_raises_not_found(self) -> None:
        """No silent wrong-bytes fallback: a version with no bookkeeping
        entry (e.g. pre-migration data) must 404, not quietly serve the
        wrong content."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1-bytes", conversation_id="conv-1",
        )
        with pytest.raises(ArtifactNotFoundError):
            await service.get_content(actor=actor, artifact_id=created.artifact_id, version=5)

    async def test_lost_bookkeeping_still_serves_the_right_older_bytes(self) -> None:
        """The failure this exists for: a bump's blob write lands but its
        graph write does not, wiping the mapping — every card already on
        screen pinning an older version 404s. Recovery must serve v1's
        ACTUAL bytes, not the latest content under a v1 label."""
        service, graph, blob = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1-bytes", conversation_id="conv-1",
        )
        await service.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v2-bytes-longer")
        await service.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v3-bytes-longest")
        graph.nodes[CollectionNames.ARTIFACTS.value][created.artifact_id]["versions"] = None

        async def _fake_fetch(*, org_id, config_service, storage_document_id, version=None, **_kw):
            return await blob.get_buffer(org_id, storage_document_id, version)

        with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=_fake_fetch):
            v1_content = await service.get_content(actor=actor, artifact_id=created.artifact_id, version=1)
            v2_content = await service.get_content(actor=actor, artifact_id=created.artifact_id, version=2)

        assert v1_content == b"v1-bytes"
        assert v2_content == b"v2-bytes-longer"

    async def test_get_download_url_pins_storage_version(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1-bytes", conversation_id="conv-1",
        )
        await service.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v2-bytes-longer")

        v1_url = await service.get_download_url(actor=actor, artifact_id=created.artifact_id, version=1)
        latest_url = await service.get_download_url(actor=actor, artifact_id=created.artifact_id)

        assert "version=0" in v1_url
        assert "version=" not in latest_url


class TestTwoPhaseUpload:
    async def test_commit_version_verifies_content_before_bumping(self) -> None:
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )
        content = b"v2-bytes"
        from app.services.artifact_registry.versioning import compute_content_hash

        grant = await service.get_upload_grant(
            actor=actor, artifact_id=created.artifact_id, declared_size=len(content),
            declared_sha256=compute_content_hash(content), mime_type="application/pdf",
        )

        with patch(
            "app.agents.actions.util.blob_staging.fetch_blob_bytes",
            new=AsyncMock(return_value=content),
        ):
            version, metadata = await service.commit_version(actor=actor, grant_id=grant.grant_id)

        assert version.version == 2
        assert metadata.version == 2

    async def test_commit_version_rejects_content_mismatch(self) -> None:
        """A compromised/buggy uploader that PUTs different bytes than it
        declared must never have its version committed — the two-phase
        flow's entire point."""
        service, _, _ = _make_service()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )
        from app.services.artifact_registry.versioning import compute_content_hash

        grant = await service.get_upload_grant(
            actor=actor, artifact_id=created.artifact_id, declared_size=8,
            declared_sha256=compute_content_hash(b"declared"), mime_type="application/pdf",
        )

        with patch(
            "app.agents.actions.util.blob_staging.fetch_blob_bytes",
            new=AsyncMock(return_value=b"actually-different-bytes"),
        ), pytest.raises(GrantVerificationError):
            await service.commit_version(actor=actor, grant_id=grant.grant_id)

    async def test_get_upload_grant_requires_write_authorization(self) -> None:
        service, _, _ = _make_service()
        owner = Actor(org_id=ORG, user_id=USER)
        created = await service.register(
            actor=owner, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )
        stranger = Actor(org_id=ORG, user_id=OTHER_USER)
        with pytest.raises(AccessDeniedError):
            await service.get_upload_grant(
                actor=stranger, artifact_id=created.artifact_id, declared_size=1,
                declared_sha256="x", mime_type="application/pdf",
            )

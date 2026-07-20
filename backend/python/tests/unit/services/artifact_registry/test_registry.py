"""Tests for `app.services.artifact_registry.registry.ArtifactRegistryService`
— the single façade every caller (agent tools, sandbox bridge, history
seeding, sub-agent propagation) goes through."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.entities import ArtifactType
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

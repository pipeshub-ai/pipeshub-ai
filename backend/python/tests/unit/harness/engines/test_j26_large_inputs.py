"""J26-1..J26-7 (J-26, C5.01, WP-69): how large an input a run takes.

The deployment sets it (``HARNESS_INPUT_MAX_MB``, 100 MiB unless the operator says otherwise; anything but a
positive whole number refuses to start). The artifact registry keeps files up to the same size, so any artifact it
holds fits as an input. An org's ``sandbox.inputMaxMb`` lowers it for its runs and never raises it. Inputs over
8 MiB reach the sandbox on their first read (C5.01), so a large one costs nothing until the engine reads it.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.control_plane.runtime.deployment import DeploymentLayer
from app.harness.engines.inputs import RegistryInputSource, run_input_source
from app.harness.run_policy.pinned import PinnedRunPolicy
from app.harness.run_policy.resolved import RELEASE_TUNING, resolved_tuning
from app.models.entities import ArtifactType
from app.services.artifact_registry.limits import (
    DEFAULT_INPUT_MAX_MB,
    INPUT_MAX_ENV,
    InvalidInputMax,
    effective_input_max_bytes,
    input_max_bytes,
)
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from harness_core.engines.errors import EngineInputTooLarge
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import InputArtifactRef
from harness_core.logs import HarnessLog
from harness_core.policy import ScopedLayer
from harness_core.workspace.forks import session_scope
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from tests.unit.harness.test_c4_10_run_tuning import _pin
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext

MIB = 1024 * 1024
TENANT, SESSION, USER = "org-j26", "ses-j26", "user-j26"


def test_j26_1_the_deployment_takes_inputs_up_to_100_mib_unless_the_operator_says_otherwise() -> None:
    assert DEFAULT_INPUT_MAX_MB == 100
    assert input_max_bytes({}) == 100 * MIB
    assert input_max_bytes({INPUT_MAX_ENV: "  "}) == 100 * MIB
    assert input_max_bytes({INPUT_MAX_ENV: "512"}) == 512 * MIB
    assert input_max_bytes({INPUT_MAX_ENV: " 40 "}) == 40 * MIB


@pytest.mark.parametrize("raw", ["0", "-5", "lots", "1.5", "12x", "1e3"])
def test_j26_2_a_value_that_is_not_a_positive_whole_number_refuses_to_start(raw: str) -> None:
    with pytest.raises(InvalidInputMax, match=INPUT_MAX_ENV):
        input_max_bytes({INPUT_MAX_ENV: raw})


def test_j26_3_the_registry_keeps_files_as_large_as_the_deployment_takes_as_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INPUT_MAX_ENV, raising=False)
    assert ArtifactRegistryService(FakeGraphProvider(), FakeBlobStore()).max_bytes == 100 * MIB
    monkeypatch.setenv(INPUT_MAX_ENV, "40")
    assert ArtifactRegistryService(FakeGraphProvider(), FakeBlobStore()).max_bytes == 40 * MIB
    # A caller that names its own cap keeps it.
    assert ArtifactRegistryService(FakeGraphProvider(), FakeBlobStore(), max_bytes=4096).max_bytes == 4096


def test_j26_4_an_orgs_setting_lowers_the_deployments_cap_and_never_raises_it() -> None:
    deployment = 100 * MIB
    assert effective_input_max_bytes(deployment, None) == deployment
    assert effective_input_max_bytes(deployment, 50) == 50 * MIB
    assert effective_input_max_bytes(deployment, 500) == deployment
    assert effective_input_max_bytes(40 * MIB, 50) == 40 * MIB


def _tuning(*layers: ScopedLayer) -> object:
    run = cast("RunContext", SimpleNamespace(org_id="o1", agent=None))
    policy = PinnedRunPolicy(run, _pin(list(layers)), deployment=DeploymentLayer(), live=None, log=HarnessLog.named("t"))
    return policy.tuning()


def test_j26_5_the_run_pin_carries_the_orgs_input_cap() -> None:
    assert RELEASE_TUNING.input_max_mb is None
    assert _tuning() == RELEASE_TUNING
    tuning = _tuning(ScopedLayer("org", {"sandbox": {"inputMaxMb": 50}}))
    assert tuning.input_max_mb == 50  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", [None, 0, -1, True, "50"])
def test_j26_5_an_unusable_pinned_value_keeps_the_deployments(value: object) -> None:
    tuning = resolved_tuning({"sandbox.inputMaxMb": {"value": value, "setBy": "org", "ceilings": []}})  # type: ignore[dict-item]
    assert tuning.input_max_mb is None


class _World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.graph = FakeGraphProvider()
        self.graph.add_user(USER, key="ukey-j26")
        self.blob = FakeBlobStore()
        self.registry = ArtifactRegistryService(self.graph, self.blob)
        self.actor = Actor(org_id=TENANT, user_id=USER)
        self.objects = InMemoryBinaryObjectStore()
        self._names = itertools.count(1)

        async def fetch(*, org_id: str, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
            return await self.blob.get_buffer(org_id, storage_document_id, version)

        monkeypatch.setattr("app.agents.actions.util.blob_staging.fetch_blob_bytes", fetch)

    async def add(self, name: str, content: bytes) -> str:
        metadata, _ = await self.registry.register_output(
            actor=self.actor, name=name, artifact_type=ArtifactType.DOCUMENT, mime_type="application/pdf",
            content=content, conversation_id=SESSION, artifact_id=f"art-j26-{next(self._names)}",
        )
        return metadata.artifact_id

    def source(self, *, deployment: int, pinned_mb: int | None) -> RegistryInputSource:
        return run_input_source(
            self.registry, self.actor, self.objects,
            object_scope=lambda b: session_scope(b.tenant, b.scope),
            deployment_max_bytes=deployment, pinned_max_mb=pinned_mb,
        )


BINDING = RunBinding(tenant=TENANT, scope=SESSION, run_id="run-j26")


async def test_j26_6_a_50_mb_pdf_is_an_input_with_the_release_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The journey's file: past the old 25 MiB cap, it is kept as an artifact and resolved as the run's input."""
    monkeypatch.delenv(INPUT_MAX_ENV, raising=False)
    world = _World(monkeypatch)
    pdf = b"%PDF-1.7\n" + b"0" * (50 * 1000 * 1000)
    artifact = await world.add("Annual report.pdf", pdf)
    (taken,) = await world.source(deployment=input_max_bytes(), pinned_mb=None).resolve(
        BINDING, [InputArtifactRef(artifact)]
    )
    assert (taken.name, taken.content.size_bytes) == ("Annual_report.pdf", len(pdf))
    assert await world.objects.get(session_scope(TENANT, SESSION), taken.content) == pdf


async def test_j26_7_the_run_refuses_an_input_over_its_orgs_cap_before_reading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INPUT_MAX_ENV, raising=False)
    world = _World(monkeypatch)
    artifact = await world.add("scan.pdf", b"%PDF-1.7\n" + b"0" * (3 * MIB))
    with pytest.raises(EngineInputTooLarge) as refused:
        await world.source(deployment=100 * MIB, pinned_mb=2).resolve(BINDING, [InputArtifactRef(artifact)])
    assert refused.value.max_bytes == 2 * MIB

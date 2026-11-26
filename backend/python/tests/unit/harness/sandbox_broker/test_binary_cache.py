"""EH2-8..EH2-12 (EH2, SBX-31, SBX-33, SBX-34): the Docker broker verifies an engine image's binary once per
image id, vouches for it on every sandbox of that id, and refuses every sandbox of an id that does not match."""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path
from typing import Any

import docker.types
import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.engines.binary import PinnedBinary
from harness_core.engines.types import EngineVersion, is_acp_kind
from harness_core.ids import RandomIds
from harness_core.sandbox.errors import ImageNotAllowed
from harness_core.sandbox.pool import WarmPoolConfig, shape_of
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.binary import InMemoryImageBinaries, sha256_digest
from harness_engines.release import (
    BINARY_PATHS,
    CANDIDATE_LABEL,
    release_allowlist,
    release_binary_pins,
)

from .test_docker_broker import CAPS

APP = Path(__file__).resolve().parents[4] / "app"
LEASE = LeaseRef("w", 1)
TAG = "pxh-codex:0.160.0"
PATH = "/usr/local/bin/codex-app-server"
REAL = b"the allow-listed codex binary"
ENGINE = EngineVersion("codex", "0.160.0", sha256_digest(REAL))
GOOD_ID = "sha256:" + "1" * 64
BAD_ID = "sha256:" + "2" * 64
NAMESPACE = "io.pipeshub.eh2unit"


class _Api:
    """A daemon that creates, starts and removes containers, and knows which image id each was created from."""

    api_version = "1.48"

    def __init__(self, image_ids: dict[str, str]) -> None:
        self.image_ids = image_ids
        self.created: dict[str, dict[str, Any]] = {}
        self.started: list[str] = []
        self.removed: list[str] = []

    def create_host_config(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return dict(docker.types.HostConfig("1.48", **kwargs))

    def create_volume(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {}

    def volumes(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"Volumes": []}

    def remove_volume(self, *_: Any, **__: Any) -> None:  # noqa: ANN401
        return None

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        container_id = uuid.uuid4().hex
        labels = dict(kwargs.get("labels") or {})
        self.created[container_id] = {"Id": container_id, "ImageID": self.image_ids[image], "Labels": labels, "State": "created"}
        return {"Id": container_id}

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        return {"Id": container_id, "Image": self.created[container_id]["ImageID"]}

    def start(self, container_id: str) -> None:
        self.started.append(container_id)
        self.created[container_id]["State"] = "running"

    def remove_container(self, container_id: str, **_: Any) -> None:  # noqa: ANN401
        self.removed.append(container_id)

    def containers(self, *, filters: dict[str, list[str]], **_: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        wanted = [f.split("=", 1) for f in filters.get("label", [])]
        return [
            info
            for cid, info in self.created.items()
            if cid not in self.removed and all(info["Labels"].get(k) == v for k, v in wanted)
        ]


class _Provider:
    def __init__(self, image_ids: dict[str, str], labels: dict[str, str] | None = None) -> None:
        self.api = _Api(image_ids)
        self.client = type("Client", (), {"api": self.api})()
        self.labels = labels or {}

    async def run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return fn(*args, **kwargs)

    async def ensure_image(self, _: str) -> bool:
        return True

    async def image_labels(self, _: str) -> dict[str, str]:
        return dict(self.labels)


def _reader() -> InMemoryImageBinaries:
    return InMemoryImageBinaries({GOOD_ID: {PATH: REAL}, BAD_ID: {PATH: b"an impostor"}})


def _broker(provider: _Provider, reader: InMemoryImageBinaries, **kwargs: Any) -> DockerSandboxBroker:  # noqa: ANN401
    return DockerSandboxBroker(
        provider=provider,  # type: ignore[arg-type]
        clock=ManualClock(),
        ids=RandomIds(),
        capabilities=CAPS,
        label_namespace=NAMESPACE,
        binary_pins={TAG: (PinnedBinary(ENGINE, PATH),)},
        binary_reader=reader,
        **kwargs,
    )


def _spec(**overrides: Any) -> SandboxSpec:  # noqa: ANN401
    fields: dict[str, Any] = {
        "image_digest": TAG,
        "mode": RuntimeMode.DOCKER_HARDENED,
        "mounts": (MountSpec("/workspace"), MountSpec("/state")),
        "labels": {"runId": "r-eh2"},
    }
    fields.update(overrides)
    return SandboxSpec(**fields)


async def test_eh2_8_the_broker_vouches_for_the_binary_of_the_image_id_it_created_from() -> None:
    provider, reader = _Provider({TAG: GOOD_ID}), _reader()
    broker = _broker(provider, reader)
    first = await broker.acquire(_spec(), lease=LEASE, idempotency_key="a")
    second = await broker.acquire(_spec(), lease=LEASE, idempotency_key="b")
    assert dict(first.verified_binaries) == {PATH: ENGINE.binary_digest}
    assert dict(second.verified_binaries) == {PATH: ENGINE.binary_digest}
    assert reader.reads == [(GOOD_ID, PATH)], "read by tag, or read twice"


async def test_eh2_8_a_spec_that_mounts_over_the_binary_is_not_vouched_for() -> None:
    broker = _broker(_Provider({TAG: GOOD_ID}), _reader())
    handle = await broker.acquire(_spec(mounts=(MountSpec("/usr/local/bin"),)), lease=LEASE, idempotency_key="a")
    assert dict(handle.verified_binaries) == {}


async def test_eh2_8_an_unpinned_image_is_never_read() -> None:
    reader = _reader()
    broker = _broker(_Provider({"pxh-sandbox:pr1": GOOD_ID}), reader)
    handle = await broker.acquire(_spec(image_digest="pxh-sandbox:pr1"), lease=LEASE, idempotency_key="a")
    assert dict(handle.verified_binaries) == {}
    assert reader.reads == []


async def test_eh2_9_a_mismatching_image_is_refused_every_time_and_never_started(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.harness.sandbox.broker")
    provider, reader = _Provider({TAG: BAD_ID}), _reader()
    broker = _broker(provider, reader)
    for key in ("a", "b"):
        with pytest.raises(ImageNotAllowed) as refused:
            await broker.acquire(_spec(), lease=LEASE, idempotency_key=key)
        assert refused.value.reason == "binary_mismatch"
    assert provider.api.started == []
    assert sorted(provider.api.removed) == sorted(provider.api.created)
    assert reader.reads == [(BAD_ID, PATH)]
    refusals = [r.getMessage() for r in caplog.records if r.getMessage().startswith("sandbox.refused")]
    assert len(refusals) == 2 and all("reason=binary_mismatch" in m and "runId=r-eh2" in m for m in refusals)
    mismatches = [r.getMessage() for r in caplog.records if r.getMessage().startswith("engine.binary.mismatch")]
    assert len(mismatches) == 2 and all("runId=r-eh2" in m and f"imageId={BAD_ID}" in m for m in mismatches)


async def test_eh2_9_a_mismatching_image_is_never_made_a_warm_sandbox() -> None:
    provider = _Provider({TAG: BAD_ID})
    shape = shape_of(_spec(labels={}))
    assert shape is not None
    with pytest.raises(ImageNotAllowed) as refused:
        await _broker(provider, _reader()).create_warm(shape, {})
    assert refused.value.reason == "binary_mismatch"
    assert provider.api.started == []
    assert sorted(provider.api.removed) == sorted(provider.api.created)


async def test_eh2_10_a_warm_sandbox_is_vouched_for_from_its_own_image_id() -> None:
    provider, reader = _Provider({TAG: GOOD_ID}), _reader()
    broker = _broker(provider, reader, warm_pool=WarmPoolConfig(ceiling=1))
    shape = shape_of(_spec(labels={}))
    assert shape is not None
    warm = await broker.create_warm(shape, {})
    provider.api.image_ids[TAG] = BAD_ID
    bound = await broker._bind_warm(warm, _spec(), "k", LEASE, proxied=False)  # pyright: ignore[reportPrivateUsage]
    assert bound.binaries == {PATH: ENGINE.binary_digest}
    assert reader.reads == [(GOOD_ID, PATH)]


async def test_eh2_10_an_adopted_sandbox_is_vouched_for_from_its_own_image_id() -> None:
    provider = _Provider({TAG: GOOD_ID})
    first = _broker(provider, _reader())
    handle = await first.acquire(_spec(), lease=LEASE, idempotency_key="k")
    restarted = _broker(provider, reader := _reader())
    adopted = await restarted.acquire(_spec(), lease=LeaseRef("w", 2), idempotency_key="k")
    assert adopted.id == handle.id
    assert dict(adopted.verified_binaries) == {PATH: ENGINE.binary_digest}
    assert reader.reads == [(GOOD_ID, PATH)]


async def test_eh2_10_an_adopted_sandbox_of_a_mismatching_image_is_refused_and_removed() -> None:
    provider = _Provider({TAG: GOOD_ID})
    handle = await _broker(provider, _reader()).acquire(_spec(), lease=LEASE, idempotency_key="k")
    container = handle.container_id
    provider.api.created[container]["ImageID"] = BAD_ID
    with pytest.raises(ImageNotAllowed):
        await _broker(provider, _reader()).acquire(_spec(), lease=LeaseRef("w", 2), idempotency_key="k")
    assert container in provider.api.removed


async def test_eh2_11_a_candidate_image_is_still_refused_before_anything_is_read() -> None:
    provider, reader = _Provider({TAG: GOOD_ID}, labels={CANDIDATE_LABEL: "true"}), _reader()
    with pytest.raises(ImageNotAllowed) as refused:
        await _broker(provider, reader).acquire(_spec(), lease=LEASE, idempotency_key="a")
    assert refused.value.reason == "candidate_image"
    assert reader.reads == [] and provider.api.created == {}


def test_eh2_12_the_release_pins_every_image_of_every_engine() -> None:
    pins = release_binary_pins()
    for release in release_allowlist():
        if is_acp_kind(release.version.kind):
            continue
        for image in {release.image, *release.variants.values()}:
            assert PinnedBinary(release.version, BINARY_PATHS[release.version.kind]) in pins[image], image


def test_eh2_12_an_acp_engine_is_pinned_only_once_its_profile_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ACP-driver: the profile names the binary; without an installed one nothing is vouched for under the ACP kind."""
    from harness_engines import release as release_module
    from harness_engines.acp.registry import packaged_acp_agents

    acp = next(r for r in release_allowlist() if is_acp_kind(r.version.kind))
    monkeypatch.setattr(release_module, "acp_profiles", lambda env=None: ())
    assert all(p.engine != acp.version for p in release_binary_pins()[acp.image])
    monkeypatch.setattr(release_module, "acp_profiles", lambda env=None: packaged_acp_agents(unsigned_ok=True))
    assert PinnedBinary(acp.version, "/usr/local/bin/opencode") in release_binary_pins()[acp.image]


def test_eh2_12_the_production_broker_is_built_with_the_release_pins() -> None:
    tree = ast.parse((APP / "sandbox_broker_main.py").read_text(encoding="utf-8"))
    [create] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "DockerSandboxBroker"
    ]
    [pins] = [k.value for k in create.keywords if k.arg == "binary_pins"]
    assert isinstance(pins, ast.Call) and isinstance(pins.func, ast.Name) and pins.func.id == "release_binary_pins"
    assert pins.args == [] and pins.keywords == []

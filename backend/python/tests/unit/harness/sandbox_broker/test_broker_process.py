"""RB-7 and the process role: the worker credential guards the broker API end to end."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestServer

from app.harness.journal_node.credentials import ConfiguredWorkerCredential
from app.harness.sandbox.auth import WorkerCredentialBrokerAuth
from app.harness.sandbox.client import broker_endpoint
from app.harness.sandbox.docker_broker import DockerCapabilities
from app.harness.sandbox.docker_egress import DockerEgressWiring
from app.harness.sandbox.egress_config import ConfiguredEgressBindings
from app.sandbox_broker_main import create_service
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.errors import BrokerUnauthorized, PolicyUnenforceable
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import LEASE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeConfig:
    def __init__(self, documents: dict[str, Any]) -> None:
        self.documents = documents

    async def get_config(self, key: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        return self.documents.get(key)


CREDENTIAL_PATH = "/services/agentHarness/workerCredential"


@pytest.fixture(autouse=True)
def _install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose and Helm always name the install; a broker without one refuses to start (CLEANUP-G6)."""
    monkeypatch.setenv("SANDBOX_INSTALL_ID", "test-install")


@pytest.mark.parametrize(
    ("install", "project", "expected"),
    [("ours", "proj", "ours"), (None, "proj", "proj"), ("default", None, "default"), (" ", "proj", "proj")],
)
def test_cleanup_g6_the_install_id_comes_from_the_install_or_the_compose_project(
    install: str | None, project: str | None, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.sandbox_broker_main import install_id

    for name, value in (("SANDBOX_INSTALL_ID", install), ("COMPOSE_PROJECT_NAME", project)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert install_id() == expected


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_cleanup_g6_a_broker_with_no_install_id_refuses_to_start(
    blank: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared fallback id would let two installs on one daemon reconcile away each other's sandboxes."""
    from app.sandbox_broker_main import InstallIdUnset, install_id

    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    if blank is None:
        monkeypatch.delenv("SANDBOX_INSTALL_ID", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_INSTALL_ID", blank)
    with pytest.raises(InstallIdUnset):
        _ = install_id()


@pytest.mark.parametrize("fails", [False, True])
async def test_cleanup_g6_the_broker_reconciles_its_install_once_at_start_and_a_failure_never_stops_it(
    fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker

    calls: list[str] = []

    async def reconcile(self: DockerSandboxBroker) -> int:
        calls.append(self._owned[self._labels.install])  # pyright: ignore[reportPrivateUsage]
        if fails:
            raise RuntimeError("daemon hiccup")
        return 0

    monkeypatch.setattr(DockerSandboxBroker, "reconcile", reconcile)
    caps = DockerCapabilities(reachable=True, engine_version="28.0.4", runtimes=frozenset({"runc"}), exec_transport="unix")
    with patch("app.harness.sandbox.docker_broker.probe", return_value=caps):
        service = await create_service(
            _config("cred-now"),  # type: ignore[arg-type]
            provider=_Provider(),  # type: ignore[arg-type]
            clock=ManualClock(),
            ids=RandomIds(),
        )
    assert calls == ["test-install"]
    assert service.app is not None


def _config(current: str, previous: str | None = None, *, grace_ms: int = 60_000) -> FakeConfig:
    document: dict[str, Any] = {"current": current}
    if previous is not None:
        document["previous"] = previous
        document["previousValidUntil"] = time.time() * 1000 + grace_ms
    return FakeConfig({CREDENTIAL_PATH: document})


def _auth(config: FakeConfig) -> WorkerCredentialBrokerAuth:
    return WorkerCredentialBrokerAuth(ConfiguredWorkerCredential(config))  # type: ignore[arg-type]


async def test_the_client_presents_the_current_credential() -> None:
    assert await _auth(_config("cred-now")).headers() == {"Authorization": "Bearer cred-now"}


@pytest.mark.parametrize(
    ("presented", "accepted"),
    [("cred-now", True), ("cred-before", True), ("cred-other", False), ("", False)],
)
async def test_the_server_accepts_current_and_previous_in_grace(presented: str, accepted: bool) -> None:
    auth = _auth(_config("cred-now", "cred-before"))
    assert await auth.verify({"authorization": f"Bearer {presented}"}) is accepted


async def test_the_previous_credential_expires_after_its_grace() -> None:
    auth = _auth(_config("cred-now", "cred-before", grace_ms=-1))
    assert await auth.verify({"Authorization": "Bearer cred-before"}) is False


async def test_the_endpoint_comes_from_the_endpoints_document() -> None:
    configured = FakeConfig({"/services/endpoints": {"sandboxBroker": {"endpoint": "http://sandbox-broker:8095"}}})
    assert await broker_endpoint(configured) == "http://sandbox-broker:8095"  # type: ignore[arg-type]
    assert await broker_endpoint(FakeConfig({})) == "http://localhost:8095"  # type: ignore[arg-type]


class _Provider:
    client = None

    def close(self) -> None:
        pass


@pytest.fixture
async def server() -> AsyncIterator[TestServer]:
    caps = DockerCapabilities(reachable=True, engine_version="28.0.4", runtimes=frozenset({"runc"}), exec_transport="unix")
    with patch("app.harness.sandbox.docker_broker.probe", return_value=caps):
        service = await create_service(
            _config("cred-now"),  # type: ignore[arg-type]
            provider=_Provider(),  # type: ignore[arg-type]
            clock=ManualClock(),
            ids=RandomIds(),
        )
    test_server = TestServer(service.app)
    await test_server.start_server()
    yield test_server
    await test_server.close()


async def test_the_broker_process_requires_the_worker_credential(server: TestServer) -> None:
    good = await RemoteSandboxBroker.open(str(server.make_url("")), _auth(_config("cred-now")))
    assert good.modes() == frozenset({RuntimeMode.DOCKER_HARDENED})
    await good.aclose()
    bad = RemoteSandboxBroker(str(server.make_url("")), StaticBearerAuth("cred-stolen"))
    with pytest.raises(BrokerUnauthorized):
        await bad.refresh_modes()
    await bad.aclose()


@pytest.mark.parametrize("container", [None, "pipeshub-egress-proxy"])
async def test_pr2_the_broker_wires_egress_only_when_a_proxy_is_configured(
    container: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if container is None:
        monkeypatch.delenv("SANDBOX_EGRESS_PROXY_CONTAINER", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_EGRESS_PROXY_CONTAINER", container)
        monkeypatch.setenv("SANDBOX_EGRESS_SUBNETS", "10.99.0.0/24")
    caps = DockerCapabilities(reachable=True, engine_version="28.0.4", runtimes=frozenset({"runc"}), exec_transport="unix")
    config = _config("cred-now")
    config.documents["/services/endpoints"] = {"egressProxy": {"controlEndpoint": "http://egress-proxy:8098"}}
    with patch("app.harness.sandbox.docker_broker.probe", return_value=caps), patch.object(DockerEgressWiring, "load"):
        service = await create_service(
            config,  # type: ignore[arg-type]
            provider=_Provider(),  # type: ignore[arg-type]
            clock=ManualClock(),
            ids=RandomIds(),
        )
    wiring = service.broker.egress
    if container is None:
        assert wiring is None
        spec = SandboxSpec(
            os.getenv("SANDBOX_DOCKER_IMAGE") or "pipeshub/sandbox:latest",
            RuntimeMode.DOCKER_HARDENED,
            egress=EgressPolicy(destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),)),
        )
        with pytest.raises(PolicyUnenforceable):
            _ = await service.broker.acquire(spec, lease=LEASE, idempotency_key="k")
    else:
        assert wiring is not None
        assert (wiring.proxy_container, wiring.pool.cidr) == (container, "10.99.0.0/24")
        # The control endpoint is read on every call (D1 FB-2, test_d1_late_config.py LC-1).
        assert isinstance(wiring.bindings, ConfiguredEgressBindings)
        assert wiring.network_name("sbx_1") == "pipeshub-sbx-net-sbx_1"


def test_br4_the_broker_allows_the_legacy_image_and_every_pinned_engine_image() -> None:
    """E0 BR-4; E0b SEL-2: the engine's run image (the tools variant), not the engine-only variant."""
    from app.harness.sandbox.docker_policy import HardeningPolicy
    from app.sandbox_broker_main import allowed_images

    with patch.dict(os.environ, {"SANDBOX_DOCKER_IMAGE": "pipeshub/sandbox:x"}):
        images = allowed_images()
    # Exactly the run image of every registered engine (E0b SEL-2): never an engine-only variant.
    assert images == frozenset(
        {
            "pipeshub/sandbox:x",
            "pxh-opencode-tools:1.18.34-ph7",
            "pxh-codex-tools:0.160.0-ph7",
            "pxh-claude-code-tools:2.1.319-ph7",
            "pxh-gemini-tools:0.63.0-ph7",
            "pxh-goose-tools:1.57.0-ph7",
            "pxh-qwen-tools:0.26.5-ph7",
            "pxh-dsh-tools:0.2.2-rc.1-ph7",
        }
    )
    policy = HardeningPolicy(user="1000:1000", allowed_networks=frozenset(), volume_allowed=lambda _: False, allowed_images=images)
    assert policy.image_allowed("pxh-opencode-tools:1.18.34-ph7")
    assert policy.image_allowed("pxh-codex-tools:0.160.0-ph7")
    # E9: the admin-built Claude Code image by its allowlisted tag only; CCPY: the tools variant, not engine-only.
    assert policy.image_allowed("pxh-claude-code-tools:2.1.319-ph7")
    assert not policy.image_allowed("pxh-claude-code-tools:2.1.320")
    assert not policy.image_allowed("pxh-claude-code:2.1.319")
    assert not policy.image_allowed("pxh-opencode:1.18.34")
    assert not policy.image_allowed("pxh-opencode-tools:1.18.33")
    # WP-76: Goose's run image, never its engine-only variant.
    assert policy.image_allowed("pxh-goose-tools:1.57.0-ph7")
    assert not policy.image_allowed("pxh-goose:1.57.0")


@pytest.mark.parametrize(("raw", "ceiling"), [(None, 0), ("", 0), ("  ", 0), ("0", 0), ("3", 3), (" 12 ", 12)])
def test_fl1_the_warm_pool_ceiling(raw: str | None, ceiling: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import warm_pool_max

    if raw is None:
        monkeypatch.delenv("SANDBOX_WARM_POOL_MAX", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_WARM_POOL_MAX", raw)
    assert warm_pool_max() == ceiling


@pytest.mark.parametrize("raw", ["-1", "two", "1.5", "true"])
def test_fl1_a_malformed_ceiling_refuses_to_start(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import InvalidWarmPoolMax, warm_pool_max

    monkeypatch.setenv("SANDBOX_WARM_POOL_MAX", raw)
    with pytest.raises(InvalidWarmPoolMax):
        _ = warm_pool_max()


@pytest.mark.parametrize(("raw", "quota"), [(None, 4096), ("", 4096), (" 512 ", 512)])
def test_sec_iso_06_the_workspace_quota(raw: str | None, quota: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import workspace_quota_mb

    if raw is None:
        monkeypatch.delenv("SANDBOX_WORKSPACE_QUOTA_MB", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_WORKSPACE_QUOTA_MB", raw)
    assert workspace_quota_mb() == quota


@pytest.mark.parametrize("raw", ["0", "-1", "lots", "1.5"])
def test_sec_iso_06_no_value_turns_the_workspace_quota_off(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import InvalidWorkspaceQuota, workspace_quota_mb

    monkeypatch.setenv("SANDBOX_WORKSPACE_QUOTA_MB", raw)
    with pytest.raises(InvalidWorkspaceQuota):
        _ = workspace_quota_mb()


@pytest.mark.parametrize(("raw", "ceiling"), [(None, 50), ("", 50), (" 7 ", 7), ("0", None), ("-3", None)])
def test_c2_19_the_install_sandbox_ceiling(raw: str | None, ceiling: int | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """SANDBOX_MAX_TOTAL as the legacy governor reads it: default 50, zero or less is no ceiling."""
    from app.sandbox_broker_main import max_sandboxes

    if raw is None:
        monkeypatch.delenv("SANDBOX_MAX_TOTAL", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_MAX_TOTAL", raw)
    assert max_sandboxes() == ceiling


@pytest.mark.parametrize("raw", ["lots", "1.5"])
def test_c2_19_a_malformed_sandbox_ceiling_refuses_to_start(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import InvalidMaxSandboxes, max_sandboxes

    monkeypatch.setenv("SANDBOX_MAX_TOTAL", raw)
    with pytest.raises(InvalidMaxSandboxes):
        _ = max_sandboxes()


@pytest.mark.parametrize(("raw", "withheld"), [(None, set()), ("", set()), (" runsc ", {"runsc"}), ("runsc,", {"runsc"})])
def test_gvs6_the_operator_may_withhold_runsc(raw: str | None, withheld: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import withheld_runtimes

    if raw is None:
        monkeypatch.delenv("SANDBOX_WITHHOLD_RUNTIMES", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_WITHHOLD_RUNTIMES", raw)
    assert withheld_runtimes() == withheld


@pytest.mark.parametrize("raw", ["runc", "gvisor", "runsc,kata"])
def test_gvs6_a_runtime_the_broker_cannot_withhold_refuses_to_start(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not leave gVisor offered while the operator believes it withheld."""
    from app.sandbox_broker_main import InvalidWithheldRuntimes, withheld_runtimes

    monkeypatch.setenv("SANDBOX_WITHHOLD_RUNTIMES", raw)
    with pytest.raises(InvalidWithheldRuntimes):
        _ = withheld_runtimes()


@pytest.mark.parametrize(("raw", "enabled"), [("0", False), ("2", True)])
async def test_fl2_the_broker_pools_the_release_images_as_engine_turns_shape_them(
    raw: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.engines.controller import (
        engine_warm_shapes,
    )
    from harness_engines.release import release_images

    monkeypatch.setenv("SANDBOX_WARM_POOL_MAX", raw)
    caps = DockerCapabilities(reachable=True, engine_version="28.0.4", runtimes=frozenset({"runc"}), exec_transport="unix")
    with (
        patch("app.harness.sandbox.docker_broker.probe", return_value=caps),
        patch.object(DockerSandboxBroker, "list_warm", return_value=[]),
    ):
        service = await create_service(
            _config("cred-now"),  # type: ignore[arg-type]
            provider=_Provider(),  # type: ignore[arg-type]
            clock=ManualClock(),
            ids=RandomIds(),
        )
    pool = service.broker.pool
    assert pool.enabled is enabled
    if enabled:
        # As the worker's controller builds engine sandboxes: with the /pipeshub session folder (R3) and each
        # engine's own CPUs (WP-17).
        from app.harness.engines.worker import controller_config
        from app.sandbox_broker_main import warm_images
        from harness_engines.release import release_allowlist

        expected = engine_warm_shapes(
            controller_config(release_allowlist()),
            sorted(warm_images()),
            RuntimeMode.DOCKER_HARDENED,
        )
        assert pool.shapes == expected
        assert {s.image_digest for s in pool.shapes} == warm_images() <= release_images()
        assert pool.config.learn_images == warm_images()
        assert pool.config.ceiling == 2


def test_r23fu_fu_1_only_images_an_installed_engine_runs_on_are_warmed() -> None:
    from app.sandbox_broker_main import engine_images, warm_images
    from harness_engines.release import uninstalled_engines

    uninstalled = set(uninstalled_engines())
    by_image = engine_images()
    for image, refs in by_image.items():
        assert (image in warm_images()) is any(ref not in uninstalled for ref in refs), image
    with patch("app.sandbox_broker_main.uninstalled_engines", return_value=()):
        assert warm_images() == frozenset(by_image)
    qwen = next(image for image, refs in by_image.items() if any(ref.startswith("acp-qwen@") for ref in refs))
    with patch("app.sandbox_broker_main.uninstalled_engines", return_value=by_image[qwen]):
        assert qwen not in warm_images(), "Qwen Code's 3 GiB image is never warmed where its profile is not installed"

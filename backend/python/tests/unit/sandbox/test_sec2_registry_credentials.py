"""SEC2 O-3 (RC-1, RC-2), OWD-1 (WP-06): a registry URL's password never enters an install sandbox, in any form.

On the broker the install gets an egress allowlist of the registry's host and a placeholder as its Basic password;
the egress proxy, holding the credential sealed in the route, puts the real one on each request (inside TLS it
ends, so the install first reads and trusts the proxy's certificate). The Docker executors have no proxy, so a
credentialed registry is refused there with the reason. Without credentials every command is today's.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding import credentials as cred
from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.sandbox import docker_executor
from app.sandbox.broker_executor import BrokerExecutor
from app.sandbox.models import SandboxLanguage
from harness_core.sandbox.egress.policy import basic_password, policy_errors
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.egress.trust import (
    TRUST_BUNDLE,
    TRUST_SCRIPT_ENV,
    trusted_command,
)
from harness_core.sandbox.types import CredentialInjection
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.testing.sandbox.egress import FakeEgressProxy

if TYPE_CHECKING:
    from pathlib import Path

    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

SECRET = "s3cr3t-Pa:ss w@rd"
PIP_URL = "https://ci-user:s3cr3t-Pa%3Ass%20w%40rd@pypi.corp.example:8443/simple"
NPM_URL = "https://ci-user:s3cr3t-Pa%3Ass%20w%40rd@npm.corp.example/repo/npm"
PUBLIC_PIP = "https://pypi.corp.example:8443/simple"
PUBLIC_NPM = "https://npm.corp.example/repo/npm"
DEPLOYMENT_KEY = "ec761ba3b3abdb780c1bebd0f56c79d81613ed8f9fb8c0e4eb7ac21ed714e5a7"
SEALER = CredentialSealer(DEPLOYMENT_KEY.encode())


class TestRc1Pure:
    def test_a_url_splits_into_its_public_url_and_the_credential(self) -> None:
        found = cred.registry(PIP_URL)
        assert found.url == PUBLIC_PIP and found.credential == cred.Credential("ci-user", SECRET)
        assert SECRET not in repr(found)

    @pytest.mark.parametrize("url", ["https://pypi.org/simple", "https://registry.npmjs.org", "http://mirror:3141/root/pypi"])
    def test_a_url_without_credentials_is_kept(self, url: str) -> None:
        assert cred.registry(url) == cred.Registry(url)

    def test_the_proxied_install_holds_a_placeholder_and_the_route_holds_the_sealed_credential(self) -> None:
        proxied = cred.proxied_registry(cred.registry(PIP_URL), SEALER, "pxh-ph-0123456789abcdef0123")
        [destination] = proxied.egress.destinations
        [route] = proxied.egress.token_routes
        assert (destination.host, destination.ports) == ("pypi.corp.example", (8443,))
        assert route.injection is CredentialInjection.BASIC and policy_errors(proxied.egress) == []
        assert SEALER.open(route) == f"ci-user:{SECRET}"
        assert proxied.pip_url() == "https://pxh:pxh-ph-0123456789abcdef0123@pypi.corp.example:8443/simple"
        assert proxied.env["HTTPS_PROXY"] == "http://egress-proxy:8097" and proxied.env["PIP_CERT"] == TRUST_BUNDLE

    def test_npm_sends_the_placeholder_as_its_scoped_basic_password(self) -> None:
        proxied = cred.proxied_registry(cred.registry(NPM_URL), SEALER, "pxh-ph-0123456789abcdef0123")
        flag = proxied.npm_auth_flag()
        scope, _, token = flag.partition(":_auth=")
        assert scope == "--//npm.corp.example/repo/npm/"
        assert basic_password("Basic " + token) == "pxh-ph-0123456789abcdef0123"
        assert base64.b64decode(token).decode().startswith("pxh:")

    def test_the_trust_step_runs_first_keeps_the_arguments_and_stops_the_command_when_it_fails(self) -> None:
        command = trusted_command(["sh", "-c", 'echo "$0|$1"', "zero", "one"])
        ran = subprocess.run(command, env={TRUST_SCRIPT_ENV: "print('trusted')", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
        assert (ran.returncode, ran.stdout) == (0, "trusted\nzero|one\n")
        failed = subprocess.run(command, env={TRUST_SCRIPT_ENV: "raise SystemExit(7)", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
        assert (failed.returncode, failed.stdout) == (7, "")


def _no_secret(*things: object) -> None:
    text = json.dumps([repr(t) for t in things])
    for needle in (SECRET, "s3cr3t", "s3cr3t-Pa%3Ass", base64.b64encode(f"ci-user:{SECRET}".encode()).decode()):
        assert needle not in text


class TestRc2AgentLoopDocker:
    @pytest.mark.parametrize("language", ["python", "typescript"])
    def test_a_credentialed_registry_is_refused_where_no_proxy_can_add_it(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, language: str
    ) -> None:
        sandbox = DockerCodingSandbox(
            working_dir=str(tmp_path / "wd"), image="img", pip_index_url=PIP_URL, npm_registry=NPM_URL
        )
        client = MagicMock()
        with caplog.at_level(logging.DEBUG):
            ok, _, stderr = sandbox._install_packages_sync(["left-pad"], language, client, "egress", ["172.30.0.0/16"])  # pyright: ignore[reportPrivateUsage]
        assert not ok and "SANDBOX_MODE=broker" in stderr
        client.containers.create.assert_not_called()
        _no_secret(stderr, caplog.text)

    def test_without_credentials_the_install_command_is_todays(self, tmp_path: Path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), image="img", pip_index_url=PUBLIC_PIP)
        cmd, _, _ = sandbox._install_command(["x"], "python")  # pyright: ignore[reportPrivateUsage]
        assert cmd[:2] == ["sh", "-c"] and cmd[4] == PUBLIC_PIP and TRUST_SCRIPT_ENV not in " ".join(cmd)


class _Recording(FakeSandboxBroker):
    def __init__(self) -> None:
        clock = ManualClock()
        super().__init__(clock=clock, egress_proxy=FakeEgressProxy(clock=clock, gateways={}))
        self.specs: list[SandboxSpec] = []
        self.runs: list[dict[str, Any]] = []

    async def acquire(self, spec: SandboxSpec, **kwargs: Any) -> FakeSandboxHandle:  # noqa: ANN401
        self.specs.append(spec)
        return await super().acquire(spec, **kwargs)


def _recording_sh(broker: _Recording, created: str):  # noqa: ANN202
    async def sh(context: FakeProcessContext) -> int:
        files = context.sandbox.files
        broker.runs.append({
            "argv": list(context.argv),
            "env": dict(context.env),
            "files": {p: f.data for p, f in files.items()},
        })
        context.write_file(created, b"")
        return 0

    return sh


def _assert_proxied(run: dict[str, Any], spec: SandboxSpec, host: str) -> None:
    [route] = spec.egress.token_routes
    assert route.injection is CredentialInjection.BASIC and route.destination == host
    assert SEALER.open(route) == f"ci-user:{SECRET}"
    assert route.placeholder in " ".join(run["argv"]) or route.placeholder in base64.b64decode(
        " ".join(run["argv"]).split(":_auth=")[1].split()[0]
    ).decode()
    assert run["argv"][2].startswith(f'python3 -c "${TRUST_SCRIPT_ENV}" || exit $?; ')
    assert run["env"]["HTTPS_PROXY"] == "http://egress-proxy:8097" and run["env"]["SSL_CERT_FILE"] == TRUST_BUNDLE


class TestRc2Broker:
    @pytest.mark.parametrize(
        ("language", "host", "created"),
        [("python", "pypi.corp.example", "/deps/p/__init__.py"), ("typescript", "npm.corp.example", "/install/node_modules/p/index.js")],
    )
    async def test_agent_loop_broker_install(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, language: str, host: str, created: str
    ) -> None:
        monkeypatch.setenv("SECRET_KEY", DEPLOYMENT_KEY)
        broker = _Recording()
        broker.programs["sh"] = _recording_sh(broker, created)

        async def source() -> FakeSandboxBroker:
            return broker

        sandbox = BrokerCodingSandbox(
            broker=source, working_dir=str(tmp_path / "wd"), image="img", pip_index_url=PIP_URL, npm_registry=NPM_URL
        )
        with caplog.at_level(logging.DEBUG):
            ok, _, _ = await sandbox._install_in_container(["p"], language, None)  # pyright: ignore[reportPrivateUsage]
        assert ok
        [run] = broker.runs
        [spec] = broker.specs
        _assert_proxied(run, spec, host)
        _no_secret(run["argv"], run["env"], run["files"], caplog.text)

    @pytest.mark.parametrize(
        ("language", "host", "created"),
        [
            (SandboxLanguage.PYTHON, "pypi.corp.example", "/deps/p/__init__.py"),
            (SandboxLanguage.TYPESCRIPT, "npm.corp.example", "/install/node_modules/p/index.js"),
        ],
    )
    async def test_legacy_executor_broker_install(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        language: SandboxLanguage,
        host: str,
        created: str,
    ) -> None:
        monkeypatch.setenv("SECRET_KEY", DEPLOYMENT_KEY)
        monkeypatch.setattr(docker_executor, "_DEFAULT_PIP_INDEX_URL", PIP_URL)
        monkeypatch.setattr(docker_executor, "_DEFAULT_NPM_REGISTRY", NPM_URL)
        broker = _Recording()
        broker.programs["sh"] = _recording_sh(broker, created)

        async def source() -> FakeSandboxBroker:
            return broker

        executor = BrokerExecutor(broker=source, image="img", work_root=str(tmp_path))
        with caplog.at_level(logging.DEBUG):
            await executor._install(["p"], language, str(tmp_path / "deps"))  # pyright: ignore[reportPrivateUsage]
        [install] = broker.runs
        _assert_proxied(install, broker.specs[0], host)
        _no_secret(install["argv"], install["env"], install["files"], caplog.text)

    async def test_without_credentials_the_install_keeps_its_open_egress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docker_executor, "_DEFAULT_PIP_INDEX_URL", PUBLIC_PIP)
        broker = _Recording()
        broker.programs["sh"] = _recording_sh(broker, "/deps/p/__init__.py")

        async def source() -> FakeSandboxBroker:
            return broker

        await BrokerExecutor(broker=source, image="img", work_root=str(tmp_path))._install(  # pyright: ignore[reportPrivateUsage]
            ["p"], SandboxLanguage.PYTHON, str(tmp_path / "deps")
        )
        assert broker.specs[0].egress.token_routes == () and broker.specs[0].egress.default.value == "allow"
        assert broker.runs[0]["env"] == {"HOME": "/tmp"}


class TestRc2LegacyDockerExecutor:
    @pytest.mark.parametrize("language", [SandboxLanguage.PYTHON, SandboxLanguage.TYPESCRIPT])
    def test_a_credentialed_registry_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, language: SandboxLanguage
    ) -> None:
        monkeypatch.setattr(docker_executor, "_DEFAULT_PIP_INDEX_URL", PIP_URL)
        monkeypatch.setattr(docker_executor, "_DEFAULT_NPM_REGISTRY", NPM_URL)
        client = MagicMock()
        with (
            patch("docker.from_env", return_value=client),
            patch.object(docker_executor, "ensure_egress_network_sync", return_value=["172.30.0.0/16"]),
            caplog.at_level(logging.DEBUG),
            pytest.raises(RuntimeError, match="SANDBOX_MODE=broker") as refused,
        ):
            docker_executor.DockerExecutor()._install_dependencies(["p"], language, 30)  # pyright: ignore[reportPrivateUsage]
        client.containers.create.assert_not_called()
        _no_secret(str(refused.value), caplog.text)

    def test_without_credentials_the_commands_are_todays(self) -> None:
        cmd, _, _ = docker_executor.install_command(["p"], SandboxLanguage.PYTHON)
        assert cmd == [
            "sh", "-c",
            f"pip install --quiet --no-cache-dir --target /deps --index-url {docker_executor._DEFAULT_PIP_INDEX_URL} p",  # pyright: ignore[reportPrivateUsage]
        ]

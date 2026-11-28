"""`BoxdCodingSandbox` and its factory against a fake `AsyncBoxd`.

The fake records every call and answers the few commands the backend sends
(`getent`, `test -f`, `find`, `stat`, `head`), so these pin how the backend
drives a machine: isolated, one lifetime, the network closed around runs,
ids not names, and errors that carry no provider text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("boxd")

from boxd.errors import AuthenticationError, BoxdError, NotFoundError

from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodingSandboxError,
    IsolationLevel,
    SandboxContext,
    SandboxRef,
)
from app.agent_loop_lib.sandbox.coding.boxd import (
    BoxdCodingSandbox,
)
from app.agent_loop_lib.sandbox.coding.factories import build_factory
from app.agent_loop_lib.sandbox.coding.settings import SharedSandboxConfig

if TYPE_CHECKING:
    from app.agent_loop_lib.sandbox.coding.registry import SandboxBackendFactory

MACHINE_ID = "11111111-2222-3333-4444-555555555555"


@dataclass
class FakeMachines:
    calls: list[tuple[str, object]] = field(default_factory=list)
    egress: list[str] = field(default_factory=list)
    fs: dict[str, bytes] = field(default_factory=dict)
    status: str = "running"
    exec_results: dict[str, tuple[str | bytes, str | bytes, int]] = field(default_factory=dict)
    exec_error: Exception | None = None
    create_error: Exception | None = None
    ready_error: Exception | None = None
    delete_error: Exception | None = None
    deleted: bool = False
    # How many probes still resolve after the allowlist closes.
    egress_lag: int = 0

    def __post_init__(self) -> None:
        self.files = FakeFiles(self)

    async def create(self, name: str | None = None, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("create", {"name": name, **kwargs}))
        if self.create_error:
            raise self.create_error
        return SimpleNamespace(id=MACHINE_ID, name=name, status="starting")

    async def wait_until_ready(self, machine_id: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("wait_until_ready", machine_id))
        if self.ready_error:
            raise self.ready_error
        return SimpleNamespace(id=machine_id, status="running")

    async def get(self, machine_id: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("get", machine_id))
        if self.deleted:
            raise NotFoundError("machine not found")
        return SimpleNamespace(id=machine_id, status=self.status)

    async def start(self, machine_id: str, **kwargs: object) -> None:
        self.calls.append(("start", machine_id))
        self.status = "running"

    async def set_egress_allow(self, machine_id: str, entries: list[str], **kwargs: object) -> list[str]:
        self.calls.append(("set_egress_allow", list(entries)))
        self.egress = list(entries)
        return list(entries)

    async def delete(self, machine_id: str, **kwargs: object) -> None:
        self.calls.append(("delete", machine_id))
        if self.delete_error:
            raise self.delete_error
        self.deleted = True

    async def exec(self, machine_id: str, command: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("exec", {"machine_id": machine_id, "command": command, **kwargs}))
        if self.deleted:
            raise NotFoundError("machine not found")
        if self.exec_error:
            raise self.exec_error
        buffer = kwargs.get("encoding") == "buffer"
        for pattern, (out, err, code) in self.exec_results.items():
            if pattern in command:
                return SimpleNamespace(stdout=out, stderr=err, exit_code=code)
        if command.startswith("getent hosts"):
            if self.egress == ["none"] and self.egress_lag > 0:
                self.egress_lag -= 1
                return SimpleNamespace(stdout="", stderr="", exit_code=0)
            return SimpleNamespace(stdout="", stderr="", exit_code=2 if self.egress == ["none"] else 0)
        if command.startswith("test -f"):
            path = command.split()[-1].strip("'")
            if "&& head -c" in command:
                path = re.search(r"test -f (\S+) &&", command).group(1).strip("'")  # type: ignore[union-attr]
                if path not in self.fs:
                    return SimpleNamespace(stdout=b"", stderr=b"", exit_code=1)
                n = int(re.search(r"head -c (\d+)", command).group(1))  # type: ignore[union-attr]
                return SimpleNamespace(stdout=self.fs[path][:n], stderr=b"", exit_code=0)
            return SimpleNamespace(stdout="", stderr="", exit_code=0 if path in self.fs else 1)
        if "find ." in command:
            listing = b"".join(
                b"./" + p.removeprefix("/home/boxd/sandbox/").encode() + b"\0" for p in sorted(self.fs)
            )
            return SimpleNamespace(stdout=listing, stderr=b"", exit_code=0)
        if command.startswith("stat -c %s"):
            path = command.split()[-1].strip("'")
            if path not in self.fs:
                return SimpleNamespace(stdout="", stderr="", exit_code=1)
            return SimpleNamespace(stdout=f"{len(self.fs[path])}\n", stderr="", exit_code=0)
        return SimpleNamespace(stdout=b"" if buffer else "ok\n", stderr=b"" if buffer else "", exit_code=0)

    def commands(self) -> list[str]:
        return [c[1]["command"] for c in self.calls if c[0] == "exec"]

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeFiles:
    def __init__(self, machines: FakeMachines) -> None:
        self._m = machines

    async def upload(self, machine_id: str, path: str, data: bytes, **kwargs: object) -> int:
        self._m.calls.append(("upload", path))
        self._m.fs[path] = bytes(data)
        return len(data)

    async def download(self, machine_id: str, path: str, **kwargs: object) -> bytes:
        self._m.calls.append(("download", path))
        if self._m.deleted or path not in self._m.fs:
            raise NotFoundError("no such file")
        return self._m.fs[path]


class FakeBoxd:
    def __init__(self, machines: FakeMachines) -> None:
        self.machines = machines
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _client(**kwargs: object) -> FakeBoxd:
    return FakeBoxd(FakeMachines(**kwargs))


def _sandbox(client: FakeBoxd, **kwargs: object) -> BoxdCodingSandbox:
    return BoxdCodingSandbox(client=client, **kwargs)


class TestProvision:
    async def test_creates_an_isolated_machine_with_one_lifetime(self) -> None:
        client = _client()
        sb = _sandbox(client, org="acme", region="fra", vcpu=1, memory="4G", lifetime_s=600)
        info = await sb.provision()

        name, kwargs = client.machines.calls[0]
        assert name == "create"
        assert kwargs["isolated"] is True
        assert kwargs["delete_after"] == 600
        assert "auto_destroy_timeout" not in kwargs
        assert kwargs["auto_suspend_timeout"] == 0
        assert (kwargs["org"], kwargs["region"], kwargs["vcpu"], kwargs["memory"]) == ("acme", "fra", 1, "4G")
        assert kwargs["name"].startswith("pipeshub-sandbox-")
        assert info.sandbox_id == MACHINE_ID == sb.sandbox_id

    async def test_names_are_unique_per_sandbox(self) -> None:
        a, b = _client(), _client()
        await _sandbox(a).provision()
        await _sandbox(b).provision()
        assert a.machines.calls[0][1]["name"] != b.machines.calls[0][1]["name"]

    async def test_labels_name_whose_sandbox_it_is_and_extra_labels_cannot_override(self) -> None:
        client = _client()
        ctx = SandboxContext(org_id="o1", user_id="u1", conversation_id="c1", run_id="r1")
        await _sandbox(client, context=ctx, labels={"team": "data", "app": "other"}).provision()
        labels = client.machines.calls[0][1]["labels"]
        assert labels == {
            "team": "data", "app": "pipeshub", "pipeshub-sandbox": "coding",
            "org-id": "o1", "user-id": "u1", "conversation-id": "c1", "run-id": "r1",
        }

    async def test_waits_for_ready_and_closes_the_network_before_any_run(self) -> None:
        client = _client()
        await _sandbox(client).provision()
        names = client.machines.names()
        assert names.index("wait_until_ready") < names.index("set_egress_allow") < names.index("exec")
        assert client.machines.egress == ["none"]
        assert client.machines.commands()[0].startswith("getent hosts")

    async def test_waits_until_the_closed_network_is_in_force(self) -> None:
        client = _client(egress_lag=3)
        await _sandbox(client).provision()
        probes = [c for c in client.machines.commands() if c.startswith("getent hosts")]
        assert len(probes) == 4

    async def test_network_on_sets_no_allowlist(self) -> None:
        client = _client()
        await _sandbox(client, allow_network=True).provision()
        assert "set_egress_allow" not in client.machines.names()

    async def test_provision_is_idempotent(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        await sb.provision()
        assert client.machines.names().count("create") == 1

    async def test_a_failure_after_create_deletes_the_machine(self) -> None:
        client = _client(ready_error=TimeoutError("not ready"))
        sb = _sandbox(client)
        with pytest.raises(CodingSandboxError, match="start the boxd machine"):
            await sb.provision()
        assert ("delete", MACHINE_ID) in client.machines.calls

    async def test_a_failed_cleanup_still_raises_the_first_failure(self) -> None:
        client = _client(ready_error=TimeoutError("not ready"), delete_error=RuntimeError("down"))
        with pytest.raises(CodingSandboxError, match="start the boxd machine"):
            await _sandbox(client).provision()

    async def test_a_refused_key_says_so_without_provider_text(self) -> None:
        client = _client(create_error=AuthenticationError("invalid key key-secretvalue"))
        with pytest.raises(CodingSandboxError) as excinfo:
            await _sandbox(client).provision()
        assert "BOXD_API_KEY" in str(excinfo.value)
        assert "secretvalue" not in str(excinfo.value)

    async def test_an_unknown_snapshot_org_or_region_says_so(self) -> None:
        client = _client(create_error=NotFoundError("snapshot not found"))
        with pytest.raises(CodingSandboxError, match="BOXD_SNAPSHOT"):
            await _sandbox(client, snapshot="typo").provision()

    async def test_no_key_is_a_clear_error_before_any_call(self) -> None:
        with pytest.raises(CodingSandboxError, match="BOXD_API_KEY is not set"):
            await BoxdCodingSandbox(api_key=None).provision()

    async def test_sandbox_id_before_provision_raises(self) -> None:
        with pytest.raises(CodingSandboxError):
            _ = _sandbox(_client()).sandbox_id


class TestExecute:
    async def test_runs_python_in_a_virtualenv_under_timeout(self) -> None:
        client = _client()
        sb = _sandbox(client)
        result = await sb.execute(CodeRequest(code="print(1)", language="python", timeout=7))
        assert result.exit_code == 0
        commands = client.machines.commands()
        assert any("python3 -m venv /home/boxd/sandbox/.venv" in c for c in commands)
        run = next(c for c in commands if ".venv/bin/python main.py" in c)
        assert run.startswith("timeout -k 5 7 sh -c ")
        call = next(c[1] for c in client.machines.calls if c[0] == "exec" and c[1]["command"] == run)
        assert call["timeout"] > 7
        assert call["env"]["OUTPUT_DIR"] == "/home/boxd/sandbox/output"
        assert call["cwd"] == "/home/boxd/sandbox"
        assert ("upload", "/home/boxd/sandbox/main.py") in client.machines.calls

    async def test_typescript_installs_its_runtime_once(self) -> None:
        client = _client(exec_results={"test -x node_modules/.bin/tsx": ("", "", 1)})
        sb = _sandbox(client, allow_network=True)
        await sb.execute(CodeRequest(code="console.log(1)"))
        await sb.execute(CodeRequest(code="console.log(2)"))
        installs = [c for c in client.machines.commands() if "npm install" in c and "tsx" in c]
        assert len(installs) == 1

    async def test_exit_code_and_stderr_are_data(self) -> None:
        client = _client(exec_results={"main.py": ("", "Traceback: boom\n", 3)})
        result = await _sandbox(client).execute(CodeRequest(code="x", language="python"))
        assert (result.exit_code, result.stderr) == (3, "Traceback: boom\n")
        assert result.error_analysis is not None

    async def test_a_timed_out_exec_is_exit_minus_one(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        orig_exec = client.machines.exec

        async def exec_timing_out(machine_id: str, command: str, **kwargs: object) -> SimpleNamespace:
            if "main.py" in command and command.startswith("timeout"):
                raise BoxdError("exec timed out after 23s")
            return await orig_exec(machine_id, command, **kwargs)

        client.machines.exec = exec_timing_out  # type: ignore[method-assign]
        result = await sb.execute(CodeRequest(code="x", language="python", timeout=3))
        assert result.exit_code == -1
        assert "timed out" in result.stderr

    async def test_a_helper_call_timing_out_is_an_infrastructure_error(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        client.machines.exec_error = BoxdError("exec timed out after 60s")
        with pytest.raises(CodingSandboxError):
            await sb.list_files()
        assert await sb.file_size("a.txt") is None

    async def test_timeout_124_after_the_deadline_is_exit_minus_one(self, monkeypatch) -> None:
        import app.agent_loop_lib.sandbox.coding.boxd as mod

        clock = iter([0.0, 10.0])
        monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock, 10.0))
        client = _client()
        sb = _sandbox(client)
        sb._python_initialized = True
        sb._machine_id = MACHINE_ID
        client.machines.exec_results = {"main.py": ("partial", "", 124)}
        stdout, stderr, code = await sb._run_command(".venv/bin/python main.py", timeout=3)
        assert (stdout, code) == ("partial", -1)
        assert "timed out" in stderr

    async def test_a_kill_after_the_deadline_is_exit_minus_one(self, monkeypatch) -> None:
        import app.agent_loop_lib.sandbox.coding.boxd as mod

        clock = iter([0.0, 10.0])
        monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock, 10.0))
        client = _client()
        sb = _sandbox(client)
        sb._machine_id = MACHINE_ID
        client.machines.exec_results = {"main.py": ("", "", 137)}
        _, _, code = await sb._run_command(".venv/bin/python main.py", timeout=3)
        assert code == -1

    async def test_another_timeout_is_not_read_as_a_timed_out_run(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        sb._python_initialized = True
        client.machines.exec_error = TimeoutError("connection timed out")
        with pytest.raises(CodingSandboxError):
            await sb.execute(CodeRequest(code="x", language="python"))

    async def test_an_own_exit_124_before_the_deadline_is_kept(self) -> None:
        client = _client(exec_results={"main.py": ("", "", 124)})
        result = await _sandbox(client).execute(CodeRequest(code="x", language="python", timeout=30))
        assert result.exit_code == 124

    async def test_artifacts_are_new_files_only(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        client.machines.fs["/home/boxd/sandbox/old.txt"] = b"x"
        orig_exec = client.machines.exec

        async def exec_writing(machine_id: str, command: str, **kwargs: object) -> SimpleNamespace:
            if "main.py" in command and "timeout" in command:
                client.machines.fs["/home/boxd/sandbox/output/new.csv"] = b"a,b"
            return await orig_exec(machine_id, command, **kwargs)

        client.machines.exec = exec_writing  # type: ignore[method-assign]
        result = await sb.execute(CodeRequest(code="x", language="python"))
        assert result.artifacts == ["output/new.csv"]

    async def test_infrastructure_errors_raise_without_provider_text(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        client.machines.exec_error = RuntimeError("upstream said host 10.0.0.1 failed")
        with pytest.raises(CodingSandboxError) as excinfo:
            await sb.execute(CodeRequest(code="x", language="python"))
        assert "10.0.0.1" not in str(excinfo.value)


class TestInstallNetwork:
    """Network off is off for the machine's whole life: generated code has
    root in its VM, so nothing may open the network after it has run."""

    async def test_network_off_never_opens_the_allowlist(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.install_packages(["six"], "python")
        await sb.execute(CodeRequest(code="x", language="python", packages=["pandas"]))
        egress = [c[1] for c in client.machines.calls if c[0] == "set_egress_allow"]
        assert egress == [["none"]]
        assert not any("pip install" in c or "npm install" in c for c in client.machines.commands())

    async def test_network_off_accepts_packages_the_machine_has(self) -> None:
        client = _client()
        result = await _sandbox(client).install_packages(["pandas>=2"], "python")
        assert result.success
        check = next(c for c in client.machines.commands() if "importlib.metadata" in c)
        assert check.endswith("pandas 2>/dev/null'") or "pandas" in check

    async def test_network_off_refuses_a_missing_package_by_name(self) -> None:
        client = _client(exec_results={"importlib.metadata": ("", "", 1)})
        result = await _sandbox(client).install_packages(["six", "rich"], "python")
        assert not result.success
        assert "network" in result.stderr and "six" in result.stderr and "rich" in result.stderr

    async def test_network_off_checks_node_packages_in_node_modules(self) -> None:
        client = _client(exec_results={"node_modules/lodash/package.json": ("", "", 1)})
        result = await _sandbox(client).install_packages(["lodash"], "typescript")
        assert not result.success and "lodash" in result.stderr

    async def test_network_on_installs_with_a_bounded_timeout(self) -> None:
        client = _client()
        result = await _sandbox(client, allow_network=True, lifetime_s=1800).install_packages(["six"], "python")
        assert result.success
        install = next(c for c in client.machines.calls if c[0] == "exec" and "pip install" in c[1]["command"])
        assert install[1]["command"].startswith("timeout -k 5 600 ")
        assert "--only-binary=:all:" in install[1]["command"]
        assert "set_egress_allow" not in client.machines.names()

    async def test_package_specs_are_validated_before_the_machine_sees_them(self) -> None:
        client = _client()
        sb = _sandbox(client, package_denylist=["evil"], allow_network=True)
        assert not (await sb.install_packages(["evil"], "python")).success
        assert not (await sb.install_packages(["six; rm -rf /"], "python")).success
        assert not any("pip install" in c for c in client.machines.commands())

    async def test_installed_packages_are_not_installed_again(self) -> None:
        client = _client()
        sb = _sandbox(client, allow_network=True)
        await sb.install_packages(["six"], "python")
        await sb.install_packages(["six"], "python")
        assert sum("pip install" in c for c in client.machines.commands()) == 1


class TestConcurrency:
    async def test_two_runs_on_one_sandbox_do_not_interleave(self) -> None:
        import asyncio

        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        sb._python_initialized = True
        await asyncio.gather(
            sb.execute(CodeRequest(code="print(1)", language="python")),
            sb.execute(CodeRequest(code="print(2)", language="python")),
        )
        steps = [
            c[0] if c[0] == "upload" else "run"
            for c in client.machines.calls
            if c[0] == "upload" or (c[0] == "exec" and "main.py" in c[1]["command"])
        ]
        assert steps == ["upload", "run", "upload", "run"]


class TestFiles:
    async def test_paths_cannot_leave_the_working_directory(self) -> None:
        sb = _sandbox(_client())
        await sb.provision()
        for bad in ("../etc/passwd", "/etc/passwd", "a/../../x"):
            with pytest.raises(ValueError):
                await sb.download_file(bad)
            with pytest.raises(ValueError):
                await sb.upload_file(bad, b"x")

    async def test_upload_download_roundtrip_and_parent_dirs(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.upload_file("workspace/data/in.bin", b"\x00\x01")
        assert await sb.download_file("workspace/data/in.bin") == b"\x00\x01"
        assert any(c == "timeout -k 5 30 sh -c 'mkdir -p /home/boxd/sandbox/workspace/data'"
                   for c in client.machines.commands())

    async def test_output_dir_alias(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.upload_file("$OUTPUT_DIR/r.txt", b"r")
        assert "/home/boxd/sandbox/output/r.txt" in client.machines.fs

    async def test_a_missing_file_is_file_not_found(self) -> None:
        sb = _sandbox(_client())
        await sb.provision()
        with pytest.raises(FileNotFoundError):
            await sb.download_file("nope.txt")

    async def test_a_deleted_machine_is_not_a_missing_file(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        client.machines.deleted = True
        with pytest.raises(CodingSandboxError):
            await sb.download_file("nope.txt")

    async def test_read_head_size_and_listing(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.upload_file("a.txt", b"0123456789")
        assert await sb.read_file_head("a.txt", 4) == (b"0123", True)
        assert await sb.read_file_head("a.txt", 50) == (b"0123456789", False)
        assert await sb.file_size("a.txt") == 10
        assert await sb.file_size("missing.txt") is None
        assert await sb.list_files() == ["a.txt"]
        with pytest.raises(FileNotFoundError):
            await sb.read_file_head("missing.txt", 4)

    async def test_listing_skips_dependency_dirs(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        await sb.list_files()
        find = next(c for c in client.machines.commands() if "find ." in c)
        for name in ("node_modules", ".venv", "__pycache__", ".git"):
            assert f"-name {name}" in find or f"-name '{name}'" in find


class TestDestroy:
    async def test_deletes_by_id_once_and_closes_its_client(self) -> None:
        client = _client()
        sb = _sandbox(client)
        await sb.provision()
        sb._owns_client = True
        await sb.destroy()
        await sb.destroy()
        assert [c for c in client.machines.calls if c[0] == "delete"] == [("delete", MACHINE_ID)]
        assert client.closed

    async def test_an_already_deleted_machine_is_fine(self) -> None:
        client = _client(delete_error=NotFoundError("gone"))
        sb = _sandbox(client)
        await sb.provision()
        await sb.destroy()

    async def test_an_attached_sandbox_without_a_client_still_deletes(self, monkeypatch) -> None:
        client = _client()
        monkeypatch.setattr(BoxdCodingSandbox, "_sdk", lambda self: client)
        sb = BoxdCodingSandbox.attach(MACHINE_ID, created_at=1000.0, api_key="test-key")
        await sb.destroy()
        assert ("delete", MACHINE_ID) in client.machines.calls

    async def test_other_delete_failures_raise(self) -> None:
        client = _client(delete_error=RuntimeError("unavailable"))
        sb = _sandbox(client)
        await sb.provision()
        with pytest.raises(CodingSandboxError):
            await sb.destroy()


class TestRefAndCapabilities:
    async def test_ref_and_expiry(self) -> None:
        sb = _sandbox(_client(), lifetime_s=600)
        await sb.provision()
        ref = sb.ref
        assert (ref.backend, ref.sandbox_id) == ("boxd", MACHINE_ID)
        assert sb.expires_at == ref.created_at + 600

    def test_capabilities_say_what_the_sandbox_does(self) -> None:
        off = BoxdCodingSandbox.describe_capabilities(allow_network=False, lifetime_s=600)
        on = BoxdCodingSandbox.describe_capabilities(allow_network=True, lifetime_s=600)
        assert off.isolation is IsolationLevel.MICROVM
        assert (off.supports_network, on.supports_network) == (False, True)
        assert off.is_metered and off.supports_reconnect
        assert off.max_timeout_s == 600.0


class TestFactory:
    def _factory(self, shared: SharedSandboxConfig | None = None, **options: object) -> SandboxBackendFactory:
        return build_factory("boxd", backend_options=options, shared_config=shared)

    def test_registered_and_installed(self) -> None:
        factory = self._factory()
        assert factory.backend_name == "boxd"
        assert factory.is_installed()

    async def test_health_needs_a_key(self, monkeypatch) -> None:
        monkeypatch.delenv("BOXD_API_KEY", raising=False)
        health = await self._factory().health_check()
        assert not health.available and "BOXD_API_KEY" in (health.reason or "")
        monkeypatch.setenv("BOXD_API_KEY", "test-key")
        assert (await self._factory().health_check()).available

    def test_options_and_env(self, monkeypatch) -> None:
        monkeypatch.setenv("BOXD_API_KEY", "key-from-env")
        monkeypatch.setenv("BOXD_ORG", "env-org")
        monkeypatch.setenv("BOXD_REGION", "nyc")
        sb = self._factory(size="4x16").create(SandboxContext())
        assert (sb._api_key, sb._org, sb._region, sb._vcpu, sb._memory) == ("key-from-env", "env-org", "nyc", 4, "16G")
        sb = self._factory(org="cfg-org", region="fra", api_key="key-from-config").create(SandboxContext())
        assert (sb._api_key, sb._org, sb._region) == ("key-from-config", "cfg-org", "fra")

    def test_key_never_shows_in_the_config(self) -> None:
        factory = self._factory(api_key="supersecret-key")
        assert "supersecret-key" not in repr(factory.config)
        assert "supersecret-key" not in factory.config.model_dump_json()

    def test_lifetime_never_outlives_the_manager(self) -> None:
        factory = self._factory(SharedSandboxConfig(max_lifetime_s=300))
        assert factory.create(SandboxContext())._lifetime_s == 300
        assert factory.capabilities().max_timeout_s == 300.0
        assert self._factory(boxd_timeout=600).create(SandboxContext())._lifetime_s == 600

    def test_bad_options_are_refused(self) -> None:
        from pydantic import ValidationError

        for options in ({"size": "3x12"}, {"name_prefix": "Bad Prefix"}, {"unknown": 1}, {"boxd_timeout": 5}):
            with pytest.raises(ValidationError):
                self._factory(**options)

    async def test_reconnect_keeps_the_package_policy(self, monkeypatch) -> None:
        client = _client()
        monkeypatch.setattr(BoxdCodingSandbox, "_sdk", lambda self: client)
        shared = SharedSandboxConfig(package_allowlist=["six"], package_denylist=["evil"])
        ref = SandboxRef(backend="boxd", sandbox_id=MACHINE_ID, created_at=1000.0)
        sb = await self._factory(shared).reconnect(ref)
        assert (sb._allowlist, sb._denylist) == ({"six"}, {"evil"})

    @pytest.mark.parametrize(("status", "call"), [("hibernated", "wake"), ("suspended", "resume")])
    async def test_reconnect_wakes_a_sleeping_machine(self, monkeypatch, status: str, call: str) -> None:
        client = _client(status=status)

        async def record(machine_id: str, **kw: object) -> None:
            client.machines.calls.append((call, machine_id))

        setattr(client.machines, call, record)
        monkeypatch.setattr(BoxdCodingSandbox, "_sdk", lambda self: client)
        ref = SandboxRef(backend="boxd", sandbox_id=MACHINE_ID, created_at=1000.0)
        await self._factory().reconnect(ref)
        assert client.machines.names()[:3] == ["get", call, "wait_until_ready"]

    async def test_reconnect_by_id_starts_a_stopped_machine(self, monkeypatch) -> None:
        client = _client(status="stopped")
        monkeypatch.setattr(BoxdCodingSandbox, "_sdk", lambda self: client)
        ref = SandboxRef(backend="boxd", sandbox_id=MACHINE_ID, created_at=1000.0)
        sb = await self._factory(boxd_timeout=600).reconnect(ref)
        assert sb.sandbox_id == MACHINE_ID
        assert sb.expires_at == 1600.0
        assert client.machines.names()[:3] == ["get", "start", "wait_until_ready"]

    async def test_reconnect_to_a_deleted_machine_fails_clearly(self, monkeypatch) -> None:
        client = _client()
        client.machines.deleted = True
        monkeypatch.setattr(BoxdCodingSandbox, "_sdk", lambda self: client)
        ref = SandboxRef(backend="boxd", sandbox_id=MACHINE_ID, created_at=1000.0)
        with pytest.raises(CodingSandboxError, match="no longer exists"):
            await self._factory().reconnect(ref)


class TestWiring:
    async def test_sandbox_mode_boxd_maps_network_off_onto_the_backend(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.capacity import _resolve_mode, is_remote

        monkeypatch.setenv("SANDBOX_MODE", "boxd")
        assert _resolve_mode() == "boxd"
        assert is_remote("boxd") and is_remote("e2b") and not is_remote("compose")

    def test_remote_capacity_needs_no_machine_reading(self) -> None:
        from app.agent_loop_lib.sandbox.capacity import (
            CapacitySettings,
            SandboxCapacity,
        )

        cap = SandboxCapacity("boxd", CapacitySettings(), slot_files=256)
        assert cap.sized
        assert cap.plan.ceiling == 100


class TestSnapshot:
    async def test_machines_start_from_the_configured_snapshot(self, monkeypatch) -> None:
        monkeypatch.setenv("BOXD_SNAPSHOT", "pipeshub-sandbox")
        sb = build_factory("boxd").create(SandboxContext())
        assert sb._snapshot == "pipeshub-sandbox"
        client = _client()
        await _sandbox(client, snapshot="pipeshub-sandbox").provision()
        assert client.machines.calls[0][1]["from_snapshot"] == "pipeshub-sandbox"
        assert client.machines.calls[0][1]["isolated"] is True

    async def test_a_snapshot_with_tsx_skips_the_runtime_install(self) -> None:
        client = _client(exec_results={"test -x node_modules/.bin/tsx": ("", "", 0)})
        await _sandbox(client, snapshot="s").execute(CodeRequest(code="console.log(1)"))
        assert not any("npm install" in c for c in client.machines.commands())

    async def test_without_tsx_the_runtime_is_installed_when_the_network_is_on(self) -> None:
        client = _client(exec_results={"test -x node_modules/.bin/tsx": ("", "", 1)})
        await _sandbox(client, allow_network=True).execute(CodeRequest(code="console.log(1)"))
        assert any("npm install" in c and "tsx" in c for c in client.machines.commands())

    async def test_without_tsx_and_network_typescript_says_what_to_do(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import EnvironmentSetupError

        client = _client(exec_results={"test -x node_modules/.bin/tsx": ("", "", 1)})
        with pytest.raises(EnvironmentSetupError, match="BOXD_SNAPSHOT"):
            await _sandbox(client).execute(CodeRequest(code="console.log(1)"))
        assert not any("npm install" in c for c in client.machines.commands())


class TestSnapshotBuilder:
    def _client(self, failing: str | None = None, statuses: tuple[str, ...] = ("pending", "ready")) -> SimpleNamespace:
        calls: list[tuple[str, object]] = []
        pending = list(statuses)

        def exec_(machine_id: str, command: str, **kwargs: object) -> SimpleNamespace:
            calls.append(("exec", command))
            failed = failing is not None and failing in command
            return SimpleNamespace(exit_code=1 if failed else 0, stdout="", stderr="boom" if failed else "")

        def snapshot(status: str) -> SimpleNamespace:
            return SimpleNamespace(id="snap_1", status=status)

        machines = SimpleNamespace(
            create=lambda name, **kw: calls.append(("create", {"name": name, **kw})) or SimpleNamespace(id=MACHINE_ID),
            wait_until_ready=lambda machine_id, **kw: calls.append(("ready", machine_id)),
            exec=exec_,
            delete=lambda machine_id, **kw: calls.append(("delete", machine_id)),
        )
        snapshots = SimpleNamespace(
            create=lambda machine_id, name, **kw: calls.append(("snapshot", name)) or snapshot(pending.pop(0)),
            get=lambda name, **kw: snapshot(pending.pop(0)),
        )
        return SimpleNamespace(machines=machines, snapshots=snapshots, calls=calls)

    def test_installs_what_run_code_advertises_then_deletes_the_build_machine(self) -> None:
        from app.agents.agent_loop.boxd_snapshot import build_snapshot
        from app.agents.agent_loop.sandbox_bridge import (
            IMAGE_NODE_PACKAGES,
            IMAGE_PYTHON_PACKAGES,
        )

        client = self._client()
        assert build_snapshot(client, "pipeshub-sandbox", org="acme", poll_s=0) == "snap_1"
        create = client.calls[0][1]
        assert create["isolated"] is True and create["org"] == "acme"
        commands = " ".join(c[1] for c in client.calls if c[0] == "exec")
        for package in (*IMAGE_PYTHON_PACKAGES, *IMAGE_NODE_PACKAGES, "tsx", "typescript"):
            assert package in commands
        assert ("snapshot", "pipeshub-sandbox") in client.calls
        assert client.calls[-1] == ("delete", MACHINE_ID)
        assert create["delete_after"] >= 1200 * 5

    def test_a_failed_step_stops_and_still_deletes_the_build_machine(self) -> None:
        from app.agents.agent_loop.boxd_snapshot import build_snapshot

        client = self._client(failing="pip install")
        with pytest.raises(RuntimeError, match="exit 1"):
            build_snapshot(client, "s", poll_s=0)
        assert not any(c[0] == "snapshot" for c in client.calls)
        assert client.calls[-1] == ("delete", MACHINE_ID)


class TestThroughTheBridge:
    """`SANDBOX_MODE=boxd` as `build_coding_sandbox_manager` builds it for a
    chat: the operator's network switch reaches the backend, and the key
    never enters the settings that are logged."""

    def setup_method(self) -> None:
        from app.agent_loop_lib.sandbox.governor import reset_default_governor

        reset_default_governor()

    def teardown_method(self) -> None:
        from app.agent_loop_lib.sandbox.governor import reset_default_governor

        reset_default_governor()

    async def _factory(self, monkeypatch, allow_network: str) -> SandboxBackendFactory:
        from app.agent_loop_lib.sandbox.manager import SandboxType
        from app.agents.agent_loop.sandbox_bridge import build_coding_sandbox_manager

        monkeypatch.setenv("SANDBOX_MODE", "boxd")
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", allow_network)
        manager = await build_coding_sandbox_manager()
        return manager._factories[SandboxType.CODING].backend_factory

    async def test_network_off_reaches_the_machine(self, monkeypatch) -> None:
        factory = await self._factory(monkeypatch, "false")
        assert factory.config.allow_network is False
        assert factory.capabilities().supports_network is False
        assert factory.create(SandboxContext())._allow_network is False

    async def test_network_on_reaches_the_machine(self, monkeypatch) -> None:
        factory = await self._factory(monkeypatch, "true")
        assert factory.config.allow_network is True

    async def test_the_key_never_enters_settings(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding.settings import EnvSandboxSettingsLoader

        monkeypatch.setenv("SANDBOX_MODE", "boxd")
        monkeypatch.setenv("BOXD_API_KEY", "do-not-leak-key")
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert "do-not-leak-key" not in settings.model_dump_json()
        assert "do-not-leak-key" not in repr(settings)

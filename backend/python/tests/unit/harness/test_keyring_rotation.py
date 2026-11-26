"""ADR-KEY-01 on the Python side (KP-1..KP-12): the ``SECRET_KEY`` ring, the platform cipher, the egress sealers and
the egress state step of the rotation. Every key is generated here."""

from __future__ import annotations

import errno
import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox import egress_credentials, egress_rekey, egress_state
from app.harness.sandbox.egress_rekey import RekeyReport, main, rekey
from app.utils.encryption import encryption_service as encryption_module
from app.utils.encryption.encryption_service import DecryptionError, EncryptionService
from app.utils.encryption.secret_key_ring import (
    SecretKeyRing,
    SecretKeyRingError,
    deployment_ring,
    hashed_secret,
    platform_cipher_key,
    platform_read_keys,
    secret_key_ring,
)
from harness_core.sandbox.egress import EgressBinding
from harness_core.sandbox.egress.errors import TokenUnavailable
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
    StateUnverifiable,
)
from harness_core.sandbox.egress.types import IssuedToken
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.sandbox.egress.ports import EgressStateStore

_LOG = logging.getLogger("test_keyring")


def _secret() -> str:
    return secrets.token_hex(32)


@pytest.fixture
def ring_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    old, new = _secret(), _secret()
    monkeypatch.setenv("SECRET_KEY", new)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", old)
    monkeypatch.delenv("SECRET_KEY_NEXT", raising=False)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    yield old, new
    EncryptionService._instance = None  # pyright: ignore[reportPrivateUsage]


# --- the ring --------------------------------------------------------------------------------------------------


def test_kp1_the_ring_reads_like_nodes_and_fails_closed_naming_no_value() -> None:
    current, other = _secret(), _secret()
    assert secret_key_ring({"SECRET_KEY": current}) == SecretKeyRing(current)
    previous = secret_key_ring({"SECRET_KEY": current, "SECRET_KEY_PREVIOUS": other})
    assert (previous.other, previous.other_role, previous.secrets) == (other, "previous", (current, other))
    assert secret_key_ring({"SECRET_KEY": current, "SECRET_KEY_NEXT": other}).other_role == "next"
    assert previous.hashed() == (hashed_secret(current), hashed_secret(other))
    assert previous.raw() == (current.encode(), (other.encode(),))

    def refused(env: dict[str, str]) -> str:
        with pytest.raises(SecretKeyRingError) as caught:
            _ = secret_key_ring(env)
        assert current not in str(caught.value)
        return str(caught.value)

    assert refused({}) == "SECRET_KEY environment variable is required"
    assert "never both" in refused({"SECRET_KEY": current, "SECRET_KEY_PREVIOUS": other, "SECRET_KEY_NEXT": _secret()})
    assert refused({"SECRET_KEY": current, "SECRET_KEY_PREVIOUS": current}) == (
        "SECRET_KEY_PREVIOUS is the same as SECRET_KEY: a rotation needs a new key"
    )
    assert refused({"SECRET_KEY": current, "SECRET_KEY_NEXT": current}).startswith("SECRET_KEY_NEXT is the same")


def test_kp2_deployment_ring_and_read_keys(monkeypatch: pytest.MonkeyPatch, ring_env: tuple[str, str]) -> None:
    old, new = ring_env
    # SEC-D-05: per secret its HKDF key, then the SHA-256 key it replaces; the writer's HKDF key first, asked for by
    # either of the deployment's own keys.
    ring = (platform_cipher_key(new), hashed_secret(new), platform_cipher_key(old), hashed_secret(old))
    assert platform_read_keys(platform_cipher_key(new)) == ring
    assert platform_read_keys(hashed_secret(new)) == ring
    assert platform_read_keys(hashed_secret(old)) == (hashed_secret(old),)
    assert platform_read_keys("ab" * 32) == ("ab" * 32,)
    monkeypatch.setenv("SECRET_KEY_NEXT", _secret())
    assert platform_read_keys(hashed_secret(new)) == (hashed_secret(new),)
    with pytest.raises(SecretKeyRingError):
        _ = deployment_ring()
    monkeypatch.delenv("SECRET_KEY")
    assert deployment_ring() is None


# --- readers meet data under the previous key -----------------------------------------------------------------


def test_kp3_the_platform_cipher_reads_either_key_and_writes_the_current_one(ring_env: tuple[str, str]) -> None:
    old, new = ring_env
    sealed = EncryptionService("aes-256-gcm", hashed_secret(old), _LOG).encrypt("value")
    platform = EncryptionService.get_instance("aes-256-gcm", hashed_secret(new), _LOG)
    assert platform.decrypt_detailed(sealed) == ("value", False)
    rewritten = platform.encrypt("value")
    assert platform.decrypt_detailed(rewritten) == ("value", True)
    with pytest.raises(DecryptionError):
        _ = EncryptionService("aes-256-gcm", hashed_secret(old), _LOG).decrypt(rewritten)
    with pytest.raises(DecryptionError):
        _ = EncryptionService("aes-256-gcm", hashed_secret(_secret()), _LOG).decrypt(sealed)
    assert encryption_module.EncryptionService is EncryptionService


def test_kp4_egress_sealers_open_records_of_either_key(ring_env: tuple[str, str]) -> None:
    old, new = ring_env
    state = egress_state.state_sealer()
    creds = egress_credentials.credential_sealer()
    assert state is not None and creds is not None
    before = StateSealer(old.encode()).seal("binding/x", b"body")
    assert state.open_detailed("binding/x", before) == (b"body", False)
    assert state.open_detailed("binding/x", state.seal("binding/x", b"body")) == (b"body", True)
    with pytest.raises(StateUnverifiable):
        _ = StateSealer(old.encode()).open("binding/x", state.seal("binding/x", b"body"))
    ref = CredentialSealer(old.encode()).seal("ph-1", "db-password")
    assert creds.open_detailed("ph-1", ref) == ("db-password", False)
    assert creds.open(TokenRoute("ph-1", "db", ref)) == "db-password"
    with pytest.raises(TokenUnavailable):
        _ = CredentialSealer(new.encode()).open(TokenRoute("ph-1", "db", ref))
    with pytest.raises(ValueError, match="empty"):
        _ = StateSealer(new.encode(), accepted=(b"",))


# --- the egress state step ------------------------------------------------------------------------------------

_DEST = EgressDestination("db.internal", (5432,))


async def _proxy_state(old: str, clock: ManualClock) -> tuple[InMemoryEgressStateStore, int]:
    """A proxy that ran under the old key: two bindings (one with a sealed route), a grant, a stop."""
    store = InMemoryEgressStateStore(clock)
    bindings = DurableEgressBindings(store, StateSealer(old.encode()), clock)
    sealed = CredentialSealer(old.encode()).seal("pxh-ph-1", "db-password")
    policy = EgressPolicy(destinations=(_DEST,), token_routes=(TokenRoute("pxh-ph-1", "db.internal", sealed),))
    await bindings.register(EgressBinding("sbx-1", "10.0.0.2", "10.0.0.3", policy, {"runId": "r1"}))
    plain = EgressPolicy(destinations=(_DEST,), token_routes=(TokenRoute("pxh-ph-2", "model-gateway", "m"),))
    await bindings.register(EgressBinding("sbx-2", "10.0.1.2", "10.0.1.3", plain, {"runId": "r2"}))
    await bindings.record_grant("sbx-2", "m", IssuedToken("tok", clock.now().replace(year=clock.now().year + 1)))
    _ = await store.put_if_absent("instance", "i-1")
    return store, len(await store.entries()) - 1


def _sealers(old: str, new: str) -> tuple[StateSealer, CredentialSealer]:
    return StateSealer(new.encode(), accepted=(old.encode(),)), CredentialSealer(new.encode(), accepted=(old.encode(),))


async def test_kp5_dry_run_counts_apply_reseals_and_a_proxy_on_the_new_key_alone_restores_everything() -> None:
    old, new = _secret(), _secret()
    clock = ManualClock()
    store, records = await _proxy_state(old, clock)
    state, creds = _sealers(old, new)
    before = await store.entries()
    dry = await rekey(store, state, creds, apply=False)
    assert dry == RekeyReport("egress-state", total=records, previous=records, draining=1)
    assert await store.entries() == before
    applied = await rekey(store, state, creds, apply=True)
    assert (applied.moved, applied.current, applied.previous, applied.draining) == (records, records, 0, 1)
    after = await rekey(store, state, creds, apply=False)
    assert (after.current, after.previous, after.moved) == (records, 0, 0)
    restored = await DurableEgressBindings(store, StateSealer(new.encode()), clock).load("i-2")
    assert (restored.bindings, restored.grants, restored.unverified) == (2, 1, 0)


async def test_kp6_a_crash_mid_step_then_a_resume_moves_the_rest_once() -> None:
    old, new = _secret(), _secret()
    clock = ManualClock()
    store, records = await _proxy_state(old, clock)
    state, creds = _sealers(old, new)
    real = store.replace
    calls = 0

    async def dies_after_one(key: str, expected: str, value: str) -> bool:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ConnectionError("redis went away")
        return await real(key, expected, value)

    store.replace = dies_after_one  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ConnectionError):
        _ = await rekey(store, state, creds, apply=True)
    store.replace = real  # pyright: ignore[reportAttributeAccessIssue]
    resumed = await rekey(store, state, creds, apply=True)
    assert (resumed.moved, resumed.current, resumed.previous) == (records - 1, records, 0)


async def test_kp7_a_record_the_proxy_rewrote_meanwhile_is_left_as_the_proxy_wrote_it() -> None:
    old, new = _secret(), _secret()
    clock = ManualClock()
    store, records = await _proxy_state(old, clock)
    state, creds = _sealers(old, new)
    real = store.replace

    async def proxy_writes_first(key: str, expected: str, value: str) -> bool:
        # An old proxy (still on the old key) renews the record between the read and the write.
        await store.put(key, StateSealer(old.encode()).seal(key, state.open(key, expected)), ttl_s=60)
        return await real(key, expected, value)

    store.replace = proxy_writes_first  # pyright: ignore[reportAttributeAccessIssue]
    raced = await rekey(store, state, creds, apply=True)
    assert (raced.moved, raced.previous) == (0, records)
    store.replace = real  # pyright: ignore[reportAttributeAccessIssue]
    assert (await rekey(store, state, creds, apply=True)).previous == 0


async def test_kp8_a_wrong_previous_key_reads_as_unreadable_and_nothing_is_written() -> None:
    old, new = _secret(), _secret()
    clock = ManualClock()
    store, records = await _proxy_state(old, clock)
    before = await store.entries()
    report = await rekey(store, *_sealers(_secret(), new), apply=True)
    assert (report.unreadable, report.moved) == (records, 0)
    assert await store.entries() == before


async def test_kp9_a_damaged_binding_body_counts_no_route_and_an_unopenable_route_drains() -> None:
    new = _secret()
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    state, creds = _sealers(_secret(), new)
    await store.put("binding/bad", state.seal("binding/bad", b"not json"), ttl_s=60)
    await store.put("binding/odd", state.seal("binding/odd", b'{"binding": 3}'), ttl_s=60)
    strange = CredentialSealer(_secret().encode()).seal("pxh-ph-9", "x")
    policy = EgressPolicy(destinations=(_DEST,), token_routes=(TokenRoute("pxh-ph-9", "db.internal", strange),))
    bindings = DurableEgressBindings(store, state, clock)
    await bindings.register(EgressBinding("sbx-9", "10.0.9.2", "10.0.9.3", policy, {}))
    report = await rekey(store, state, creds, apply=False)
    assert (report.total, report.current, report.draining) == (3, 3, 1)


async def test_kp10_main_prints_counts_only(capsys: pytest.CaptureFixture[str]) -> None:
    old, new = _secret(), _secret()
    clock = ManualClock()
    store, records = await _proxy_state(old, clock)

    async def build(_log: logging.Logger) -> tuple[EgressStateStore, StateSealer, CredentialSealer]:
        return (store, *_sealers(old, new))

    assert await main(["--apply"], build) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {
        "step": "egress-state",
        "total": records,
        "current": records,
        "previous": 0,
        "moved": records,
        "unreadable": 0,
        "draining": 1,
    }
    assert old not in out and new not in out


async def test_kp11_main_reports_a_failure_by_our_message_or_by_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    async def misconfigured(_log: logging.Logger) -> tuple[EgressStateStore, StateSealer, CredentialSealer]:
        raise SecretKeyRingError("SECRET_KEY_PREVIOUS is the same as SECRET_KEY: a rotation needs a new key")

    async def leaky(_log: logging.Logger) -> tuple[EgressStateStore, StateSealer, CredentialSealer]:
        raise RuntimeError("redis://user:hunter2@host")

    assert await main([], misconfigured) == 1
    assert json.loads(capsys.readouterr().out)["error"].startswith("SECRET_KEY_PREVIOUS is the same")
    assert await main([], leaky) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"step": "egress-state", "error": "RuntimeError"}
    assert "hunter2" not in out


def test_kp12_the_module_takes_only_apply() -> None:
    assert egress_rekey.parse_apply([]) is False
    assert egress_rekey.parse_apply(["--apply"]) is True
    with pytest.raises(SystemExit):
        _ = egress_rekey.parse_apply(["--retire"])


async def test_keyring_fu1_an_os_error_names_its_call_errno_and_kind_of_path_never_the_path(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    secret_dir = tmp_path / "hunter2-folder"
    _ = secret_dir.write_text("x", encoding="utf-8")

    async def read_only(_log: logging.Logger) -> tuple[EgressStateStore, StateSealer, CredentialSealer]:
        os.makedirs(secret_dir / "logs")
        raise AssertionError("unreachable")

    assert await main([], read_only) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"step": "egress-state", "error": "NotADirectoryError ENOTDIR in os.makedirs on an absolute path"}
    assert "hunter2" not in out
    relative = PermissionError(errno.EACCES, "Permission denied", "logs")
    assert egress_rekey.describe_error(relative) == "PermissionError EACCES in an operation on a path under the working directory"


def test_keyring_fu1_the_step_runs_from_a_read_only_working_directory(tmp_path: Path) -> None:
    """KEYRING-FU-1: on the lane the code mount is read-only, and the shared logger made ``logs/`` in the working
    directory at import, so the step died before it read a record. Logging goes to the console there instead."""
    cwd = tmp_path / "ro"
    cwd.mkdir()
    cwd.chmod(0o555)
    if os.access(cwd, os.W_OK):  # pragma: no cover - root ignores the mode
        cwd.chmod(0o755)
        pytest.skip("running as root: a mode cannot make the folder read-only")
    root = Path(__file__).resolve().parents[3]
    probe = (
        "import app.config.key_value_store_factory, app.config.configuration_service, "
        "app.harness.sandbox.egress_state, app.harness.sandbox.egress_credentials; print('imported')"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=cwd,
            env={**os.environ, "PYTHONPATH": str(root)},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    finally:
        cwd.chmod(0o755)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().endswith("imported")
    assert "file logging off, the log folder under the working directory is not writable (EACCES)" in done.stderr
    assert done.stderr.count("file logging off") == 1, "said once, not per logger"
    assert not (cwd / "logs").exists()


def _rotation_check():  # noqa: ANN202 - a module loaded by path
    import importlib.util

    path = Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "rotation_check.py"
    spec = importlib.util.spec_from_file_location("rotation_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(mode: str, **steps: tuple[int, int, int, int]) -> str:
    rows = [
        {"step": name, "total": total, "current": total - previous, "previous": previous, "moved": moved,
         "unreadable": unreadable, "draining": 0}
        for name, (total, previous, moved, unreadable) in steps.items()
    ]
    return "noise before " + json.dumps({"event": "result", "mode": mode, "ok": True, "refused": None, "steps": rows})


def _runbook_log(*, data_keys_moved: int = 1, left: int = 0) -> str:
    lines = [
        _result("dry-run", **{"data-keys": (1, 1, 0, 0), "config-store": (36, 32, 0, 0)}),
        _result("rotate", **{"data-keys": (1, 0, data_keys_moved, 0), "config-store": (36, 0, 32, 0)}),
        _result("verify", **{"data-keys": (1, 0, 0, 0), "config-store": (36, 0, 0, 0)}),
        _result("retire", **{"data-keys": (1, 0, 0, 0), "config-store": (36, 0, 0, 0)}),
        _result("verify", **{"data-keys": (1, 0, 0, 0), "config-store": (36, left, 0, 0)}),
    ]
    return "\n".join(lines)


def test_keyring_fu1_the_lanes_rotation_check_passes_a_whole_rotation_only() -> None:
    check = _rotation_check()
    assert check.findings(check.results(_runbook_log())) == []
    assert any("no org data key" in f for f in check.findings(check.results(_runbook_log(data_keys_moved=0))))
    assert any("still under the previous key" in f for f in check.findings(check.results(_runbook_log(left=2))))
    assert check.findings(check.results(_runbook_log().rsplit("\n", 1)[0]))[0].startswith("the commands ran as")


def test_keyring_fu1_the_step_prints_only_its_report_on_stdout_whatever_logs_after_it(tmp_path: Path) -> None:
    """The Node step reads stdout's last line. A logger bound to stdout that writes after the report (the config
    cache's listener did on the lane) must land on stderr, and the report alone on stdout."""
    script = tmp_path / "late_log.py"
    _ = script.write_text(
        "import logging, sys\n"
        "from app.harness.sandbox import egress_rekey\n"
        "late = logging.getLogger('late'); late.addHandler(logging.StreamHandler(sys.stdout)); late.propagate = False\n"
        "late.setLevel(logging.INFO)\n"
        "async def build(_log):\n"
        "    late.info('a log line before the report')\n"
        "    raise RuntimeError('the store is down')\n"
        "egress_rekey.deployment_state = build\n"
        "code = egress_rekey.cli([])\n"
        "late.info('a log line after the report')\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[3]
    done = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 1, done.stderr[-2000:]
    assert [json.loads(line) for line in done.stdout.splitlines()] == [{"step": "egress-state", "error": "RuntimeError"}]
    assert "a log line before the report" in done.stderr
    assert "a log line after the report" in done.stderr

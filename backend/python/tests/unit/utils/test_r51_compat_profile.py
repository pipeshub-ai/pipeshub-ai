"""R51-COMPAT (ADR-OPS-07): the Python side of the security profile.

``PIPESHUB_HARDENED`` unset is the compatibility profile: an install upgraded from a release without the security
programme keeps that release's behaviour (a weak SECRET_KEY, values sealed under the SHA-256 key it reads, a flag value
it read leniently, Kafka without SASL, a Docker API proxy without credentials), each with a warning. ``true`` keeps the
hardened refusals. The suite runs hardened (``tests/conftest.py``); these tests delete the variable for their duration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.sandbox.docker_proxy import (
    ENV_BROKER_CALLER_TOKEN,
    ENV_CALLER_TOKEN,
    DockerApiPolicy,
    DockerSocketProxy,
    anonymous_caller,
    proxy_callers,
)
from app.services.messaging.kafka.config.kafka_config import kafka_security_kwargs
from app.utils import env_flags
from app.utils.encryption.encryption_service import EncryptionService
from app.utils.encryption.secret_key_ring import (
    SecretKeyRingError,
    hashed_secret,
    platform_cipher_key,
    secret_key_ring,
)
from app.utils.env_flags import (
    LEGACY_UNRECOGNISED,
    PIPESHUB_ENABLE_CODE_EXECUTION,
    PIPESHUB_ENABLE_SKILLS,
    SANDBOX_ALLOW_NETWORK,
    InvalidEnvFlag,
    env_flag,
    validate_env_flags,
)
from app.utils.security_profile import hardened_profile, reset_compat_warnings_for_tests

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REPO = Path(__file__).resolve().parents[5]
PROFILE_CASES = json.loads((REPO / "contracts" / "secret-key" / "profile-cases.json").read_text(encoding="utf-8"))[
    "cases"
]
STRONG = hashlib.sha256(b"r51-compat strong").hexdigest()
WEAK = "your_random_encryption_secret_key"
TOKEN = hashlib.sha256(b"r51-compat proxy token").hexdigest()


def _legacy() -> DockerApiPolicy:
    return DockerApiPolicy(allowed_images=frozenset({"pipeshubai/pipeshub-sandbox:test"}), allowed_networks=frozenset())


@pytest.fixture
def compat(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("PIPESHUB_HARDENED", raising=False)
    reset_compat_warnings_for_tests()
    yield
    reset_compat_warnings_for_tests()


# -- the switch -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "hardened"),
    [(None, False), ("", False), ("false", False), ("0", False), ("true", True), ("1", True), ("YES", True),
     ("on", True), ("maybe", False)],
)
def test_the_switch_reads_compatibility_unless_true(raw: str | None, hardened: bool) -> None:
    reset_compat_warnings_for_tests()
    env = {} if raw is None else {"PIPESHUB_HARDENED": raw}
    assert hardened_profile(env) is hardened


def test_a_switch_that_is_not_a_boolean_warns_and_never_stops(caplog: pytest.LogCaptureFixture) -> None:
    reset_compat_warnings_for_tests()
    with caplog.at_level(logging.WARNING):
        assert hardened_profile({"PIPESHUB_HARDENED": "strict"}) is False
    assert "running the compatibility profile" in caplog.text


# -- item 2: SECRET_KEY ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", PROFILE_CASES, ids=[c["name"] for c in PROFILE_CASES])
def test_shared_profile_cases(case: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    reset_compat_warnings_for_tests()
    monkeypatch.delenv("PIPESHUB_HARDENED", raising=False)
    monkeypatch.delenv("SECRET_KEY_KDF", raising=False)
    env = dict(case["env"])  # type: ignore[arg-type]
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    secret = STRONG if case["secret"] == "strong" else WEAK
    env["SECRET_KEY"] = secret
    if case["refused"]:
        with pytest.raises(SecretKeyRingError):
            secret_key_ring(env)
        return
    writer = secret_key_ring(env).cipher_keys(env)[0]
    expected = hashed_secret(secret) if case["writer"] == "sha256" else platform_cipher_key(secret)
    assert writer == expected


def test_compatibility_keeps_a_weak_key_with_a_warning_never_the_value(
    compat: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        ring = secret_key_ring({"SECRET_KEY": WEAK})
    assert ring.current == WEAK
    assert "SECRET_KEY is a published placeholder" in caplog.text
    assert "compatibility" in caplog.text
    assert WEAK not in caplog.text


def test_compatibility_writes_what_the_previous_release_reads_and_reads_both(
    compat: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback safety: a value written after the upgrade opens under upstream's SHA-256 key, and a value the hardened
    profile wrote (HKDF) still opens."""
    monkeypatch.setenv("SECRET_KEY", STRONG)
    monkeypatch.delenv("SECRET_KEY_KDF", raising=False)
    keys = secret_key_ring().cipher_keys()
    assert keys[0] == hashed_secret(STRONG)
    assert platform_cipher_key(STRONG) in keys
    logger = logging.getLogger("test")
    ours = EncryptionService("aes-256-gcm", keys[0], logger, keys)
    upstream = EncryptionService("aes-256-gcm", hashed_secret(STRONG), logger)
    hardened_writer = EncryptionService("aes-256-gcm", platform_cipher_key(STRONG), logger)
    assert upstream.decrypt(ours.encrypt("written after the upgrade")) == "written after the upgrade"
    assert ours.decrypt(hardened_writer.encrypt("written hardened")) == "written hardened"


def test_hardened_refuses_a_weak_key() -> None:
    with pytest.raises(SecretKeyRingError):
        secret_key_ring({"SECRET_KEY": WEAK})


# -- item 12: boolean flags -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "reading"),
    [(SANDBOX_ALLOW_NETWORK, True), (PIPESHUB_ENABLE_SKILLS, False), (PIPESHUB_ENABLE_CODE_EXECUTION, True)],
)
def test_compatibility_reads_an_unrecognised_value_as_the_previous_release_did(
    compat: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, flag: str, reading: bool
) -> None:
    monkeypatch.setenv(flag, "enabled")
    with caplog.at_level(logging.WARNING):
        validate_env_flags()
        assert env_flag(flag) is reading
    assert flag in caplog.text


def test_compatibility_reads_a_new_flag_s_typo_as_its_default(compat: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "ture")
    validate_env_flags()
    assert env_flag("HARNESS_FLOW_GUARD") is True


def test_compatibility_still_reads_the_accepted_spellings(compat: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for raw, value in (("1", True), ("yes", True), ("True", True), ("OFF", False), ("0", False)):
        monkeypatch.setenv(PIPESHUB_ENABLE_SKILLS, raw)
        assert env_flag(PIPESHUB_ENABLE_SKILLS) is value


def test_hardened_refuses_an_unrecognised_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPESHUB_ENABLE_SKILLS, "enabled")
    with pytest.raises(InvalidEnvFlag):
        validate_env_flags()


def test_every_legacy_reading_names_a_flag() -> None:
    assert set(LEGACY_UNRECOGNISED) <= set(env_flags.ENV_FLAG_DEFAULTS)


# -- Kafka without KAFKA_PASSWORD ------------------------------------------------------------------------------------

_SASL_NO_PASSWORD = {"username": "pipeshub", "password": "", "mechanism": "scram-sha-512"}


def test_compatibility_connects_to_kafka_without_sasl_when_no_password(compat: None) -> None:
    assert kafka_security_kwargs(ssl_enabled=False, sasl=_SASL_NO_PASSWORD) == {}


def test_hardened_refuses_kafka_sasl_without_a_password() -> None:
    with pytest.raises(ValueError, match="no password"):
        kafka_security_kwargs(ssl_enabled=False, sasl=_SASL_NO_PASSWORD)


def test_tls_kafka_without_a_password_is_refused_in_both_profiles(compat: None) -> None:
    # A release before SEC-D-06 refused this too: MSK's SASL_SSL needs the password.
    with pytest.raises(ValueError, match="no password"):
        kafka_security_kwargs(ssl_enabled=True, sasl=_SASL_NO_PASSWORD)


# -- item 5: the Docker API proxy without credentials ---------------------------------------------------------------


def test_compatibility_proxy_starts_with_no_credential_and_admits_the_legacy_executor(
    compat: None, caplog: pytest.LogCaptureFixture
) -> None:
    env = {"SANDBOX_INSTALL_ID": "pxh-test"}
    assert proxy_callers(env) == {}
    with caplog.at_level(logging.WARNING):
        anonymous = anonymous_caller(env)
    assert isinstance(anonymous, DockerApiPolicy)
    assert "./install.sh --upgrade" in caplog.text


def test_a_configured_credential_admits_nobody_without_one(compat: None) -> None:
    assert anonymous_caller({ENV_CALLER_TOKEN: TOKEN}) is None
    assert anonymous_caller({ENV_BROKER_CALLER_TOKEN: TOKEN}) is None
    with pytest.raises(ValueError, match="admits no caller without one"):
        DockerSocketProxy(None, "/nonexistent", callers={TOKEN: _legacy()}, anonymous=_legacy())


def test_hardened_proxy_refuses_to_start_without_a_credential() -> None:
    assert anonymous_caller({}) is None
    with pytest.raises(RuntimeError, match="has no caller"):
        proxy_callers({})


class _Daemon:
    def __init__(self) -> None:
        self.seen: list[bytes] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{}")
        await writer.drain()
        writer.close()


@pytest.fixture
async def anonymous_proxy() -> AsyncIterator[tuple[_Daemon, int]]:
    daemon = _Daemon()
    sock_dir = tempfile.mkdtemp(prefix="r51proxy-", dir="/tmp")
    sock = f"{sock_dir}/d.sock"
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(None, sock, anonymous=_legacy())
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    try:
        yield daemon, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        upstream.close()
        shutil.rmtree(sock_dir, ignore_errors=True)


async def test_a_compatibility_proxy_forwards_a_request_without_a_credential(
    anonymous_proxy: tuple[_Daemon, int],
) -> None:
    daemon, port = anonymous_proxy
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /v1.43/version HTTP/1.1\r\nHost: d\r\n\r\n")
    await writer.drain()
    answer = await reader.read()
    writer.close()
    assert answer.startswith(b"HTTP/1.1 200")
    assert daemon.seen


class _FakeClient:
    def __init__(self) -> None:
        self.api = type("Api", (), {"headers": {}})()


def _docker_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    import docker

    from app.harness.sandbox.docker_client import proxied_docker_client

    monkeypatch.setattr(docker, "from_env", _FakeClient)
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
    return proxied_docker_client()


def test_compatibility_executor_calls_the_proxy_without_a_credential_when_none_is_set(
    compat: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_CALLER_TOKEN, raising=False)
    assert "Authorization" not in _docker_client(monkeypatch).api.headers


def test_a_configured_executor_credential_is_always_sent(compat: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CALLER_TOKEN, TOKEN)
    assert _docker_client(monkeypatch).api.headers["Authorization"] == f"Bearer {TOKEN}"


def test_hardened_executor_refuses_to_run_without_its_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_CALLER_TOKEN, raising=False)
    with pytest.raises(RuntimeError, match="is not set"):
        _docker_client(monkeypatch)

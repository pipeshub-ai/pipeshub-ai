"""SEC-C-10 (ADR-CRED-01) and SEC-D-03 (ADR-KEY-02): machine credentials by role, and the sandbox plane's own keys.

The egress proxy and the sandbox broker hold no SECRET_KEY: they open only the document Node seals for them, which
holds their own credential and digests of the credentials they admit; they log in to Redis as their own ACL user.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from app.harness.credential_roles import (
    ComponentCredentials,
    PlatformRoleVerifier,
    RoleCredentialAuth,
    component_server_auth,
    listener_auth,
    process_holder,
    scrape_only_auth,
    seal_component_document,
    worker_client_auth,
)
from app.harness.journal_node.credentials import (
    ConfiguredWorkerCredential,
    WorkerCredentialUnavailable,
)
from app.utils.encryption.component_key import (
    ComponentKeyError,
    component_acl_rules,
    component_cipher_key,
    component_identity,
    component_redis_login,
    component_subkey,
    process_cipher_key,
)
from app.utils.encryption.encryption_service import EncryptionService
from app.utils.encryption.secret_key_ring import platform_cipher_key
from harness_core.sandbox.egress.aead_ring import derived_key
from harness_core.sandbox.egress.sealed import CREDENTIAL_KEY_LABEL, CredentialSealer
from harness_core.sandbox.egress.state import STATE_KEY_LABEL, StateSealer

REPO = Path(__file__).resolve().parents[5]
CONTRACT = json.loads((REPO / "contracts" / "secret-key" / "component-acl.json").read_text(encoding="utf-8"))
PROXY_KEY = hashlib.sha256(b"proxy key").hexdigest()
SECRET = hashlib.sha256(b"component test secret").hexdigest()
WORKER, SCRAPE, BROKER, PROXY = "phw_worker", "phw_scrape", "phw_broker", "phw_proxy"
NOW_MS = 1_800_000_000_000.0


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Config:
    """``get_config`` over a document sealed the way Node seals it, opened with the component's key."""

    def __init__(self, documents: dict[str, object], cipher_key: str | None = None) -> None:
        self.documents = documents
        self.cipher_key = cipher_key
        self.down = False

    async def get_config(self, key: str, *args: object, **kwargs: object) -> object:
        if self.down:
            raise ConnectionError("store down")
        value = self.documents.get(key)
        if isinstance(value, str) and self.cipher_key is not None:
            return json.loads(EncryptionService("aes-256-gcm", self.cipher_key, logging.getLogger("t")).decrypt(value))
        return value


def _proxy_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "role": "egress-proxy",
        "credential": {"current": PROXY, "previous": None, "previousValidUntil": None, "rotateId": None},
        "accepts": {
            "worker": [{"digest": _digest(WORKER), "validUntil": None}, {"digest": _digest("phw_old"), "validUntil": NOW_MS - 1}],
            "sandbox-broker": [{"digest": _digest(BROKER), "validUntil": None}],
            "scrape": [{"digest": _digest(SCRAPE), "validUntil": None}, {"digest": _digest("phw_grace"), "validUntil": NOW_MS + 60_000}],
        },
        "egressSealing": {
            "state": [derived_key(SECRET.encode(), STATE_KEY_LABEL).hex()],
            "credentials": [derived_key(SECRET.encode(), CREDENTIAL_KEY_LABEL).hex()],
        },
    }
    document.update(overrides)
    return document


def _sealed_config(document: dict[str, object]) -> _Config:
    sealed = seal_component_document("egress-proxy", PROXY_KEY, document)
    return _Config({"/services/agentHarness/components/egressProxy": sealed}, component_cipher_key("egress-proxy", PROXY_KEY))


def _credentials(document: dict[str, object] | None = None) -> ComponentCredentials:
    return ComponentCredentials(_sealed_config(document or _proxy_document()), "egress-proxy", wall_ms=lambda: NOW_MS)  # type: ignore[arg-type]


def _auth(token: str, scheme: str = "Bearer") -> dict[str, str]:
    return {"Authorization": f"{scheme} {token}"}


# --- the shared vectors ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("component", ["egress-proxy", "sandbox-broker"])
def test_subkeys_match_node(component: str) -> None:
    key = CONTRACT["subkeys"]["key"]
    assert component_cipher_key(component, key) == CONTRACT["subkeys"][component]["config"]
    assert component_subkey(component, key, "redis").hex() == CONTRACT["subkeys"][component]["redis"]


@pytest.mark.parametrize("case", CONTRACT["acl"], ids=lambda c: f"{c['component']}-{c['namespace'] or 'none'}")
def test_acl_rules_match_node(case: dict[str, object]) -> None:
    assert component_acl_rules(str(case["component"]), str(case["namespace"]), str(case["kvPrefix"])) == case["rules"]


# --- the component document: role-scoped admission ------------------------------------------------------------------


async def test_the_proxy_presents_its_own_credential() -> None:
    assert await _credentials().worker_credential() == PROXY


async def test_the_proxy_admits_the_worker_and_broker_on_control_and_the_scraper_on_metrics_only() -> None:
    auth = component_server_auth(_sealed_config(_proxy_document()), "egress-proxy")  # type: ignore[arg-type]
    auth._verifier = _credentials()  # the same document, with a fixed wall clock
    assert await auth.verify(_auth(WORKER), path="/v1/bindings")
    assert await auth.verify(_auth(BROKER), path="/v1/bindings")
    assert not await auth.verify(_auth(SCRAPE), path="/v1/bindings")
    assert await auth.verify(_auth(SCRAPE), path="/metrics")
    assert not await auth.verify(_auth(WORKER), path="/metrics")
    assert not await auth.verify(_auth(PROXY), path="/v1/bindings"), "its own credential admits nothing here"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"bearer {WORKER}"},
        {"authorization": f"Bearer {WORKER}"},
        {"AUTHORIZATION": f"BEARER   {WORKER}"},
    ],
)
async def test_header_spelling_does_not_matter_for_the_right_role(headers: dict[str, str]) -> None:
    auth = RoleCredentialAuth(None, _credentials(), control=("worker",))
    assert await auth.verify(headers, path="/x")


@pytest.mark.parametrize(
    "token",
    [SCRAPE, WORKER.upper(), f"{WORKER}\x00", f"{WORKER}x", _digest(WORKER), "", "phw_old"],
)
async def test_bypass_variants_are_refused_on_control(token: str) -> None:
    auth = RoleCredentialAuth(None, _credentials(), control=("worker",))
    assert not await auth.verify(_auth(token) if token else {}, path="/v1/bindings")


async def test_a_previous_credential_works_through_its_grace_only() -> None:
    verifier = _credentials()
    assert await verifier.accepts(("scrape",), "phw_grace")
    assert not await verifier.accepts(("worker",), "phw_old")


async def test_basic_scheme_is_refused() -> None:
    auth = RoleCredentialAuth(None, _credentials(), control=("worker",))
    assert not await auth.verify(_auth(WORKER, "Basic"), path="/x")


async def test_a_tampered_document_opens_nothing() -> None:
    config = _sealed_config(_proxy_document())
    key = "/services/agentHarness/components/egressProxy"
    sealed = str(config.documents[key])
    config.documents[key] = sealed[:-2] + ("00" if not sealed.endswith("00") else "11")
    credentials = ComponentCredentials(config, "egress-proxy")  # type: ignore[arg-type]
    assert await credentials.document() == {}
    assert not await credentials.accepts(("worker",), WORKER)
    with pytest.raises(WorkerCredentialUnavailable):
        await credentials.worker_credential()


async def test_a_store_outage_serves_the_last_read_then_refuses() -> None:
    now = [0.0]
    config = _sealed_config(_proxy_document())
    credentials = ComponentCredentials(config, "egress-proxy", clock=lambda: now[0], wall_ms=lambda: NOW_MS, stale_limit_s=60)  # type: ignore[arg-type]
    assert await credentials.accepts(("worker",), WORKER)
    config.down = True
    now[0] = 30
    assert await credentials.accepts(("worker",), WORKER)
    now[0] = 61
    assert not await credentials.accepts(("worker",), WORKER)
    with pytest.raises(WorkerCredentialUnavailable):
        await credentials.worker_credential()


async def test_an_unprovisioned_document_refuses_everyone() -> None:
    credentials = ComponentCredentials(_Config({}), "sandbox-broker")  # type: ignore[arg-type]
    assert not await credentials.accepts(("worker",), WORKER)
    with pytest.raises(WorkerCredentialUnavailable):
        await credentials.worker_credential()


# --- platform processes: role sets in configuration -----------------------------------------------------------------


async def test_a_gateway_metrics_route_admits_only_the_scraper() -> None:
    config = _Config({
        "/services/agentHarness/workerCredential": {"current": WORKER},
        "/services/agentHarness/roleCredentials/scrape": {"current": SCRAPE},
    })
    auth = scrape_only_auth(config)  # type: ignore[arg-type]
    assert await auth.verify(_auth(SCRAPE), path="/metrics")
    assert not await auth.verify(_auth(WORKER), path="/metrics")
    assert not await auth.verify(_auth(SCRAPE), path="/other")


async def test_platform_verifier_reads_each_role_set() -> None:
    config = _Config({
        "/services/agentHarness/workerCredential": {"current": WORKER},
        "/services/agentHarness/roleCredentials/sandboxBroker": {"current": BROKER},
    })
    verifier = PlatformRoleVerifier(config)  # type: ignore[arg-type]
    assert await verifier.accepts(("worker", "sandbox-broker"), BROKER)
    assert not await verifier.accepts(("worker",), BROKER)


async def test_a_client_presents_and_admits_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPESHUB_COMPONENT", raising=False)
    config = _Config({"/services/agentHarness/workerCredential": {"current": WORKER}})
    auth = worker_client_auth(config)  # type: ignore[arg-type]
    assert await auth.headers() == {"Authorization": f"Bearer {WORKER}"}
    assert not await auth.verify(_auth(WORKER), path="/x")
    assert isinstance(process_holder(config), ConfiguredWorkerCredential)  # type: ignore[arg-type]


# --- the component's own key ----------------------------------------------------------------------------------------


def _component_env(**extra: str) -> dict[str, str]:
    return {"PIPESHUB_COMPONENT": "egress-proxy", "PIPESHUB_COMPONENT_KEY": PROXY_KEY, **extra}


def test_identity_off_the_sandbox_plane() -> None:
    assert component_identity({}) is None


def test_a_component_refuses_to_start_beside_secret_key() -> None:
    with pytest.raises(ComponentKeyError, match="must not hold SECRET_KEY") as caught:
        component_identity(_component_env(SECRET_KEY=SECRET))
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("key", ["", "changeme", "a" * 64, f" {PROXY_KEY}"])
def test_a_weak_or_missing_component_key_is_refused(key: str) -> None:
    with pytest.raises(ComponentKeyError) as caught:
        component_identity(_component_env(PIPESHUB_COMPONENT_KEY=key))
    if key.strip():
        assert key.strip() not in str(caught.value)


def test_an_unknown_component_is_refused() -> None:
    with pytest.raises(ComponentKeyError, match="PIPESHUB_COMPONENT must be one of"):
        component_identity(_component_env(PIPESHUB_COMPONENT="pipeshub-ai"))


def test_the_key_can_come_from_a_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "key"
    secret_file.write_text(PROXY_KEY + "\n", encoding="utf-8")
    env = {"PIPESHUB_COMPONENT": "egress-proxy", "PIPESHUB_COMPONENT_KEY_FILE": str(secret_file)}
    assert component_identity(env) == ("egress-proxy", PROXY_KEY)
    with pytest.raises(ComponentKeyError, match="cannot be read"):
        component_identity({**env, "PIPESHUB_COMPONENT_KEY_FILE": str(tmp_path / "missing")})


def test_the_component_cipher_cannot_open_a_configuration_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _component_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    key = process_cipher_key()
    assert key == component_cipher_key("egress-proxy", PROXY_KEY)
    stored = EncryptionService("aes-256-gcm", platform_cipher_key(SECRET), logging.getLogger("t")).encrypt("model key")
    with pytest.raises(Exception):  # noqa: B017 - DecryptionError
        EncryptionService("aes-256-gcm", key, logging.getLogger("t")).decrypt(stored)


def test_the_component_logs_in_to_redis_as_its_own_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.redis.config import RedisConnectionConfig

    for name, value in _component_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("REDIS_PASSWORD", "the-default-users-password")
    config = RedisConnectionConfig.from_host_port(host="redis", port=6379, password="the-default-users-password")
    username, password = component_redis_login()  # type: ignore[misc]
    assert (config.username, config.password) == (username, password)
    assert username.startswith("pipeshub-egress-proxy-")


# --- egress sealing keys: the proxy opens what the worker sealed, holding only the derived keys ----------------------


def test_derived_sealers_open_what_the_secret_sealed_and_the_reverse() -> None:
    by_secret = CredentialSealer(SECRET.encode())
    by_keys = CredentialSealer.of_keys([derived_key(SECRET.encode(), CREDENTIAL_KEY_LABEL)])
    sealed = by_secret.seal("ph_x", "db-password")
    assert by_keys.open_detailed("ph_x", sealed) == ("db-password", True)
    assert by_secret.open_detailed("ph_x", by_keys.seal("ph_x", "v")) == ("v", True)
    state_secret, state_keys = StateSealer(SECRET.encode()), StateSealer.of_keys([derived_key(SECRET.encode(), STATE_KEY_LABEL)])
    assert state_keys.open("binding:1", state_secret.seal("binding:1", b"{}")) == b"{}"


def test_a_derived_key_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialSealer.of_keys([b"short"])


async def test_listener_auth_on_the_sandbox_plane_uses_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _component_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    auth = listener_auth(_sealed_config(_proxy_document()), "egress-proxy")  # type: ignore[arg-type]
    assert isinstance(auth._verifier, ComponentCredentials)
    assert await auth.headers() == {"Authorization": f"Bearer {PROXY}"}


async def test_a_component_reads_only_its_document_and_the_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every other key is answered as absent without asking Redis, whose ACL would refuse it (ADR-KEY-02)."""
    from app.config.configuration_service import ConfigurationService

    for name, value in _component_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    asked: list[str] = []

    class _Store:
        async def get_key(self, key: str, **_: object) -> object:
            asked.append(key)
            return None

    service = ConfigurationService(logging.getLogger("t"), _Store())
    monkeypatch.setattr(service, "_start_watch", lambda: None, raising=False)
    assert await service.get_config("/services/redis", default={"fallback": True}) == {"fallback": True}
    assert await service.get_config("/services/metricsCollection") is None
    await service.get_config("/services/endpoints")
    await service.get_config("/services/agentHarness/components/egressProxy")
    # R38IDENT-FU-10: the endpoints are read from the component's own sealed document, never the plain one.
    doc = "/services/agentHarness/components/egressProxy"
    assert asked == [doc, doc]
    # The cache watch's startup check of Node's etcd-to-Redis flag is a read outside the component's ACL too: Redis
    # refused it with NOPERM on every proxy and broker start (R38-IDENT lane).
    assert await service._migration_completed("/migrations/etcd_to_redis") is False  # pyright: ignore[reportPrivateUsage]
    assert asked == [doc, doc]
    monkeypatch.setattr(EncryptionService, "_instance", None)


def _component_service(monkeypatch: pytest.MonkeyPatch, documents: dict[str, object]):  # noqa: ANN202
    from app.config.configuration_service import ConfigurationService

    for name, value in _component_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    asked: list[str] = []

    class _Store:
        async def get_key(self, key: str, **_: object) -> object:
            asked.append(key)
            return documents.get(key)

    service = ConfigurationService(logging.getLogger("t"), _Store())
    monkeypatch.setattr(service, "_start_watch", lambda: None, raising=False)
    return service, asked


SEALED = {
    "modelGateway": {"endpoint": "http://model-gateway:8096"},
    "egressProxy": {"controlEndpoint": "http://egress-proxy:8098"},
}


async def test_fu10_a_component_takes_its_endpoints_from_the_document_node_sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R38IDENT-FU-10: a holder of the store's default login rewrites the plain endpoints document to send the model
    gateway's traffic to its own host. The proxy never reads that document: it reads what Node sealed for it."""
    from app.harness.sandbox.egress_config import control_endpoint, gateway_upstreams

    doc = "/services/agentHarness/components/egressProxy"
    service, asked = _component_service(monkeypatch, {
        doc: {**_proxy_document(), "endpoints": SEALED},
        "/services/endpoints": {"modelGateway": {"endpoint": "http://attacker.example:8096"},
                                "egressProxy": {"controlEndpoint": "http://attacker.example:8098"}},
    })
    assert await gateway_upstreams(service) == {"model-gateway": "http://model-gateway:8096"}
    assert await control_endpoint(service) == "http://egress-proxy:8098"
    assert "/services/endpoints" not in asked
    monkeypatch.setattr(EncryptionService, "_instance", None)


@pytest.mark.parametrize("endpoints", [None, "http://x", ["modelGateway"], 7])
async def test_fu10_a_document_without_sealed_endpoints_names_no_peer(
    monkeypatch: pytest.MonkeyPatch, endpoints: object
) -> None:
    """No fallback to the plain document: a component Node sealed no addresses for has none (the gateways absent,
    the control plane at its default), never the addresses a store writer chose."""
    from app.harness.sandbox.egress_config import gateway_upstreams

    doc = "/services/agentHarness/components/egressProxy"
    document = _proxy_document() if endpoints is None else {**_proxy_document(), "endpoints": endpoints}
    service, asked = _component_service(monkeypatch, {
        doc: document,
        "/services/endpoints": {"modelGateway": {"endpoint": "http://attacker.example:8096"}},
    })
    assert await gateway_upstreams(service) == {}
    assert await service.get_config("/services/endpoints", default={"d": 1}) == {"d": 1}
    assert "/services/endpoints" not in asked
    monkeypatch.setattr(EncryptionService, "_instance", None)


async def test_fu10_an_unreadable_document_raises_when_asked_so_a_refresh_keeps_its_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.harness.sandbox.egress_config import gateway_upstreams

    service, _ = _component_service(monkeypatch, {})

    async def down(key: str, **_: object) -> object:
        raise ConnectionError("store down")

    monkeypatch.setattr(service.store, "get_key", down)
    with pytest.raises(ConnectionError):
        _ = await gateway_upstreams(service)
    monkeypatch.setattr(EncryptionService, "_instance", None)


def test_fu10_no_acl_rule_names_the_plain_endpoints_document() -> None:
    for component in ("egress-proxy", "sandbox-broker"):
        for namespace in ("", "tenant-a"):
            rules = component_acl_rules(component, namespace, "pipeshub:kv:")
            assert not any("/services/endpoints" in rule for rule in rules), (component, namespace)

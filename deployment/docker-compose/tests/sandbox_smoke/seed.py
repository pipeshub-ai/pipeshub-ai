"""Writes what Node writes at startup on the ADR-KEY-02 shape (dev-only smoke).

- the worker credential (the client presents it to the broker);
- the document Node seals for each sandbox-plane component under that component's own key: its role credential,
  digests of the credentials it admits, the sandbox-plane endpoints, and (the proxy) the egress sealing keys;
- each component's Redis ACL user, so the broker and the proxy log in as themselves, never as the default user;
- a decoy plain /services/endpoints, which neither component may read.

The keys, shapes, ciphers and ACL rules are Node's (ComponentCredentialPublisher, RedisComponentLogins,
ConfigService.getSandboxPlaneEndpoints), reached through the Python twins the contract tests hold to Node's answer;
the values are the addresses docker-compose.yml gives the app container.
"""

import asyncio
import hashlib
import os
import sys

sys.path[:0] = ["/app/python"]
sys.path.append("/site")

import redis.asyncio as redis  # noqa: E402

from app.config.constants.service import config_node_constants  # noqa: E402
from app.harness.assembly.services import HarnessServices  # noqa: E402
from app.harness.credential_roles import COMPONENT_ACCEPTS, seal_component_document  # noqa: E402
from app.utils.encryption.component_key import (  # noqa: E402
    COMPONENT_DOCUMENTS,
    NODE_KEY_ENV,
    component_acl_rules,
    component_redis_login,
)
from harness_core.sandbox.egress.aead_ring import derived_key  # noqa: E402
from harness_core.sandbox.egress.sealed import CREDENTIAL_KEY_LABEL  # noqa: E402
from harness_core.sandbox.egress.state import STATE_KEY_LABEL  # noqa: E402

ENDPOINTS = {
    "sandboxBroker": {"endpoint": "http://sandbox-broker:8095"},
    "egressProxy": {"controlEndpoint": "http://egress-proxy:8098"},
    "modelGateway": {"endpoint": "http://model-gateway:8096"},
}
# What a holder of the store's default login might write to redirect the gateways (R38IDENT-FU-10): the broker and
# the proxy must not read it, so SM-2 passes only through the sealed addresses.
DECOY = {service: {key: "http://pxh-decoy.invalid:9"} for service, entry in ENDPOINTS.items() for key in entry}
CREDENTIALS = {
    "worker": os.environ["SMOKE_WORKER_CREDENTIAL"],
    "egress-proxy": os.environ["SMOKE_PROXY_CREDENTIAL"],
    "sandbox-broker": os.environ["SMOKE_BROKER_CREDENTIAL"],
}


def _set(value: str) -> dict[str, object]:
    return {"current": value, "previous": None, "previousValidUntil": None, "rotateId": None}


def _document(component: str) -> dict[str, object]:
    families = COMPONENT_ACCEPTS[component]
    admitted = [role for role in (*families["control"], *families["metrics"]) if role in CREDENTIALS]
    document: dict[str, object] = {
        "role": component,
        "credential": _set(CREDENTIALS[component]),
        "accepts": {
            role: [{"digest": hashlib.sha256(CREDENTIALS[role].encode()).hexdigest(), "validUntil": None}]
            for role in admitted
        },
        # R38IDENT-FU-10: the component reads its peers' addresses here, never from the plain document.
        "endpoints": ENDPOINTS,
    }
    if component == "egress-proxy":
        secret = os.environ["SECRET_KEY"].encode()
        document["egressSealing"] = {
            "state": [derived_key(secret, STATE_KEY_LABEL).hex()],
            "credentials": [derived_key(secret, CREDENTIAL_KEY_LABEL).hex()],
        }
    return document


async def main() -> None:
    config = HarnessServices().config_service()
    await config.set_config(config_node_constants.AGENT_HARNESS_WORKER_CREDENTIAL.value, _set(CREDENTIALS["worker"]))
    await config.set_config(config_node_constants.ENDPOINTS.value, DECOY)
    # Sealed under the component's key, not the platform's: written below the encrypting layer, as Node writes it.
    raw = getattr(config.store, "store", config.store)
    admin = redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ["REDIS_PORT"]),
                        password=os.environ.get("REDIS_PASSWORD") or None)
    try:
        for component, path in COMPONENT_DOCUMENTS.items():
            key = os.environ[NODE_KEY_ENV[component]]
            await raw.create_key(path, seal_component_document(component, key, _document(component)))
            login = component_redis_login({"PIPESHUB_COMPONENT": component, "PIPESHUB_COMPONENT_KEY": key})
            assert login is not None
            user, password = login
            rules = component_acl_rules(component, os.environ.get("REDIS_KEY_NAMESPACE", ""),
                                        os.environ.get("REDIS_KV_PREFIX", "pipeshub:kv:"))
            await admin.execute_command(
                "ACL", "SETUSER", user, "reset", "on", f"#{hashlib.sha256(password.encode()).hexdigest()}", *rules
            )
    finally:
        await admin.aclose()
    print("seeded", sorted(ENDPOINTS), sorted(COMPONENT_DOCUMENTS), flush=True)


asyncio.run(main())

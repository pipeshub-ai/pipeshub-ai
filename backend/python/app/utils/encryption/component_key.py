"""A sandbox-plane component's own key (ADR-KEY-02, SEC-D-03).

The egress proxy and the sandbox broker face sandbox code, so they never hold ``SECRET_KEY``. Each is given its own
random key instead (``PIPESHUB_COMPONENT_KEY``, or a file named by ``PIPESHUB_COMPONENT_KEY_FILE``) and its name
(``PIPESHUB_COMPONENT``). Node holds the same key (``EGRESS_PROXY_COMPONENT_KEY``, ``SANDBOX_BROKER_COMPONENT_KEY``)
and seals for the component, under subkeys HKDF derives from it, only what that component needs: its own role
credential, the digests it verifies callers with, and (the proxy) the egress sealing keys. From the same key the
component derives its Redis login, whose ACL Node sets: it reaches its own keys and no configuration secret.

Node's twin is ``backend/nodejs/apps/src/modules/agent-harness/domain/component-keys.ts``; both derive alike.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.utils.encryption.secret_key_ring import (
    deployment_cipher_key,
    platform_keys_held,
    secret_key_configured,
    weak_secret_reason,
)

COMPONENT_ENV = "PIPESHUB_COMPONENT"
COMPONENT_KEY_ENV = "PIPESHUB_COMPONENT_KEY"
COMPONENT_KEY_FILE_ENV = "PIPESHUB_COMPONENT_KEY_FILE"
# The component's name, and where Node keeps the document it seals for it.
COMPONENT_DOCUMENTS = {
    "egress-proxy": "/services/agentHarness/components/egressProxy",
    "sandbox-broker": "/services/agentHarness/components/sandboxBroker",
}
# Node's environment names for each component's key.
NODE_KEY_ENV = {"egress-proxy": "EGRESS_PROXY_COMPONENT_KEY", "sandbox-broker": "SANDBOX_BROKER_COMPONENT_KEY"}
_SALT = b"pipeshub/component"


class ComponentKeyError(ValueError):
    """The component's key is missing, weak or beside SECRET_KEY; the message names variables, never a value."""


def component_subkey(component: str, key: str, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=f"{component}/{purpose}/v1".encode()).derive(
        key.encode("utf-8")
    )


def component_identity(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """``(component, key)`` when this process runs as a sandbox-plane component, else ``None``."""
    source = os.environ if env is None else env
    component = (source.get(COMPONENT_ENV) or "").strip()
    if not component:
        return None
    if component not in COMPONENT_DOCUMENTS:
        raise ComponentKeyError(f"{COMPONENT_ENV} must be one of {', '.join(sorted(COMPONENT_DOCUMENTS))}")
    held = platform_keys_held(source)
    if held:
        raise ComponentKeyError(
            f"the {component} must not hold SECRET_KEY: it faces sandbox code. Remove {', '.join(held)} from its "
            f"environment; it reads what it needs with {COMPONENT_KEY_ENV} (ADR-KEY-02)"
        )
    key = _read_key(source)
    reason = weak_secret_reason(key)
    if reason is not None:
        raise ComponentKeyError(
            f"{COMPONENT_KEY_ENV} for the {component} is not a random key ({reason}): set {NODE_KEY_ENV[component]} "
            "to the output of `openssl rand -base64 32` (install.sh generates it), on Node and on this service"
        )
    return component, key


def _read_key(source: dict[str, str] | os._Environ[str]) -> str:
    path = (source.get(COMPONENT_KEY_FILE_ENV) or "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ComponentKeyError(f"{COMPONENT_KEY_FILE_ENV} cannot be read ({type(exc).__name__})") from None
    key = source.get(COMPONENT_KEY_ENV) or ""
    if not key:
        raise ComponentKeyError(f"{COMPONENT_KEY_ENV} (or {COMPONENT_KEY_FILE_ENV}) is required for a sandbox-plane component")
    return key


def component_cipher_key(component: str, key: str) -> str:
    """The AES key of the document Node seals for the component, hex."""
    return component_subkey(component, key, "config").hex()


def component_redis_login(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """The component's own Redis ACL user and password; ``None`` outside a component."""
    identity = component_identity(env)
    if identity is None:
        return None
    component, key = identity
    password = component_subkey(component, key, "redis").hex()
    # The fingerprint keeps two installs sharing one Redis (lane stacks on one host) from overwriting each other's user.
    fingerprint = hashlib.sha256(password.encode("utf-8")).hexdigest()[:12]
    return f"pipeshub-{component}-{fingerprint}", password


_READ_COMMANDS = (
    "+get", "+mget", "+exists", "+ttl", "+pttl", "+scan", "+ping", "+hello", "+auth", "+select", "+client|setname",
    "+subscribe", "+unsubscribe", "+psubscribe", "+punsubscribe", "+quit", "+reset",
)
_EGRESS_STATE_COMMANDS = ("+set", "+del", "+eval", "+evalsha")


def component_acl_rules(component: str, namespace: str, kv_prefix: str) -> list[str]:
    """The component's Redis ACL rules (no password): its own document (which carries the endpoints), the proxy's egress state,
    the configuration-change channel, and only the commands those need. Node's twin is ``componentAclRules``
    (``redis-component-logins.ts``); ``contracts/secret-key/component-acl.json`` holds both to one answer."""
    scoped = f"{namespace}:" if namespace else ""
    kv = f"{scoped}{kv_prefix}"
    # Not ``/services/endpoints``: a component reads its peers' addresses from its own sealed document
    # (R38IDENT-FU-10), so a writer of the plain document cannot redirect it.
    keys = [f"~{kv}{COMPONENT_DOCUMENTS[component]}"]
    if component == "egress-proxy":
        keys.append(f"~{namespace}harness:egress:*")
    return [
        *keys,
        f"&{scoped}pipeshub:cache:invalidate",
        "-@all",
        *_READ_COMMANDS,
        *(_EGRESS_STATE_COMMANDS if component == "egress-proxy" else ()),
    ]


def process_cipher_key() -> str:
    """The configuration cipher's key for this process: HKDF of ``SECRET_KEY``, or, on the sandbox plane, of the
    component's own key (it then opens its own document and the plaintext keys, and no configuration secret)."""
    identity = component_identity()
    if identity is not None:
        return component_cipher_key(*identity)
    if not secret_key_configured():
        raise ValueError("SECRET_KEY environment variable is required")
    return deployment_cipher_key()


__all__ = [
    "COMPONENT_DOCUMENTS",
    "COMPONENT_ENV",
    "COMPONENT_KEY_ENV",
    "COMPONENT_KEY_FILE_ENV",
    "NODE_KEY_ENV",
    "ComponentKeyError",
    "component_acl_rules",
    "component_cipher_key",
    "component_identity",
    "component_redis_login",
    "component_subkey",
    "process_cipher_key",
]

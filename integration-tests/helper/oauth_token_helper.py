"""
Generic OAuth refresh-token helper for integration tests.

Exchanges a stored refresh token for fresh credentials and writes them
directly into the backend KV store (Redis by default, etcd fallback)
using the same AES-256-GCM encryption scheme as the backend's
EncryptedKeyValueStore.  No backend API changes are required.

After this helper runs, the subsequent ``toggle_sync`` call reads the
fresh credentials, verifies the connection, and sets ``isAuthenticated``
automatically — the helper never touches the connector registry.

Required env vars (in .env.local or .env.refresh_tokens):
  SECRET_KEY       — same value used by the backend (for AES-256-GCM key derivation)
  KV_STORE_TYPE    — "redis" (default) or "etcd"
  REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB / REDIS_KV_PREFIX
    (reuse the same vars already in env.local.template)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv, set_key

logger = logging.getLogger("oauth-token-helper")

_INTEGRATION_TESTS_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_REFRESH_TOKENS_FILE = _INTEGRATION_TESTS_DIR / ".env.refresh_tokens"

# Redis Pub/Sub channel the backend listens on for cache invalidation
_CACHE_INVALIDATION_CHANNEL = "pipeshub:cache:invalidate"


# ---------------------------------------------------------------------------
# Encryption helpers  (mirror backend EncryptionService exactly)
# ---------------------------------------------------------------------------

def _derive_key(secret_key: str) -> bytes:
    """SHA-256 hash of SECRET_KEY → 32-byte AES key (hex round-trip)."""
    return hashlib.sha256(secret_key.encode()).digest()


def _encrypt(key: bytes, text: str) -> str:
    """AES-256-GCM encrypt *text*. Returns ``iv_hex:ciphertext_hex:auth_tag_hex``."""
    iv = os.urandom(12)
    encrypted = AESGCM(key).encrypt(iv, text.encode("utf-8"), None)
    ciphertext, auth_tag = encrypted[:-16], encrypted[-16:]
    return f"{iv.hex()}:{ciphertext.hex()}:{auth_tag.hex()}"


def _decrypt(key: bytes, encrypted_text: str) -> str:
    """AES-256-GCM decrypt from ``iv_hex:ciphertext_hex:auth_tag_hex``."""
    iv_hex, ct_hex, tag_hex = encrypted_text.split(":")
    combined = bytes.fromhex(ct_hex) + bytes.fromhex(tag_hex)
    return AESGCM(key).decrypt(bytes.fromhex(iv_hex), combined, None).decode("utf-8")


# ---------------------------------------------------------------------------
# KV store write helpers
# ---------------------------------------------------------------------------

def _config_path(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/config"


def _write_credentials_to_redis(
    connector_id: str,
    access_token: str,
    refresh_token: str,
    key_bytes: bytes,
) -> None:
    """Read-modify-write credentials in the Redis KV store.

    Replicates the serialization used by EncryptedKeyValueStore + RedisDistributedKeyValueStore:
      stored bytes  =  json.dumps(encrypted_string).encode("utf-8")
    where encrypted_string is ``iv_hex:ciphertext_hex:auth_tag_hex``.
    """
    import redis as redis_lib  # noqa: PLC0415 — keep top-level import light

    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD") or None
    redis_db = int(os.getenv("REDIS_DB", "0"))
    key_prefix = os.getenv("REDIS_KV_PREFIX", "pipeshub:kv:")

    client = redis_lib.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        db=redis_db,
        socket_connect_timeout=10,
        socket_timeout=10,
        decode_responses=False,
    )

    path = _config_path(connector_id)
    redis_key = f"{key_prefix}{path}"

    # Read + decrypt existing config so we preserve auth/sync/filters blocks
    existing_config: dict = {}
    raw = client.get(redis_key)
    if raw:
        try:
            encrypted_str = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"Existing Redis KV config for connector {connector_id!r} is not valid JSON. "
                "Aborting to prevent data loss."
            ) from exc
        try:
            existing_config = json.loads(_decrypt(key_bytes, encrypted_str))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to decrypt existing Redis KV config for connector {connector_id!r}. "
                "Aborting to prevent accidental data loss. "
                "Verify that SECRET_KEY matches the backend's value."
            ) from exc

    # Merge — only replace the credentials block
    existing_config["credentials"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
    }

    # Encrypt → JSON-serialize → store
    encrypted = _encrypt(key_bytes, json.dumps(existing_config))
    serialized = json.dumps(encrypted).encode("utf-8")
    client.set(redis_key, serialized)

    # Publish cache-invalidation so the backend drops its in-process LRU cache
    client.publish(_CACHE_INVALIDATION_CHANNEL, path)

    client.close()
    logger.info("Wrote OAuth credentials to Redis for connector %s", connector_id)


def _write_credentials_to_etcd(
    connector_id: str,
    access_token: str,
    refresh_token: str,
    key_bytes: bytes,
) -> None:
    """Read-modify-write credentials in the etcd KV store.

    Replicates the serialization used by EncryptedKeyValueStore + Etcd3DistributedKeyValueStore:
      stored bytes  =  json.dumps(encrypted_string).encode("utf-8")
    """
    try:
        import etcd3  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "KV_STORE_TYPE=etcd requires the 'etcd3' package. "
            "Install it with: pip install etcd3  "
            "— or switch to KV_STORE_TYPE=redis."
        ) from exc

    etcd_url = os.getenv("ETCD_URL", "localhost:2379")
    # Strip scheme if present
    for scheme in ("http://", "https://"):
        if etcd_url.startswith(scheme):
            etcd_url = etcd_url[len(scheme):]
    parts = etcd_url.split(":")
    etcd_host = parts[0]
    etcd_port = int(parts[1]) if len(parts) > 1 else 2379
    etcd_user = os.getenv("ETCD_USERNAME") or None
    etcd_pass = os.getenv("ETCD_PASSWORD") or None

    client = etcd3.client(
        host=etcd_host,
        port=etcd_port,
        user=etcd_user,
        password=etcd_pass,
    )

    path = _config_path(connector_id)

    # Read + decrypt existing config
    existing_config: dict = {}
    raw, _ = client.get(path)
    if raw:
        try:
            encrypted_str = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"Existing etcd KV config for connector {connector_id!r} is not valid JSON. "
                "Aborting to prevent data loss."
            ) from exc
        try:
            existing_config = json.loads(_decrypt(key_bytes, encrypted_str))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to decrypt existing etcd KV config for connector {connector_id!r}. "
                "Aborting to prevent accidental data loss. "
                "Verify that SECRET_KEY matches the backend's value."
            ) from exc

    existing_config["credentials"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
    }

    encrypted = _encrypt(key_bytes, json.dumps(existing_config))
    serialized = json.dumps(encrypted).encode("utf-8")
    client.put(path, serialized)
    logger.info("Wrote OAuth credentials to etcd for connector %s", connector_id)


def _write_credentials_to_kv(
    connector_id: str,
    access_token: str,
    refresh_token: str,
    key_bytes: bytes,
) -> None:
    """Dispatch to the correct KV store based on KV_STORE_TYPE (default: redis)."""
    kv_type = os.getenv("KV_STORE_TYPE", "redis").lower()
    if kv_type == "redis":
        _write_credentials_to_redis(connector_id, access_token, refresh_token, key_bytes)
    elif kv_type == "etcd":
        _write_credentials_to_etcd(connector_id, access_token, refresh_token, key_bytes)
    else:
        raise ValueError(
            f"Unsupported KV_STORE_TYPE: {kv_type!r}. Expected 'redis' or 'etcd'."
        )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def exchange_refresh_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    timeout: int = 30,
) -> tuple[str, str]:
    """Exchange a refresh token for a fresh access token.

    Returns ``(access_token, new_refresh_token)``.  When the provider does not
    rotate refresh tokens the original *refresh_token* is returned unchanged.

    Raises :class:`RuntimeError` if the exchange fails or the response does not
    contain an ``access_token``.
    """
    resp = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=timeout,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Token refresh failed: HTTP {resp.status_code} — {resp.text[:400]}"
        )

    token_data = resp.json()
    access_token: Optional[str] = token_data.get("access_token")
    if not access_token:
        raise RuntimeError(
            f"Token refresh response did not include access_token: {token_data!r}"
        )

    # Atlassian and some other providers rotate refresh tokens.
    # Fall back to the original token for providers that don't.
    new_refresh_token: str = token_data.get("refresh_token") or refresh_token
    return access_token, new_refresh_token


def authenticate_connector_with_refresh_token(
    connector_id: str,
    refresh_token_env_var: str,
    token_url: str,
    client_id_env_var: str,
    client_secret_env_var: str,
    *,
    env_file: Optional[Path] = None,
) -> None:
    """Exchange a stored refresh token for fresh OAuth credentials and inject
    them directly into the backend KV store.

    The function is intentionally generic — all provider-specific values
    (token_url, env var names) are supplied by the caller.  Only the
    ``.env.refresh_tokens`` file and the common ``SECRET_KEY`` / Redis env
    vars need to be present in the test environment.

    Args:
        connector_id: The connector instance ID returned by ``create_connector``.
        refresh_token_env_var: Name of the env var holding the refresh token,
            e.g. ``"JIRA_REFRESH_TOKEN"``.  Loaded from *env_file*.
        token_url: OAuth token endpoint, e.g.
            ``"https://auth.atlassian.com/oauth/token"``.
        client_id_env_var: Env var name for the OAuth client ID,
            e.g. ``"JIRA_CLIENT_ID"``.
        client_secret_env_var: Env var name for the OAuth client secret,
            e.g. ``"JIRA_CLIENT_SECRET"``.
        env_file: Path to the refresh-tokens file.
            Defaults to ``integration-tests/.env.refresh_tokens``.

    After this function returns the connector has valid credentials in the KV
    store.  The next ``toggle_sync`` call will call ``init()`` +
    ``test_connection_and_access()``, verify the credentials, and set
    ``isAuthenticated = True`` in the connector registry.
    """
    if env_file is None:
        env_file = _DEFAULT_REFRESH_TOKENS_FILE

    # Ensure the refresh-tokens file is loaded into the environment
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=True)

    refresh_token = os.getenv(refresh_token_env_var)
    if not refresh_token:
        logger.warning(
            "authenticate_connector_with_refresh_token: %s is not set in %s — skipping.",
            refresh_token_env_var,
            env_file,
        )
        return

    client_id = os.getenv(client_id_env_var)
    client_secret = os.getenv(client_secret_env_var)
    if not client_id or not client_secret:
        raise ValueError(
            f"OAuth client credentials not set: {client_id_env_var!r} / "
            f"{client_secret_env_var!r}.  Add them to .env.local."
        )

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise ValueError(
            "SECRET_KEY is not set.  Add it to .env.local matching the backend's SECRET_KEY."
        )

    logger.info(
        "Exchanging refresh token for connector %s via %s", connector_id, token_url
    )
    access_token, new_refresh_token = exchange_refresh_token(
        token_url, client_id, client_secret, refresh_token
    )

    key_bytes = _derive_key(secret_key)
    _write_credentials_to_kv(connector_id, access_token, new_refresh_token, key_bytes)

    # Persist the updated refresh token if the provider issued a new one
    if new_refresh_token != refresh_token:
        set_key(str(env_file), refresh_token_env_var, new_refresh_token)
        logger.info(
            "Updated %s in %s with rotated refresh token",
            refresh_token_env_var,
            env_file,
        )

    logger.info(
        "OAuth credentials written for connector %s — "
        "toggle_sync will complete authentication.",
        connector_id,
    )

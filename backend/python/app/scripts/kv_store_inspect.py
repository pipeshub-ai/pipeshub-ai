"""
Inspect the encrypted KV store with decrypted keys and values.

The app stores values as AES-256-GCM ciphertext (except a few paths like
/services/endpoints, /services/storage, /services/migrations). Some deployments
also store key *names* as ciphertext strings; this script uses the same
decryption logic as EncryptedKeyValueStore.list_keys_in_directory and get_key.

**Security:** Output can contain secrets. Do not pipe to logs or share.

How to run
----------
From ``backend/python`` (so ``.env`` is loaded and imports resolve)::

    cd backend/python
    python -m app.scripts.kv_store_inspect --all
    python -m app.scripts.kv_store_inspect --key /services/neo4j
    python -m app.scripts.kv_store_inspect --all --prefix /services

Required env (same as the app): ``SECRET_KEY``, ``KV_STORE_TYPE`` (``etcd`` or
``redis``), and either ``ETCD_URL`` (+ optional etcd auth) or Redis settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import dotenv  # type: ignore

# Load .env before importing app config (which reads os.environ).
# Prefer backend/python/.env next to this package tree.
_py_backend = Path(__file__).resolve().parents[2]
_env_file = _py_backend / ".env"
if _env_file.is_file():
    dotenv.load_dotenv(_env_file)
else:
    dotenv.load_dotenv()

from app.config.providers.encrypted_store import EncryptedKeyValueStore  # noqa: E402
from app.utils.logger import create_logger  # noqa: E402


def _format_value(value: Any) -> str:
    if value is None:
        return "(no value / key missing)"
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, default=str, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


async def _run(args: argparse.Namespace) -> int:
    logger = create_logger("kv_store_inspect")
    store = EncryptedKeyValueStore(logger=logger)

    try:
        if args.key is not None:
            value = await store.get_key(args.key)
            print(f"key: {args.key}")
            print(_format_value(value))
            return 0

        # --all
        directory = args.prefix or "/"
        keys = await store.list_keys_in_directory(directory)
        if not keys:
            print(f"No keys under prefix {directory!r}.")
            return 0

        keys = sorted(keys)
        for i, key in enumerate(keys):
            value = await store.get_key(key)
            sep = "\n" if i else ""
            print(f"{sep}{'=' * 72}\nkey: {key}\n{'-' * 72}")
            print(_format_value(value))
        print(f"\n{'=' * 72}\nTotal keys: {len(keys)}")
        return 0
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print decrypted KV store keys and/or values (uses app SECRET_KEY and backend config)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--key",
        metavar="PATH",
        help="Logical key path (e.g. /services/neo4j). Value is printed decrypted/parsed.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="List all keys (decrypted) under --prefix and print each decrypted value.",
    )
    parser.add_argument(
        "--prefix",
        default="",
        metavar="DIR",
        help="With --all: only keys starting with this path (default: all keys). Example: /services",
    )

    args = parser.parse_args()

    # Ensure SECRET_KEY is available (same derivation as EncryptedKeyValueStore)
    if not os.getenv("SECRET_KEY"):
        print(
            "SECRET_KEY is not set. Run from backend/python with .env loaded, or export SECRET_KEY.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        code = asyncio.run(_run(args))
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()

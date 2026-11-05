"""Operator command: copy the configuration key-value store from Redis to PostgreSQL.

    python -m app.scripts.kv_redis_to_postgres [--dry-run] [--overwrite]

Run it once, before switching KV_STORE_TYPE from redis to postgres, so the config, credentials and
pending-delete records the install holds are there when it starts on PostgreSQL. Nothing runs unless
you run it. Redis is only read.

Reads the Redis settings the services use (REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
REDIS_KV_PREFIX, REDIS_KEY_NAMESPACE) and writes through the PostgreSQL settings (PG_CORE_URL, ...).
Only keys under the KV prefix and namespace are copied; leases, locks, caches and queues are runtime
state and are left alone. Values are copied as stored (already encrypted); they are never decoded or
printed. A key's remaining TTL becomes its expiry; a key already expired is not copied.

A key PostgreSQL already holds with a different value, or a configuration key it holds that Redis does
not, stops the run before any write (exit 1, key names listed) unless ``--overwrite`` is given (which
replaces or removes them). Every live key is written with the value and TTL Redis
holds at that moment (equal values too, so PostgreSQL's expiry matches Redis's), so a re-run after a
successful copy changes nothing. Stop the services first for a clean cutover: after the copy every
copied key is compared with Redis again and re-copied if a service changed it meanwhile, but a key
changed after that check needs another run. ``--dry-run`` reads only key names and prints counts per
top-level prefix.

Output is a JSON summary; exit 1 on conflicts, or when a key still differs after the copy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config.providers.postgres_store import PostgresKeyValueStore
    from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore


def _prefix_of(key: str) -> str:
    return key.strip("/").split("/", 1)[0]


async def _read(src: RedisDistributedKeyValueStore[bytes], key: str) -> tuple[bytes, float | None] | None:
    """The stored (ciphertext, seconds to live) of ``key``; None when gone or already expired."""
    full = src._build_key(key)  # the one place that knows namespace + prefix
    value = await src.client.get(full)
    pttl = await src.client.pttl(full)
    if value is None or pttl in (-2, 0):
        return None
    return bytes(value), (pttl / 1000 if pttl > 0 else None)


async def copy_kv(
    src: RedisDistributedKeyValueStore[bytes],
    dst: PostgresKeyValueStore[bytes],
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Redis is only read. Writes made while it runs are caught by the verify pass below (keys it
    copied are compared again and re-copied); a key changed after that, or a new one, needs a re-run."""
    keys = sorted(await src.get_all_keys())
    summary: dict[str, Any] = {"dry_run": dry_run, "source_keys": len(keys), "by_prefix": dict(Counter(map(_prefix_of, keys)))}
    if dry_run:
        return summary
    live = {k: r for k in keys if (r := await _read(src, k))}
    existing = {k: await dst.get_key(k) for k in keys}
    gone = [k for k in keys if k not in live]  # expired in Redis: PostgreSQL must not keep a live copy either
    # configuration keys are paths ("/services/..."); runtime state in the same table ("lease:...") is never touched
    only_in_postgres = sorted(set(await dst.list_keys_in_directory("/")) - set(keys))
    conflicts = sorted([k for k in live if existing[k] is not None and existing[k] != live[k][0]]
                       + [k for k in gone if existing[k] is not None] + only_in_postgres)
    summary |= {"skipped_expired": len(keys) - len(live), "conflicts": conflicts}
    if conflicts and not overwrite:  # refuse before any write
        return summary
    removed = 0
    for k in [*gone, *only_in_postgres]:  # only reached with --overwrite: Redis no longer has the key
        if k in only_in_postgres or existing[k] is not None:
            removed += await dst.delete_key(k)
    written = changed = expired_late = 0
    for k in live:
        now = await _read(src, k)  # the value and TTL as of this write; a key that expired since the scan is not written
        if now is None:
            expired_late += 1
            if existing[k] is not None:  # equal to what Redis held (a different value was a conflict)
                removed += await dst.delete_key(k)
            continue
        # written even when PostgreSQL holds the same value, so its expiry ends up the same as Redis's
        await dst.create_key(k, now[0], overwrite=True, ttl=now[1])
        written += 1
        changed += existing[k] != now[0]
    recopied = 0
    for k in keys:  # every scanned key: one a service changed, or wrote again after it was gone, during the copy
        now = await _read(src, k)
        if now and await dst.get_key(k) != now[0]:
            await dst.create_key(k, now[0], overwrite=True, ttl=now[1])
            recopied += 1
    different = 0
    for k in keys:  # PostgreSQL must now hold exactly what Redis holds for each scanned key
        now = await _read(src, k)
        if await dst.get_key(k) != (now[0] if now else None):
            different += 1
    different += len(set(await dst.list_keys_in_directory("/")) - set(keys))  # a configuration key Redis does not have
    return summary | {"written": written, "changed": changed, "unchanged": written - changed, "expired_during_copy": expired_late,
                      "removed_expired": removed,
                      "recopied": recopied, "different_after": different}


def _redis_store() -> RedisDistributedKeyValueStore[bytes]:
    from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore

    return RedisDistributedKeyValueStore(
        serializer=bytes,
        deserializer=bytes,
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        key_prefix=os.getenv("REDIS_KV_PREFIX", "pipeshub:kv:"),
        connect_timeout=float(os.getenv("REDIS_TIMEOUT", "10000")) / 1000,
    )


async def _run(args: argparse.Namespace) -> int:
    from app.config.providers.postgres_store import PostgresKeyValueStore

    src = _redis_store()
    dst = PostgresKeyValueStore(serializer=bytes, deserializer=bytes)
    try:
        summary = await copy_kv(src, dst, overwrite=args.overwrite, dry_run=args.dry_run)
    finally:
        await src.close()
        await dst.close()
    sys.stdout.write(json.dumps(summary) + "\n")
    return 1 if summary.get("different_after") or summary.get("conflicts") and not args.overwrite else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--dry-run", action="store_true", help="print counts per top-level prefix; write nothing")
    p.add_argument("--overwrite", action="store_true", help="replace keys PostgreSQL holds with a different value")
    return asyncio.run(_run(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())

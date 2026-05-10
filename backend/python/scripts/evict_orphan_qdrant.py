"""
One-off cleanup: evict points from Qdrant by virtualRecordId.

Why: when a `documents` row exists in Mongo but its blob is missing
(empty `local.url`), search hits on its virtualRecordId crash the whole
request at retrieval_service.py:610. Removing the orphan from Qdrant
breaks the loop without any code changes.

Usage:
  cd backend/python
  python scripts/evict_orphan_qdrant.py --vrid e9159cf5-f645-43c3-8918-41f2c3dd8763           # dry-run
  python scripts/evict_orphan_qdrant.py --vrid e9159cf5-f645-43c3-8918-41f2c3dd8763 --apply   # delete
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from qdrant_client import AsyncQdrantClient

from app.containers.query import QueryAppContainer


async def main(vrid: str, apply: bool) -> int:
    container = QueryAppContainer.init("query_service")
    qdrant_service = await container.vector_db_service()
    client = qdrant_service.client
    if client is None:
        print("ERROR: Qdrant client not connected")
        return 2

    if isinstance(client, AsyncQdrantClient):
        collections = await client.get_collections()
    else:
        collections = await asyncio.to_thread(client.get_collections)
    coll_names = [c.name for c in collections.collections]
    print(f"Qdrant collections: {coll_names}")

    if not coll_names:
        print("No collections found, nothing to do.")
        return 0

    total_matched = 0
    total_deleted = 0

    for name in coll_names:
        scroll_filter = await qdrant_service.filter_collection(
            must={"virtualRecordId": vrid}
        )
        result = await qdrant_service.scroll(
            collection_name=name,
            scroll_filter=scroll_filter,
            limit=10_000,
        )
        points = result[0] if result else []
        matched = len(points)
        if matched == 0:
            continue
        print(f"[{name}] matched {matched} point(s) for virtualRecordId={vrid}")
        for p in points[:5]:
            print(f"    sample point id={p.id}")

        total_matched += matched
        if apply:
            await qdrant_service.delete_points(name, scroll_filter)
            print(f"[{name}] deleted {matched} point(s)")
            total_deleted += matched

    print()
    print(f"Total matched across all collections: {total_matched}")
    if apply:
        print(f"Total deleted: {total_deleted}")
    else:
        print("Dry-run only. Re-run with --apply to delete.")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--vrid",
        required=True,
        help="virtualRecordId to evict from all Qdrant collections",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete (default is dry-run)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(main(args.vrid, args.apply)))

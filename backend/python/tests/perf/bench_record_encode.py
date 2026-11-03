"""Event-loop CPU spent encoding one large record for classify and blob storage.

A big GitHub pull request indexes as thousands of short blocks. Each record
is JSON-encoded on the indexing worker's event loop twice over: once for the
extraction service's classify request and once (or more) for its blob upload.
This times both through the real client and storage code, with the network
mocked out, so the numbers are the CPU a record holds the loop for.

Run from backend/python on any checkout, before and after a change:

    python tests/perf/bench_record_encode.py [--blocks 8000] [--repeat 7]

Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.models.entities import Record
from app.modules.transformers.blob_storage import BlobStorage
from app.services.extraction.client import ExtractionClient

_WORDS = (
    "the of fix add refactor handler request response token index block group "
    "vector graph query parse retry"
).split()


def _text(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(n))


def build_record(n_blocks: int, per_group: int = 20) -> Record:
    """A pull-request-shaped record: one group per file, short markdown blocks."""
    rng = random.Random(1)
    blocks: list[Block] = []
    groups: list[BlockGroup] = []
    for g in range(n_blocks // per_group):
        first = g * per_group
        groups.append(BlockGroup(
            index=g, type=GroupType.TEXT_SECTION, name=f"src/file_{g}.py",
            description=_text(rng, 8),
            children=BlockGroupChildren.from_indices(list(range(first, first + per_group))),
        ))
        blocks.extend(
            Block(
                index=i, parent_index=g, type=BlockType.TEXT, format=DataFormat.MARKDOWN,
                data=_text(rng, rng.randint(8, 25)),
                weburl="https://github.com/o/r/pull/1#diff-abc", source_id=f"sha{i}",
                source_creation_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            for i in range(first, first + per_group)
        )
    return Record(
        record_name="PR #1", record_type="PULL_REQUEST", external_record_id="1", version=1,
        origin="CONNECTOR", connector_name="GITHUB", connector_id="c", org_id="org-1",
        virtual_record_id="vr-1", block_containers=BlocksContainer(blocks=blocks, block_groups=groups),
    )


async def _cpu_ms(coro_fn, repeat: int) -> float:
    best = float("inf")
    for _ in range(repeat):
        start = time.thread_time()
        await coro_fn()
        best = min(best, time.thread_time() - start)
    return best * 1000


async def bench_classify(record: Record, repeat: int) -> float:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={"llm": [{"provider": "openai"}]})
    client = ExtractionClient(service_url="http://extraction:8093", config_service=config_service)
    client._auth_headers = AsyncMock(return_value={})
    client._request_with_retry = AsyncMock(
        return_value=httpx.Response(200, json={"success": True, "classification": None})
    )
    return await _cpu_ms(lambda: client.classify(record.block_containers, "org-1"), repeat)


async def bench_blob(record: Record, repeat: int) -> tuple[float, float]:
    """(whole record write, of which the JSON encode + upload body)."""
    bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
    bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node:3000", "local"))
    resp = AsyncMock()
    resp.status = 200
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.put = MagicMock(return_value=resp)

    record_dict = bs._clean_empty_values(record.model_dump(mode="json", exclude_none=True))

    async def encode_and_upload() -> None:
        await bs.update_record_buffer("org-1", "doc-1", record_dict, "vr-1")

    async def whole() -> None:
        d = bs._clean_empty_values(record.model_dump(mode="json", exclude_none=True))
        await bs.update_record_buffer("org-1", "doc-1", d, "vr-1")

    with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session), \
         patch("app.modules.transformers.blob_storage.aiohttp.FormData", return_value=MagicMock()):
        return await _cpu_ms(whole, repeat), await _cpu_ms(encode_and_upload, repeat)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--blocks", type=int, default=8000)
    parser.add_argument("--repeat", type=int, default=7)
    args = parser.parse_args()

    record = build_record(args.blocks)
    chars = sum(len(b.data) for b in record.block_containers.blocks)
    print(f"record: {args.blocks} blocks, {chars:,} characters of text")
    print(f"classify request (build + encode): {await bench_classify(record, args.repeat):7.1f} ms")
    whole, encode = await bench_blob(record, args.repeat)
    print(f"blob write, whole:                 {whole:7.1f} ms")
    print(f"blob write, JSON encode + body:    {encode:7.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())

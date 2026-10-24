"""Operator command: queue an org's indexed records to have their named entities
extracted again from their stored blocks (see ``named_entity_completion``).

    python -m app.scripts.named_entity_reextract --org ORG [--all] [--apply]
        [--after-key KEY] [--limit N]

By default it queues the records whose stored extraction left text unread
(``needs_reextract``). Records extracted before extraction read long blocks
whole can look complete while they were not; ``--all`` queues every record that
has a stored extraction. Nothing is queued without ``--apply``. Queuing is an
upsert, so a run can be repeated or resumed with ``--after-key``. The background
loop then works the queue while indexing leaves the model idle.

Output is one JSON object per page and a final summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, TextIO

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.named_entities.kept import read_kept_extraction
from app.modules.named_entities.stage import needs_reextract
from app.services.graph_db.common.record_visibility import is_live_record

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

PAGE_SIZE = 100
EXIT_FAILED = 1


@dataclass
class Tally:
    checked: int = 0
    queued: int = 0
    without_extraction: int = 0
    unreadable: int = 0
    after_key: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="named_entity_reextract",
        description="Queue records to have their named entities extracted again; dry run unless --apply.",
    )
    parser.add_argument("--org", required=True)
    parser.add_argument("--all", action="store_true", help="queue every record with a stored extraction")
    parser.add_argument("--apply", action="store_true", help="queue the records (default: dry run)")
    parser.add_argument("--after-key", default=None, help="resume after this record key")
    parser.add_argument("--limit", type=int, default=None, help="stop after checking this many records")
    return parser


async def queue_reextractions(
    graph_provider: IGraphDBProvider,
    blob_storage: Any,  # noqa: ANN401 - BlobStorage
    *,
    org_id: str,
    everything: bool,
    apply: bool,
    after_key: str | None = None,
    limit: int | None = None,
    page_size: int = PAGE_SIZE,
    out: TextIO = sys.stdout,
) -> Tally:
    tally = Tally(after_key=after_key)
    while limit is None or tally.checked < limit:
        size = page_size if limit is None else min(page_size, limit - tally.checked)
        page = await graph_provider.get_documents_paginated(
            CollectionNames.RECORDS.value,
            limit=size,
            filters={"orgId": org_id, "indexingStatus": ProgressStatus.COMPLETED.value},
            sort_field="_key",
            raise_on_error=True,
            after_key=tally.after_key,
        )
        if not page:
            break
        queued_before = tally.queued
        for record in page:
            tally.checked += 1
            tally.after_key = record.get("_key")
            if await _wants_reextract(record, graph_provider, blob_storage, tally, everything=everything):
                tally.queued += 1
                if apply:
                    await graph_provider.schedule_named_entity_reextract(org_id, record["_key"])
        out.write(json.dumps({"page": len(page), "page_queued": tally.queued - queued_before, **asdict(tally)}) + "\n")
        if len(page) < size:
            break
    return tally


async def _wants_reextract(
    record: dict[str, Any],
    graph_provider: IGraphDBProvider,
    blob_storage: Any,  # noqa: ANN401 - BlobStorage
    tally: Tally,
    *,
    everything: bool,
) -> bool:
    if not is_live_record(record) or not record.get("virtualRecordId"):
        return False
    try:
        blob = await blob_storage.get_record_from_storage(record["virtualRecordId"], record.get("orgId"))
        metadata = (blob or {}).get("semantic_metadata") or {}
        extraction = await read_kept_extraction(
            graph_provider, record["virtualRecordId"], record.get("contentRev"), metadata.get("named_entities")
        )
    except Exception:
        tally.unreadable += 1
        return False
    if extraction is None:
        tally.without_extraction += 1
        return False
    return everything or needs_reextract(extraction)


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.containers.indexing import IndexingAppContainer
    from app.modules.transformers.blob_storage import BlobStorage

    container = IndexingAppContainer.init("named_entity_reextract")
    logger = container.logger()
    try:
        graph_provider = await container.graph_provider()
    except Exception as exc:
        logger.exception("named_entity_reextract: graph unavailable")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    try:
        blob_storage = BlobStorage(logger, container.config_service(), graph_provider)
        tally = await queue_reextractions(
            graph_provider, blob_storage, org_id=args.org, everything=args.all, apply=args.apply,
            after_key=args.after_key, limit=args.limit,
        )
        sys.stdout.write(json.dumps({"done": True, "applied": args.apply, **asdict(tally)}) + "\n")
        return 0
    except Exception as exc:
        logger.exception("named_entity_reextract failed")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    finally:
        await graph_provider.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

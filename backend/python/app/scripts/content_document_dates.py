"""Operator command: give an org's indexed knowledge-base uploads the dates
their content states, from the named-entity extraction each blob holds.

    python -m app.scripts.content_document_dates --org ORG [--apply] [--revert]
        [--after-key KB/KEY] [--limit N] [--concurrency N]

The same rule as indexing with CONTENT_DOCUMENT_DATES on (see
``app.modules.indexing.content_dates``), so dating needs the switch set in this
process's environment too; it reads no model. Uploads stored before their
timestamp origin was kept get it stamped. ``--revert`` puts back what the
upload set on every content-dated record and reads no blob. Nothing is written
without ``--apply``. Each write applies only while the record still holds what
was read, so a run can be repeated or resumed with ``--after-key``.

It walks the org's knowledge bases one at a time, each by the records'
(connectorId, key) index, so a page costs the same however many uploads the
org has.

Output is one JSON object per page and a final summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, TextIO

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ProgressStatus,
)
from app.models.blocks import BlocksContainer
from app.models.source_dates import (
    SourceTimestampOrigin,
    content_dates_replace,
    is_kb_upload,
)
from app.modules.artifacts.reader import record_artifact_reader
from app.modules.indexing.content_dates import (
    DatesWrite,
    latest_upload_ms,
    plan_dates_write,
    plan_revert,
)
from app.modules.named_entities.document_dates import find_document_dates
from app.modules.named_entities.kept import read_kept_extraction
from app.services.graph_db.common.record_visibility import is_live_record

if TYPE_CHECKING:
    from app.modules.artifacts.reader import RecordArtifactReader
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

PAGE_SIZE = 200
CONCURRENCY = 8
EXIT_FAILED = 1
_RECORDS = CollectionNames.RECORDS.value
_APPS = CollectionNames.APPS.value
# Between the knowledge base and the record key in the resume cursor.
CURSOR_SEPARATOR = "/"


@dataclass
class Tally:
    checked: int = 0
    date: int = 0
    redate: int = 0
    revert: int = 0
    stamp: int = 0
    written: int = 0
    without_extraction: int = 0
    unreadable: int = 0
    after_key: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content_document_dates",
        description="Date knowledge-base uploads by the dates their content states; dry run unless --apply.",
    )
    parser.add_argument("--org", required=True)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--revert", action="store_true", help="put back the dates uploads set")
    parser.add_argument(
        "--after-key", default=None, help="resume after this KB/KEY, the after_key a page printed",
    )
    parser.add_argument("--limit", type=int, default=None, help="stop after checking this many records")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY, help="blobs read at once")
    return parser


async def backfill_content_dates(
    graph_provider: IGraphDBProvider,
    blob_storage: Any,  # noqa: ANN401 - BlobStorage
    *,
    org_id: str,
    apply: bool,
    replace: frozenset[SourceTimestampOrigin],
    revert: bool = False,
    after_key: str | None = None,
    limit: int | None = None,
    page_size: int = PAGE_SIZE,
    concurrency: int = CONCURRENCY,
    out: TextIO = sys.stdout,
) -> Tally:
    if apply and not revert and not replace:
        raise ValueError("Dating writes need CONTENT_DOCUMENT_DATES set to true or all")
    tally = Tally(after_key=after_key)
    reads = asyncio.Semaphore(max(1, concurrency))
    reader = record_artifact_reader(blob_storage)
    resume_kb, _, resume_key = (after_key or "").partition(CURSOR_SEPARATOR)
    for kb_id in await _knowledge_bases(graph_provider, org_id, page_size):
        if resume_kb and kb_id < resume_kb:
            continue
        cursor = (resume_key or None) if kb_id == resume_kb else None
        while limit is None or tally.checked < limit:
            size = page_size if limit is None else min(page_size, limit - tally.checked)
            page = await graph_provider.get_documents_paginated(
                _RECORDS,
                limit=size,
                filters={"connectorId": kb_id},
                sort_field="_key",
                raise_on_error=True,
                after_key=cursor,
            )
            if not page:
                break
            cursor = page[-1].get("_key")
            tally.after_key = f"{kb_id}{CURSOR_SEPARATOR}{cursor}"
            uploads = [doc for doc in page if is_kb_upload(doc.get("connectorName"), doc.get("origin"))]
            tally.checked += len(uploads)
            planned = await asyncio.gather(*(
                _plan(doc, reader, graph_provider, reads, tally, replace=replace, revert=revert) for doc in uploads
            ))
            writes = [(doc["_key"], write) for doc, write in zip(uploads, planned, strict=True) if write is not None]
            for _key, write in writes:
                setattr(tally, write.action, getattr(tally, write.action) + 1)
            written = 0
            if apply and writes:
                rows = [(key, write.updates, write.expected) for key, write in writes]
                written = len(await graph_provider.update_nodes_fields_if_match(_RECORDS, rows))
                tally.written += written
            out.write(json.dumps({"page": len(page), "page_planned": len(writes), "page_written": written, **asdict(tally)}) + "\n")
            if len(page) < size:
                break
        if limit is not None and tally.checked >= limit:
            break
    return tally


async def _knowledge_bases(graph_provider: IGraphDBProvider, org_id: str, page_size: int) -> list[str]:
    """The org's knowledge bases, in key order. Each is the connectorId of its uploads."""
    keys: list[str] = []
    while True:
        page = await graph_provider.get_documents_paginated(
            _APPS,
            limit=page_size,
            filters={"orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            sort_field="_key",
            raise_on_error=True,
            after_key=keys[-1] if keys else None,
        )
        keys.extend(doc["_key"] for doc in page if doc.get("_key"))
        if len(page) < page_size:
            return keys


async def _plan(
    doc: dict[str, Any],
    reader: RecordArtifactReader,
    graph_provider: IGraphDBProvider,
    reads: asyncio.Semaphore,
    tally: Tally,
    *,
    replace: frozenset[SourceTimestampOrigin],
    revert: bool,
) -> DatesWrite | None:
    if not is_live_record(doc):
        return None
    if revert:
        return plan_revert(doc)
    virtual_record_id = doc.get("virtualRecordId")
    if doc.get("indexingStatus") != ProgressStatus.COMPLETED.value or not virtual_record_id:
        return None
    async with reads:
        try:
            stored = await reader.read_record(virtual_record_id, doc.get("orgId") or "")
            # The entities stage keeps its extraction in its state; a record indexed
            # before the stage existed keeps it in the stored record.
            extraction = await read_kept_extraction(
                graph_provider, virtual_record_id, doc.get("contentRev"),
                ((stored or {}).get("semantic_metadata") or {}).get("named_entities"),
            )
        except Exception:
            tally.unreadable += 1
            return None
    containers = (stored or {}).get("block_containers")
    if extraction is None or not containers or extraction.status in ("FAILED", "SKIPPED"):
        tally.without_extraction += 1
        # Nothing is known of the content, so only a missing origin is written.
        write = plan_dates_write(doc, None, frozenset())
        return write if write is not None and write.action == "stamp" else None
    try:
        blocks = BlocksContainer.model_validate(containers).blocks
        dates = find_document_dates(extraction, blocks, uploaded_ms=latest_upload_ms(doc))
    except Exception:
        # Damaged stored content: skipped, so a resumed run does not stop on it again.
        tally.unreadable += 1
        return None
    return plan_dates_write(doc, dates, replace)


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.containers.indexing import IndexingAppContainer
    from app.modules.transformers.blob_storage import BlobStorage

    container = IndexingAppContainer.init("content_document_dates")
    logger = container.logger()
    try:
        graph_provider = await container.graph_provider()
    except Exception as exc:
        logger.exception("content_document_dates: graph unavailable")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    try:
        blob_storage = BlobStorage(logger, container.config_service(), graph_provider)
        tally = await backfill_content_dates(
            graph_provider, blob_storage, org_id=args.org, apply=args.apply, revert=args.revert,
            replace=content_dates_replace(), after_key=args.after_key, limit=args.limit,
            concurrency=args.concurrency,
        )
        sys.stdout.write(json.dumps({"done": True, "applied": args.apply, **asdict(tally)}) + "\n")
        return 0
    except Exception as exc:
        logger.exception("content_document_dates failed")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    finally:
        await graph_provider.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

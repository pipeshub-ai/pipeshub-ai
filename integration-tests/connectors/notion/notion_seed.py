# pyright: ignore-file

"""Build (and sweep) the Notion fixture tree the integration tests run against.

Everything lives under a per-run root page ``PHIT-Notion-{run_id}`` inside the permanent IT
root page. Trashing that run root at teardown cascades to every descendant, so a run leaves
no residue. ``sweep_stale_runs`` clears roots left behind by a crashed run — without it those
orphans inflate the next run's counts, because the connector has no delete detection.

The returned :class:`NotionSeed` is the tests' source of truth for expected graph contents. It
is built from what we actually created, not from re-querying the connector's own search call,
so a sync that drops records cannot also drop the expectation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from connectors.notion.constants import (  # type: ignore[import-not-found]
    ARCHIVED_DB_TITLE,
    ARCHIVED_PAGE_TITLE,
    BOOKMARK_URL,
    BULK_DATABASE_COUNT,
    BULK_DATABASE_TITLE_FMT,
    BULK_DB_ROW_COUNT,
    BULK_DB_TITLE,
    BULK_PAGE_COUNT,
    BULK_PAGE_TITLE_FMT,
    CHILD_REF_DB_TITLE,
    CHILD_REF_PAGE_TITLE,
    COMMENT_ATTACHMENT_FILENAME,
    COMMENT_ATTACHMENT_SAMPLE_PATH,
    COMMENTED_PAGE_TITLE,
    DB_ROW_NAMED_TITLE,
    DB_ROW_WITH_CHILDREN_TITLE,
    DEEP_PAGE_BLOCK_COUNT,
    DEEP_PAGE_TITLE,
    EMBED_URL,
    FALLBACK_DB_TITLE,
    FALLBACK_TITLE_PROPERTY,
    FIXTURE_DB_TITLE,
    FIXTURE_ROOT_TITLE,
    IMAGE_PNG_URL,
    NESTED_CHILD_TITLE,
    NESTED_PARENT_TITLE,
    RICH_PAGE_TITLE,
    RUN_ROOT_PREFIX,
    SAMPLE_FILE_FIXTURES,
    SCRATCH_ROOT_TITLE,
    STALE_RUN_MIN_AGE_SECONDS,
    TITLE_FALLBACK_TITLE,
    TITLE_PREFIX,
    TITLE_STANDARD_TITLE,
    YOUTUBE_EMBED_URL,
)
from connectors.notion.notion_expected import (  # type: ignore[import-not-found]
    normalize_filename_for_id,
)
from connectors.notion.notion_source_helper import (  # type: ignore[import-not-found]
    NotionSourceHelper,
    external_media_block,
    file_upload_block,
    find_sample_file_by_suffix,
    paragraph_block,
    rich_text,
)

logger = logging.getLogger("notion-seed")


@dataclass
class NotionSeed:
    """Everything the fixture created, plus the derived graph expectations."""

    run_id: str
    workspace_id: str
    workspace_name: Optional[str]

    # Structure
    run_root_page_id: str = ""
    fixture_page_id: str = ""
    scratch_page_id: str = ""

    # Pages
    nested_parent_id: str = ""
    nested_child_id: str = ""
    rich_page_id: str = ""
    rich_page_url: str = ""
    deep_page_id: str = ""
    commented_page_id: str = ""
    title_standard_id: str = ""
    title_fallback_row_id: str = ""
    child_ref_page_id: str = ""
    archived_page_id: str = ""

    # Databases / data sources
    fixture_db_id: str = ""
    fixture_data_source_id: str = ""
    child_ref_db_id: str = ""
    child_ref_data_source_id: str = ""
    fallback_db_id: str = ""
    fallback_data_source_id: str = ""
    bulk_db_id: str = ""
    bulk_data_source_id: str = ""
    archived_db_id: str = ""
    root_database_id: str = ""
    root_data_source_id: str = ""

    # Database rows (pages)
    db_row_named_id: str = ""
    db_row_with_children_id: str = ""
    db_row_sub_page_id: str = ""
    bulk_row_ids: list[str] = field(default_factory=list)

    # Bulk
    bulk_page_ids: list[str] = field(default_factory=list)
    bulk_database_ids: list[str] = field(default_factory=list)
    bulk_data_source_ids: list[str] = field(default_factory=list)

    # Blocks on the rich page. ``file_block_ids`` become FileRecords; ``skipped_block_ids``
    # must NOT become FileRecords.
    file_block_ids: dict[str, str] = field(default_factory=dict)
    skipped_block_ids: dict[str, str] = field(default_factory=dict)
    # False when the shared sample-data repo has no .svg, which makes the SVG→PNG assertion skip.
    has_svg_image: bool = False
    callout_block_id: str = ""
    synced_source_block_id: str = ""
    synced_reference_block_id: str = ""
    table_block_id: str = ""

    # Comments on ``commented_page_id``
    commented_first_block_id: str = ""
    page_comment_id: str = ""
    block_comment_id: str = ""
    reply_comment_id: str = ""
    attachment_comment_id: str = ""
    comment_attachment_external_id: str = ""

    # Expected graph contents (see ``finalize``)
    expected_page_ids: set[str] = field(default_factory=set)
    expected_data_source_ids: set[str] = field(default_factory=set)
    expected_file_ids: set[str] = field(default_factory=set)

    @property
    def expected_record_count(self) -> int:
        return (
            len(self.expected_page_ids)
            + len(self.expected_data_source_ids)
            + len(self.expected_file_ids)
        )


def _normalize_id(raw: str) -> str:
    """Notion returns dashed uuids everywhere; ids are compared as-is."""
    return str(raw)


def _age_seconds(page: dict[str, Any], now: datetime) -> Optional[float]:
    """Age of a page from ``created_time``; ``None`` when Notion omits/malforms it."""
    raw = page.get("created_time")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - created).total_seconds()


async def sweep_stale_runs(
    helper: NotionSourceHelper,
    root_page_id: str,
    *,
    min_age_seconds: float = STALE_RUN_MIN_AGE_SECONDS,
    only_run_id: Optional[str] = None,
) -> int:
    """Trash run roots left behind by an earlier crashed run. Returns how many were trashed.

    Roots younger than ``min_age_seconds`` are left alone: CI can run the neo4j and arango
    legs on separate runners at the same time, and both sweep before they seed, so a young
    root usually belongs to a run that is still going. Trashing one mid-sync destroys that
    run — its pages vanish under the connector, which then dies on a stale search cursor.

    Pass ``only_run_id`` to target exactly one run's root regardless of age; teardown uses
    it to clean up after itself when seeding failed before returning a manifest.
    """
    target = f"{RUN_ROOT_PREFIX}{only_run_id}" if only_run_id else None
    now = datetime.now(timezone.utc)
    trashed = 0
    for page in await helper.search_objects("page"):
        parent = page.get("parent") or {}
        if parent.get("type") != "page_id" or parent.get("page_id") != root_page_id:
            continue
        title = _plain_title(page)
        if not title.startswith(RUN_ROOT_PREFIX):
            continue
        if target is not None:
            if title != target:
                continue
        else:
            age = _age_seconds(page, now)
            # Unknown age is treated as young: skipping leaves a loud, recoverable
            # count mismatch, while trashing a live run does not.
            if age is None or age < min_age_seconds:
                logger.info(
                    "SWEEP: leaving run root %s (%s) — age %s < %.0fs, may still be running",
                    title,
                    page.get("id"),
                    "unknown" if age is None else f"{age:.0f}s",
                    min_age_seconds,
                )
                continue
        logger.warning("SWEEP: trashing stale run root %s (%s)", title, page.get("id"))
        await helper.trash_page(str(page["id"]))
        trashed += 1
    return trashed


async def foreign_run_object_ids(
    helper: NotionSourceHelper,
    seed: NotionSeed,
) -> tuple[set[str], set[str]]:
    """Page / data-source ids that carry the IT title prefix but were not created by ``seed``.

    The connector syncs everything the token can see, not just this run's subtree, so a
    concurrent run's fixture tree lands in this run's graph as well. Those objects are
    ours-by-title but not ours-by-id — the same discriminator ``_baseline_object_ids`` uses
    to keep them out of the baseline.

    Resolved at assert time rather than at seed time: the other run keeps creating pages
    after our baseline snapshot, so anything captured earlier would already be stale.
    """
    mine_pages = seed.expected_page_ids | {seed.archived_page_id}
    mine_data_sources = seed.expected_data_source_ids | {seed.archived_db_id}

    foreign_pages = {
        str(p["id"])
        for p in await helper.search_objects("page")
        if _plain_title(p).startswith(TITLE_PREFIX) and str(p["id"]) not in mine_pages
    }
    foreign_data_sources = {
        str(d["id"])
        for d in await helper.search_objects("data_source")
        if _plain_title(d).startswith(TITLE_PREFIX) and str(d["id"]) not in mine_data_sources
    }
    return foreign_pages, foreign_data_sources


async def wait_for_swept_runs_gone(
    helper: NotionSourceHelper,
    root_page_id: str,
    *,
    timeout: int = 120,
    interval: int = 5,
) -> None:
    """Wait until Search no longer returns trashed PHIT-Notion-* run roots under the IT root.

    Notion's search index lags trash; seeding before that settles causes TC-SYNC-001 failures
    with unexpected leftover pages in the graph.
    """
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        stale = []
        for page in await helper.search_objects("page"):
            parent = page.get("parent") or {}
            if parent.get("type") != "page_id" or parent.get("page_id") != root_page_id:
                continue
            title = _plain_title(page)
            if title.startswith(RUN_ROOT_PREFIX):
                stale.append((title, str(page.get("id"))))
        if not stale:
            return
        if asyncio.get_event_loop().time() >= deadline:
            sample = ", ".join(f"{t} ({i})" for t, i in stale[:3])
            raise AssertionError(
                f"Notion search still lists {len(stale)} swept run root(s) after {timeout}s "
                f"(e.g. {sample})"
            )
        logger.info(
            "SWEEP: waiting for search index to drop %d stale run root(s)",
            len(stale),
        )
        await asyncio.sleep(interval)


def _plain_title(page: dict[str, Any]) -> str:
    """Page title as the connector reads it (``_extract_page_title`` order)."""
    properties = page.get("properties") or {}
    for name in ("title", "Title", "Name", "name"):
        prop = properties.get(name) or {}
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title") or [])
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title") or [])
    return ""


async def discover_root_pages(helper: NotionSourceHelper) -> list[str]:
    """Top-of-tree pages the integration can see, excluding this suite's own run roots.

    An integration only ever sees what has been explicitly shared with it, and sharing is a
    Notion UI action with no API — so *something* must be shared by hand. But in a dedicated
    workspace that is normally a single page, and it can be found rather than configured.

    A page counts as a root when it has no accessible ancestor page. Framing it as "no parent we
    can see" rather than "parent is the workspace" keeps teamspace-rooted pages working: the API
    has no teamspace parent type, and what it reports for them is not contractual.
    """
    pages = await helper.search_objects("page")
    accessible = {str(p["id"]) for p in pages}
    roots: list[str] = []
    for page in pages:
        # Only this suite's own run roots are excluded, matched on the full run-root prefix
        # rather than TITLE_PREFIX — a user-created root page is free to be named anything.
        if _plain_title(page).startswith(RUN_ROOT_PREFIX):
            continue
        parent = page.get("parent") or {}
        parent_type = parent.get("type")
        # Database rows and block-nested pages hang off something else that is in scope.
        if parent_type in ("database_id", "data_source_id", "block_id"):
            continue
        if parent_type == "page_id" and str(parent.get("page_id")) in accessible:
            continue
        roots.append(str(page["id"]))
    return roots


async def discover_workspace_root_database(
    helper: NotionSourceHelper,
) -> Optional[tuple[str, str]]:
    """``(database_id, data_source_id)`` for a shared database whose parent is the workspace.

    Only such a database exercises ``_get_database_parent_page_id`` returning None, which is
    what TC-DS-003 asserts. Integrations cannot create one (``POST /v1/databases`` requires a
    page parent), so if none is shared that test skips.
    """
    for data_source in await helper.search_objects("data_source"):
        parent = data_source.get("parent") or {}
        if parent.get("type") != "database_id":
            continue
        database_id = str(parent["database_id"])
        try:
            database = await helper.retrieve_database(database_id)
        except Exception as e:  # noqa: BLE001 - a database we cannot read is simply not a candidate
            logger.debug("discover: skipping unreadable database %s (%s)", database_id, e)
            continue
        if (database.get("parent") or {}).get("type") == "workspace":
            return database_id, str(data_source["id"])
    return None


async def _baseline_object_ids(helper: NotionSourceHelper) -> tuple[set[str], set[str]]:
    """Page / data-source ids already visible to the integration before seeding.

    These are the permanent objects you provisioned (IT root page, workspace-root database).
    Folding them into the expectation keeps the count assertions exact without requiring the
    workspace to be literally empty.

    Anything carrying the IT title prefix is excluded: Notion's search index lags a trash by a
    few seconds, so a page the sweep just removed can still show up here and would then be
    expected in the graph it never reaches.
    """
    pages = {
        str(p["id"]) for p in await helper.search_objects("page")
        if not _plain_title(p).startswith(TITLE_PREFIX)
    }
    data_sources = {
        str(d["id"]) for d in await helper.search_objects("data_source")
        if not "".join(
            t.get("plain_text", "") for t in d.get("title") or []
        ).startswith(TITLE_PREFIX)
    }
    return pages, data_sources


async def seed_notion_workspace(
    helper: NotionSourceHelper,
    *,
    root_page_id: str,
    root_database_id: Optional[str],
    run_id: str,
) -> NotionSeed:
    """Create the full fixture tree and return the manifest.

    ``root_database_id`` is optional: without a workspace-parented database TC-DS-003 has
    nothing to assert against and skips.
    """
    workspace_id, workspace_name = await helper.workspace_info()
    seed = NotionSeed(run_id=run_id, workspace_id=workspace_id, workspace_name=workspace_name)

    if root_database_id:
        seed.root_database_id = root_database_id
        root_db = await helper.retrieve_database(root_database_id)
        seed.root_data_source_id = await helper.first_data_source_id(root_db)

    baseline_pages, baseline_data_sources = await _baseline_object_ids(helper)

    # ---------------------------------------------------------------- structure
    run_root = await helper.create_child_page(root_page_id, f"{RUN_ROOT_PREFIX}{run_id}")
    seed.run_root_page_id = _normalize_id(run_root["id"])
    fixture = await helper.create_child_page(seed.run_root_page_id, FIXTURE_ROOT_TITLE)
    seed.fixture_page_id = _normalize_id(fixture["id"])
    scratch = await helper.create_child_page(seed.run_root_page_id, SCRATCH_ROOT_TITLE)
    seed.scratch_page_id = _normalize_id(scratch["id"])

    # ---------------------------------------------------------------- nested pages
    nested_parent = await helper.create_child_page(
        seed.fixture_page_id, NESTED_PARENT_TITLE, [paragraph_block("Nested parent body.")]
    )
    seed.nested_parent_id = _normalize_id(nested_parent["id"])
    nested_child = await helper.create_child_page(
        seed.nested_parent_id, NESTED_CHILD_TITLE, [paragraph_block("Nested child body.")]
    )
    seed.nested_child_id = _normalize_id(nested_child["id"])

    # ---------------------------------------------------------------- rich page
    await _seed_rich_page(helper, seed)

    # ---------------------------------------------------------------- deep page
    deep = await helper.create_child_page(seed.fixture_page_id, DEEP_PAGE_TITLE)
    seed.deep_page_id = _normalize_id(deep["id"])
    await helper.append_blocks(
        seed.deep_page_id,
        [paragraph_block(f"Deep block {i:03d}") for i in range(DEEP_PAGE_BLOCK_COUNT)],
    )

    # ---------------------------------------------------------------- comments
    await _seed_commented_page(helper, seed)

    # ---------------------------------------------------------------- titles
    standard = await helper.create_child_page(
        seed.fixture_page_id, TITLE_STANDARD_TITLE, [paragraph_block("Standard title page.")]
    )
    seed.title_standard_id = _normalize_id(standard["id"])

    fallback_db = await helper.create_database(
        seed.fixture_page_id, FALLBACK_DB_TITLE,
        properties={FALLBACK_TITLE_PROPERTY: {"title": {}}, "Note": {"rich_text": {}}},
    )
    seed.fallback_db_id = _normalize_id(fallback_db["id"])
    seed.fallback_data_source_id = await helper.first_data_source_id(fallback_db)
    fallback_row = await helper.create_row_page(
        seed.fallback_data_source_id,
        {FALLBACK_TITLE_PROPERTY: {"title": rich_text(TITLE_FALLBACK_TITLE)}},
    )
    seed.title_fallback_row_id = _normalize_id(fallback_row["id"])

    # ---------------------------------------------------------------- fixture database
    fixture_db = await helper.create_database(seed.fixture_page_id, FIXTURE_DB_TITLE)
    seed.fixture_db_id = _normalize_id(fixture_db["id"])
    seed.fixture_data_source_id = await helper.first_data_source_id(fixture_db)

    named_row = await helper.create_row_page(
        seed.fixture_data_source_id,
        {"Name": {"title": rich_text(DB_ROW_NAMED_TITLE)}, "Tag": {"rich_text": rich_text("alpha")}},
    )
    seed.db_row_named_id = _normalize_id(named_row["id"])

    row_with_children = await helper.create_row_page(
        seed.fixture_data_source_id,
        {"Name": {"title": rich_text(DB_ROW_WITH_CHILDREN_TITLE)}},
        children=[paragraph_block("Row body paragraph.")],
    )
    seed.db_row_with_children_id = _normalize_id(row_with_children["id"])
    # A sub-page under the row is what populates TableRowMetadata.children_records.
    row_sub_page = await helper.create_child_page(
        seed.db_row_with_children_id, f"{DB_ROW_WITH_CHILDREN_TITLE}-Sub"
    )
    seed.db_row_sub_page_id = _normalize_id(row_sub_page["id"])

    # ---------------------------------------------------------------- archived (must not sync)
    archived_page = await helper.create_child_page(seed.fixture_page_id, ARCHIVED_PAGE_TITLE)
    seed.archived_page_id = _normalize_id(archived_page["id"])
    await helper.trash_page(seed.archived_page_id)

    archived_db = await helper.create_database(seed.fixture_page_id, ARCHIVED_DB_TITLE)
    seed.archived_db_id = _normalize_id(archived_db["id"])
    archived_ds_id = await helper.first_data_source_id(archived_db)
    await helper.trash_database(seed.archived_db_id)

    # ---------------------------------------------------------------- bulk (pagination)
    await _seed_bulk(helper, seed)

    # ---------------------------------------------------------------- expectations
    _finalize(
        seed,
        baseline_pages=baseline_pages,
        baseline_data_sources=baseline_data_sources,
        archived_data_source_id=archived_ds_id,
    )
    logger.info(
        "SEED done: run=%s pages=%d data_sources=%d files=%d",
        run_id, len(seed.expected_page_ids), len(seed.expected_data_source_ids),
        len(seed.expected_file_ids),
    )
    return seed


async def _seed_rich_page(helper: NotionSourceHelper, seed: NotionSeed) -> None:
    """The page carrying every block type the connector classifies differently."""
    rich = await helper.create_child_page(seed.fixture_page_id, RICH_PAGE_TITLE)
    seed.rich_page_id = _normalize_id(rich["id"])
    seed.rich_page_url = str(rich.get("url") or "")

    uploads: dict[str, tuple[str, str]] = {}
    for relative_path, content_type, block_type in SAMPLE_FILE_FIXTURES:
        filename = relative_path.rsplit("/", 1)[-1]
        uploads[block_type] = (
            await helper.upload_sample_file(relative_path, content_type), filename
        )

    # An SVG exercises the SVG→PNG branch of the base64 conversion. It is optional: if the
    # shared sample-data repo has no .svg yet, that one assertion is skipped rather than
    # failing the suite.
    svg_path = find_sample_file_by_suffix(".svg")
    svg_upload_id = (
        await helper.upload_file(svg_path.name, svg_path.read_bytes(), "image/svg+xml")
        if svg_path else None
    )
    seed.has_svg_image = svg_upload_id is not None

    children: list[dict[str, Any]] = [
        paragraph_block("Rich page intro paragraph."),
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": rich_text("Callout with children."),
                "children": [paragraph_block("Callout child paragraph.")],
            },
        },
        {
            "object": "block", "type": "quote",
            "quote": {
                "rich_text": rich_text("Quote with children."),
                "children": [paragraph_block("Quote child paragraph.")],
            },
        },
        # Downloadable attachments → FileRecord
        file_upload_block("file", *uploads["file"]),
        file_upload_block("pdf", *uploads["pdf"]),
        # Images are embedded as base64 during streaming, never synced as FileRecord. An
        # external image block takes the same conversion path as a Notion-hosted one, so no
        # binary is needed for the PNG case.
        external_media_block("image", IMAGE_PNG_URL),
        *(
            [file_upload_block("image", svg_upload_id, svg_path.name)]
            if svg_upload_id and svg_path else []
        ),
        # Non-downloadable → LINK blocks, never FileRecord
        external_media_block("video", YOUTUBE_EMBED_URL),
        {"object": "block", "type": "bookmark", "bookmark": {"url": BOOKMARK_URL}},
        {"object": "block", "type": "embed", "embed": {"url": EMBED_URL}},
        {
            "object": "block", "type": "table",
            "table": {
                "table_width": 2, "has_column_header": True,
                "children": [
                    {"object": "block", "type": "table_row",
                     "table_row": {"cells": [rich_text("Header A"), rich_text("Header B")]}},
                    {"object": "block", "type": "table_row",
                     "table_row": {"cells": [rich_text("Cell A1"), rich_text("Cell B1")]}},
                ],
            },
        },
    ]
    created = await helper.append_blocks(seed.rich_page_id, children)

    for block in created:
        block_type = str(block.get("type"))
        block_id = _normalize_id(block["id"])
        if block_type in ("file", "pdf", "video"):
            payload = block.get(block_type) or {}
            if payload.get("type") == "external":
                # The only external media block is the YouTube video — an embed platform, so
                # the connector must classify it as a LINK and never as a FileRecord.
                seed.skipped_block_ids["video_embed"] = block_id
            else:
                seed.file_block_ids[block_type] = block_id
        elif block_type == "image":
            key = "image" if "image" not in seed.skipped_block_ids else "image_svg"
            seed.skipped_block_ids[key] = block_id
        elif block_type in ("bookmark", "embed"):
            seed.skipped_block_ids[block_type] = block_id
        elif block_type == "callout":
            seed.callout_block_id = block_id
        elif block_type == "table":
            seed.table_block_id = block_id

    # Synced block: an original with children, then a reference to it. The connector must read
    # the ORIGINAL's children when it walks the reference.
    synced_source = await helper.append_blocks(seed.rich_page_id, [{
        "object": "block", "type": "synced_block",
        "synced_block": {
            "synced_from": None,
            "children": [paragraph_block("Synced source content.")],
        },
    }])
    seed.synced_source_block_id = _normalize_id(synced_source[0]["id"])
    synced_ref = await helper.append_blocks(seed.rich_page_id, [{
        "object": "block", "type": "synced_block",
        "synced_block": {"synced_from": {"block_id": seed.synced_source_block_id}},
    }])
    seed.synced_reference_block_id = _normalize_id(synced_ref[0]["id"])

    # child_page / child_database blocks — the ChildRecord resolution fixtures.
    child_page = await helper.create_child_page(
        seed.rich_page_id, CHILD_REF_PAGE_TITLE, [paragraph_block("Child reference target.")]
    )
    seed.child_ref_page_id = _normalize_id(child_page["id"])
    child_db = await helper.create_database(seed.rich_page_id, CHILD_REF_DB_TITLE)
    seed.child_ref_db_id = _normalize_id(child_db["id"])
    seed.child_ref_data_source_id = await helper.first_data_source_id(child_db)


async def _seed_commented_page(helper: NotionSourceHelper, seed: NotionSeed) -> None:
    """Page-level comment, block-level comment, a threaded reply, and an attachment comment."""
    page = await helper.create_child_page(
        seed.fixture_page_id, COMMENTED_PAGE_TITLE,
        [paragraph_block("Commented paragraph one."), paragraph_block("Commented paragraph two.")],
    )
    seed.commented_page_id = _normalize_id(page["id"])

    blocks = await helper.list_block_children(seed.commented_page_id)
    seed.commented_first_block_id = _normalize_id(blocks[0]["id"])

    page_comment = await helper.create_comment(
        page_id=seed.commented_page_id, text="Page level comment."
    )
    seed.page_comment_id = _normalize_id(page_comment["id"])

    block_comment = await helper.create_comment(
        block_id=seed.commented_first_block_id, text="Block level comment."
    )
    seed.block_comment_id = _normalize_id(block_comment["id"])

    reply = await helper.create_comment(
        discussion_id=str(block_comment["discussion_id"]), text="Threaded reply."
    )
    seed.reply_comment_id = _normalize_id(reply["id"])

    # Two uploads of the SAME filename on one comment: the connector dedups by normalized
    # filename per comment, so this must yield exactly one FileRecord.
    duplicate_uploads = [
        await helper.upload_sample_file(COMMENT_ATTACHMENT_SAMPLE_PATH, "application/pdf")
        for _ in range(2)
    ]
    attachment_comment = await helper.create_comment(
        page_id=seed.commented_page_id,
        text="Comment carrying an attachment.",
        file_upload_ids=duplicate_uploads,
    )
    seed.attachment_comment_id = _normalize_id(attachment_comment["id"])
    # Derive the expected external id from the attachment Notion actually stored: the connector
    # takes the filename from the signed URL's path, which need not equal the upload filename.
    seed.comment_attachment_external_id = comment_attachment_external_id(
        attachment_comment, seed.attachment_comment_id
    )


def comment_attachment_external_id(comment: dict[str, Any], comment_id: str) -> str:
    """Mirror ``_transform_to_comment_file_record``'s id: ``ca_{comment_id}_{normalized_name}``."""
    for attachment in comment.get("attachments") or []:
        file_url = ((attachment.get("file") or {}).get("url")) or ""
        if not file_url:
            continue
        path = unquote(urlparse(file_url).path)
        filename = path.split("/")[-1] if "/" in path else ""
        if filename:
            return f"ca_{comment_id}_{normalize_filename_for_id(filename)}"
    logger.warning(
        "comment %s returned no attachment url; falling back to the upload filename", comment_id
    )
    return f"ca_{comment_id}_{normalize_filename_for_id(COMMENT_ATTACHMENT_FILENAME)}"


async def _seed_bulk(helper: NotionSourceHelper, seed: NotionSeed) -> None:
    """Enough pages / data sources / rows to force multi-page Search and query pagination."""
    for index in range(1, BULK_PAGE_COUNT + 1):
        page = await helper.create_child_page(
            seed.fixture_page_id, BULK_PAGE_TITLE_FMT.format(index=index)
        )
        seed.bulk_page_ids.append(_normalize_id(page["id"]))

    for index in range(1, BULK_DATABASE_COUNT + 1):
        database = await helper.create_database(
            seed.fixture_page_id, BULK_DATABASE_TITLE_FMT.format(index=index)
        )
        seed.bulk_database_ids.append(_normalize_id(database["id"]))
        seed.bulk_data_source_ids.append(await helper.first_data_source_id(database))

    bulk_db = await helper.create_database(seed.fixture_page_id, BULK_DB_TITLE)
    seed.bulk_db_id = _normalize_id(bulk_db["id"])
    seed.bulk_data_source_id = await helper.first_data_source_id(bulk_db)
    for index in range(1, BULK_DB_ROW_COUNT + 1):
        row = await helper.create_row_page(
            seed.bulk_data_source_id,
            {"Name": {"title": rich_text(f"{BULK_DB_TITLE}-Row-{index:02d}")}},
        )
        seed.bulk_row_ids.append(_normalize_id(row["id"]))


def _finalize(
    seed: NotionSeed,
    *,
    baseline_pages: set[str],
    baseline_data_sources: set[str],
    archived_data_source_id: str,
) -> None:
    """Derive the expected graph contents from what was created."""
    seed.expected_page_ids = baseline_pages | {
        seed.run_root_page_id,
        seed.fixture_page_id,
        seed.scratch_page_id,
        seed.nested_parent_id,
        seed.nested_child_id,
        seed.rich_page_id,
        seed.deep_page_id,
        seed.commented_page_id,
        seed.title_standard_id,
        seed.title_fallback_row_id,
        seed.child_ref_page_id,
        seed.db_row_named_id,
        seed.db_row_with_children_id,
        seed.db_row_sub_page_id,
        *seed.bulk_page_ids,
        *seed.bulk_row_ids,
    }
    seed.expected_page_ids.discard(seed.archived_page_id)

    seed.expected_data_source_ids = baseline_data_sources | {
        seed.fixture_data_source_id,
        seed.child_ref_data_source_id,
        seed.fallback_data_source_id,
        seed.bulk_data_source_id,
        *seed.bulk_data_source_ids,
    }
    seed.expected_data_source_ids.discard(archived_data_source_id)

    seed.expected_file_ids = set(seed.file_block_ids.values()) | {
        seed.comment_attachment_external_id
    }


async def wait_for_seed_indexed(
    helper: NotionSourceHelper,
    seed: NotionSeed,
    *,
    timeout: int = 600,
    interval: int = 15,
) -> None:
    """Block until Search returns every seeded page and data source.

    The connector enumerates solely through Search, whose index lags writes. Syncing straight
    after seeding leaves the last-created objects invisible, which shows up as a handful of
    pages simply missing from the graph.
    """
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        indexed_pages = {str(p["id"]) for p in await helper.search_objects("page")}
        indexed_sources = {str(d["id"]) for d in await helper.search_objects("data_source")}
        missing_pages = seed.expected_page_ids - indexed_pages
        missing_sources = seed.expected_data_source_ids - indexed_sources
        if not missing_pages and not missing_sources:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"Notion search index did not catch up within {timeout}s: "
                f"{len(missing_pages)} page(s) and {len(missing_sources)} data source(s) "
                f"still missing (e.g. {sorted(missing_pages)[:3]} / "
                f"{sorted(missing_sources)[:3]})"
            )
        logger.info(
            "SEED: waiting for search index — %d page(s), %d data source(s) outstanding",
            len(missing_pages), len(missing_sources),
        )
        await asyncio.sleep(interval)


async def teardown_seed(helper: NotionSourceHelper, seed: NotionSeed) -> None:
    """Trash the run root — descendants inherit ``in_trash``, so this clears the whole tree."""
    if not seed.run_root_page_id:
        return
    try:
        await helper.trash_page(seed.run_root_page_id)
    except Exception as e:  # noqa: BLE001 - teardown is best-effort
        logger.warning("TEARDOWN: failed to trash run root %s: %s", seed.run_root_page_id, e)


__all__ = [
    "NotionSeed",
    "comment_attachment_external_id",
    "seed_notion_workspace",
    "foreign_run_object_ids",
    "sweep_stale_runs",
    "wait_for_swept_runs_gone",
    "wait_for_seed_indexed",
    "teardown_seed",
]

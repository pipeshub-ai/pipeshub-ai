"""Removal of what left a Confluence Data Center space, shared by the team and personal connectors.

Each sync reads a space's database listing (``GET /rest/api/content``: current
and archived pages, current blog posts, as the connector's account sees them).
A stored item missing from it, or one the sync filters clearly leave out, is
removed; a current item it has that PipesHub doesn't hold is synced by id, so
content the account can see again comes back. Spaces the listing of spaces no
longer has are removed with their records and checkpoints. A read that fails
removes nothing. An item that fails to save holds the checkpoint for a bounded
number of syncs, then is given up on until it changes.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.core.registry.filters import SyncFilterKey
from app.models.entities import Record, RecordGroup, RecordGroupType, RecordType

# The content search moves date filters by this much (``time_offset_hours``).
TIME_OFFSET_HOURS = 24
# A filter date only removes what it leaves out by more than this, so an item
# near the edge is never removed by one check and brought back by the next.
FILTER_REMOVAL_MARGIN = timedelta(hours=2 * TIME_OFFSET_HOURS)
CONTENT_LIST_LIMIT = 100
RECORD_SCAN_PAGE_SIZE = 500
ID_SYNC_CHUNK = 50
RECORD_DELETE_CHUNK = 200
# How many runs the checkpoint is held for pages that failed to save before they
# are given up on, so one broken page can't stop a space from ever moving on.
MAX_FAILED_PAGE_ATTEMPTS = 5

# (id, title, revision marker) of an item that failed to save.
FailedItem = tuple[str, str, str]


def stored_map(value: object) -> dict[str, Any]:
    """A map kept in a sync point as JSON text (graph stores such as Neo4j can't hold nested maps)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def item_last_modified_when(item_data: dict[str, Any]) -> str | None:
    """Extract last modified timestamp from Confluence item data.

    Tries history.lastUpdated.when first, then falls back to version.when or version.createdAt.
    """
    history = item_data.get("history")
    if isinstance(history, dict):
        last_updated = history.get("lastUpdated")
        if isinstance(last_updated, dict):
            when = last_updated.get("when")
            if when:
                return when
    version = item_data.get("version")
    if isinstance(version, dict):
        return version.get("when") or version.get("createdAt")
    return None


def item_revision_marker(item_data: dict[str, Any]) -> str | None:
    """What identifies this revision of an item: its last-modified time, else its version number.

    None when neither is known, so a given-up item can't be matched and is never skipped.
    """
    when = item_last_modified_when(item_data)
    if when:
        return when
    history = item_data.get("history")
    last_updated = history.get("lastUpdated") if isinstance(history, dict) else None
    version = item_data.get("version")
    number = (last_updated.get("number") if isinstance(last_updated, dict) else None) or (
        version.get("number") if isinstance(version, dict) else None
    )
    return f"version:{number}" if number is not None else None


class ContentListing(NamedTuple):
    """What one space's page or blog post listing read.

    ``full`` means it ran without a checkpoint, so ``seen`` holds every item the
    sync filters admit; otherwise ``seen`` holds only what changed since then.
    ``last_sync_data`` is the checkpoint as read, ``failed`` the items that
    failed to save, ``given_up`` the items given up on (id to revision marker),
    and ``synced_any`` whether anything was saved: what saving the checkpoint needs.
    """

    full: bool
    complete: bool
    seen: frozenset[str]
    checkpoint_key: str
    last_sync_data: dict[str, Any] | None = None
    failed: tuple[FailedItem, ...] = ()
    given_up: dict[str, str] | None = None
    synced_any: bool = False


class ListedItem(NamedTuple):
    """One page or blog post as the space's database listing returns it."""

    status: str
    ancestors: frozenset[str]
    created: datetime | None
    modified: datetime | None


class ConfluenceDataCenterRemovalMixin:
    """Needs ``data_entities_processor``, ``connector_id``, ``logger``, ``sync_filters``,
    ``pages_sync_point``, ``_get_fresh_datasource``, ``_space_listing_complete`` and
    ``_saved_space_ids`` (the spaces this sync wrote to the graph), and
    a ``_sync_content_by_ids`` that syncs the given ids and says whether it read them all.
    """

    async def _sync_content_by_ids(self, space: RecordGroup, record_type: RecordType, ids: list[str]) -> bool:
        raise NotImplementedError

    async def _save_content_checkpoint(
        self,
        sync_point_key: str,
        last_sync_data: dict[str, Any] | None,
        failed_items: list[FailedItem],
        given_up: dict[str, str],
        content_type: str,
        space_key: str,
        *,
        synced_any: bool,
        hold: bool = False,
    ) -> None:
        """Move the checkpoint to now, or keep it while any item that failed still has attempts left.

        Each failed item has its own count. One that fails ``MAX_FAILED_PAGE_ATTEMPTS`` syncs
        in a row is given up on and skipped until its last-modified time changes. With
        ``hold`` the checkpoint stays where it is, but the counts are still saved, so an
        item that keeps failing is given up on and stops holding it.
        """
        stored = last_sync_data or {}
        attempts_before = stored_map(stored.get("failedPages"))
        held: dict[str, int] = {}
        newly_given_up: list[str] = []
        for item_id, title, when in failed_items:
            attempts = int(attempts_before.get(item_id) or 0) + 1
            if attempts >= MAX_FAILED_PAGE_ATTEMPTS:
                if when:
                    given_up[item_id] = when
                newly_given_up.append(f"'{title}' ({item_id})")
            else:
                held[item_id] = attempts
        if newly_given_up:
            self.logger.error(
                f"❌ {content_type.capitalize()}s {', '.join(newly_given_up)} in space {space_key} still could not be "
                f"saved after {MAX_FAILED_PAGE_ATTEMPTS} syncs; moving on without them. They are read again when "
                "they next change"
            )

        counts_changed = bool(
            failed_items or stored.get("failedPages") or given_up != stored_map(stored.get("givenUpPages"))
        )
        if held or (hold and counts_changed):
            checkpoint: dict[str, Any] = {}
            if stored.get("last_sync_time"):
                checkpoint["last_sync_time"] = stored["last_sync_time"]
            if held:
                titles = {item_id: title for item_id, title, _ in failed_items}
                self.logger.warning(
                    f"Keeping the {content_type}s checkpoint for space {space_key}: "
                    + ", ".join(f"'{titles[i]}' ({i}, attempt {n} of {MAX_FAILED_PAGE_ATTEMPTS})" for i, n in held.items())
                    + " could not be saved and will be read again next sync"
                )
        elif not hold and (synced_any or counts_changed):
            # Given-up items are kept until they change: by-id sync would otherwise retry one the search no longer returns.
            checkpoint = {"last_sync_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
            self.logger.info(f"Updated {content_type}s sync checkpoint to {checkpoint['last_sync_time']}")
        else:
            return

        # Written even when empty: Neo4j merges sync point fields, so an omitted field would keep its old value.
        if held or stored.get("failedPages"):
            checkpoint["failedPages"] = json.dumps(held, sort_keys=True)
        if given_up or stored.get("givenUpPages"):
            checkpoint["givenUpPages"] = json.dumps(given_up, sort_keys=True)
        await self.pages_sync_point.update_sync_point(sync_point_key, checkpoint)

    async def _reconcile_space_content(
        self, space: RecordGroup, record_type: RecordType, listing: ContentListing
    ) -> bool:
        """Bring the space's stored pages or blog posts in line with what the space holds.

        What exists is the space's database listing: current and archived pages,
        current blog posts, that the connector's account can see. A stored item
        missing from it (deleted, trashed, purged, moved away, or restricted from
        the account) is removed, as is one the sync filters now leave out. A
        current item the listing has but PipesHub doesn't hold is synced by id,
        whatever its last modified time, so an item comes back once the account
        can see it again. Returns False when a read or delete failed, so the
        caller holds the checkpoint.
        """
        existing = await self._list_space_content(space.short_name, record_type)
        if existing is None:
            return False
        try:
            stored = await self._stored_content(str(space.external_group_id), record_type)
        except Exception as e:
            self.logger.warning(f"Could not read the stored records of space {space.short_name}; nothing removed: {e}")
            return False

        settled = True
        stored_ids = {r.external_record_id for r in stored}
        missing = sorted(
            item_id for item_id, item in existing.items()
            if item.status == "current" and item_id not in stored_ids and item_id not in listing.seen
            and self._listed_item_may_pass_filters(item_id, item, record_type)
        )
        for start in range(0, len(missing), ID_SYNC_CHUNK):
            complete = await self._sync_content_by_ids(space, record_type, missing[start:start + ID_SYNC_CHUNK])
            settled = settled and complete

        gone = [r for r in stored if r.external_record_id not in existing]
        left_out = [
            r for r in stored
            if r.external_record_id in existing
            and self._listed_item_filtered_out(r.external_record_id, existing[r.external_record_id], record_type)
        ]
        if gone:
            self.logger.info(
                f"Removing {len(gone)} {record_type.value} records the account can no longer find in space {space.short_name}"
            )
        if left_out:
            self.logger.info(
                f"Removing {len(left_out)} {record_type.value} records the sync filters now leave out in space {space.short_name}"
            )
        if gone or left_out:
            settled = await self._delete_content_records(gone + left_out) and settled
        return settled

    async def _list_space_content(self, space_key: str, record_type: RecordType) -> dict[str, ListedItem] | None:
        """Every page (current and archived) or current blog post in a space the account can see.

        Read from the database listing, not the search index; None unless read to
        the end. Only a response without a next link ends it; an empty page that
        still points further, or one without a results list, is a failed read.
        A server too old to know archived pages answers that listing's first
        page with 400, which reads as none; a 400 on a later page is a failed read.
        """
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"
        statuses = ("current", "archived") if record_type == RecordType.CONFLUENCE_PAGE else ("current",)
        listed: dict[str, ListedItem] = {}
        datasource = await self._get_fresh_datasource()
        for status in statuses:
            start = 0
            while True:
                try:
                    response = await datasource.list_space_content_v1(
                        space_key, content_type, status=status, start=start, limit=CONTENT_LIST_LIMIT,
                        expand="ancestors,history,version",
                    )
                except Exception as e:
                    self.logger.warning(f"Could not list the {content_type}s of space {space_key}; nothing removed: {e}")
                    return None
                if status == "archived" and response is not None and response.status == HttpStatusCode.BAD_REQUEST.value:
                    if start == 0:
                        break
                    # A server that served earlier archived pages knows the listing; a 400 now is a failed read.
                    self.logger.warning(
                        f"Could not list the archived {content_type}s of space {space_key} past {start}; nothing removed"
                    )
                    return None
                data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
                results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    self.logger.warning(
                        f"Could not list the {content_type}s of space {space_key} "
                        f"(HTTP {response.status if response else 'no response'}); nothing removed"
                    )
                    return None
                for item in results:
                    if isinstance(item, dict) and item.get("id"):
                        listed[str(item["id"])] = self._listed_item(item, status)
                next_url = (data.get("_links") or {}).get("next")
                if not next_url:
                    break
                if not results:
                    self.logger.warning(f"Can't follow the {content_type} listing of space {space_key}; nothing removed")
                    return None
                start += len(results)
        return listed

    @staticmethod
    def _listed_item(item: dict[str, Any], status: str) -> ListedItem:
        def when(value: object) -> datetime | None:
            if not isinstance(value, str) or not value:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        history = item.get("history") or {}
        version = item.get("version") or {}
        return ListedItem(
            status=str(item.get("status") or status),
            ancestors=frozenset(str(a["id"]) for a in item.get("ancestors") or [] if isinstance(a, dict) and a.get("id")),
            created=when(history.get("createdDate")),
            modified=when(version.get("when") or (history.get("lastUpdated") or {}).get("when")),
        )

    async def _stored_content(self, space_id: str, record_type: RecordType) -> list[Record]:
        """Stored records of this type in the space, without placeholder ancestors."""
        stored: list[Record] = []
        after_key: str | None = None
        while True:
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, space_id, RECORD_SCAN_PAGE_SIZE, after_key
            )
            stored.extend(r for r in page if r.record_type == record_type and not r.is_placeholder)
            if len(page) < RECORD_SCAN_PAGE_SIZE:
                return stored
            after_key = page[-1].id

    def _id_filter(self, record_type: RecordType) -> tuple[set[str], str] | None:
        key = SyncFilterKey.PAGE_IDS if record_type == RecordType.CONFLUENCE_PAGE else SyncFilterKey.BLOGPOST_IDS
        ids_filter = self.sync_filters.get(key)
        if ids_filter is None or not ids_filter.get_value():
            return None
        operator = ids_filter.get_operator()
        return {str(i) for i in ids_filter.get_value()}, operator.value if hasattr(operator, "value") else str(operator)

    def _passes_id_filter(self, item_id: str, item: ListedItem, record_type: RecordType) -> bool:
        """The id filter as the listing applies it: a page filter covers the pages below it too."""
        id_filter = self._id_filter(record_type)
        if id_filter is None:
            return True
        ids, operator = id_filter
        named = item_id in ids or (record_type == RecordType.CONFLUENCE_PAGE and bool(item.ancestors & ids))
        return not named if operator == "not_in" else named

    def _date_bounds(self, key: SyncFilterKey) -> tuple[datetime | None, datetime | None]:
        date_filter = self.sync_filters.get(key)
        if not date_filter:
            return None, None

        def parse(value: str | None) -> datetime | None:
            if not value:
                return None
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        after, before = date_filter.get_datetime_iso()
        return parse(after), parse(before)

    def _outside_dates(self, item: ListedItem, margin: timedelta) -> bool:
        for key, value in ((SyncFilterKey.MODIFIED, item.modified), (SyncFilterKey.CREATED, item.created)):
            after, before = self._date_bounds(key)
            if value is None:
                continue
            if (after and value < after - margin) or (before and value > before + margin):
                return True
        return False

    def _listed_item_filtered_out(self, item_id: str, item: ListedItem, record_type: RecordType) -> bool:
        """Whether the applied sync filters clearly leave this item out."""
        return not self._passes_id_filter(item_id, item, record_type) or self._outside_dates(item, FILTER_REMOVAL_MARGIN)

    def _listed_item_may_pass_filters(self, item_id: str, item: ListedItem, record_type: RecordType) -> bool:
        """Worth asking for by id: the id filter admits it and no date filter clearly rules it out."""
        return self._passes_id_filter(item_id, item, record_type) and not self._outside_dates(item, FILTER_REMOVAL_MARGIN)

    async def _delete_content_records(self, records: list[Record]) -> bool:
        """Delete pages or blog posts with their file attachments and comments; True if every delete succeeded.

        Comments and their replies (with their files) are records under the page,
        so they go with it. Child pages stay, as Confluence moves them up: the
        page's own delete follows only its file attachments and clears the parent
        link of what survives, so browse lists it at the space root.
        """
        comment_types = (RecordType.COMMENT, RecordType.INLINE_COMMENT)
        failed = 0
        for record in records:
            try:
                comments = [
                    c.id for c in await self.data_entities_processor.get_records_by_parent(
                        self.connector_id, record.external_record_id
                    )
                    if c.record_type in comment_types
                ]
                if comments:
                    result = await self.data_entities_processor.on_records_deleted_cascade(
                        comments, self.connector_id, cascade_children=True
                    )
                    if not self._cascade_succeeded(result):
                        raise RuntimeError(f"its comments could not all be deleted: {result}")
                result = await self.data_entities_processor.on_records_deleted_cascade(
                    [record.id], self.connector_id, cascade_children=False
                )
                if not self._cascade_succeeded(result):
                    raise RuntimeError(f"delete failed: {result}")
            except Exception as e:
                failed += 1
                self.logger.warning(f"Could not delete {record.record_type} {record.external_record_id}: {e}")
        if failed:
            self.logger.warning(f"{failed} of {len(records)} records could not be deleted; retrying next sync")
        return not failed

    @staticmethod
    def _cascade_succeeded(result: object) -> bool:
        return isinstance(result, dict) and bool(result.get("success", True)) and not result.get("failed_count")

    async def _remove_spaces_out_of_scope(self, spaces: list[RecordGroup]) -> None:
        """Delete the stored spaces this sync no longer lists, with their records and checkpoints.

        A space drops out when the space filter leaves it out, it is deleted or
        archived, or the account lost access; it comes back, read in full, if it
        is listed again. Runs only after a space listing read to the end, and
        only when the listed set differs from the one last cleaned up or a
        removal is still unfinished. A listing with no spaces at all removes
        nothing: that is the account failing, not every space leaving.
        """
        if not self._space_listing_complete:
            return
        in_scope = sorted({str(s.external_group_id) for s in spaces})
        if not in_scope:
            self.logger.warning("The space listing came back empty; no space is removed")
            return
        key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "confluence_space_scope", "all")
        cleaned = await self.pages_sync_point.read_sync_point(key) or {}
        pending = {str(p) for p in (cleaned.get("pending") or []) if str(p) not in in_scope}
        if cleaned.get("space_ids") == in_scope and not pending:
            return

        wanted = set(in_scope)
        after_key: str | None = None
        try:
            stored_spaces = await self._stored_space_ids()
            if not stored_spaces or not self._saved_space_ids <= stored_spaces:
                # A read that misses a space this sync saved failed: the stores answer [] on error.
                raise RuntimeError("the stored spaces read back without the spaces just saved")
            # From the stored spaces too: one whose last record already went has nothing in the scan below.
            by_space: dict[str, list[Record]] = {space_id: [] for space_id in stored_spaces - wanted}
            while True:
                page = await self.data_entities_processor.get_records_by_status(
                    self.connector_id, None, limit=RECORD_SCAN_PAGE_SIZE, after_key=after_key,
                )
                for r in page:
                    if r.external_record_group_id and r.external_record_group_id not in wanted:
                        by_space.setdefault(r.external_record_group_id, []).append(r)
                if len(page) < RECORD_SCAN_PAGE_SIZE:
                    break
                after_key = page[-1].id
        except Exception as e:
            self.logger.warning(f"Could not read the stored spaces; no space removed: {e}")
            return

        unfinished: list[str] = []
        for space_id in sorted(set(by_space) | pending):
            try:
                removed = await self._remove_space(space_id, by_space.get(space_id, []))
            except Exception as e:
                removed = False
                self.logger.warning(f"Could not finish removing space {space_id}; retrying next sync: {e}")
            if not removed:
                unfinished.append(space_id)
        # The store merges fields, so an empty list clears what an earlier run left pending.
        await self.pages_sync_point.update_sync_point(
            key, {"pending": unfinished} if unfinished else {"space_ids": in_scope, "pending": []}
        )

    async def _stored_space_ids(self) -> set[str]:
        groups = await self.data_entities_processor.get_nodes_by_filters(
            collection=CollectionNames.RECORD_GROUPS.value,
            filters={"connectorId": self.connector_id, "groupType": RecordGroupType.CONFLUENCE_SPACES.value},
            return_fields=["externalGroupId"],
        )
        return {str(g["externalGroupId"]) for g in groups or [] if isinstance(g, dict) and g.get("externalGroupId")}

    async def _remove_space(self, space_id: str, records: list[Record]) -> bool:
        """Delete one space's records, clear its checkpoints, then drop the space; True if all of it worked."""
        self.logger.info(f"Removing space {space_id} and its {len(records)} records: this sync no longer lists it")
        try:
            group = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, space_id)
        except Exception as e:
            self.logger.warning(f"Could not read space {space_id}; not removed: {e}")
            return False
        deleted: set[str] = set()
        ids = [r.id for r in records]
        for start in range(0, len(ids), RECORD_DELETE_CHUNK):
            # An id a full cascade already took counts as a failed root if passed again.
            chunk = [i for i in ids[start:start + RECORD_DELETE_CHUNK] if i not in deleted]
            if not chunk:
                continue
            result = await self.data_entities_processor.on_records_deleted_cascade(
                chunk, self.connector_id, cascade_children=True
            )
            if not self._cascade_succeeded(result):
                self.logger.warning(f"Could not delete the records of space {space_id}; retrying next sync: {result}")
                return False
            deleted.update(
                str(d["record_id"]) for d in result.get("deleted_records") or []
                if isinstance(d, dict) and d.get("record_id")
            )
        if group is not None and group.short_name:
            # An empty checkpoint reads as none, so the space is read in full if it is listed again.
            for content_type in ("pages", "blogposts"):
                checkpoint_key = generate_record_sync_point_key(
                    RecordType.WEBPAGE.value, f"confluence_{content_type}", group.short_name
                )
                await self.pages_sync_point.update_sync_point(checkpoint_key, {"last_sync_time": ""})
        if group is not None and not await self.data_entities_processor.on_record_group_deleted(
            space_id, self.connector_id
        ):
            self.logger.warning(f"Could not delete space {space_id}; retrying next sync")
            return False
        return True

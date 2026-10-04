# pyright: ignore-file

"""One set of sync scenarios, run against every connector that plugs in an adapter.

Every connector suite had its own idea of what "incremental sync works" means,
and most checked only the graph. This module asks the same questions of each
connector, and asks every store:

- add: the new item becomes a record, gets vectors holding its text, and the
  owner can find it in search;
- update content: the record's vectors are replaced (the new text is there, the
  old text is gone) and search finds the new text;
- update metadata: a rename changes the record in place (same record, new name);
- update permissions: sharing with a second person lets them open and find it,
  and unsharing takes that away again;
- delete: the record, its vectors and its search hit are gone;
- full sync: every record survives with the same id and stays searchable, and
  an item the connector never saw is picked up;
- filter change: an already-synced item that the new filter excludes is removed
  from every store (graph, vectors, blob storage and MongoDB), and the rest stay
  searchable;
- indexing settings change: with manual indexing switched on, a new item is
  synced but not indexed;
- scheduled sync: with a one-minute schedule, a change lands without anyone
  triggering a sync.

A connector plugs in with two things, both in a ``*_scenario_matrix_test.py``
module next to its suite:

1. A ``ScenarioAdapter`` subclass that performs the source-side actions against
   the real service or container (create, update, rename, share, delete, and the
   filter that excludes one item).
2. A test class that subclasses ``ConnectorScenarioMatrix``, carries the
   connector's pytest marker (so it runs in that connector's shard), names the
   actions its source cannot do in ``UNSUPPORTED``, and lists any product bug
   found by reading the code in ``KNOWN_BUGS`` (strict xfail). The module also
   defines a module-scoped ``scenario_adapter`` fixture that creates the
   connector and yields the adapter.

Rounds are shared. Every item the scenarios need is created in one go and synced
once; every mutation is applied in one go and synced once. Each test then checks
its own part. That keeps a connector's whole matrix to a handful of syncs, which
is what makes it affordable in the nightly shards.

Waits poll real conditions (a record's status, its vectors' text, a search hit),
never fixed sleeps. The matrix runs on the Neo4j leg only, unless
``SCENARIO_MATRIX_GRAPHS`` says otherwise (see ``graph_leg_skip``).
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, ClassVar, Iterable

import pytest

from helper.connector_visibility import search_connector_as, search_connector_as_admin
from helper.cross_store import RecordFootprint, assert_fully_deleted
from helper.graph_provider_utils import async_poll_until, wait_for_sync_completion
from helper.record_access import access_matches, record_access_status, wait_for_record_access
from helper.second_user import NO_ACCESS_STATUSES
from helper.storage_incremental import restart_sync
from retrieval.ranking import virtual_id_of

if TYPE_CHECKING:
    from helper.blob_store import BlobStoreProbe
    from helper.graph_provider import GraphProviderProtocol
    from helper.mongo_store import MongoStoreProbe
    from helper.second_user import SecondUser
    from helper.vector_store import VectorStoreProbe
    from pipeshub_client import PipeshubClient

logger = logging.getLogger("scenario-matrix")

SYNC_TIMEOUT_SEC = 300
INDEX_TIMEOUT_SEC = 300
SEARCH_TIMEOUT_SEC = 180
SYNC_ATTEMPTS = 3
POLL_INTERVAL_SEC = 5
SCHEDULE_INTERVAL_MINUTES = 1
# The scheduler's first tick lands up to one interval after the schedule is set,
# and the sync it starts then has to finish.
SCHEDULED_PICKUP_TIMEOUT_SEC = 420

SCHEDULED_ELSEWHERE = (
    "runs on the Nextcloud matrix only: the scheduler publishes the same sync event for "
    "every connector type, and each run waits on a real scheduler tick"
)

FILTER_KEEPS_EXCLUDED_ITEM = (
    "a narrowed sync filter must remove the excluded item from every store (record, "
    "vectors, stored files). This connector's full sync only drops the item's sync edges "
    "(event_service.py, delete_connector_sync_edges) and never deletes it, so its record "
    "and vectors stay behind where nobody can find them"
)

INDEXED = "COMPLETED"
NOT_INDEXED = "AUTO_INDEX_OFF"
MANUAL_INDEXING_FILTER = {
    "indexing": {
        "values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}}
    }
}


class Action(str, enum.Enum):
    """A source-side action a scenario needs. A test is skipped when its source lacks one."""

    CREATE = "create_item"
    UPDATE_CONTENT = "update_content"
    UPDATE_METADATA = "update_metadata"
    CHANGE_PERMISSION = "change_permission"
    DELETE = "delete_item"
    SET_FILTER = "set_filter"
    SET_INDEXING = "set_indexing"
    FULL_SYNC = "trigger_full_sync"
    INCREMENTAL_SYNC = "trigger_incremental_sync"
    SCHEDULED_SYNC = "scheduled_sync"


class Role(str, enum.Enum):
    """What an item is for. Each scenario owns one, so a scenario's change never touches another's item."""

    KEEP = "keep"
    CONTENT = "content"
    METADATA = "metadata"
    PERMISSION = "permission"
    DELETE = "delete"
    FILTERED = "filtered"
    FULL = "full"
    INDEX_OFF = "indexoff"
    SCHEDULED = "scheduled"


# Created in the first round, when the action they exist for is supported.
_ROLE_NEEDS: dict[Role, Action] = {
    Role.KEEP: Action.CREATE,
    Role.CONTENT: Action.UPDATE_CONTENT,
    Role.METADATA: Action.UPDATE_METADATA,
    Role.PERMISSION: Action.CHANGE_PERMISSION,
    Role.DELETE: Action.DELETE,
    Role.FILTERED: Action.SET_FILTER,
}


@dataclass
class SourceItem:
    """One item an adapter made at the source."""

    role: Role
    key: str
    record_name: str
    text: str
    token: str
    external_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordView:
    """The parts of a graph record the scenarios read, whichever shape the provider returned.

    ``get_record_by_name`` returns the node's properties as a dict (camelCase,
    ``_key`` on ArangoDB, ``id`` on Neo4j); ``get_record_by_external_id``
    returns a ``Record`` model (snake_case attributes).
    """

    id: str
    name: str
    virtual_record_id: str | None
    indexing_status: str | None
    version: Any
    revision: str | None
    external_id: str | None

    @classmethod
    def of(cls, record: Any) -> "RecordView | None":
        if record is None:
            return None
        if isinstance(record, dict):
            return cls(
                id=str(record.get("_key") or record.get("id") or ""),
                name=str(record.get("recordName") or record.get("name") or ""),
                virtual_record_id=record.get("virtualRecordId"),
                indexing_status=record.get("indexingStatus"),
                version=record.get("version"),
                revision=record.get("externalRevisionId"),
                external_id=record.get("externalRecordId"),
            )
        return cls(
            id=str(getattr(record, "id", "") or ""),
            name=str(getattr(record, "record_name", "") or ""),
            virtual_record_id=getattr(record, "virtual_record_id", None),
            indexing_status=getattr(record, "indexing_status", None),
            version=getattr(record, "version", None),
            revision=getattr(record, "external_revision_id", None),
            external_id=getattr(record, "external_record_id", None),
        )

    def changed_since(self, before: "RecordView") -> bool:
        """The source change reached the record: a new version or a new source revision."""
        return self.version != before.version or self.revision != before.revision


def new_token() -> str:
    """A word no embedding model or other document shares, so a hit on it means this item."""
    return f"mx{uuid.uuid4().hex[:10]}"


def body_marker(token: str) -> str:
    """Words only an item's body holds.

    Adapters name items after the bare token, and many connectors index the name
    with the body (a page's heading, a table's DDL), so the bare old token stays
    in the vectors of an item whose body was replaced.
    """
    return f"The {token} review"


def item_text(role: Role, token: str) -> str:
    return (
        f"Scenario matrix {role.value} note {token}. "
        f"{body_marker(token)} covers the quarterly budget, hiring plan and launch city."
    )


def search_verdict(
    status: int, body: Any, virtual_record_id: str, *, denial_is_miss: bool = False,
) -> bool | None:
    """What one search proved: ``True`` a hit, ``False`` a miss, ``None`` nothing.

    A miss needs a 200 whose hit list parsed. A 5xx, an expired login, or a body
    without ``searchResults`` proves nothing, so a delete or unshare check must
    keep polling rather than pass while search is down. For someone other than
    the owner, an explicit refusal (403/404: the connector does not resolve for
    them at all) is the answer to "can they find it", so the sharee's searches
    count it as a miss. The owner's never do: a refusal to the owner is a fault.
    """
    if denial_is_miss and status in NO_ACCESS_STATUSES:
        return False
    if status != 200 or not isinstance(body, dict):
        return None
    response = body.get("searchResponse") or body
    hits = response.get("searchResults") if isinstance(response, dict) else None
    if not isinstance(hits, list):
        return None
    record_ids: list[str] = []
    for hit in hits:
        record_id = virtual_id_of(hit) if isinstance(hit, dict) else None
        # A hit that names no record cannot prove this record is absent.
        if record_id is None:
            return None
        record_ids.append(record_id)
    return virtual_record_id in record_ids


class ScenarioAdapter:
    """Source-side actions for one connector. Subclass per connector.

    Only the actions the connector's test class does not list in
    ``UNSUPPORTED`` are ever called. ``owner`` is who the scenarios search as
    (``None``: the admin the suite runs as, which owns what it synced);
    ``sharee`` is the second person a share grants access to, whose email must
    exist at the source.
    """

    source: ClassVar[str] = "the source"
    # Some connectors recreate every record on each sync (Local FS reading a
    # folder); for them "the same record survives" is not a claim to check.
    record_ids_survive_sync: ClassVar[bool] = True

    def __init__(
        self,
        *,
        client: "PipeshubClient",
        graph: "GraphProviderProtocol",
        connector_id: str,
        owner: "SecondUser | None" = None,
        sharee: "SecondUser | None" = None,
    ) -> None:
        self.client = client
        self.graph = graph
        self.connector_id = connector_id
        self.owner = owner
        self.sharee = sharee

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        raise NotImplementedError

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        raise NotImplementedError

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        """Rename or retitle ``item``; return it with the ``record_name`` it now has."""
        raise NotImplementedError

    async def change_permission(
        self, item: SourceItem, sharee: "SecondUser", *, grant: bool
    ) -> None:
        raise NotImplementedError

    async def delete_item(self, item: SourceItem) -> None:
        raise NotImplementedError

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        """The ``filters`` payload that leaves out ``excluded`` and none of ``kept``.

        Nested as the connector reads it (``{"sync": {"values": {...}}}``). Saving
        ``filters.sync`` replaces the whole block, so a connector already scoped by a
        sync filter (a run folder, one project) must repeat that scope here.
        """
        raise NotImplementedError

    async def apply_filters(self, filters: dict[str, Any]) -> None:
        """Save a ``filters`` payload. The save needs the connector off, and turning it back on syncs."""
        self.client.update_connector_filters_sync_safe(self.connector_id, filters=filters)

    async def trigger_incremental_sync(self) -> None:
        restart_sync(self.client, self.connector_id)

    async def trigger_full_sync(self) -> None:
        self.client.resync_connector(self.connector_id, full_sync=True)

    async def cleanup(self) -> None:
        """Remove what the adapter made at the source, including any share it left behind."""


def needs(*actions: Action) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Record which source actions a scenario test relies on, for ``apply_static_marks``."""

    def mark(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._matrix_needs = tuple(actions)  # type: ignore[attr-defined]
        return fn

    return mark


def static_marks_for(cls: type, fn: Callable[..., Any]) -> list[pytest.MarkDecorator]:
    """The skip or xfail a scenario test gets from its connector's declarations.

    A skip names the action the source lacks and why; an xfail (strict, so a
    fix turns the run red until the entry goes) names the product bug.
    """
    needed: Iterable[Action] = getattr(fn, "_matrix_needs", ())
    unsupported: dict[str, str] = getattr(cls, "UNSUPPORTED", {})
    source = getattr(cls, "SOURCE", "") or "this source"
    if Action.SCHEDULED_SYNC in needed and not getattr(cls, "SCHEDULED_SYNC", False):
        return [pytest.mark.skip(reason=SCHEDULED_ELSEWHERE)]
    for action in needed:
        reason = unsupported.get(action.value)
        if reason:
            return [pytest.mark.skip(reason=f"not supported by {source}: {reason}")]
    scenario = fn.__name__.removeprefix("test_matrix_")
    bug = getattr(cls, "KNOWN_BUGS", {}).get(scenario)
    if bug:
        return [pytest.mark.xfail(reason=bug, strict=True)]
    return []


def graph_leg_skip(graph: str | None = None, legs: str | None = None) -> pytest.MarkDecorator | None:
    """Skip the matrix on graph legs it is not asked to run on (Neo4j only, by default).

    What the matrix tests is the connector: its code is the same whichever graph
    backend stores the result, and the existing suites already run on both. A
    matrix per leg would double its nightly cost for little more coverage, so it
    runs on Neo4j, the backend installs ship with. ``SCENARIO_MATRIX_GRAPHS``
    (comma-separated, e.g. ``neo4j,arango``) widens it.
    """
    graph = (graph if graph is not None else os.getenv("TEST_GRAPH_DB_TYPE", "neo4j")).strip().lower()
    legs = legs if legs is not None else os.getenv("SCENARIO_MATRIX_GRAPHS", "neo4j")
    wanted = {leg.strip().lower() for leg in legs.split(",") if leg.strip()}
    if graph in wanted:
        return None
    return pytest.mark.skip(
        reason=f"the scenario matrix runs on the {', '.join(sorted(wanted))} leg; this is the "
        f"{graph} leg (set SCENARIO_MATRIX_GRAPHS to widen it)"
    )


def apply_static_marks(items: Iterable[pytest.Item]) -> None:
    """Called from ``connectors/conftest.py`` at collection, so the reasons show before anything runs."""
    leg_skip = graph_leg_skip()
    for item in items:
        cls = getattr(item, "cls", None)
        fn = getattr(item, "function", None)
        if cls is None or fn is None or not issubclass(cls, ConnectorScenarioMatrix):
            continue
        marks = [leg_skip] if leg_skip else static_marks_for(cls, fn)
        for marker in marks:
            item.add_marker(marker)


class _Rounds:
    """Run each named round once per module; later callers get its result or its failure."""

    def __init__(self) -> None:
        self._done: dict[str, tuple[bool, Any]] = {}

    async def run(self, name: str, body: Callable[[], Awaitable[Any]]) -> Any:
        if name not in self._done:
            try:
                self._done[name] = (True, await body())
            except BaseException as exc:  # noqa: BLE001 - replayed to every test of the round
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self._done[name] = (False, exc)
        ok, value = self._done[name]
        if not ok:
            raise value
        return value


class MatrixRun:
    """One connector's walk through the scenarios, shared by the tests of one module."""

    def __init__(
        self,
        adapter: ScenarioAdapter,
        *,
        unsupported: dict[str, str],
        vector: "VectorStoreProbe",
        known_bugs: dict[str, str] | None = None,
        blob: "BlobStoreProbe | None" = None,
        mongo: "MongoStoreProbe | None" = None,
        org_id: str = "",
    ) -> None:
        self.adapter = adapter
        self.client = adapter.client
        self.graph = adapter.graph
        self.connector_id = adapter.connector_id
        self.vector = vector
        self.blob = blob
        self.mongo = mongo
        self.org_id = org_id
        self.unsupported = unsupported
        self.known_bugs = dict(known_bugs or {})
        self.items: dict[Role, SourceItem] = {}
        self.added: dict[Role, RecordView] = {}
        self.rounds = _Rounds()

    def supports(self, action: Action) -> bool:
        return action.value not in self.unsupported

    def is_known_bug(self, scenario: str) -> bool:
        """A strict-xfail scenario. Shared rounds must not wait on the change it never makes."""
        return scenario in self.known_bugs

    # -- reading the stores ---------------------------------------------------

    async def record(self, item: SourceItem) -> RecordView | None:
        if item.external_id:
            return RecordView.of(
                await self.graph.get_record_by_external_id(self.connector_id, item.external_id)
            )
        return RecordView.of(await self.graph.get_record_by_name(self.connector_id, item.record_name))

    async def wait_indexed(self, item: SourceItem, timeout: int = INDEX_TIMEOUT_SEC) -> RecordView:
        async def _indexed() -> RecordView | None:
            view = await self.record(item)
            if view and view.indexing_status == INDEXED and view.virtual_record_id:
                return view
            return None

        try:
            return await async_poll_until(
                _indexed, timeout=timeout, interval=POLL_INTERVAL_SEC,
                description=f"{item.record_name!r} to be indexed",
            )
        except TimeoutError as exc:
            view = await self.record(item)
            state = f"status {view.indexing_status}" if view else "not in the graph"
            raise AssertionError(
                f"{self.adapter.source}: {item.record_name!r} was not indexed within {timeout}s "
                f"({state})."
            ) from exc

    async def texts(self, virtual_record_id: str) -> list[str]:
        return await self.vector.content_texts(virtual_record_id)

    async def wait_vectors_hold(
        self, item: SourceItem, token: str, *, absent: Iterable[str] = (),
        timeout: int = INDEX_TIMEOUT_SEC,
    ) -> RecordView:
        """Wait until the record is indexed and its chunks carry ``token`` and none of ``absent``."""
        absent = tuple(absent)
        last: dict[str, Any] = {}

        async def _holds() -> RecordView | None:
            view = await self.record(item)
            if not view or view.indexing_status != INDEXED or not view.virtual_record_id:
                last.update(view=view, texts=None)
                return None
            texts = await self.texts(view.virtual_record_id)
            last.update(view=view, texts=texts)
            joined = "\n".join(texts)
            if token in joined and not any(old in joined for old in absent):
                return view
            return None

        try:
            return await async_poll_until(
                _holds, timeout=timeout, interval=POLL_INTERVAL_SEC,
                description=f"vectors of {item.record_name!r} to hold {token}",
            )
        except TimeoutError as exc:
            view, texts = last.get("view"), last.get("texts")
            if texts is None:
                detail = f"record: {view}"
            else:
                joined = "\n".join(texts)
                detail = (
                    f"{len(texts)} chunk(s); new text present: {token in joined}; "
                    f"old text still present: {[t for t in absent if t in joined]}"
                )
            raise AssertionError(
                f"{self.adapter.source}: the vectors of {item.record_name!r} never matched its "
                f"source text within {timeout}s ({detail})."
            ) from exc

    async def wait_content_replaced(
        self, item: SourceItem, timeout: int = INDEX_TIMEOUT_SEC,
    ) -> RecordView:
        """Wait until an edited item's vectors hold its new body and none of its old one."""
        return await self.wait_vectors_hold(
            item, item.token, absent=[body_marker(item.extra["old_token"])], timeout=timeout,
        )

    def _search(self, query: str, as_user: "SecondUser | None"):
        if as_user is None:
            return search_connector_as_admin(self.client, self.connector_id, query)
        return search_connector_as(as_user, self.connector_id, query)

    async def wait_search(
        self, query: str, virtual_record_id: str, *, expect: bool,
        as_user: "SecondUser | None" = None, who: str = "the owner",
        timeout: int = SEARCH_TIMEOUT_SEC, denial_is_miss: bool = False,
    ) -> None:
        last: dict[str, Any] = {}

        def _ask() -> bool | None:
            resp = self._search(query, as_user)
            try:
                body = resp.json()
            except ValueError:
                body = None
            last.update(status=resp.status_code, verdict=None)
            verdict = search_verdict(
                resp.status_code, body, virtual_record_id, denial_is_miss=denial_is_miss,
            )
            last["verdict"] = verdict
            return verdict

        async def _settled() -> bool:
            return await asyncio.to_thread(_ask) is expect

        try:
            await async_poll_until(
                _settled, timeout=timeout, interval=POLL_INTERVAL_SEC,
                description=f"search as {who} {'to find' if expect else 'to miss'} {virtual_record_id}",
            )
        except TimeoutError as exc:
            seen = {True: "a hit", False: "a miss", None: "no usable answer"}[last.get("verdict")]
            raise AssertionError(
                f"{self.adapter.source}: search as {who} for {query!r} should "
                f"{'find' if expect else 'not find'} virtual record {virtual_record_id}, "
                f"and still did not after {timeout}s (last search: HTTP "
                f"{last.get('status', 'none')}, {seen})."
            ) from exc

    async def wait_gone(self, item: SourceItem, timeout: int = SYNC_TIMEOUT_SEC) -> None:
        async def _gone() -> bool:
            return await self.record(item) is None

        try:
            await async_poll_until(
                _gone, timeout=timeout, interval=POLL_INTERVAL_SEC,
                description=f"{item.record_name!r} to leave the graph",
            )
        except TimeoutError as exc:
            raise AssertionError(
                f"{self.adapter.source}: {item.record_name!r} is still in the graph {timeout}s "
                "after it was deleted at the source."
            ) from exc

    # -- driving the connector ------------------------------------------------

    async def settle(self) -> None:
        await wait_for_sync_completion(
            self.client, self.graph, self.connector_id, timeout=SYNC_TIMEOUT_SEC,
        )

    async def sync_until(
        self, condition: Callable[[], Awaitable[bool]], description: str, *, full: bool = False,
    ) -> None:
        """Sync until ``condition`` holds. Retries absorb a sync that started before the change landed."""
        for attempt in range(1, SYNC_ATTEMPTS + 1):
            if full:
                await self.adapter.trigger_full_sync()
            else:
                await self.adapter.trigger_incremental_sync()
            await self.settle()
            if await condition():
                return
            logger.warning(
                "%s: %s not seen after sync %d of %d", self.adapter.source, description,
                attempt, SYNC_ATTEMPTS,
            )
        raise AssertionError(
            f"{self.adapter.source}: {description} did not happen after {SYNC_ATTEMPTS} "
            f"{'full' if full else 'incremental'} syncs."
        )

    async def _create(self, role: Role) -> SourceItem:
        token = new_token()
        item = await self.adapter.create_item(role, item_text(role, token), token)
        self.items[role] = item
        return item

    # -- rounds -----------------------------------------------------------------

    async def add_round(self) -> None:
        """Create one item per supported scenario, sync once, and wait for each to be indexed."""

        async def body() -> None:
            roles = [r for r, action in _ROLE_NEEDS.items() if self.supports(action)]
            for role in roles:
                await self._create(role)

            async def _all_present() -> bool:
                return all([await self.record(self.items[r]) is not None for r in roles])

            await self.sync_until(_all_present, f"the new items {[r.value for r in roles]}")
            for role in roles:
                self.added[role] = await self.wait_indexed(self.items[role])

        await self.rounds.run("add", body)

    async def mutate_round(self) -> None:
        """Apply every update and the delete at once, then sync once."""
        await self.add_round()

        async def body() -> None:
            expect: list[Callable[[], Awaitable[bool]]] = []
            if self.supports(Action.UPDATE_CONTENT):
                item = self.items[Role.CONTENT]
                token = new_token()
                item.extra["old_token"] = item.token
                updated = await self.adapter.update_content(item, item_text(Role.CONTENT, token), token)
                updated.extra["old_token"] = item.extra["old_token"]
                self.items[Role.CONTENT] = updated
                before = self.added[Role.CONTENT]

                async def _content_moved() -> bool:
                    view = await self.record(self.items[Role.CONTENT])
                    return bool(view and view.changed_since(before))

                if not self.is_known_bug("incr_update_content"):
                    expect.append(_content_moved)
            if self.supports(Action.UPDATE_METADATA):
                item = self.items[Role.METADATA]
                renamed = await self.adapter.update_metadata(item)
                renamed.extra["old_name"] = item.record_name
                self.items[Role.METADATA] = renamed

                async def _renamed() -> bool:
                    view = await self.record(self.items[Role.METADATA])
                    return bool(view and view.name == self.items[Role.METADATA].record_name)

                if not self.is_known_bug("incr_update_metadata"):
                    expect.append(_renamed)
            if self.supports(Action.CHANGE_PERMISSION) and self.adapter.sharee is not None:
                await self.adapter.change_permission(
                    self.items[Role.PERMISSION], self.adapter.sharee, grant=True
                )
            if self.supports(Action.DELETE):
                await self.adapter.delete_item(self.items[Role.DELETE])

                async def _deleted() -> bool:
                    return await self.record(self.items[Role.DELETE]) is None

                # Otherwise a delete that never lands fails the content and rename tests too.
                if not self.is_known_bug("incr_delete"):
                    expect.append(_deleted)

            async def _all_landed() -> bool:
                return all([await check() for check in expect])

            await self.sync_until(_all_landed, "the content update, rename and delete")

        await self.rounds.run("mutate", body)

    def item(self, role: Role) -> SourceItem:
        return self.items[role]


async def _admin_can_find(run: MatrixRun, item: SourceItem, view: RecordView) -> None:
    await run.wait_search(item.text, view.virtual_record_id or "", expect=True,
                          as_user=run.adapter.owner)


class ConnectorScenarioMatrix:
    """The scenarios. A connector's test class subclasses this and sets the class attributes.

    ``UNSUPPORTED`` maps an ``Action`` value to why the source cannot do it;
    ``KNOWN_BUGS`` maps a scenario name (the test name after ``test_matrix_``)
    to a product bug found by reading the code. ``SCHEDULED_SYNC`` opts one
    connector into the scheduler test; it waits on a real scheduler tick, so it
    runs on one connector, not all of them.
    """

    pytestmark = [pytest.mark.scenario_matrix, pytest.mark.asyncio(loop_scope="session")]
    SOURCE: ClassVar[str] = ""
    UNSUPPORTED: ClassVar[dict[str, str]] = {}
    KNOWN_BUGS: ClassVar[dict[str, str]] = {}
    SCHEDULED_SYNC: ClassVar[bool] = False

    @pytest.mark.order(1)
    @needs(Action.CREATE, Action.INCREMENTAL_SYNC)
    async def test_matrix_incr_add(self, scenario_run: MatrixRun) -> None:
        """New items become records with vectors holding their text, and the owner can find them."""
        run = scenario_run
        await run.add_round()
        for role, view in run.added.items():
            item = run.item(role)
            await run.wait_vectors_hold(item, item.token)
            await _admin_can_find(run, item, view)

    @pytest.mark.order(2)
    @needs(Action.CREATE, Action.UPDATE_CONTENT)
    async def test_matrix_incr_update_content(self, scenario_run: MatrixRun) -> None:
        """An edit replaces the record's vectors: the new text is in, the old text is out."""
        run = scenario_run
        await run.mutate_round()
        before = run.added[Role.CONTENT]
        item = run.item(Role.CONTENT)
        view = await run.wait_content_replaced(item)
        assert view.changed_since(before), (
            f"{run.adapter.source}: the record's version and revision did not move after the edit"
        )
        if run.adapter.record_ids_survive_sync:
            assert view.id == before.id, (
                f"{run.adapter.source}: editing the item replaced its record "
                f"({before.id} -> {view.id}) instead of updating it"
            )
        if before.virtual_record_id and before.virtual_record_id != view.virtual_record_id:
            await run.vector.assert_embeddings_gone(before.virtual_record_id)
        await _admin_can_find(run, item, view)

    @pytest.mark.order(3)
    @needs(Action.CREATE, Action.UPDATE_METADATA)
    async def test_matrix_incr_update_metadata(self, scenario_run: MatrixRun) -> None:
        """A rename updates the record in place: same record, new name, still searchable."""
        run = scenario_run
        await run.mutate_round()
        before = run.added[Role.METADATA]
        item = run.item(Role.METADATA)
        view = await run.wait_indexed(item)
        assert view.name == item.record_name, (
            f"{run.adapter.source}: record is named {view.name!r}, not {item.record_name!r}"
        )
        if run.adapter.record_ids_survive_sync:
            assert view.id == before.id, (
                f"{run.adapter.source}: renaming replaced the record ({before.id} -> {view.id})"
            )
        names = Counter(await run.graph.fetch_record_names(run.connector_id))
        assert names[item.record_name] == 1, (
            f"{run.adapter.source}: {names[item.record_name]} records carry {item.record_name!r}"
        )
        old_name = item.extra["old_name"]
        assert names[old_name] == 0, (
            f"{run.adapter.source}: a record still carries the old name {old_name!r}"
        )
        await _admin_can_find(run, item, view)

    @pytest.mark.order(4)
    @needs(Action.CREATE, Action.CHANGE_PERMISSION)
    async def test_matrix_incr_update_permissions(self, scenario_run: MatrixRun) -> None:
        """Sharing with the second person lets them open and find the item; unsharing takes it back."""
        run = scenario_run
        sharee = run.adapter.sharee
        if sharee is None:
            pytest.skip(f"{run.adapter.source}: no second person at the source to share with")
        await run.mutate_round()
        item = run.item(Role.PERMISSION)
        view = await run.wait_indexed(item)
        vrid = view.virtual_record_id or ""

        await asyncio.to_thread(
            wait_for_record_access, sharee, view.id, expect_access=True,
            description=f"{run.adapter.source} item shared with them",
        )
        await run.wait_search(item.text, vrid, expect=True, as_user=sharee, who="the sharee")

        async def body() -> None:
            await run.adapter.change_permission(item, sharee, grant=False)

            async def _revoked() -> bool:
                status = await asyncio.to_thread(record_access_status, sharee, view.id)
                return access_matches(status, expect_access=False)

            await run.sync_until(_revoked, "the unshare")

        await run.rounds.run("revoke", body)
        await run.wait_search(item.text, vrid, expect=False, as_user=sharee, who="the sharee",
                              denial_is_miss=True)
        await _admin_can_find(run, item, view)

    @pytest.mark.order(5)
    @needs(Action.CREATE, Action.DELETE)
    async def test_matrix_incr_delete(self, scenario_run: MatrixRun) -> None:
        """A deleted item's record, vectors and search hit are all gone."""
        run = scenario_run
        await run.mutate_round()
        before = run.added[Role.DELETE]
        item = run.item(Role.DELETE)
        # A known-bug delete is expected to stay; there is no point waiting the full timeout.
        await run.wait_gone(item, timeout=60 if run.is_known_bug("incr_delete") else SYNC_TIMEOUT_SEC)
        assert before.virtual_record_id
        await run.vector.assert_embeddings_gone(before.virtual_record_id)
        await run.wait_search(item.text, before.virtual_record_id, expect=False,
                              as_user=run.adapter.owner)

    @pytest.mark.order(6)
    @needs(Action.CREATE, Action.FULL_SYNC)
    async def test_matrix_full_sync(self, scenario_run: MatrixRun) -> None:
        """A full sync keeps every record (same id, one copy), keeps it searchable, and picks up an unseen item."""
        run = scenario_run
        await run.add_round()
        if any(run.supports(a) for a in (Action.UPDATE_CONTENT, Action.UPDATE_METADATA, Action.DELETE)):
            await run.mutate_round()
        survivors = [r for r in run.added if r is not Role.DELETE or not run.supports(Action.DELETE)]

        async def body() -> dict[Role, RecordView]:
            before = {r: await run.wait_indexed(run.item(r)) for r in survivors}
            fresh = await run._create(Role.FULL)

            async def _fresh_present() -> bool:
                return await run.record(fresh) is not None

            await run.sync_until(_fresh_present, "the item created before the full sync", full=True)
            return before

        before = await run.rounds.run("full", body)
        fresh = run.item(Role.FULL)
        view = await run.wait_vectors_hold(fresh, fresh.token)
        await _admin_can_find(run, fresh, view)

        names = Counter(await run.graph.fetch_record_names(run.connector_id))
        for role, then in before.items():
            item = run.item(role)
            now = await run.wait_indexed(item)
            if run.adapter.record_ids_survive_sync:
                assert now.id == then.id, (
                    f"{run.adapter.source}: full sync replaced {item.record_name!r} "
                    f"({then.id} -> {now.id})"
                )
            assert names[item.record_name] == 1, (
                f"{run.adapter.source}: {names[item.record_name]} records carry "
                f"{item.record_name!r} after the full sync"
            )
            # Full sync deletes and rewrites every permission edge; a record it
            # failed to rewrite stays in the graph but drops out of search.
            await _admin_can_find(run, item, now)
        # A connector whose deletes never land is already a strict xfail on incr_delete;
        # repeating that here would hide every other full-sync check behind it.
        if (Role.DELETE in run.items and run.supports(Action.DELETE)
                and not run.is_known_bug("incr_delete")):
            assert await run.record(run.item(Role.DELETE)) is None, (
                f"{run.adapter.source}: the full sync brought back an item deleted at the source"
            )

    @pytest.mark.order(7)
    @needs(Action.CREATE, Action.SET_FILTER)
    async def test_matrix_filter_change(self, scenario_run: MatrixRun) -> None:
        """An already-synced item a narrowed filter excludes leaves every store; the rest stay searchable."""
        run = scenario_run
        await run.add_round()
        excluded = run.item(Role.FILTERED)
        before = run.added[Role.FILTERED]
        kept = [run.item(Role.KEEP)]
        vrid = before.virtual_record_id or ""
        prefix = f"{run.org_id}/PipesHub/records/{vrid}"
        vendor = (await run.mongo.storage_vendor_under_path(prefix) if run.mongo else None) or "local"

        async def body() -> None:
            await run.adapter.apply_filters(await run.adapter.exclusion_filter(excluded, kept))
            await run.settle()

        await run.rounds.run("filter", body)
        # Graph by name, so wait on the record first: the other stores follow its delete.
        await run.wait_gone(excluded)
        await assert_fully_deleted(
            RecordFootprint(
                record_name=excluded.record_name, virtual_record_id=vrid,
                org_id=run.org_id, connector_id=run.connector_id,
            ),
            run.graph, run.vector, run.blob, run.mongo, storage_vendor=vendor,
        )
        await run.wait_search(excluded.text, vrid, expect=False, as_user=run.adapter.owner)
        keep = run.item(Role.KEEP)
        await _admin_can_find(run, keep, await run.wait_indexed(keep))

    @pytest.mark.order(8)
    @needs(Action.CREATE, Action.SET_INDEXING)
    async def test_matrix_indexing_settings_change(self, scenario_run: MatrixRun) -> None:
        """With manual indexing on, a new item is synced but not indexed; indexed ones stay searchable."""
        run = scenario_run
        await run.add_round()

        async def body() -> SourceItem:
            item = await run._create(Role.INDEX_OFF)
            await run.adapter.apply_filters(MANUAL_INDEXING_FILTER)
            await run.settle()

            async def _present() -> bool:
                return await run.record(item) is not None

            if not await _present():
                await run.sync_until(_present, "the item created with manual indexing on")
            return item

        item = await run.rounds.run("indexing", body)
        view = await run.record(item)
        assert view is not None
        assert view.indexing_status == NOT_INDEXED, (
            f"{run.adapter.source}: with manual indexing on, a new item should be "
            f"{NOT_INDEXED}, but is {view.indexing_status}"
        )
        if view.virtual_record_id:
            assert await run.vector.count_for_virtual_record(view.virtual_record_id) == 0, (
                f"{run.adapter.source}: an item synced with manual indexing on has vectors"
            )
            await run.wait_search(item.text, view.virtual_record_id, expect=False,
                                  as_user=run.adapter.owner)
        keep = run.item(Role.KEEP)
        await _admin_can_find(run, keep, await run.wait_indexed(keep))

    @pytest.mark.order(9)
    @needs(Action.CREATE, Action.SCHEDULED_SYNC)
    async def test_matrix_scheduled_sync(self, scenario_run: MatrixRun) -> None:
        """On a one-minute schedule, a new item lands with no sync triggered by anyone."""
        run = scenario_run
        schedule = {
            "selectedStrategy": "SCHEDULED",
            "scheduledConfig": {"intervalMinutes": SCHEDULE_INTERVAL_MINUTES, "timezone": "UTC"},
        }
        run.client.update_connector_filters_sync_safe(run.connector_id, sync=schedule)
        try:
            # Re-enabling runs a sync of its own; the item must come after it.
            await run.settle()
            item = await run._create(Role.SCHEDULED)

            async def _landed() -> bool:
                return await run.record(item) is not None

            try:
                await async_poll_until(
                    _landed, timeout=SCHEDULED_PICKUP_TIMEOUT_SEC, interval=POLL_INTERVAL_SEC,
                    description="the scheduled sync to pick up the new item",
                )
            except TimeoutError as exc:
                raise AssertionError(
                    f"{run.adapter.source}: with a {SCHEDULE_INTERVAL_MINUTES}-minute schedule "
                    f"and no manual trigger, {item.record_name!r} was not synced within "
                    f"{SCHEDULED_PICKUP_TIMEOUT_SEC}s"
                ) from exc
            view = await run.wait_vectors_hold(item, item.token)
            await _admin_can_find(run, item, view)
        finally:
            run.client.update_connector_filters_sync_safe(
                run.connector_id, sync={"selectedStrategy": "MANUAL"}
            )

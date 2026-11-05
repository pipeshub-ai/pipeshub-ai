import asyncio
import contextvars
import functools
import inspect
import logging
import os
import random
import re
from collections.abc import Awaitable, Callable, Collection
from contextlib import asynccontextmanager, suppress
from logging import Logger
from typing import Any, AsyncContextManager, Optional, TypeVar

# Import Neo4j exceptions with fallback for compatibility
try:
    from neo4j.exceptions import DriverError, Neo4jError, TransientError
    NEO4J_AVAILABLE = True
except ImportError:
    DriverError = Neo4jError = TransientError = None
    NEO4J_AVAILABLE = False

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.core import org_context
from app.exceptions.graph_db_exceptions import GraphWriteError
from app.models.entities import (
    AppMetadata,
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    Org,
    Person,
    Record,
    RecordGroup,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db import edge_effects
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.taxonomy import MAX_TAXONOMY_ALIASES
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    OPENED_KEY,
    UNSAFE_KEY,
    ScopeGenerationConflict,
    ScopeGenerationError,
    new_unsafe_mark,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

_T = TypeVar("_T")

#: Tries of one unsafe mark written after a write that could not be undone.
_MARK_ATTEMPTS = 3

# An ArangoDB write-write conflict (1200) clears only once the other transaction
# commits, and an indexing stream transaction can hold a record for seconds. So
# the waits are 0.5, 1, 2, 4, 4s, each +/-20% so colliding writers drift apart:
# 9.2s to 13.8s across six attempts. Neo4j deadlocks share the schedule.
_RETRY_ATTEMPTS = 6
_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 4.0
_RETRY_JITTER = 0.2


def _retry_delay(failed_attempt: int) -> float:
    """Seconds to wait after the failed attempt with this 0-based number."""
    nominal = min(_RETRY_MAX_DELAY, _RETRY_BASE_DELAY * (2 ** failed_attempt))
    return nominal * random.uniform(1 - _RETRY_JITTER, 1 + _RETRY_JITTER)


def ensure_written(result: bool | None, what: str) -> None:
    """Raise GraphWriteError when a write answered False instead of raising
    (ArangoDB's batch_upsert_nodes does, on a document it refused), so the
    transaction around it rolls back rather than committing the rest."""
    if not result:
        raise GraphWriteError(f"The graph database did not save {what}")


# "[1200]" is how a batch reports one, e.g. "[1200] timeout waiting to lock key".
_WRITE_CONFLICT = re.compile(r'write-write conflict|"errorNum"\s*:\s*1200\b|\[1200\]')


def is_write_conflict(exception: Exception) -> bool:
    """An ArangoDB write-write conflict (errorNum 1200): another transaction wrote
    the same document; running the whole transaction again is the remedy."""
    return bool(_WRITE_CONFLICT.search(str(exception)))


# The Neo4j conflicts with a concurrent writer that a re-run resolves.
_NEO4J_CONFLICTS = ("DeadlockDetected", "LockAcquisitionTimeout")


def _names_deadlock(exception: BaseException) -> bool:
    exception_str = str(exception)
    if NEO4J_AVAILABLE and TransientError and isinstance(exception, TransientError):
        return any(conflict in exception_str for conflict in _NEO4J_CONFLICTS)

    return "TransientError" in type(exception).__name__ and any(
        conflict in exception_str for conflict in _NEO4J_CONFLICTS
    )


def _is_deadlock_error(exception: Exception) -> bool:
    """
    Check if exception is a Neo4j deadlock error, or was raised because of one:
    the driver answers each statement after the deadlock with "Transaction
    failed", raised from it.
    Module-level function used by both the decorator and GraphDataStore.
    """
    seen: set[int] = set()
    error: BaseException | None = exception
    while error is not None and id(error) not in seen:
        if _names_deadlock(error):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


is_transient_conflict = _is_deadlock_error


def ends_explicit_transaction(exception: BaseException) -> bool:
    """A failure the Neo4j server or driver reported. The explicit transaction
    it was raised in runs no further statement, so going on after it only moves
    the failure to the next one."""
    return NEO4J_AVAILABLE and isinstance(exception, (Neo4jError, DriverError))


def _is_retryable(instance: object, exception: Exception) -> bool:
    """A Neo4j deadlock, or a failure the instance's data store calls transient
    (an ArangoDB write-write conflict, which never says "deadlock")."""
    if _is_deadlock_error(exception):
        return True
    store = getattr(instance, "data_store_provider", None)
    # `is True`: a mocked provider answers with a truthy mock, which is not a yes.
    return isinstance(store, DataStoreProvider) and store.is_transient_error(exception) is True


# False while retry_on_deadlock still has an attempt left after the current one.
_ON_LAST_ATTEMPT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "retry_on_deadlock_on_last_attempt", default=True
)


def will_retry(instance: object, exception: Exception) -> bool:
    """Whether the retry_on_deadlock call this runs in will run again after this
    failure. A handler inside one logs such a failure as a warning: the decorator
    reports each retry, and an error there alerted on syncs that then succeeded."""
    return not _ON_LAST_ATTEMPT.get() and _is_retryable(instance, exception)


def retry_on_deadlock(max_retries: int = _RETRY_ATTEMPTS):
    """
    Decorator that retries an async function on deadlocks and write conflicts.

    When one is detected, the entire function is re-executed from scratch,
    which naturally creates a fresh transaction on retry. Besides Neo4j's
    deadlock error, it retries whatever the decorated object's
    ``data_store_provider`` reports as transient.

    Waits between attempts follow ``_retry_delay``: about 11.5s in all by default.

    Args:
        max_retries: Maximum number of attempts (default: 6)

    Usage:
        @retry_on_deadlock()
        async def on_new_records(self, records):
            async with self.data_store_provider.transaction() as tx_store:
                # transaction code here
                pass
    """
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            logger = logging.getLogger(func.__module__)
            last_exception = None
            for attempt in range(max_retries):
                last_attempt = _ON_LAST_ATTEMPT.set(attempt == max_retries - 1)
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    retryable = _is_retryable(args[0] if args else None, e)

                    if retryable and attempt < max_retries - 1:
                        backoff = _retry_delay(attempt)
                        logger.warning(
                            f"Deadlock or write conflict in {func.__name__} "
                            f"(attempt {attempt + 1}/{max_retries}), "
                            f"retrying in {backoff:.1f}s: {str(e)[:200]}"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        if retryable:
                            logger.error(
                                f"Deadlock or write conflict persists in {func.__name__} "
                                f"after {max_retries} attempts: {str(e)[:200]}"
                            )
                        raise
                finally:
                    _ON_LAST_ATTEMPT.reset(last_attempt)

            raise last_exception
        return wrapper
    return decorator

read_collections = [
    collection.value for collection in CollectionNames
]

write_collections = [
    collection.value for collection in CollectionNames
]

# ---- the only handle the store has on the provider --------------------------------------------------------------------------
# `GraphTransactionStore.graph_provider` is this proxy. A read goes through. Anything else must happen inside a store method that
# is classified (structural, with how to find its connector, or harmless): those are wrapped at the bottom of this module and set
# `_CLASSIFIED_SCOPE`. A provider write reached from anywhere else (an unlisted method, a helper given the provider, an alias, a
# bound method, `getattr`, the raw client) finds no scope: under pytest it raises `UnclassifiedProviderWrite`; in production it is
# logged once per name and let through, so a missed classification never takes a sync down. The scope is read when the attribute is fetched:
# a bound method fetched inside a classified method and called after it returned is not seen (a limit; the guard test over the store's source
# and review keep that shape out).
_CLASSIFIED_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar("graph_store_classified_scope", default=None)
_READ_PREFIXES = ("get_", "find_", "read_", "is_", "opens_", "has_", "count_", "list_", "filter_")
_WRITE_WORDS = ("or_create", "or_insert", "or_update", "and_create", "and_delete", "and_update", "and_set", "upsert", "ensure",
                "create", "delete", "remove", "insert", "replace")
_RAW_CLIENTS = frozenset({"client", "http_client"})
_RAW_ACCESS = _RAW_CLIENTS | {"execute_query", "execute_aql", "execute"}
_warned: set[str] = set()


class UnclassifiedProviderWrite(RuntimeError):
    """A graph write reached the provider from a store method nobody classified as structural or harmless."""


def _is_read(name: str) -> bool:
    return name.startswith(_READ_PREFIXES) and not any(w in name for w in _WRITE_WORDS)


class _GuardedProvider:
    __slots__ = ("_provider", "_on_unclassified", "_before_unclassified")

    def __init__(
        self, provider: IGraphDBProvider, on_unclassified: Callable[[], None] | None = None,
        before_unclassified: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_on_unclassified", on_unclassified)
        object.__setattr__(self, "_before_unclassified", before_unclassified)

    def __getattr__(self, name: str):  # noqa: ANN204
        attr = getattr(self._provider, name)
        if name in ("logger",) or (not callable(attr) and name not in _RAW_ACCESS) or (_is_read(name) and name not in _RAW_ACCESS):
            return attr
        if name not in _RAW_ACCESS and _CLASSIFIED_SCOPE.get() is not None:
            return attr
        if os.environ.get("PYTEST_CURRENT_TEST") or name in _RAW_CLIENTS or not callable(attr):
            # the raw client (`client`, `http_client`) cannot be wrapped to mark the organisation's connectors before its first write,
            # and nothing reaches it through a transaction store: it is refused, in production too
            raise UnclassifiedProviderWrite(f"graph_provider.{name} is reached outside a classified store method")
        if self._on_unclassified is not None:
            self._on_unclassified()  # fail closed: the connectors this transaction saw are marked unsafe when it ends
        if name not in _warned:
            _warned.add(name)
            logging.getLogger(__name__).error("graph_provider.%s reached outside a classified store method", name)
        before = self._before_unclassified
        if before is None or not callable(attr):
            return attr

        async def after_marking(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            await before()  # may refuse: then the write never reaches the provider
            result = attr(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        return after_marking

    def __setattr__(self, name: str, value: object) -> None:
        setattr(self._provider, name, value)


class GraphTransactionStore(TransactionStore):
    """
    Graph database transaction-aware data store using IGraphDBProvider.
    """

    def __init__(self, graph_provider: IGraphDBProvider, txn: str, *, explicit: bool = False) -> None:
        self.unclassified_write = False
        self._org_marked_for_unclassified = False
        # The structure bookkeeping (the guarded provider, the reads before and after a structural write, the bump) exists only while the
        # scope fields are on: with them off the store reaches the provider directly and makes no query it did not make before.
        self.tracked = scope_fields_enabled()
        self.graph_provider = (
            _GuardedProvider(graph_provider, self._note_unclassified, self._mark_org_before_unclassified_write) if self.tracked else graph_provider)
        self.txn = txn  # Transaction ID (string) for HTTP provider
        self.explicit = explicit  # the block runs in one real transaction
        self.logger = graph_provider.logger
        # Lookups whose answer cannot change for the life of the transaction.
        # One batch is 100 records from a single connector, so the record group
        # and the permission users are a handful of objects fetched a hundred
        # times each: measured at 12 graph calls per record, ~5 of them these
        # repeats. The transaction is exactly how long the answers are stable,
        # which makes it the right scope.
        self._memo: dict[tuple, object] = {}
        # Connectors whose structure or permissions this transaction changes (see `_STRUCTURAL_WRITERS`); each gets one bump of
        # its scope generation, in this transaction, at the end of the block.
        self.structural_connectors: set[str] = set()
        self._connector_of: dict[tuple[str, str], str | None] = {}
        # nodes this transaction created: no stamp can be stale for them, whatever edges it then gives them
        self.new_nodes: set[tuple[str, str]] = set()
        # Connectors this transaction counted as opened (before an unclassified write outside a transaction), the ones whose unsafe mark was then
        # written, and the ones counted as closed: only a connector marked is closed, so a closing step never covers a mark that did not land.
        self.opened: set[str] = set()
        self.marked: set[str] = set()
        self.closed: set[str] = set()

    def _note_unclassified(self) -> None:
        # Before the write is forwarded: with no organisation it could not be charged to one at the end, and outside an explicit
        # transaction it would already be committed with nothing to mark or roll back. It is refused instead (`MissingOrgContext`).
        org_context.current()
        self.unclassified_write = True

    async def _mark_org_before_unclassified_write(self) -> None:
        """Outside an explicit transaction an unclassified write commits as it runs and cannot be undone, and which connector it touches
        is not known: every connector of the organisation is marked unsafe first, and the write is refused (the error propagates) when
        the lookup or a mark fails. In an explicit transaction the rollback undoes it, and the marks are written when the block ends."""
        if self.explicit or self._org_marked_for_unclassified:
            return
        org_id = org_context.current().org_id
        apps = await self.graph_provider._provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []
        seen = {c for c in self._connector_of.values() if c} | self.structural_connectors
        ids = seen | {str(k) for a in apps if (k := a.get("_key") or a.get("id"))}
        for connector_id in sorted(ids):
            # One atomic increment each: concurrent blocks each count, and the connector is not current while opened > closed.
            if await self.graph_provider._provider.increment_node_field(connector_id, CollectionNames.APPS.value, OPENED_KEY) is None:
                raise ScopeGenerationError(f"could not open the unclassified write on connector {connector_id}: no such connector")
            self.opened.add(connector_id)
        self._org_marked_for_unclassified = True

    async def _connector_of_node(self, collection: str, node_id: str) -> str | None:
        return (await self._connectors_of([(collection, node_id)])).get((collection, node_id))

    async def _connectors_of(self, targets: list[tuple[str, str | None]]) -> dict[tuple[str, str], str]:
        """The connector of each node (collection, id) that has one: one query per collection for the nodes not read yet in this
        transaction. A failed read fails the block (it must not read as "no connector")."""
        want: dict[str, list[str]] = {}
        for collection, node_id in targets:
            if node_id and (collection, node_id) not in self._connector_of:
                want.setdefault(collection, []).append(node_id)
        for collection, ids in want.items():
            found = await self.graph_provider.get_nodes_by_field_in(
                collection, "_key", sorted(set(ids)), ["_key", "connectorId"], transaction=self.txn, raise_on_error=True)
            by_id = {str(d.get("_key")): d.get("connectorId") for d in found or [] if isinstance(d, dict)}
            for node_id in set(ids):
                self._connector_of[(collection, node_id)] = by_id.get(node_id)
        return {t: str(c) for t in targets if t[1] and (c := self._connector_of.get((t[0], t[1])))}

    async def _touch(self, targets: list[tuple[str, str | None]]) -> None:
        """This transaction is about to change the structure under these nodes (collection, id); remember their connectors.
        Resolved now, before a delete removes the node."""
        self.structural_connectors.update((await self._connectors_of(targets)).values())

    async def _stored(self, collection: str, ids: list[str]) -> dict[str, dict]:
        """The stored documents of these nodes (the ones that exist), one query; a failed read fails the block."""
        found = await self.graph_provider.get_nodes_by_field_in(
            collection, "_key", sorted(set(ids)), None, transaction=self.txn, raise_on_error=True) if ids else []
        stored = {str(d["_key"]): d for d in found or [] if isinstance(d, dict) and d.get("_key") is not None}
        for node_id, doc in stored.items():
            self._connector_of[(collection, node_id)] = doc.get("connectorId")
        return stored

    async def _structures_of(self, targets: list[tuple[str, str]]) -> dict[tuple[str, str], frozenset]:
        """The hierarchy, group and inherit edges that decide each node's access (never its grants), one query per collection: what an
        idempotent edge writer must have changed for the write to count as structural."""
        by_collection: dict[str, list[str]] = {}
        for collection, node_id in targets:
            by_collection.setdefault(collection, []).append(node_id)
        out: dict[tuple[str, str], frozenset] = {}
        for collection, ids in by_collection.items():
            got = await self.graph_provider.get_structure_of_nodes(collection, sorted(set(ids)), transaction=self.txn, raise_on_error=True)
            out.update({(collection, i): got.get(i, frozenset()) for i in ids})
        return out

    async def flush_scope_generation(self) -> None:
        """Bump the scope generation of every connector touched, once each, inside this transaction (called before the commit,
        so the change and the bump commit together or neither does). Raises when a bump cannot be written, which rolls the
        transaction back. The bump is a classified write of its own (it is the bookkeeping itself)."""
        from app.services.permissions.structure_generation import bump_generation_in

        token = _CLASSIFIED_SCOPE.set("flush_scope_generation")
        try:
            for connector_id in sorted(self.structural_connectors):
                await bump_generation_in(self, connector_id)
        finally:
            _CLASSIFIED_SCOPE.reset(token)
        self.structural_connectors.clear()

    def _memo_put(self, key: tuple, value: object) -> None:
        """Cache positives only. A miss usually means "not created yet", and the
        caller creates it moments later; caching that would hide it."""
        if value is not None:
            self._memo[key] = value

    def _memo_drop(self, key: tuple) -> None:
        """Deleting inside the transaction must not leave a positive behind, or
        the next lookup reports something that is gone."""
        self._memo.pop(key, None)

    def _memo_drop_external_id(self, connector_id: str, external_id: str) -> None:
        """Record lookups are cached per visibility; a change drops every one."""
        for key in [key for key in self._memo if key[:3] == ("record", connector_id, external_id)]:
            del self._memo[key]

    def _memo_forget_record(self, record_id: str) -> None:
        """Drop every cached lookup that resolves to this vertex.

        A move re-upserts the vertex under a new external id, and a delete by key
        removes it without naming its path; either way its old path must stop
        answering with it. on_records_moved retires whatever holds a destination
        path, so a stale hit there deletes a record moved earlier in the batch.
        """
        stale = [
            key for key, value in self._memo.items()
            if key[0] == "record" and getattr(value, "id", None) == record_id
        ]
        for key in stale:
            del self._memo[key]

    def _memo_forget_all_records(self) -> None:
        """For deletes whose victims are not known here by id (whole subtrees)."""
        for key in [key for key in self._memo if key[0] == "record"]:
            del self._memo[key]

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str) -> bool | None:
        return await self.graph_provider.batch_upsert_nodes(nodes, collection, transaction=self.txn)

    async def batch_update_nodes(self, nodes: list[dict], collection: str) -> bool | None:
        return await self.graph_provider.batch_update_nodes(nodes, collection, transaction=self.txn)

    async def get_record_by_path(self, connector_id: str, path: list[str], external_record_group_id: str) -> dict | None:
        return await self.graph_provider.get_record_by_path(connector_id, path, external_record_group_id, transaction=self.txn)

    async def get_records_by_file_path(self, connector_id: str, path: str) -> list[Record]:
        return await self.graph_provider.get_records_by_file_path(connector_id, path, transaction=self.txn)

    async def get_record_by_key(self, key: str, *, raise_on_error: bool = False) -> Optional[dict]:
        return await self.graph_provider.get_document(
            key, CollectionNames.RECORDS.value, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_app_by_id(self, connector_id: str) -> Optional[AppMetadata]:
        """Get app metadata by connector ID."""
        doc = await self.graph_provider.get_document(connector_id, CollectionNames.APPS.value, transaction=self.txn)
        return AppMetadata.from_db_document(doc) if doc else None

    async def get_record_by_external_id(
        self, connector_id: str, external_id: str, visibility: RecordVisibility = RecordVisibility.ALL,
        *, raise_on_error: bool = False,
    ) -> Optional[Record]:
        # Sync decides create-or-update on this answer; hiding a trashed record
        # would mint a second record for the same source item.
        #
        # Every record in a folder asks for the same parent. The first one
        # creates it, and without this the other ninety-nine each pay a round
        # trip to rediscover it.
        key = ("record", connector_id, external_id, visibility)
        if key in self._memo:
            return self._memo[key]  # type: ignore[return-value]
        value = await self.graph_provider.get_record_by_external_id(
            connector_id, external_id, transaction=self.txn, visibility=visibility,
            **({"raise_on_error": True} if raise_on_error else {}),
        )
        self._memo_put(key, value)
        return value

    async def get_record_by_external_revision_id(self, connector_id: str, external_revision_id: str) -> Optional[Record]:
        return await self.graph_provider.get_record_by_external_revision_id(connector_id, external_revision_id, transaction=self.txn)

    async def get_records_by_status(
        self,
        org_id: str,
        connector_id: str,
        status_filters: list[str],
        limit: Optional[int] = None,
        offset: int = 0,
        record_group_id: Optional[str] = None,
        is_placeholder: Optional[bool] = None,
        after_key: Optional[str] = None,
        exclude_statuses: Optional[list[str]] = None,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        """Get records by status. Returns properly typed Record instances.

        An empty list means no record matched. A listing that could not be read
        raises GraphQueryError - callers must not read that as "nothing found".
        """
        return await self.graph_provider.get_records_by_status(
            org_id,
            connector_id,
            status_filters,
            limit,
            offset,
            transaction=self.txn,
            record_group_id=record_group_id,
            is_placeholder=is_placeholder,
            after_key=after_key,
            exclude_statuses=exclude_statuses,
            visibility=visibility,
        )

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        key = ("record_group", connector_id, external_id)
        if key in self._memo:
            return self._memo[key]  # type: ignore[return-value]
        value = await self.graph_provider.get_record_group_by_external_id(connector_id, external_id, transaction=self.txn)
        self._memo_put(key, value)
        return value

    async def take_back_kept_record_group(self, group_id: str) -> bool:
        return await self.graph_provider.take_back_kept_record_group(group_id, transaction=self.txn)

    async def find_slack_burst_record_by_ts(
        self,
        connector_id: str,
        channel_id: str,
        ts: str,
    ) -> Optional[Record]:
        """Find the Slack burst MessageRecord whose startTs <= ts <= endTs."""
        return await self.graph_provider.find_slack_burst_record_by_ts(
            connector_id, channel_id, ts, transaction=self.txn
        )

    async def get_file_record_by_id(self, id: str) -> Optional[FileRecord]:
        return await self.graph_provider.get_file_record_by_id(id, transaction=self.txn)

    async def get_record_group_by_id(self, id: str) -> Optional[RecordGroup]:
        # Both providers return the raw document.
        doc = await self.graph_provider.get_record_group_by_id(id, transaction=self.txn)
        return RecordGroup.from_arango_base_record_group(doc) if doc else None

    async def create_record_groups_relation(self, child_id: str, parent_id: str) -> None:
        """
        Create BELONGS_TO edge from child record group to parent record group.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.create_record_groups_relation(child_id, parent_id, transaction=self.txn)

    async def get_user_by_email(
        self, email: str, *, raise_on_error: bool = False, org_id: str | None = None,
    ) -> Optional[User]:
        # Every record in a batch carries the same owner, so this is the same
        # lookup a hundred times over. Users are not created inside a record
        # transaction, so the answer cannot go stale within it.
        key = ("user_email", email, org_id)
        if key in self._memo:
            return self._memo[key]  # type: ignore[return-value]
        scope = {"org_id": org_id} if org_id is not None else {}
        value = await self.graph_provider.get_user_by_email(
            email, transaction=self.txn, raise_on_error=raise_on_error, **scope
        )
        self._memo_put(key, value)
        return value

    async def get_users_by_emails(
        self, emails: list[str], *, raise_on_error: bool = False, org_id: str | None = None,
    ) -> dict[str, User]:
        """The members for ``emails`` (lowercased keys), one provider call for the addresses the memo lacks."""
        wanted = list(dict.fromkeys(e.lower() for e in emails if e))
        keys = {e: ("user_email", e, org_id) for e in wanted}
        found = {e: self._memo[k] for e, k in keys.items() if k in self._memo}
        missing = [e for e in wanted if e not in found]
        if missing:
            scope = {"org_id": org_id} if org_id is not None else {}
            got = await self.graph_provider.get_users_by_emails(
                missing, transaction=self.txn, raise_on_error=raise_on_error, **scope
            )
            for e in missing:
                self._memo_put(keys[e], got.get(e))
                if got.get(e) is not None:
                    found[e] = got[e]
        return found

    async def get_user_by_source_id(
        self, source_user_id: str, connector_id: str, *, raise_on_error: bool = False
    ) -> Optional[User]:
        return await self.graph_provider.get_user_by_source_id(
            source_user_id, connector_id, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_app_user_by_email(self, email: str, connector_id: str) -> Optional[AppUser]:
        return await self.graph_provider.get_app_user_by_email(email, connector_id, transaction=self.txn)

    async def get_record_owner_source_user_email(
        self, record_id: str, *, raise_on_error: bool = False
    ) -> str | None:
        return await self.graph_provider.get_record_owner_source_user_email(
            record_id, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> Optional[User]:
        return await self.graph_provider.get_user_by_user_id(user_id, raise_on_error=raise_on_error)

    async def delete_record_by_key(self, key: str) -> None:
        self._memo_forget_record(key)
        # The node with its edges: on Neo4j that is the same single DETACH DELETE,
        # and on ArangoDB the edges no longer dangle after the record is gone.
        result = await self.graph_provider.delete_nodes_and_edges(
            [key],
            CollectionNames.RECORDS.value,
            graph_name="knowledgeGraph",
            transaction=self.txn,
        )
        # A code file's blocks hang off it by recordId, not by an edge, so removing
        # the record vertex alone strands the whole file's symbol projection. After
        # the record, so a write that fails here cannot leave the record live.
        await self.graph_provider.delete_blocks_for_records([key], transaction=self.txn)
        return result

    async def delete_record_by_external_id(
        self, connector_id: str, external_id: str, user_id: str | None = None, *, soft_delete: bool = False,
    ) -> dict | None:
        self._memo_drop_external_id(connector_id, external_id)
        return await self.graph_provider.delete_record_by_external_id(
            connector_id, external_id, user_id, transaction=self.txn, soft_delete=soft_delete
        )

    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str) -> None:
        return await self.graph_provider.remove_user_access_to_record(connector_id, external_id, user_id, transaction=self.txn)

    async def remove_user_access_to_connector(self, connector_id: str, user_id: str) -> int:
        return await self.graph_provider.remove_user_access_to_connector(connector_id, user_id, transaction=self.txn)

    async def delete_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edge(from_id, from_collection, to_id, to_collection, collection, transaction=self.txn)

    async def delete_nodes(self, keys: list[str], collection: str) -> None:
        if collection == CollectionNames.RECORDS.value:
            for key in keys:
                self._memo_forget_record(key)
        return await self.graph_provider.delete_nodes(keys, collection, transaction=self.txn)

    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edges_from(from_id, from_collection, collection, transaction=self.txn)

    async def delete_edges_to(self, to_id: str, to_collection: str, collection: str) -> int:
        return await self.graph_provider.delete_edges_to(to_id, to_collection, collection, transaction=self.txn)

    async def replace_edges_to(self, to_id: str, to_collection: str, edges: list[dict], collection: str) -> None:
        await self.graph_provider.replace_edges_to(to_id, to_collection, edges, collection, transaction=self.txn)

    async def replace_record_permissions(
        self, record_id: str, edges: list[dict], record_group_id: str | None, *, inherit: bool
    ) -> None:
        await self.graph_provider.replace_record_permissions(
            record_id, edges, record_group_id, inherit=inherit, transaction=self.txn
        )

    async def link_record_to_group(
        self,
        record_id: str,
        record_group_id: str | None,
        *,
        inherit: bool | None,
        leaving_group_id: str | None = None,
        browse_root: bool | None = None,
    ) -> int:
        return await self.graph_provider.link_record_to_group(
            record_id,
            record_group_id,
            inherit=inherit,
            leaving_group_id=leaving_group_id,
            browse_root=browse_root,
            transaction=self.txn,
        )

    async def delete_parent_child_edge_to_record(self, record_id: str) -> int:
        """Delete PARENT_CHILD edges pointing to a specific target record"""
        return await self.graph_provider.delete_parent_child_edge_to_record(record_id, transaction=self.txn)

    async def delete_edges_to_groups(self, from_id: str, from_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edges_to_groups(from_id, from_collection, collection, transaction=self.txn)

    async def get_record_group_organization(self, record_group_id: str, org_id: str) -> str | None:
        return await self.graph_provider.get_record_group_organization(record_group_id, org_id, transaction=self.txn)

    async def delete_edges_between_collections(self, from_id: str, from_collection: str, edge_collection: str, to_collection: str) -> None:
        return await self.graph_provider.delete_edges_between_collections(from_id, from_collection, edge_collection, to_collection, transaction=self.txn)

    async def delete_record_entity_relations(self, record_id: str, to_collection: str, origin: str) -> int:
        return await self.graph_provider.delete_record_entity_relations(record_id, to_collection, origin, transaction=self.txn)

    async def delete_edges_by_relationship_types(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: list[str]
    ) -> int:
        """Delete edges by relationship types from a record."""
        return await self.graph_provider.delete_edges_by_relationship_types(
            from_id, from_collection, collection, relationship_types, transaction=self.txn
        )

    async def delete_nodes_and_edges(self, keys: list[str], collection: str) -> None:
        return await self.graph_provider.delete_nodes_and_edges(keys, collection, graph_name="knowledgeGraph", transaction=self.txn)

    async def delete_records_recursive(
        self, record_ids: list[str], connector_id: str, cascade_children: bool = True,
        within_folder_id: str | None = None, *, include_trashed_roots: bool = False,
    ) -> dict:
        """Delete records within the active transaction.

        When *cascade_children* is True (default), the full PARENT_CHILD +
        ATTACHMENT subtree is deleted.  When False, only ATTACHMENT edges are
        traversed — child records linked via PARENT_CHILD survive, and each one
        that keeps a record group is listed under the result's ``reparented`` key
        for the caller to re-point.
        """
        self._memo_forget_all_records()
        return await self.graph_provider.delete_records_recursive(
            record_ids, connector_id, transaction=self.txn, cascade_children=cascade_children,
            within_folder_id=within_folder_id, include_trashed_roots=include_trashed_roots,
        )

    async def soft_delete_records(
        self,
        record_ids: list[str],
        connector_id: str,
        *,
        delete_source: str,
        batch_id: str,
        deleted_by_user_id: str | None = None,
        follow: tuple[str, ...] = ("PARENT_CHILD", "ATTACHMENT"),
        within_folder_id: str | None = None,
        include_trashed_roots: bool = False,
    ) -> dict:
        """Move records to the trash within the active transaction."""
        # A trashed record no longer answers a LIVE lookup, and the walk can reach
        # records not named here.
        self._memo_forget_all_records()
        return await self.graph_provider.soft_delete_records(
            record_ids,
            connector_id,
            delete_source=delete_source,
            batch_id=batch_id,
            deleted_by_user_id=deleted_by_user_id,
            follow=follow,
            transaction=self.txn,
            within_folder_id=within_folder_id,
            include_trashed_roots=include_trashed_roots,
        )

    async def restore_records(
        self,
        restores: list[dict],
        batch_id: str | None,
        *,
        connector_id: str | None = None,
        require_live_parent: bool = False,
    ) -> list[str]:
        """Bring records back from the trash within the active transaction."""
        return await self.graph_provider.restore_records(
            restores, batch_id, transaction=self.txn,
            connector_id=connector_id, require_live_parent=require_live_parent,
        )

    async def claim_push_record(self, **claim: object) -> object:
        """The conditional write that arbitrates concurrent pushes, within the active transaction."""
        return await self.graph_provider.claim_push_record(transaction=self.txn, **claim)

    async def retag_push_record(self, **retag: object) -> bool:
        return await self.graph_provider.retag_push_record(transaction=self.txn, **retag)

    async def delete_single_record(self, record_id: str) -> dict:
        """Single-record delete within the active transaction — no containment walk."""
        self._memo_forget_record(record_id)
        return await self.graph_provider.delete_single_record(record_id, transaction=self.txn)

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str, *, raise_on_error: bool = False) -> Optional[AppUserGroup]:
        return await self.graph_provider.get_user_group_by_external_id(
            connector_id, external_id, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def delete_user_group_by_id(self, group_id: str) -> None:
        return await self.graph_provider.delete_nodes_and_edges([group_id],CollectionNames.GROUPS.value,graph_name="knowledgeGraph",transaction=self.txn)

    async def get_app_role_by_external_id(self, connector_id: str, external_id: str, *, raise_on_error: bool = False) -> Optional[AppRole]:
        return await self.graph_provider.get_app_role_by_external_id(
            connector_id, external_id, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_users(self, org_id: str, active: bool = True) -> list[User]:
        users_dict = await self.graph_provider.get_users(org_id, active=active)
        return [User.from_arango_user(user_dict) for user_dict in users_dict if user_dict is not None]

    async def get_app_users(
        self, org_id: str, connector_id: str, *, raise_on_error: bool = False
    ) -> list[AppUser]:
        app_users_dict = await self.graph_provider.get_app_users(
            org_id, connector_id, raise_on_error=raise_on_error
        )
        # A membership the source names no user for, as an external collaborator's
        # (on_external_app_users), is no app user: AppUser cannot be built without the
        # source's id, and one such row would fail the whole list.
        return [
            AppUser.from_arango_user(user_dict) for user_dict in app_users_dict
            if user_dict is not None and user_dict.get("sourceUserId")
        ]

    async def get_user_groups(
        self, connector_id: str, org_id: str, *, raise_on_error: bool = False
    ) -> list[AppUserGroup]:
        return await self.graph_provider.get_user_groups(
            connector_id, org_id, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def batch_upsert_people(self, people: list[Person]) -> None:
        return await self.graph_provider.batch_upsert_people(people, transaction=self.txn)

    async def get_person_by_email(
        self, email: str, org_id: str, *, raise_on_error: bool = False
    ) -> Optional[Person]:
        return await self.graph_provider.get_person_by_email(
            email, org_id, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def upsert_person_by_email(self, person: Person, *, raise_on_error: bool = False) -> Optional[str]:
        return await self.graph_provider.upsert_person_by_email(
            person, transaction=self.txn, raise_on_error=raise_on_error
        )


    async def get_person_by_source_key(self, source_key: str, org_id: str) -> Person | None:
        return await self.graph_provider.get_person_by_source_key(
            source_key, org_id, transaction=self.txn, raise_on_error=True
        )

    def is_transient_error(self, error: BaseException) -> bool:
        return self.graph_provider.is_transient_error(error)

    async def upsert_person_by_source_key(self, person: Person, *, raise_on_error: bool = False) -> str | None:
        return await self.graph_provider.upsert_person_by_source_key(
            person, transaction=self.txn, raise_on_error=raise_on_error
        )
    async def ensure_app_membership(
        self,
        principal_id: str,
        principal_collection: str,
        connector_id: str,
        *,
        is_external: bool,
        source_user_id: str | None = None,
    ) -> None:
        return await self.graph_provider.ensure_app_membership(
            principal_id,
            principal_collection,
            connector_id,
            is_external=is_external,
            source_user_id=source_user_id,
            transaction=self.txn,
        )

    async def reap_stale_external_app_relations(self, connector_id: str) -> int:
        return await self.graph_provider.reap_stale_external_app_relations(
            connector_id, transaction=self.txn
        )

    async def create_user_group_hierarchy(
        self,
        child_external_id: str,
        parent_external_id: str,
        connector_id: str
    ) -> bool:
        """Create BELONGS_TO edge between child and parent user groups"""
        try:
            # Lookup both groups
            child_group = await self.get_user_group_by_external_id(connector_id, child_external_id)
            if not child_group:
                self.logger.warning(
                    f"Child user group not found: {child_external_id} (connector: {connector_id})"
                )
                return False

            parent_group = await self.get_user_group_by_external_id(connector_id, parent_external_id)
            if not parent_group:
                self.logger.warning(
                    f"Parent user group not found: {parent_external_id} (connector: {connector_id})"
                )
                return False

            # Create BELONGS_TO edge
            edge = {
                "from_id": child_group.id,
                "from_collection": CollectionNames.GROUPS.value,
                "to_id": parent_group.id,
                "to_collection": CollectionNames.GROUPS.value,
                "entityType": "GROUP",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.graph_provider.batch_create_edges(
                [edge],
                collection=CollectionNames.BELONGS_TO.value,
                transaction=self.txn
            )

            self.logger.debug(f"Created user group hierarchy: {child_group.name} -> {parent_group.name}")
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to create user group hierarchy ({child_external_id} -> {parent_external_id}): {str(e)}",
                exc_info=True
            )
            return False

    async def create_user_group_membership(
        self,
        user_source_id: str,
        group_external_id: str,
        connector_id: str
    ) -> bool:
        """Create BELONGS_TO and PERMISSION edges from user to group using source IDs."""
        try:
            # Lookup user by sourceUserId
            user = await self.get_user_by_source_id(user_source_id, connector_id)
            if not user:
                self.logger.warning(
                    f"User not found: {user_source_id} (connector: {connector_id})"
                )
                return False

            # Lookup group
            group = await self.get_user_group_by_external_id(connector_id, group_external_id)
            if not group:
                self.logger.warning(
                    f"User group not found: {group_external_id} (connector: {connector_id})"
                )
                return False

            # Create PERMISSION edge (access grant) — matches on_new_user_groups
            permission = Permission(
                external_id=user_source_id,
                email=user.email,
                type=PermissionType.READ,
                entity_type=EntityType.USER,
            )
            perm_edge = permission.to_arango_permission(
                from_id=user.id,
                from_collection=CollectionNames.USERS.value,
                to_id=group.id,
                to_collection=CollectionNames.GROUPS.value,
            )

            await self.graph_provider.batch_create_edges(
                [perm_edge],
                collection=CollectionNames.PERMISSION.value,
                transaction=self.txn
            )

            self.logger.debug(f"Created user group membership: {user.email} -> {group.name}")
            return True

        except Exception as e:
            if self.graph_provider.is_transient_error(e) is True:
                raise  # the transaction is aborted; its owner runs it again
            self.logger.error(
                f"Failed to create user group membership ({user_source_id} -> {group_external_id}): {str(e)}",
                exc_info=True
            )
            return False

    async def get_first_user_with_permission_to_node(self, node_id: str, node_collection: str) -> Optional[User]:
        return await self.graph_provider.get_first_user_with_permission_to_node(node_id, node_collection, transaction=self.txn)

    async def get_users_with_permission_to_node(
        self, node_id: str, node_collection: str, *, raise_on_error: bool = False
    ) -> list[User]:
        return await self.graph_provider.get_users_with_permission_to_node(
            node_id, node_collection, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_groups_with_permission_to_node(
        self, node_id: str, node_collection: str, *, raise_on_error: bool = False
    ) -> list[AppUserGroup]:
        return await self.graph_provider.get_groups_with_permission_to_node(
            node_id, node_collection, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        *,
        raise_on_error: bool = False,
    ) -> Optional[dict[str, Any]]:
        return await self.graph_provider.get_edge(
            from_id, from_collection, to_id, to_collection, collection,
            transaction=self.txn, raise_on_error=raise_on_error,
        )

    async def get_record_by_conversation_index(self, connector_id: str, conversation_index: str, thread_id: str, org_id: str, user_id: str) -> Optional[Record]:
        return await self.graph_provider.get_record_by_conversation_index(connector_id, conversation_index, thread_id, org_id, user_id, transaction=self.txn)

    async def get_record_by_issue_key(self, connector_id: str, issue_key: str) -> Optional[Record]:
        """Get record by Jira issue key (e.g., PROJ-123) by searching weburl pattern."""
        return await self.graph_provider.get_record_by_issue_key(connector_id, issue_key, transaction=self.txn)

    async def get_record_by_weburl(self, weburl: str, org_id: Optional[str] = None) -> Optional[Record]:
        """Get record by weburl (exact match)."""
        return await self.graph_provider.get_record_by_weburl(weburl, org_id, transaction=self.txn)

    async def get_org_apps(self, org_id: str) -> list[dict]:
        return await self.graph_provider.get_org_apps(org_id, active_only=False)

    async def get_records_by_weburls(self, weburls: list[str], org_id: str) -> list[dict]:
        return await self.graph_provider.get_records_by_weburls(weburls, org_id, transaction=self.txn)

    async def get_issue_sites(self, org_id: str, connector_names: list[str]) -> list[str]:
        return await self.graph_provider.get_issue_sites(org_id, connector_names, transaction=self.txn)

    async def get_records_linking_to_weburls(
        self,
        weburls: list[str],
        org_id: str,
        connector_names: list[str],
        limit: int = 100,
        after: Optional[str] = None,
    ) -> list[str]:
        return await self.graph_provider.get_records_linking_to_weburls(
            weburls, org_id, connector_names, limit=limit, after=after, transaction=self.txn
        )

    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None,
        *,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        """Get all child records for a parent record by parent_external_record_id. Optionally filter by record_type."""
        return await self.graph_provider.get_records_by_parent(
            connector_id, parent_external_record_id, record_type, transaction=self.txn, visibility=visibility
        )

    async def get_records_by_record_type(
        self,
        connector_id: str,
        record_type: str,
    ) -> list[Record]:
        """Return this connector's records of ``record_type``."""
        return await self.graph_provider.get_records_by_record_type(
            connector_id, record_type, transaction=self.txn
        )

    async def get_record_path(self, record_id: str) -> Optional[str]:
        """Get full hierarchical path for a record by traversing parent-child edges."""
        return await self.graph_provider.get_record_path(record_id, transaction=self.txn)

    async def get_record_path_segments(self, record_id: str) -> list[str]:
        """Get individual record names from root to this record."""
        return await self.graph_provider.get_record_path_segments(record_id, transaction=self.txn)

    async def get_app_creator_user(self, connector_id:str) ->Optional[User]:
        """Get the creator user for a connector/app by connectorId."""
        return await self.graph_provider.get_app_creator_user(connector_id,transaction=self.txn)

    async def batch_upsert_records(
        self, records: list[Record], *, release_trashed_external_ids: bool = False
    ) -> None:
        """
        Batch upsert records (base + specific type + IS_OF_TYPE edge).

        Delegates to graph_provider for the full record upsert logic.
        """
        result = await self.graph_provider.batch_upsert_records(
            records, transaction=self.txn, release_trashed_external_ids=release_trashed_external_ids
        )
        self._memo_forget_upserted(records)
        return result

    async def upsert_record_under_parent(self, record: Record, parent_record_id: str | None) -> None:
        await self.graph_provider.upsert_record_under_parent(record, parent_record_id, transaction=self.txn)
        self._memo_forget_upserted([record])

    def _memo_forget_upserted(self, records: list[Record]) -> None:
        # Forget, never cache, what was just written: the upsert merges into the
        # stored vertex, so the caller's object is not what a read returns (it
        # lacks virtualRecordId, which the upsert never writes), and some record
        # types are skipped by the provider altogether. The next lookup rereads.
        for record in records:
            record_id = getattr(record, "id", None)
            if record_id:
                self._memo_forget_record(record_id)
            external_id = getattr(record, "external_record_id", None)
            connector_id = getattr(record, "connector_id", None)
            if external_id and connector_id:
                # Every visibility: releasing a trashed record's external id renames it.
                self._memo_drop_external_id(connector_id, external_id)

    async def batch_upsert_record_groups(self, record_groups: list[RecordGroup]) -> None:
        """
        Batch upsert record groups.

        Delegates to graph_provider for implementation.
        """
        result = await self.graph_provider.batch_upsert_record_groups(record_groups, transaction=self.txn)
        for group in record_groups:
            external_id = getattr(group, "external_group_id", None)
            connector_id = getattr(group, "connector_id", None)
            if external_id and connector_id:
                self._memo_drop(("record_group", connector_id, external_id))
        return result

    async def batch_upsert_record_permissions(self, record_id: str, permissions: list[Permission]) -> None:
        return await self.graph_provider.batch_upsert_record_permissions(record_id, permissions, transaction=self.txn)

    async def batch_create_user_app_edges(self, edges: list[dict]) -> int:
        return await self.graph_provider.batch_create_user_app_edges(edges)

    async def batch_upsert_user_groups(self, user_groups: list[AppUserGroup]) -> None:
        """
        Batch upsert user groups.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_user_groups(user_groups, transaction=self.txn)

    async def batch_upsert_app_roles(self, app_roles: list[AppRole]) -> None:
        """
        Batch upsert app roles.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_app_roles(app_roles, transaction=self.txn)

    async def batch_upsert_app_users(self, users: list[AppUser]) -> None:
        """
        Batch upsert app users.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_app_users(users, transaction=self.txn)

    async def ensure_team_app_edge(self, connector_id: str, org_id: str) -> None:
        """
        Ensure the org's "All" team has an edge to the app in userAppRelation.
        Idempotent. Used by TEAM-scope connectors.
        """
        return await self.graph_provider.ensure_team_app_edge(connector_id, org_id, transaction=self.txn)

    async def upsert_authenticated_as(
        self, creator_key: str, source_user_key: str, connector_id: str, org_id: str
    ) -> None:
        return await self.graph_provider.upsert_authenticated_as(
            creator_key, source_user_key, connector_id, org_id, transaction=self.txn
        )

    async def remove_authenticated_as(self, connector_id: str) -> bool:
        return await self.graph_provider.remove_authenticated_as(connector_id, transaction=self.txn)

    async def batch_upsert_orgs(self, orgs: list[Org]) -> None:
        return await self.graph_provider.batch_upsert_orgs(orgs, transaction=self.txn)

    async def commit(self) -> None:
        """
        Commit the transaction.

        With HTTP provider: Makes async HTTP call (PUT /_api/transaction/{txn_id})
        With SDK provider: Wrapped in executor for backward compatibility
        """
        await self.graph_provider.commit_transaction(self.txn)

    async def rollback(self) -> None:
        """
        Rollback the transaction.

        With HTTP provider: Makes async HTTP call (DELETE /_api/transaction/{txn_id})
        With SDK provider: Wrapped in executor for backward compatibility
        """
        await self.graph_provider.rollback_transaction(self.txn)

    async def create_record_relation(
        self,
        from_record_id: str,
        to_record_id: str,
        relation_type: str
    ) -> None:
        """
        Create a relation edge between two records.

        Delegates to graph_provider for implementation.

        Args:
            from_record_id: Source record ID
            to_record_id: Target record ID
            relation_type: Type of relation (e.g., "BLOCKS", "CLONES", etc.)
        """
        return await self.graph_provider.create_record_relation(
            from_record_id, to_record_id, relation_type, transaction=self.txn
        )
    async def create_record_group_relation(self, record_id: str, record_group_id: str) -> None:
        """
        Create BELONGS_TO edge from record to record group.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.create_record_group_relation(record_id, record_group_id, transaction=self.txn)

    async def create_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        """
        Create INHERIT_PERMISSIONS edge from record to record group.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.create_inherit_permissions_relation_record_group(
            record_id, record_group_id, transaction=self.txn
        )

    async def delete_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        """
        Delete INHERIT_PERMISSIONS edge from record to record group.
        Called when a record's inherit_permissions is False, to remove a previously created edge (e.g. from placeholder).

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.delete_inherit_permissions_relation_record_group(
            record_id, record_group_id, transaction=self.txn
        )

    async def create_inherit_permissions_relation_record(self, child_record_id: str, parent_record_id: str) -> None:
        """Create INHERIT_PERMISSIONS edge from a record to its parent *record*.

        A nested record inherits from the record directly above it, not from its
        group, so a restriction part way down a tree takes effect.
        """
        record_edge = {
                    "from_id": child_record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": parent_record_id,
                    "to_collection": CollectionNames.RECORDS.value,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
        await self.graph_provider.batch_create_edges(
            [record_edge], collection=CollectionNames.INHERIT_PERMISSIONS.value, transaction=self.txn
        )

    async def delete_inherit_permissions_relation_record(self, child_record_id: str, parent_record_id: str) -> None:
        """Remove that edge when a record stops inheriting from its parent record."""
        await self.graph_provider.delete_edge(
            child_record_id,
            CollectionNames.RECORDS.value,
            parent_record_id,
            CollectionNames.RECORDS.value,
            CollectionNames.INHERIT_PERMISSIONS.value,
            transaction=self.txn,
        )

    async def get_sync_point(self, sync_point_key: str, *, raise_on_error: bool = False) -> Optional[dict]:
        return await self.graph_provider.get_sync_point(
            sync_point_key,
            CollectionNames.SYNC_POINTS.value,
            transaction=self.txn,
            raise_on_error=raise_on_error,
        )

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False) -> list[Org]:
        return await self.graph_provider.get_all_orgs(
            active=active,
            is_external=is_external,
            transaction=self.txn,
        )

    async def create_user_groups(self, user_groups: list[AppUserGroup]) -> None:
        return await self.graph_provider.batch_upsert_nodes([user_group.to_arango_base_user_group() for user_group in user_groups],
                    collection=CollectionNames.GROUPS.value, transaction=self.txn)

    async def create_users(self, users: list[AppUser]) -> None:
        return await self.graph_provider.batch_upsert_nodes([user.to_arango_base_user() for user in users],
                    collection=CollectionNames.USERS.value, transaction=self.txn)

    async def create_orgs(self, orgs: list[Org]) -> None:
        return await self.graph_provider.batch_upsert_nodes([org.to_arango_base_org() for org in orgs],
                    collection=CollectionNames.ORGS.value, transaction=self.txn)

    async def create_sync_point(self, sync_point_key: str, sync_point_data: dict) -> None:
        return await self.graph_provider.upsert_sync_point(sync_point_key, sync_point_data, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def delete_sync_point(self, sync_point_key: str) -> None:
        return await self.graph_provider.remove_sync_point(sync_point_key,
                    collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def read_sync_point(self, sync_point_key: str, *, raise_on_error: bool = False) -> Optional[dict]:
        return await self.graph_provider.get_sync_point(
            sync_point_key,
            collection=CollectionNames.SYNC_POINTS.value,
            transaction=self.txn,
            raise_on_error=raise_on_error,
        )

    async def update_sync_point(self, sync_point_key: str, sync_point_data: dict) -> None:
        return await self.graph_provider.upsert_sync_point(sync_point_key, sync_point_data, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def batch_upsert_record_group_permissions(
        self, record_group_id: str, permissions: list[Permission], connector_id: str
    ) -> None:
        """
        Batch upsert permissions for a record group.

        Creates permission edges from users/groups to the record group.
        Looks up users by email and groups by external_id, then creates
        the appropriate permission edges.

        Args:
            record_group_id: Internal ID (_key) of the record group
            permissions: List of Permission objects
            connector_id: Connector ID for scoped group lookups
        """
        if not permissions:
            return

        permission_edges = []
        to_id = record_group_id
        to_collection = CollectionNames.RECORD_GROUPS.value

        for permission in permissions:
            from_id = None
            from_collection = None

            if permission.entity_type.value == "USER":
                # Lookup user by email
                user = None
                if permission.email:
                    user = await self.get_user_by_email(permission.email)

                if user:
                    from_id = user.id
                    from_collection = CollectionNames.USERS.value
                else:
                    self.logger.warning(
                        f"User not found for email: {permission.email}"
                    )
                    continue

            elif permission.entity_type.value == "GROUP":
                # Lookup group by external_id using the provided connector_name
                user_group = None
                if permission.external_id:
                    user_group = await self.get_user_group_by_external_id(
                        connector_id, permission.external_id
                    )

                if user_group:
                    from_id = user_group.id
                    from_collection = CollectionNames.GROUPS.value
                else:
                    self.logger.warning(
                        f"Group not found for external_id: {permission.external_id}"
                    )
                    continue

            if from_id and from_collection:
                permission_edges.append(
                    permission.to_arango_permission(from_id, from_collection, to_id, to_collection)
                )

        if permission_edges:
            await self.graph_provider.batch_create_edges(
                permission_edges,
                collection=CollectionNames.PERMISSION.value,
                transaction=self.txn
            )

    async def batch_create_edges(self, edges: list[dict[str, Any]], collection: str) -> bool:
        return bool(await self.graph_provider.batch_create_edges(edges, collection=collection, transaction=self.txn))

    async def create_edges_if_absent(self, edges: list[dict], collection: str) -> None:
        await self.graph_provider.create_edges_if_absent(edges, collection, transaction=self.txn)

    async def batch_delete_edges(self, edges: list[dict], collection: str) -> int:
        return await self.graph_provider.batch_delete_edges(edges, collection=collection, transaction=self.txn)

    async def batch_upsert_node_relations(self, edges: list[dict]) -> None:
        """Batch upsert record relation edges with relationshipType in UPSERT match condition."""
        return await self.graph_provider.batch_upsert_node_relations(edges, transaction=self.txn)

    async def batch_create_entity_relations(self, edges: list[dict]) -> None:
        """Batch create entity relation edges with edgeType in UPSERT match condition."""
        return await self.graph_provider.batch_create_entity_relations(edges, transaction=self.txn)

    async def get_edges_to_node(
        self, node_id: str, edge_collection: str, *, raise_on_error: bool = False
    ) -> list[dict]:
        """Get all edges pointing to a specific node"""
        return await self.graph_provider.get_edges_to_node(
            node_id, edge_collection, transaction=self.txn, raise_on_error=raise_on_error
        )

    async def get_edges_from_node(self, from_node_id: str, edge_collection: str) -> list[dict]:
        """Get all edges originating from a specific node"""
        return await self.graph_provider.get_edges_from_node(from_node_id, edge_collection, transaction=self.txn)

    async def get_edges_from_node_with_target_name(
        self, from_node_id: str, edge_collection: str, *, raise_on_error: bool = False
    ) -> list[dict]:
        """Get all edges originating from a specific node with a specific target name"""
        return await self.graph_provider.get_edges_from_node_with_target_name(
            from_node_id, edge_collection, transaction=self.txn, raise_on_error=raise_on_error
        )
    
    async def get_related_node_field(
        self, node_id: str, edge_collection: str, target_collection: str,
        field: str, direction: str = "outbound"
    ) -> list:
        """Get specific field values from related nodes"""
        return await self.graph_provider.get_related_node_field(
            node_id, edge_collection, target_collection, field, direction, transaction=self.txn
        )

    async def delete_records_and_relations(self, record_key: str, hard_delete: bool = False) -> None:
        """Delete a record and all its relations"""
        self._memo_forget_record(record_key)
        return await self.graph_provider.delete_records_and_relations(record_key, hard_delete=hard_delete, transaction=self.txn)

    async def filter_linked_record_ids(self, record_ids: list[str]) -> list[str]:
        """The ids among ``record_ids`` still in the hierarchy (see the graph provider)."""
        return await self.graph_provider.filter_linked_record_ids(record_ids, transaction=self.txn)

    async def get_nodes_by_field_in(
        self, collection: str, field: str, values: list, return_fields: list[str] = None
    ) -> list[dict]:
        """Get nodes where field value is in list"""
        return await self.graph_provider.get_nodes_by_field_in(
            collection, field, values, return_fields, transaction=self.txn
        )

    async def remove_nodes_by_field(self, collection: str, field: str, value) -> int:
        """Remove nodes matching field value"""
        return await self.graph_provider.remove_nodes_by_field(
            collection, field, field_value=value, transaction=self.txn
        )

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: dict,
        return_fields: Optional[list[str]] = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Get nodes from a collection matching multiple field filters."""
        return await self.graph_provider.get_nodes_by_filters(
            collection=collection,
            filters=filters,
            return_fields=return_fields,
            transaction=self.txn,
            raise_on_error=raise_on_error,
        )

    async def find_taxonomy_nodes(
        self, collection: str, org_id: str, normalized_names: list[str]
    ) -> list[dict]:
        return await self.graph_provider.find_taxonomy_nodes(
            collection, org_id, normalized_names, transaction=self.txn
        )

    async def create_taxonomy_node_if_absent(self, collection: str, node: dict) -> None:
        await self.graph_provider.create_taxonomy_node_if_absent(
            collection, node, transaction=self.txn
        )

    async def add_taxonomy_aliases(
        self,
        collection: str,
        key: str,
        aliases: list[str],
        normalized_aliases: list[str],
        *,
        org_id: str,
        max_aliases: int = MAX_TAXONOMY_ALIASES,
    ) -> None:
        await self.graph_provider.add_taxonomy_aliases(
            collection, key, aliases, normalized_aliases,
            org_id=org_id, max_aliases=max_aliases, transaction=self.txn,
        )


class GraphDataStore(DataStoreProvider):
    """
    Graph database data store using IGraphDBProvider.

    """

    def __init__(self, logger: Logger, graph_provider: IGraphDBProvider) -> None:
        self.logger = logger
        self.graph_provider = graph_provider

    def is_transient_error(self, error: BaseException, *, explicit: bool | None = None) -> bool:
        if isinstance(error, ScopeGenerationConflict):
            # a conflict on the counter rolled the block back only when it ran in a real transaction; outside one, the block's statements
            # already committed and running it again would repeat them
            return self.graph_provider.opens_explicit_transaction(explicit) is True
        return self.graph_provider.is_transient_error(error, explicit=explicit)

    async def compare_and_set_indexing_status(
        self, record_ids: list[str], expected: str, new_status: str
    ) -> list[str]:
        # No transaction: this races the indexing service, so it has to be a single
        # atomic statement whose effect is visible right away.
        return await self.graph_provider.compare_and_set_indexing_status(
            record_ids, expected, new_status
        )

    async def get_existing_record_keys(self, record_ids: list[str]) -> set[str]:
        return await self.graph_provider.get_existing_record_keys(record_ids)

    @asynccontextmanager
    async def transaction(self, explicit: bool | None = None) -> AsyncContextManager["TransactionStore"]:
        """
        Create a graph database transaction store context manager.

        ``explicit`` is handed to the provider only when a caller sets it.

        With HTTP provider (ArangoHTTPProvider):
        - begin_transaction() returns transaction ID (string) - fully async
        - All operations pass txn_id in HTTP headers
        - commit/rollback are fully async HTTP calls

        Neo4j blocks are all-or-nothing unless NEO4J_EXPLICIT_TRANSACTIONS is false,
        where each statement commits on its own; explicit=True still asks for a
        real transaction then. It holds its locks until commit, so keep such a block to a few
        writes, with no slow calls inside.
        """
        options = {} if explicit is None else {"explicit": explicit}
        txn = await self.graph_provider.begin_transaction(
            read=read_collections,
            write=write_collections,
            **options,
        )
        self.logger.debug(f"✅ Transaction started with ID: {txn}")

        tx_store = GraphTransactionStore(
            self.graph_provider, txn, explicit=self.graph_provider.opens_explicit_transaction(explicit) is True,
        )

        try:
            yield tx_store
            await self._flush_scope_generation(tx_store)
        except BaseException as e:
            # BaseException, not Exception: asyncio.CancelledError is a
            # BaseException, so `except Exception` let a cancellation -- the
            # record-processing timeout, or a lost-lease guard -- pass straight
            # through with neither rollback nor commit. The session stayed in
            # the Neo4j client's `_active_sessions` holding a pooled connection,
            # and nothing sweeps that map until process shutdown. Under load
            # each such cancel leaked one of the pool's 100 connections until
            # every query waited out the 60s acquisition timeout -- which
            # produced more record timeouts, more cancels, more leaks.
            if isinstance(e, Exception) and self.graph_provider.is_write_conflict(e):
                self.logger.warning("Transaction hit a write conflict, rolling back: %s", str(e)[:200])
            elif isinstance(e, Exception):
                self.logger.error(f"❌ Transaction error, rolling back: {str(e)}")
            else:
                self.logger.warning("Transaction interrupted (%s); rolling back", type(e).__name__)
            await self._mark_unsafe_after_failed_block(tx_store)
            try:
                await tx_store.rollback()
            except Exception as rb_err:
                # Never mask the original failure; the client's abort already
                # tolerates a dead session.
                self.logger.warning("Rollback after transaction failure raised: %s", rb_err)
            raise
        else:
            try:
                await tx_store.commit()
            except BaseException as e:
                # A failed ArangoDB commit leaves the stream transaction open, holding
                # its locks until the server's idle timeout, so abort it. Neo4j has
                # already closed the session and answers the abort with ValueError.
                self.logger.warning("Transaction commit failed (%s); aborting it", type(e).__name__)
                # The store may still have applied it: callers that created something outside the
                # transaction (a pushed record's storage document) must not undo it on this error.
                with suppress(Exception):
                    e.commit_outcome_unknown = True  # type: ignore[attr-defined]
                try:
                    await tx_store.rollback()
                except ValueError:
                    pass
                except Exception as rb_err:
                    self.logger.warning("Abort after a failed commit raised: %s", rb_err)
                raise

    async def _mark_unsafe(self, tx_store: "GraphTransactionStore", connector_ids: Collection[str]) -> None:
        """Mark connectors scope-unsafe (their stamps are not current until a pass over the whole connector clears it). In an explicit
        transaction the mark is written with it (it commits only if the block does, and cannot wait on a lock the block itself holds);
        without one the statements of the block already committed one by one, so the mark is written on its own and survives the failure."""
        transaction = tx_store.txn if tx_store.explicit else None
        for connector_id in sorted(connector_ids):
            # Retried; if it never lands the connector stays opened (not current) and is not closed.
            for attempt in range(_MARK_ATTEMPTS):
                try:
                    # False: no such app document, so no mark was written and the opening must not be counted as closed
                    if await self.graph_provider.update_node(connector_id, CollectionNames.APPS.value, {UNSAFE_KEY: new_unsafe_mark()}, transaction) is not False:
                        tx_store.marked.add(connector_id)
                    break
                except Exception:
                    if attempt + 1 == _MARK_ATTEMPTS:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))

    async def _mark_unsafe_after_unclassified_write(self, tx_store: "GraphTransactionStore") -> None:
        """A write nobody classified reached the provider, and which connector it touched is not known (the proxy sees a method
        name, not a target): every connector of the organisation this block runs for may have changed structurally, so none of their
        stamps is current. The connectors the transaction saw are marked first, then the organisation's, also when the block never
        resolved a connector. The organisation comes from the ambient context, resolved here and not inside any ``try``: with none the
        block fails (``MissingOrgContext``), because it cannot say what it changed and must not commit as if it were harmless."""
        seen = {c for c in tx_store._connector_of.values() if c} | tx_store.structural_connectors
        await self._mark_unsafe(tx_store, seen)
        org_id = org_context.current().org_id
        apps = await self.graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []
        await self._mark_unsafe(tx_store, {str(k) for a in apps if (k := a.get("_key") or a.get("id"))} - seen)
        await self._close_opened(tx_store)

    async def _close_opened(self, tx_store: "GraphTransactionStore") -> None:
        """Count the connectors this block opened as closed, those whose unsafe mark has been written. Tried once: an exception proves nothing (a timeout
        can arrive after the provider applied the increment), and a second increment of an applied one would count a write that is still running as
        closed. A closing step with an unknown or negative outcome (logged) leaves the connector opened, which is not current: the fail-closed side."""
        for connector_id in sorted((tx_store.opened & tx_store.marked) - tx_store.closed):
            try:
                if await self.graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, CLOSED_KEY) is not None:
                    tx_store.closed.add(connector_id)
                    continue
            except Exception:
                pass
            self.logger.error("could not close the unclassified write on connector %s: it stays not current until the next pass is run "
                              "with --close-open-mark", connector_id)

    async def _mark_unsafe_after_failed_block(self, tx_store: "GraphTransactionStore") -> None:
        """A block that failed without an explicit transaction left its earlier statements committed: the connectors a structural (or an
        unclassified) write touched changed with no bump, so their stamps are marked unsafe. In an explicit transaction the rollback
        undoes it all. Best effort: a mark that cannot be written is logged at error and never masks the failure."""
        if not tx_store.tracked or tx_store.explicit:
            return
        connectors = set(tx_store.structural_connectors)
        if tx_store.unclassified_write:
            try:  # the whole organisation, as when the block completes
                await self._mark_unsafe_after_unclassified_write(tx_store)
            except Exception:
                self.logger.error("could not mark the connectors of the organisation scope-unsafe after a failed block")
            connectors |= {c for c in tx_store._connector_of.values() if c}
        for connector_id in sorted(connectors):
            try:
                await self._mark_unsafe(tx_store, [connector_id])
            except BaseException:
                self.logger.error("could not mark connector %s scope-unsafe after a failed block", connector_id)
        try:
            await self._close_opened(tx_store)
        except BaseException:
            self.logger.error("could not close the unclassified writes of a failed block")

    async def _flush_scope_generation(self, tx_store: "GraphTransactionStore") -> None:
        if not tx_store.tracked:
            return
        if tx_store.unclassified_write:
            await self._mark_unsafe_after_unclassified_write(tx_store)
        touched = sorted(tx_store.structural_connectors)
        if not touched:
            return
        try:
            await tx_store.flush_scope_generation()
        except Exception:
            if not tx_store.explicit:
                # The statements of this block already committed one by one, so rolling back undoes nothing: mark the
                # connectors unsafe (their stamps are not current) before failing the block.
                for connector_id in touched:
                    try:
                        await self._mark_unsafe(tx_store, [connector_id])
                    except Exception:
                        self.logger.error("could not mark connector %s scope-unsafe", connector_id)
            raise

    async def execute_in_transaction(self, func, *args, explicit: bool | None = None, **kwargs) -> None:
        """Execute function within graph database transaction.

        Re-run on a transient transaction failure (a deadlock between two
        writers on the same nodes, a lock timeout) -- but only when the
        provider says the failed attempt rolled back cleanly, which is what
        ``is_transient_error`` answers; the default is never to retry.
        ``explicit`` is passed to :meth:`transaction`.
        """
        attempts = 0
        while True:
            attempts += 1
            try:
                async with self.transaction(explicit=explicit) as tx_store:
                    return await func(tx_store, *args, **kwargs)
            except Exception as e:
                if attempts >= _RETRY_ATTEMPTS or not self.is_transient_error(e, explicit=explicit):
                    raise
                delay = _retry_delay(attempts - 1)
                self.logger.warning(
                    "Transient graph transaction failure (attempt %d/%d), retrying in %.1fs: %s",
                    attempts, _RETRY_ATTEMPTS, delay, e,
                )
                await asyncio.sleep(delay)

    async def execute_idempotent_in_transaction(
        self,
        func: Callable[..., Awaitable[_T]],
        *args: object,
        explicit: bool | None = None,
        **kwargs: object,
    ) -> _T:
        """Run ``func(tx_store, ...)`` in a transaction, re-running it when it
        collides with a concurrent writer (``is_write_conflict``).

        Unlike :meth:`execute_in_transaction` this retries even where the
        failed attempt may have partly landed (Neo4j auto-commit), so
        ``func`` must be safe to run again from the start: read what is
        there, then write only the difference. ``explicit`` is passed to
        :meth:`transaction`.
        """
        attempts = 0
        while True:
            attempts += 1
            try:
                async with self.transaction(explicit=explicit) as tx_store:
                    return await func(tx_store, *args, **kwargs)
            except Exception as e:
                if attempts >= _RETRY_ATTEMPTS or not (self.graph_provider.is_write_conflict(e) or isinstance(e, ScopeGenerationConflict)):
                    raise
                delay = _retry_delay(attempts - 1)
                self.logger.warning(
                    "Graph write conflict (attempt %d/%d), retrying in %.1fs: %s",
                    attempts, _RETRY_ATTEMPTS, delay, str(e)[:200],
                )
                await asyncio.sleep(delay)


# ---- the one place every structural write passes through -------------------------------------------------------------
# A write that changes what a vector point's scope fields are made of (a hierarchy, group or inherit edge, a record's or group's
# access rule or its place) is listed here with the nodes it touches. The wrapper below records their connectors before the call
# (so a delete is resolved first), and `GraphDataStore.transaction` bumps each connector's scope generation once, inside the same
# transaction. A new writer on `GraphTransactionStore` that is not listed here is caught by `test_structural_writers.py`.
_R, _G = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
_STRUCTURAL_EDGES = frozenset({
    CollectionNames.NODE_RELATIONS.value, CollectionNames.BELONGS_TO.value, CollectionNames.INHERIT_PERMISSIONS.value,
})
# A grant (a PERMISSION edge into a record or group) is watched too, but only for whether the node has any: the projector reads `granted`,
# so the first grant added to or the last removed from a node changes what its descendants are stamped with. Further grants change nothing,
# and a created grant edge is not counted (`_STRUCTURAL_EDGES` is what the providers report), so a sync of grants moves nothing by itself.
_GRANTS = CollectionNames.PERMISSION.value
_WATCHED_EDGES = _STRUCTURAL_EDGES | {_GRANTS}
_CHILD_SIDE = {  # the node whose access the edge changes
    CollectionNames.NODE_RELATIONS.value: "to", CollectionNames.BELONGS_TO.value: "from",
    CollectionNames.INHERIT_PERMISSIONS.value: "from", _GRANTS: "to",
}


def _edge_targets(edges: list[dict], collection: str) -> list[tuple[str, str | None]]:
    if collection not in _WATCHED_EDGES:
        return []
    side = _CHILD_SIDE[collection]
    out: list[tuple[str, str | None]] = []
    for e in edges:
        if collection == CollectionNames.NODE_RELATIONS.value and e.get("relationshipType") not in (None, "PARENT_CHILD", "ATTACHMENT"):
            continue
        node = e.get(f"{side}_id") or (str(e.get(f"_{side}", "")).split("/", 1)[-1] or None)
        coll = e.get(f"{side}_collection") or (str(e.get(f"_{side}", "")).split("/", 1)[0] or _R)
        if not node:
            raise ValueError(f"an edge written to {collection} names no node at its {side} end: {sorted(e)}")
        if collection == _GRANTS and coll not in (_R, _G):
            continue  # a grant on anything but a record or a record group (an agent, a team, a conversation) reaches no stamp
        out.append((coll, node))
    return out


_STRUCTURAL_WRITERS: dict[str, Callable[[dict], list[tuple[str, str | None]]]] = {
    "create_record_groups_relation": lambda b: [(_G, b["child_id"])],
    "delete_record_by_key": lambda b: [(_R, b["key"])],
    "delete_edge": lambda b: _edge_targets([{"from_id": b["from_id"], "from_collection": b["from_collection"], "to_id": b["to_id"],
                                              "to_collection": b["to_collection"]}], b["collection"]),
    "delete_nodes": lambda b: [(b["collection"], k) for k in b["keys"]] if b["collection"] in (_R, _G) else [],
    "delete_edges_from": lambda b: [(b["from_collection"], b["from_id"])] if b["collection"] in _WATCHED_EDGES else [],
    "delete_edges_to": lambda b: [(b["to_collection"], b["to_id"])] if b["collection"] in _WATCHED_EDGES else [],
    "replace_edges_to": lambda b: [(b["to_collection"], b["to_id"])] if b["collection"] in _WATCHED_EDGES else [],
    "replace_record_permissions": lambda b: [(_R, b["record_id"])],
    "link_record_to_group": lambda b: [(_R, b["record_id"])],
    "delete_parent_child_edge_to_record": lambda b: [(_R, b["record_id"])],
    "delete_edges_to_groups": lambda b: [(b["from_collection"], b["from_id"])],
    "delete_edges_between_collections": lambda b: [(b["from_collection"], b["from_id"])] if b["edge_collection"] in _WATCHED_EDGES else [],
    "delete_edges_by_relationship_types": lambda b: [(b["from_collection"], b["from_id"])] if b["collection"] in _WATCHED_EDGES else [],
    "delete_nodes_and_edges": lambda b: [(b["collection"], k) for k in b["keys"]] if b["collection"] in (_R, _G) else [],
    "delete_single_record": lambda b: [(_R, b["record_id"])],
    "upsert_record_under_parent": lambda b: [(_R, getattr(b["record"], "id", None))],
    "create_record_relation": lambda b: [(_R, b["to_record_id"])] if b["relation_type"] in ("PARENT_CHILD", "ATTACHMENT") else [],
    "create_record_group_relation": lambda b: [(_R, b["record_id"])],
    "create_inherit_permissions_relation_record_group": lambda b: [(_R, b["record_id"])],
    "delete_inherit_permissions_relation_record_group": lambda b: [(_R, b["record_id"])],
    "create_inherit_permissions_relation_record": lambda b: [(_R, b["child_record_id"])],
    "delete_inherit_permissions_relation_record": lambda b: [(_R, b["child_record_id"])],
    "batch_create_edges": lambda b: _edge_targets(b["edges"], b["collection"]),
    "create_edges_if_absent": lambda b: _edge_targets(b["edges"], b["collection"]),
    "batch_delete_edges": lambda b: _edge_targets(b["edges"], b["collection"]),
    "batch_upsert_node_relations": lambda b: _edge_targets(b["edges"], CollectionNames.NODE_RELATIONS.value),
    "delete_records_and_relations": lambda b: [(_R, b["record_key"])],
    # grants: the first one added to a node, or the last removed from it, changes what its descendants are stamped with
    "batch_upsert_record_permissions": lambda b: [(_R, b["record_id"])],
    "batch_upsert_record_group_permissions": lambda b: [(_G, b["record_group_id"])],
    # removes the group with every grant it holds (PERMISSION edges), any of which may be the last on its node
    "delete_user_group_by_id": lambda b: [(CollectionNames.GROUPS.value, b["group_id"])],
}


# Writers that remove the node itself: structural whenever the node existed (the connector is resolved before the call).
_REMOVES_NODE = frozenset({"delete_record_by_key", "delete_nodes", "delete_nodes_and_edges", "delete_single_record",
                           "delete_records_and_relations", "delete_user_group_by_id"})


# Edge writers that name only one end of the edges they remove: (which end, the argument holding the edge collection). When the named end
# is not the one whose access the edge changes (`_CHILD_SIDE`) - the parent in nodeRelations, the group in belongsTo, the parent in
# inheritPermissions - the structure that changes is the other ends' (the children's), which the named node's own structure does not show.
_NAMED_END = {
    "delete_edges_from": ("from", "collection"), "delete_edges_by_relationship_types": ("from", "collection"),
    "delete_edges_between_collections": ("from", "edge_collection"),
    "delete_edges_to": ("to", "collection"), "replace_edges_to": ("to", "collection"),
}
_HIERARCHY_TYPES = ("PARENT_CHILD", "ATTACHMENT")


async def _note_removed_at_parent_end(self: GraphTransactionStore, name: str, arguments: dict, nodes: list[tuple[str, str]]) -> None:
    """Before an edge writer removes the edges at the parent end of a node: if any structural edge is there, the connectors of the named
    node and of every node at the other ends are structural. Read first, one query for the edges and one per collection for the
    other ends' connectors."""
    end, collection_arg = _NAMED_END[name]
    edge_collection = arguments[collection_arg]
    (named_collection, named_id), = nodes
    me = f"{named_collection}/{named_id}"
    read = self.graph_provider.get_edges_from_node if end == "from" else self.graph_provider.get_edges_to_node
    edges = await read(me, edge_collection, transaction=self.txn, raise_on_error=True) or []
    types = arguments.get("relationship_types")
    other_key = "_to" if end == "from" else "_from"
    other_collection = arguments.get("to_collection") if name == "delete_edges_between_collections" else None
    ends: list[tuple[str, str | None]] = []
    for e in edges:
        kind = e.get("relationshipType")
        if edge_collection == CollectionNames.NODE_RELATIONS.value and kind not in _HIERARCHY_TYPES:
            continue
        if types is not None and kind not in types:
            continue
        endpoint = "to" if end == "from" else "from"
        key, coll = e.get(f"{endpoint}_id"), e.get(f"{endpoint}_collection")  # the generic shape (Neo4j's edges-to-node)
        if not key:
            coll, _, key = str(e.get(other_key, "")).partition("/")  # the document shape
        if other_collection is not None and coll != other_collection:
            continue
        ends.append((coll or _R, key or None))
    if ends:
        await self._touch([(named_collection, named_id), *ends])


def _wrap_structural(name: str, targets: Callable[[dict], list[tuple[str, str | None]]]) -> None:
    """Edge writers are idempotent (they re-create what is there), so a call counts only when the node's structure differs
    after it; a writer that removes the node counts whenever the node existed. A writer that removes the edges at the parent end of
    a node is read first (`_note_removed_at_parent_end`)."""
    original = getattr(GraphTransactionStore, name)
    signature = inspect.signature(original)

    @functools.wraps(original)
    async def wrapper(self: GraphTransactionStore, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not self.tracked:
            return await original(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        nodes = [(c, n) for c, n in targets(arguments) if n]
        if name in _REMOVES_NODE:
            await self._touch(nodes)
            return await original(self, *args, **kwargs)
        if name in _NAMED_END:
            end, collection_arg = _NAMED_END[name]
            edge_collection = arguments[collection_arg]
            if edge_collection in _WATCHED_EDGES and end != _CHILD_SIDE[edge_collection]:
                if nodes:
                    await _note_removed_at_parent_end(self, name, arguments, nodes)
                # replace_edges_to also writes edges: the nodes at their child ends are compared below
                nodes = _edge_targets(arguments.get("edges") or [], edge_collection)
                nodes = [(c, n) for c, n in nodes if n]
        # a node that does not exist yet has no stamp that could go stale: only existing nodes count
        candidates = [t for t in nodes if t not in self.new_nodes]
        existing = await self._connectors_of(candidates)
        before = await self._structures_of(list(existing))
        # The providers report the edges their statements really created. An edge another transaction removed after the read above
        # and this write put back looks unchanged to the comparison, but it was created.
        with edge_effects.track(_STRUCTURAL_EDGES) as created:
            result = await original(self, *args, **kwargs)
        after = await self._structures_of(list(existing))
        for t, connector in existing.items():
            if t[1] in created or after[t] != before[t]:
                self.structural_connectors.add(connector)
        return result

    setattr(GraphTransactionStore, name, wrapper)


for _name, _targets in _STRUCTURAL_WRITERS.items():
    _wrap_structural(_name, _targets)


def _wrap_connector_writer(name: str, connector_of: Callable[[dict], str | None]) -> None:
    original = getattr(GraphTransactionStore, name)
    signature = inspect.signature(original)

    @functools.wraps(original)
    async def wrapper(self: GraphTransactionStore, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not self.tracked:
            return await original(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        connector_id = connector_of(dict(bound.arguments))
        if connector_id:
            self.structural_connectors.add(str(connector_id))
        return await original(self, *args, **kwargs)

    setattr(GraphTransactionStore, name, wrapper)


# writers that name their connector
_CONNECTOR_WRITERS: dict[str, Callable[[dict], str | None]] = {
    "delete_records_recursive": lambda b: b["connector_id"],
    "soft_delete_records": lambda b: b["connector_id"],
    "restore_records": lambda b: b.get("connector_id"),
    "delete_record_by_external_id": lambda b: b["connector_id"],
    # a revoked grant may be the last one on its record, and the record is named by its external id: the connector moves on every call
    "remove_user_access_to_record": lambda b: b["connector_id"],
    # the same for every grant the user holds on the connector: any of them may be the last on its record or group
    "remove_user_access_to_connector": lambda b: b["connector_id"],
}
for _name, _of in _CONNECTOR_WRITERS.items():
    _wrap_connector_writer(_name, _of)


# stored key -> what a document without it means
# Every node field the projector reads (`ScopeProjector` / `StampNode`): the rule, the model, deleted, placeholder; the place comes from edges and
# from the parent id a connector writes.
_RULE_FIELDS = {"accessRule": "OPEN", "permissionModel": None, "isDeleted": False, "isPlaceholder": False, "externalParentId": None}


def _norm(key: str, value: object) -> str:
    value = getattr(value, "value", value)
    return str(_RULE_FIELDS[key] if value is None else value)


def _differs(stored: dict, key: str, new: object) -> bool:
    """Old and new after defaulting both: a field missing from the stored node is its default, and a change *to* the default
    (a group that stops being record-group-level, a parent cleared) counts as much as a change away from it."""
    return _norm(key, stored.get(key)) != _norm(key, new)


def _wrap_upsert(name: str, collection: str, fields: dict[str, str], items_arg: str) -> None:
    """An upsert that changes the access rule (or model) of an existing node: compare with what is stored, read for the whole batch with
    one query. ``fields`` maps the stored key to the model attribute."""
    original = getattr(GraphTransactionStore, name)
    signature = inspect.signature(original)

    @functools.wraps(original)
    async def wrapper(self: GraphTransactionStore, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not self.tracked:
            return await original(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        items = [i for i in bound.arguments[items_arg] if getattr(i, "id", None) and getattr(i, "connector_id", None)]
        stored = await self._stored(collection, [i.id for i in items])
        for item in items:
            doc = stored.get(item.id)
            if doc is None:
                self.new_nodes.add((collection, item.id))
            elif str(item.connector_id) not in self.structural_connectors and any(
                    _differs(doc, key, getattr(item, attr)) for key, attr in fields.items() if hasattr(item, attr)):
                self.structural_connectors.add(str(item.connector_id))
        return await original(self, *args, **kwargs)

    setattr(GraphTransactionStore, name, wrapper)


def _wrap_patch(name: str) -> None:
    """A generic node write whose payload carries a field that decides access or place, on records or record groups: detected by
    the field, not by who calls it. The stored nodes are read with one query for the batch."""
    original = getattr(GraphTransactionStore, name)
    signature = inspect.signature(original)

    @functools.wraps(original)
    async def wrapper(self: GraphTransactionStore, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not self.tracked:
            return await original(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        collection = bound.arguments["collection"]
        if collection in (_R, _G):
            nodes = [(n, n.get("_key") or n.get("id")) for n in bound.arguments["nodes"] if any(k in n for k in _RULE_FIELDS)]
            stored = await self._stored(collection, [i for _, i in nodes if i])
            for node, node_id in nodes:
                if not node_id:
                    continue
                doc = stored.get(node_id)
                if doc is None:
                    self.new_nodes.add((collection, node_id))
                elif doc.get("connectorId") and any(_differs(doc, k, node[k]) for k in _RULE_FIELDS if k in node):
                    self.structural_connectors.add(str(doc["connectorId"]))
        return await original(self, *args, **kwargs)

    setattr(GraphTransactionStore, name, wrapper)


# stored key -> the model attribute that writes it: every field of `_RULE_FIELDS`, compared when the model has the attribute
_MODEL_ATTRIBUTES = {"accessRule": "access_rule", "permissionModel": "permission_model", "isDeleted": "is_deleted",
                     "isPlaceholder": "is_placeholder", "externalParentId": "parent_external_record_id"}
assert set(_MODEL_ATTRIBUTES) == set(_RULE_FIELDS), "a field the projector reads must be compared by the upserts too"
_wrap_upsert("batch_upsert_records", _R, _MODEL_ATTRIBUTES, "records")
_wrap_upsert("batch_upsert_record_groups", _G, _MODEL_ATTRIBUTES, "record_groups")
_wrap_patch("batch_update_nodes")
_wrap_patch("batch_upsert_nodes")


# writes that move no structure: grants, memberships, people, sync points, content and indexing state, and the transaction itself
_HARMLESS_WRITERS = frozenset({
    "batch_upsert_nodes", "batch_update_nodes", "batch_upsert_people",
    "upsert_person_by_email", "upsert_person_by_source_key", "ensure_app_membership", "reap_stale_external_app_relations", "create_user_group_hierarchy",
    "create_user_group_membership", "batch_create_user_app_edges", "batch_upsert_user_groups",
    "batch_upsert_app_roles", "batch_upsert_app_users", "ensure_team_app_edge", "upsert_authenticated_as", "remove_authenticated_as",
    "batch_upsert_orgs", "commit", "rollback", "create_user_groups", "create_users", "create_orgs", "create_sync_point",
    "delete_sync_point", "update_sync_point", "batch_create_entity_relations",
    "remove_nodes_by_field", "create_taxonomy_node_if_absent", "add_taxonomy_aliases", "take_back_kept_record_group",
    "batch_upsert_records", "batch_upsert_record_groups", "delete_records_recursive", "soft_delete_records", "restore_records",
    "delete_record_by_external_id", "compare_and_set_indexing_status",
    # a push claim writes the document id and version of the record, never a field that decides access or place
    "claim_push_record", "retag_push_record",
    # clears a record's entityRelations edges to one entity collection: never a hierarchy, group, inherit or grant edge
    "delete_record_entity_relations",
})
CLASSIFIED_WRITERS = frozenset(_STRUCTURAL_WRITERS) | frozenset(_CONNECTOR_WRITERS) | _HARMLESS_WRITERS


def _scoped(name: str) -> None:
    original = getattr(GraphTransactionStore, name)

    @functools.wraps(original)
    async def wrapper(self: GraphTransactionStore, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not self.tracked:
            return await original(self, *args, **kwargs)
        token = _CLASSIFIED_SCOPE.set(name)
        try:
            return await original(self, *args, **kwargs)
        finally:
            _CLASSIFIED_SCOPE.reset(token)

    setattr(GraphTransactionStore, name, wrapper)


for _name in sorted(CLASSIFIED_WRITERS):
    if hasattr(GraphTransactionStore, _name):
        _scoped(_name)

"""One in-memory fake of the graph's record-access surface (``RecordAccessReader``), for every test (H5).

The access rule is held once: a user of an org reads a record of that org when the record's connector is one of the
user's apps (or the user is not limited to apps) and a grant reaches the user directly, through one of the user's
groups, or through the org, and the record is not in the trash. Every method of the surface is derived from that
rule, so the single check and the batch can never disagree, and the fake is held to the contract Neo4j and ArangoDB
are held to (``tests/support/record_access_contract.py``).

Tests seed the rule (``user``, ``record``, ``grant``) instead of mocking a method; ``serve`` puts every method of the
surface on a graph double, so a method added to the surface reaches every test that uses the fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import DEFAULT, Mock

from app.services.graph_db.common.record_visibility import is_live_record
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.interface.record_access import RECORD_ACCESS_METHODS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.graph_db.interface.graph_db_provider import RowScope

Via = Literal["USER", "GROUP", "ORG"]

_ROLE_PRIORITY = {"OWNER": 6, "ORGANIZER": 5, "FILEORGANIZER": 4, "WRITER": 3, "COMMENTER": 2, "READER": 1}
_METADATA_KEYS = ("departments", "categories", "subcategories1", "subcategories2", "subcategories3", "topics", "languages")


@dataclass
class _User:
    org_id: str
    groups: frozenset[str]
    apps: frozenset[str] | None
    #: the graph key (``User.id``) the one access check takes; the user id unless a test names another
    key: str = ""


@dataclass(frozen=True)
class _Grant:
    via: Via
    principal: str
    role: str


@dataclass
class InMemoryRecordAccess:
    users: dict[str, _User] = field(default_factory=dict[str, _User])
    records: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    grants: dict[str, list[_Grant]] = field(default_factory=dict[str, list[_Grant]])
    failure: BaseException | None = None
    #: every (user, org, record) a method was asked about, in order: what a test asserts instead of a mock's calls
    asked_about: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    #: where a record sits (its knowledge base and folder), as the details read names them
    places: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    #: the graph double ``serve`` put the surface on (its user lookup names users' graph keys)
    _served: Any = field(default=None, repr=False, compare=False)

    # --- seeding the rule -------------------------------------------------------------------------------------

    def user(
        self, user_id: str, org_id: str, *, groups: tuple[str, ...] = (), apps: tuple[str, ...] | None = None,
        key: str | None = None,
    ) -> InMemoryRecordAccess:
        """A user of ``org_id``; ``apps`` limits which connectors' records the user may read (``None``: any);
        ``key`` is the graph key the access check is asked with (the user id when omitted)."""
        self.users[user_id] = _User(
            org_id, frozenset(groups), None if apps is None else frozenset(apps), key or user_id
        )
        return self

    def record(self, record_id: str, org_id: str, **document: Any) -> InMemoryRecordAccess:  # noqa: ANN401
        """A record document of ``org_id`` (``connectorId``, ``recordName``, ``recordType``, ... as stored); a
        ``fileRecord``, ``mailRecord`` or ``ticketRecord`` given is the typed record the details read joins."""
        self.records[record_id] = {"recordName": record_id, "recordType": "FILE", **document, "orgId": org_id}
        return self

    def place(
        self, record_id: str, *, knowledge_base: dict[str, Any] | None = None, folder: dict[str, Any] | None = None,
    ) -> InMemoryRecordAccess:
        """The knowledge base and folder the record sits in, as the details read names them."""
        self.places[record_id] = {"knowledgeBase": knowledge_base, "folder": folder}
        return self

    def grant(
        self, record_id: str, *, user: str | None = None, group: str | None = None, org: str | None = None,
        role: str = "READER",
    ) -> InMemoryRecordAccess:
        """A permission on the record for exactly one of a user, a group or an org."""
        chosen = [(via, who) for via, who in (("USER", user), ("GROUP", group), ("ORG", org)) if who is not None]
        if len(chosen) != 1:
            raise ValueError("a grant names exactly one of user, group or org")
        via, who = chosen[0]
        self.grants.setdefault(record_id, []).append(_Grant(via, who, role))  # type: ignore[arg-type]
        return self

    def readable(self, user_id: str, org_id: str, *record_ids: str, **document: Any) -> InMemoryRecordAccess:  # noqa: ANN401
        """Shorthand: the user (created if new, any app) owns each record."""
        if user_id not in self.users:
            self.user(user_id, org_id)
        for record_id in record_ids:
            if record_id not in self.records:
                self.record(record_id, org_id, **document)
            self.grant(record_id, user=user_id, role="OWNER")
        return self

    def revoke(
        self, record_id: str | None = None, *, user: str | None = None, group: str | None = None, org: str | None = None
    ) -> InMemoryRecordAccess:
        """Removes the grants on ``record_id`` (every record when ``None``) to the named principal (all when none)."""
        named = {via: who for via, who in (("USER", user), ("GROUP", group), ("ORG", org)) if who is not None}
        for rid in [record_id] if record_id is not None else list(self.grants):
            self.grants[rid] = [
                grant for grant in self.grants.get(rid, []) if named and named.get(grant.via) != grant.principal
            ]
        return self

    def fail_with(self, error: BaseException | None) -> InMemoryRecordAccess:
        """Every access read raises ``error`` (a graph outage); ``None`` heals it."""
        self.failure = error
        return self

    def serve(self, graph: Any) -> Any:  # noqa: ANN401
        """Puts every method of the record-access surface on ``graph`` (a provider double); returns it. The
        user lookup the access check's callers make first is answered too, unless the test configured one."""
        for name in RECORD_ACCESS_METHODS:
            setattr(graph, name, getattr(self, name))
        self._served = graph
        for name in ("get_user_by_user_id", "get_nodes_by_field_in", "get_selection_nodes"):
            current = getattr(graph, name, None)
            if current is None or (
                isinstance(current, Mock) and current.side_effect is None and current._mock_return_value is DEFAULT
            ):
                setattr(graph, name, getattr(self, name))
        return graph

    async def get_selection_nodes(
        self, org_id: str, *, group_ids: list[str], record_ids: list[str], exact_record_ids: list[str], limit: int,
    ) -> dict[str, Any]:
        """What a selection covers, from the hierarchy alone (main #4255): a record with every record whose
        ``parentId`` chain reaches it, and an exact record alone. The fake seeds no record groups."""
        def under(root: str) -> list[str]:
            out, frontier = [root], [root]
            while frontier:
                frontier = [rid for rid, doc in self.records.items() if doc.get("parentId") in frontier]
                out.extend(frontier)
            return out

        picked = {rid for r in record_ids if r in self.records for rid in under(r)}
        picked |= {r for r in exact_record_ids if r in self.records}
        rows = [
            {"id": rid, "vrid": self.records[rid].get("virtualRecordId"), "connectorId": self.records[rid].get("connectorId")}
            for rid in sorted(picked)
            if self.records[rid].get("orgId") == org_id and is_live_record(self.records[rid])
        ]
        return {"groups": [], "records": rows[: limit + 1]}

    async def get_nodes_by_field_in(
        self, collection: str, field: str, values: list[str], *, return_fields: list[str] | None = None, **_: object
    ) -> list[dict[str, Any]]:
        """The seeded records looked up by a field (what the scope overlay reads: where a record sits)."""
        if collection != "records":
            return []
        rows = [{"id": rid, **doc} for rid, doc in self.records.items() if {"id": rid, **doc}.get(field) in set(values)]
        return [{k: row.get(k) for k in return_fields} for row in rows] if return_fields else rows

    async def get_user_by_user_id(self, user_id: str | None = None, *_: object, **__: object) -> dict[str, Any] | None:
        """Any named user resolves (an unseeded one is a user with no grants, so the rule still denies it)."""
        if not user_id:
            return None
        user = self.users.get(user_id)
        key = user.key if user is not None else user_id
        return {"_key": key, "id": key, "userId": user_id, "orgId": user.org_id if user is not None else None}

    async def _user_with_key(self, user_key: str) -> str | None:
        """The seeded user the graph key names: its own key, else the key the served graph's user lookup gives it
        (a test that configures that lookup decides what a user's key is)."""
        found = next((uid for uid, user in self.users.items() if user.key == user_key), None)
        lookup = getattr(self._served, "get_user_by_user_id", None)
        if found is not None or lookup is None or lookup == self.get_user_by_user_id:
            return found
        for uid in self.users:
            try:
                doc = await lookup(uid)
            except Exception:  # a lookup that cannot answer names nobody
                continue
            if isinstance(doc, dict) and user_key in (doc.get("_key"), doc.get("id")):
                return uid
        return None

    # --- the rule ---------------------------------------------------------------------------------------------

    def _best_grant(self, user_id: str, org_id: str, record_id: str) -> _Grant | None:
        user = self.users.get(user_id)
        record = self.records.get(record_id)
        if user is None or record is None or user.org_id != org_id or record["orgId"] != org_id:
            return None
        if not is_live_record(record):
            return None
        connector = record.get("connectorId")
        if user.apps is not None and connector is not None and connector not in user.apps:
            return None
        reaching = [
            grant
            for grant in self.grants.get(record_id, [])
            if (grant.via == "USER" and grant.principal == user_id)
            or (grant.via == "GROUP" and grant.principal in user.groups)
            or (grant.via == "ORG" and grant.principal == org_id)
        ]
        return max(reaching, key=lambda grant: _ROLE_PRIORITY.get(grant.role, 0), default=None)

    def _raise_if_failing(self) -> None:
        if self.failure is not None:
            raise self.failure

    # --- the surface (RecordAccessReader) ---------------------------------------------------------------------

    async def check_record_access_with_details(
        self, user_id: str, org_id: str, record_id: str, transaction: str | None = None
    ) -> dict[str, Any] | None:
        self.asked_about.append((user_id, org_id, record_id))
        self._raise_if_failing()
        grant = self._best_grant(user_id, org_id, record_id)
        if grant is None:
            return None
        document = dict(self.records[record_id])
        typed = {name: document.get(name) for name in ("fileRecord", "mailRecord", "ticketRecord")}
        place = self.places.get(record_id, {})
        return {
            "record": {**document, "id": record_id, **typed},
            "knowledgeBase": place.get("knowledgeBase"),
            "folder": place.get("folder"),
            "metadata": {key: [] for key in _METADATA_KEYS},
            "permissions": [
                {
                    "id": record_id,
                    "name": document.get("recordName"),
                    "type": document.get("recordType"),
                    "relationship": grant.role,
                    "accessType": grant.via,
                }
            ],
        }

    async def check_access(
        self,
        user_key: str,
        org_id: str,
        *,
        node_ids: Iterable[str] = (),
        virtual_record_ids: Iterable[str] = (),
        indexed_only: bool = False,
        connector_ids: frozenset[str] | None = None,
        scopes: Iterable[RowScope] = (),
        access: dict[str, Any] | None = None,
        transaction: str | None = None,
    ) -> AccessCheck:
        """The rule as main's ``check_access`` answers it: rows of the readable asked records and of the
        readable records carrying the asked virtual record ids, then the same citation choices."""
        asked = list(dict.fromkeys(node_ids))
        vrids = set(virtual_record_ids) if connector_ids != frozenset() else set()
        user_id = await self._user_with_key(user_key)
        self.asked_about.extend((user_id or user_key, org_id, rid) for rid in asked)
        self._raise_if_failing()
        if user_id is None or not (asked or vrids):
            return AccessCheck()
        rows = [
            {"id": rid, "connectorId": doc.get("connectorId"), "groupIds": list(doc.get("groupIds") or ()),
             "vrid": doc.get("virtualRecordId"), "indexingStatus": doc.get("indexingStatus", "COMPLETED")}
            for rid, doc in self.records.items()
            if (rid in asked or doc.get("virtualRecordId") in vrids)
            and self._best_grant(user_id, org_id, rid) is not None
            and doc.get("isPlaceholder") is not True
        ]
        scopes = list(scopes)
        by_vrid: dict[str, str] = {}
        for row in rows:
            vrid = row["vrid"]
            if vrid not in vrids or (indexed_only and row["indexingStatus"] != "COMPLETED"):
                continue
            if connector_ids is not None and row["connectorId"] not in connector_ids:
                continue
            if all(scope.admits(row) for scope in scopes) and (vrid not in by_vrid or row["id"] < by_vrid[vrid]):
                by_vrid[vrid] = row["id"]
        nodes = [row for row in rows if row["id"] in asked]
        return AccessCheck(
            node_ids=frozenset(row["id"] for row in nodes),
            records_by_vrid=by_vrid,
            node_ids_in_scope=frozenset(row["id"] for row in nodes if all(scope.admits(row) for scope in scopes)),
        )

    async def filter_accessible_record_ids(
        self, record_ids: list[str], user_id: str, org_id: str, *, transaction: str | None = None
    ) -> set[str]:
        self.asked_about.extend((user_id, org_id, rid) for rid in record_ids)
        self._raise_if_failing()
        return {
            rid
            for rid in record_ids
            if self._best_grant(user_id, org_id, rid) is not None
            and self.records[rid].get("isPlaceholder") is not True
            and self.records[rid].get("isInternal") is not True
        }


__all__ = ["InMemoryRecordAccess"]

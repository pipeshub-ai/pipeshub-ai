"""The knowledge hub's connector listing on PostgreSQL: All Records (the flat listing of one connector) read from the typed graph.

The structure of a page (which rows, their order, the keyset) comes from ``kg.records``; who may see a row is asked of the access check for every row a page keeps, so an answer is never
older than the check (kg classes for a connector kg serves, the live walk otherwise). A listing therefore works while a connector syncs: structure may lag the drain, access never does.
A connector with no copy in kg yet is not listed as empty: it is refused as not ready.

A page scans windows of the sort order until it has the rows asked for, the end, or a bound (time or rows looked at). At a bound it hands back the position it reached (``resume``), so that
a page with no rows can still be continued. The total counts the rows the check admits among the first :data:`COUNT_SCAN` candidates: exact when that was all of them, else a lower bound
(``totalCapped``) that never includes a row the viewer may not read.

The arrow of a group or a folder is on exactly when the browse of it (``kh_browse``) lists something: one rule, :meth:`KhListingMixin._kh_lists`, for both.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.core.org_context import current_or_system, current_org_id
from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingNotReady,
    KnowledgeHubListingTooLarge,
)
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import NotImplementedInPostgres, PgBase, _doc
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.model import CODE_COLL, CODE_EPOCH

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import Tx

BUDGET_S = 1.0               # how long one request keeps scanning for rows before it hands back the position it reached
MAX_SCANNED = 50_000         # candidates one request looks at, at most
HIDDEN_MAX = 100_000        # rows hidden by groups that hide their children that one listing carries to its statements; more is a view too wide to list (measured at 20,000 of 20,050 rows: about 100 ms)
COUNT_CAP = 10_000          # a count of what kg decides is exact up to this many rows; a listing with more reports the cap
UNDECIDED_MAX = 200         # rows kg does not decide that a count may hand to the walk
CLASS_MAX = 50_000          # classes of a connector a count evaluates
COUNT_SCAN = 500            # candidates the total asks the access check about when kg does not serve the connector (the check is the expensive part during a sync); more than that is reported as "at least"
WINDOW_MAX = 2_000
ADMIT_CHUNK = 2_000
ARROW_CHILDREN = 200         # the children of a row asked about for its arrow when the access check decides them (a sample: a row whose only readable child is further down shows no arrow)
# shortcut: each placeholder's own walk follows its way down for LEAD_NODES rows, LEAD_DEPTH levels and LEAD_PER children of a node; past those it is taken as leading nowhere (not listed, never a leak).
# Upgrade to a statement over the closure if a connector writes chains of placeholders that wide.
LEAD_NODES = 5_000
LEAD_DEPTH = 50
LEAD_PER = 1_000
ANY_GROUP = ""      # ``only_group`` of the chain-top walk for the records of every group at once (``_kh_tops_under``): each is placed under its own groups, as for one

@dataclass
class LeadBudget:
    """One request's placeholder walks (its page, its total and its arrows): the answers it holds, the rows and group checks they read against LEAD_NODES, and when it began."""
    decided: dict[int, bool] = field(default_factory=dict)
    spent: int = 0
    t0: float = field(default_factory=time.monotonic)


LINKS = "/* pipeshub:kg.listing_links */ SELECT child_sk, parent_sk, kind FROM kg.links WHERE org_id = $1 AND child_sk = ANY($2::bigint[])"
KEYS = "/* pipeshub:kg.listing_keys */ SELECT sk, coll, id FROM kg.node_keys WHERE org_id = $1 AND sk = ANY($2::bigint[])"
DOCS = """/* pipeshub:kg.listing_docs */ SELECT n.coll, n.id, n.doc FROM unnest($2::text[], $3::text[]) AS q(coll, id)
    JOIN graph.nodes n ON n.org_id = $1 AND n.coll = q.coll AND n.id = q.id"""
FILES = """/* pipeshub:kg.listing_files */ SELECT e.from_id, f.doc->>'extension' AS ext, f.doc->>'sizeInBytes' AS size FROM graph.edges e
    JOIN graph.nodes f ON f.org_id = e.org_id AND f.coll = 'files' AND f.id = e.to_id WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_coll = 'records' AND e.from_id = ANY($2::text[])"""


def _node_type(coll: str, doc: dict) -> str:
    if coll == "recordGroups":
        return "recordGroup"
    if coll == "apps":
        return "app"
    return "folder" if doc.get("mimeType") in FOLDER_MIME_TYPES or str(doc.get("recordType") or "").upper() == "FOLDER" else "record"


async def class_sets(tx: Tx, org: str, app_sk: int, held: set[str]) -> dict[str, Any] | None:
    """What the viewer's grants open of a connector kg serves, per class: met with no grant of the row's own (``plain``), met with the row's own grant (``own``), and the classes kg cannot vouch
    for (``unvouched``). ``held``: every grant of every gated connector. None when the connector has too many classes to evaluate."""
    rows = await tx.fetch(kgl.CLASSES, org, app_sk, CLASS_MAX + 1)
    if len(rows) > CLASS_MAX:
        return None
    found = {r["id"]: (json.loads(r["clauses"]) if isinstance(r["clauses"], str) else list(r["clauses"])) for r in rows}
    verdicts = await kg_read.class_verdicts(tx, org, found, held)
    return {"app_sk": app_sk, "plain": {c for c, v in verdicts.items() if v and v[0]}, "own": {c for c, v in verdicts.items() if v and v[1] and not v[0]}, "unvouched": {c for c, v in verdicts.items() if v is None}}


async def head(tx: Tx, org: str, app_id: str, held: set[str], *, by_class: bool) -> tuple[int | None, dict, list[int], bool, dict[str, set[int]] | None]:
    """A connector's listing state: its node key (None when kg holds no current copy of it), its document, the rows groups that hide their children hide, whether kg serves it, and
    what the viewer's grants open of it by class (None unless ``by_class``: the page reads one snapshot, so the stamps it admits by are the ones the currency check saw). ``held``:
    every grant of every gated connector, since a clause's literals are the nodes a walk passes through, of any connector; a row's own grant is still the App's."""
    k = await tx.fetchrow(kgl.STATE, org, app_id)
    hidden: list[int] = []
    ready = bool(k["sk"] is not None and k["mode"] != "legacy" and k["code_epoch"] == CODE_EPOCH and k["verified_at"] is not None)      # a copy of an older code lacks the listing columns: it is not listed from
    if ready and await tx.fetchval(kgl.HAS_HIDER, org, k["sk"]):
        hidden = list(await tx.fetchval(kgl.HIDDEN, org, k["sk"]))
        if len(hidden) > HIDDEN_MAX:
            raise KnowledgeHubListingTooLarge
    served = ready and kg_read.enabled() and await kg_read.serves(tx, org, app_id)      # PG_KG_READ=0, or a connector this process distrusts (as ``read.decide``): every row is the live check's
    return (k["sk"] if ready else None), (_doc(k["app_doc"]) if k["app_doc"] else {}), hidden, served, (await class_sets(tx, org, k["sk"], held) if served and by_class else None)


def origin_of(app_doc: dict) -> str:
    return "COLLECTION" if app_doc.get("type") == "KB" else "CONNECTOR"


def chain_tops_apply(app_doc: dict) -> bool:
    """Whether a browse of the connector places chain-tops: not in an App that opens everything (``_kh_v3_chain_top_groups``)."""
    return not (app_doc.get("permissionModel") == "APP_LEVEL" or app_doc.get("type") == "KB")


def filtered_out(app_id: str, app_doc: dict, filters: dict[str, Any]) -> bool:
    """Whether the connector and origin filters leave the whole connector out."""
    return bool((filters.get("connector_ids") and app_id not in filters["connector_ids"]) or (filters.get("origins") and origin_of(app_doc) not in filters["origins"]))


def where_of(org: str, app_sk: int, app_doc: dict, filters: dict[str, Any], hidden: list[int]) -> tuple[kgl.Params, str]:
    """The parameters (``$1`` the org, ``$2`` the connector, ``$3`` the window) and the conditions on a row ``r`` of the filters and the hidden rows."""
    base = kgl.Params([org, app_sk, None])
    where = kgl.filters_sql(filters, base)
    if origin_of(app_doc) == "COLLECTION" and (groups := filters.get("record_group_ids")):
        # As the other stores do: the list narrows a collection's record groups only; records and folders pass.
        where += f" AND (r.kind <> 2 OR r.id = ANY({base.add(list(groups))}::text[]))"
    if hidden:
        where += f" AND r.sk <> ALL({base.add(hidden)}::bigint[])"
    return base, where


class KhListingMixin(PgBase):
    async def _kq(self, fn: Any, transaction: str | None) -> Any:  # noqa: ANN401
        """A read unit of the listing: custom plans (a statement that carries arrays of ids is planned for them, not generically after the fifth run), in the page's transaction when it has one."""
        if transaction:
            return await self._q(fn, transaction)
        return await self._db.run(fn, jobs=current_or_system().kind == "system", custom_plan=True)

    async def get_knowledge_hub_connector_page_v3(
        self, app_id: str, org_id: str, grantee_ids: list[str], gated_app_ids: list[str], granted_ids: list[str] | None = None, limit: int = 50, *, flatten: bool = True,
        sort_field: str = "name", sort_dir: str = "ASC", after: dict[str, Any] | None = None, direction: str = "next", filters: dict[str, Any] | None = None, include_total: bool = True,
        start_id: str | None = None, start_type: str = "app", grants_by_connector: dict[str, list[str]] | None = None, include_scope: bool = False, via_parent_id: str | None = None,
        transaction: str | None = None, name_parents: bool = True, user_key: str | None = None, grants_out: dict[str, list[str]] | None = None, max_depth: int = 50,
    ) -> dict[str, Any]:
        """One page of the flat listing of a connector (``flatten`` from the app), or the browse of a record group or a record (``kh_browse``). A browse of the connector itself and a depth
        bound are not served here yet."""
        if direction not in ("next", "prev"):
            raise ValueError(f"Unsupported page direction: {direction!r}.")
        order = (sort_dir or "").strip().upper()
        if order not in ("ASC", "DESC"):
            raise ValueError(f"Unsupported sort order: {sort_dir!r}. Use 'asc' or 'desc'.")
        if sort_field not in kgl.SORTS:
            raise ValueError(f"Unsupported sort field: {sort_field!r}. Allowed: {sorted(kgl.SORTS)}")
        start_id = start_id or app_id
        if flatten and (max_depth != 50 or not (start_type in ("app", "kb") or start_id == app_id)):
            raise NotImplementedInPostgres("PostgresGraphProvider.get_knowledge_hub_connector_page_v3 flattens a whole connector only")
        org = current_org_id()
        filters = filters or {}
        if flatten and (org_id != org or app_id not in set(gated_app_ids)):
            return self._kh_v3_empty_page(start_id, include_scope=include_scope, include_total=include_total, admitted=False)
        if flatten:      # a browse resolves its App from its start first (``_kh_browse``)
            self._kh_v3_require_other_grants(app_id, gated_app_ids, granted_ids, grants_by_connector, user_key)      # before any read
        if transaction is None:
            # One snapshot for the page (a browse's too): whether kg serves the connector and the rows its classes admit are read at one commit. A change to the connector as a whole hides it
            # from kg without marking its rows (restamp.py), so a page read after such a commit, by a check made before it, would admit by stamps that are hidden.
            # Every read of the page runs on this one connection, the access checks included: a page that held one pooled connection and waited for another would
            # starve the pool under as many concurrent pages as it has connections.
            snap = await self.begin_snapshot()
            try:
                return await self.get_knowledge_hub_connector_page_v3(
                    app_id, org_id, grantee_ids, gated_app_ids, granted_ids, limit, flatten=flatten, sort_field=sort_field, sort_dir=sort_dir, after=after, direction=direction,
                    filters=filters, include_total=include_total, start_id=start_id, start_type=start_type, grants_by_connector=grants_by_connector, include_scope=include_scope,
                    via_parent_id=via_parent_id, transaction=snap, name_parents=name_parents, user_key=user_key, grants_out=grants_out, max_depth=max_depth)
            finally:
                await self.rollback_transaction(snap)
        # Admission by class trusts that the stamps read are the ones the currency check saw: only in the page's snapshot. A caller's own transaction (read committed) reads
        # each statement at a new commit, so its rows are asked of the access check, which reads kg's currency with the stamps it reads.
        snapshot = bool(getattr(self._txns.get(transaction), "snapshot", False))
        if not flatten:
            return await self._kh_browse(
                org=org, org_id=org_id, app_id=app_id, start_id=start_id, grantee_ids=grantee_ids, gated_app_ids=gated_app_ids, granted_ids=granted_ids, grants_by_connector=grants_by_connector,
                user_key=user_key, grants_out=grants_out, limit=limit, sort_field=sort_field, order=order, after=after, direction=direction, filters=filters, include_total=include_total,
                include_scope=include_scope, via_parent_id=via_parent_id, transaction=transaction, name_parents=name_parents, by_class=snapshot)
        # Every gated connector's grants (`_kh_v3_page_grants`); a row's own grant is this App's.
        every = await self._kh_v3_page_grants(app_id, org_id, gated_app_ids, granted_ids, grants_by_connector, user_key, transaction)
        granted = every.get(app_id) or []
        held = {g for ids in every.values() for g in ids}
        if grants_out is not None:
            grants_out[app_id] = granted
        access = {"grantee_ids": grantee_ids, "gated_app_ids": gated_app_ids, "by_connector": every}

        app_sk, app_doc, hidden, served, sets = await self._kq(lambda tx: head(tx, org, app_id, held, by_class=snapshot), transaction)
        if app_sk is None:
            raise KnowledgeHubListingNotReady
        if filtered_out(app_id, app_doc, filters):
            return self._kh_v3_empty_page(start_id, include_scope=include_scope, include_total=include_total, admitted=True)
        base, where = where_of(org, app_sk, app_doc, filters, hidden)
        admit = self._kh_admitter(org, access, transaction, served=served)
        gset = set(granted)
        admitted_of = self._kh_admitted_of(sets, gset, admit)
        viewer = await self._kh_flat_viewer(sets, where, base, admit, granted, transaction)
        admit_sql, pbase = None, kgl.Params(list(base.values))
        if viewer is not None:      # a connector kg serves: the classes admit in the statement, so a window needs no question to the access check
            admit_sql = kgl.class_admit("w", pbase, viewer)
        need, kept, boundary, scanned, resume = limit + 1, [], after, 0, None
        window, t0 = min(WINDOW_MAX, max(need * 4, 100)), time.monotonic()
        while len(kept) < need:
            p = kgl.Params(list(pbase.values))
            p.values[2] = window
            branches = kgl.after(sort_field, order, direction, boundary, p) if boundary is not None else None
            sql = kgl.page_sql(sort_field, order, direction, where, branches, admit_sql)
            got = await self._kq(lambda tx, s=sql, v=tuple(p.values): tx.fetch(s, *v), transaction)
            allowed = None if viewer is not None else await admit([r["id"] for r in got]) if got else set()
            kept += [r for r in got if (r["ok"] if allowed is None else r["id"] in allowed)][:need - len(kept)]
            if len(got) < window:
                break
            if len(kept) >= need:
                break
            scanned += len(got)
            last = got[-1]
            value_col, order_col = kgl.SORTS[sort_field]
            boundary = {"nullRank": 1 if last[order_col] is None else 0, "sortKey": last[value_col], "id": last["id"]}      # rank by the column the statement orders by: a NULL there sorts last, by id
            if scanned >= MAX_SCANNED or time.monotonic() - t0 > BUDGET_S:
                resume = boundary
                break
            window = min(WINDOW_MAX, window * 8)
        total = counts = None
        capped = False
        if include_total:
            total, counts, capped = await self._kh_flat_exact(org, app_sk, where, base, viewer, transaction) if viewer is not None else await self._kh_flat_count(org, app_sk, where, base, admit, transaction)
        # the arrow of a group or a folder: on exactly when a browse of it lists something
        containers = [c for c in kept[:need] if c["kind"] == 2 or c["folder"]]
        rows, read = await self._kh_flat_rows(org, app_id, app_doc, kept[:need], sort_field, transaction,
                                              also=lambda tx: self._kh_arrows_read(tx, org, containers, hidden, sets, gset, access, tops=chain_tops_apply(app_doc)))
        tops = self._kh_tops_under(org, app_id, access, gset, transaction, apply=chain_tops_apply(app_doc))
        lists = await self._kh_lists(read, hidden, admitted_of, org, transaction, tops) if read else set()
        lists |= await self._kh_held_lists(kept[:need], read, lists, tops)
        for row, c in zip(rows, kept[:need], strict=True):
            row["hasChildren"] = c["sk"] in lists
        payload = {"rows": rows, "total": total, "nRecord": (counts or {}).get("record", 0), "nFolder": (counts or {}).get("folder", 0), "nGroup": (counts or {}).get("recordGroup", 0)}
        page = await self._kh_v3_page_result(
            payload, limit=limit, direction=direction, include_total=include_total, include_scope=include_scope, scoped_start=False, start_id=start_id, app_id=app_id,
            via_parent_id=via_parent_id, transaction=transaction, org_id=org_id, access=access, listed_under=None, name_parents=name_parents)
        if capped:
            page["totalCapped"] = True
        if resume is not None:       # looked at as many rows as it may and found fewer than a page: the rows are complete up to ``resume``, and the next page starts there
            page["hasMore"] = True
            page["resume"] = resume
        return page

    def _kh_admitter(self, org: str, access: dict[str, Any], transaction: str | None, *, served: bool) -> Callable[[list[str]], Awaitable[set[str]]]:
        """The access check for a list of ids: kg first for a connector it serves, the live walk otherwise."""
        async def admit(ids: list[str]) -> set[str]:
            out: set[str] = set()
            for i in range(0, len(ids), ADMIT_CHUNK):
                chunk = ids[i:i + ADMIT_CHUNK]
                if served:
                    rows = await self._kh_v3_accessible_rows("", org, chunk, [], access=access, transaction=transaction)
                else:      # kg decides nothing for a connector it does not serve: the live check is asked directly, without the read of kg that would hand every row back to it
                    rows = await self._kq(lambda tx, c=chunk: access_sql.accessible_rows(tx, org, c, [], access), transaction)
                out |= {r["id"] for r in rows}
            return out
        return admit

    def _kh_admitted_of(self, sets: dict[str, Any] | None, gset: set[str], admit: Callable[[list[str]], Awaitable[set[str]]]) -> Callable[[list[Any]], Awaitable[set[str]]]:
        """Of rows of the listing's window, the ids the viewer may open: the classes decide what they can, the access check the rest."""
        async def admitted_of(rows: list[Any]) -> set[str]:
            ok, left = self._kh_class_split(rows, sets, gset)
            return ok | (await admit([r["id"] for r in left]) if left else set())
        return admitted_of

    @staticmethod
    def _kh_class_split(rows: list[Any], sets: dict[str, Any] | None, granted: set[str]) -> tuple[set[str], list[Any]]:
        """Of rows, the ids the stamped classes admit, and the rows the classes do not decide (no stamp, a placeholder, a pending restamp, a class kg cannot vouch for): those go to the access check."""
        if sets is None:
            return set(), list(rows)
        ok: set[str] = set()
        left: list[Any] = []
        for r in rows:
            # a row of another connector has its connector's classes, not these: the check decides it, as it does a deleted or a foreign one
            if (r["r13"] is False and r["has_path"] and r["class_id"] is not None and not r["fenced"] and not r["placeholder"] and r["class_id"] not in sets["unvouched"]
                    and r["app_sk"] == sets["app_sk"] and not r["deleted"] and not r["alien"]):
                if r["class_id"] in sets["plain"] or (r["class_id"] in sets["own"] and r["id"] in granted):
                    ok.add(r["id"])
            else:
                left.append(r)
        return ok, left

    def _kh_tops_under(self, org: str, app_id: str, access: dict[str, Any], gset: set[str], transaction: str | None, *, apply: bool) -> Callable[[list[Any]], Awaitable[set[int]]] | None:
        """Of group rows, the sks the chain-top walk places a record under (``_kh_v3_chain_tops``, the rule every store shares), for the placeholder walk and the arrows. Resolved once
        per request and held: the groups that hold a granted record (``kgl.HELD_ANY``, by key from the grants), then, only if there are any, the placements of every granted record
        of the App in one pass; each question after that is answered from them. None in an App that opens everything."""
        if not apply or not gset:
            return None
        held: set[int] | None = None
        placed: set[str] | None = None

        async def under(groups: list[Any]) -> set[int]:
            nonlocal held, placed
            if held is None:
                held = {r["sk"] for r in await self._kq(lambda tx: tx.fetch(kgl.HELD_ANY, org, sorted(gset)), transaction)}
            asked = [g for g in groups if g["sk"] in held]
            if not asked:
                return set()
            if placed is None:
                by_groups = await self._kh_v3_chain_top_groups(app_id, org, sorted(gset), ANY_GROUP, transaction)
                found = await self._kh_v3_chain_tops(app_id, org, access, transaction, only_group=ANY_GROUP, by_groups=by_groups) if by_groups else {}
                placed = {g for g, rows in found.items() if rows and g != app_id}
            return {g["sk"] for g in asked if g["id"] in placed}
        return under

    @staticmethod
    async def _kh_held_lists(page: list[Any], read: tuple | None, lists: set[int], tops: Callable[[list[Any]], Awaitable[set[int]]] | None) -> set[int]:
        """Of the page's groups that hold a granted record (``read[3]``) and list nothing else, those a chain-top is placed under: their arrow is on too."""
        held = [c for c in page if read and c["sk"] in read[3] and c["sk"] not in lists]
        return await tops(held) if tops and held else set()

    async def _kh_leads(self, stubs: list[Any], hidden: list[int], admitted_of: Callable[[list[Any]], Awaitable[set[str]]], org: str, transaction: str | None, *,
                        tops: Callable[[list[Any]], Awaitable[set[int]]] | None = None, budget: LeadBudget | None = None) -> tuple[set[int], set[int]]:
        """Of placeholders the viewer may open (rows, in the caller's order), the sks that lead somewhere (:meth:`_kh_lead`), and the sks left undecided. Each placeholder is decided by
        a walk of its own, so its answer is the same in any window, page or cursor. ``budget`` is the request's (:class:`LeadBudget`, shared by every call of one page: its rows,
        its total and its arrows): LEAD_NODES rows at most over all their walks, none started after BUDGET_S. The request's first walk has all of it, so a caller that resumes at
        the first undecided placeholder always moves on; the next ones share what is left (their first level in one statement), and one the request cannot finish within it is
        left undecided, never taken as leading nowhere."""
        budget = budget or LeadBudget()
        memo = budget.decided
        todo = [r for r in {r["sk"]: r for r in stubs}.values() if r["sk"] not in memo]

        def may_start() -> bool:
            return budget.spent == 0 or (budget.spent < LEAD_NODES and time.monotonic() - budget.t0 <= BUDGET_S)
        if todo and may_start():
            found, used = await self._kh_lead(todo[0], hidden, admitted_of, org, transaction, tops, allowance=LEAD_NODES - budget.spent)
            budget.spent += used
            if found is not None:
                memo[todo[0]["sk"]] = found
        rest = todo[1:] if todo and todo[0]["sk"] in memo else []
        if rest and may_start():
            b = LEAD_NODES - budget.spent
            got = await self._kq(lambda tx, f=[r["sk"] for r in rest]: tx.fetch(kgl.children_of_sql(total=True), org, f, LEAD_PER, hidden, b), transaction)
            budget.spent += len(got)
            kids: dict[int, list[Any]] = {}
            for r in got:
                kids.setdefault(r["parent"], []).append(r)
            # the statement reads the placeholders in the order asked: cut at its bound, the last one read may be partial and those after it are unread (undecided)
            whole = rest if len(got) < b else rest[:[r["sk"] for r in rest].index(got[-1]["parent"])]
            read = {w["sk"] for w in whole}
            ok = await admitted_of([r for r in got if r["parent"] in read]) if whole else set()
            for r in whole:
                if not may_start():
                    break
                found, used = await self._kh_lead(r, hidden, admitted_of, org, transaction, tops, allowance=LEAD_NODES - budget.spent, first=(kids.get(r["sk"], []), ok))
                budget.spent += used
                if found is None:      # the request's rows ran out before this walk ended: it and the ones after it wait for the next page
                    break
                memo[r["sk"]] = found
        return {r["sk"] for r in stubs if memo.get(r["sk"])}, {r["sk"] for r in stubs if r["sk"] not in memo}

    async def _kh_lead(self, root: Any, hidden: list[int], admitted_of: Callable[[list[Any]], Awaitable[set[str]]], org: str, transaction: str | None,  # noqa: ANN401
                       tops: Callable[[list[Any]], Awaitable[set[int]]] | None, *, allowance: int, first: tuple[list[Any], set[str]] | None = None) -> tuple[bool | None, int]:
        """Whether a path of nodes the viewer may open leads from the placeholder ``root`` to a node that is no placeholder, or to a placeholder group a chain-top is placed under
        (``tops``): its walk alone, level by level through the open placeholders below it, each node once, at most LEAD_NODES rows and group checks of its own; and the rows it read
        here (``first``, its own children and the ids among them the viewer may open, were read by the caller and are not counted). None when the request's ``allowance`` ran out
        before the walk could end: the answer is then unknown, not no."""
        seen, level, used, mine = {root["sk"]}, [root], 0, 0      # used: rows read here, against the allowance; mine: the walk's own count, against LEAD_NODES

        def cap() -> tuple[int, bool]:
            """How many more rows or checks this walk may take, and whether that is its own bound (else the request's)."""
            own, left = LEAD_NODES - mine, allowance - used
            return (own, True) if own <= left else (left, False)
        for depth in range(LEAD_DEPTH):
            groups = sorted((r for r in level if r["kind"] == 2), key=lambda r: r["sk"])
            if tops and groups:
                # each group asked is one of the walk's LEAD_NODES: past them it stops, as at a row past them (the same groups on every run, in key order)
                room, own = cap()
                asked = groups[:max(0, room)]
                used, mine = used + len(asked), mine + len(asked)
                if asked and await tops(asked):
                    return True, used
                if len(asked) < len(groups):
                    return (False if own else None), used
            if depth == 0 and first is not None:
                got, ok = first
                mine += len(got)
            else:
                room, own = cap()
                if room <= 0:
                    return (False if own else None), used
                got = await self._kq(lambda tx, f=sorted(r["sk"] for r in level), b=room: tx.fetch(kgl.children_of_sql(total=True), org, f, LEAD_PER, hidden, b), transaction)
                used, mine = used + len(got), mine + len(got)
                ok = None
            fresh = [r for r in got if r["sk"] not in seen]
            if ok is None:
                ok = await admitted_of(fresh) if fresh else set()
            if any(r["id"] in ok and not r["placeholder"] for r in fresh):
                return True, used
            if (depth > 0 or first is None) and len(got) >= room and not own:      # cut by the request's rows, not its own: what lies past the cut is unknown
                return None, used
            level = []
            for r in fresh:
                if r["id"] in ok and r["placeholder"] and r["sk"] not in seen:
                    seen.add(r["sk"])
                    level.append(r)
            if not level:
                break
        return False, used

    async def _kh_arrows_read(self, tx: Tx, org: str, page: list[Any], hidden: list[int], sets: dict[str, Any] | None, gset: set[str], access: dict[str, Any], *,
                              tops: bool) -> tuple[set[int], list[Any], set[str], set[int]]:
        """The read behind the arrows of a page's rows, in the caller's unit: the rows a statement over the classes finds a child for (a connector kg serves: exact), and for the
        others a sample of their children with the ones the viewer may open (the classes, then the live check for what they do not decide). Last, with ``tops``, the groups that
        hold a granted record that may be a chain-top listed under them (``kgl.HELD``)."""
        asked = [c["sk"] for c in page if not c["hide_children"]]
        lists: set[int] = set()
        if not asked:
            return lists, [], set(), set()
        groups = [c["sk"] for c in page if c["kind"] == 2 and not c["hide_children"]]
        held = {r["sk"] for r in await tx.fetch(kgl.HELD, org, groups, sorted(gset))} if tops and groups and gset else set()
        rest = asked
        if sets is not None:
            q = kgl.Params([org, asked, hidden])
            admit_c = kgl.class_admit("c", q, {"plain": sorted(sets["plain"]), "own": sorted(sets["own"]), "unvouched": sorted(sets["unvouched"]), "granted": sorted(gset), "walked": set()})
            # a child the classes do not decide (no stamp, a placeholder, a class kg cannot vouch for, another connector's): only the rows that have one are sampled
            found = await tx.fetch(kgl.arrows_sql(admit_c, kgl.undecided_child(q, sorted(sets["unvouched"]), sets["app_sk"]), ARROW_CHILDREN), *q.values)
            lists = {r["sk"] for r in found if r["lists"]}
            rest = [r["sk"] for r in found if not r["lists"] and r["undecided"]]
        # the sample: a row's first children (never a pass over all of them), the classes deciding what they can and the live check the rest
        kids = list(await tx.fetch(kgl.children_of_sql(), org, rest, ARROW_CHILDREN, hidden)) if rest else []
        ok, left = self._kh_class_split(kids, sets, gset)
        for i in range(0, len(left), ADMIT_CHUNK):
            ok |= {r["id"] for r in await access_sql.accessible_rows(tx, org, [r["id"] for r in left[i:i + ADMIT_CHUNK]], [], access)}
        return lists, kids, ok, held

    async def _kh_lists(self, read: tuple[set[int], list[Any], set[str], set[int]], hidden: list[int], admitted_of: Callable[[list[Any]], Awaitable[set[str]]], org: str,
                        transaction: str | None, tops: Callable[[list[Any]], Awaitable[set[int]]] | None = None, budget: LeadBudget | None = None) -> set[int]:
        """The rows of the page whose opening lists something (from :meth:`_kh_arrows_read`): a child the viewer may open, a placeholder only when it leads somewhere
        (:meth:`_kh_leads`); none for a group that hides its children."""
        lists, kids, ok, _ = read
        by_stub: dict[int, set[int]] = {}
        stubs: list[Any] = []
        for r in kids:
            if r["id"] not in ok:
                continue
            if not r["placeholder"]:
                lists.add(r["parent"])
            elif r["parent"] not in lists:
                by_stub.setdefault(r["sk"], set()).add(r["parent"])
                stubs.append(r)
        if by_stub:
            # shortcut: a placeholder the request's budget leaves undecided lights no arrow; opening the row resumes before it. Upgrade if rows of many wide placeholders are common.
            led, _ = await self._kh_leads([r for r in stubs if by_stub[r["sk"]] - lists], hidden, admitted_of, org, transaction, tops=tops, budget=budget)
            for sk in led:
                lists |= by_stub[sk]
        return lists

    async def _kh_flat_viewer(self, sets: dict[str, set[int]] | None, where: str, base: kgl.Params, admit: Any, granted: list[str], transaction: str | None) -> dict[str, Any] | None:  # noqa: ANN401
        """What the viewer's grants open of a connector kg serves, by class: the classes met with no grant of the row's own (``plain``), the classes that need the row's own grant (``own``), the classes
        kg cannot vouch for (``unvouched``) and the few rows no class decides, with the ones among them the access check admits (``walked``). None when the connector is not served or too many rows are
        left to the check: the rows are then asked of the access check one window at a time."""
        if sets is None:
            return None
        plain, own, unvouched = sorted(sets["plain"]), sorted(sets["own"]), sorted(sets["unvouched"])
        p = kgl.Params(list(base.values))
        p.values[2] = UNDECIDED_MAX + 1
        sql = kgl.undecided_sql(where, p.add(unvouched))
        left = [r["id"] for r in await self._kq(lambda tx, s=sql, v=tuple(p.values): tx.fetch(s, *v), transaction)]
        if len(left) > UNDECIDED_MAX:
            return None
        return {"plain": plain, "own": own, "unvouched": unvouched, "left": left, "walked": await admit(left) if left else set(), "granted": sorted(set(granted))}

    async def _kh_flat_exact(self, org: str, app_sk: int, where: str, base: kgl.Params, viewer: dict[str, Any], transaction: str | None) -> tuple[int, dict[str, int], bool]:
        """The total of a connector kg serves, from the stamped classes: what the viewer's grants open by class are counted where the rows are, up to COUNT_CAP, and the few rows kg does not decide
        are counted from the access check's answer."""
        left, walked = viewer["left"], viewer["walked"]
        q = kgl.Params(list(base.values))
        q.values[2] = COUNT_CAP + 1
        sql = kgl.count_sql(where, q.add(viewer["plain"]), q.add(viewer["own"]), q.add(viewer["granted"]), q.add(viewer["unvouched"]))
        counts = {"record": 0, "folder": 0, "recordGroup": 0}
        for r in await self._kq(lambda tx, s=sql, v=tuple(q.values): tx.fetch(s, *v), transaction):
            counts["recordGroup" if r["kind"] == 2 else "folder" if r["folder"] else "record"] += r["n"]
        if left:       # the rows the check admitted, by type, from their own rows
            by_id = {r["id"]: r for r in await self._kq(lambda tx: tx.fetch("/* pipeshub:kg.listing_walked */ SELECT id, kind, folder FROM kg.records r WHERE org_id = $1 AND app_sk = $2 AND kind IN (2, 3) AND id = ANY($3::text[])", org, app_sk, sorted(walked)), transaction)}
            for i in walked:
                r = by_id.get(i)
                if r:
                    counts["recordGroup" if r["kind"] == 2 else "folder" if r["folder"] else "record"] += 1
        total = sum(counts.values())
        return min(total, COUNT_CAP), {k: v for k, v in counts.items() if v}, total > COUNT_CAP

    async def _kh_flat_count(self, org: str, app_sk: int, where: str, base: kgl.Params, admit: Any, transaction: str | None) -> tuple[int, dict[str, int], bool]:  # noqa: ANN401
        """The rows of the first COUNT_SCAN candidates the check admits, by type, and whether there were more candidates (then the total is a lower bound)."""
        counts = {"record": 0, "folder": 0, "recordGroup": 0}
        last, seen, capped = 0, 0, False
        while True:
            p = kgl.Params(list(base.values))
            p.values[2] = COUNT_SCAN - seen + 1
            sql = kgl.scan_sql(where, p.add(last))
            rows = await self._kq(lambda tx, s=sql, v=tuple(p.values): tx.fetch(s, *v), transaction)
            take = rows[:COUNT_SCAN - seen]
            allowed = await admit([r["id"] for r in take]) if take else set()
            for r in take:
                if r["id"] in allowed:
                    counts["recordGroup" if r["kind"] == 2 else "folder" if r["folder"] else "record"] += 1
            seen += len(take)
            if len(rows) > len(take):
                capped = True
                break
            if len(rows) < p.values[2]:
                break
            last = rows[-1]["sk"]
        return sum(counts.values()), {k: v for k, v in counts.items() if v}, capped

    async def _kh_flat_rows(self, org: str, app_id: str, app_doc: dict, cands: list[Any], sort_field: str, transaction: str | None,
                            also: Callable[[Tx], Awaitable[Any]] | None = None, under: dict[str, Any] | None = None, tx: Tx | None = None) -> tuple[list[dict[str, Any]], Any]:
        """The rows of a page as the listing returns them, from the documents of the page's nodes; ``also`` is read in the same unit and its answer handed back beside them.
        ``under``: the node a browse lists them under, the parent every row names (its other parents are not read). ``tx``: read in the caller's unit."""
        if not cands:
            return [], None
        col, order_col = kgl.SORTS[sort_field]
        sks = [c["sk"] for c in cands]
        sks_all = sorted({*sks, *(c["parent_sk"] for c in cands if c["parent_sk"]), *(c["group_sk"] for c in cands if c["group_sk"])})

        async def read(tx: Tx) -> tuple:
            if under is not None:      # the rows' own keys are on the rows
                links, keys = [], {c["sk"]: (CODE_COLL[c["coll"]], c["id"]) for c in cands}
            else:
                links = await tx.fetch(LINKS, org, sks)
                extra = sorted({*sks_all, *(r["parent_sk"] for r in links)})
                keys = {r["sk"]: (CODE_COLL[r["coll"]], r["id"]) for r in await tx.fetch(KEYS, org, extra)}
            pairs = sorted(set(keys.values()))
            docs = {(r["coll"], r["id"]): _doc(r["doc"]) for r in await tx.fetch(DOCS, org, [c for c, _ in pairs], [i for _, i in pairs])}
            files = {r["from_id"]: r for r in await tx.fetch(FILES, org, [c["id"] for c in cands if c["kind"] == 3])}
            return links, keys, docs, files, (await also(tx) if also else None)
        links, keys, docs, files, more = await (read(tx) if tx is not None else self._kq(read, transaction))
        up: dict[int, list[tuple[int, int]]] = {}
        for lk in links:
            up.setdefault(lk["child_sk"], []).append((lk["parent_sk"], lk["kind"]))

        def option(sk: int, *, own: bool) -> dict[str, Any] | None:
            key = keys.get(sk)
            d = docs.get(key) if key else None
            if not d:
                return None
            o = {"id": d["id"], "name": d.get("name") or d.get("recordName") or d.get("groupName"), "nodeType": _node_type(key[0], d), "isInternal": bool(d.get("isInternal"))}
            return {**o, "ownGroup": True} if own else o
        out = []
        for c in cands:
            coll = CODE_COLL[c["coll"]]
            d = docs.get((coll, c["id"])) or {}
            f = files.get(c["id"])
            hier = [c["parent_sk"], *(p for p, k in up.get(c["sk"], []) if k == 1)]
            grp = [c["group_sk"], *(p for p, k in up.get(c["sk"], []) if k == 2)]
            options = [under] if under is not None else [o for o in (*(option(p, own=False) for p in hier if p), *(option(g, own=True) for g in grp if g)) if o]
            options.append({"id": app_id, "name": app_doc.get("name"), "nodeType": "app", "isInternal": False})
            key = c[col]
            out.append({
                "id": c["id"], "name": d.get("name") or d.get("recordName") or d.get("groupName"), "nodeType": _node_type(coll, d), "parentId": None, "parentType": None,
                "parentName": None, "parentIsInternal": False, "origin": "COLLECTION" if app_doc.get("type") == "KB" else "CONNECTOR", "connector": d.get("connector") or app_doc.get("type"),
                "connectorId": d.get("connectorId") or app_id, "recordType": d.get("recordType"), "recordGroupType": d.get("groupType"), "indexingStatus": d.get("indexingStatus"),
                "reason": d.get("reason"), "embedDone": d.get("embedDone"), "embedTotal": d.get("embedTotal"), "createdAt": c["created_ms"], "updatedAt": c["updated_ms"],
                "sizeInBytes": d.get("sizeInBytes") if d.get("sizeInBytes") is not None else (int(f["size"]) if f and f["size"] not in (None, "") else None),
                "mimeType": d.get("mimeType"), "extension": f["ext"] if f else d.get("extension"), "webUrl": d.get("webUrl"), "sharingStatus": d.get("sharingStatus"),
                "isInternal": bool(d.get("isInternal")), "isPlaceholder": bool(c["placeholder"]), "hasChildren": False, "userRole": None,
                "sortKey": key, "nullRank": 1 if c[order_col] is None else 0, "parentOptions": options})
        return out, more

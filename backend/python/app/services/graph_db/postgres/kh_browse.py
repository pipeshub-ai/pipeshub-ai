"""The knowledge hub's browse of one node (the App, a record group or a record) on PostgreSQL: its children by a hierarchy edge that the viewer may open, and, for the App or a
group, the viewer's chain-tops placed there (granted rows whose hierarchy parents they may not open: ``GraphDBProvider._kh_v3_chain_tops``, the rule every store shares).

A child is listed when the access decision admits it (the same decision as the flat listing's: the stamped classes for a connector kg serves, the live walk otherwise), it is not hidden by a group
that hides its children, and, when it is a placeholder, a path of nodes the viewer may open leads from it to a node that is no placeholder. A group that hides its children lists none. An arrow is on
exactly when opening the row lists something. So the browse lists every hierarchy child the viewer may open, and nothing they may not. The decision does not depend on where the browse starts, so a
placeholder is listed and shows its arrow exactly when a browse of the placeholder itself lists something.

The children of a node are read whole up to :data:`BROWSE_MEMORY_MAX`, admitted, sorted and cut in memory. A wider node is read in windows of the children indexes of the name and the updated
sorts (as the flat listing reads a connector) and is refused in the other sorts.
"""
from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING, Any

from app.core.org_context import current_org_id
from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingNotReady,
    KnowledgeHubListingTooLarge,
)
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import PgBase, _doc
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg.model import CODE_COLL
from app.services.graph_db.postgres.kh_listing import (
    ADMIT_CHUNK,
    ANY_GROUP,
    BUDGET_S,
    COUNT_CAP,
    COUNT_SCAN,
    DOCS,
    KEYS,
    LINKS,
    MAX_SCANNED,
    UNDECIDED_MAX,
    WINDOW_MAX,
    LeadBudget,
    _node_type,
    chain_tops_apply,
    filtered_out,
    head,
    where_of,
)

# The connector of a node kg holds no row for (not copied yet): from its document.
START_CONNECTOR = """/* pipeshub:kg.browse_start_connector */ SELECT doc->>'connectorId' FROM graph.nodes WHERE org_id = $1 AND coll = ANY('{records,recordGroups}'::text[]) AND id = $2
    LIMIT 1"""

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import Tx

BROWSE_MEMORY_MAX = 5_000     # children of one node a browse holds, sorts and cuts in memory; past it only the name and updated sorts are served (by the children indexes), the others are refused
NO_COUNTS = {"record": 0, "folder": 0, "recordGroup": 0}


class KhBrowseMixin(PgBase):
    async def _kh_browse(self, *, org: str, org_id: str, app_id: str, start_id: str, grantee_ids: list[str], gated_app_ids: list[str], granted_ids: list[str] | None,
                         grants_by_connector: dict[str, list[str]] | None, user_key: str | None, grants_out: dict[str, list[str]] | None, limit: int, sort_field: str, order: str,
                         after: dict[str, Any] | None, direction: str, filters: dict[str, Any], include_total: bool, include_scope: bool, via_parent_id: str | None,
                         transaction: str, name_parents: bool, by_class: bool) -> dict[str, Any]:
        """The browse of ``start_id``, in the page's transaction: the page's snapshot (``by_class``: the classes may admit), or a caller's own transaction (every row is asked of the check)."""
        def refused() -> dict[str, Any]:
            return self._kh_v3_empty_page(start_id, include_scope=include_scope, include_total=include_total, admitted=False)
        if org_id != org:
            return refused()
        at_app = start_id == app_id      # the connector itself: kg keeps it as the hierarchy parent of its top nodes, so it is browsed by its own key
        gated = set(gated_app_ids)
        side = bool(filters.get("only_containers"))      # the sidebar: the rows the browse lists that are groups or folders, or records whose arrow is on

        async def resolve(tx: Tx) -> tuple[Any, str] | str:
            """The start and its connector."""
            start, aid = None, app_id
            if not at_app:      # a browse of a node names no connector: it is the one the node belongs to (from the graph when kg has not copied the node)
                found = await tx.fetch(kgl.BROWSE_START, org, start_id)
                start = found[0] if len(found) == 1 else None
                aid = (start["app_id"] if start else None) or (await tx.fetchval(START_CONNECTOR, org, start_id) if not found else None) or ""
            return "refused" if aid not in gated else (start, aid)

        resolved = await self._q(resolve, transaction)
        if resolved == "refused":
            return refused()
        start, aid = resolved
        # Every gated connector's grants, as the flat listing reads them (``_kh_v3_page_grants``): the access check and the class verdicts read them all, since a walk can pass
        # through a node of another gated connector; a row's own grant, the seeds and the chain-tops are this App's.
        every = await self._kh_v3_page_grants(aid, org_id, gated_app_ids, granted_ids, grants_by_connector, user_key, transaction)
        granted = every.get(aid) or []
        held = {g for ids in every.values() for g in ids}
        access = {"grantee_ids": grantee_ids, "gated_app_ids": gated_app_ids, "by_connector": every}
        tops: list[int] = []
        if at_app or (start is not None and start["kind"] == 2):
            # The viewer's chain-tops placed here (granted rows whose hierarchy parents they may not open), by the rule every store shares: read beside the node's linked children.
            placed = await self._kh_v3_chain_tops(aid, org, access, transaction, only_group=None if at_app else start_id)
            tops = [c["sk"] for c in placed.get(aid if at_app else start_id, [])]

        async def unit(tx: Tx) -> dict[str, Any] | str:
            """Everything up to the page, in one read: the listing state, the breadcrumbs' trail, the children of a node held whole, and the access check's answer for the start,
            its ancestors and the children the classes do not decide."""
            app_sk, app_doc, hidden, served, sets = await head(tx, org, aid, held, by_class=by_class)
            if app_sk is None:      # a connector the viewer may browse that kg holds no current copy of: not ready, as the flat listing says
                return "not_ready"
            # a start of this connector that kg holds and no group above hides (else the browse's 404)
            if not at_app and (start is None or start["app_sk"] != app_sk or start["deleted"] or start["alien"] or start["sk"] in set(hidden)):
                return "refused"
            key = app_sk if at_app else start["sk"]
            links = [r["child_sk"] for r in await tx.fetch(kgl.LINKED_CHILDREN, org, key, kgl.LINKS_MAX + 1, hidden)]      # read once: every statement below is handed them
            linked = set(links)
            by_key = [*links, *(sk for sk in tops if sk not in linked)]      # the chain-tops are read by their keys, as the linked children are
            base, where = where_of(org, app_sk, app_doc, filters, hidden, browse=True)
            where += "" if self._kh_lists_placeholders(flatten=False, filters=filters) else " AND NOT r.placeholder"
            p = kgl.Params(list(base.values))
            p.values[1], p.values[2] = key, BROWSE_MEMORY_MAX + 1
            links_param = p.add(by_key) if by_key else None
            trail = await self._kh_browse_trail(tx, org, start) if include_scope and not at_app else None
            if at_app:      # the node every row names as its parent
                under = {"id": aid, "name": app_doc.get("name"), "nodeType": "app", "isInternal": False}
            else:
                ck = (CODE_COLL[start["coll"]], start_id)
                d = trail[3].get(ck) if trail else next((_doc(r["doc"]) for r in await tx.fetch(DOCS, org, [ck[0]], [start_id])), None)
                d = d or {}
                under = {"id": start_id, "name": d.get("name") or d.get("recordName") or d.get("groupName"), "nodeType": _node_type(ck[0], d), "isInternal": bool(d.get("isInternal"))}
            rows: list[Any] | None = []
            if len(links) <= kgl.LINKS_MAX and (at_app or not start["hide_children"]):
                got = await tx.fetch(kgl.children_held_sql(where, links_param), *p.values)
                rows = None if got[0]["n"] > BROWSE_MEMORY_MAX else [r for r in got if r["sk"] is not None]
            gs = set(granted)
            ok, left = self._kh_class_split(rows or [], sets, gs)
            # the children the classes do not decide are asked here only when they are few; otherwise (kg does not serve the connector, or it is mid-sync) they are asked a page at a time
            windowed = rows is not None and (sets is None or len(left) > UNDECIDED_MAX)
            # one question to the check: the start, its ancestors (which the breadcrumbs name) and those children
            ancestors = [r for sk, r in (trail[4] if trail else {}).items() if sk != start["sk"] and r["kind"] != 1]
            ok2, left2 = self._kh_class_split([*([] if at_app else [start]), *ancestors], sets, gs)
            ok |= ok2
            ids = [r["id"] for r in [*left2, *([] if windowed else left)]]
            for k in range(0, len(ids), ADMIT_CHUNK):
                ok |= {r["id"] for r in await access_sql.accessible_rows(tx, org, ids[k:k + ADMIT_CHUNK], [], access)}
            if not at_app and start_id not in ok:
                return "refused"
            if len(links) > kgl.LINKS_MAX:
                raise KnowledgeHubListingTooLarge
            u = {"start_sk": start["sk"] if start else None, "app_id": aid, "granted": granted, "app_sk": app_sk, "app_doc": app_doc, "hidden": hidden, "served": served,
                 "sets": sets, "filtered": filtered_out(aid, app_doc, filters), "under": under, "where": where, "links": links_param, "p": p, "trail": trail, "rows": rows,
                 "opened": ok, "windowed": windowed, "access": access, "done": None}
            if not u["filtered"] and rows is not None and not windowed and not side and not any(r["placeholder"] and r["id"] in ok for r in rows):
                # held whole with no placeholder to follow: the page and its rows in this read too
                listed = [r for r in rows if r["id"] in ok]
                page = self._kh_order(listed, sort_field, order, after, direction)[:limit + 1]
                out, read = await self._kh_flat_rows(org, aid, app_doc, page, sort_field, transaction, also=lambda t: self._kh_arrows_read(t, org, page, hidden, sets, gs, access, tops=chain_tops_apply(app_doc)),
                                                     under=under, tx=tx)
                u["done"] = (page, len(listed), self._kh_counts(listed), out, read)
            return u

        u = await self._q(unit, transaction)
        if u == "refused":
            return refused()
        if u == "not_ready":
            raise KnowledgeHubListingNotReady
        app_id, app_sk, app_doc, hidden, served, sets = (u[k] for k in ("app_id", "app_sk", "app_doc", "hidden", "served", "sets"))
        if grants_out is not None:
            grants_out[app_id] = granted
        if u["filtered"]:      # the start is admitted; the filters leave its connector out
            return self._kh_v3_empty_page(start_id, include_scope=include_scope, include_total=include_total, admitted=True)
        where, links, p, trail, rows, opened = u["where"], u["links"], u["p"], u["trail"], u["rows"], u["opened"]
        gset = set(granted)
        admitted_of = self._kh_admitted_of(sets, gset, self._kh_admitter(org, access, transaction, served=served))
        tops = self._kh_tops_under(org, app_id, access, gset, transaction, apply=chain_tops_apply(app_doc))
        lead = LeadBudget()      # the request's placeholder walks: its page, its total and its arrows share one budget
        arrows = (hidden, admitted_of, org, transaction, tops, lead)      # what deciding a placeholder and an arrow reads
        on: dict[int, bool] = {}

        async def keep(rows: list[Any]) -> list[Any]:
            """The sidebar's rows (``only_containers``): the groups and the folders, and a record only where its arrow is on, as the page's arrows decide it."""
            ask = [r for r in rows if r["kind"] == 3 and not r["folder"] and r["sk"] not in on]
            if ask:
                read = await self._kq(lambda tx: self._kh_arrows_read(tx, org, ask, hidden, sets, gset, access, tops=chain_tops_apply(app_doc)), transaction)
                lit = await self._kh_arrows(read, ask, *arrows)
                on.update((r["sk"], r["sk"] in lit) for r in ask)
            return [r for r in rows if r["kind"] != 3 or r["folder"] or on[r["sk"]]]
        walk = (*arrows, keep if side else None)      # what listing a child reads (``_kh_listed``)
        resume, capped, out, read = None, False, None, None
        if u["done"] is not None:
            page, total, counts, out, read = u["done"]
        elif rows is not None and not u["windowed"]:
            ordered = self._kh_order(rows, sort_field, order, after, direction)
            listed, n = await self._kh_listed(ordered, *walk, ok=opened)
            page = listed[:limit + 1]
            if len(page) <= limit and n < len(ordered):      # the walks stopped before a placeholder: the page ends there, and the next starts there
                resume = _edge(ordered[n - 1], sort_field)
            every, m = await self._kh_listed(self._kh_order(rows, sort_field, order, None, "next"), *walk, ok=opened)
            total, counts, capped = len(every), self._kh_counts(every), m < len(rows)
        elif rows is not None:      # held, its rows asked of the check a window at a time in the page's order; the total as for a wide node
            page, resume = await self._kh_held_page(rows, limit, sort_field, order, after, direction, walk)
            total, counts, capped = (await self._kh_browse_total(p, where, links, sets, gset, walk)) if include_total else (0, dict(NO_COUNTS), False)
        elif sort_field not in ("name", "updatedAt"):      # the children of a wide node are read in index order for these two sorts only
            raise KnowledgeHubListingTooLarge
        else:
            page, resume = await self._kh_browse_wide(p, where, links, limit, sort_field, order, after, direction, walk)
            total, counts, capped = (await self._kh_browse_total(p, where, links, sets, gset, walk)) if include_total else (0, dict(NO_COUNTS), False)
        if out is None:      # the rows and their arrows in one read
            out, read = await self._kh_flat_rows(org, app_id, app_doc, page, sort_field, transaction, also=lambda tx: self._kh_arrows_read(tx, org, page, hidden, sets, gset, access,
                                                                                                                  tops=chain_tops_apply(app_doc)),
                                                 under=u["under"])
        lists = await self._kh_arrows(read, page, *arrows)
        for row, c in zip(out, page, strict=True):
            row["hasChildren"] = c["sk"] in lists
        payload: dict[str, Any] = {"rows": out, "total": total if include_total else None, "nRecord": counts["record"], "nFolder": counts["folder"], "nGroup": counts["recordGroup"]}
        if at_app:      # the App's own breadcrumb, from the document already read
            payload["crumbNodes"], payload["crumbEdges"] = [self._kh_app_crumb(app_id, app_doc)], []
        elif trail:
            payload["crumbNodes"], payload["crumbEdges"] = self._kh_browse_scope(app_id, app_doc, u["start_sk"], trail, opened, gated)
        result = await self._kh_v3_page_result(
            payload, limit=limit, direction=direction, include_total=include_total, include_scope=include_scope, scoped_start=True, start_id=start_id, app_id=app_id,
            via_parent_id=via_parent_id, transaction=transaction, org_id=org_id, access=access, listed_under=start_id, name_parents=name_parents)
        if capped:
            result["totalCapped"] = True
        if resume is not None:      # a wide node's scan stopped at a bound before it had a page: the next page starts there
            result["hasMore"] = True
            result["resume"] = resume
        return result

    @staticmethod
    def _kh_app_crumb(app_id: str, app_doc: dict) -> dict[str, Any]:
        """The App's breadcrumb node, as ``_kh_v3_app_scope`` builds it."""
        return {"id": app_id, "name": app_doc.get("name") or "", "nodeType": "app", "subType": app_doc.get("type"), "admitted": True, "isInternal": False, "ownGroups": []}

    @staticmethod
    def _kh_counts(rows: list[Any]) -> dict[str, int]:
        counts = dict(NO_COUNTS)
        for r in rows:
            counts["recordGroup" if r["kind"] == 2 else "folder" if r["folder"] else "record"] += 1
        return counts

    async def _kh_listed(self, rows: list[Any], hidden: list[int], admitted_of: Callable[[list[Any]], Awaitable[set[str]]], org: str, transaction: str | None,
                         tops: Callable[[list[Any]], Awaitable[set[int]]] | None, budget: LeadBudget, keep: Callable[[list[Any]], Awaitable[list[Any]]] | None = None,
                         ok: set[str] | None = None) -> tuple[list[Any], int]:
        """Of children in the caller's order, those a browse lists: the ones the viewer may open (``ok`` when already asked), a placeholder only when it leads somewhere
        (:meth:`_kh_leads`), and of those the ones ``keep`` keeps (the sidebar's); and how many of ``rows`` are decided, a prefix that ends before the first placeholder the request's
        budget left undecided (the caller resumes there)."""
        ok = await admitted_of(rows) if ok is None else ok
        stubs = [r for r in rows if r["placeholder"] and r["id"] in ok]
        led, undecided = await self._kh_leads(stubs, hidden, admitted_of, org, transaction, tops=tops, budget=budget) if stubs else (set(), set())
        n = next((i for i, r in enumerate(rows) if r["sk"] in undecided and r["placeholder"] and r["id"] in ok), len(rows))
        listed = [r for r in rows[:n] if r["id"] in ok and (not r["placeholder"] or r["sk"] in led)]
        return (await keep(listed) if keep else listed), n

    async def _kh_arrows(self, read: tuple | None, rows: list[Any], hidden: list[int], admitted_of: Callable[[list[Any]], Awaitable[set[str]]], org: str, transaction: str | None,
                         tops: Callable[[list[Any]], Awaitable[set[int]]] | None, budget: LeadBudget) -> set[int]:
        """Of ``rows``, the sks whose arrow is on (opening the row lists something), from their :meth:`_kh_arrows_read`: the one rule for a page's arrows and its sidebar."""
        lists = await self._kh_lists(read, hidden, admitted_of, org, transaction, tops, budget) if read else set()
        return lists | await self._kh_held_lists(rows, read, lists, tops)

    @staticmethod
    def _kh_order(listed: list[Any], sort_field: str, order: str, after: dict[str, Any] | None, direction: str) -> list[Any]:
        """Children held whole, in the listing's order (``kgl.order``) past the keyset boundary, nearest the boundary first."""
        _, order_col = kgl.SORTS[sort_field]

        def cmp(a: Any, b: Any) -> int:  # noqa: ANN401
            va, vb = a[order_col], b[order_col]
            if (va is None) != (vb is None):
                return 1 if va is None else -1
            if va != vb:
                return (-1 if va < vb else 1) * (-1 if order == "DESC" else 1)
            ia, ib = a["id"].encode(), b["id"].encode()      # COLLATE "C": by the bytes of the id
            return (ia > ib) - (ia < ib)
        rows = sorted(listed, key=functools.cmp_to_key(cmp))
        if after is not None:
            bk = after.get("sortKey")
            edge = {order_col: None if int(after["nullRank"]) == 1 else (bk.encode("utf-16-be", "surrogatepass") if sort_field == "name" and isinstance(bk, str) else bk), "id": str(after["id"])}
            rows = [r for r in rows if (cmp(r, edge) > 0 if direction == "next" else cmp(r, edge) < 0)]
        return rows if direction == "next" else list(reversed(rows))

    async def _kh_held_page(self, rows: list[Any], limit: int, sort_field: str, order: str, after: dict[str, Any] | None, direction: str, walk: tuple) -> tuple[list[Any], dict[str, Any] | None]:
        """The page of children held whole that the check has to be asked about: in the page's order, a window at a time, as the flat listing reads a connector kg does not serve."""
        ordered = self._kh_order(rows, sort_field, order, after, direction)
        need, kept, at, before = limit + 1, [], 0, after
        window, t0 = min(WINDOW_MAX, max(need * 4, 100)), time.monotonic()
        while at < len(ordered) and len(kept) < need:
            got = ordered[at:at + window]
            at += len(got)
            listed, n = await self._kh_listed(got, *walk)
            kept += listed[:need - len(kept)]
            if len(kept) < need and n < len(got):      # the walks stopped before a placeholder: resume there (where the window began, if they decided none of it)
                return kept, _edge(got[n - 1], sort_field) if n else before
            before = _edge(got[-1], sort_field)
            if len(kept) < need and at < len(ordered) and time.monotonic() - t0 > BUDGET_S:
                return kept, _edge(got[-1], sort_field)
            window = min(WINDOW_MAX, window * 8)
        return kept, None

    async def _kh_browse_wide(self, p: kgl.Params, where: str, links: str | None, limit: int, sort_field: str, order: str, after: dict[str, Any] | None, direction: str,
                              walk: tuple) -> tuple[list[Any], dict[str, Any] | None]:
        """The page of a node too wide to hold: its children read in windows of the children index of the sort, as the flat listing reads a connector."""
        need, kept, boundary, scanned = limit + 1, [], after, 0
        window, t0 = min(WINDOW_MAX, max(need * 4, 100)), time.monotonic()
        transaction = walk[3]
        while True:
            q = kgl.Params(list(p.values))
            q.values[2] = window
            branches = kgl.after(sort_field, order, direction, boundary, q) if boundary is not None else None
            sql = kgl.page_sql(sort_field, order, direction, where, branches, None, kgl.browse_scopes(links))
            got = list(await self._kq(lambda tx, s=sql, v=tuple(q.values): tx.fetch(s, *v), transaction))
            listed, n = await self._kh_listed(got, *walk)
            kept += listed[:need - len(kept)]
            if len(kept) < need and n < len(got):      # the walks stopped before a placeholder: resume there (where the window began, if they decided none of it)
                return kept, _edge(got[n - 1], sort_field) if n else boundary
            if len(got) < window or len(kept) >= need:
                return kept, None
            scanned += len(got)
            boundary = _edge(got[-1], sort_field)
            if scanned >= MAX_SCANNED or time.monotonic() - t0 > BUDGET_S:
                return kept, boundary
            window = min(WINDOW_MAX, window * 8)

    async def _kh_browse_total(self, p: kgl.Params, where: str, links: str | None, sets: dict[str, Any] | None, gset: set[str], walk: tuple) -> tuple[int, dict[str, int], bool]:
        """The total of a node too wide to hold, as the flat listing's: for a connector kg serves, the children its classes admit counted where they are (up to COUNT_CAP) and the few it does
        not decide asked of the check; otherwise (or with too many undecided) the listed ones among the first COUNT_SCAN children, a lower bound when there were more (or when the
        request's budget left a placeholder undecided)."""
        transaction = walk[3]
        if sets is not None and walk[6] is None:      # the classes cannot count the sidebar's records: each one's arrow decides it
            u = kgl.Params(list(p.values))
            u.values[2] = UNDECIDED_MAX + 1
            sql = kgl.browse_undecided_sql(where, u.add(sorted(sets["unvouched"])), u.add(sets["app_sk"]), links)
            left = list(await self._kq(lambda tx, s=sql, v=tuple(u.values): tx.fetch(s, *v), transaction))
            if len(left) <= UNDECIDED_MAX:
                c = kgl.Params(list(p.values))
                c.values[2] = COUNT_CAP + 1
                sql = kgl.browse_count_sql(where, c.add(sorted(sets["plain"])), c.add(sorted(sets["own"])), c.add(sorted(gset)), c.add(sorted(sets["unvouched"])),
                                           c.add(sets["app_sk"]), links)
                listed, n = await self._kh_listed(left, *walk)
                counts = self._kh_counts(listed)
                for r in await self._kq(lambda tx, s=sql, v=tuple(c.values): tx.fetch(s, *v), transaction):
                    counts["recordGroup" if r["kind"] == 2 else "folder" if r["folder"] else "record"] += r["n"]
                total = sum(counts.values())
                return min(total, COUNT_CAP), counts, total > COUNT_CAP or n < len(left)
        c = kgl.Params(list(p.values))
        c.values[2] = COUNT_SCAN + 1
        first = list(await self._kq(lambda tx, s=kgl.children_sql(where, links), v=tuple(c.values): tx.fetch(s, *v), transaction))
        counted, n = await self._kh_listed(first[:COUNT_SCAN], *walk)
        return len(counted), self._kh_counts(counted), len(first) > COUNT_SCAN or n < len(first[:COUNT_SCAN])


    @staticmethod
    async def _kh_browse_trail(tx: Tx, org: str, start: Any) -> tuple[dict[int, tuple[str, str]], list[tuple[int, int]], dict[int, list[int]], dict[tuple[str, str], dict], dict[int, Any]]:  # noqa: ANN401
        """What the breadcrumbs are made of: the start, its ancestors by the hierarchy and its own groups (and theirs), as their keys, hierarchy edges, own groups, documents and
        kg rows (for the access check)."""
        rows: dict[int, Any] = {}
        edges: list[tuple[int, int]] = []
        groups: dict[int, list[int]] = {}
        frontier = {start["sk"]}
        seen = set(frontier)
        for level in range(50):
            if not frontier:
                break
            nxt: set[int] = set()
            for r in [start] if level == 0 else await tx.fetch(kgl.BROWSE_UP, org, sorted(frontier)):      # the start's row is in hand
                rows[r["sk"]] = r
                if r["parent_sk"]:
                    edges.append((r["parent_sk"], r["sk"]))
                    nxt.add(r["parent_sk"])
                if r["group_sk"]:
                    groups.setdefault(r["sk"], []).append(r["group_sk"])
                    nxt.add(r["group_sk"])
            for r in await tx.fetch(LINKS, org, sorted(frontier)):
                if r["kind"] == 1:
                    edges.append((r["parent_sk"], r["child_sk"]))
                else:
                    groups.setdefault(r["child_sk"], []).append(r["parent_sk"])
                nxt.add(r["parent_sk"])
            frontier = nxt - seen
            seen |= frontier
        known = {r["sk"]: (CODE_COLL[r["coll"]], r["id"]) for r in await tx.fetch(KEYS, org, sorted(seen))}
        pairs = sorted(set(known.values()))
        docs = {(r["coll"], r["id"]): _doc(r["doc"]) for r in await tx.fetch(DOCS, org, [c for c, _ in pairs], [i for _, i in pairs])}
        return known, edges, groups, docs, rows

    @staticmethod
    def _kh_browse_scope(app_id: str, app_doc: dict, start_sk: int, trail: tuple, opened: set[str], gated: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The breadcrumb nodes and edges of the trail; an ancestor is admitted when the access check opened it, an App when it is the browsed connector or one the viewer is gated
        to (another App the trail reaches is never named)."""
        known, sk_edges, groups, docs, _ = trail
        ids = {sk: i for sk, (_, i) in known.items()}
        nodes = []
        for sk, (coll, node_id) in known.items():
            d = docs.get((coll, node_id)) or {}
            nodes.append({"id": node_id, "name": d.get("name") or d.get("recordName") or d.get("groupName"), "nodeType": _node_type(coll, d),
                          "subType": d.get("type") if coll == "apps" else d.get("groupType") if coll == "recordGroups" else d.get("recordType"), "isInternal": bool(d.get("isInternal")),
                          "admitted": sk == start_sk or (node_id in opened if coll != "apps" else node_id == app_id or node_id in gated), "ownGroups": [ids[g] for g in groups.get(sk, []) if g in ids]})
        if not any(n["id"] == app_id for n in nodes):
            nodes.append({"id": app_id, "name": app_doc.get("name"), "nodeType": "app", "subType": app_doc.get("type"), "isInternal": False, "admitted": True, "ownGroups": []})
        edges = [{"parentId": ids[a], "childId": ids[b], "lists": True} for a, b in sorted(set(sk_edges)) if a in ids and b in ids]
        return nodes, edges

    # The chain-top walk (``GraphDBProvider._kh_v3_chain_tops``) reads these three; the rule that places what they return is the one every store shares.
    async def _kh_v3_chain_top_groups(self, app_id: str, org_id: str, granted_ids: list[str], only_group: str | None, transaction: str | None) -> list[dict[str, Any]]:
        org = current_org_id()
        if org_id != org or not granted_ids:
            return []

        async def read(tx: Tx) -> list[dict[str, Any]]:
            app = await tx.fetchval("/* pipeshub:kg.chain_app */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2", org, app_id)
            if app is None or not chain_tops_apply(_doc(app)):
                return []
            rows = await tx.fetch(kgl.CHAIN_GRANTED, org, sorted(set(granted_ids)), app_id)
            nodes = await _chain_nodes(tx, org, rows)
            by: dict[tuple[str, ...], list[str]] = {}
            for n in nodes.values():
                own = tuple(g for g, _ in n["own"])
                if only_group is None or (n["kind"] == 3 and (only_group in own if only_group != ANY_GROUP else bool(own))):
                    by.setdefault(own, []).append(n["id"])
            return [{"ownGroups": list(g), "ids": ids} for g, ids in by.items()]
        return await self._kq(read, transaction)

    async def _kh_v3_chain_top_candidates(self, app_id: str, node_ids: list[str], transaction: str | None) -> list[dict[str, Any]]:
        org = current_org_id()

        async def read(tx: Tx) -> list[dict[str, Any]]:
            nodes = await _chain_nodes(tx, org, await tx.fetch(kgl.CHAIN_ROWS, org, sorted(set(node_ids))))
            return [{"id": n["id"], "handle": n["id"], "sk": n["sk"], "parents": n["parents"], "ownGroups": [{"id": g, "deleted": d} for g, d in n["own"]]} for n in nodes.values()]
        return await self._kq(read, transaction) if node_ids else []

    async def _kh_v3_chain_top_facts(self, app_id: str, node_ids: list[str], group_ids: list[str], transaction: str | None) -> dict[str, set[str]]:
        org = current_org_id()

        async def read(tx: Tx) -> dict[str, set[str]]:
            app_sk = await tx.fetchval(kgl.APP_KEY, org, app_id)
            if app_sk is None:
                return {"hidden": set(node_ids), "underApp": set()}
            hidden: set[int] = set(await tx.fetchval(kgl.HIDDEN, org, app_sk)) if await tx.fetchval(kgl.HAS_HIDER, org, app_sk) else set()
            nodes = {r["id"]: r["sk"] for r in await tx.fetch(kgl.CHAIN_ROWS, org, sorted({*node_ids, *group_ids}))}
            # up the hierarchy from the groups (the primary parent and the hierarchy links), as far as the check's walk goes: the groups that reach the App
            starts = {nodes[g] for g in group_ids if g in nodes}
            up: dict[int, set[int]] = {}
            frontier, seen = set(starts), set(starts)
            for _ in range(access_sql.MAX_HOPS):
                if not frontier:
                    break
                for r in await tx.fetch(kgl.CHAIN_BY_SK, org, sorted(frontier)):
                    if r["parent_sk"] is not None:
                        up.setdefault(r["sk"], set()).add(r["parent_sk"])
                for r in await tx.fetch(LINKS, org, sorted(frontier)):
                    if r["kind"] == 1:
                        up.setdefault(r["child_sk"], set()).add(r["parent_sk"])
                frontier = {p for sk in frontier for p in up.get(sk, ())} - seen - {app_sk}
                seen |= frontier

            def reaches(sk: int) -> bool:
                level, met = {sk}, {sk}
                for _ in range(access_sql.MAX_HOPS):
                    level = {p for x in level for p in up.get(x, ())} - met
                    if app_sk in level:
                        return True
                    met |= level
                return False
            return {"hidden": {i for i, sk in nodes.items() if sk in hidden and i in set(node_ids)},
                    "underApp": {g for g in group_ids if g in nodes and reaches(nodes[g])}}
        return await self._kq(read, transaction)


async def _chain_nodes(tx: Tx, org: str, rows: list[Any]) -> dict[int, dict[str, Any]]:
    """Rows of kg.records (``sk, id, kind, parent_sk, group_sk``) with their hierarchy parents (ids, the App included) and their own groups (``(id, deleted)``): a record's
    groups (its group and its group links), a group's parent groups."""
    if not rows:
        return {}
    links = await tx.fetch(LINKS, org, [r["sk"] for r in rows])
    hier = {r["sk"]: [s for s in [r["parent_sk"]] if s is not None] for r in rows}
    groups = {r["sk"]: [s for s in [r["group_sk"]] if s is not None] for r in rows}
    for lk in links:
        (hier if lk["kind"] == 1 else groups)[lk["child_sk"]].append(lk["parent_sk"])
    refs = sorted({s for d in (hier, groups) for v in d.values() for s in v})
    keys = {r["sk"]: (r["coll"], r["id"]) for r in await tx.fetch(KEYS, org, refs)} if refs else {}
    deleted = {r["sk"]: r["deleted"] for r in await tx.fetch(kgl.CHAIN_BY_SK, org, [s for s in refs if keys.get(s, (0,))[0] == 2])} if refs else {}
    out = {}
    for r in rows:
        own = hier[r["sk"]] if r["kind"] == 2 else groups[r["sk"]]
        out[r["sk"]] = {"sk": r["sk"], "id": r["id"], "kind": r["kind"], "parents": [keys[s][1] for s in hier[r["sk"]] if s in keys],
                        "own": [(keys[s][1], bool(deleted.get(s))) for s in dict.fromkeys(own) if s in keys and keys[s][0] == 2 and s != r["sk"]]}
    return out


def _edge(row: Any, sort_field: str) -> dict[str, Any]:  # noqa: ANN401
    """The keyset position of a row: where a page that stops after it resumes."""
    value_col, order_col = kgl.SORTS[sort_field]
    return {"nullRank": 1 if row[order_col] is None else 0, "sortKey": row[value_col], "id": row["id"]}

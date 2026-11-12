"""The knowledge hub's connector listing on PostgreSQL: All Records (the flat listing of one connector) read from the typed graph.

The structure of a page (which rows, their order, the keyset) comes from ``kg.records``; who may see a row is asked of the access check for every row a page keeps, so an answer is never
older than the check (kg classes for a connector kg serves, the live walk otherwise). A listing therefore works while a connector syncs: structure may lag the drain, access never does.
A connector with no copy in kg yet is not listed as empty: it is refused as not ready.

A page scans windows of the sort order until it has the rows asked for, the end, or a bound (time or rows looked at). At a bound it hands back the position it reached (``resume``), so that
a page with no rows can still be continued. The total counts the rows the check admits among the first :data:`COUNT_SCAN` candidates: exact when that was all of them, else a lower bound
(``totalCapped``) that never includes a row the viewer may not read.
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.core.org_context import current_or_system, current_org_id
from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingNotReady,
    KnowledgeHubListingTooLarge,
)
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import NotImplementedInPostgres, PgBase, _doc
from app.services.graph_db.postgres.kg import drainer
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.model import CODE_COLL, CODE_EPOCH

if TYPE_CHECKING:
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
ARROW_CHILDREN = 200         # the children of a row asked about, for a connector kg does not serve (a sample: a row whose only readable child is further down shows no arrow until the connector is served again)

CHILDREN = """/* pipeshub:kg.listing_children */ SELECT p.sk AS parent, c.id FROM unnest($2::bigint[]) AS p(sk) CROSS JOIN LATERAL (
    SELECT x.id FROM (
      (SELECT r.id FROM kg.records r WHERE r.org_id = $1 AND r.parent_sk = p.sk AND r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND r.sk <> ALL($4::bigint[]) LIMIT $3)
      UNION ALL (SELECT r.id FROM kg.records r WHERE r.org_id = $1 AND r.group_sk = p.sk AND r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND r.sk <> ALL($4::bigint[]) LIMIT $3)
      UNION ALL (SELECT r.id FROM kg.records r WHERE r.org_id = $1 AND r.sk = ANY (ARRAY(SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = p.sk LIMIT $3))
                 AND r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND r.sk <> ALL($4::bigint[]) LIMIT $3)) x LIMIT $3) c"""
LINKS = "/* pipeshub:kg.listing_links */ SELECT child_sk, parent_sk, kind FROM kg.links WHERE org_id = $1 AND child_sk = ANY($2::bigint[])"
KEYS = "/* pipeshub:kg.listing_keys */ SELECT sk, coll, id FROM kg.node_keys WHERE org_id = $1 AND sk = ANY($2::bigint[])"
DOCS = """/* pipeshub:kg.listing_docs */ SELECT n.coll, n.id, n.doc FROM unnest($2::text[], $3::text[]) AS q(coll, id)
    JOIN graph.nodes n ON n.org_id = $1 AND n.coll = q.coll AND n.id = q.id"""
FILES = """/* pipeshub:kg.listing_files */ SELECT e.from_id, f.doc->>'extension' AS ext, f.doc->>'sizeInBytes' AS size FROM graph.edges e
    JOIN graph.nodes f ON f.org_id = e.org_id AND f.coll = 'files' AND f.id = e.to_id WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_coll = 'records' AND e.from_id = ANY($2::text[])"""
APP = "/* pipeshub:kg.listing_app_doc */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2"


def _node_type(coll: str, doc: dict) -> str:
    if coll == "recordGroups":
        return "recordGroup"
    if coll == "apps":
        return "app"
    return "folder" if doc.get("mimeType") in FOLDER_MIME_TYPES or str(doc.get("recordType") or "").upper() == "FOLDER" else "record"


class KhListingMixin(PgBase):
    async def _kq(self, fn: Any, transaction: str | None) -> Any:  # noqa: ANN401
        """A read unit of the listing: custom plans (a statement that carries arrays of ids is planned for them, not generically after the fifth run), in the caller's explicit transaction when it has one."""
        if transaction:
            return await self._q(fn, transaction)
        return await self._db.run(fn, jobs=current_or_system().kind == "system", custom_plan=True)

    async def get_knowledge_hub_connector_page_v3(
        self, app_id: str, org_id: str, grantee_ids: list[str], gated_app_ids: list[str], granted_ids: list[str] | None = None, limit: int = 50, *, flatten: bool = True,
        sort_field: str = "name", sort_dir: str = "ASC", after: dict[str, Any] | None = None, direction: str = "next", filters: dict[str, Any] | None = None, include_total: bool = True,
        start_id: str | None = None, start_type: str = "app", grants_by_connector: dict[str, list[str]] | None = None, include_scope: bool = False, via_parent_id: str | None = None,
        transaction: str | None = None, name_parents: bool = True, user_key: str | None = None, grants_out: dict[str, list[str]] | None = None, max_depth: int = 50,
    ) -> dict[str, Any]:
        """One page of the flat listing of a connector (``flatten`` from the app). Browsing a node and a depth bound are not served here yet."""
        if direction not in ("next", "prev"):
            raise ValueError(f"Unsupported page direction: {direction!r}.")
        order = (sort_dir or "").strip().upper()
        if order not in ("ASC", "DESC"):
            raise ValueError(f"Unsupported sort order: {sort_dir!r}. Use 'asc' or 'desc'.")
        if sort_field not in kgl.SORTS:
            raise ValueError(f"Unsupported sort field: {sort_field!r}. Allowed: {sorted(kgl.SORTS)}")
        start_id = start_id or app_id
        if not (flatten and (start_type in ("app", "kb") or start_id == app_id)) or max_depth != 50:
            raise NotImplementedInPostgres("PostgresGraphProvider.get_knowledge_hub_connector_page_v3 lists a whole connector only")
        org = current_org_id()
        filters = filters or {}
        if org_id != org or app_id not in set(gated_app_ids):
            return self._kh_v3_empty_page(start_id, include_scope=include_scope, include_total=include_total, admitted=False)
        if grants_by_connector is not None:
            granted = list(grants_by_connector.get(app_id) or [])
        elif granted_ids is None and user_key:
            granted = list(await self.get_knowledge_hub_connector_grants(user_key, org_id, app_id, transaction=transaction))
        else:
            granted = list(granted_ids or [])
        if grants_out is not None:
            grants_out[app_id] = granted
        access = {"grantee_ids": grantee_ids, "gated_app_ids": gated_app_ids, "by_connector": {app_id: granted}}

        async def head(tx: Tx) -> tuple[int | None, str, dict, list[int], bool]:
            k = await tx.fetchrow(kgl.STATE, org, app_id)
            doc = await tx.fetchrow(APP, org, app_id)
            hidden: list[int] = []
            ready = bool(k and k["mode"] != "legacy" and k["code_epoch"] == CODE_EPOCH and k["verified_at"] is not None)      # a copy of an older code lacks the listing columns: it is not listed from
            if ready and await tx.fetchval(kgl.HAS_HIDER, org, k["sk"]):
                hidden = list(await tx.fetchval(kgl.HIDDEN, org, k["sk"]))
                if len(hidden) > HIDDEN_MAX:
                    raise KnowledgeHubListingTooLarge
            served = ready and await drainer.serves(tx, org, app_id)
            return (k["sk"] if ready else None), (k["mode"] if k else "legacy"), (_doc(doc["doc"]) if doc else {}), hidden, served
        app_sk, mode, app_doc, hidden, served = await self._kq(head, transaction)
        if app_sk is None:
            raise KnowledgeHubListingNotReady
        app_origin = "COLLECTION" if app_doc.get("type") == "KB" else "CONNECTOR"
        if (filters.get("connector_ids") and app_id not in filters["connector_ids"]) or (filters.get("origins") and app_origin not in filters["origins"]):
            return self._kh_v3_empty_page(start_id, include_scope=include_scope, include_total=include_total, admitted=True)

        base = kgl.Params([org, app_sk, None])
        where = kgl.filters_sql(filters, base)
        if app_origin == "COLLECTION" and (groups := filters.get("record_group_ids")):
            # As the other stores do: the list narrows a collection's record groups only; records and folders pass.
            where += f" AND (r.kind <> 2 OR r.id = ANY({base.add(list(groups))}::text[]))"
        if hidden:
            where += f" AND r.sk <> ALL({base.add(hidden)}::bigint[])"

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

        viewer = await self._kh_flat_viewer(org, app_id, app_sk, where, base, admit, granted, transaction)
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
        rows = await self._kh_flat_rows(org, app_id, app_doc, kept[:need], sort_field, access, viewer, hidden, transaction)
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

    async def _kh_flat_viewer(self, org: str, app_id: str, app_sk: int, where: str, base: kgl.Params, admit: Any, granted: list[str], transaction: str | None) -> dict[str, Any] | None:  # noqa: ANN401
        """What the viewer's grants open of a connector kg serves, by class: the classes met with no grant of the row's own (``plain``), the classes that need the row's own grant (``own``), the classes
        kg cannot vouch for (``unvouched``) and the few rows no class decides, with the ones among them the access check admits (``walked``). None when the connector is not served or too many rows are
        left to the check: the rows are then asked of the access check one window at a time."""
        async def classes(tx: Tx) -> dict[int, list[list[Any]]] | None:
            if not await drainer.serves(tx, org, app_id):
                return None
            rows = await tx.fetch(kgl.CLASSES, org, app_sk, CLASS_MAX + 1)
            if len(rows) > CLASS_MAX:
                return None
            return {r["id"]: (json.loads(r["clauses"]) if isinstance(r["clauses"], str) else list(r["clauses"])) for r in rows}
        found = await self._kq(classes, transaction)
        if found is None:
            return None
        verdicts = await self._kq(lambda tx: kg_read.class_verdicts(tx, org, found, set(granted)), transaction)
        plain = sorted(c for c, v in verdicts.items() if v and v[0])
        own = sorted(c for c, v in verdicts.items() if v and v[1] and not v[0])
        unvouched = sorted(c for c, v in verdicts.items() if v is None)
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

    async def _kh_flat_rows(self, org: str, app_id: str, app_doc: dict, cands: list[Any], sort_field: str, access: dict[str, Any], viewer: dict[str, Any] | None, hidden: list[int], transaction: str | None) -> list[dict[str, Any]]:
        """The rows of a page as the listing returns them, from the documents of the page's nodes."""
        if not cands:
            return []
        col, order_col = kgl.SORTS[sort_field]
        sks = [c["sk"] for c in cands]
        sks_all = sorted({*sks, *(c["parent_sk"] for c in cands if c["parent_sk"]), *(c["group_sk"] for c in cands if c["group_sk"])})

        async def read(tx: Tx) -> tuple:
            links = await tx.fetch(LINKS, org, sks)
            extra = sorted({*sks_all, *(r["parent_sk"] for r in links)})
            keys = {r["sk"]: (CODE_COLL[r["coll"]], r["id"]) for r in await tx.fetch(KEYS, org, extra)}
            pairs = sorted(set(keys.values()))
            docs = {(r["coll"], r["id"]): _doc(r["doc"]) for r in await tx.fetch(DOCS, org, [c for c, _ in pairs], [i for _, i in pairs])}
            files = {r["from_id"]: r for r in await tx.fetch(FILES, org, [c["id"] for c in cands if c["kind"] == 3])}
            containers = [c["sk"] for c in cands if (c["kind"] == 2 and not c["hide_children"]) or c["folder"]]
            kids: dict[int, list[str]] = {}
            lists: set[int] = set()
            if containers and viewer is not None:      # a connector kg serves: exact, by the classes, with no sample of the children
                q = kgl.Params([org, containers, hidden])
                lists = {r["sk"] for r in await tx.fetch(kgl.arrows_sql(kgl.class_admit("c", q, viewer)), *q.values)}
            elif containers:
                for r in await tx.fetch(CHILDREN, org, containers, ARROW_CHILDREN, hidden):
                    kids.setdefault(r["parent"], []).append(r["id"])
            return links, keys, docs, files, kids, lists
        links, keys, docs, files, kids, lists = await self._kq(read, transaction)
        up: dict[int, list[tuple[int, int]]] = {}
        for lk in links:
            up.setdefault(lk["child_sk"], []).append((lk["parent_sk"], lk["kind"]))
        opened: set[str] = set()
        every = sorted({i for ids in kids.values() for i in ids})
        for i in range(0, len(every), ADMIT_CHUNK):
            opened |= {r["id"] for r in await self._kh_v3_accessible_rows("", org, every[i:i + ADMIT_CHUNK], [], access=access, transaction=transaction)}

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
            options = [o for o in (*(option(p, own=False) for p in hier if p), *(option(g, own=True) for g in grp if g)) if o]
            options.append({"id": app_id, "name": app_doc.get("name"), "nodeType": "app", "isInternal": False})
            key = c[col]
            out.append({
                "id": c["id"], "name": d.get("name") or d.get("recordName") or d.get("groupName"), "nodeType": _node_type(coll, d), "parentId": None, "parentType": None,
                "parentName": None, "parentIsInternal": False, "origin": "COLLECTION" if app_doc.get("type") == "KB" else "CONNECTOR", "connector": d.get("connector") or app_doc.get("type"),
                "connectorId": d.get("connectorId") or app_id, "recordType": d.get("recordType"), "recordGroupType": d.get("groupType"), "indexingStatus": d.get("indexingStatus"),
                "reason": d.get("reason"), "embedDone": d.get("embedDone"), "embedTotal": d.get("embedTotal"), "createdAt": c["created_ms"], "updatedAt": c["updated_ms"],
                "sizeInBytes": d.get("sizeInBytes") if d.get("sizeInBytes") is not None else (int(f["size"]) if f and f["size"] not in (None, "") else None),
                "mimeType": d.get("mimeType"), "extension": f["ext"] if f else d.get("extension"), "webUrl": d.get("webUrl"), "sharingStatus": d.get("sharingStatus"),
                "isInternal": bool(d.get("isInternal")), "isPlaceholder": False, "hasChildren": c["sk"] in lists or any(i in opened for i in kids.get(c["sk"], [])), "userRole": None,
                "sortKey": key, "nullRank": 1 if c[order_col] is None else 0, "parentOptions": options})
        return out

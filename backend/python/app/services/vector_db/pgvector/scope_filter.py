"""The scope filter (:class:`~app.services.vector_db.filters.ScopeFilter`) as one SQL condition over ``retrieval.point_groups``.

The condition is ANDed with everything else a search already filters on (the membership arms, the narrowing list, the extra conditions),
never in place of them. It is a probe of the group's row by primary key, per candidate row, that admits a point only when its group's
stamp is trusted and the stamp admits the viewer; everything else is **not** admitted, so the conditions below fail closed:

* no group row, a group never stamped (NULL stamp, version, generation or lists), a ``legacy`` or unknown stamp, another field version
  or scope generation: not admitted (a NULL makes the comparison NULL, and ``EXISTS`` ... ``WHERE NULL`` finds no row);
* a group with no connector, or one connector outside ``current``: not admitted (``<@`` alone would admit an empty array);
* an empty scope list matches nothing (``&&`` with ``'{}'`` is false); a filter that can trust nothing (``ScopeFilter.trusts_nothing``)
  is the condition ``false``.

Every value is a bound parameter (``$n``); only the parameter numbers are spliced in. The org and collection are the statement's own
``$1`` and ``$2``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.vector_db.filters import ScopeFilter

#: The parameters of the condition, in order: current connectors, open apps, readable groups, reach, excluded scopes, pending scopes, blocked
#: connectors (``text[]``), the field version and the scope generation (``int``).
N_PARAMS = 9
_NOTHING = " AND false"


def predicate(flt: ScopeFilter, first_param: int) -> tuple[str, list[object]]:
    """``(" AND EXISTS (...)", parameters)`` numbered from ``first_param``, or ``(" AND false", [])`` for a filter that trusts nothing."""
    if flt.trusts_nothing:
        return _NOTHING, []
    cur, opn, grp, reach, exc, pen, blk, ver, gen = (f"${first_param + i}" for i in range(N_PARAMS))
    sql = (
        " AND EXISTS (SELECT 1 FROM retrieval.point_groups sg "
        "WHERE sg.org_id = $1 AND sg.collection_id = $2 AND sg.group_key = p.group_key "
        f"AND sg.scope_version = {ver}::int AND sg.scope_generation = {gen}::int "
        f"AND cardinality(sg.connector_ids) > 0 AND sg.connector_ids <@ {cur}::text[] "
        # a subtree being rewritten is hidden whatever the stamp (a missing path names no pending scope)
        f"AND NOT coalesce(sg.path_ids && {pen}::text[], false) AND ("
        # a record group or root group the viewer reads, or a connector that opens everything in it: not for a connector with a pending root
        f"(sg.scope_stamp = 'container' AND NOT (sg.connector_ids && {blk}::text[]) "
        f"AND (sg.record_group_ids && {grp}::text[] OR sg.root_record_group_ids && {grp}::text[] OR sg.connector_ids && {opn}::text[])) "
        # the record's own id or a seed on its chain (a container point of a blocked connector is read with the legacy filter)
        f"OR (sg.scope_stamp IN ('container', 'union', 'intersection', 'override') AND sg.acl_scope && {reach}::text[] "
        f"AND (sg.scope_stamp <> 'container' OR NOT (sg.connector_ids && {blk}::text[]))) "
        # a path that reaches something the viewer reads and passes through nothing it may not
        f"OR (sg.scope_stamp IN ('union', 'intersection') AND sg.path_ids && {reach}::text[] AND NOT (sg.path_ids && {exc}::text[]))"
        ") OFFSET 0)"
    )
    current, open_apps, groups, reach_ids, excluded, pending, blocked = flt.lists()
    return sql, [current, open_apps, groups, reach_ids, excluded, pending, blocked, flt.version, flt.generation]

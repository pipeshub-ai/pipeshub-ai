"""Shared filter-building helpers for all vector DB providers.

All three providers (Qdrant, OpenSearch, Redis) share the same
``filter_collection`` argument-parsing logic.  This module extracts that
common body so each provider only overrides the one step that differs
(provider-specific ``build_conditions`` + optional ``min_should_match``
rejection).

Typical provider usage::

    from app.services.vector_db.filters import build_filter_expression

    async def filter_collection(self, filter_mode=..., must=..., ...):
        return build_filter_expression(
            filter_mode,
            must=must, should=should, must_not=must_not,
            min_should_match=min_should_match,
            extra_kwargs=kwargs,
            build_conditions=MyUtils.build_conditions_generic,
        )
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Union

from app.services.permissions.scope_fields import SCOPE_KEYS
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.errors import OrgMismatch, OrgRequired
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.services.permissions.scopes import ViewerScopes

# Top-level payload fields — must not be auto-prefixed with ``metadata.``.
TOP_LEVEL_FILTER_FIELDS = frozenset(
    {CONNECTOR_IDS_FIELD, RECORD_GROUP_IDS_FIELD, ROOT_RECORD_GROUP_IDS_FIELD, *SCOPE_KEYS}
)


def canonical_filter_key(key: str) -> str:
    """Return the payload path used by vector filters.

    Chunk metadata lives under ``metadata.*``. VRID-level membership arrays
    (``connectorIds``, ``recordGroupIds``) are top-level siblings.
    """
    if key.startswith("metadata.") or key in TOP_LEVEL_FILTER_FIELDS:
        return key
    return f"metadata.{key}"


ORG_FIELD = "metadata.orgId"
_ORG_KEY = "orgId"      # the key inside ``metadata``; a read of a payload being checked, not a source of the request's org


def is_missing(value: object) -> bool:
    """None and blank strings stand for "no value" (``""`` is the default of several callers)."""
    return value is None or (isinstance(value, str) and not value.strip())


def org_for_filter(org_id: str | None = None, *, match_bound: bool = False) -> str:
    """The org a filter must pin: the explicit one, else the one bound in ``app.core.org_context``.

    Raises OrgRequired for a blank org or when no single org is bound (a system context included),
    never falls back to "every org". With ``match_bound`` an explicit org that differs from a bound
    one raises ``OrgMismatch``.
    """
    from app.core import org_context  # deferred: the context module pulls in telemetry

    if org_id is not None:
        if is_missing(org_id):
            raise OrgRequired("the organisation of this vector call is blank")
        if match_bound:
            try:
                bound = org_context.current_or_system().org_id
            except org_context.MissingOrgContext:
                bound = None
            if bound is not None and bound != org_id:
                raise OrgMismatch("the organisation of this vector call differs from the bound one")
        return org_id
    try:
        return org_context.current_org_id()
    except (org_context.MissingOrgContext, org_context.SystemContextNotAllowed) as exc:
        raise OrgRequired(f"no organisation is bound for this vector call: {exc}") from exc


def require_filter_org(expr: FilterExpression) -> str:
    """Raise OrgRequired unless MUST pins ``metadata.orgId`` to a non-blank value.

    Every delete and update names its org, so a filter without one is a bug of the caller,
    not a request for every org. An org-only filter passes: that is the whole-org removal.
    """
    orgs: set = set()
    for cond in expr.must:
        if cond.key != ORG_FIELD:
            continue
        vals = cond.values if cond.values is not None else [cond.value]
        if len(vals) != 1 or is_missing(vals[0]):
            raise OrgRequired("the filter must pin metadata.orgId to exactly one non-blank value")
        orgs.add(vals[0])
    if not orgs:
        raise OrgRequired("the filter carries no organisation (metadata.orgId in MUST is required)")
    if len(orgs) > 1:
        raise OrgMismatch("the filter names more than one organisation")
    return next(iter(orgs))


def _org_in_payload(payload: dict) -> tuple[bool, object]:
    """Whether a payload names ``orgId`` (nested under ``metadata``, dotted, or as the flattened Redis field) and what it says."""
    meta = payload.get("metadata")
    if isinstance(meta, dict) and _ORG_KEY in meta:
        return True, meta[_ORG_KEY]
    for key in ("metadata.orgId", "metadata_orgId"):
        if key in payload:
            return True, payload[key]
    return False, None


def forbid_org_in_payload(payload: dict) -> None:
    """A payload merge must not change which org a point belongs to (the org is what every write filter pins)."""
    if _org_in_payload(payload)[0]:
        raise OrgMismatch("a payload update may not name orgId: it would move points to another organisation")


def keep_org_in_overwrite(payload: dict, expr: FilterExpression) -> None:
    """An overwrite replaces the whole payload, so it carries ``metadata.orgId``; it must be the org the filter pins."""
    named, value = _org_in_payload(payload)
    if named and value != require_filter_org(expr):
        raise OrgMismatch("an overwritten payload must keep the organisation of the points it replaces")


def build_max_values_conditions(max_values: Dict[str, int]) -> List[FieldCondition]:
    """Build array-length upper-bound conditions.

    Provider-independent: there is no value to match, only a count, so this
    does not go through a provider's ``build_conditions``.
    """
    return [
        FieldCondition(key=canonical_filter_key(key), values_count_lte=limit)
        for key, limit in max_values.items()
        if limit is not None
    ]


def split_values_count_conditions(
    expr: FilterExpression,
) -> tuple[FilterExpression, List[tuple[str, int]]]:
    """Peel array-length conditions off an expression.

    For providers with no server-side count (Redis): query with the returned
    expression, then apply the ``(key, upper_bound)`` pairs to the results. The
    remaining expression always matches a superset, never a subset, so
    post-filtering can only narrow it.
    """
    limits: List[tuple[str, int]] = []

    def _peel(conds: List[FieldCondition], clause: str) -> List[FieldCondition]:
        kept: List[FieldCondition] = []
        for cond in conds:
            if cond.values_count_lte is None:
                kept.append(cond)
                continue
            if clause != "must":
                # Callers apply the peeled bounds conjunctively, so a bound that
                # arrived under should/must_not would come back with its meaning
                # inverted — under must_not it would delete exactly the points it
                # was written to spare.
                raise ValueError(
                    f"values_count_lte on '{cond.key}' is only supported in the "
                    f"MUST clause, not {clause.upper()}"
                )
            limits.append((cond.key, cond.values_count_lte))
            # A condition carrying both a match and a count still has to
            # contribute its match to the query.
            if cond.value is not None or cond.values is not None:
                kept.append(
                    FieldCondition(key=cond.key, value=cond.value, values=cond.values)
                )
        return kept

    stripped = FilterExpression(
        must=_peel(expr.must, "must"),
        should=_peel(expr.should, "should"),
        must_not=_peel(expr.must_not, "must_not"),
        min_should_match=expr.min_should_match,
    )
    return stripped, limits


def build_filter_expression(
    filter_mode: Union[str, FilterMode] = FilterMode.MUST,
    *,
    must: Optional[Dict[str, FilterValue]] = None,
    should: Optional[Dict[str, FilterValue]] = None,
    must_not: Optional[Dict[str, FilterValue]] = None,
    min_should_match: Optional[int] = None,
    max_values: Optional[Dict[str, int]] = None,
    extra_kwargs: Optional[Dict[str, FilterValue]] = None,
    build_conditions: Callable[[Dict[str, FilterValue]], List[FieldCondition]],
) -> FilterExpression:
    """Parse filter_collection arguments and return a ``FilterExpression``.

    Parameters
    ----------
    filter_mode:
        Which clause the positional ``extra_kwargs`` belong to.
    must / should / must_not:
        Explicit per-clause condition dicts (passed as keyword args by caller).
    min_should_match:
        Minimum number of SHOULD clauses that must match.  Providers that do
        not support this should validate before calling this function and raise
        ``NotImplementedError``.
    max_values:
        ``{array_field: upper_bound}`` on how many values the field holds.
        Always added to MUST.
    extra_kwargs:
        Additional ``**kwargs`` forwarded from the caller (routed by filter_mode).
    build_conditions:
        Provider-specific callable that converts ``{key: value}`` dicts into
        ``List[FieldCondition]``.  Called once per non-empty clause dict.
    """
    if isinstance(filter_mode, str):
        try:
            filter_mode = FilterMode(filter_mode.lower())
        except ValueError:
            raise ValueError(
                f"Invalid mode '{filter_mode}'. Must be 'must', 'should', or 'must_not'"
            )

    all_must = dict(must) if must else {}
    all_should = dict(should) if should else {}
    all_must_not = dict(must_not) if must_not else {}

    if extra_kwargs:
        if filter_mode == FilterMode.MUST:
            all_must.update(extra_kwargs)
        elif filter_mode == FilterMode.SHOULD:
            all_should.update(extra_kwargs)
        elif filter_mode == FilterMode.MUST_NOT:
            all_must_not.update(extra_kwargs)

    # A blank org would be dropped by the condition builders and widen the filter to every org.
    for key in ("orgId", ORG_FIELD):
        if key in all_must and (
            is_missing(all_must[key])
            or (isinstance(all_must[key], (list, tuple)) and (len(all_must[key]) != 1 or is_missing(all_must[key][0])))
        ):
            raise OrgRequired(f"'{key}' in the filter must be exactly one non-blank org id")

    must_conds = build_conditions(all_must) if all_must else []
    should_conds = build_conditions(all_should) if all_should else []
    must_not_conds = build_conditions(all_must_not) if all_must_not else []

    # Always MUST: an array-length bound narrows a match, it never stands alone
    # as an alternative to one.
    if max_values:
        must_conds = must_conds + build_max_values_conditions(max_values)

    return FilterExpression(
        must=must_conds,
        should=should_conds,
        must_not=must_not_conds,
        min_should_match=min_should_match if should_conds else None,
    )


def build_generic_conditions(filters: dict[str, FilterValue]) -> list[FieldCondition]:
    """Backend-neutral ``FieldCondition`` list from a key -> value dict.

    Keys are canonicalised to ``metadata.x``; None and blank-string values are
    skipped. A list is kept even when empty (after dropping None): an empty list
    matches nothing, it is not the absence of a filter. Providers translate the
    conditions into their own query syntax.
    """
    conditions: list[FieldCondition] = []
    for key, value in filters.items():
        if value is None:
            continue
        field_key = canonical_filter_key(key)
        if isinstance(value, (list, tuple)):
            filtered = [v for v in value if v is not None]
            conditions.append(FieldCondition(key=field_key, values=filtered))
        elif isinstance(value, str) and not value.strip():
            continue
        else:
            conditions.append(FieldCondition(key=field_key, value=value))
    return conditions


#: Most ids one list of a ScopeFilter may hold; a viewer past it is not filtered by scope (`ScopeFilterTooLarge`): the caller keeps the
#: legacy filter for that viewer.
MAX_SCOPE_IDS = 100_000


class ScopeFilterTooLarge(ValueError):
    """A viewer's scope list is longer than a store can take in one query."""


class ScopeFilterUnsupported(ValueError):
    """A store cannot evaluate this filter exactly (Redis tags compare without case, so an id with capitals could meet another id)."""


@dataclass(frozen=True)
class ScopeFilter:
    """What one viewer may read, as a store-neutral predicate over the scope fields a point carries (ANDed with the request's filter).

    A point is admitted only by a **trusted** stamp: its group carries the current field version and the deployment's scope generation,
    and every connector it belongs to is in ``current`` (the connectors whose stamps may be used now). Then, by its stamp:

    * ``container``: a record group or root group in ``readable_groups``, or a connector in ``open_apps``;
    * ``union`` / ``intersection``: ``pathIds`` meets ``reach`` and does not meet ``excluded``;
    * any of them: ``aclScope`` (the record's own id and the seeds on its chain) meets ``reach``;
    * **whatever the stamp**, a point whose ``pathIds`` meet ``pending`` (the scopes whose subtree is being rewritten) is hidden, and a
      ``container`` point of a connector in ``blocked`` (a connector with a pending root) is not admitted by the stamp at all: a point with no
      path cannot be hidden by a path, so it is read with the legacy filter until the rewrite finishes.

    Every other point (never stamped, a ``legacy`` or unknown stamp, an untrusted one, a connector not in ``current``) is **not** admitted:
    an empty list admits nothing, never everything. ``reach`` is ``readable_groups | readable_scopes | open_apps``.
    """

    current: frozenset[str] = frozenset()
    open_apps: frozenset[str] = frozenset()
    readable_groups: frozenset[str] = frozenset()
    readable_scopes: frozenset[str] = frozenset()
    #: Restricted scopes the viewer cannot read: a ``union`` / ``intersection`` path through one hides the point.
    excluded: frozenset[str] = frozenset()
    #: The field version and scope generation a stamp must carry to be used (`ScopeFields.trusted`); a negative generation (the shared
    #: state could not be read) trusts nothing.
    version: int = 0
    generation: int = -1
    #: Scopes whose subtree is being rewritten (hide-first), and the connectors that have such a scope (see the class docstring). Last, so the
    #: positional order of the fields before them is unchanged.
    pending: frozenset[str] = frozenset()
    blocked: frozenset[str] = frozenset()

    @classmethod
    def of(cls, scopes: ViewerScopes, current_connectors: Iterable[str] | Mapping[str, int], *, version: int, generation: int,
           blocked: Iterable[str] = ()) -> ScopeFilter:
        return cls(frozenset(current_connectors), scopes.open_apps, scopes.readable_groups, scopes.readable_scopes,
                   scopes.unreadable_restricted, version, generation, scopes.pending, frozenset(blocked))

    @property
    def reach(self) -> frozenset[str]:
        return self.readable_groups | self.readable_scopes | self.open_apps

    @property
    def trusts_nothing(self) -> bool:
        return self.generation < 0 or not self.current

    def lists(self) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str], list[str]]:
        """``(current, open_apps, readable_groups, reach, excluded, pending, blocked)``, sorted, for a store that binds arrays. Computed once per
        filter: callers must not mutate it."""
        return self._lists

    @cached_property
    def _lists(self) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str], list[str]]:
        sets = (self.current, self.open_apps, self.readable_groups, self.reach, self.excluded, self.pending, self.blocked)
        if any(len(x) > MAX_SCOPE_IDS for x in sets):                                                     # refuse before paying for the sort
            raise ScopeFilterTooLarge(f"a scope list holds more than {MAX_SCOPE_IDS} ids")
        return tuple(sorted(x) for x in sets)  # type: ignore[return-value]

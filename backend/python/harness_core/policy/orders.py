"""The closed set of policy orders (R2 CP3, 02-lld §3.2), each implemented once per language.

Node's ``domain/policy/orders.ts`` is the twin; ``contracts/harness/vectors/policy-resolve.json`` holds the two
to one answer. ``leq(a, b)`` is "a is at or below b" (a may follow b); ``meet`` is the greatest value at or below
both, ``UNDEFINED`` where none exists (two unrelated modes). Null is unbounded (the top) in every order that
admits it. Values are JSON (the wire form of a layer).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast, override

from harness_core.contracts import canonical_json
from harness_core.effects.flow import FlowRule, covers, layer_rule, strictest_flows
from harness_core.effects.policy import ApprovalConfigInvalid, parse_rules, stricter

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalDecision, EffectKind


class _Undefined:
    @override
    def __repr__(self) -> str:
        return "UNDEFINED"


# No value: the meet of two unrelated values, or an admitted item not chosen yet.
UNDEFINED: Final = _Undefined()


class InvalidPolicyValue(ValueError):
    """A layer's value the order cannot read."""


@dataclass(frozen=True, slots=True)
class Widened:
    """Why a value is not at or below another; ``at`` names a member of a composite value."""

    detail: str
    at: str | None = None


@dataclass(frozen=True, slots=True)
class Order:
    kind: str
    lift: Callable[[object], object]
    leq: Callable[[object, object], Widened | None]
    narrow: Callable[[object, object], object]
    meet: Callable[[object, object], object]
    contains: Callable[[object, object], bool]


def _same(a: object, b: object) -> bool:
    return canonical_json(a) == canonical_json(b)


def _show(value: object) -> str:
    return value if isinstance(value, str) else canonical_json(value).decode()


def _names(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast("list[object]", value)):
        raise InvalidPolicyValue("not a list of names")
    return sorted(set(cast("list[str]", value)))


def _number(value: object) -> float | int | None:
    if value is None or (isinstance(value, int | float) and not isinstance(value, bool)):
        return value
    raise InvalidPolicyValue("not a number")


def _contained(leq: Callable[[object, object], Widened | None]) -> Callable[[object, object], bool]:
    return lambda ceiling, choice: leq(choice, ceiling) is None


def _first(a: object, _b: object) -> object:
    return a


def _min_leq(a: object, b: object) -> Widened | None:
    x, y = _number(a), _number(b)
    if y is None or (x is not None and x <= y):
        return None
    return Widened(f"{_show(x)} is above {_show(y)}")


def _min_meet(a: object, b: object) -> object:
    x, y = _number(a), _number(b)
    return y if x is None else x if y is None else min(x, y)


MIN: Final = Order("min", _number, _min_leq, _first, _min_meet, _contained(_min_leq))


# The key of a ``min-map`` that bounds every other key.
MIN_MAP_DEFAULT: Final = "default"
_MIN_MAP_KEY: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _min_map(value: object) -> dict[str, float | int] | None:
    """A ``min-map`` value in canonical form: an entry that does not lower its key's bound is dropped, an empty
    map is None (the top)."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidPolicyValue("not a map of numbers")
    entries = cast("Mapping[object, object]", value)
    for key, bound in entries.items():
        if not isinstance(key, str) or not _MIN_MAP_KEY.match(key):
            raise InvalidPolicyValue(f"{key} is not a key")
        if isinstance(bound, bool) or not isinstance(bound, int | float) or bound != bound or bound < 0 or bound == float("inf"):
            raise InvalidPolicyValue(f"{key} is not bounded by a number")
    bounds = cast("Mapping[str, float | int]", value)
    every = bounds.get(MIN_MAP_DEFAULT)
    kept = {key: bounds[key] for key in sorted(bounds) if key == MIN_MAP_DEFAULT or every is None or bounds[key] < every}
    return kept or None


def min_map_bound(value: object, key: str) -> float | int | None:
    """The bound a ``min-map`` value holds for a key: the lower of its own and ``default``; None: none."""
    bounds = _min_map(value)
    if bounds is None:
        return None
    own, every = bounds.get(key), bounds.get(MIN_MAP_DEFAULT)
    if own is None:
        return every
    return own if every is None else min(own, every)


def _min_map_keys(*values: dict[str, float | int] | None) -> list[str]:
    return sorted({key for value in values for key in (value or {})})


def _min_map_leq(a: object, b: object) -> Widened | None:
    x, y = _min_map(a), _min_map(b)
    for key in _min_map_keys(x, y):
        mine, theirs = min_map_bound(x, key), min_map_bound(y, key)
        if theirs is not None and (mine is None or mine > theirs):
            return Widened(f"{'no bound' if mine is None else _show(mine)} is above {_show(theirs)}", at=key)
    return None


def _min_map_meet(a: object, b: object) -> object:
    x, y = _min_map(a), _min_map(b)
    out: dict[str, float | int] = {}
    for key in _min_map_keys(x, y):
        mine, theirs = min_map_bound(x, key), min_map_bound(y, key)
        bound = theirs if mine is None else mine if theirs is None else min(mine, theirs)
        if bound is not None:
            out[key] = bound
    return _min_map(out)


MIN_MAP: Final = Order(
    "min-map", _min_map, _min_map_leq, lambda a, _b: _min_map(a), _min_map_meet, _contained(_min_map_leq)
)


def _subset_lift(value: object) -> object:
    return None if value is None else _names(value)


def _subset_leq(a: object, b: object) -> Widened | None:
    if b is None:
        return None
    if a is None:
        return Widened("any is not within the ceiling")
    held = set(_names(b))
    extra = [item for item in _names(a) if item not in held]
    return None if not extra else Widened(f"{','.join(extra)} not within the ceiling")


def _subset_meet(a: object, b: object) -> object:
    if a is None:
        return None if b is None else _names(b)
    if b is None:
        return _names(a)
    held = set(_names(b))
    return [item for item in _names(a) if item in held]


def _subset_contains(ceiling: object, choice: object) -> bool:
    """A choice names one member (a string), several (a list) or one per role (a map)."""
    if ceiling is None:
        return True
    held = set(_names(ceiling))
    if isinstance(choice, str):
        named: list[object] = [choice]
    elif isinstance(choice, list):
        named = list(cast("list[object]", choice))
    elif isinstance(choice, Mapping):
        named = list(cast("Mapping[str, object]", choice).values())
    else:
        named = [choice]
    return all(isinstance(item, str) and item in held for item in named)


SUBSET: Final = Order("subset", _subset_lift, _subset_leq, lambda a, _b: _subset_lift(a), _subset_meet, _subset_contains)


def _rank(ranks: Sequence[Sequence[str]], *, raise_: bool) -> Order:
    """``rank``: groups from the narrowest up; ``rank-raise``: from the widest up."""
    rank_of = {value: (len(ranks) - 1 - i if raise_ else i) for i, group in enumerate(ranks) for value in group}
    listed = [value for group in ranks for value in group]

    def rank(value: object) -> int:
        if not isinstance(value, str) or value not in rank_of:
            raise InvalidPolicyValue(f"{_show(value)} is not ranked")
        return rank_of[value]

    def lift(value: object) -> object:
        if value is not None:
            _ = rank(value)
        return value

    def leq(a: object, b: object) -> Widened | None:
        if b is None or (a is not None and rank(a) <= rank(b)):
            return None
        return Widened(f"{_show(a)} is looser than {_show(b)}" if raise_ else f"{_show(a)} is above {_show(b)}")

    def meet(a: object, b: object) -> object:
        if a is None:
            return b
        if b is None:
            return a
        ra, rb = rank(a), rank(b)
        if ra != rb:
            return a if ra < rb else b
        # Equal ranks meet to the value listed first in their group, so meet commutes.
        return a if listed.index(cast("str", a)) <= listed.index(cast("str", b)) else b

    return Order("rank-raise" if raise_ else "rank", lift, leq, _first, meet, _contained(leq))


_RULE_KEY: Final = re.compile(r"^[A-Za-z0-9_.-]+:(\*|[A-Za-z0-9_.-]+)$")


def _rank_raise_rules(ranks: Sequence[Sequence[str]]) -> Order:
    """A rank per rule key ``{rule}:{subject}``, raised only; ``{rule}:*`` binds every subject of that rule.

    A value's rank for a key is the higher of its own and its wildcard's. Meet keeps the higher rank per key;
    null is the top (no rule).
    """
    looseness_of = {value: len(ranks) - 1 - i for i, group in enumerate(ranks) for value in group}

    def looseness(value: object) -> int:
        if not isinstance(value, str) or value not in looseness_of:
            raise InvalidPolicyValue(f"{_show(value)} is not ranked")
        return looseness_of[value]

    def rules(value: object) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise InvalidPolicyValue("not a set of rules")
        out: dict[str, str] = {}
        for key, level in cast("Mapping[object, object]", value).items():
            if not isinstance(key, str) or not _RULE_KEY.match(key):
                raise InvalidPolicyValue(f"{_show(key)} is not a rule key")
            _ = looseness(level)
            out[key] = cast("str", level)
        return out

    def level_at(value: Mapping[str, str], key: str) -> int | None:
        own = looseness(value[key]) if key in value else None
        wild_key = f"{key.split(':', 1)[0]}:*"
        wild = None if key.endswith(":*") or wild_key not in value else looseness(value[wild_key])
        if own is None:
            return wild
        return own if wild is None else min(own, wild)

    def meet(a: object, b: object) -> object:
        if a is None:
            return None if b is None else rules(b)
        if b is None:
            return rules(a)
        x, y = rules(a), rules(b)
        out: dict[str, str] = {}
        for key in sorted({*x, *y}):
            left, right = x.get(key), y.get(key)
            if left is None:
                out[key] = cast("str", right)
            elif right is None:
                out[key] = left
            else:
                out[key] = left if looseness(left) <= looseness(right) else right
        return out

    def leq(a: object, b: object) -> Widened | None:
        if b is None:
            return None
        ceiling = rules(b)
        keys = sorted(ceiling)
        if a is None:
            return None if not keys else Widened("no rule is looser than the ceiling", at=keys[0])
        value = rules(a)
        for key in keys:
            level = level_at(value, key)
            if level is None or level > looseness(ceiling[key]):
                return Widened(f"{_show(value.get(key, 'no rule'))} is looser than {_show(ceiling[key])}", at=key)
        return None

    return Order(
        "rank-raise-rules",
        lambda value: None if value is None else rules(value),
        leq,
        meet,
        meet,
        _contained(leq),
    )


def _bool(*, narrow_value: bool) -> Order:
    def check(value: object) -> bool:
        if not isinstance(value, bool):
            raise InvalidPolicyValue("not a switch")
        return value

    def leq(a: object, b: object) -> Widened | None:
        return None if check(a) == check(b) or check(a) == narrow_value else Widened(f"{_show(a)} widens {_show(b)}")

    def meet(a: object, b: object) -> object:
        return narrow_value if narrow_value in (check(a), check(b)) else not narrow_value

    return Order("bool-or" if narrow_value else "bool-and", check, leq, _first, meet, _contained(leq))


def _flat(bottom: str | None) -> Order:
    """Values are unordered except a ``bottom`` below each; null (when admitted) is the top."""

    def leq(a: object, b: object) -> Widened | None:
        if b is None or _same(a, b) or (bottom is not None and a == bottom):
            return None
        return Widened(f"{_show(a)} is not within {_show(b)}")

    def meet(a: object, b: object) -> object:
        if a is None or _same(a, b):
            return b
        if b is None:
            return a
        return UNDEFINED if bottom is None else bottom

    return Order("flat", lambda value: value, leq, _first, meet, _contained(leq))


def _tree(children: Mapping[str, Sequence[str]]) -> Order:
    """Each value narrows to itself and to every value below it in ``children``."""

    def name(value: object) -> str:
        if not isinstance(value, str):
            raise InvalidPolicyValue("not a name")
        return value

    def below(value: str) -> set[str]:
        out = {value}
        for child in children.get(value, ()):
            out |= below(child)
        return out

    def leq(a: object, b: object) -> Widened | None:
        return None if name(a) in below(name(b)) else Widened(f"{_show(a)} is not within {_show(b)}")

    def meet(a: object, b: object) -> object:
        if name(a) in below(name(b)):
            return a
        if name(b) in below(name(a)):
            return b
        return UNDEFINED

    return Order("tree", name, leq, _first, meet, _contained(leq))


def _gated(switch: str, bounds: Sequence[str], off: Mapping[str, JsonValue]) -> Order:
    """A record whose boolean ``switch`` turns numeric ``bounds`` on; switched off, it is ``off``."""

    def record(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise InvalidPolicyValue("not a record")
        return cast("Mapping[str, object]", value)

    def on(value: object) -> bool:
        return record(value).get(switch) is True

    def leq(a: object, b: object) -> Widened | None:
        if not on(a):
            return None
        if not on(b):
            return Widened("switched off above", at=switch)
        for key in bounds:
            x, y = cast("int", record(a)[key]), cast("int", record(b)[key])
            if x > y:
                return Widened(f"{x} is above {y}", at=key)
        return None

    def narrow(a: object, _b: object) -> object:
        return dict(record(a)) if on(a) else dict(off)

    def meet(a: object, b: object) -> object:
        if not on(a) or not on(b):
            return dict(off)
        return {**record(a), **{key: min(cast("int", record(a)[key]), cast("int", record(b)[key])) for key in bounds}}

    return Order("gated", record, leq, narrow, meet, _contained(leq))


def _admits_leq(selectors: object, item: object) -> Widened | None:
    """A layer admits items ``{kind, version}`` by selectors (``kind`` or ``kind@version``)."""
    entry = cast("Mapping[str, str]", item)
    kind, version = entry["kind"], entry["version"]
    admitted = _names(selectors)
    if kind in admitted or f"{kind}@{version}" in admitted:
        return None
    return Widened(f"{kind}@{version} is not admitted")


# The item itself is chosen elsewhere (the first release every layer admits) and never changes here.
ADMITS: Final = Order(
    "admits",
    _names,
    _admits_leq,
    lambda _selectors, item: item,
    lambda _a, _b: UNDEFINED,
    lambda selectors, item: _admits_leq(selectors, item) is None,
)

_EFFECTS: Final[tuple[EffectKind, ...]] = ("read", "workspace", "write", "execute")
_RANK: Final[dict[str, int]] = {"allow": 0, "ask": 1, "deny": 2}


def _flow_rules(ceiling: Mapping[str, object]) -> list[FlowRule]:
    return [FlowRule(rule["level"], rule["above"]) for rule in cast("list[dict[str, str]]", ceiling.get("flow", []))]  # pyright: ignore[reportArgumentType]


def _with_flows(classes: dict[str, str], tools: dict[str, str], flows: Sequence[FlowRule]) -> dict[str, object]:
    ceiling: dict[str, object] = {"classes": classes, "tools": tools}
    if flows:
        ceiling["flow"] = [{"level": rule.level, "above": rule.above} for rule in flows]
    return ceiling


_LEVELS: Final = frozenset(_RANK)
_FLOW_LEVELS: Final = frozenset({"ask", "deny"})
_THRESHOLDS: Final = frozenset({"public", "private"})


def _lifted(raw: object) -> dict[str, object] | None:
    """``raw`` when it is already a ceiling (a parent's resolved value, which a child's pin folds): returned
    checked, never read as a layer's rules. A ceiling may hold two flow rules, which no layer's ``flow`` can say."""
    if not isinstance(raw, Mapping) or not isinstance(cast("Mapping[str, object]", raw).get("flow", []), list):
        return None
    value = cast("Mapping[str, object]", raw)
    classes, tools, flow = value.get("classes"), value.get("tools"), cast("list[object]", value.get("flow", []))
    if set(value) - {"classes", "tools", "flow"} or not isinstance(classes, Mapping) or not isinstance(tools, Mapping):
        return None
    # A layer's rules name the classes they set; a ceiling decides all of them. Rules that happen to name all four
    # and no flow read the same either way.
    if set(cast("Mapping[str, object]", classes)) != set(_EFFECTS):
        return None
    levels = [*cast("Mapping[str, object]", classes).values(), *cast("Mapping[str, object]", tools).values()]
    rules_ok = all(
        isinstance(rule, Mapping)
        and set(cast("Mapping[str, object]", rule)) == {"level", "above"}
        and cast("Mapping[str, object]", rule)["level"] in _FLOW_LEVELS
        and cast("Mapping[str, object]", rule)["above"] in _THRESHOLDS
        for rule in flow
    )
    if not all(level in _LEVELS for level in levels) or not rules_ok:
        raise InvalidPolicyValue("approval_ceiling_invalid")
    rules = [FlowRule(rule["level"], rule["above"]) for rule in cast("list[dict[str, str]]", flow)]  # pyright: ignore[reportArgumentType]
    return _with_flows(
        dict(cast("Mapping[str, str]", classes)), dict(cast("Mapping[str, str]", tools)), strictest_flows(rules)
    )


def approval_ceiling(raw: object) -> dict[str, object]:
    """One layer's rules (``approval-rules.schema.json``) as a ceiling: every class, its tool rules, its flow.
    A value that is a ceiling already is itself (lifting is idempotent)."""
    lifted = _lifted(raw)
    if lifted is not None:
        return lifted
    try:
        rules = parse_rules(cast("JsonValue", raw))
    except ApprovalConfigInvalid as refused:
        raise InvalidPolicyValue(refused.reason_code) from None
    flow = None if rules.flow is None else layer_rule(rules.flow)
    return _with_flows(
        {kind: rules.classes.get(kind, "allow") for kind in _EFFECTS},
        dict(rules.tools),
        strictest_flows((flow,)),
    )


def meet_approvals(a: object, b: object) -> dict[str, object]:
    """The strictest of two ceilings: per class, per tool, and the flow rules of both."""
    x, y = cast("Mapping[str, Mapping[str, ApprovalDecision]]", a), cast("Mapping[str, Mapping[str, ApprovalDecision]]", b)
    classes = {kind: stricter(x["classes"][kind], y["classes"][kind]) for kind in _EFFECTS}
    tools: dict[str, str] = dict(x["tools"])
    for tool, level in y["tools"].items():
        tools[tool] = stricter(cast("ApprovalDecision", tools.get(tool, "allow")), level)
    return _with_flows(dict(classes), tools, strictest_flows((*_flow_rules(x), *_flow_rules(y))))


def approvals_within(a: object, b: object) -> bool:
    """``a`` decides every class and tool at least as strictly as ``b``, and covers each of ``b``'s flow rules."""
    x, y = cast("Mapping[str, Mapping[str, str]]", a), cast("Mapping[str, Mapping[str, str]]", b)
    return (
        all(_RANK[x["classes"][kind]] >= _RANK[y["classes"][kind]] for kind in _EFFECTS)
        and all(_RANK[x["tools"].get(tool, "allow")] >= _RANK[level] for tool, level in y["tools"].items())
        and all(any(covers(own, rule) for own in _flow_rules(x)) for rule in _flow_rules(y))
    )


APPROVAL_RULES: Final = Order(
    "approval-rules",
    approval_ceiling,
    lambda a, b: None if approvals_within(a, b) else Widened("decides a call more loosely"),
    lambda a, b: a if b is None else meet_approvals(a, b),
    meet_approvals,
    lambda ceiling, choice: approvals_within(choice, ceiling),
)


def order_of(spec: Mapping[str, JsonValue]) -> Order:
    """The order a dimension row names, built from its parameters."""
    kind = spec["kind"]
    ranks = cast("list[list[str]]", spec.get("ranks", []))
    builders: dict[str, Callable[[], Order]] = {
        "min": lambda: MIN,
        "subset": lambda: SUBSET,
        "min-map": lambda: MIN_MAP,
        "rank": lambda: _rank(ranks, raise_=False),
        "rank-raise": lambda: _rank(ranks, raise_=True),
        "bool-and": lambda: _bool(narrow_value=False),
        "bool-or": lambda: _bool(narrow_value=True),
        "flat": lambda: _flat(cast("str | None", spec.get("bottom"))),
        "tree": lambda: _tree(cast("dict[str, list[str]]", spec.get("children", {}))),
        "gated": lambda: _gated(
            cast("str", spec.get("switch", "enabled")),
            cast("list[str]", spec.get("bounds", [])),
            cast("dict[str, JsonValue]", spec.get("off", {"enabled": False})),
        ),
        "admits": lambda: ADMITS,
        "approval-rules": lambda: APPROVAL_RULES,
        "rank-raise-rules": lambda: _rank_raise_rules(ranks),
    }
    if kind not in builders:
        raise InvalidPolicyValue(f"unknown order {kind!r}")
    return builders[kind]()


__all__ = [
    "ADMITS",
    "APPROVAL_RULES",
    "MIN",
    "MIN_MAP",
    "MIN_MAP_DEFAULT",
    "SUBSET",
    "UNDEFINED",
    "InvalidPolicyValue",
    "Order",
    "Widened",
    "approval_ceiling",
    "approvals_within",
    "meet_approvals",
    "min_map_bound",
    "order_of",
]

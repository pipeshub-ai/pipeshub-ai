"""The YAML scenario DSL: load, validate and dump, one format for every tier.

A scenario names a world (a shared base world file, or an inline one), the
changes applied to it, and what the product should answer afterwards. The runner
also checks every viewer x node pair, so `expect.verdicts` lists only the pairs
that matter. See `scenarios/README.md` for a worked example.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import yaml

from . import mutations
from .model import (
    MUTATION_KINDS,
    ORG_ID,
    Gate,
    Grant,
    Link,
    Membership,
    Mutation,
    Node,
    Parent,
    Principal,
    World,
)

Data = Mapping[str, object]
SCENARIOS_ROOT = Path(__file__).parent / "scenarios"
INDEX_WRITES = ("0", "1", "subtree", "delete", "unlink")  # unlink: the full-sync sweep unlinks the node
_WORLD_KEYS = ("principals", "nodes", "grants", "memberships", "gates", "links")
T = TypeVar("T")


@dataclass(frozen=True)
class Verdict:
    viewer: str
    node: str
    admit: bool


@dataclass(frozen=True)
class Cite:
    """Which record a viewer is shown for a virtual record id (None: none)."""

    viewer: str
    vrid: str
    record: str | None
    indexed_only: bool = False


@dataclass(frozen=True)
class Expect:
    verdicts: tuple[Verdict, ...] = ()
    before: tuple[Verdict, ...] = ()  # the same pairs before the change
    cite: tuple[Cite, ...] = ()
    index_writes: str | None = None
    during: str | None = None
    source_delta: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    world: World
    source: str = ""
    rule: str = ""
    world_ref: str | None = None  # base world file, relative to the scenarios root
    mutations: tuple[Mutation, ...] = ()
    expect: Expect = field(default_factory=Expect)
    tags: tuple[str, ...] = ()
    comment: str = ""

    def before_world(self) -> World:
        return self.world

    def final_world(self) -> World:
        return mutations.apply_all(self.world, self.mutations)


# ---------------------------------------------------------------- world <-> data

def _plain(obj: object, defaults: object) -> dict[str, object]:
    """Fields of a dataclass that differ from the defaults, in field order."""
    return {
        f.name: getattr(obj, f.name)
        for f in dataclasses.fields(obj)  # type: ignore[arg-type]
        if getattr(obj, f.name) != getattr(defaults, f.name)
    }


def _node_data(n: Node) -> dict[str, object]:
    base = Node(n.id, n.kind)
    out: dict[str, object] = {"id": n.id, "kind": n.kind}
    out.update({k: v for k, v in _plain(n, base).items() if k not in ("id", "kind", "parents", "inherits", "belongs")})
    if n.parents:
        out["parents"] = [p.id if p.rel == "PARENT_CHILD" else {"id": p.id, "rel": p.rel} for p in n.parents]
    if n.inherits:
        out["inherits"] = list(n.inherits)
    if n.belongs:
        out["belongs"] = list(n.belongs)
    return out


def _row(obj: object, required: int) -> list[object]:
    """Field values in order, without trailing defaults (but at least `required`)."""
    fields = dataclasses.fields(obj)  # type: ignore[arg-type]
    values = [getattr(obj, f.name) for f in fields]
    while len(values) > required and values[-1] == fields[len(values) - 1].default:
        values.pop()
    return values


def world_to_data(w: World) -> dict[str, object]:
    data: dict[str, object] = {}
    if w.principals:
        data["principals"] = [
            {"id": p.id, **{k: v for k, v in _plain(p, Principal(p.id)).items() if k != "id"}}
            for p in w.principals
        ]
    if w.nodes:
        data["nodes"] = [_node_data(n) for n in w.nodes]
    if w.grants:
        data["grants"] = [_row(g, 2) for g in w.grants]
    if w.memberships:
        data["memberships"] = [_row(m, 2) for m in w.memberships]
    if w.gates:
        data["gates"] = [_row(g, 2) for g in w.gates]
    if w.links:
        data["links"] = [_row(link, 3) for link in w.links]
    return data


def _check_keys(data: Data, allowed: Iterable[str], where: str) -> None:
    extra = set(data) - set(allowed)
    if extra:
        raise ValueError(f"{where}: unknown keys {sorted(extra)}")


def _from_fields(cls: Callable[..., T], data: Data, where: str) -> T:
    names = [f.name for f in dataclasses.fields(cls)]  # type: ignore[arg-type]
    _check_keys(data, names, where)
    return cls(**data)


def _parent(entry: object) -> Parent:
    if isinstance(entry, str):
        return Parent(entry)
    if isinstance(entry, Mapping):
        return _from_fields(Parent, entry, "parent")
    raise ValueError(f"bad parent entry {entry!r}")


def _node(data: Data) -> Node:
    fields = dict(data)
    fields["parents"] = tuple(_parent(p) for p in fields.get("parents", ()))  # type: ignore[attr-defined]
    fields["inherits"] = tuple(fields.get("inherits", ()))  # type: ignore[arg-type]
    fields["belongs"] = tuple(fields.get("belongs", ()))  # type: ignore[arg-type]
    return _from_fields(Node, fields, f"node {data.get('id')}")


def _rows(cls: Callable[..., T], entries: Iterable[object], names: tuple[str, ...]) -> tuple[T, ...]:
    out: list[T] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            out.append(_from_fields(cls, entry, cls.__name__))  # type: ignore[attr-defined]
        elif isinstance(entry, list):
            out.append(cls(**dict(zip(names, entry, strict=False))))
        else:
            raise ValueError(f"bad {cls.__name__} row {entry!r}")  # type: ignore[attr-defined]
    return tuple(out)


def world_from_data(data: Data) -> World:
    _check_keys(data, (*_WORLD_KEYS, "name", "comment"), "world")
    return World(
        principals=tuple(_from_fields(Principal, p, "principal") for p in data.get("principals", ())),  # type: ignore[attr-defined]
        nodes=tuple(_node(n) for n in data.get("nodes", ())),  # type: ignore[attr-defined]
        grants=_rows(Grant, data.get("grants", ()), ("grantee", "node", "type", "role")),  # type: ignore[arg-type]
        memberships=_rows(Membership, data.get("memberships", ()), ("member", "group", "type", "edge")),  # type: ignore[arg-type]
        gates=_rows(Gate, data.get("gates", ()), ("who", "app", "via")),  # type: ignore[arg-type]
        links=_rows(Link, data.get("links", ()), ("user", "source", "connector")),  # type: ignore[arg-type]
    )


def validate_world(w: World, where: str) -> None:
    """Every edge endpoint must exist, so a typo fails loudly instead of vanishing."""
    nodes = {n.id for n in w.nodes}
    principals = {p.id for p in w.principals}
    if len(nodes) != len(w.nodes) or len(principals) != len(w.principals):
        raise ValueError(f"{where}: duplicate ids")
    known = nodes | principals | {ORG_ID}
    refs: list[str] = []
    for n in w.nodes:
        refs += [p.id for p in n.parents] + list(n.inherits) + list(n.belongs)
    refs += [x for g in w.grants for x in (g.grantee, g.node)]
    refs += [x for m in w.memberships for x in (m.member, m.group)]
    refs += [x for g in w.gates for x in (g.who, g.app)]
    refs += [x for link in w.links for x in (link.user, link.source, link.connector)]
    missing = sorted({r for r in refs if r not in known})
    if missing:
        raise ValueError(f"{where}: unknown ids {missing}")


# ---------------------------------------------------------------- scenario <-> data

_SCENARIO_KEYS = ("id", "title", "source", "rule", "world", "mutation", "mutations", "expect", "tags", "comment")


def _verdicts(table: object) -> tuple[Verdict, ...]:
    out: list[Verdict] = []
    for viewer, row in (table or {}).items():  # type: ignore[attr-defined]
        for node, word in row.items():
            if word not in ("admit", "deny"):
                raise ValueError(f"verdict must be admit or deny, got {word!r}")
            out.append(Verdict(viewer, node, word == "admit"))
    return tuple(out)


def _verdict_table(verdicts: tuple[Verdict, ...]) -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for v in verdicts:
        table.setdefault(v.viewer, {})[v.node] = "admit" if v.admit else "deny"
    return table


def _expect(data: Data) -> Expect:
    _check_keys(data, ("verdicts", "before", "cite", "index_writes", "during", "source_delta"), "expect")
    writes = data.get("index_writes")
    if writes is not None and str(writes) not in INDEX_WRITES:
        raise ValueError(f"index_writes must be one of {INDEX_WRITES}, got {writes!r}")
    delta = data.get("source_delta")
    return Expect(
        verdicts=_verdicts(data.get("verdicts")),
        before=_verdicts(data.get("before")),
        cite=tuple(_from_fields(Cite, c, "cite") for c in data.get("cite", ())),  # type: ignore[attr-defined]
        index_writes=None if writes is None else str(writes),
        during=data.get("during"),  # type: ignore[arg-type]
        source_delta=None if delta in (None, "none") else str(delta),
    )


def _mutation(data: Data) -> Mutation:
    for key in ("kind", "node", "ref", "value"):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"mutation {key} must be a string, got {data[key]!r} (quote it: `value: \"true\"`)")
    m = _from_fields(Mutation, data, "mutation")
    if m.kind not in MUTATION_KINDS:
        raise ValueError(f"unknown mutation kind {m.kind!r}")
    return m


class WorldStore:
    """Resolves `world: <ref>` to a base world file under the scenarios root."""

    def __init__(self, root: Path = SCENARIOS_ROOT) -> None:
        self.root = root
        self._cache: dict[str, World] = {}

    def world(self, ref: str) -> World:
        if ref not in self._cache:
            data = yaml.safe_load((self.root / f"{ref}.yaml").read_text())
            w = world_from_data(data)
            validate_world(w, ref)
            self._cache[ref] = w
        return self._cache[ref]


def scenario_from_data(data: Data, store: WorldStore) -> Scenario:
    _check_keys(data, _SCENARIO_KEYS, f"scenario {data.get('id')}")
    ref: str | None = None
    raw = data["world"]
    if isinstance(raw, str):
        ref, world = raw, store.world(raw)
    else:
        world = world_from_data(raw)  # type: ignore[arg-type]
        validate_world(world, str(data["id"]))
    raw_mutations = data.get("mutations", [data["mutation"]] if "mutation" in data else [])
    scenario = Scenario(
        id=str(data["id"]),
        title=str(data["title"]),
        world=world,
        source=str(data.get("source", "")),
        rule=str(data.get("rule", "")),
        world_ref=ref,
        mutations=tuple(_mutation(m) for m in raw_mutations),  # type: ignore[attr-defined]
        expect=_expect(data.get("expect", {})),  # type: ignore[arg-type]
        tags=tuple(data.get("tags", ())),  # type: ignore[arg-type]
        comment=str(data.get("comment", "")),
    )
    _validate_scenario(scenario)
    return scenario


def _validate_scenario(s: Scenario) -> None:
    final = s.final_world()
    known = {n.id for n in final.nodes} | {p.id for p in final.principals}
    pairs = [*s.expect.verdicts, *s.expect.before]
    for v in pairs:
        if v.viewer not in known or v.node not in known:
            raise ValueError(f"{s.id}: verdict names unknown id {v.viewer}/{v.node}")
    for c in s.expect.cite:
        if c.viewer not in known:
            raise ValueError(f"{s.id}: cite names unknown viewer {c.viewer}")


def scenario_to_data(s: Scenario) -> dict[str, object]:
    data: dict[str, object] = {"id": s.id, "title": s.title}
    if s.source:
        data["source"] = s.source
    if s.rule:
        data["rule"] = s.rule
    data["world"] = s.world_ref if s.world_ref else world_to_data(s.world)
    if s.mutations:
        data["mutations"] = [_plain(m, Mutation("")) | {"kind": m.kind} for m in s.mutations]
    expect: dict[str, object] = {}
    e = s.expect
    if e.before:
        expect["before"] = _verdict_table(e.before)
    if e.verdicts:
        expect["verdicts"] = _verdict_table(e.verdicts)
    if e.cite:
        expect["cite"] = [{f.name: getattr(c, f.name) for f in dataclasses.fields(c)} for c in e.cite]
    if e.index_writes is not None:
        expect["index_writes"] = int(e.index_writes) if e.index_writes.isdigit() else e.index_writes
    if e.during:
        expect["during"] = e.during
    if e.source_delta:
        expect["source_delta"] = e.source_delta
    data["expect"] = expect
    if s.tags:
        data["tags"] = list(s.tags)
    if s.comment:
        data["comment"] = s.comment
    return data


# ---------------------------------------------------------------- text

def _flow(obj: object) -> str:
    text = yaml.safe_dump(obj, default_flow_style=True, width=10**6, sort_keys=False).strip()
    return text.removesuffix("\n...").strip()


def _block(key: str, value: object, indent: str) -> list[str]:
    if isinstance(value, dict) and not value:
        return [f"{indent}{key}: {{}}"]       # a bare key would reload as null
    if isinstance(value, dict) and key == "world":
        lines = [f"{indent}{key}:"]
        for section, items in value.items():
            lines.append(f"{indent}  {section}:")
            lines += [f"{indent}    - {_flow(item)}" for item in items]  # type: ignore[attr-defined]
        return lines
    if isinstance(value, dict) and key == "expect":
        lines = [f"{indent}{key}:"]
        for k, v in value.items():
            if isinstance(v, dict) and k in ("verdicts", "before"):
                lines.append(f"{indent}  {k}:")
                lines += [f"{indent}    {_flow(viewer)}: {_flow(row)}" for viewer, row in v.items()]
            else:
                lines.append(f"{indent}  {k}: {_flow(v)}")
        return lines
    if key == "mutations":
        return [f"{indent}{key}:", *[f"{indent}  - {_flow(m)}" for m in value]]  # type: ignore[attr-defined]
    return [f"{indent}{key}: {_flow(value)}"]


def dump_scenarios(scenarios: Iterable[Scenario]) -> str:
    out: list[str] = []
    for s in scenarios:
        lines: list[str] = []
        for key, value in scenario_to_data(s).items():
            lines += _block(key, value, "  ")
        lines[0] = "-" + lines[0][1:]
        out += lines
    return "\n".join(out) + "\n"


def dump_world(w: World) -> str:
    return "\n".join(_world_lines(w)) + "\n"


def _world_lines(w: World) -> list[str]:
    lines: list[str] = []
    for section, items in world_to_data(w).items():
        lines.append(f"{section}:")
        lines += [f"  - {_flow(item)}" for item in items]  # type: ignore[attr-defined]
    return lines


def load_scenarios(path: Path, store: WorldStore | None = None) -> list[Scenario]:
    store = store or WorldStore()
    data = yaml.safe_load(path.read_text())
    entries = data if isinstance(data, list) else [data]
    return [scenario_from_data(e, store) for e in entries]


def all_scenario_files(root: Path = SCENARIOS_ROOT) -> list[Path]:
    """Scenario files: a yaml list, or a mapping with an id (base worlds and the deltas table have neither)."""
    files = []
    for p in sorted(root.rglob("*.yaml")):
        data = yaml.safe_load(p.read_text())
        if isinstance(data, list) or (isinstance(data, dict) and "id" in data):
            files.append(p)
    return files


def load_all(root: Path = SCENARIOS_ROOT) -> list[Scenario]:
    store = WorldStore(root)
    return [s for p in all_scenario_files(root) for s in load_scenarios(p, store)]

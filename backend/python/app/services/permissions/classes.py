"""The access class of a record, compiled from the hop rule over the nodes a stamper reads.

A class is a disjunction of clauses; a clause is a set of literals. A literal is a node the viewer must hold a grant on (rule R5), or SELF (the record's own grant, checked
per row). Every clause also needs the gate of the record's app (R4). The arms mirror ``tests/permissions/oracle.py``:

- R12 member of an app that opens: the empty clause;
- R8 declared: for each live record-group-level group g of the app at or above the record's own groups: ``{g}`` or the R7 clauses of g;
- R7 walk: OR over hierarchy paths to the app (at most 50 hops, record-group-level parents of the app skipped) of AND over hops, where a hop c -> p is always true (the app
  opens; or the rule is OPEN/STRICT and c inherits p), a literal c (OPEN/STRICT not inheriting; RESTRICTED inheriting) or never true (deleted; RESTRICTED not inheriting; any
  other rule). A literal on a node with no grant at all can never be held, so the hop never passes;
- R9 seed: ``{SELF}`` for an OPEN live granted record or group;
- R10 below a seed: ``{s}`` plus the literals of the stretch, for each OPEN live granted ancestor s of the app reached over OPEN live nodes that inherit or are granted. This is the
  monotone form: exact where the oracle's "no other arm admits s" cannot change the answer, i.e. in an app without record-group-level groups (reaching s from the app then implies
  the R7 clause through s). In an app with such groups it is exact only for a seed that t reaches over plain nodes and whose R8 groups are all t's (``seeds_exact``); a record that
  needs any other R10 clause the other arms do not absorb is refused.

A refusal means the record keeps today's walk (legacy) instead of a class: a node the map lacks (fail closed), more than ``MAX_CLAUSES`` clauses or ``too_many_paths``, a path
near the oracle's 50-hop cap under an R10 clause, an R10 seed in a record-group-level app that ``seeds_exact`` cannot show exact, a record of no app. ``deny`` is not legacy: the record is never readable (deleted, placeholder).

One :class:`Compiler` serves a whole app. Its walk memo is cleared for every record, so a clause computed for one record cannot carry that record's SELF into another; the per-node
``shallow`` memo is allowed only after :func:`check_shallow` proves the app's map acyclic with every path under ``MAX_HOPS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.permissions.ports import StampNode

SELF = "@self"
MAX_HOPS = 50
MAX_CLAUSES = 16


@dataclass(frozen=True)
class Klass:
    app: str
    clauses: frozenset[frozenset[str]]

    def admits(self, granted: set[str], gated: set[str], *, self_granted: bool) -> bool:
        if self.app not in gated:
            return False
        return any(all((self_granted if lit == SELF else lit in granted) for lit in c) for c in self.clauses)


@dataclass(frozen=True)
class Refusal:
    reason: str


class Refused(Exception):
    pass


def _absorb(clauses: set[frozenset[str]]) -> frozenset[frozenset[str]]:
    keep = [c for c in clauses if not any(o < c for o in clauses)]
    return frozenset(keep)


class Compiler:
    def __init__(self, nodes: dict[str, StampNode], app_id: str, *, opens: bool, group_level: bool, shallow: bool = False) -> None:
        """One instance may compile many records. The walk memo is per record (cleared by `compile`) unless `shallow`, the bulk mode,
        which memoises per node across records and is only allowed on a map `check_shallow` accepts (acyclic, every path under
        MAX_HOPS): there no walk from an ancestor can reach the record again, so no clause can carry another record's SELF, and the
        hop budget never binds."""
        self.n, self.app, self.opens, self.rgl, self.shallow = nodes, app_id, opens, group_level, shallow
        if shallow:
            bad = check_shallow(nodes)
            if bad:
                raise ValueError(f"shallow mode refused: {bad}")
        self._walk: dict[tuple[str, int], frozenset[frozenset[str]]] = {}
        self._below_memo: dict[str, frozenset[frozenset[str]]] = {}

    def node(self, i: str) -> StampNode | None:
        if i == self.app:
            return None
        if i not in self.n:
            raise Refused("missing_node")
        return self.n[i]

    def _lit(self, c: str, t: str | None) -> str | None:
        """The literal for "c is granted to the viewer", or None when c can never be (no grant at all)."""
        if c == t:
            return SELF if self.n[c].granted else None
        return c if self.n[c].granted else None

    def hop(self, c: str, p: str, t: str | None) -> frozenset[str] | None:
        """Literals the hop c -> p needs (empty: always passes), or None: it never passes."""
        cn = self.n[c]
        if cn.deleted:
            return None
        if self.opens:
            return frozenset()
        inh = p in cn.inherits
        rule = cn.rule or "OPEN"
        if rule in ("OPEN", "STRICT"):
            if inh:
                return frozenset()
        elif rule == "RESTRICTED":
            if not inh:
                return None
        else:
            return None
        lit = self._lit(c, t)
        return frozenset({lit}) if lit else None

    def _must(self, i: str) -> StampNode:
        """The node ``i`` (not the app): a node the map lacks fails closed."""
        n = self.node(i)
        if n is None:
            raise Refused("missing_node")
        return n

    def is_rgl(self, i: str) -> bool:
        x = self.n.get(i)
        return x is not None and x.kind == "group" and x.group_level and x.connector_id == self.app

    def walk(self, c: str, t: str | None, left: int) -> frozenset[frozenset[str]]:
        """R7: clauses for 'some path c -> ... -> app passes' within `left` hops."""
        key = (c, -1 if self.shallow else left) if c != t else (c + "\0self", left)
        if key in self._walk:
            return self._walk[key]
        out: set[frozenset[str]] = set()
        if left > 0:
            cn = self._must(c)
            for p in cn.parents:
                if self.is_rgl(p):
                    continue
                need = self.hop(c, p, t)
                if need is None:
                    continue
                if p == self.app:
                    out.add(need)
                    continue
                _ = self._must(p)
                for rest in self.walk(p, t, left - 1):
                    out.add(need | rest)
                if len(out) > 4 * MAX_CLAUSES:
                    raise Refused("too_many_paths")
        res = _absorb(out)
        self._walk[key] = res
        return res

    def declared_groups(self, t: str) -> set[str]:
        """The live record-group-level groups R8 looks at for ``t``: the groups it belongs to (itself, for a group) and every group above them."""
        tn = self.n[t]
        starts = [t] if tn.kind == "group" else list(tn.groups)
        out: set[str] = set()
        for mg in starts:
            m = self.node(mg)
            if m is None or m.kind != "group" or m.deleted:
                continue
            seen: set[str] = {mg}
            frontier: set[str] = {mg}
            for depth in range(MAX_HOPS + 1):
                out |= {g for g in frontier if self.is_rgl(g) and not self.n[g].deleted}
                if depth == MAX_HOPS:
                    break
                nxt: set[str] = set()
                for c in frontier:
                    if self.n[c].deleted:
                        continue
                    for p in self.n[c].parents:
                        if p == self.app:
                            continue
                        pn = self._must(p)
                        if pn.kind == "group" and p not in seen:
                            seen.add(p)
                            nxt.add(p)
                if not nxt:
                    break
                frontier = nxt
        return out

    def declared(self, t: str) -> set[frozenset[str]]:
        """R8 clauses."""
        out: set[frozenset[str]] = set()
        for g in self.declared_groups(t):
            me = t if g == t else None
            lit = self._lit(g, t) if g == t else (g if self.n[g].granted else None)
            if lit:
                out.add(frozenset({lit}))
            out |= set(self.walk(g, me, MAX_HOPS))
        return out

    def _is_seed(self, pn: StampNode) -> bool:
        return pn.kind in ("record", "group") and pn.connector_id == self.app and not pn.deleted and (pn.rule or "OPEN") == "OPEN" and pn.granted

    def _below(self, c: str, t: str) -> frozenset[frozenset[str]]:
        """R10 clauses of the walk that is at ``c`` (shallow maps only): per node, so the files of one folder share the folder's answer. Only ``t`` itself, whose own grant
        is SELF, has a memo of its own; the hop budget never binds on a shallow map."""
        key = c + "\0self" if c == t else c
        got = self._below_memo.get(key)
        if got is not None:
            return got
        out: set[frozenset[str]] = set()
        cn = self.node(c)
        if cn is not None and not cn.deleted and (cn.rule or "OPEN") == "OPEN":
            for p in cn.parents:
                if p == self.app:
                    continue
                if p in cn.inherits:
                    extra: frozenset[str] = frozenset()
                else:
                    lit = self._lit(c, t)
                    if lit is None:
                        continue
                    extra = frozenset({lit})
                if self._is_seed(self._must(p)):
                    out.add(extra | {p})
                out |= {extra | need for need in self._below(p, t)}
                if len(out) > 4 * MAX_CLAUSES:
                    raise Refused("too_many_seeds")
        res = _absorb(out)
        self._below_memo[key] = res
        return res

    def below_seed(self, t: str) -> set[frozenset[str]]:
        """R10 clauses (monotone form). The frontier keeps, per node, every minimal set of stretch literals that reaches it."""
        if self.shallow:
            return set(self._below(t, t))
        out: set[frozenset[str]] = set()
        frontier: dict[str, frozenset[frozenset[str]]] = {t: frozenset({frozenset()})}
        for _ in range(MAX_HOPS):
            nxt: dict[str, set[frozenset[str]]] = {}
            for c, needs in frontier.items():
                cn = self.node(c)
                if cn is None or cn.deleted or (cn.rule or "OPEN") != "OPEN":
                    continue
                for p in cn.parents:
                    if p == self.app:
                        continue
                    if p in cn.inherits:
                        extra: frozenset[str] = frozenset()
                    else:
                        lit = self._lit(c, t)
                        if lit is None:
                            continue
                        extra = frozenset({lit})
                    pn = self._must(p)
                    for need in needs:
                        step = need | extra
                        if pn.kind in ("record", "group") and pn.connector_id == self.app and not pn.deleted \
                                and (pn.rule or "OPEN") == "OPEN" and pn.granted:
                            out.add(step | {p})
                        nxt.setdefault(p, set()).add(step)
            if not nxt:
                break
            frontier = {p: _absorb(ss) for p, ss in nxt.items()}
            if len(out) > 4 * MAX_CLAUSES or sum(len(v) for v in frontier.values()) > 64 * MAX_CLAUSES:
                raise Refused("too_many_seeds")
        return out

    def seeds_exact(self, t: str, others: set[frozenset[str]]) -> bool:
        """Whether the monotone R10 form is exact for ``t`` in a record-group-level app, where the oracle adds "no other arm admits the seed s". It is when every seed whose clause
        no clause of ``others`` absorbs is a plain node that t reaches over plain nodes (no record-group-level group on the way) and whose own R8 groups are all groups of t:
        then whatever admits s by R7 (the same path continues from t, the record-group-level parents being the only ones R7 skips) or by R8 (t has the same groups) admits t."""
        mine = self.declared_groups(t)
        frontier: dict[str, set[tuple[frozenset[str], bool]]] = {t: {(frozenset(), False)}}
        for _ in range(MAX_HOPS):
            nxt: dict[str, set[tuple[frozenset[str], bool]]] = {}
            for c, needs in frontier.items():
                cn = self.node(c)
                if cn is None or cn.deleted or (cn.rule or "OPEN") != "OPEN":
                    continue
                for p in cn.parents:
                    if p == self.app:
                        continue
                    if p in cn.inherits:
                        extra: frozenset[str] = frozenset()
                    else:
                        lit = self._lit(c, t)
                        if lit is None:
                            continue
                        extra = frozenset({lit})
                    pn = self._must(p)
                    for need, crossed in needs:
                        step, crossed_now = need | extra, crossed or self.is_rgl(p)
                        if self._is_seed(pn) and not any(o <= step | {p} for o in others) and (crossed_now or not self.declared_groups(p) <= mine):
                            return False
                        nxt.setdefault(p, set()).add((step, crossed_now))
            if not nxt:
                break
            frontier = nxt
            if sum(len(v) for v in frontier.values()) > 64 * MAX_CLAUSES:
                raise Refused("too_many_seeds")
        return True

    def upward(self, t: str) -> tuple[set[str], bool]:
        """Every node reachable up from t over hierarchy parents (the app excluded) and whether that part of the map has a cycle."""
        seen: set[str] = set()
        state: dict[str, int] = {}
        cyclic = False
        stack: list[tuple[str, int]] = [(t, 0)]
        while stack:
            c, i = stack.pop()
            if i == 0:
                if state.get(c) == 2:
                    continue
                state[c] = 1
                seen.add(c)
            ps = [p for p in (self.n[c].parents if c in self.n else ()) if p != self.app]
            if i < len(ps):
                stack.append((c, i + 1))
                p = ps[i]
                if state.get(p) == 1:
                    cyclic = True
                elif state.get(p) != 2:
                    stack.append((p, 0))
            else:
                state[c] = 2
        return seen, cyclic

    def near_cap(self, t: str) -> bool:
        """True when some simple path up from t may reach the oracle's 50-hop cap. Exact longest path on an acyclic part; on a cyclic
        part (where longest simple path is not a DP) the bound "the part has >= MAX_HOPS nodes" decides (a DFS memo
        through a cycle cut undercounts)."""
        seen, cyclic = self.upward(t)
        if cyclic:
            return len(seen) >= MAX_HOPS
        h: dict[str, int] = {}                 # hops of the longest path up from a node (the hop into the app counts)
        stack: list[tuple[str, bool]] = [(t, False)]
        while stack:
            c, done = stack.pop()
            if c in h:
                continue
            ps = list(self.n[c].parents) if c in self.n else []
            if not done:
                stack.append((c, True))
                stack += [(p, False) for p in ps if p != self.app and p not in h]
                continue
            h[c] = max((1 if p == self.app else 1 + h.get(p, 0) for p in ps), default=0)
        return h[t] >= MAX_HOPS

    def compile(self, t: str) -> Klass | Refusal:
        if not self.shallow:
            self._walk = {}                    # per-record memo
        try:
            tn = self.n[t]
            if tn.deleted or tn.placeholder:
                return Refusal("deny")          # R11: never readable
            clauses: set[frozenset[str]] = set()
            if self.opens and tn.kind == "record" and tn.belongs_app:
                return Klass(self.app, frozenset({frozenset()}))   # R12
            if self.rgl:
                clauses |= self.declared(t)
            clauses |= set(self.walk(t, t, MAX_HOPS))
            if (tn.rule or "OPEN") == "OPEN" and tn.granted:
                clauses.add(frozenset({SELF}))                     # R9
            seeds = self.below_seed(t)
            if seeds:
                merged = _absorb(clauses | seeds)
                if not set(merged) <= clauses:   # an R10 clause the other arms do not absorb
                    if self.rgl and not self.seeds_exact(t, clauses):
                        return Refusal("rgl_below_seed")
                    if self.near_cap(t):
                        return Refusal("deep_below_seed")
                clauses = set(merged)
            res = _absorb(clauses)
            if len(res) > MAX_CLAUSES:
                return Refusal("too_many_clauses")
            return Klass(self.app, res)
        except Refused as r:
            return Refusal(str(r))


def check_shallow(nodes: dict[str, StampNode]) -> str | None:
    """None when the map is acyclic over hierarchy parents and every path is shorter than MAX_HOPS; else why not."""
    depth: dict[str, int] = {}
    state: dict[str, int] = {}
    for root in nodes:
        if state.get(root) == 2:
            continue
        stack = [(root, 0)]
        while stack:
            c, i = stack.pop()
            if i == 0:
                state[c] = 1
            ps = [p for p in (nodes[c].parents if c in nodes else ()) if p in nodes and nodes[p].kind != "app"]
            if i < len(ps):
                stack.append((c, i + 1))
                p = ps[i]
                if state.get(p) == 1:
                    return f"cycle at {p}"
                if state.get(p) != 2:
                    stack.append((p, 0))
            else:
                depth[c] = max((depth[p] + 1 for p in ps), default=1)
                if depth[c] >= MAX_HOPS:
                    return f"path of {depth[c]} hops at {c}"
                state[c] = 2
    return None

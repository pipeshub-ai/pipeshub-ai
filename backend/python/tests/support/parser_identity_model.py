"""A deterministic model of three records sharing bytes, driven through the real identity rules of ``parser_identity``.

World: same-bytes re-syncs, type renames (the connector resets the record to NOT_STARTED and publishes an update), content changes,
forced runs that re-point their twins (a replacement copy), and the md5 twin attach, in a fleet where a fraction of the processes is
the previous release (pre-identity code for the whole event: a skip with no identity, an ungated attach, a re-point that moves only
the copy, no identity written).

A skip is *wrong* when the parser that built the record's copy differs from the type the record has now in the graph. Each wrong
skip is attributed to the identity state it relied on: ``parsed``/``twin`` (known: the identity claimed the copy was verified),
``adopted``/``absent`` (never confirmed by a parse: records indexed before the identity existed, the accepted residual).

The decisions are the real functions (``known_identity``, ``twin_may_share``, ``copied_identity_fields``, ``indexed_parser_fields``,
``key_of``, ``parser_key``); only the stores are dicts. ``python -m tests.support.parser_identity_model`` runs the full seed ranges.
"""
from __future__ import annotations

import collections
import random
from typing import TYPE_CHECKING, Any

from app.modules.pipeline import parser_identity as pi

if TYPE_CHECKING:
    from collections.abc import Callable

TYPES = {"txt": ("text/plain", "txt", "a.txt"), "md": ("text/markdown", "md", "a.md")}
RANGES = ((90210, 92210), (4711, 6711), (31337, 33337))
KNOWN_STATES = frozenset({pi.SOURCE_PARSED, pi.SOURCE_TWIN})


def _key_of_type(kind: str) -> str:
    mime, ext, name = TYPES[kind]
    return pi.parser_key(mime, ext, name, "FILE") or ""


class World:
    def __init__(
        self, rng: random.Random, p_old: float, *, known: Callable[[dict[str, Any]], str | None] = pi.known_identity,
        gate_attach: bool = True, gate_repoint: bool = True, carry_source: bool = True, copy_identity: bool = True,
    ) -> None:
        self.rng, self.p_old, self.known = rng, p_old, known
        self.gate_attach, self.gate_repoint, self.carry_source, self.copy_identity = gate_attach, gate_repoint, carry_source, copy_identity
        self.t = 0
        self.vid = 0
        self.copies: dict[str, str] = {}   # virtualRecordId -> the type whose parser built it
        self.recs: dict[str, dict[str, Any]] = {}
        self.queue: dict[str, list[tuple[str, str, int]]] = collections.defaultdict(list)
        self.wrong: collections.Counter[tuple[str, str]] = collections.Counter()
        self.skips: collections.Counter[tuple[str, str]] = collections.Counter()

    def tick(self) -> int:
        self.t += 1
        return self.t

    def new_copy(self, kind: str) -> str:
        self.vid += 1
        self.copies[f"v{self.vid}"] = kind
        return f"v{self.vid}"

    def graph_key(self, rec: dict[str, Any]) -> str | None:
        return pi.parser_key(*TYPES[rec["type"]], "FILE")

    def completed(self, rec: dict[str, Any], vrid: str, extra: dict[str, Any] | None = None) -> None:
        rec.update(status="COMPLETED", virtualRecordId=vrid, lastIndexTimestamp=self.tick())
        if extra:
            rec.update(extra)

    def fields(self, identity: str | None, source: str, rec: dict[str, Any]) -> dict[str, Any]:
        return pi.indexed_parser_fields(identity, source, at=rec["lastIndexTimestamp"], vrid=rec["virtualRecordId"])

    def note_skip(self, rec: dict[str, Any], process: str, state: str) -> None:
        self.skips[(process, state)] += 1
        if self.copies[rec["virtualRecordId"]] != rec["type"]:
            self.wrong[(process, state)] += 1

    def process(self, name: str) -> None:
        if not self.queue[name]:
            return
        kind, route, rev = self.queue[name].pop(0)
        rec = self.recs[name]
        old = self.rng.random() < self.p_old
        forced = kind == "force"
        graph = self.graph_key(rec)
        if not forced and rec["status"] == "COMPLETED" and rec["virtualRecordId"] and rec["rev"] == rev:
            if old:
                self.note_skip(rec, "old", "none")
                return
            known = self.known(rec)
            if known is not None:
                if pi.key_of(known) == graph:
                    self.note_skip(rec, "new", rec[pi.IDENTITY_SOURCE_FIELD])
                    return
            elif rec.get(pi.IDENTITY_FIELD) is None:
                self.note_skip(rec, "new", "absent")
                rec.update(self.fields(f"inproc:{graph}", pi.SOURCE_ADOPTED, rec) | {})
                return
        rec["rev"] = rev
        if not forced and self.attach(rec, name, rev, old, graph):
            return
        vrid = self.new_copy(route)
        self.completed(rec, vrid)
        if not old:
            rec.update(self.fields(f"inproc:{_key_of_type(route)}", pi.SOURCE_PARSED, rec))
        if forced:
            self.repoint(rec, name, rev, vrid, old)

    def attach(self, rec: dict[str, Any], name: str, rev: int, old: bool, graph: str | None) -> bool:
        for other_name, other in self.recs.items():
            if other_name == name or other["status"] != "COMPLETED" or other["rev"] != rev or not other["virtualRecordId"]:
                continue
            if old:
                self.completed(rec, other["virtualRecordId"])
                return True
            known = self.known(other)
            if self.gate_attach and not pi.twin_may_share(graph, other, self.graph_key(other) if known is None else None):
                continue
            self.completed(rec, other["virtualRecordId"])
            if known is not None and self.copy_identity:
                source = pi.source_for_copy(other.get(pi.IDENTITY_SOURCE_FIELD)) if self.carry_source else pi.SOURCE_TWIN
                rec.update(self.fields(known, source, rec))
            return True
        return False

    def repoint(self, rec: dict[str, Any], name: str, rev: int, vrid: str, old: bool) -> None:
        me = self.known(rec)
        for other_name, other in self.recs.items():
            if other_name == name or other["status"] != "COMPLETED" or other["rev"] != rev or other["virtualRecordId"] is None:
                continue
            if old or me is None:
                other["virtualRecordId"] = vrid   # pre-identity re-point: the copy moves, the timestamp does not
                continue
            theirs = self.known(other)
            if self.gate_repoint and (theirs is None or pi.key_of(theirs) != pi.key_of(me)):
                continue
            other["virtualRecordId"] = vrid
            if theirs is not None:
                source = pi.source_for_copy(rec.get(pi.IDENTITY_SOURCE_FIELD)) if self.carry_source else pi.SOURCE_TWIN
                other.update(self.fields(me, source, other))


def run(seed: int, steps: int = 60, *, p_old: float = 0.3, **options: Any) -> tuple[collections.Counter, collections.Counter]:  # noqa: ANN401
    rng = random.Random(seed)
    world = World(rng, p_old, **options)
    names = ["A", "B", "C"]
    for n in names:
        kind, content = rng.choice(list(TYPES)), rng.choice([1, 2])
        world.recs[n] = {"type": kind, "status": "NOT_STARTED", "rev": None, "virtualRecordId": None, "lastIndexTimestamp": None, "content": content}
        world.queue[n].append(("upd", kind, content))
    for _ in range(steps):
        n = rng.choice(names)
        rec = world.recs[n]
        op = rng.random()
        if op < 0.45:
            world.process(n)
        elif op < 0.65:
            world.queue[n].append(("upd", rec["type"], rec["content"]))
        elif op < 0.80:  # a rename: the connector resets the record and publishes an update
            rec["type"] = "md" if rec["type"] == "txt" else "txt"
            rec["status"] = "NOT_STARTED"
            world.queue[n].append(("upd", rec["type"], rec["content"]))
        elif op < 0.88:
            rec["content"] = rng.choice([1, 2])
            rec["status"] = "NOT_STARTED"
            world.queue[n].append(("upd", rec["type"], rec["content"]))
        else:
            world.queue[n].append(("force", rec["type"], rec["content"]))
    for n in names:
        while world.queue[n]:
            world.process(n)
    return world.wrong, world.skips


def total(seeds: range, **options: Any) -> tuple[collections.Counter, collections.Counter]:  # noqa: ANN401
    wrong: collections.Counter = collections.Counter()
    skips: collections.Counter = collections.Counter()
    for seed in seeds:
        w, s = run(seed, **options)
        wrong += w
        skips += s
    return wrong, skips


def known_wrong(wrong: collections.Counter) -> int:
    return sum(n for (_, state), n in wrong.items() if state in KNOWN_STATES)


def never_identified_wrong(wrong: collections.Counter) -> int:
    return sum(n for (_, state), n in wrong.items() if state in ("absent", "adopted"))


if __name__ == "__main__":
    import os

    os.environ["RECORD_PARSER_IDENTITY_WRITE"] = "true"
    for a, b in RANGES:
        for p_old in (0.0, 0.3):
            wrong, skips = total(range(a, b), p_old=p_old)
            print(f"seeds {a}-{b - 1} p_old={p_old}: known wrong {known_wrong(wrong)}, never-identified wrong {never_identified_wrong(wrong)}, skips {dict(skips)}, wrong {dict(wrong)}")

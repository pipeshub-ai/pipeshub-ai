"""A structural proof that a regex runs in linear time on a backtracking engine (SEC-C-01, SEC-C-06, SEC-C-17/18).

No clock is read: the rules look at the pattern's parse tree, the same tree Python's engine runs (and the sources
the shared vector ships to Node and the browser are the same strings). Three shapes make a backtracking engine
super-linear, and each is refused unless its guard is there:

* **nested**: an unbounded repeat inside an unbounded repeat, unless the inner one cannot run into the next pass
  (the outer body starts with a character the inner repeat excludes, as `(?:\\.[a-z]*)+`);
* **adjacent**: two repeats over overlapping characters with only optional items between them, which can split
  one run every way (`\\s*(.*?)`, `[ \\t]*[-*]?[ \\t]*`, a lazy `.*?` before `[ \\t]*$`);
* **restart**: a repeat over a class C that a match can reach having read only characters of C, followed by
  something that can fail. A match tried from the next position inside the run scans it again, so a run costs its
  square (`[.:!?]+$`, `eyJ[\\w-]+\\.`, `<[^>]{0,10000}>`). The guard is a leading negative lookbehind holding all
  of C, a tempered body that stops at the pattern's own opening literal, or a `^` that the run cannot cross.

A bounded repeat counts as unbounded past ``LARGE_BOUND``: `{0,10000}` costs ten thousand steps a start.
"""

from __future__ import annotations

import re
import re._compiler as sre_compile
import re._constants as sre
import re._parser as sre_parse
from dataclasses import dataclass
from typing import Any

LARGE_BOUND = 256
_SAMPLE = frozenset([chr(i) for i in range(128)] + ["é", " ", "•", "€", " ", "Ж"])


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    detail: str


def _chars(item: tuple[Any, Any], state: sre_parse.State) -> frozenset[str]:
    """The sample characters one single-character item matches."""
    sub = sre_parse.SubPattern(state, [item])
    compiled = sre_compile.compile(sub, state.flags)
    return frozenset(ch for ch in _SAMPLE if compiled.fullmatch(ch))


def _single(body: list[Any]) -> bool:
    return len(body) == 1 and body[0][0] in (sre.LITERAL, sre.NOT_LITERAL, sre.IN, sre.ANY)


def _tempered_on(body: list[Any], opening: list[Any]) -> bool:
    """`(?:(?!OPENING).)` : a run that ends where the pattern's own opening (its first items) matches again."""
    if len(body) != 2 or body[0][0] is not sre.ASSERT_NOT or not _single(body[1:]):
        return False
    direction, inner = body[0][1]
    inner = list(inner)
    return direction == 1 and bool(inner) and inner == opening[: len(inner)]


def _large(hi: int) -> bool:
    return hi is sre.MAXREPEAT or hi > LARGE_BOUND


def _nullable(item: tuple[Any, Any]) -> bool:
    op, av = item
    if op in (sre.ASSERT, sre.ASSERT_NOT, sre.AT):
        return True
    if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT):
        return av[0] == 0 or all(_nullable(i) for i in av[2])
    if op is sre.SUBPATTERN:
        return all(_nullable(i) for i in av[3])
    if op is sre.BRANCH:
        return any(all(_nullable(i) for i in b) for b in av[1])
    return False


def _leading_repeats(items: list[Any], state: sre_parse.State) -> list[tuple[frozenset[str], int]]:
    """The single-character repeats a sequence can start with, through items that may match nothing."""
    found: list[tuple[frozenset[str], int]] = []
    for item in items:
        op, av = item
        if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT):
            lo, hi, body = av
            if _single(body):
                if _large(hi):
                    found.append((_chars(body[0], state), lo))
            else:
                found.extend(_leading_repeats(body, state))
        elif op is sre.SUBPATTERN:
            found.extend(_leading_repeats(av[3], state))
        elif op is sre.BRANCH:
            for branch in av[1]:
                found.extend(_leading_repeats(branch, state))
        if not _nullable(item):
            break
    return found


def _check_sequence(items: list[Any], state: sre_parse.State, out: list[Violation]) -> None:
    for index, (op, av) in enumerate(items):
        if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT):
            lo, hi, body = av
            if _large(hi) and not _single(body) and op is not sre.POSSESSIVE_REPEAT:
                inner = _inner_repeats(body, state)
                # A pass that must read a character no inner repeat takes cannot be split another way.
                if inner and _fits(body, frozenset().union(*inner), state):
                    out.append(Violation("nested", sre_parse.SubPattern(state, [items[index]]).__repr__()[:80]))
            if _large(hi) and _single(body) and op is not sre.POSSESSIVE_REPEAT:
                mine = _chars(body[0], state)
                # Nothing after that can fail: the first split tried succeeds, and no other is tried.
                if not _can_fail(items[index + 1 :]):
                    continue
                for chars, _ in _leading_repeats(items[index + 1 :], state):
                    if mine & chars:
                        out.append(Violation("adjacent", f"two repeats share {sorted(mine & chars)[:6]}"))
                        break
            _check_sequence(body, state, out)
        elif op is sre.SUBPATTERN:
            # A group's last repeat meets whatever follows the group.
            _check_sequence([*av[3], *items[index + 1 :]], state, out)
        elif op is sre.BRANCH:
            for branch in av[1]:
                _check_sequence([*branch, *items[index + 1 :]], state, out)
        elif op in (sre.ASSERT, sre.ASSERT_NOT):
            _check_sequence(av[1], state, out)


def _inner_repeats(items: list[Any], state: sre_parse.State) -> list[frozenset[str]]:
    found: list[frozenset[str]] = []
    for op, av in items:
        if op in (sre.MAX_REPEAT, sre.MIN_REPEAT) and _large(av[1]):
            found.append(_all_chars(av[2], state))
        elif op is sre.SUBPATTERN:
            found.extend(_inner_repeats(av[3], state))
        elif op is sre.BRANCH:
            for branch in av[1]:
                found.extend(_inner_repeats(branch, state))
    return found


def _all_chars(items: list[Any], state: sre_parse.State) -> frozenset[str]:
    chars: set[str] = set()
    for item in items:
        op, av = item
        if op in (sre.LITERAL, sre.NOT_LITERAL, sre.IN, sre.ANY):
            chars |= _chars(item, state)
        elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT):
            chars |= _all_chars(av[2], state)
        elif op is sre.SUBPATTERN:
            chars |= _all_chars(av[3], state)
        elif op is sre.BRANCH:
            for branch in av[1]:
                chars |= _all_chars(branch, state)
    return frozenset(chars)




def _fits(items: list[Any], chars: frozenset[str], state: sre_parse.State) -> bool:
    """Whether some way through the mandatory part of ``items`` reads only ``chars``."""
    for item in items:
        op, av = item
        if _nullable(item):
            continue
        if op in (sre.LITERAL, sre.NOT_LITERAL, sre.IN, sre.ANY):
            if not _chars(item, state) <= chars:
                return False
        elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT, sre.SUBPATTERN):
            body = av[2] if op is not sre.SUBPATTERN else av[3]
            if not _fits(body, chars, state):
                return False
        elif op is sre.BRANCH and not any(_fits(branch, chars, state) for branch in av[1]):
            return False
    return True


def _can_fail(items: list[Any]) -> bool:
    for op, av in items:
        if op is sre.AT:
            if av in (sre.AT_END, sre.AT_END_STRING, sre.AT_BOUNDARY, sre.AT_NON_BOUNDARY, sre.AT_END_LINE):
                return True
            continue
        if op in (sre.ASSERT, sre.ASSERT_NOT):
            return True
        if not _nullable((op, av)):
            return True
    return False




def _ends_inside(rest: list[Any], chars: frozenset[str], state: sre_parse.State) -> bool:
    """`[\\w.-]*\\w` and nothing after that can fail: the run backs off to its last such character, and a start
    later in the run finds no later one either, so only one start per run fails."""
    for position, item in enumerate(rest):
        if _nullable(item):
            continue
        single = item[0] in (sre.LITERAL, sre.NOT_LITERAL, sre.IN, sre.ANY)
        return single and _chars(item, state) <= chars and not _can_fail(rest[position + 1 :])
    return False


def _flatten(items: list[Any]) -> list[Any]:
    flat: list[Any] = []
    for op, av in items:
        if op is sre.SUBPATTERN:
            flat.extend(_flatten(av[3]))
        else:
            flat.append((op, av))
    return flat


def _check_restart(items: list[Any], state: sre_parse.State, out: list[Violation]) -> None:
    flat = _flatten(items)
    guard: frozenset[str] = frozenset()
    start = 0
    multiline = bool(state.flags & sre.SRE_FLAG_MULTILINE)
    anchored = False
    if flat and flat[0][0] is sre.AT and flat[0][1] in (sre.AT_BEGINNING, sre.AT_BEGINNING_STRING):
        anchored = True
        start = 1
        if flat[0][1] is sre.AT_BEGINNING_STRING or not multiline:
            return
    while start < len(flat) and (
        (flat[start][0] is sre.ASSERT_NOT and flat[start][1][0] == -1)
        or (flat[start][0] is sre.AT and flat[start][1] is sre.AT_BOUNDARY)
    ):
        if flat[start][0] is sre.AT:
            # `\b` before a word character: no start inside a run of word characters.
            guard |= frozenset(ch for ch in _SAMPLE if re.fullmatch(r"\w", ch))
        else:
            guard |= _all_chars(flat[start][1][1], state)
        start += 1
    opening = flat[start:]
    for index in range(start, len(flat)):
        op, av = flat[index]
        if op not in (sre.MAX_REPEAT, sre.MIN_REPEAT) or not _large(av[1]):
            continue
        body = av[2]
        if _tempered_on(body, opening):
            continue
        chars = _all_chars(body, state)
        if anchored and "\n" not in chars:
            continue
        if not _fits(flat[start:index], chars, state) or not _can_fail(flat[index + 1 :]):
            continue
        if _ends_inside(flat[index + 1 :], chars, state):
            continue
        if not chars <= guard:
            out.append(Violation("restart", f"a run of {sorted(chars - guard)[:6]} is rescanned from each position"))


def violations(source: str, flags: int = 0) -> list[Violation]:
    """Every super-linear shape in ``source``; empty when the pattern is linear by these rules."""
    parsed = sre_parse.parse(source, flags)
    out: list[Violation] = []
    _check_sequence(list(parsed.data), parsed.state, out)
    _check_restart(list(parsed.data), parsed.state, out)
    return out


def flags_of(text: str) -> int:
    return (re.IGNORECASE if "i" in text else 0) | (re.MULTILINE if "m" in text else 0) | (re.DOTALL if "s" in text else 0)

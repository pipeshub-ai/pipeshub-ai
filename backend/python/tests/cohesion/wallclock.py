"""Finds assertions that hold a host-clock duration to a fixed bound (WALLCLOCK-1).

A delta of ``time.monotonic()``, ``perf_counter()``, ``time.time()`` or a CPU clock also holds whatever kept the
process off the CPU meanwhile: a full gate on the shared host, a collection of a large heap. Compared to a fixed
number, it fails on the host and passes alone. A test asserts the code's own clock (``ManualClock``, a
``VirtualTimeLoop``), an order or a count instead; where a real-time bound is the point it lives in a ``slow``
gate run (C-25), or carries ``# wall-clock-ok: <reason>`` on the assertion's line or the line above it.

The scan follows names through assignments within a module: ``start = time.monotonic()`` makes ``start`` a reading,
``elapsed = time.monotonic() - start`` makes ``elapsed`` a duration, and so do values built from a duration
(``samples.append(elapsed)``, ``p95 = sorted(samples)[-1]``). A comparison of two durations is relative and passes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

_CLOCKS = frozenset({
    "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "time", "time_ns", "process_time", "thread_time",
})
_UNITTEST_ORDER = frozenset({"assertLess", "assertLessEqual", "assertGreater", "assertGreaterEqual"})
# A wait that expects completion within this is a bound on the host; one at or above it is a hang backstop.
SHORT_WAIT_S = 2.0
OPT_OUT = re.compile(r"(?:#|//)\s*wall-clock-ok:\s*(?P<reason>\S.{9,})")


@dataclass(frozen=True)
class Finding:
    line: int
    source: str


def _clock_names(tree: ast.AST, text: str) -> set[str]:
    """Names bound to a clock by ``from time import monotonic`` (or ``as``)."""
    found: set[str] = set()
    for node in ast.walk(tree) if "from time import" in text else ():
        if isinstance(node, ast.ImportFrom) and node.module == "time":
            found.update(alias.asname or alias.name for alias in node.names if alias.name in _CLOCKS)
    return found


_LOOP_LOOKUPS = frozenset({"get_running_loop", "get_event_loop"})
LOOP_CLOCK = "<loop>"


def _is_loop(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return "loop" in node.id
    if isinstance(node, ast.Attribute):
        return "loop" in node.attr
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LOOP_LOOKUPS


def _is_clock_call(node: ast.AST, bare: set[str]) -> bool:
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in bare
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "time" and LOOP_CLOCK in bare and _is_loop(func.value):
        return True
    return func.attr in _CLOCKS and isinstance(func.value, ast.Name) and func.value.id in {"time", "_time"}


def _targets(node: ast.AST) -> list[str]:
    """The names a binding rebinds: ``a``, ``a, b`` or ``*a``; ``x.attr = …`` and ``x[k] = …`` rebind nothing."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for element in node.elts for name in _targets(element)]
    if isinstance(node, ast.Starred):
        return _targets(node.value)
    return []


def _walk_values(node: ast.AST) -> list[ast.AST]:
    """``ast.walk`` that does not look inside ``len(…)``: the size of a list of durations is a count."""
    found: list[ast.AST] = []
    stack = [node]
    while stack:
        current = stack.pop()
        found.append(current)
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name) and current.func.id == "len":
            continue
        stack.extend(ast.iter_child_nodes(current))
    return found


# A module's functions that return a duration, by name: ``None`` for a duration itself, or which positions of the
# tuple it returns are one (``elapsed, world, children = await _cascade()`` makes only ``elapsed`` a duration).
Helpers = dict[str, "tuple[bool, ...] | None"]


def _called_helper(node: ast.AST, helpers: Helpers) -> str | None:
    if isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in helpers:
        return node.func.id
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"self", "cls"}
        and node.func.attr in helpers
    ):
        return node.func.attr
    return None


class _Taint:
    def __init__(self, scope: ast.AST, bare: set[str], helpers: Helpers | None = None) -> None:
        self._bare = bare
        self._helpers: Helpers = helpers or {}
        self.readings: set[str] = set()
        self.durations: set[str] = set()
        self._propagate(scope)

    def _names(self, node: ast.AST) -> set[str]:
        return {n.id for n in _walk_values(node) if isinstance(n, ast.Name)}

    def reading(self, node: ast.AST) -> bool:
        """A point in time: a clock call, a name holding one, or one shifted by a constant (``now - 3``)."""
        return any(_is_clock_call(n, self._bare) for n in _walk_values(node)) or bool(self._names(node) & self.readings)

    def duration(self, node: ast.AST) -> bool:
        """The difference of two points in time, or a value built from one."""
        for n in _walk_values(node):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub) and self.reading(n.left) and self.reading(n.right):
                return True
            if _called_helper(n, self._helpers):
                return True
        return bool(self._names(node) & self.durations)

    def _unpacks(self, node: ast.Assign) -> bool:
        """``a, b = x, y`` and ``a, b = helper()`` bind position by position; True when handled here."""
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Tuple):
            return False
        names = node.targets[0].elts
        helper = _called_helper(node.value, self._helpers)
        flags = self._helpers.get(helper) if helper else None
        if isinstance(flags, tuple) and len(flags) == len(names):
            self.durations.update(n.id for n, flag in zip(names, flags, strict=True) if flag and isinstance(n, ast.Name))
            return True
        if isinstance(node.value, ast.Tuple) and len(node.value.elts) == len(names):
            self._pairs.extend((_targets(n), v) for n, v in zip(names, node.value.elts, strict=True))
            return True
        return False

    def _propagate(self, scope: ast.AST) -> None:
        self._pairs: list[tuple[list[str], ast.AST]] = []
        bindings: list[tuple[list[str], ast.AST]] = []
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and self._unpacks(node):
                continue
            if isinstance(node, ast.Assign):
                bindings.append(([name for t in node.targets for name in _targets(t)], node.value))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                bindings.append((_targets(node.target), node.value))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                bindings.append((_targets(node.target), node.iter))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend", "add", "insert"}
                and isinstance(node.func.value, ast.Name)
            ):
                bindings.extend(([node.func.value.id], arg) for arg in node.args)
        bindings.extend(self._pairs)
        changed = True
        while changed:
            changed = False
            for targets, value in bindings:
                if self.duration(value):
                    kind = self.durations
                elif self.reading(value):
                    kind = self.readings
                else:
                    continue
                new = set(targets) - kind
                kind |= new
                changed |= bool(new)


def _sides(node: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
    """Each ordering in ``node`` as (the side held below, the bound)."""
    pairs: list[tuple[ast.AST, ast.AST]] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Compare):
            operands = [n.left, *n.comparators]
            for i, op in enumerate(n.ops):
                if isinstance(op, (ast.Lt, ast.LtE)):
                    pairs.append((operands[i], operands[i + 1]))
                elif isinstance(op, (ast.Gt, ast.GtE)):
                    pairs.append((operands[i + 1], operands[i]))
    return pairs


def _slow(node: ast.AST) -> bool:
    """A ``slow`` test is a named gate run where a real-time bound is the point (C-25)."""
    decorators = getattr(node, "decorator_list", [])
    return any("mark.slow" in ast.unparse(d) for d in decorators)


def _module_is_slow(tree: ast.Module) -> bool:
    return any(
        isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets)
        and "mark.slow" in ast.unparse(stmt.value)
        for stmt in tree.body
    )


def _scopes(tree: ast.Module) -> list[ast.AST]:
    """Each top-level function (methods included) is its own scope, so a ``start`` that is a clock reading in one
    test is not one in the next; nested functions share their parent's names, as closures do."""
    if _module_is_slow(tree):
        return []
    scopes: list[ast.AST] = []
    rest: list[ast.stmt] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _slow(stmt):
                scopes.append(stmt)
        elif isinstance(stmt, ast.ClassDef):
            if not _slow(stmt):
                scopes.extend(
                    s for s in stmt.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and not _slow(s)
                )
        else:
            rest.append(stmt)
    scopes.append(ast.Module(body=rest, type_ignores=[]))
    return scopes


# A file none of whose text reads a clock has no duration to hold: the scan below skips it.
_CLOCK_TEXT = re.compile(r"\b(?:" + "|".join(sorted(_CLOCKS - {"time"})) + r")\b|\btime\(\)")


def _orders(scope: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Compare) and any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in n.ops))
        or (isinstance(n, ast.Attribute) and n.attr in _UNITTEST_ORDER)
        for n in ast.walk(scope)
    )


def findings(text: str) -> list[Finding]:
    if not (_CLOCK_TEXT.search(text) or "wait_for" in text):
        return []
    tree = ast.parse(text)
    lines = text.split("\n")
    found: list[Finding] = []
    bare = _clock_names(tree, text)
    # On a real loop ``loop.time()`` is the host's monotonic clock; on a ``VirtualTimeLoop`` (or a loop that
    # ``tests.support.loop_clock.virtual_loop_time`` drives) it is the code's own, and a ``wait_for`` takes no wall time.
    real_loop = not any(v in text for v in ("VirtualTimeLoop", "on_virtual_time", "virtual_loop_time"))
    scopes = _scopes(tree)
    if real_loop:
        bare.add(LOOP_CLOCK)
        if "wait_for" in text:
            found.extend(w for scope in scopes for w in _short_waits(scope))
    if _CLOCK_TEXT.search(text):
        helpers = _duration_helpers(tree, bare)
        for scope in scopes:
            if _orders(scope):
                found.extend(_scope_findings(scope, _Taint(scope, bare, helpers), lines))
    return sorted(
        (f for f in found if not (OPT_OUT.search(lines[f.line - 1]) or (f.line > 1 and OPT_OUT.search(lines[f.line - 2])))),
        key=lambda f: f.line,
    )


def _duration_helpers(tree: ast.AST, bare: set[str]) -> Helpers:
    """Functions of the module that return a duration (``closed_within(reader)``): a call to one is a duration."""
    found: Helpers = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name in found:
                continue
            calls = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "") for n in ast.walk(node)
                     if isinstance(n, ast.Call)}
            if not (calls & (_CLOCKS | bare | {"time"}) or calls & set(found)) or not any(
                isinstance(r, ast.Return) and r.value is not None for r in ast.walk(node)
            ):
                continue
            taint = _Taint(node, bare, dict(found))
            returns = [r.value for r in ast.walk(node) if isinstance(r, ast.Return) and r.value is not None]
            tuples = [r for r in returns if isinstance(r, ast.Tuple)]
            if tuples and len(tuples) == len(returns) and len({len(t.elts) for t in tuples}) == 1:
                flags = tuple(any(taint.duration(t.elts[i]) for t in tuples) for i in range(len(tuples[0].elts)))
                if any(flags):
                    found[node.name] = flags
                    changed = True
            elif any(taint.duration(r) for r in returns):
                found[node.name] = None
                changed = True
    return found


def _timeout_handler(node: ast.AST) -> bool:
    text = ast.unparse(node)
    return "Timeout" in text or "CancelledError" in text


def _expects_a_timeout(tree: ast.AST) -> set[int]:
    """Nodes inside ``pytest.raises(TimeoutError)``, ``suppress(TimeoutError)`` or ``try: … except TimeoutError``:
    a wait there asserts that something does not finish, which a slow host cannot make fail."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        bodies: list[ast.AST] = []
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(
            _timeout_handler(item.context_expr) for item in node.items
        ):
            bodies = [node]
        elif isinstance(node, ast.Try) and any(h.type is not None and _timeout_handler(h.type) for h in node.handlers):
            bodies = list(node.body)
        for body in bodies:
            inside.update(id(n) for n in ast.walk(body))
    return inside


def _short_waits(tree: ast.AST) -> list[Finding]:
    """``asyncio.wait_for(x, 1)`` that expects ``x`` to finish: the bound is the host's (use ``settle``)."""
    negative = _expects_a_timeout(tree)
    found: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and ast.unparse(node.func) in {"asyncio.wait_for", "wait_for"}):
            continue
        if id(node) in negative or len(node.args) < 1:
            continue
        bound = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "timeout"), None)
        if isinstance(bound, ast.Constant) and isinstance(bound.value, (int, float)) and bound.value < SHORT_WAIT_S:
            found.append(Finding(node.lineno, f"wait_for(…, {bound.value}): {ast.unparse(node)[:80]}"))
    return found


def _inverted(node: ast.AST, taint: _Taint) -> bool:
    """``pages / elapsed``: a rate, which a longer duration makes smaller."""
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, (ast.Div, ast.FloorDiv))
        and taint.duration(node.right)
        and not taint.duration(node.left)
    )


def _held_below(low: ast.AST, high: ast.AST, taint: _Taint) -> bool:
    """Whether ``low <= high`` caps a duration from above."""
    if taint.duration(low) and taint.duration(high):
        return False
    return (taint.duration(low) and not _inverted(low, taint)) or _inverted(high, taint)


def _scope_findings(scope: ast.AST, taint: _Taint, lines: list[str]) -> list[Finding]:
    found: list[Finding] = []
    for node in ast.walk(scope):
        pairs: list[tuple[ast.AST, ast.AST]] = []
        if isinstance(node, ast.Assert):
            pairs = _sides(node.test)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _UNITTEST_ORDER
            and len(node.args) >= 2
        ):
            low, high = node.args[0], node.args[1]
            pairs = [(low, high) if "Less" in node.func.attr else (high, low)]
        # A load can only lengthen a duration, so a lower bound (``waited >= 1``) cannot fail on the host.
        if not any(_held_below(low, high, taint) for low, high in pairs):
            continue
        found.append(Finding(node.lineno, lines[node.lineno - 1].strip()))
    return found


# Node (mocha + chai): the same rule over ``Date.now()``, ``performance.now()`` and ``process.hrtime``.
_NODE_CLOCK = r"(?:Date\.now\(\)|performance\.now\(\)|process\.hrtime\.bigint\(\)|process\.hrtime\(\))"
_NODE_BINDING = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*([^\n;]+)")
_NODE_UPPER = re.compile(
    r"\s*\.to(?:\.not)?(?:\.be)?(?:\.a\('number'\)\.and(?:\.to)?(?:\.be)?)?"
    r"\.(?:below|lessThan|lt|lte|most|at\.most|within|lessThanOrEqual)\("
    r"|\s*\.toBeLessThan(?:OrEqual)?\("
)


def _balanced(text: str, start: int) -> int:
    """The index just past the parenthesis that closes the one at ``start``."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


_NODE_FUNCTION = re.compile(
    r"\bfunction\s+(\w+)\s*\(|\b(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^()]*\)|\w+)\s*(?::[^=\n]+)?=>"
)


def _node_helpers(text: str) -> dict[str, str]:
    """Local helpers by name, with their bodies: one that returns a clock delta (``elapsedMs(run)``) is a duration."""
    helpers: dict[str, str] = {}
    for match in _NODE_FUNCTION.finditer(text):
        name = match.group(1) or match.group(2)
        brace = text.find("{", match.end())
        newline = text.find("\n", match.end())
        if brace == -1 or (newline != -1 and brace > newline + 200):
            helpers[name] = text[match.end() : newline if newline != -1 else len(text)]
            continue
        depth = 0
        for i in range(brace, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                helpers[name] = text[brace : i + 1]
                break
    return helpers


def node_findings(text: str) -> list[Finding]:
    # sinon's fake timers fake ``Date`` and ``performance`` too: a delta in such a file is on the test's clock.
    if "useFakeTimers" in text or not re.search(_NODE_CLOCK, text):
        return []
    readings: set[str] = set()
    durations: set[str] = set()

    def delta(expr: str) -> bool:
        operand = r"(?:" + _NODE_CLOCK + r"|\b(?:" + "|".join(map(re.escape, readings or {"\0"})) + r")\b)"
        return bool(re.search(operand + r"\)?\s*-\s*\(?\s*" + operand, expr))

    def duration(expr: str) -> bool:
        return (
            delta(expr)
            or any(re.search(rf"\b{re.escape(d)}\b", expr) for d in durations)
            or any(re.search(rf"\b{re.escape(h)}\(", expr) for h, body in helpers.items() if delta(body))
        )

    bindings = _NODE_BINDING.findall(text)
    helpers = _node_helpers(text)
    changed = True
    while changed:
        changed = False
        for name, value in bindings:
            if duration(value) and name not in durations:
                durations.add(name)
                changed = True
            elif re.search(_NODE_CLOCK, value) and not duration(value) and name not in readings:
                readings.add(name)
                changed = True
    if not readings and not durations and not re.search(_NODE_CLOCK, text):
        return []
    lines = text.split("\n")
    found: list[Finding] = []
    for match in re.finditer(r"\bexpect\(|\bassert(?:\.ok)?\(|\bassert\.isBelow\(", text):
        open_at = match.end() - 1
        close_at = _balanced(text, open_at)
        subject = text[open_at + 1 : close_at - 1]
        if match.group().startswith("expect"):
            if not (_NODE_UPPER.match(text, close_at) and duration(subject)):
                continue
        elif match.group() == "assert.isBelow(":
            if not duration(subject.split(",")[0]):
                continue
        else:
            bound = re.split(r"\s*<=?\s*", subject, maxsplit=1)
            if len(bound) != 2 or not duration(bound[0]) or duration(bound[1]):
                continue
        line = text.count("\n", 0, match.start()) + 1
        if OPT_OUT.search(lines[line - 1]) or (line > 1 and OPT_OUT.search(lines[line - 2])):
            continue
        found.append(Finding(line, lines[line - 1].strip()))
    return found

"""Check the integration-test shards: every connector runs once, and the shards stay balanced.

The nightly run splits connector suites across CONN_SHARD_1..N in
`.github/workflows/integration-tests.yml`; the `core` shard is everything those
do not name. Connector tests are also marked `integration`, so a connector left
out of every shard is not skipped — it falls into `core`, quietly making that
shard longer, which is the imbalance the split exists to avoid. A CONN_SHARD_N
with no matching `connectors-N` job is worse: `core` excludes it and no job
selects it, so those tests really do stop running. A shard that grows far past
the others decides how long the whole nightly takes.

Run: python3 scripts/shard_balance.py --check
Rebalance: move markers between the CONN_SHARD_* lines until this reports even
shards, using the measured minutes in scripts/shard_durations.json. Refresh
those minutes from a recent nightly's reports-both-<shard> artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/integration-tests.yml"
PYTEST_INI = REPO / "integration-tests/pytest.ini"
DURATIONS = REPO / "scripts/shard_durations.json"
SIM_TIERS = REPO / "integration-tests/sim_tiers.json"

# How far the longest shard may sit above the average before this fails. The
# nightly ends when the slowest shard ends, so drift here is wasted wall clock.
MAX_OVER_MEAN = 1.35

# Simulator shards (integration-tests/sim_tiers.json) hold only simulator suites, so the live suites can never move
# onto them to even things out: counting them in the average would fail the live shards for a small simulator shard
# nobody touched. They are left out of the average and held to their own ceiling instead: both graph legs of a shard
# (the measured minutes are summed across both) plus about 30 minutes of stack start-up must fit the job's 300.
SIM_SHARD_MAX_MINUTES = 250.0

# CIFS is not registered yet, and Snowflake needs a live account the repository
# does not hold, so both stay out of nightly shards and core.
HELD_OUT_CONNECTORS = frozenset({"cifs", "snowflake"})

# Suites with a job of their own, named after their marker. "demo" must start on
# a stack no other suite has touched; "ai_agents" costs model calls, so its job
# runs on the nightly only. Core must leave them out, or they run twice.
SOLO_SHARDS = ("demo", "ai_agents")

_SHARD_LINE = re.compile(r'^\s*CONN_SHARD_(\d+):\s*"([^"]*)"\s*$', re.MULTILINE)
_CORE_MARKER_LINE = re.compile(
    r'^[ \t]*core\)[ \t]+MARKERS="([^"]*)"',
    re.MULTILINE,
)
_MARKER_LINE = re.compile(r"^\s{4}(\w+):\s*(.+)$")
_MATRIX_LINE = re.compile(r"^\s*shard:\s.*$", re.MULTILINE)
_MATRIX_SHARD = re.compile(r'"(connectors-\d+)"')
_MATRIX_ANY_SHARD = re.compile(r'"([\w-]+)"')
# The matrix line's single-quoted JSON lists; the last is the default (full-run) one.
_MATRIX_LIST = re.compile(r"'(\[[^']*\])'")
# One per test step (Neo4j, ArangoDB): the shell `case` that picks the markers.
_SHARD_CASE_BLOCK = re.compile(r'case "\$\{?SHARD\}?" in\n(.*?)\n[ \t]*esac', re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_Marker = tuple


def _marker_tokens(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(expression):
        if expression[index].isspace():
            index += 1
            continue
        if expression[index] in "()":
            tokens.append((expression[index], expression[index]))
            index += 1
            continue
        word = _IDENT.match(expression, index)
        if word is None:
            raise ValueError(expression[index:])
        text = word.group(0)
        kind = text if text in {"and", "or", "not"} else "id"
        tokens.append((kind, text))
        index = word.end()
    return tokens


def _parse_marker_expr(expression: str) -> _Marker:
    """Pytest ``-m`` expression. ``and`` binds tighter than ``or``."""
    tokens = _marker_tokens(expression)
    pos = 0

    def peek() -> str:
        return tokens[pos][0] if pos < len(tokens) else ""

    def eat(kind: str) -> tuple[str, str]:
        nonlocal pos
        if peek() != kind:
            raise ValueError(kind)
        token = tokens[pos]
        pos += 1
        return token

    def parse_or() -> _Marker:
        node = parse_and()
        while peek() == "or":
            eat("or")
            node = ("or", node, parse_and())
        return node

    def parse_and() -> _Marker:
        node = parse_not()
        while peek() == "and":
            eat("and")
            node = ("and", node, parse_not())
        return node

    def parse_not() -> _Marker:
        if peek() == "not":
            eat("not")
            return ("not", parse_not())
        return parse_primary()

    def parse_primary() -> _Marker:
        if peek() == "(":
            eat("(")
            node = parse_or()
            eat(")")
            return node
        if peek() == "id":
            return ("id", eat("id")[1])
        raise ValueError(peek() or "end")

    if not tokens:
        raise ValueError("empty")
    tree = parse_or()
    if pos != len(tokens):
        raise ValueError("trailing")
    return tree


def _marker_names(tree: _Marker) -> set[str]:
    kind = tree[0]
    if kind == "id":
        return {tree[1]}
    if kind == "not":
        return _marker_names(tree[1])
    return _marker_names(tree[1]) | _marker_names(tree[2])


def _eval_marker(tree: _Marker, env: dict[str, bool]) -> bool:
    kind = tree[0]
    if kind == "id":
        return env.get(tree[1], False)
    if kind == "not":
        return not _eval_marker(tree[1], env)
    left = _eval_marker(tree[1], env)
    right = _eval_marker(tree[2], env)
    return left or right if kind == "or" else left and right


def _terms(tree: _Marker, kind: str) -> list[_Marker]:
    """The operands of a chain of ``kind`` ("and" or "or") at the top of ``tree``."""
    if tree[0] == kind:
        return _terms(tree[1], kind) + _terms(tree[2], kind)
    return [tree]


def _always_excludes(expression: str, name: str) -> bool:
    """True when no marker assignment that includes ``name`` can match."""
    try:
        tree = _parse_marker_expr(expression)
    except ValueError:
        return False
    # The shape every core line has: "a and not (b or name ...) and not c ...". A top-level "not" over a disjunction
    # that names ``name`` is false whenever ``name`` is set, so the whole conjunction is, however many names it holds.
    for term in _terms(tree, "and"):
        if term[0] == "not" and ("id", name) in _terms(term[1], "or"):
            return True
    others = sorted(_marker_names(tree) - {name})
    # Any other shape: enumerate the assignments. Past this many names, fail closed.
    if len(others) > 16:
        return False
    for mask in range(1 << len(others)):
        env = {var: bool(mask & (1 << index)) for index, var in enumerate(others)}
        env[name] = True
        if _eval_marker(tree, env):
            return False
    return True


def matrix_shards(workflow_text: str) -> set[str]:
    """The connector shard jobs the matrix actually runs."""
    line = _MATRIX_LINE.search(workflow_text)
    return set(_MATRIX_SHARD.findall(line.group(0))) if line else set()


def matrix_solo_shards(workflow_text: str) -> set[str]:
    """The solo shard jobs (see SOLO_SHARDS) a full run's matrix runs.

    Only the default list counts: a name in the one-marker dispatch list alone
    would not run on the nightly.
    """
    line = _MATRIX_LINE.search(workflow_text)
    lists = _MATRIX_LIST.findall(line.group(0)) if line else []
    if not lists:
        return set()
    return set(_MATRIX_ANY_SHARD.findall(lists[-1])) & set(SOLO_SHARDS)


_IT_ALL_LIST = re.compile(r"'it-all'\)\s*&&\s*'(\[[^']*\])'")
_CASE_SHARD_LINE = re.compile(r'^\s*(connectors-\d+)\)\s+MARKERS="\$CONN_SHARD_(\d+)"', re.MULTILINE)
_SHARD_VARIABLE = re.compile(r"\$CONN_SHARD_(\d+)")


def _expand_shards(expression: str, shards: dict[str, list[str]]) -> str:
    """``$CONN_SHARD_N`` replaced by that shard's markers, as the shell will."""
    lists = {shard.rsplit("-", 1)[-1]: names for shard, names in shards.items()}
    return _SHARD_VARIABLE.sub(
        lambda match: "(" + (" or ".join(lists.get(match.group(1), [])) or "__no_marker__") + ")", expression
    )


def _core_selects(expression: str, marker: str, shards: dict[str, list[str]]) -> bool:
    """Whether a core expression can select a test of ``marker``'s suite, with any other markers it carries
    (``sim_core``, ``integration``, ...). Fails closed: an expression this cannot read selects it."""
    return not _always_excludes(_expand_shards(expression, shards), marker)


def _wiring_problems(workflow_text: str, shards: dict[str, list[str]]) -> list[str]:
    """What every CONN_SHARD needs besides its line, or its tests run twice or not at all on some event.

    - Both test steps' ``core`` expressions leave its suites out; otherwise core runs them a second time.
    - A test step that maps connector shards maps it too; otherwise that job stops on "Unknown shard".
    - The full-run list (the nightly, a full dispatch) and the it-all list run its job; otherwise its suites run on
      no nightly, or on no it-all pull request, while core still leaves them out.
    """
    problems: list[str] = []
    steps = [block for block in _SHARD_CASE_BLOCK.findall(workflow_text) if _CORE_MARKER_LINE.search(block)]
    # Counted only once some step is read as a case block, the form the workflow uses: then a core line outside
    # one is a step this check cannot see (a case statement spelled another way).
    unread = len(_CORE_MARKER_LINE.findall(workflow_text)) - len(steps) if steps else 0
    if unread > 0:
        problems.append(
            f"{unread} core line(s) sit outside a `case \"$SHARD\" in ... esac` block this check can read, so their "
            f"step goes unchecked. Write the step's case statement in that form."
        )
    for index, block in enumerate(steps, start=1):
        core = _CORE_MARKER_LINE.search(block).group(1)
        for shard, names in sorted(shards.items()):
            for name in names:
                if _core_selects(core, name, shards):
                    problems.append(
                        f"Test step {index}'s core expression selects '{name}' ({shard}), so core runs that suite "
                        f"a second time. Exclude its CONN_SHARD there."
                    )
        mapped = {shard: number for shard, number in _CASE_SHARD_LINE.findall(block)}
        if mapped:
            for shard in sorted(shards):
                if mapped.get(shard) != shard.rsplit("-", 1)[-1]:
                    problems.append(
                        f"Test step {index} has no `{shard}) MARKERS=\"$CONN_SHARD_{shard.rsplit('-', 1)[-1]}\"` line, "
                        f"so that job stops on 'Unknown shard'."
                    )
    line = _MATRIX_LINE.search(workflow_text)
    text = line.group(0) if line else ""
    lists = _MATRIX_LIST.findall(text)
    full = set(json.loads(lists[-1])) if lists else set()
    it_all = _IT_ALL_LIST.search(text)
    labelled = set(json.loads(it_all.group(1))) if it_all else set()
    for shard in sorted(shards):
        if lists and shard not in full:
            problems.append(f"'{shard}' is not in the matrix's full-run list, so the nightly never runs its suites.")
        if it_all and shard not in labelled:
            problems.append(f"'{shard}' is not in the it-all list, so an it-all pull request never runs its suites.")
    return problems


def _solo_shard_problems(workflow_text: str, core_expressions: list[str]) -> list[str]:
    problems: list[str] = []
    jobs = matrix_solo_shards(workflow_text)
    for name in SOLO_SHARDS:
        excluded = bool(core_expressions) and all(
            _always_excludes(expression, name) for expression in core_expressions
        )
        if name in jobs:
            if not excluded:
                problems.append(
                    f"The matrix runs a '{name}' job, but a core job still selects the "
                    f"'{name}' marker, so those tests run twice, once on core's shared stack."
                )
            steps = [block for block in _SHARD_CASE_BLOCK.findall(workflow_text) if _CORE_MARKER_LINE.search(block)]
            # Through `;;`: `MARKERS="demo"extra` is the marker "demoextra" to the shell.
            case_line = re.compile(rf'^[ \t]*{name}\)[ \t]+MARKERS="{name}"[ \t]*;;', re.MULTILINE)
            if not steps or any(len(case_line.findall(block)) != 1 for block in steps):
                problems.append(
                    f"The matrix runs a '{name}' job, but not every test step's `case \"$SHARD\"` "
                    f'has exactly one `{name}) MARKERS="{name}"` line, so a step fails as an '
                    f"unknown shard (or the lines disagree)."
                )
        elif core_expressions and any(
            _always_excludes(expression, name) for expression in core_expressions
        ):
            problems.append(
                f"Core leaves out the '{name}' marker, but the matrix has no '{name}' job, "
                f"so those tests stop running."
            )
    return problems


def shard_markers(workflow_text: str) -> dict[str, list[str]]:
    """Marker names per CONN_SHARD_*, in the order the workflow lists them."""
    shards: dict[str, list[str]] = {}
    for number, expression in _SHARD_LINE.findall(workflow_text):
        shards[f"connectors-{number}"] = [
            part.strip() for part in expression.split(" or ") if part.strip()
        ]
    return shards


def declared_markers(pytest_ini_text: str) -> dict[str, str]:
    """Every marker declared in pytest.ini, mapped to its description."""
    markers: dict[str, str] = {}
    inside = False
    for line in pytest_ini_text.splitlines():
        if line.startswith("markers"):
            inside = True
            continue
        if inside:
            if line.strip().startswith("#"):
                continue
            match = _MARKER_LINE.match(line)
            if not match:
                if line.strip() and not line.startswith(" "):
                    break
                continue
            markers[match.group(1)] = match.group(2).strip()
    return markers


def connector_markers(markers: dict[str, str]) -> set[str]:
    """Markers for a single connector's suite, which must live in exactly one shard.

    Picked out by their description: every connector marker in pytest.ini says
    so ("marks tests specific to the X connector"). A suite that is not tied to
    one connector (cleanup, retrieval, oauth_*, ...) belongs to `core`, which
    takes whatever the shards do not name.
    """
    return {
        name
        for name, description in markers.items()
        if "connector" in description.lower()
    }


def sim_shards(path: Path = SIM_TIERS) -> frozenset[str]:
    """The simulator shards named in integration-tests/sim_tiers.json (none when the file is absent)."""
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(path.read_text(encoding="utf-8"))["sim_shards"])


def check(
    workflow_text: str,
    pytest_ini_text: str,
    durations: dict[str, float],
    core_minutes: float | None = None,
    simulator_shards: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Return (problems, report lines). Problems empty means the split is sound."""
    problems: list[str] = []
    report: list[str] = []

    shards = shard_markers(workflow_text)
    if not shards:
        return (["No CONN_SHARD_* lines found in the workflow."], report)

    markers = declared_markers(pytest_ini_text)
    connectors = connector_markers(markers)

    jobs = matrix_shards(workflow_text)
    for shard in sorted(set(shards) - jobs):
        number = shard.rsplit("-", 1)[-1]
        problems.append(
            f"CONN_SHARD_{number} lists suites but the matrix has no '{shard}' job, so "
            f"nothing selects them and `core` excludes them: those tests stop running. "
            f"Add '{shard}' to the shard matrix in {WORKFLOW.name}."
        )
    for shard in sorted(jobs - set(shards)):
        problems.append(
            f"The matrix runs a '{shard}' job with no matching CONN_SHARD_* line, so it "
            f"would run with an empty marker expression."
        )

    seen: dict[str, str] = {}
    for shard, names in sorted(shards.items()):
        for name in names:
            if name not in markers:
                problems.append(
                    f"{shard} names '{name}', which is not a marker in pytest.ini "
                    f"(a rename? nothing would select those tests)."
                )
            elif name not in connectors:
                problems.append(
                    f"{shard} names '{name}', which is not a single connector's marker. "
                    f"A shard must list only connector suites: '{name}' would pull in "
                    f"tests that `core` then excludes, skewing both."
                )
            if name in seen:
                problems.append(
                    f"'{name}' is in both {seen[name]} and {shard}; it would run twice."
                )
            seen[name] = shard

    held = HELD_OUT_CONNECTORS & connectors
    core_expressions = _CORE_MARKER_LINE.findall(workflow_text)
    for name in sorted(held):
        if name in seen:
            problems.append(
                f"'{name}' is held out of the nightly until its connector is registered, "
                f"but {seen[name]} still selects it."
            )
        elif not core_expressions or any(
            not _always_excludes(expression, name) for expression in core_expressions
        ):
            problems.append(
                f"'{name}' is held out of the shards, but the core job does not exclude "
                f"it, so those tests fall into core."
            )

    problems.extend(_solo_shard_problems(workflow_text, core_expressions))
    problems.extend(_wiring_problems(workflow_text, shards))

    for name in sorted(connectors - set(seen) - held):
        problems.append(
            f"Connector marker '{name}' is in no shard. Its tests are marked "
            f"`integration`, so they fall into `core` instead of their own shard, making "
            f"`core` slower and the split uneven. Add it to a CONN_SHARD_* line in "
            f"{WORKFLOW.name}."
        )

    unmeasured = sorted(set(seen) - set(durations))
    totals = {
        shard: sum(durations.get(name, 0.0) for name in names)
        for shard, names in sorted(shards.items())
    }
    live = {shard: total for shard, total in totals.items() if shard not in simulator_shards}
    mean = sum(live.values()) / len(live) if live else 0.0

    report.append(f"{len(seen)} connector suites across {len(shards)} shards, "
                  f"{sum(totals.values()):.0f} min total, {mean:.0f} min average of the live shards:")
    for shard, total in sorted(totals.items(), key=lambda item: -item[1]):
        if shard in simulator_shards:
            report.append(f"  {shard:14s} {total:6.1f} min  (simulator shard, at most {SIM_SHARD_MAX_MINUTES:.0f})  "
                          f"{len(shards[shard])} suites")
            continue
        share = total / mean if mean else 1.0
        report.append(f"  {shard:14s} {total:6.1f} min  ({share:.2f}x average)  "
                      f"{len(shards[shard])} suites")
    if core_minutes is not None:
        report.append(f"  {'core':14s} {core_minutes:6.1f} min  (for reference; not marker-split)")
    if unmeasured:
        report.append(f"  no measured time yet (counted as 0): {', '.join(unmeasured)}")

    for shard in sorted(simulator_shards & set(totals)):
        if totals[shard] > SIM_SHARD_MAX_MINUTES:
            problems.append(
                f"{shard} is a simulator shard with {totals[shard]:.0f} min of suites, over its "
                f"{SIM_SHARD_MAX_MINUTES:.0f}: with stack start-up it would pass the job's 300-minute timeout. "
                f"Add another simulator shard (integration-tests/sim_tiers.json) and move suites to it."
            )
    if mean:
        worst, worst_total = max(live.items(), key=lambda item: item[1])
        if worst_total / mean > MAX_OVER_MEAN:
            problems.append(
                f"{worst} is {worst_total / mean:.2f}x the average shard "
                f"({worst_total:.0f} min vs {mean:.0f} min). The nightly ends when the "
                f"slowest shard ends, so move suites off it, using the minutes in "
                f"{DURATIONS.name}."
            )
    return (problems, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when a connector is unassigned or the shards drift apart")
    args = parser.parse_args()

    measured = json.loads(DURATIONS.read_text(encoding="utf-8"))
    problems, report = check(
        WORKFLOW.read_text(encoding="utf-8"),
        PYTEST_INI.read_text(encoding="utf-8"),
        measured["minutes"],
        measured.get("_core_minutes"),
        sim_shards(),
    )
    print("\n".join(report))
    for problem in problems:
        print(f"\nPROBLEM: {problem}")
    if problems and args.check:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

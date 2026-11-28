"""Which cases of a simulator suite run on a pull request, on the nightly, and in a full run.

A simulator suite can hold far more cases than a pull request should wait for. ``sim_tiers.json`` (next to
pytest.ini) gives each suite two tiers, and this plugin deselects at collection time:

* ``pr``: ``core`` keeps only the cases marked ``sim_core`` on a pull request; ``all`` keeps the whole suite.
* ``nightly``: ``all`` keeps the whole suite; ``rotate`` keeps the core cases plus one seventh of the others: those
  whose id hashes to the run's slot. The slot (0-6) comes from the workflow's setup job, once per run, so a re-run
  of a failed nightly tests the same cases (``sim_slot`` in scripts/nightly_setup.py: every case runs once in each
  Monday-to-Sunday week, on a different weekday each week, so over seven weeks it meets every weekday's setup, the
  message broker included). Each leg a suite rotates on (``rotate_legs``, both graph databases unless it says
  otherwise) takes its own slot, ArangoDB's three days after Neo4j's.

The workflow passes ``--sim-tier pr|nightly|all`` and ``--sim-slot``; without them every case runs, so a local run
or another shard is unchanged. Cases of suites the file does not name are never touched.

A case's id is its rootdir-relative path, classes and name with parameters: the node id without the ``@group``
that xdist's ``--dist loadgroup`` appends, so a serial run, a parallel run and ``--collect-only`` pick the same
cases. The rules are a pure function (:func:`keep`), tested without pytest (``scripts/test_sim_tiers.py``). A broken
file or bad options stop the session at start-up, before any worker starts; a case of two suites stops it at
collection.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

TIERS_FILE = Path(__file__).resolve().parent.parent / "sim_tiers.json"
CORE_MARKER = "sim_core"
TIER_CHOICES = ("all", "pr", "nightly")
PR_TIERS = ("core", "all")
NIGHTLY_TIERS = ("all", "rotate")
LEGS = ("neo4j", "arango")
DEFAULT_LEG = "neo4j"
SLOTS = 7
# ArangoDB's slice runs three nights after Neo4j's, so a case is not on both legs the same night.
LEG_OFFSET = {"neo4j": 0, "arango": 3}


class TierError(ValueError):
    """``sim_tiers.json``, the options, or a case's markers cannot be read the way the tiers need."""


@dataclass(frozen=True)
class Suite:
    pr: str
    nightly: str
    rotate_legs: tuple[str, ...] = LEGS


@dataclass(frozen=True)
class Tiers:
    sim_shards: tuple[str, ...]
    suites: dict[str, Suite]


def _suite(marker: str, tiers: Any) -> Suite:
    if not isinstance(tiers, dict) or not {"pr", "nightly"} <= set(tiers) <= {"pr", "nightly", "rotate_legs"}:
        raise TierError(f"{marker}: tiers must hold pr and nightly, and may hold rotate_legs")
    if tiers["pr"] not in PR_TIERS:
        raise TierError(f"{marker}: pr must be one of {PR_TIERS}, not {tiers['pr']!r}")
    if tiers["nightly"] not in NIGHTLY_TIERS:
        raise TierError(f"{marker}: nightly must be one of {NIGHTLY_TIERS}, not {tiers['nightly']!r}")
    if tiers["nightly"] == "rotate" and tiers["pr"] != "core":
        raise TierError(f"{marker}: nightly rotate needs pr core (the core is what runs every night)")
    if "rotate_legs" not in tiers:
        return Suite(pr=tiers["pr"], nightly=tiers["nightly"])
    legs = tiers["rotate_legs"]
    if tiers["nightly"] != "rotate":
        raise TierError(f"{marker}: rotate_legs is only for nightly rotate")
    if not isinstance(legs, list) or not legs or len(set(legs)) != len(legs) or not set(legs) <= set(LEGS):
        raise TierError(f"{marker}: rotate_legs must list some of {LEGS}, each once")
    return Suite(pr=tiers["pr"], nightly=tiers["nightly"], rotate_legs=tuple(legs))


def parse(data: Any) -> Tiers:
    """Validate the file's content and return it; every rule failure names what is wrong."""
    if not isinstance(data, dict) or set(data) != {"schema", "sim_shards", "suites"}:
        raise TierError("sim_tiers.json must hold exactly: schema, sim_shards, suites")
    if data["schema"] != 1:
        raise TierError(f"sim_tiers.json schema {data['schema']!r} is not 1")
    shards = data["sim_shards"]
    if not isinstance(shards, list) or not shards or not all(isinstance(s, str) and s.startswith("connectors-") for s in shards):
        raise TierError("sim_shards must be a non-empty list of connectors-N shard names")
    if len(set(shards)) != len(shards):
        raise TierError("sim_shards lists a shard twice")
    if not isinstance(data["suites"], dict):
        raise TierError("suites must map a marker to its tiers")
    suites = {marker: _suite(marker, tiers) for marker, tiers in data["suites"].items()}
    return Tiers(sim_shards=tuple(shards), suites=suites)


def load(path: Path = TIERS_FILE) -> Tiers:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise TierError(f"cannot read {path}: {exc}") from exc
    return parse(data)


def case_slot(case_id: str) -> int:
    return int(hashlib.sha1(case_id.encode()).hexdigest(), 16) % SLOTS


def suite_of(markers: set[str], tiers: Tiers, case_id: str) -> str | None:
    found = sorted(markers & tiers.suites.keys())
    if len(found) > 1:
        raise TierError(f"{case_id} carries the markers of two simulator suites: {', '.join(found)}")
    return found[0] if found else None


def keep(markers: set[str], case_id: str, tier: str, slot: int | None, leg: str, tiers: Tiers) -> bool:
    """Whether a case runs under ``tier``. Cases of no listed suite always run."""
    suite_name = suite_of(markers, tiers, case_id)
    if suite_name is None or tier == "all":
        return True
    suite = tiers.suites[suite_name]
    core = CORE_MARKER in markers
    if tier == "pr":
        return suite.pr == "all" or core
    if tier == "nightly":
        if suite.nightly == "all" or core:
            return True
        if slot is None:
            raise TierError("--sim-tier nightly needs --sim-slot")
        if leg not in suite.rotate_legs:
            return False
        return case_slot(case_id) == (slot + LEG_OFFSET[leg]) % SLOTS
    raise TierError(f"unknown tier {tier!r}; expected one of {TIER_CHOICES}")


def case_id(item: pytest.Item, rootpath: Path) -> str:
    """The rootdir-relative path, classes and name of a case, untouched by xdist's ``@group`` suffix."""
    try:
        path = item.path.relative_to(rootpath).as_posix()
    except ValueError:
        path = item.path.as_posix()
    names = [node.name for node in item.listchain() if node.path == item.path and node.name != item.path.name]
    return "::".join([path, *names])


def _leg() -> str:
    leg = os.getenv("TEST_GRAPH_DB_TYPE", DEFAULT_LEG).strip().lower() or DEFAULT_LEG
    return "arango" if leg.startswith("arango") else leg


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("sim-tiers", "simulator suite tiers (integration-tests/sim_tiers.json)")
    group.addoption("--sim-tier", choices=TIER_CHOICES, default="all", help="Which cases of a simulator suite to run.")
    group.addoption(
        "--sim-slot", default=None,
        help="The slot (0-6) a nightly run keeps of a rotating suite; the workflow's setup job picks it per run.",
    )


def validate(tier: str, raw_slot: str | None, tiers: Tiers) -> int | None:
    """The slot the options name, or None; a TierError says what is wrong with them."""
    slot: int | None = None
    if raw_slot not in (None, ""):
        try:
            slot = int(raw_slot)
        except ValueError:
            slot = -1
        if not 0 <= slot < SLOTS:
            raise TierError(f"--sim-slot must be 0-{SLOTS - 1}, not {raw_slot!r}")
    if tier == "nightly" and slot is None and any(s.nightly == "rotate" for s in tiers.suites.values()):
        raise TierError("--sim-tier nightly needs --sim-slot (a suite in sim_tiers.json rotates)")
    return slot


def pytest_configure(config: pytest.Config) -> None:
    """Check the file and the options before collection, on the controller too, so a mistake is a usage error."""
    import pytest

    tier = config.getoption("--sim-tier")
    try:
        tiers = load()
        slot = validate(tier, config.getoption("--sim-slot"), tiers)
    except TierError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config._sim_tiers = (tiers, tier, slot, _leg())  # type: ignore[attr-defined]


def split(
    items: list[Any], tiers: Tiers, tier: str, slot: int | None, leg: str, rootpath: Path,
) -> tuple[list[Any], list[Any], dict[str, int]]:
    """(kept, left out, left-out count per suite) for collected items, each judged by its :func:`case_id`, so the
    node id xdist rewrote never decides a case's slot."""
    kept: list[Any] = []
    dropped: list[Any] = []
    left_out: dict[str, int] = {}
    for item in items:
        markers = {mark.name for mark in item.iter_markers()}
        identity = case_id(item, rootpath)
        suite_name = suite_of(markers, tiers, identity)
        if keep(markers, identity, tier, slot, leg, tiers):
            kept.append(item)
        else:
            dropped.append(item)
            left_out[suite_name] = left_out.get(suite_name, 0) + 1
    return kept, dropped, left_out


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    import pytest

    tiers, tier, slot, leg = config._sim_tiers  # type: ignore[attr-defined]
    try:
        kept, dropped, left_out = split(items, tiers, tier, slot, leg, config.rootpath)
    except TierError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config._sim_tier_dropped = left_out  # type: ignore[attr-defined]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_collection_finish(session: pytest.Session) -> None:
    """Count what each suite runs after every deselection, ``-m`` included, for the summary line."""
    config = session.config
    tiers = config._sim_tiers[0]  # type: ignore[attr-defined]
    running: dict[str, int] = {}
    for item in session.items:
        markers = {mark.name for mark in item.iter_markers()}
        name = next(iter(markers & tiers.suites.keys()), None)
        if name is not None:
            running[name] = running.get(name, 0) + 1
    config._sim_tier_running = running  # type: ignore[attr-defined]


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    running = getattr(config, "_sim_tier_running", {})
    left_out = getattr(config, "_sim_tier_dropped", {})
    if not (running or left_out):
        return
    _, tier, slot, leg = config._sim_tiers  # type: ignore[attr-defined]
    where = f"{tier}, slot {slot}, {leg}" if slot is not None else f"{tier}, {leg}"
    for suite_name in sorted(set(running) | set(left_out)):
        terminalreporter.write_line(
            f"sim tiers ({where}): {suite_name} runs {running.get(suite_name, 0)}, "
            f"its tier leaves out {left_out.get(suite_name, 0)}"
        )

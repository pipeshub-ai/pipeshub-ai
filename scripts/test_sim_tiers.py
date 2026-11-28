"""Simulator suite tiers: integration-tests/sim_tiers.json, its selection rules, and how it fits the workflow.

The file decides which cases of a simulator suite a pull request, the nightly and a full run keep
(integration-tests/helper/sim_tiers.py). These checks run without a stack or secrets, on every pull request:
a suite that names a tier its tests cannot fill, or a simulator suite left on a shard that runs on the real
model, is caught here rather than after a run that needed approval.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim_shard_env

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "integration-tests"
WORKFLOW = REPO / ".github" / "workflows" / "integration-tests.yml"


def _load():
    spec = importlib.util.spec_from_file_location("sim_tiers", TESTS / "helper" / "sim_tiers.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sim_tiers = _load()


def registered_markers() -> set[str]:
    text = (TESTS / "pytest.ini").read_text()
    block = text.split("markers =", 1)[1]
    names = set()
    for line in block.splitlines()[1:]:
        if line and not line[0].isspace():
            break
        match = re.match(r"\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
        if match:
            names.add(match.group(1))
    return names


def conn_shards() -> dict[str, list[str]]:
    """``connectors-N`` -> its markers, from the ``CONN_SHARD_N: "a or b"`` lines of the workflow."""
    shards = {}
    for number, value in re.findall(r'^\s+CONN_SHARD_(\d+): "([^"]*)"', WORKFLOW.read_text(), re.MULTILINE):
        shards[f"connectors-{number}"] = [name.strip() for name in value.split(" or ") if name.strip()]
    return shards


def files_marked(marker: str) -> list[Path]:
    pattern = re.compile(rf"\bmark\.{re.escape(marker)}\b")
    return [path for path in (TESTS / "connectors").rglob("*.py") if pattern.search(path.read_text(errors="replace"))]


VALID = {"schema": 1, "sim_shards": ["connectors-6"], "suites": {"a_sim": {"pr": "core", "nightly": "rotate"}}}


class Schema(unittest.TestCase):
    def test_a_valid_file_parses(self) -> None:
        tiers = sim_tiers.parse(VALID)
        self.assertEqual(tiers.sim_shards, ("connectors-6",))
        self.assertEqual(tiers.suites["a_sim"].nightly, "rotate")

    def test_each_broken_file_names_what_is_wrong(self) -> None:
        broken = {
            "an extra key": {**VALID, "weekly": {}},
            "another schema": {**VALID, "schema": 2},
            "no sim shard": {**VALID, "sim_shards": []},
            "a shard that is not connectors-N": {**VALID, "sim_shards": ["core"]},
            "a shard twice": {**VALID, "sim_shards": ["connectors-6", "connectors-6"]},
            "an unknown pr tier": {**VALID, "suites": {"a_sim": {"pr": "most", "nightly": "all"}}},
            "an unknown nightly tier": {**VALID, "suites": {"a_sim": {"pr": "core", "nightly": "weekly"}}},
            "rotate without a core": {**VALID, "suites": {"a_sim": {"pr": "all", "nightly": "rotate"}}},
            "a missing tier": {**VALID, "suites": {"a_sim": {"pr": "core"}}},
            "rotate_legs without rotate": {**VALID, "suites": {"a_sim": {"pr": "core", "nightly": "all", "rotate_legs": ["neo4j"]}}},
            "an unknown leg": {**VALID, "suites": {"a_sim": {"pr": "core", "nightly": "rotate", "rotate_legs": ["postgres"]}}},
            "no leg": {**VALID, "suites": {"a_sim": {"pr": "core", "nightly": "rotate", "rotate_legs": []}}},
            "a leg twice": {**VALID, "suites": {"a_sim": {"pr": "core", "nightly": "rotate", "rotate_legs": ["neo4j", "neo4j"]}}},
        }
        for name, data in broken.items():
            with self.subTest(name), self.assertRaises(sim_tiers.TierError):
                sim_tiers.parse(data)

    def test_the_committed_file_parses(self) -> None:
        sim_tiers.load()


class Select(unittest.TestCase):
    tiers = sim_tiers.parse({
        "schema": 1,
        "sim_shards": ["connectors-6"],
        "suites": {
            "rot_sim": {"pr": "core", "nightly": "rotate"},
            "neo_sim": {"pr": "core", "nightly": "rotate", "rotate_legs": ["neo4j"]},
            "core_sim": {"pr": "core", "nightly": "all"},
            "whole_sim": {"pr": "all", "nightly": "all"},
        },
    })
    case_ids: ClassVar[list[str]] = [f"connectors/x/x_sim_{n % 9}_test.py::TestX::test_case_{n}[{n % 3}]" for n in range(700)]

    def keep(self, markers, case_id="t.py::test", tier="pr", slot=None, leg="neo4j"):
        return sim_tiers.keep(set(markers), case_id, tier, slot, leg, self.tiers)

    def slots_kept(self, suite, leg, slots):
        return {
            case_id: [slot for slot in slots if self.keep({suite}, case_id=case_id, tier="nightly", slot=slot, leg=leg)]
            for case_id in self.case_ids
        }

    def test_a_pull_request_keeps_the_core_of_a_core_suite_and_all_of_a_whole_one(self) -> None:
        self.assertTrue(self.keep({"core_sim", "sim_core"}))
        self.assertFalse(self.keep({"core_sim"}))
        self.assertFalse(self.keep({"rot_sim"}))
        self.assertTrue(self.keep({"whole_sim"}))

    def test_cases_of_no_listed_suite_always_run(self) -> None:
        for tier in ("pr", "nightly", "all"):
            self.assertTrue(self.keep({"jira", "integration"}, tier=tier, slot=0))

    def test_a_full_run_keeps_everything(self) -> None:
        self.assertTrue(self.keep({"rot_sim"}, tier="all"))
        self.assertTrue(self.keep({"neo_sim"}, tier="all", leg="arango"))

    def test_the_core_runs_every_night_on_both_legs(self) -> None:
        for slot in range(7):
            for leg in ("neo4j", "arango"):
                self.assertTrue(self.keep({"rot_sim", "sim_core"}, tier="nightly", slot=slot, leg=leg))
                self.assertTrue(self.keep({"core_sim"}, tier="nightly", slot=slot, leg=leg))

    def test_each_leg_runs_every_case_once_in_seven_slots_and_not_in_the_same_slot(self) -> None:
        neo4j = self.slots_kept("rot_sim", "neo4j", range(7))
        arango = self.slots_kept("rot_sim", "arango", range(7))
        for case_id in self.case_ids:
            self.assertEqual(len(neo4j[case_id]), 1, case_id)
            self.assertEqual(len(arango[case_id]), 1, case_id)
            self.assertNotEqual(neo4j[case_id], arango[case_id], case_id)
        per_slot = [sum(1 for slots in neo4j.values() if slots == [slot]) for slot in range(7)]
        self.assertLess(max(per_slot), 2 * min(per_slot), per_slot)

    def test_a_suite_can_rotate_on_one_leg_only(self) -> None:
        arango = self.slots_kept("neo_sim", "arango", range(7))
        self.assertFalse(any(arango.values()))
        self.assertTrue(all(len(slots) == 1 for slots in self.slots_kept("neo_sim", "neo4j", range(7)).values()))

    def test_a_case_id_is_its_path_classes_and_name_whatever_xdist_does_to_the_node_id(self) -> None:
        root = Path("/repo/integration-tests")
        path = root / "connectors" / "x" / "x_sim_test.py"

        class Node:
            def __init__(self, name: str, node_path: Path) -> None:
                self.name, self.path = name, node_path

        class Item(Node):
            def __init__(self, name: str, parents: list[Node]) -> None:
                super().__init__(name, path)
                self.parents = parents
                self.nodeid = "connectors/x/x_sim_test.py::TestX::" + name + "@serial"  # what xdist leaves

            def listchain(self) -> list[Node]:
                return [*self.parents, self]

        chain = [Node("", root), Node("connectors", root / "connectors"), Node("x_sim_test.py", path), Node("TestX", path)]
        for name in ("test_a", "test_b[user@example.com]", "test_c[a::b c]"):
            with self.subTest(name=name):
                item = Item(name, chain)
                self.assertEqual(sim_tiers.case_id(item, root), f"connectors/x/x_sim_test.py::TestX::{name}")
        nested = Item("test_d", [*chain, Node("Inner", path)])
        self.assertEqual(sim_tiers.case_id(nested, root), "connectors/x/x_sim_test.py::TestX::Inner::test_d")

    def test_the_hook_judges_each_case_by_its_case_id_not_the_node_id_xdist_rewrote(self) -> None:
        root = Path("/repo/integration-tests")
        path = root / "connectors" / "x" / "x_sim_test.py"

        class Mark:
            def __init__(self, name: str) -> None:
                self.name = name

        class Node:
            def __init__(self, name: str) -> None:
                self.name, self.path = name, path

        class Item(Node):
            def __init__(self, name: str) -> None:
                super().__init__(name)
                self.nodeid = f"connectors/x/x_sim_test.py::{name}@serial"

            def listchain(self) -> list:
                return [Node("x_sim_test.py"), self]

            def iter_markers(self) -> list:
                return [Mark("rot_sim")]

        items = [Item(f"test_{n}") for n in range(60)]
        for slot in range(7):
            kept, _, left_out = sim_tiers.split(items, self.tiers, "nightly", slot, "neo4j", root)
            expected = [item for item in items if sim_tiers.case_slot(f"connectors/x/x_sim_test.py::{item.name}") == slot]
            self.assertEqual(kept, expected, slot)
            self.assertEqual(left_out, {"rot_sim": len(items) - len(expected)})
        by_node_id = [item for item in items if sim_tiers.case_slot(item.nodeid) == 3]
        kept_at_3 = sim_tiers.split(items, self.tiers, "nightly", 3, "neo4j", root)[0]
        self.assertNotEqual(by_node_id, kept_at_3, "the fixture must tell a node-id hash from a case-id hash")

    def test_the_options_are_checked_before_any_case_is_collected(self) -> None:
        self.assertEqual(sim_tiers.validate("nightly", "3", self.tiers), 3)
        self.assertIsNone(sim_tiers.validate("pr", None, self.tiers))
        self.assertIsNone(sim_tiers.validate("all", "", self.tiers))
        for tier, raw in (("nightly", None), ("nightly", ""), ("pr", "7"), ("pr", "-1"), ("nightly", "Tuesday")):
            with self.subTest(tier=tier, slot=raw), self.assertRaises(sim_tiers.TierError):
                sim_tiers.validate(tier, raw, self.tiers)
        whole = sim_tiers.parse({"schema": 1, "sim_shards": ["connectors-6"], "suites": {"w_sim": {"pr": "all", "nightly": "all"}}})
        self.assertIsNone(sim_tiers.validate("nightly", None, whole))  # nothing rotates, so no slot is needed

    def test_a_nightly_without_a_slot_and_a_case_of_two_suites_fail(self) -> None:
        with self.assertRaises(sim_tiers.TierError):
            self.keep({"rot_sim"}, tier="nightly", slot=None)
        with self.assertRaises(sim_tiers.TierError):
            self.keep({"rot_sim", "whole_sim"})


class FitsTheRepository(unittest.TestCase):
    """The committed file against pytest.ini, the workflow's shards and the suites' own tests."""

    tiers = sim_tiers.load()
    shards = conn_shards()
    shard_env = sim_shard_env.FAKE_MODEL_ENV

    def test_every_suite_is_a_registered_marker(self) -> None:
        missing = set(self.tiers.suites) - registered_markers()
        self.assertFalse(missing, f"not registered in integration-tests/pytest.ini: {sorted(missing)}")
        self.assertIn(sim_tiers.CORE_MARKER, registered_markers())

    def test_every_sim_shard_exists_and_holds_only_listed_suites(self) -> None:
        for shard in self.tiers.sim_shards:
            with self.subTest(shard=shard):
                self.assertIn(shard, self.shards, f"{shard} has no CONN_SHARD line in {WORKFLOW.name}")
                strangers = set(self.shards[shard]) - set(self.tiers.suites)
                self.assertFalse(strangers, f"{shard} runs on the fake model but also holds {sorted(strangers)}")

    def test_every_suite_sits_in_exactly_one_shard_and_that_is_a_sim_shard(self) -> None:
        for marker in self.tiers.suites:
            with self.subTest(marker=marker):
                homes = [shard for shard, markers in self.shards.items() if marker in markers]
                self.assertEqual(len(homes), 1, f"{marker} is in {homes or 'no shard'}")
                self.assertIn(homes[0], self.tiers.sim_shards)

    def test_no_simulator_test_file_also_carries_a_live_shard_marker(self) -> None:
        """A sim case also marked for a live shard would run twice: there on the real model, and here."""
        live = {marker for shard, markers in self.shards.items() if shard not in self.tiers.sim_shards for marker in markers}
        for marker in self.tiers.suites:
            for path in files_marked(marker):
                text = path.read_text(errors="replace")
                with self.subTest(file=str(path.relative_to(REPO))):
                    both = sorted(name for name in live if re.search(rf"\bmark\.{re.escape(name)}\b", text))
                    self.assertFalse(both, f"{path.name} marks {marker} and the live {both}")

    def test_no_simulator_env_file_overrides_what_the_shard_sets(self) -> None:
        """Simulator steps load their env files into GITHUB_ENV after the shard's own settings, so they would win."""
        reserved = {key for key, _ in self.shard_env} | {"SIM_TIER", "SIM_SLOT", "SHARD", "CONNECTORS_INPUT"}
        for path in sorted((TESTS / "connectors").glob("*_sim.env")):
            keys = {line.split("=", 1)[0].strip() for line in path.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#")}
            with self.subTest(file=path.name):
                self.assertFalse(keys & reserved, f"{path.name} sets {sorted(keys & reserved)}")

    def test_a_core_tier_has_core_cases(self) -> None:
        for marker, suite in self.tiers.suites.items():
            files = files_marked(marker)
            with self.subTest(marker=marker):
                self.assertTrue(files, f"no test file carries mark.{marker}")
                if suite.pr == "core":
                    self.assertTrue(
                        any(re.search(r"\bmark\.sim_core\b", path.read_text()) for path in files),
                        f"{marker} is pr: core, but none of its files marks a case sim_core",
                    )


if __name__ == "__main__":
    unittest.main()

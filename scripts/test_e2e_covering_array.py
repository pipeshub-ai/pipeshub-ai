"""Tests for scripts/e2e_covering_array.py (run: python3 -m unittest discover -s scripts -p 'test_*.py')."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_covering_array as ca  # noqa: E402

BASE = dict(mode="quick", action="send", path="durable", engine="none", wire="openai_chat", compiler="off",
            flow_guard="ask", write_policy="allow", decision="none", tool_family="knowledge", sandbox="broker_closed",
            trigger="interactive", graph="neo4j", mongo="replset", kv="redis", broker="redis_streams", deploy="compose",
            role="user", child_model="none")


def row(**kw):
    return {**BASE, **kw}


class OracleTest(unittest.TestCase):
    def test_durable_read_only_turn_answers_without_effects(self):
        e = ca.oracle(row())
        self.assertEqual((e["route"], e["executor"], e["card"], e["effects"]), ("durable", "loop", False, 0))

    def test_flow_rule_holds_a_write_after_a_read(self):
        e = ca.oracle(row(tool_family="toolset", decision="allow_once"))
        self.assertTrue(e["card"])
        self.assertEqual((e["outcome"], e["effects"]), ("approved:written", 1))

    def test_org_deny_refuses_without_a_card(self):
        e = ca.oracle(row(tool_family="mcp", flow_guard="org_deny"))
        self.assertFalse(e["card"])
        self.assertEqual((e["outcome"], e["effects"]), ("denied:flow_external_write", 0))

    def test_legacy_routes_refuse_a_tainted_write_and_name_the_reason(self):
        e = ca.oracle(row(mode="plan_execute", tool_family="toolset"))
        self.assertEqual((e["route"], e["reason"], e["outcome"]), ("legacy", "chat_mode_not_supported", "refused:approval_unavailable"))
        e = ca.oracle(row(path="kill_switch", mongo="replset", tool_family="toolset", flow_guard="off"))
        self.assertEqual((e["reason"], e["outcome"], e["effects"]), ("kill_switch", "written", 1))

    def test_engine_writes_always_ask(self):
        e = ca.oracle(row(engine="opencode", sandbox="broker_open", tool_family="mcp", flow_guard="off", decision="deny"))
        self.assertTrue(e["card"])
        # CTL-6: the engine works as the loop's code_task child.
        self.assertEqual((e["executor"], e["effects"]), ("loop+code_task:opencode", 0))

    def test_qa37_e1_a_role_model_is_the_childs_and_changes_nothing_else(self):
        self.assertEqual(ca.oracle(row())["child"], "inherit")
        self.assertEqual(ca.oracle(row(mode="deep"))["child"], "inherit")
        plain = ca.oracle(row(mode="deep", tool_family="toolset", decision="allow_once"))
        for level in ("same", "different"):
            e = ca.oracle(row(mode="deep", tool_family="toolset", decision="allow_once", child_model=level))
            self.assertEqual(e, {**plain, "child": f"role:{level}"})

    def test_qa37_e1_a_forbidden_role_model_refuses_the_spawn_and_the_parent_answers(self):
        e = ca.oracle(row(mode="deep", tool_family="toolset", child_model="forbidden"))
        self.assertEqual((e["route"], e["executor"], e["child"]), ("durable", "loop", "refused:models.roles"))
        self.assertEqual((e["card"], e["outcome"], e["effects"]), (False, "denied:child_grant_too_large", 0))
        e = ca.oracle(row(mode="deep", tool_family="knowledge", child_model="forbidden"))
        self.assertEqual((e["outcome"], e["effects"], e["child"]), ("answered", 0, "refused:models.roles"))

    def test_qa37_e1_the_legacy_loop_has_no_rule_for_a_role_model(self):
        legacy = dict(mode="deep", path="kill_switch", tool_family="toolset", flow_guard="off")
        plain = ca.oracle(row(**legacy))
        for level in ("same", "different", "forbidden"):
            self.assertEqual(ca.oracle(row(**legacy, child_model=level)), {**plain, "child": "unspecified"})


class ConstraintTest(unittest.TestCase):
    def test_a_decision_without_a_card_is_invalid(self):
        self.assertFalse(ca.consistent(row(decision="allow_once")))
        self.assertTrue(ca.consistent(row(tool_family="toolset", decision="allow_once")))

    def test_engines_need_a_durable_agent_mode_and_the_broker(self):
        self.assertFalse(ca.consistent(row(engine="codex", wire="openai_responses", mode="plain", tool_family="none")))
        self.assertFalse(ca.consistent(row(engine="codex", wire="openai_responses", sandbox="unset")))
        self.assertFalse(ca.consistent(row(engine="codex", wire="openai_chat")))

    def test_a_conversation_grant_needs_a_named_destination_and_no_org_rule(self):
        """QA31-F1: the card offers "Allow for this conversation" only for a call that names where it goes, and an
        org's own write rule is never granted (app/harness/flow_destination.py, harness_core.effects.flow)."""
        grant = dict(decision="allow_conversation")
        self.assertTrue(ca.consistent(row(tool_family="toolset", **grant)))
        self.assertFalse(ca.consistent(row(tool_family="mcp", **grant)))
        self.assertFalse(ca.consistent(row(tool_family="toolset", write_policy="ask", **grant)))
        engine = dict(engine="codex", wire="openai_responses", flow_guard="off")
        self.assertTrue(ca.consistent(row(tool_family="toolset", **engine, **grant)))
        self.assertFalse(ca.consistent(row(tool_family="mcp", **engine, **grant)))
        self.assertFalse(ca.consistent(row(tool_family="toolset", write_policy="ask", **engine, **grant)))

    def test_qa37_e1_a_role_model_needs_a_turn_that_spawns_a_child_run(self):
        for level in ("same", "different", "forbidden"):
            self.assertTrue(ca.consistent(row(mode="deep", child_model=level)), level)
            # Kept, and blocked in the runner's plan (QA37-F1): the legacy route is not removed from the matrix.
            self.assertTrue(ca.consistent(row(mode="deep", path="kill_switch", child_model=level)), level)
            for mode in ("quick", "auto", "react", "plan_execute", "universal"):
                self.assertFalse(ca.consistent(row(mode=mode, child_model=level)), (mode, level))
            engine = dict(mode="deep", engine="opencode", sandbox="broker_closed")
            self.assertTrue(ca.consistent(row(**engine)))
            self.assertFalse(ca.consistent(row(**engine, child_model=level)), level)

    def test_qa37_e1_a_refused_spawn_shows_no_card_so_the_row_has_no_decision(self):
        held = dict(mode="deep", tool_family="toolset")
        self.assertTrue(ca.consistent(row(**held, decision="allow_once", child_model="different")))
        self.assertFalse(ca.consistent(row(**held, decision="allow_once", child_model="forbidden")))
        self.assertTrue(ca.consistent(row(**held, child_model="forbidden")))

    def test_a_memory_row_is_attended(self):
        """QA6-F13: a scheduled run is unattended and is granted no memory write scope (P7.3), on any executor."""
        self.assertTrue(ca.consistent(row(tool_family="memory")))
        self.assertFalse(ca.consistent(row(tool_family="memory", trigger="schedule_cron")))
        self.assertTrue(ca.consistent(row(tool_family="knowledge", trigger="schedule_cron")))
        engine = dict(engine="codex", wire="openai_responses", flow_guard="off", sandbox="broker_open")
        self.assertTrue(ca.consistent(row(tool_family="memory", **engine)))
        self.assertFalse(ca.consistent(row(tool_family="memory", trigger="schedule_cron", **engine)))

    def test_no_mode_is_one_the_chat_routes_refuse(self):
        """QA6-F5: `image` was a level no chat route accepts; a row holding a retired level takes one that is left."""
        names, levels, _ = ca.load()
        self.assertNotIn("image", levels["mode"])
        retired = row(mode="image", tool_family="none")
        self.assertEqual(ca.relevel(row(), names, levels), row())
        mended = ca.relevel(retired, names, levels)
        self.assertTrue(ca.consistent(mended))
        self.assertEqual([f for f in names if mended[f] != retired[f]], ["mode"])

    def test_stack_profiles_are_a_pairwise_array_of_the_store_factors(self):
        import itertools
        stacks = list(ca.STACKS.values())
        for i, j in itertools.combinations(range(len(ca.STACK_FACTORS)), 2):
            seen = {(s[i], s[j]) for s in stacks}
            self.assertEqual(len(seen), 4, f"{ca.STACK_FACTORS[i]} x {ca.STACK_FACTORS[j]}")


class CommittedArraysTest(unittest.TestCase):
    def test_committed_arrays_are_valid_and_cover_every_tuple(self):
        names, levels, core = ca.load()
        for stem, strength, which, _prefix in ca.ARRAYS:
            factors = core if which == "core" else names
            data = json.loads((ca.DATA / f"e2e-{stem}.json").read_text())
            rows = [{k: v for k, v in r.items() if k not in ("id", "stack")} for r in data["rows"]]
            self.assertEqual(ca.verify(rows, names, levels, factors, strength), [], stem)

    def test_committed_files_are_what_the_generator_writes(self):
        """A regeneration (repair of the committed rows) changes no committed byte: nothing stale, nothing hand-edited."""
        names, levels, core = ca.load()
        for path, body in ca.array_files(names, levels, core, fresh=False).items():
            self.assertEqual(path.read_text(), body, f"{path.name} is stale: run scripts/e2e_covering_array.py")

    def test_generation_is_deterministic_across_hash_seeds(self):
        """Same inputs, same rows: no set order, hash seed or clock reaches the greedy choice."""
        import subprocess

        # Few candidates and three factors keep it to seconds; the code path is the full generator's.
        prog = ("import json, e2e_covering_array as ca; ca.CANDIDATES = 3; n, lv, _ = ca.load(); "
                "print(json.dumps(ca.generate(n, lv, ['engine', 'wire', 'sandbox'], 2)))")
        here = str(Path(__file__).resolve().parent)
        runs = {subprocess.run([sys.executable, "-c", prog], cwd=here, env={"PYTHONHASHSEED": seed}, check=True,
                               capture_output=True, text=True).stdout for seed in ("0", "1")}
        self.assertEqual(len(runs), 1)

    def test_repair_keeps_valid_rows_and_mends_a_refused_one_by_one_factor(self):
        names, levels, core = ca.load()
        data = json.loads((ca.DATA / "e2e-threeway-core.json").read_text())
        rows = [{k: v for k, v in r.items() if k not in ("id", "stack")} for r in data["rows"]]
        self.assertEqual(ca.repair(rows, names, levels, core, 3), rows)
        # QA6-F5: a committed row holding a retired level is re-levelled in place; nothing else moves.
        retired = [dict(rows[0], mode="image"), *rows[1:]]
        self.assertEqual(ca.repair(retired, names, levels, core, 3), [ca.relevel(retired[0], names, levels), *rows[1:]])
        refused = dict(rows[0], tool_family="mcp", decision="allow_conversation", flow_guard="ask", write_policy="allow")
        self.assertFalse(ca.consistent(refused))
        mended = ca.mend(refused, names, levels)
        self.assertTrue(ca.consistent(mended))
        self.assertEqual([f for f in names if mended[f] != refused[f]], ["decision"])

    def test_qa37_e1_repair_gives_a_new_factor_its_first_level_in_committed_rows(self):
        names, levels, core = ca.load()
        data = json.loads((ca.DATA / "e2e-threeway-core.json").read_text())
        rows = [{k: v for k, v in r.items() if k not in ("id", "stack")} for r in data["rows"]]
        before = [{k: v for k, v in r.items() if k != "child_model"} for r in rows]
        repaired = ca.repair(before, names, levels, core, 3)
        self.assertEqual(repaired[:len(before)], [{**r, "child_model": "none"} for r in before])
        self.assertEqual([list(r) for r in repaired], [names] * len(repaired))

    def test_qa37_e1_every_role_model_level_meets_every_level_it_can(self):
        """The pairwise array runs each level on the durable loop and on the legacy one; no engine row has one."""
        rows = json.loads((ca.DATA / "e2e-pairwise.json").read_text())["rows"]
        for level in ("same", "different", "forbidden"):
            mine = [r for r in rows if r["child_model"] == level]
            self.assertEqual({ca.oracle(r)["route"] for r in mine}, {"durable", "legacy"}, level)
            self.assertEqual({(r["mode"], r["engine"]) for r in mine}, {("deep", "none")}, level)

    def test_must_run_scenarios_are_valid_rows(self):
        names, levels, _ = ca.load()
        mr = json.loads(ca.MUST_RUN.read_text())
        ids = [s["id"] for s in mr["scenarios"]]
        self.assertEqual(len(ids), len(set(ids)))
        for sc in mr["scenarios"]:
            r = {**mr["defaults"], **sc["factors"]}
            self.assertEqual(set(r), set(names), sc["id"])
            for f, v in r.items():
                self.assertIn(v, levels[f], f"{sc['id']}: {f}={v}")
            # Must-run scenarios may use a stack outside the seven profiles (EM-35); every other constraint holds.
            broken = [c.name for c in ca.CONSTRAINTS if c.name != "stack_profile" and not c.holds(r)]
            self.assertEqual(broken, [], sc["id"])

    def test_every_row_is_a_valid_routing_input_and_the_oracle_routes_it_by_the_table(self) -> None:
        """QA8-6: the oracle's route is the routing table's (contracts/harness/vectors/routing.json)."""
        import routing_table

        mr = json.loads(ca.MUST_RUN.read_text())
        rows = [{**mr["defaults"], **sc["factors"]} for sc in mr["scenarios"]]
        for stem, *_ in ca.ARRAYS:
            rows += json.loads((ca.DATA / f"e2e-{stem}.json").read_text())["rows"]
        for r in rows:
            inp = routing_table.e2e_input(r)
            self.assertTrue(routing_table.is_valid(inp), (r, inp))
            then = routing_table.answer(inp)[1]
            e = ca.oracle(r)
            self.assertEqual(e["route"], "legacy" if then["route"] == "legacy" else "durable")
            self.assertEqual(e["reason"], then.get("reason", ""))
            # CTL-6: an engine agent's turn is a loop turn that may hand off (the table's codeTask).
            self.assertEqual("+code_task:" in e["executor"], then.get("codeTask") is True)


if __name__ == "__main__":
    unittest.main()

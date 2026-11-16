"""The feature map: loading and overlays, shape checks, drift against the product, coverage of a run."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from qa import __main__ as qa_main
from qa import catalog, config, features, testing

OPENAPI = """\
openapi: 3.0.3
servers:
  - url: "{instance_url}/api/v1"
paths:
  /search:
    post:
      summary: Search
  /knowledgebase/{kbId}:
    get:
      summary: One knowledge base
    delete:
      summary: Delete it
  /health:
    get:
      summary: Health
components:
  schemas:
    X:
      type: object
"""

SEARCH = """\
[[feature]]
id = "search.query"
title = "Search"
flow = "search"
what = "A user searches the records they can read."
routes = ["POST /api/v1/search"]
code = ["backend/python/app/services/retrieval.py"]
status = "reviewed"

  [[feature.behavior]]
  id = "search.query.acl"
  says = "Search hides records the user cannot read."
  priority = "P0"
  kind = "security"
  tests = ["TC-RET-001"]

  [[feature.behavior]]
  id = "search.query.ranking"
  says = "Exact title matches rank first."
  priority = "P1"
  tests = ["TC-RET-002"]

  [[feature.behavior]]
  id = "search.query.empty"
  says = "An empty query returns no results."
  priority = "P0"

[[feature]]
id = "kb.manage"
title = "Knowledge bases"
flow = "search"
what = "Admins open and delete knowledge bases."
routes = ["* /api/v1/knowledgebase/{id}"]
pages = ["frontend/app/login/**"]
connectors = ["jira"]

  [[feature.behavior]]
  id = "kb.manage.vendor"
  says = "A Jira sync against a real site keeps every issue."
  priority = "P1"
  manual = "needs a real Jira site"

[ignore]
routes = ["GET /api/v1/health"]
"""


class FeatureMapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        testing.write(self.root, features.OPENAPI, OPENAPI)
        testing.write(self.root, "scripts/qa/features/search.toml", SEARCH)
        testing.commit(self.root, "map")
        self.cfg = config.load(self.root)

    def load(self) -> dict:
        config.clear_cache()
        return features.load(self.root)

    def rows(self) -> list[dict]:
        return catalog.build(self.cfg)

    # --- loading and checks

    def test_loads_and_checks_clean(self) -> None:
        fmap = self.load()
        self.assertEqual([f["id"] for f in fmap["features"]], ["search.query", "kb.manage"])
        self.assertEqual(features.check(self.cfg, fmap), [])

    def test_overlay_extends_lists_and_adds_behaviors(self) -> None:
        testing.write(self.root, "scripts/plus/qa/features/search.toml", (
            '[[feature]]\nid = "search.query"\nroutes = ["GET /api/v1/search/plus"]\n'
            '  [[feature.behavior]]\n  id = "search.query.plus"\n  says = "x"\n  priority = "P2"\n'))
        f = self.load()["features"][0]
        self.assertEqual(f["routes"][-1], "GET /api/v1/search/plus")
        self.assertEqual(f["behavior"][-1]["id"], "search.query.plus")

    def test_two_overlay_files_may_extend_one_feature_but_not_add_it_twice(self) -> None:
        ext = '[[feature]]\nid = "search.query"\nroutes = ["GET /api/v1/search/{n}"]\n'
        testing.write(self.root, "scripts/plus/qa/features/a.toml", ext.replace("{n}", "a"))
        testing.write(self.root, "scripts/plus/qa/features/b.toml", ext.replace("{n}", "b"))
        self.assertEqual(self.load()["features"][0]["routes"][-2:], ["GET /api/v1/search/a", "GET /api/v1/search/b"])
        new = '[[feature]]\nid = "plus.x"\ntitle = "t"\nflow = "search"\nwhat = "w"\n'
        testing.write(self.root, "scripts/plus/qa/features/a.toml", new)
        testing.write(self.root, "scripts/plus/qa/features/b.toml", new)
        with self.assertRaises(ValueError) as ctx:
            self.load()
        self.assertIn("plus.x", str(ctx.exception))

    def test_overlay_cannot_change_a_scalar(self) -> None:
        testing.write(self.root, "scripts/plus/qa/features/search.toml", '[[feature]]\nid = "search.query"\ntitle = "Other"\n')
        with self.assertRaises(ValueError) as ctx:
            self.load()
        self.assertIn("title", str(ctx.exception))

    def test_duplicate_feature_in_one_tree_raises(self) -> None:
        testing.write(self.root, "scripts/qa/features/other.toml", '[[feature]]\nid = "kb.manage"\ntitle = "t"\nflow = "search"\nwhat = "w"\n')
        with self.assertRaises(ValueError) as ctx:
            self.load()
        self.assertIn("kb.manage", str(ctx.exception))

    def test_check_finds_shape_errors(self) -> None:
        testing.write(self.root, "scripts/qa/features/bad.toml", (
            '[[feature]]\nid = "bad"\ntitle = "t"\nflow = "nope"\nwhat = "w"\nroute = ["GET /x"]\nroutes = ["/x"]\n'
            '  [[feature.behavior]]\n  id = "search.query.acl"\n  says = "s"\n  priority = "P9"\n  kind = "vibes"\n'))
        errors = "\n".join(features.check(self.cfg, self.load()))
        for want in ("unknown flow 'nope'", "unknown key route", "'METHOD /path'", "priority must be", "kind must be",
                     "duplicate id 'search.query.acl'"):
            self.assertIn(want, errors)

    # --- surfaces and drift

    def test_openapi_routes_carry_the_server_base(self) -> None:
        self.assertEqual(features.openapi_routes(OPENAPI), [
            "DELETE /api/v1/knowledgebase/{kbId}", "GET /api/v1/health", "GET /api/v1/knowledgebase/{kbId}", "POST /api/v1/search"])

    def test_route_patterns(self) -> None:
        m = features._route_matches
        self.assertTrue(m("* /api/v1/knowledgebase/{id}", "DELETE /api/v1/knowledgebase/{kbId}"))
        self.assertTrue(m("get /api/v1/kb/**", "GET /api/v1/kb/{a}/records"))
        self.assertFalse(m("GET /api/v1/kb/*", "GET /api/v1/kb/{a}/records"))
        self.assertFalse(m("POST /api/v1/search", "GET /api/v1/search"))

    def test_a_complete_map_has_no_drift(self) -> None:
        doc = features.drift(self.root, self.load(), self.rows())
        self.assertEqual((doc["unclaimed"], doc["stale"], doc["dead_test"]), ([], [], []))
        self.assertEqual(doc["surfaces"], {"routes": 4, "pages": 1, "connectors": 1, "toolsets": 0})

    def test_drift_finds_new_gone_and_dead(self) -> None:
        testing.write(self.root, features.OPENAPI, OPENAPI.replace("  /health:", "  /agents:\n    get:\n      summary: A\n  /health:"))
        testing.write(self.root, "frontend/app/chat/page.tsx", "export default function P() { return null; }\n")
        testing.write(self.root, "backend/python/app/agents/actions/slack/tool.py", "X = 1\n")
        testing.write(self.root, "scripts/qa/features/search.toml", SEARCH.replace('"TC-RET-002"', '"TC-RET-404"')
                      .replace('connectors = ["jira"]', 'connectors = ["jira", "zendesk"]'))
        (self.root / "frontend/app/login/page.tsx").unlink()
        testing.commit(self.root, "drift")
        doc = features.drift(self.root, self.load(), self.rows())
        self.assertEqual(sorted(u["value"] for u in doc["unclaimed"]), ["GET /api/v1/agents", "frontend/app/chat/page.tsx", "slack"])
        self.assertEqual(sorted((s["feature"], s["value"]) for s in doc["stale"]),
                         [("kb.manage", "frontend/app/login/**"), ("kb.manage", "zendesk")])
        self.assertEqual(doc["dead_test"], [{"behavior": "search.query.ranking", "test": "TC-RET-404"}])

    def test_drift_since_lists_changed_features(self) -> None:
        base = testing.git(self.root, "rev-parse", "HEAD")
        testing.write(self.root, "backend/python/app/services/retrieval.py", "def search():\n    return [1]\n")
        testing.commit(self.root, "change")
        self.assertEqual(features.drift(self.root, self.load(), self.rows(), since=base)["changed"], ["search.query"])

    # --- coverage

    def coverage(self, outcomes: dict[str, dict]) -> dict[str, dict]:
        cov = features.coverage(self.load(), self.rows(), {"tests": [{"id": i, **o} for i, o in outcomes.items()]})
        return {b["behavior"]: b for b in cov["behaviors"]}

    def ids(self) -> tuple[str, str]:
        rows = self.rows()
        return features.resolve("TC-RET-001", rows)[0], features.resolve("TC-RET-002", rows)[0]

    def test_coverage_statuses(self) -> None:
        acl, ranking = self.ids()
        cov = self.coverage({acl: {"outcome": "pass"}, ranking: {"outcome": "fail"}})
        self.assertEqual({k: v["status"] for k, v in cov.items()}, {
            "search.query.acl": "passed", "search.query.ranking": "failing",
            "search.query.empty": "no-test", "kb.manage.vendor": "manual"})

    def test_not_run_and_flaky_never_count_as_passed(self) -> None:
        acl, ranking = self.ids()
        cov = self.coverage({acl: {"outcome": "not_run", "kind": "secret"}, ranking: {"outcome": "flaky"}})
        self.assertEqual(cov["search.query.acl"]["status"], "not-run")
        self.assertIn("secret", cov["search.query.acl"]["detail"])
        self.assertEqual(cov["search.query.ranking"]["status"], "flaky")
        self.assertEqual(self.coverage({})["search.query.acl"]["detail"].split(": ")[1], "not in this run")

    def test_one_failing_test_fails_the_behavior(self) -> None:
        acl, ranking = self.ids()
        testing.write(self.root, "scripts/qa/features/search.toml", SEARCH.replace('tests = ["TC-RET-001"]', 'tests = ["TC-RET-001", "TC-RET-002"]'))
        cov = self.coverage({acl: {"outcome": "pass"}, ranking: {"outcome": "error"}})
        self.assertEqual(cov["search.query.acl"]["status"], "failing")

    def test_render_lists_open_p0_behaviors(self) -> None:
        acl, _ = self.ids()
        text = features.render(features.coverage(self.load(), self.rows(), {"tests": [{"id": acl, "outcome": "fail"}]}))
        self.assertIn("2 features (1 draft), 4 behaviors", text)
        self.assertIn("**failing** `search.query.acl`", text)
        self.assertIn("**no-test** `search.query.empty`", text)
        self.assertNotIn("search.query.ranking", text)
        self.assertEqual(features.render(features.coverage({"features": [], "ignore": {}}, [], None)), "")

    # --- the command

    def cli(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(buf):
            config.clear_cache()
            code = qa_main.main(["--out", str(self.root / "qa-out"), "features", *argv])
        return code, buf.getvalue()

    def test_cli_drift_fails_unless_warn_and_coverage_writes_its_file(self) -> None:
        testing.write(self.root, "frontend/app/chat/page.tsx", "export default function P() { return null; }\n")
        testing.commit(self.root, "new page")
        self.assertEqual(self.cli("check")[0], 0)
        code, text = self.cli("drift")
        self.assertEqual(code, 1)
        self.assertIn("1 unclaimed", text)
        code, text = self.cli("drift", "--warn")
        self.assertEqual(code, 0)
        self.assertIn("::warning::feature map drift", text)
        results = self.root / "results.json"
        results.write_text(json.dumps({"tests": []}), encoding="utf-8")
        code, text = self.cli("coverage", "--results", str(results))
        self.assertEqual(code, 0)
        self.assertIn("Map drift: 1 unclaimed", text)
        self.assertTrue((self.root / "qa-out/features-coverage.json").is_file())

    def test_cli_check_fails_on_a_bad_map(self) -> None:
        testing.write(self.root, "scripts/qa/features/bad.toml", '[[feature]]\nid = "x"\n')
        code, text = self.cli("check")
        self.assertEqual(code, 1)
        self.assertIn("feature x: missing title", text)


if __name__ == "__main__":
    unittest.main()

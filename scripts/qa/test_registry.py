"""The module and flow registry: loading, merging overlays, linting."""

from __future__ import annotations

import unittest

from qa import catalog, config, paths, testing


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)

    def lint_errors(self) -> list[str]:
        config.clear_cache()
        cfg = config.load(self.root)
        return catalog.lint(cfg, catalog.build(cfg))[0]

    def test_registry_loads_base_and_overlay(self) -> None:
        cfg = config.load(self.root)
        self.assertEqual([f["id"] for f in cfg.flows], ["search", "login", "usage"])
        self.assertTrue(cfg.flow("search")["triggers"][-1] == "backend/python/app/plus/**")
        self.assertIsNone(cfg.flow("nope"))
        self.assertEqual(len(cfg.modules), 5)

    def test_overlay_cannot_change_flow_priority(self) -> None:
        testing.write(self.root, "scripts/plus/qa/rules/flows.toml", '[[flow]]\nid = "search"\npriority = "P2"\n')
        with self.assertRaises(ValueError) as ctx:
            config.load(self.root)
        self.assertIn("search", str(ctx.exception))
        self.assertIn("priority", str(ctx.exception))

    def test_overlay_scalar_for_list_key_raises(self) -> None:
        testing.write(self.root, "scripts/plus/qa/rules/flows.toml", '[[flow]]\nid = "search"\ntriggers = "backend/x/**"\n')
        with self.assertRaises(ValueError) as ctx:
            config.load(self.root)
        for word in ("flow", "search", "triggers"):
            self.assertIn(word, str(ctx.exception))

    def test_duplicate_flow_ids_raise(self) -> None:
        testing.write(self.root, "scripts/qa/rules/flows.toml", testing.FLOWS + '\n[[flow]]\nid = "login"\n')
        with self.assertRaises(ValueError) as ctx:
            config.load(self.root)
        self.assertIn("duplicate flow ids", str(ctx.exception))

    def test_without_overlay_only_base_flows(self) -> None:
        root, tmp = testing.make_repo(overlay=False)
        self.addCleanup(tmp.cleanup)
        self.assertEqual([f["id"] for f in config.load(root).flows], ["search", "login"])

    def test_lint_flags_unknown_module_bad_priority_and_noncritical_p1(self) -> None:
        testing.write(self.root, "scripts/qa/rules/flows.toml", testing.FLOWS + '''
[[flow]]
id = "a"
priority = "P1"
modules = ["nope"]

[[flow]]
id = "b"
priority = "P9"

[[flow]]
id = "c"
priority = "P1"
critical = true
''')
        errors = self.lint_errors()
        self.assertIn("flow a: unknown module nope", errors)
        self.assertIn("flow b: priority must be P0, P1 or P2", errors)
        self.assertIn("flow c: only a P0 flow can be critical", errors)

    def test_lint_flags_rule_with_unknown_module(self) -> None:
        testing.write(self.root, "scripts/qa/rules/impact.toml", testing.IMPACT + '\n[[rule]]\nid = "x"\nmodule = "ghost"\npaths = ["frontend/app/**"]\n')
        self.assertIn("impact rule x: unknown module ghost", self.lint_errors())

    def test_every_rule_names_a_module(self) -> None:
        testing.write(self.root, "scripts/qa/rules/impact.toml", testing.IMPACT + '\n[[rule]]\nid = "loose"\npaths = ["frontend/app/**"]\n')
        self.assertIn("impact rule loose: no module", self.lint_errors())

    def test_repo_registry_is_clean(self) -> None:
        config.clear_cache()
        cfg = config.load(paths.REPO)
        errors, warnings = catalog.lint(cfg, catalog.build(cfg))
        self.assertEqual([e for e in errors if e.startswith(("module ", "flow ", "impact rule "))], [])
        bad = [w for w in warnings if w.startswith(("module ", "flow ")) or "unknown module" in w]
        self.assertEqual(bad, [])

    def test_lint_warns_on_dead_registry_globs(self) -> None:
        testing.write(self.root, "scripts/qa/rules/modules.toml", '[[module]]\nid = "m"\npaths = ["nowhere/**"]\n')
        testing.write(self.root, "scripts/qa/rules/flows.toml", '[[flow]]\nid = "f"\npriority = "P2"\nmodules = ["m"]\ntriggers = ["gone/**"]\ntests = ["none/**"]\n')
        config.clear_cache()
        cfg = config.load(self.root)
        _, warnings = catalog.lint(cfg, catalog.build(cfg))
        self.assertIn("module m: path 'nowhere/**' matches no file", warnings)
        self.assertIn("flow f: trigger 'gone/**' matches no file", warnings)
        self.assertIn("flow f: tests 'none/**' matches no catalogued test", warnings)


if __name__ == "__main__":
    unittest.main()

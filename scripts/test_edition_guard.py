"""The edition guard (scripts/edition_guard.py) on synthetic diffs, and on a throwaway repository.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edition_guard  # noqa: E402

MARKER = "LicenseRef-" + "PipesHub-Proprietary"
PRIVATE = "pipeshub" + "ai-ee"


def diff(path: str, *lines: str) -> str:
    """A unified diff that adds ``lines`` to ``path`` from its first line."""
    return f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)


class AddedPaths(unittest.TestCase):
    def test_a_path_under_an_ee_directory_at_any_depth_fails(self) -> None:
        for path in (
            "ee/README.md",
            "backend/python/app/ee/thing.py",
            "backend/nodejs/apps/src/ee/thing.ts",
            "frontend/ee/lib/thing.ts",
            "contracts/harness/ee/flags.json",
            "docs/ee/notes.md",
            "scripts/ee/sync.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(len(edition_guard.check([path], "").errors), 1)

    def test_edition_only_build_files_fail(self) -> None:
        for path in ("Dockerfile.ee", ".github/workflows/build-ee.yml", ".github/workflows/x-ee.yaml",
                     "deployment/docker-compose/docker-compose.ee.yml"):
            with self.subTest(path=path):
                self.assertTrue(edition_guard.check([path], "").errors)

    def test_names_that_only_contain_ee_pass(self) -> None:
        for path in ("backend/python/app/free/thing.py", "docs/employee.md", "frontend/coffee/ee.ts",
                     "deployment/docker-compose/docker-compose.yml", "Dockerfile", ".github/workflows/free.yml",
                     "backend/python/app/edition_services.py"):
            with self.subTest(path=path):
                self.assertEqual(edition_guard.check([path], "").errors, [])

    def test_a_new_file_in_a_fixes_only_directory_fails(self) -> None:
        for path in ("backend/python/tests/evals/new_case.py", "backend/python/app/agent_loop_lib/eval/x.py",
                     "backend/python/app/agents/agent_loop/evals/y.json", ".github/workflows/nightly-evals.yml"):
            with self.subTest(path=path):
                (error,) = edition_guard.check([path], "").errors
                self.assertIn("fixes only", error)

    def test_a_fix_to_a_file_in_a_fixes_only_directory_passes(self) -> None:
        changed = diff("backend/python/tests/evals/existing.py", "assert answer == 42")
        self.assertEqual(edition_guard.check([], changed).errors, [])


class Imports(unittest.TestCase):
    def test_python_imports_of_the_edition_package_fail(self) -> None:
        for line in ("from app.ee.harness import edition", "import app.ee", "    from app.ee import x",
                     # Split, so this file does not hold the call it checks for.
                     "module = importlib.import_module(" + "'app.ee.thing')", "import_module(" + '"app.ee")'):
            with self.subTest(line=line):
                (error,) = edition_guard.check([], diff("backend/python/app/x.py", line)).errors
                self.assertTrue(error.startswith("backend/python/app/x.py:1:"), error)

    def test_python_lookalikes_pass(self) -> None:
        for line in ("from app.eel import x", "from app.edition_services import harness_edition",
                     "# from app.ee import x is how an edition would do it", 'path = "app/ee/x.py"'):
            with self.subTest(line=line):
                self.assertEqual(edition_guard.check([], diff("backend/python/app/x.py", line)).errors, [])

    def test_script_imports_of_an_edition_directory_fail(self) -> None:
        for line in ("import { X } from '@/ee/lib/x';", "import { Y } from './ee/y';",
                     "export { Z } from '../../ee/z';", "import '../ee/side-effect';",
                     "const m = require('./ee/m');", "const lazy = await import('@/ee/lazy');"):
            for path in ("frontend/config.ts", "frontend/app/page.tsx", "backend/nodejs/apps/src/config.ts"):
                with self.subTest(line=line, path=path):
                    self.assertEqual(len(edition_guard.check([], diff(path, line)).errors), 1)

    def test_script_lookalikes_pass(self) -> None:
        for line in ("import { X } from '@/eel/x';", "import { Y } from './free/y';",
                     "import { Z } from '@/app/(main)/workspace/sidebar/workspace-nav';"):
            with self.subTest(line=line):
                self.assertEqual(edition_guard.check([], diff("frontend/config.ts", line)).errors, [])

    def test_an_import_in_another_kind_of_file_is_not_read_as_code(self) -> None:
        self.assertEqual(edition_guard.check([], diff("docs/guide.md", "from app.ee import x")).errors, [])

    def test_the_line_number_is_where_the_line_lands(self) -> None:
        changed = "+++ b/src/a.ts\n@@ -10,0 +11,2 @@\n+const ok = 1;\n+import { X } from './ee/x';\n"
        (error,) = edition_guard.check([], changed).errors
        self.assertTrue(error.startswith("src/a.ts:12:"), error)

    def test_removed_lines_and_deleted_files_are_not_read(self) -> None:
        changed = "--- a/src/a.ts\n+++ /dev/null\n@@ -1 +0,0 @@\n-import { X } from './ee/x';\n"
        self.assertEqual(edition_guard.check([], changed).errors, [])


class Licence(unittest.TestCase):
    def test_a_proprietary_licence_marker_fails_in_any_file(self) -> None:
        for path in ("backend/python/app/x.py", "frontend/x.ts", "README.md"):
            with self.subTest(path=path):
                (error,) = edition_guard.check([], diff(path, f"# SPDX-License-Identifier: {MARKER}")).errors
                self.assertIn("licence", error)

    def test_this_repositorys_licence_passes(self) -> None:
        self.assertEqual(edition_guard.check([], diff("x.py", "# SPDX-License-Identifier: Apache-2.0")).errors, [])


class ProjectTracker(unittest.TestCase):
    def test_re_adding_a_moved_tracker_document_fails(self) -> None:
        for path in (
            "docs/agent-harness-v2/STATUS.md",
            "docs/agent-harness-v2/REMAINING.md",
            "docs/agent-harness-v2/NEXT-PHASE.md",
            "docs/agent-harness-v2/research.md",
            "docs/agent-harness-v2/impl/LOG.md",
            "docs/agent-harness-v2/impl/evidence/x/README.md",
            "docs/agent-harness-v2/impl/P4.8-compiler-default.md",
            "docs/agent-harness-v2/notes/10-lead.md",
            "docs/agent-harness-v2/research/R1.md",
            "docs/agent-harness-v2/history/PROJECT-PLAN-v2.0.md",
            "docs/agent-harness-v2/pr/PR-1.md",
            "docs/agent-harness-v2/ui-revamp/PLAN.md",
            "docs/agent-harness-v2/classic-jobs/PLAN.md",
            "docs/agent-harness-v2/SECURITY-PROGRAM.md",
            "docs/agent-harness-v2/TESTING-GUIDE.md",
            "docs/agent-harness-v2/security/D-ops-supply-chain.md",
            "docs/agent-harness-pending-work.md",
            "docs/test-plan/wp/qa6.md",
            "docs/test-plan/07-work-packages.md",
            "docs/agent-harness-design-review.md",
        ):
            with self.subTest(path=path):
                (error,) = edition_guard.check([path], "").errors
                self.assertIn("project-tracking", error)

    def test_reference_documents_pass(self) -> None:
        for path in (
            "docs/agent-harness-v2/ARCHITECTURE.md",
            "docs/agent-harness-v2/adr/ADR-RUN-07-x.md",
            "docs/agent-harness-v2/guides/operator-guide.md",
            "docs/agent-harness-v2/impl/tool-effects.md",
            "docs/agent-harness-v2/impl/mocks/x.html",
            "docs/agent-harness-v2/ui-cases/x.json",
            "docs/agent-harness-v2/RELEASE-CHECKLIST.md",
            "docs/agent-harness-v2/guides/threat-model.md",
            "docs/test-plan/04-e2e-test-plan.md",
            "docs/test-plan/QA-FINAL.md",
            "docs/agent-harness-design.md",
            "docs/STATUS.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(edition_guard.check([path], "").errors, [])


class PrivateRepository(unittest.TestCase):
    def test_naming_a_private_repository_fails_in_any_file_and_any_spelling(self) -> None:
        for path, text in (
            ("docs/agent-harness-v2/checklist.md", f"| X-1 | the code lives in {PRIVATE}) |"),
            ("README.md", f"see github.com/org/{PRIVATE.upper()}"),
            ("x.py", "# from " + "pipeshub-ai" + "-ee/docs"),
        ):
            with self.subTest(path=path):
                (error,) = edition_guard.check([], diff(path, text)).errors
                self.assertIn("private repository", error)

    def test_a_commit_message_naming_one_fails(self) -> None:
        (error,) = edition_guard.check([], "", [f"docs: point at {PRIVATE}"]).errors
        self.assertIn("private repository", error)

    def test_this_repositorys_own_name_and_the_edition_wording_pass(self) -> None:
        text = "pipeshub-ai/pipeshub-ai, pipeshub-ai-agent-harness and the enterprise edition (EE)"
        self.assertEqual(edition_guard.check([], diff("README.md", text), [text]).errors, [])


class Messages(unittest.TestCase):
    def test_an_edition_prefix_or_a_session_trailer_warns_without_failing(self) -> None:
        report = edition_guard.check([], "", ["ee: add a thing", "EE-12 thing", "fix: x\n\nClaude-Session: abc"])
        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.warnings), 3)

    def test_an_ordinary_message_is_quiet(self) -> None:
        report = edition_guard.check([], "", ["feat(harness): an extension point\n\nSigned-off-by: A <a@b.c>", ""])
        self.assertEqual(report.warnings, [])


class AgainstGit(unittest.TestCase):
    """``collect`` reads what git says changed; run against a throwaway repository."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "guard@example.test")
        self.git("config", "user.name", "guard")
        self.write("backend/python/app/x.py", "x = 1\n")
        self.write(edition_guard.ADMIN_ALLOWLIST, "# none\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        patcher = mock.patch.object(edition_guard, "REPO", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def write(self, path: str, text: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_commits_since_the_base(self) -> None:
        self.git("checkout", "-q", "-b", "change")
        self.write("frontend/ee/x.ts", "export const x = 1;\n")
        self.write("backend/python/app/x.py", "x = 1\nfrom app.ee import y\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "ee: a change")
        added, changed, messages = edition_guard.collect("main", worktree=False)
        report = edition_guard.check(added, changed, messages)
        self.assertEqual(sorted(report.errors), [
            "backend/python/app/x.py:2: imports an edition package; use an extension point instead",
            "frontend/ee/x.ts: an edition-only path; it belongs in that edition's repository",
        ])
        self.assertEqual(len(report.warnings), 1)

    def test_uncommitted_and_untracked_files(self) -> None:
        self.write("backend/python/app/x.py", "x = 1\nimport app.ee\n")
        self.write("docs/ee/new.md", textwrap.dedent(f"""\
            # A note
            {MARKER}
            """))
        added, changed, _ = edition_guard.collect(None, worktree=True)
        report = edition_guard.check(added, changed)
        self.assertEqual(sorted(report.errors), [
            "backend/python/app/x.py:2: imports an edition package; use an extension point instead",
            "docs/ee/new.md: an edition-only path; it belongs in that edition's repository",
            "docs/ee/new.md:2: a proprietary licence marker; this repository's code is Apache-2.0",
        ])

    def test_a_clean_tree_passes_and_main_says_so(self) -> None:
        self.assertEqual(edition_guard.main(["--worktree"]), 0)


OPENAPI_KEPT = textwrap.dedent("""\
    openapi: 3.0.0
    paths:
      /agent-harness/admin/dependencies:
        get:
          responses:
            '403': { description: "The caller is not an org admin" }
      /agent-harness/runs:
        get:
          description: The caller's own runs.
      /users:
        get:
          description: Org admins only.
    components: {}
    """)
LAYER_KEPT = '{"properties": {"schemaVersion": {}, "scope": {}, "updated": {}, "lockedBy": {}, "approvals": {}}}'
ALLOWLIST_KEPT = textwrap.dedent("""\
    # kept
    route GET /agent-harness/admin/dependencies -- operational health
    page labs -- Labs switches
    section approvals -- the deployment's rules
    """)
PAGE = "frontend/app/(main)/workspace/{}/page.tsx"


class AdminSurfaces(unittest.TestCase):
    """Rule 7 on a synthetic tree, then on this repository's own."""

    def tree(self, **files: str) -> tuple[list[str], object]:
        texts = {
            edition_guard.ADMIN_ALLOWLIST: ALLOWLIST_KEPT,
            edition_guard.OPENAPI: OPENAPI_KEPT,
            edition_guard.POLICY_LAYER: LAYER_KEPT,
            PAGE.format("labs"): "export default function Labs() {}\n",
        }
        texts.update({path.replace("__", "/"): text for path, text in files.items()})
        return list(texts), texts.get

    def errors(self, files: dict[str, str]) -> list[str]:
        paths, read = self.tree(**{k.replace("/", "__"): v for k, v in files.items()})
        return edition_guard.check_admin_surfaces(paths, read).errors

    def test_the_kept_surfaces_pass(self) -> None:
        self.assertEqual(self.errors({}), [])

    def test_a_planted_admin_route_fails(self) -> None:
        planted = (
            "  /agent-harness/admin/run-limits:\n"
            "    put:\n"
            "      description: Org admins only.\n"
            "  /control-plane/policy/org/current/quotas:\n"
            "    put:\n"
            "      responses:\n"
            "        '403': { description: \"The caller is not an org admin\" }\n"
        )
        openapi = OPENAPI_KEPT.replace("components: {}", planted + "components: {}")
        self.assertEqual(sorted(e.split(":")[0] for e in self.errors({edition_guard.OPENAPI: openapi})), [
            "route PUT /agent-harness/admin/run-limits",
            "route PUT /control-plane/policy/org/current/quotas",
        ])

    def test_an_admin_checked_express_route_needs_an_allowlisted_route(self) -> None:
        source = "backend/nodejs/apps/src/modules/agent-harness/transport/x-routes.ts"
        kept = "router.get(\n  '/dependencies',\n  auth.authenticate,\n  userAdminCheck,\n  handler,\n);\n"
        self.assertEqual(self.errors({source: kept}), [])
        planted = kept + "router.put('/controls/:controlId', auth.authenticate, userAdminCheck, handler);\n"
        (error,) = self.errors({source: planted})
        self.assertIn("PUT /controls/{controlId} is admin-only", error)

    def test_a_planted_workspace_page_fails_in_either_tree(self) -> None:
        for path in (PAGE.format("run-settings"), "frontend/app/(main)/@sidebar/workspace/run-settings/page.tsx"):
            with self.subTest(path=path):
                (error,) = self.errors({path: "export default function Page() {}\n"})
                self.assertTrue(error.startswith("page run-settings: an org-admin surface"))

    def test_a_planted_policy_section_fails(self) -> None:
        layer = LAYER_KEPT.replace('"approvals": {}', '"approvals": {}, "auditExport": {}')
        (error,) = self.errors({edition_guard.POLICY_LAYER: layer})
        self.assertTrue(error.startswith("section auditExport: an org-admin surface"))

    def test_a_line_without_a_reason_and_a_missing_allowlist_fail(self) -> None:
        (error,) = self.errors({edition_guard.ADMIN_ALLOWLIST: ALLOWLIST_KEPT + "page labs\n"})
        self.assertIn("not `<route|page|section> <name> -- <reason>`", error)
        paths, read = self.tree()
        texts = {p: read(p) for p in paths if p != edition_guard.ADMIN_ALLOWLIST}
        (missing,) = edition_guard.check_admin_surfaces(list(texts), texts.get).errors
        self.assertIn("missing", missing)

    def test_an_entry_the_tree_does_not_have_is_a_warning(self) -> None:
        paths, read = self.tree(**{edition_guard.ADMIN_ALLOWLIST.replace("/", "__"): ALLOWLIST_KEPT + "page gone -- was here\n"})
        report = edition_guard.check_admin_surfaces(paths, read)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [f"{edition_guard.ADMIN_ALLOWLIST}: names page gone, which this tree does not have"])

    def test_this_repository_keeps_only_what_its_allowlist_names(self) -> None:
        self.assertEqual(edition_guard.check_admin_surfaces(*edition_guard.tree(worktree=True)).errors, [])


if __name__ == "__main__":
    unittest.main()

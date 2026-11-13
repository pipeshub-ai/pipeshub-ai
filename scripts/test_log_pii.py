"""Emails and names in log calls are found, reviewed exemptions are counted, and only new findings fail a pull request.

Run: TS_MODULE=<backend/nodejs/apps/node_modules/typescript> python3 -m unittest discover -s scripts -p 'test_*.py'
The TypeScript tests need that package (npm ci in backend/nodejs/apps sets it up); they fail without it, never skip.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import log_pii  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
NEW_FINDINGS = REPO / ".github" / "scripts" / "new_lint_findings.py"
TS_MODULE = os.path.abspath(os.environ.get("TS_MODULE") or log_pii.DEFAULT_TS_MODULE)


def py(source: str) -> list[tuple[int, str]]:
    return log_pii.findings(log_pii.scan(source), log_pii.GATED)


def classes(source: str) -> list[str]:
    return [h["cls"] for h in log_pii.scan(source)]


class TestPythonFindings(unittest.TestCase):
    def test_personal_data_in_log_calls_is_found(self) -> None:
        for source in (
            'logger.info(f"sync for {user.email}")',
            'self.logger.warning("user %s", payload["email"])',
            'logger.debug("x %s", m.get("emailAddress"))',
            'logger.info("x %s", getattr(u, "username", "?"))',
            'logger.info("%s", ", ".join(m.email for m in members))',
            'logger.info("%s", str(email))',
            'logger.info("sync for %s" % user.email)',
            'logger.info("sync for %s, %s" % (user.email, n))',
            'logger.info(f"{email.lower()}")',
            'logger.info("x", extra={"email": value})',
            'logger.info("x", extra={"k": user.email})',
            'log_event(logger, "user.synced", user_email=value)',
            'log_failure(logger, "user.failed", exc, owner_email=value)',
            'self._log.info(f"{user_email}")',
            'logging.warning(f"{user_email}")',
            'logger.log(level, "%s", user_email)',
            'logger.info(f"{userPrincipalName}")',
            'logger.info(f"{member.get(\'displayName\')}")',
            'logger.info(f"{lead_display_name}")',
            'logger.info(f"{member.name}")',
            'logger.info(f"{zammad_user.full_name}")',
            'logger.info(f"{self.mailbox}")',
            'logger.info(f"{mailbox_email}")',
            'logger.debug(f"{users}")',
            'logger.warning(f"{permission.email}")',
            'state_logger.info(f"{user_email}")',
            'logger.info("x %s", m.get("id", user.email))',
            'logger.info("x %s", getattr(m, "id", user.email))',
            'log_failure(logger, "user.failed", exc, **{"email": value})',
            'log_event(logger, "user.synced", **{"k": user.email})',
        ):
            with self.subTest(source=source):
                found = py(source)
                self.assertTrue(found)
                self.assertEqual(set(found), {(1, log_pii.CODE)})

    def test_other_names_and_values_are_not_findings(self) -> None:
        for source in (
            'logger.info(f"{connector_name}")',
            'logger.info(f"{self.connector.display_name}")',
            'logger.info(f"{file_name}")',
            'logger.info(f"{repo.full_name}")',
            'logger.info(f"{full_name}")',
            'logger.info(f"{group.display_name}")',
            'logger.info(f"{role.get(\'display_name\')}")',
            'logger.info(f"{tool_full_name}")',
            'logger.info(f"{len(emails)}")',
            'logger.info(f"{bool(user.email)}")',
            'logger.info(f"{email_count}")',
            'logger.info(f"{no_email}")',
            'logger.info(f"{mail_error}")',
            'logger.info(f"{type(e).__name__}")',
            'logger.info(f"{user.id} {user_id}")',
            'logger.info(f"{name} {user}")',
            'warnings.warn(f"{email}")',
            'print(email)',
            'logger.info("done")  # sent to alice@example.test',
            'self.error(f"{email}")',
            'catalog.info(f"{email}")',
            'logger.info(f"{group.email} {team.mail}")',
            'logger.info(f"{cfg.mail_server} {email_format} {email_hash} {email_backend}")',
            'logger.info(f"{emails_processed} {mailbox_name} {email_data}")',
            'logger.info("x", extra={"user": user_id, "users": n})',
        ):
            with self.subTest(source=source):
                self.assertEqual(py(source), [])

    def test_classes_of_names_that_are_reported_but_not_gated(self) -> None:
        self.assertEqual(classes('logger.info(f"{connector_name}")'), ["fp-connector"])
        self.assertEqual(classes('logger.info(f"{name}")'), ["ambiguous-name"])
        self.assertEqual(classes('logger.info(f"{user}")'), ["user-ref"])

    def test_one_finding_per_expression_at_its_calls_first_line(self) -> None:
        source = 'x = 1\nlogger.info(\n    "%s %s",\n    user.email,\n    user.full_name,\n)\n'
        self.assertEqual(py(source), [(2, log_pii.CODE), (2, log_pii.CODE)])

    def test_the_same_expression_twice_on_one_line_counts_once(self) -> None:
        self.assertEqual(py('logger.info("%s %s", user.email, user.email)'), [(1, log_pii.CODE)])

    def test_two_calls_on_one_line_count_twice(self) -> None:
        self.assertEqual(py('logger.info(user.email); logger.info(user.email)'), [(1, log_pii.CODE), (1, log_pii.CODE)])

    def test_a_file_that_does_not_parse_has_no_findings(self) -> None:
        self.assertEqual(py('logger.info(f"{email}"'), [])


class TestPythonAnnotations(unittest.TestCase):
    def test_an_annotation_on_the_flagged_line_exempts_it(self) -> None:
        self.assertEqual(py('logger.info(f"{email}")  # log-pii: ok test fixture'), [(1, log_pii.CODE_OK)])
        self.assertEqual(py('logger.info(\n    "%s",\n    email,  # log-pii: ok test fixture\n)'), [(1, log_pii.CODE_OK)])

    def test_an_annotation_elsewhere_in_the_call_does_not(self) -> None:
        self.assertEqual(py('logger.info(\n    f"{email}",\n)  # log-pii: ok test fixture'), [(1, log_pii.CODE)])
        self.assertEqual(py('logger.info(f"{email}")\nx = 1  # log-pii: ok test fixture'), [(1, log_pii.CODE)])

    def test_a_new_argument_beside_an_annotated_one_fails(self) -> None:
        source = 'logger.warning(\n    "%s %s",\n    member.name,  # log-pii: ok tar member path\n    u.email,\n)'
        self.assertEqual(sorted(py(source)), [(1, log_pii.CODE), (1, log_pii.CODE_OK)])

    def test_each_annotated_expression_counts_once(self) -> None:
        one = 'logger.warning("%s", member.name)  # log-pii: ok tar member path'
        two = 'logger.warning("%s %s", member.name, member.email)  # log-pii: ok tar member path'
        self.assertEqual(py(one), [(1, log_pii.CODE_OK)])
        self.assertEqual(py(two), [(1, log_pii.CODE_OK), (1, log_pii.CODE_OK)])

    def test_an_annotation_needs_a_reason(self) -> None:
        self.assertEqual(py('logger.info(f"{email}")  # log-pii: ok'), [(1, log_pii.CODE)])

    def test_an_annotation_inside_a_string_does_not_count(self) -> None:
        self.assertEqual(py('logger.info(f"{email} # log-pii: ok fake")'), [(1, log_pii.CODE)])


class TestJsonOutput(unittest.TestCase):
    def test_prints_ruff_shaped_json_with_both_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text('logger.info(f"{email}")\nlogger.info(f"{email}")  # log-pii: ok fixture\n', encoding="utf-8")
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "log_pii.py"), str(path)],
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [
            {"filename": str(path), "code": "LOG-PII", "location": {"row": 1}},
            {"filename": str(path), "code": "LOG-PII-OK", "location": {"row": 2}},
        ])

    def test_a_typescript_file_without_typescript_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.ts"
            path.write_text("logger.info(email);\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(REPO / "scripts" / "log_pii.py"), str(path)],
                                    env={**os.environ, "TS_MODULE": str(Path(tmp) / "missing")},
                                    capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("typescript is not installed", result.stderr)


class TestTypeScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.path.isdir(TS_MODULE):
            raise AssertionError(f"typescript not found at {TS_MODULE}: run npm ci in backend/nodejs/apps or set TS_MODULE")
        os.environ["TS_MODULE"] = TS_MODULE

    def ts(self, source: str) -> list[tuple[int, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.ts")
            with open(path, "w", encoding="utf-8") as f:
                f.write(source)
            return log_pii.findings(log_pii.ts_hits([path])[path], log_pii.GATED_TS)

    def test_personal_data_in_log_calls_is_found(self) -> None:
        for source in (
            "logger.info(`sync for ${email}`);",
            "this.logger.warn('blocked', email);",
            "this.logger.info('x', { email });",
            "this.logger.info('x', { oldEmail: target.email });",
            "this.logger.debug('x', payload?.email);",
            "this.logger.debug(users);",
            "this.logger.info('x', { emails: users.map((u) => u.email) });",
            "console.log(user.fullName);",
            "this.logger.warn(String(user.email));",
            "this.logger.info('x', { user });",
            "this.logger.info('x', { ...user });",
            "this.logger.info('x', req.user);",
            "this.auditLogger.info('x', { email });",
            "this.logger.info('x', { list: users.map((u) => { return u.email; }) });",
            "this.logger.info('x', { list: users.map(function (u) { return u.email; }) });",
        ):
            with self.subTest(source=source):
                found = self.ts(source)
                self.assertTrue(found)
                self.assertEqual(set(found), {(1, log_pii.CODE)})

    def test_other_names_and_values_are_not_findings(self) -> None:
        for source in (
            "this.logger.info('x', { userId });",
            "this.logger.info('x', { connectorName });",
            "this.logger.info(`${file.name}`);",
            "this.logger.error('x', { name: error.name });",
            "this.logger.info('x', { count: emails.length });",
            "foo.info(email);",
            "catalogService.info(user.email);",
            "this.logger.info('x', { user: userId });",
            "this.logger.info('x', { mailServer, emailFormat });",
            "this.logger.info('x', { ids: users.map((u) => { const f = () => u.email; return u.id; }) });",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.ts(source), [])

    def test_two_calls_on_one_line_count_twice(self) -> None:
        self.assertEqual(self.ts("this.logger.info(email); this.logger.warn(email);"), [(1, log_pii.CODE), (1, log_pii.CODE)])

    def test_annotations(self) -> None:
        self.assertEqual(self.ts("this.logger.warn('blocked', email); // log-pii: ok fixture"), [(1, log_pii.CODE_OK)])
        self.assertEqual(self.ts("this.logger.warn('blocked', email); // log-pii: ok"), [(1, log_pii.CODE)])
        self.assertEqual(self.ts("this.logger.warn('blocked // log-pii: ok fake', email);"), [(1, log_pii.CODE)])
        self.assertEqual(self.ts("this.logger.info(\n  'x', // log-pii: ok fixture\n  email,\n);"), [(1, log_pii.CODE)])


class TestOnlyNewFindingsFail(unittest.TestCase):
    """new_lint_findings.py log-pii against a throwaway git repository, for a Python and a TypeScript file."""

    PY = "backend/python/app/m.py"
    TS = "backend/nodejs/apps/src/m.ts"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        for rel in (self.PY, self.TS):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "main")
        self._commit({self.PY: 'logger.info(f"{user.email}")\n', self.TS: "this.logger.warn('blocked', email);\n"})
        self.base = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, text=True,
                              check=True).stdout

    def _commit(self, files: dict[str, str]) -> None:
        for rel, source in files.items():
            (self.repo / rel).write_text(source, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "c")

    def _check(self, *files: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(NEW_FINDINGS), "log-pii", self.base, *(files or (self.PY, self.TS))],
            cwd=self.repo, env={**os.environ, "TS_MODULE": TS_MODULE}, capture_output=True, text=True, check=False,
        )

    def _assert_one_new_site_fails(self, rel: str, source: str) -> None:
        self._commit({rel: source})
        result = self._check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{rel}: 1 new LOG-PII (main has 1", result.stdout)

    def test_a_new_python_site_fails(self) -> None:
        self._assert_one_new_site_fails(self.PY, 'logger.info(f"{user.email}")\nlogger.error("%s", member.full_name)\n')

    def test_a_new_typescript_site_fails(self) -> None:
        self._assert_one_new_site_fails(self.TS, "this.logger.warn('blocked', email);\nthis.logger.info('x', { email });\n")

    def test_rewording_or_removing_existing_sites_passes(self) -> None:
        self._commit({self.PY: 'x = 1\nlogger.info(f"reworded {user.email}")\n', self.TS: "const x = 1;\n"})
        result = self._check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already on main", result.stdout)
        self.assertEqual((self.repo / self.PY).read_text(encoding="utf-8"), 'x = 1\nlogger.info(f"reworded {user.email}")\n')

    def test_a_new_exemption_warns_but_passes(self) -> None:
        # Annotating the old site frees its slot for a new one; the new LOG-PII-OK is what the reviewer sees.
        self._commit({self.PY: 'logger.info(f"{user.email}")  # log-pii: ok bogus\nlogger.info(f"{owner_email}")\n'})
        result = self._check(self.PY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning file=backend/python/app/m.py", result.stdout)
        self.assertIn("1 new LOG-PII-OK", result.stdout)

    def test_a_second_expression_on_an_annotated_line_warns(self) -> None:
        self._commit({self.PY: 'logger.info("%s", member.name)  # log-pii: ok tar member path\n'})
        self.base = self._git("rev-parse", "HEAD").strip()
        self._commit({self.PY: 'logger.info("%s %s", member.name, member.email)  # log-pii: ok tar member path\n'})
        result = self._check(self.PY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 new LOG-PII-OK", result.stdout)

    def test_a_value_added_to_a_call_that_already_leaks_fails(self) -> None:
        for head in ('logger.info(f"{user.email} {user.full_name}")\n',
                     'logger.info(\n    f"{user.email}"\n    f"{user.first_name}"\n)\n'):
            with self.subTest(head=head):
                self._commit({self.PY: head})
                result = self._check(self.PY)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("1 new LOG-PII (main has 1, this pull request has 2)", result.stdout)

    def test_a_second_call_on_a_line_that_already_leaks_fails(self) -> None:
        self._commit({self.PY: 'logger.info(f"{user.email}"); logger.info(f"{user.email}")\n'})
        result = self._check(self.PY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1 new LOG-PII (main has 1, this pull request has 2)", result.stdout)

    def test_the_already_on_main_notice_leaves_out_exemptions(self) -> None:
        self._commit({self.PY: 'logger.info(f"{user.email}")\nlogger.info(f"{owner_email}")  # log-pii: ok fixture\n'})
        self.base = self._git("rev-parse", "HEAD").strip()
        self._commit({self.PY: 'x = 1\nlogger.info(f"{user.email}")\nlogger.info(f"{owner_email}")  # log-pii: ok fixture\n'})
        result = self._check(self.PY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::notice::1 log-pii findings in these files were already on main", result.stdout)

    def test_a_renamed_file_is_compared_with_its_old_path(self) -> None:
        moved = "backend/python/app/n.py"
        self._git("mv", self.PY, moved)
        self._git("commit", "-q", "-m", "mv")
        result = self._check(moved)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

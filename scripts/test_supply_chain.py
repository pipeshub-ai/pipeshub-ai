"""Supply-chain rules (SEC-SC-07..11): each is shown to fail on a seeded bad case and to pass on this repository.

Run: python3 -m unittest discover -s scripts -p 'test_supply_chain.py'
backend/python/tests/cohesion/test_sec_sc_supply_chain.py collects these same cases into the pytest gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import supply_chain as sc  # noqa: E402

H1, H2, H3 = "a" * 64, "b" * 64, "c" * 64
REAL_REPO = Path(__file__).resolve().parent.parent

PYPROJECT = """\
[project]
name = "fixture"
version = "0"
requires-python = ">=3.12"
dependencies = [
    "requests==2.34.2",
    "litellm>=1.79.2,<1.107",
    "etcd3 @ git+https://github.com/kragniz/python-etcd3.git@e58a899579ba416449c4e225b61f039457c8072a",
]

[project.optional-dependencies]
dev = ["pytest"]

[tool.uv]
exclude-dependencies = ["cchardet"]
"""


def _pin(name: str, version: str, *hashes: str) -> str:
    return f"{name}=={version} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes) + "\n    # via fixture\n"


BASE = "pipeshubai/pipeshub-ai-base:runtime@sha256:" + "e" * 64


class Fixture:
    """A small repository that passes every rule; each test breaks one thing."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.write("backend/python/pyproject.toml", PYPROJECT)
        self.write("backend/python/requirements/build.in", "setuptools\n")
        self.write("backend/python/requirements/tools.in", "ruff==0.11.9\n")
        runtime = _pin("requests", "2.34.2", H1) + _pin("litellm", "1.106.2", H2, H3)
        self.locks = {
            "runtime": runtime,
            "dev": runtime + _pin("pytest", "9.1.1", H3),
            "build": _pin("setuptools", "81.0.0", H1),
            "tools": _pin("ruff", "0.11.9", H2),
        }
        self.write_locks()
        self.write("backend/python/requirements/vcs.lock",
                   "etcd3 @ git+https://github.com/kragniz/python-etcd3.git@e58a899579ba416449c4e225b61f039457c8072a\n")
        self.write("contracts/supply-chain/denylist.json", json.dumps(
            {"python": [{"name": "litellm", "versions": ["1.107.0"], "status": "confirmed"}],
             "npm": [{"name": "event-stream", "versions": ["3.3.6"], "status": "confirmed"}]}))
        self.write("contracts/supply-chain/image-pins.json", json.dumps(
            {"tagOnly": [{"ref": "pipeshubai/pipeshub-ai-base:runtime", "category": "first-party", "files": ["Dockerfile"]}]}))
        self.write("Dockerfile", textwrap.dedent("""\
            ARG PY=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
            FROM ${PY} AS build
            RUN sh requirements/install.sh runtime --project . --editable
            FROM pipeshubai/pipeshub-ai-base:runtime AS web
            RUN npm ci --ignore-scripts
            FROM build
            """))
        self.write(".github/workflows/ci.yml", textwrap.dedent("""\
            jobs:
              test:
                steps:
                  # pip install anything   (a comment is not a command)
                  - run: |
                      backend/python/requirements/install.sh dev --project . --editable
                      pip install --require-hashes --no-deps -r backend/python/requirements/tools.lock
                      npm ci --ignore-scripts
                      uv sync --locked
            """))
        self.write("CONTRIBUTING.md", "Run `npm install` in prose is fine.\n\n```bash\nnpm ci\n```\n")
        self.write("frontend/package.json", json.dumps({"name": "f", "dependencies": {"left-pad": "1.3.0"}}))
        self.lock_json = {"lockfileVersion": 3, "packages": {
            "": {"name": "f"},
            "node_modules/left-pad": {"version": "1.3.0", "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                                      "integrity": "sha512-" + "A" * 86 + "=="}}}
        self.write_node_lock()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_locks(self) -> None:
        inputs = sc.fingerprint(self.root)
        for name, body in self.locks.items():
            self.write(f"backend/python/requirements/{name}.lock", sc._header(name, inputs, "2026-10-26T00:00:00Z") + body)

    def write_node_lock(self) -> None:
        self.write("frontend/package-lock.json", json.dumps(self.lock_json, indent=2))

    def rules(self) -> list[str]:
        return [f.rule for f in sc.run_checks(self.root)]


class SupplyChainCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="pxh-sc-")
        self.fx = Fixture(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertOnly(self, rule: str) -> list[sc.Finding]:  # noqa: N802 - unittest naming
        findings = sc.run_checks(self.fx.root)
        self.assertTrue(findings, f"expected a {rule} finding")
        self.assertEqual({f.rule for f in findings}, {rule}, "\n".join(map(str, findings)))
        return findings


class TestSecSc07HashedPythonLocks(SupplyChainCase):
    """SEC-SC-07: every Python install is from a lock that pins each package to sha256 hashes."""

    def test_sec_sc_07_the_fixture_passes(self) -> None:
        self.assertEqual(sc.run_checks(self.fx.root), [])

    def test_sec_sc_07_an_entry_without_a_hash_fails(self) -> None:
        self.fx.locks["runtime"] = "requests==2.34.2\n" + _pin("litellm", "1.106.2", H2, H3)
        self.fx.locks["dev"] = self.fx.locks["runtime"] + _pin("pytest", "9.1.1", H3)
        self.fx.write_locks()
        findings = sc.run_checks(self.fx.root)
        # An unhashed line is not a lock entry, so the requirement also reads as unlocked.
        self.assertEqual({f.rule for f in findings}, {"PY-LOCK-HASH", "PY-LOCK-AGREE"})
        self.assertTrue(all("requests" in f.message for f in findings))

    def test_sec_sc_07_a_range_instead_of_a_pin_fails(self) -> None:
        self.fx.locks["build"] = f"setuptools>=81 --hash=sha256:{H1}\n"
        self.fx.write_locks()
        self.assertEqual(set(self.fx.rules()), {"PY-LOCK-HASH", "PY-LOCK-AGREE"})

    def test_sec_sc_07_an_index_option_in_a_lock_fails(self) -> None:
        self.fx.locks["tools"] = "--extra-index-url https://evil.example/simple\n" + self.fx.locks["tools"]
        self.fx.write_locks()
        self.assertOnly("PY-LOCK-INDEX")

    def test_sec_sc_07_a_git_requirement_not_pinned_to_a_commit_fails(self) -> None:
        self.fx.write("backend/python/requirements/vcs.lock", "etcd3 @ git+https://github.com/kragniz/python-etcd3.git@main\n")
        self.assertEqual(set(self.fx.rules()), {"PY-LOCK-VCS", "PY-LOCK-AGREE"})

    def test_sec_sc_07_pip_install_without_hashes_fails_in_a_workflow_and_a_dockerfile(self) -> None:
        self.fx.write(".github/workflows/bad.yml", "jobs:\n  a:\n    steps:\n      - run: pip install uv\n")
        self.fx.write("deploy/Dockerfile", "FROM scratch\nRUN uv pip install --system \\\n    -e .\n")
        findings = self.assertOnly("PY-INSTALL")
        self.assertEqual(sorted(f.path for f in findings), [".github/workflows/bad.yml:4", "deploy/Dockerfile:2"])

    def test_sec_sc_07_a_uv_lock_file_without_a_hash_fails(self) -> None:
        self.fx.write("integration-tests/uv.lock", textwrap.dedent(f"""\
            version = 1
            [[package]]
            name = "six"
            version = "1.17.0"
            source = {{ registry = "https://pypi.org/simple" }}
            sdist = {{ url = "https://files.example/six-1.17.0.tar.gz", hash = "sha256:{H1}" }}
            wheels = [{{ url = "https://files.example/six-1.17.0-py3-none-any.whl" }}]
            """))
        findings = self.assertOnly("PY-LOCK-HASH")
        self.assertIn("six==1.17.0", findings[0].message)

    def test_sec_sc_07_uv_sync_must_be_locked(self) -> None:
        self.fx.write(".github/workflows/bad.yml", "jobs:\n  a:\n    steps:\n      - run: uv sync\n")
        self.assertOnly("PY-INSTALL")

    def test_sec_sc_07_the_contributing_guide_is_checked_inside_code_blocks_only(self) -> None:
        self.fx.write("CONTRIBUTING.md", "Prose: pip install x.\n\n```bash\npip install uv\n```\n")
        findings = self.assertOnly("PY-INSTALL")
        self.assertEqual(findings[0].path, "CONTRIBUTING.md:4")

    def test_sec_sc_07_a_hash_the_index_does_not_publish_fails_verification(self) -> None:
        published = {H1: "requests-2.34.2-py3-none-any.whl", H2: "litellm-1.106.2-py3-none-any.whl",
                     H3: "pytest-9.1.1-py3-none-any.whl"}
        with mock.patch.object(sc, "published_files", lambda name, version, cache: dict(published)), \
                mock.patch("sys.stdout"):
            self.assertEqual(sc.cmd_verify_hashes(self.fx.root), 0)
        del published[H3]
        with mock.patch.object(sc, "published_files", lambda name, version, cache: dict(published)), \
                mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            self.assertEqual(sc.cmd_verify_hashes(self.fx.root), 1)

    def test_sec_sc_07_a_release_with_no_file_for_a_target_platform_fails_verification(self) -> None:
        published = {H1: "requests-2.34.2-cp312-cp312-win_amd64.whl", H2: "litellm-1.106.2-py3-none-any.whl",
                     H3: "pytest-9.1.1-py3-none-any.whl"}
        with mock.patch.object(sc, "published_files", lambda name, version, cache: dict(published)), \
                mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            self.assertEqual(sc.cmd_verify_hashes(self.fx.root), 1)

    def test_sec_sc_07_install_sh_refuses_an_unknown_set_and_another_python(self) -> None:
        script = REAL_REPO / "backend/python/requirements/install.sh"
        bad_set = subprocess.run(["sh", str(script), "everything"], capture_output=True, text=True, check=False)
        self.assertEqual(bad_set.returncode, 2)
        fake = Path(self._tmp.name) / "python"
        fake.write_text("#!/bin/sh\nexec %s -c 'import sys; sys.exit(\"the locks are resolved for CPython 3.12; this is pypy\")'\n"
                        % sys.executable)
        fake.chmod(0o755)
        other = subprocess.run(["sh", str(script), "runtime"], capture_output=True, text=True, check=False,
                               env={**os.environ, "PYTHON": str(fake)})
        self.assertNotEqual(other.returncode, 0)
        self.assertIn("CPython 3.12", other.stderr)

    def test_sec_sc_07_install_sh_installs_every_lock_with_require_hashes(self) -> None:
        text = (REAL_REPO / "backend/python/requirements/install.sh").read_text(encoding="utf-8")
        for target in ('"$here/build.lock"', '"$here/$set_name.lock"'):
            line = next(line for line in text.splitlines() if target in line)
            self.assertIn("--require-hashes", line)
        self.assertIn("--no-deps", text)

    def test_sec_sc_07_git_requirements_are_split_out_of_the_resolver_output(self) -> None:
        body, vcs = sc._split_vcs(
            "etcd3 @ git+https://github.com/x/y.git@" + "1" * 40 + " ; sys_platform == 'linux'\n"
            "    # via fixture\n" + _pin("six", "1.17.0", H1))
        self.assertEqual(vcs, ["etcd3 @ git+https://github.com/x/y.git@" + "1" * 40 + " ; sys_platform == 'linux'"])
        self.assertTrue(body.startswith("six==1.17.0"))


class TestSecSc08LockAgreesWithPyproject(SupplyChainCase):
    """SEC-SC-08: a lock older than pyproject.toml, or one that disagrees with it, fails."""

    def test_sec_sc_08_a_changed_pyproject_makes_every_lock_stale(self) -> None:
        self.fx.write("backend/python/pyproject.toml", PYPROJECT.replace('"requests==2.34.2"', '"requests==2.35.0"'))
        rules = self.fx.rules()
        self.assertEqual(rules.count("PY-LOCK-STALE"), 4)
        self.assertIn("PY-LOCK-AGREE", rules)

    def test_sec_sc_08_a_changed_tools_in_makes_the_locks_stale(self) -> None:
        self.fx.write("backend/python/requirements/tools.in", "ruff==0.11.9\nwatchdog\n")
        self.assertEqual(set(self.fx.rules()), {"PY-LOCK-STALE", "PY-LOCK-AGREE"})

    def test_sec_sc_08_a_lock_edited_to_a_version_the_pin_refuses_fails(self) -> None:
        self.fx.locks["runtime"] = _pin("requests", "2.33.0", H1) + _pin("litellm", "1.106.2", H2, H3)
        self.fx.locks["dev"] = self.fx.locks["runtime"] + _pin("pytest", "9.1.1", H3)
        self.fx.write_locks()
        findings = self.assertOnly("PY-LOCK-AGREE")
        self.assertIn("requests==2.33.0 does not satisfy requests==2.34.2", findings[0].message)

    def test_sec_sc_08_a_required_package_missing_from_the_lock_fails(self) -> None:
        self.fx.locks["dev"] = self.fx.locks["runtime"]
        self.fx.write_locks()
        findings = self.assertOnly("PY-LOCK-AGREE")
        self.assertIn("pytest is required but not locked", findings[0].message)

    def test_sec_sc_08_an_excluded_dependency_in_a_lock_fails(self) -> None:
        self.fx.locks["runtime"] += _pin("cchardet", "2.4.0", H1)
        self.fx.locks["dev"] += _pin("cchardet", "2.4.0", H1)
        self.fx.write_locks()
        self.assertOnly("PY-LOCK-AGREE")

    def test_sec_sc_08_runtime_and_dev_must_lock_the_same_files(self) -> None:
        self.fx.locks["dev"] = _pin("requests", "2.34.2", H3) + _pin("litellm", "1.106.2", H2, H3) + _pin("pytest", "9.1.1", H3)
        self.fx.write_locks()
        self.assertOnly("PY-LOCK-SUBSET")


class TestSecSc09Denylist(SupplyChainCase):
    """SEC-SC-09: no lock resolves to a denylisted release, and no direct requirement admits one."""

    def test_sec_sc_09_a_denylisted_python_release_in_a_lock_fails(self) -> None:
        self.fx.write("backend/python/pyproject.toml", PYPROJECT.replace('"litellm>=1.79.2,<1.107"', '"litellm>=1.79.2"'))
        self.fx.locks["runtime"] = _pin("requests", "2.34.2", H1) + _pin("litellm", "1.107.0", H2)
        self.fx.locks["dev"] = self.fx.locks["runtime"] + _pin("pytest", "9.1.1", H3)
        self.fx.write_locks()
        findings = self.assertOnly("DENYLIST")
        paths = {f.path.split(":")[0] for f in findings}
        self.assertEqual(paths, {"backend/python/requirements/runtime.lock", "backend/python/requirements/dev.lock",
                                 "backend/python/pyproject.toml"})

    def test_sec_sc_09_a_requirement_that_admits_a_denylisted_release_fails(self) -> None:
        self.fx.write("backend/python/pyproject.toml", PYPROJECT.replace('"litellm>=1.79.2,<1.107"', '"litellm>=1.79.2"'))
        self.fx.write_locks()
        findings = self.assertOnly("DENYLIST")
        self.assertIn("admits denylisted litellm 1.107.0", findings[0].message)

    def test_sec_sc_09_a_denylisted_npm_release_fails(self) -> None:
        self.fx.lock_json["packages"]["node_modules/event-stream"] = {
            "version": "3.3.6", "resolved": "https://registry.npmjs.org/event-stream/-/event-stream-3.3.6.tgz",
            "integrity": "sha512-" + "B" * 86 + "=="}
        self.fx.write_node_lock()
        self.assertOnly("DENYLIST")

    def test_sec_sc_09_a_denylisted_release_in_a_uv_lock_fails(self) -> None:
        self.fx.write("integration-tests/uv.lock", 'version = 1\n[[package]]\nname = "litellm"\nversion = "1.107.0"\n')
        self.assertOnly("DENYLIST")

    def test_sec_sc_09_the_uv_projects_requirements_are_checked_too(self) -> None:
        self.fx.write("integration-tests/pyproject.toml", '[project]\nname = "it"\ndependencies = ["litellm>=1.79"]\n')
        findings = self.assertOnly("DENYLIST")
        self.assertEqual(findings[0].path, "integration-tests/pyproject.toml")

    def test_sec_sc_09_the_incident_release_is_on_the_denylist(self) -> None:
        self.assertIn("1.107.0", sc.load_denylist(REAL_REPO)["python"]["litellm"])

    def test_r15_sc_1_an_entry_must_say_whether_its_report_is_confirmed(self) -> None:
        # R15-SC-1: an entry without a status (or an unknown one) cannot tell a reader which reports still need
        # checking; the check names it.
        for status in (None, "maybe"):
            item = {"name": "litellm", "versions": ["1.107.0"]} | ({} if status is None else {"status": status})
            self.fx.write("contracts/supply-chain/denylist.json", json.dumps({"python": [item], "npm": []}))
            findings = self.assertOnly("DENYLIST")
            self.assertIn("status", findings[0].message)

    def test_r15_sc_2_a_release_to_verify_is_denied_as_a_confirmed_one_is(self) -> None:
        # R15-SC-2: "verify" marks a public report PipesHub has not confirmed; the release is still refused.
        self.fx.write("contracts/supply-chain/denylist.json", json.dumps(
            {"python": [{"name": "litellm", "versions": ["1.106.2"], "status": "verify"}], "npm": []}))
        findings = self.assertOnly("DENYLIST")
        self.assertIn("litellm==1.106.2 is denylisted", "\n".join(f.message for f in findings))

    def test_r15_sc_3_the_unconfirmed_litellm_releases_stay_denied_and_marked_verify(self) -> None:
        data = json.loads((REAL_REPO / sc.DENYLIST).read_text(encoding="utf-8"))
        status = {(i["name"], v): i.get("status") for i in data["python"] for v in i["versions"]}
        self.assertEqual(status[("litellm", "1.107.0")], "confirmed")
        self.assertEqual(status[("litellm", "1.82.7")], "verify")
        self.assertEqual(status[("litellm", "1.82.8")], "verify")


class TestSecSc10NodeInstalls(SupplyChainCase):
    """SEC-SC-10: Node installs are `npm ci` against a lock with an integrity hash for every package."""

    def test_sec_sc_10_npm_install_fails_in_a_dockerfile_a_workflow_and_the_guide(self) -> None:
        self.fx.write("web/Dockerfile", "FROM scratch\nRUN npm install -g pm2\n")
        self.fx.write(".github/workflows/web.yml", "jobs:\n  a:\n    steps:\n      - run: |\n          cd frontend\n          npm install\n")
        self.fx.write("CONTRIBUTING.md", "```bash\nnpm i\n```\n")
        findings = self.assertOnly("NODE-CI")
        self.assertEqual(sorted(f.path for f in findings),
                         [".github/workflows/web.yml:6", "CONTRIBUTING.md:2", "web/Dockerfile:2"])

    def test_sec_sc_10_npm_ci_and_npm_uninstall_pass(self) -> None:
        self.fx.write("web/Dockerfile", "FROM scratch\nRUN npm ci --omit=dev --ignore-scripts && npm uninstall x && npm run build\n")
        self.assertEqual(self.fx.rules(), [])

    def test_sec_sc_10_a_lock_entry_without_integrity_fails(self) -> None:
        del self.fx.lock_json["packages"]["node_modules/left-pad"]["integrity"]
        self.fx.write_node_lock()
        findings = self.assertOnly("NODE-INTEGRITY")
        self.assertIn("node_modules/left-pad has no sha512 integrity", findings[0].message)

    def test_sec_sc_10_a_lock_entry_resolved_off_the_registry_fails(self) -> None:
        self.fx.lock_json["packages"]["node_modules/left-pad"]["resolved"] = "https://evil.example/left-pad.tgz"
        self.fx.write_node_lock()
        self.assertOnly("NODE-INTEGRITY")

    def _vendor(self, resolved: str, data: bytes = b"tarball bytes", integrity: str | None = None) -> None:
        self.fx.write("frontend/vendor/pad-1.0.0.tgz", "")
        (self.fx.root / "frontend/vendor/pad-1.0.0.tgz").write_bytes(data)
        digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        self.fx.lock_json["packages"]["node_modules/pad"] = {"version": "1.0.0", "resolved": resolved,
                                                            "integrity": digest if integrity is None else integrity}
        self.fx.write_node_lock()

    def test_sec_sc_10_a_vendored_tarball_inside_the_repository_with_its_hash_passes(self) -> None:
        self._vendor("file:vendor/pad-1.0.0.tgz")
        self.assertEqual(sc.run_checks(self.fx.root), [])

    def test_sec_sc_10_a_vendored_tarball_is_refused_unless_it_is_in_the_repository_and_matches(self) -> None:
        other = "sha512-" + base64.b64encode(hashlib.sha512(b"other bytes").digest()).decode()
        cases = {
            "is not exactly the sha512 of the file": ("file:vendor/pad-1.0.0.tgz", other),
            "is not a vendored tarball in the repository": ("file:vendor/missing-1.0.0.tgz", None),
            "is not a path inside the repository": ("file:../outside/pad-1.0.0.tgz", None),
            "is not a path inside the repository ": ("file:/tmp/pad-1.0.0.tgz", None),
            "has no sha512 integrity": ("file:vendor/pad-1.0.0.tgz", ""),
            "resolves outside the npm registry": ("git+https://example.com/pad.git#0123", None),
        }
        for why, (resolved, integrity) in cases.items():
            with self.subTest(resolved=resolved, integrity=integrity):
                self._vendor(resolved, integrity=integrity)
                (finding,) = self.assertOnly("NODE-INTEGRITY")
                self.assertIn(why.strip(), finding.message)

    def test_sec_sc_10_a_target_npm_resolves_elsewhere_is_refused_even_with_a_matching_decoy(self) -> None:
        """npm-package-arg reads a file: target as a URL: a leading ~ is the home directory, and `.`, `..`,
        percent escapes, backslashes, `?` and `#` change the path. A decoy committed at the literal path must not pass."""
        decoy = b"decoy bytes"
        both = " ".join("sha512-" + base64.b64encode(hashlib.sha512(b).digest()).decode() for b in (decoy, b"home bytes"))
        for target in ("~/.npm/pad.tgz", "~", "vendor/./pad.tgz", "vendor/%70ad.tgz", "vendor\\pad.tgz",
                       "vendor/pad.tgz#x", "vendor/pad.tgz?x", "C:/pad.tgz"):
            with self.subTest(target=target):
                literal = self.fx.root / "frontend" / target
                if literal.parent.is_relative_to(self.fx.root):
                    literal.parent.mkdir(parents=True, exist_ok=True)
                    if not literal.is_dir():
                        literal.write_bytes(decoy)
                self.fx.lock_json["packages"]["node_modules/pad"] = {"version": "1.0.0", "resolved": f"file:{target}",
                                                                    "integrity": both}
                self.fx.write_node_lock()
                (finding,) = self.assertOnly("NODE-INTEGRITY")
                self.assertIn("is not a path inside the repository", finding.message)

    def test_sec_sc_10_a_vendored_tarball_needs_exactly_its_own_hash(self) -> None:
        """npm accepts a file matching any listed hash, so a second token would let another file through."""
        own = "sha512-" + base64.b64encode(hashlib.sha512(b"tarball bytes").digest()).decode()
        other = "sha512-" + base64.b64encode(hashlib.sha512(b"other bytes").digest()).decode()
        for integrity in (f"{own} {other}", f"{other} {own}", f"{own}?opt"):
            with self.subTest(integrity=integrity):
                self._vendor("file:vendor/pad-1.0.0.tgz", integrity=integrity)
                (finding,) = self.assertOnly("NODE-INTEGRITY")
                self.assertIn("is not exactly the sha512 of the file", finding.message)

    def test_sec_sc_10_a_package_json_with_dependencies_and_no_lock_fails(self) -> None:
        self.fx.write("tools/x/package.json", json.dumps({"dependencies": {"left-pad": "1.3.0"}}))
        self.assertOnly("NODE-INTEGRITY")

    def test_sec_sc_10_a_version_1_lock_fails(self) -> None:
        self.fx.lock_json["lockfileVersion"] = 1
        self.fx.write_node_lock()
        self.assertOnly("NODE-INTEGRITY")


class TestSecSc07InstallsOutsideImagesAndCi(SupplyChainCase):
    """C6.44: every file that runs or tells someone to run an install is held to the lock rules, not only images and
    CI: the shell scripts and Makefiles a person or a job runs, the translated contributor guides, package.json
    scripts; an environment resolved at run time (`uv run --with`, uvx, pipx) and an npx that would fetch a package no
    lock holds fail too. What may stay unlocked is listed with its reason in unlocked-installs.json."""

    def _with_bin(self) -> None:
        self.fx.lock_json["packages"]["node_modules/left-pad"]["bin"] = {"left-pad": "cli.js"}
        self.fx.write_node_lock()

    def test_c644_1_a_shell_script_and_a_makefile_install_fail_but_a_message_naming_one_does_not(self) -> None:
        self.fx.write("scripts/setup.sh", 'echo "run pip install x yourself"\npip install requests\n')
        self.fx.write("scripts/Makefile", 'setup:\n\t@echo "uv pip install is gone"\n\tcd py && uv pip install -e .\n')
        findings = self.assertOnly("PY-INSTALL")
        self.assertEqual(sorted(f.path for f in findings), ["scripts/Makefile:3", "scripts/setup.sh:2"])

    def test_c644_2_a_translated_contributor_guide_is_checked_in_its_code_blocks(self) -> None:
        self.fx.write("docs/i18n/de/CONTRIBUTING.md", "Führe `npm install` aus.\n\n```bash\nnpm install\n```\n")
        self.assertEqual([f.path for f in self.assertOnly("NODE-CI")], ["docs/i18n/de/CONTRIBUTING.md:4"])

    def test_c644_3_an_environment_resolved_at_run_time_fails(self) -> None:
        for command in ("uv run --no-project --with requests python x.py", "uvx ruff check", "pipx install poetry",
                        "uv tool install omnigent"):
            with self.subTest(command=command):
                self.fx.write(".github/workflows/adhoc.yml", f"jobs:\n  a:\n    steps:\n      - run: {command}\n")
                self.assertOnly("PY-INSTALL")
        self.fx.write(".github/workflows/adhoc.yml", "jobs:\n  a:\n    steps:\n      - run: uv run --locked pytest\n")
        self.assertEqual(self.fx.rules(), [])

    def test_c644_4_npx_runs_only_what_a_committed_lock_installs(self) -> None:
        self._with_bin()
        self.fx.write(".github/workflows/npx.yml", "jobs:\n  a:\n    steps:\n      - run: npx --no-install left-pad x\n")
        self.assertEqual(self.fx.rules(), [])
        for command in ("npx open-cli report.html", "npx left-pad@1.3.0 x", "npx -p evil-pkg left-pad", "npx --package=evil left-pad"):
            with self.subTest(command=command):
                self.fx.write(".github/workflows/npx.yml", f"jobs:\n  a:\n    steps:\n      - run: {command}\n")
                self.assertOnly("NODE-NPX")

    def test_c644_5_a_package_json_script_is_checked_too(self) -> None:
        self._with_bin()
        self.fx.write("frontend/package.json", json.dumps(
            {"name": "f", "dependencies": {"left-pad": "1.3.0"}, "scripts": {"a": "npx left-pad", "b": "npx open-cli r.html"}}))
        (finding,) = self.assertOnly("NODE-NPX")
        self.assertIn("open-cli", finding.message)

    def test_c644_6_a_listed_unlocked_install_passes_and_an_entry_that_excuses_nothing_fails(self) -> None:
        self.fx.write("tests/sdk.sh", "pip install -e . pytest\n")
        entry = {"rule": "PY-INSTALL", "file": "tests/sdk.sh", "match": "pip install -e .", "reason": "an SDK as users install it"}
        self.fx.write("contracts/supply-chain/unlocked-installs.json", json.dumps({"installs": [entry]}))
        self.assertEqual(self.fx.rules(), [])
        self.fx.write("contracts/supply-chain/unlocked-installs.json", json.dumps({"installs": [{**entry, "rule": "NODE-NPX"}]}))
        self.assertEqual(sorted(self.fx.rules()), ["NODE-NPX", "PY-INSTALL"], "an entry excuses its own rule only")
        self.fx.write("tests/sdk.sh", "echo done\n")
        self.fx.write("contracts/supply-chain/unlocked-installs.json", json.dumps({"installs": [entry]}))
        (stale,) = self.assertOnly("PY-INSTALL")
        self.assertIn("drop the entry", stale.message)

    def test_c644_6b_a_build_lists_its_own_unlocked_installs_beside_the_shared_list(self) -> None:
        """A build that adds files of its own lists their excuses under contracts/supply-chain/extra/, merged into
        the shared list and held to the same rules."""
        self.fx.write("tests/sdk.sh", "pip install -e . pytest\n")
        entry = {"rule": "PY-INSTALL", "file": "tests/sdk.sh", "match": "pip install -e .", "reason": "an SDK as users install it"}
        self.fx.write("contracts/supply-chain/extra/unlocked-installs.json", json.dumps({"installs": [entry]}))
        self.assertEqual(self.fx.rules(), [])
        self.fx.write("tests/sdk.sh", "echo done\n")
        (stale,) = self.assertOnly("PY-INSTALL")
        self.assertIn("drop the entry", stale.message)


class TestSdkJobLocks(SupplyChainCase):
    """C644-FU-3: the SDK job's provisioning helpers and the Python SDK suite's test tools install from hashed locks
    (integration-tests/sdk_tests/requirements/); only the SDK under test installs from its own pyproject."""

    SDK_DIR = REAL_REPO / "integration-tests" / "sdk_tests"
    WORKFLOW = REAL_REPO / ".github" / "workflows" / "sdk-tests.yml"

    def _lines(self, path: Path) -> list[str]:
        return [line for _, line in sc._command_lines(path)]

    def test_c644_7_the_sdk_jobs_helpers_run_from_the_hashed_helpers_lock(self) -> None:
        lines = self._lines(self.WORKFLOW)
        self.assertFalse([ln for ln in lines if sc._PY_ADHOC.search(ln)], "no environment resolved at run time")
        (install,) = [ln for ln in lines if sc._PIP_INSTALL.search(ln)]
        for flag in ("--require-hashes", "--no-deps", "-r integration-tests/sdk_tests/requirements/helpers.lock"):
            self.assertIn(flag, install)
        helpers = [ln for ln in lines if re.search(r"(provision_sdk_test_env|with_oauth_app)\.py", ln)]
        self.assertEqual(len(helpers), 3, helpers)
        for ln in helpers:
            self.assertTrue(ln.startswith('"$SDK_HELPERS_PYTHON" '), ln)

    def test_c644_8_the_python_suites_test_tools_come_from_the_hashed_tests_lock_before_the_sdk(self) -> None:
        script = self.SDK_DIR / "run_sdk_tests.sh"
        installs = [ln for ln in self._lines(script) if sc._PIP_INSTALL.search(ln)]
        self.assertEqual(len(installs), 2, installs)
        tools, sdk = installs
        for flag in ("--require-hashes", "--no-deps", '-r ""'):
            self.assertIn(flag, tools)
        self.assertIn('TEST_TOOLS_LOCK=$(realpath "$(dirname "$0")/requirements/tests.lock")', script.read_text())
        self.assertNotIn("--require-hashes", sdk)
        self.assertNotRegex(sdk, r"\bpytest", "a test tool installed beside the SDK would resolve unlocked")
        locked = {sc.canonicalize_name(line) for line in sc._in_lines(self.SDK_DIR / "requirements" / "tests.in")}
        # The suite's own command line needs these plugins: -n (xdist), --timeout, and the async tests.
        self.assertLessEqual({"pytest", "pytest-xdist", "pytest-timeout", "pytest-asyncio"}, locked)

    def test_c644_9_the_excuse_list_holds_only_the_sdk_under_test(self) -> None:
        text = (REAL_REPO / sc.UNLOCKED_INSTALLS).read_text(encoding="utf-8")
        self.assertNotIn("C644-FU-3", text)
        entries = [e for e in json.loads(text)["installs"] if e["file"].startswith(("integration-tests/sdk_tests/", ".github/workflows/sdk-tests"))]
        self.assertEqual([e["file"] for e in entries], ["integration-tests/sdk_tests/run_sdk_tests.sh"])
        matched = [ln for ln in self._lines(self.SDK_DIR / "run_sdk_tests.sh") if entries[0]["match"] in ln]
        self.assertEqual(len(matched), 1, matched)
        self.assertIn("--editable", matched[0])

    def test_c644_10_the_helpers_lock_holds_every_package_the_helpers_import(self) -> None:
        import ast

        locks, _ = sc.load_locks(REAL_REPO)
        self.assertLessEqual({"sdk-helpers", "sdk-tests"}, set(locks))
        helper_dir = REAL_REPO / "integration-tests" / "helper"
        todo = [self.SDK_DIR / "provision_sdk_test_env.py", self.SDK_DIR / "with_oauth_app.py"]
        seen: set[Path] = set()
        third_party: set[str] = set()
        while todo:
            path = todo.pop()
            if path in seen:
                continue
            seen.add(path)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
                for name in names:
                    parts = name.split(".")
                    top = parts[0]
                    # pipeshub_client falls back to `from helper.local_auth import ...`.
                    local = helper_dir / f"{parts[1] if top == 'helper' and len(parts) > 1 else top}.py"
                    if local.exists():
                        todo.append(local)
                    elif top not in sys.stdlib_module_names and top not in ("helper", "__future__"):
                        third_party.add(sc.canonicalize_name(top))
        self.assertIn("pydantic", third_party, "the walk reaches local_auth")
        locked = {e.key for e in locks["sdk-helpers"].entries}
        self.assertLessEqual(third_party, locked)
        self.assertLessEqual(third_party, {sc.canonicalize_name(n) for n in sc._in_lines(self.SDK_DIR / "requirements" / "helpers.in")})

    def test_c644_11_a_standalone_lock_is_held_to_the_lock_rules(self) -> None:
        spec = sc.STANDALONE_LOCKS["sdk-tests"]
        self.fx.write(str(spec.source), "pytest\n")
        (missing,) = self.assertOnly("PY-LOCK-MISSING")
        self.assertEqual(missing.path, str(spec.lock))
        header = sc._header("sdk-tests", sc.standalone_fingerprint(self.fx.root, "sdk-tests"), "2026-11-05T00:00:00Z")
        self.fx.write(str(spec.lock), header + _pin("pytest", "9.2.0", H1))
        self.assertEqual(self.fx.rules(), [])
        self.assertIn(f"-r {spec.lock}", header)
        self.fx.write(str(spec.lock), header + "pytest==9.2.0\n")
        self.assertEqual(sorted(set(self.fx.rules())), ["PY-LOCK-AGREE", "PY-LOCK-HASH"], "unhashed, so not locked")
        self.fx.write(str(spec.source), "pytest\npytest-xdist\n")
        self.fx.write(str(spec.lock), header + _pin("pytest", "9.2.0", H1))
        self.assertEqual(sorted(set(self.fx.rules())), ["PY-LOCK-AGREE", "PY-LOCK-STALE"])

    def test_c644_12_lock_check_resolves_each_set_against_its_own_recorded_cutoff(self) -> None:
        # The SDK locks were written alone (--set) a week after the backend's; one shared cutoff would re-resolve
        # them against the backend's date and report them stale every week.
        spec = sc.STANDALONE_LOCKS["sdk-tests"]
        self.fx.write(str(spec.source), "pytest\n")
        self.fx.write(str(spec.lock), sc._header("sdk-tests", sc.standalone_fingerprint(self.fx.root, "sdk-tests"),
                                                  "2026-11-05T00:00:00Z") + _pin("pytest", "9.2.0", H1))
        seen: dict[str, str] = {}

        def backend(repo: Path, out_dir: Path, uv: str, cutoff: str, *_: object) -> None:
            seen["backend"] = cutoff
            for n in (*sc.HASHED_LOCKS, "vcs"):
                (out_dir / "requirements" / f"{n}.lock").write_text((repo / sc.REQ_DIR / f"{n}.lock").read_text())

        def standalone(repo: Path, name: str, out_dir: Path, uv: str, cutoff: str, *_: object) -> None:
            seen[name] = cutoff
            (out_dir / "requirements.lock").write_text((repo / sc.STANDALONE_LOCKS[name].lock).read_text())

        with mock.patch.dict(os.environ, {"UV": "/bin/true"}), mock.patch.object(sc, "generate_locks", backend), \
                mock.patch.object(sc, "generate_standalone_lock", standalone):
            self.assertEqual(sc.cmd_lock(self.fx.root, True, []), 0)
        self.assertEqual(seen, {"backend": "2026-10-26T00:00:00Z", "sdk-tests": "2026-11-05T00:00:00Z"})


class TestSecSc08NoInstallScripts(SupplyChainCase):
    """SEC-SC-08 (C6.44): an image build runs no npm install script but those install-scripts.json names."""

    def scripted(self, name: str = "esbuild") -> None:
        self.fx.lock_json["packages"][f"node_modules/{name}"] = {
            "version": "1.0.0", "resolved": f"https://registry.npmjs.org/{name}/-/{name}-1.0.0.tgz",
            "integrity": "sha512-" + "B" * 86 + "==", "hasInstallScript": True}
        self.fx.write_node_lock()

    def contract(self, packages: dict, scripted_installs: list | None = None) -> None:
        self.fx.write("contracts/supply-chain/install-scripts.json", json.dumps(
            {"packages": packages, "scriptedInstalls": scripted_installs or []}))

    def test_sec_sc_08_a_dockerfile_npm_ci_with_scripts_fails(self) -> None:
        self.fx.write("web/Dockerfile", f"FROM {BASE}\nRUN npm ci --omit=dev && npm cache clean --force\n")
        findings = self.assertOnly("NODE-SCRIPTS")
        self.assertIn("web/Dockerfile:2", findings[0].path)

    def test_sec_sc_08_a_listed_scripted_install_passes_and_only_that_command(self) -> None:
        self.fx.write("web/Dockerfile", f"FROM {BASE}\nRUN npm ci\nRUN npm ci --omit=dev\n")
        self.contract({}, [{"file": "web/Dockerfile", "command": "npm ci", "why": "test"}])
        findings = self.assertOnly("NODE-SCRIPTS")
        self.assertEqual([f.path for f in findings], ["web/Dockerfile:3"])

    def test_sec_sc_08_a_new_package_with_an_install_script_must_be_classified(self) -> None:
        self.scripted()
        self.assertIn("esbuild has an install script", self.assertOnly("NODE-SCRIPTS")[0].message)
        self.contract({"frontend/package-lock.json": {"esbuild": {"run": "skip", "why": "prebuilt"}}})
        self.assertEqual(sc.run_checks(self.fx.root), [])

    def test_sec_sc_08_a_classified_package_whose_script_is_gone_leaves_the_list(self) -> None:
        self.contract({"frontend/package-lock.json": {"esbuild": {"run": "skip", "why": "prebuilt"}}})
        self.assertIn("drop it", self.assertOnly("NODE-SCRIPTS")[0].message)

    def test_sec_sc_08_a_ci_job_npm_ci_with_scripts_fails_in_either_run_form(self) -> None:
        # C644-FU-2: a CI job's secrets and token are in the environment a lifecycle script inherits.
        self.fx.write(".github/workflows/w.yml", textwrap.dedent("""\
            jobs:
              j:
                steps:
                  - run: npm ci
                  - name: both
                    run: |
                      (cd backend && npm ci --no-audit)
                      (cd frontend && npm ci --ignore-scripts)
                  # npm ci in a comment is no step
            """))
        self.assertEqual([f.path for f in self.assertOnly("NODE-SCRIPTS")],
                         [".github/workflows/w.yml:4", ".github/workflows/w.yml:7"])
        self.fx.write(".github/workflows/w.yml", "jobs:\n  j:\n    steps:\n      - run: npm ci --ignore-scripts\n")
        self.assertEqual(sc.run_checks(self.fx.root), [])

    def test_sec_sc_08_a_ci_job_may_rebuild_only_a_rebuild_entry(self) -> None:
        self.scripted()
        self.contract({"frontend/package-lock.json": {"esbuild": {"run": "skip", "why": "prebuilt"}}})
        self.fx.write(".github/workflows/w.yml",
                      "jobs:\n  j:\n    steps:\n      - run: npm ci --ignore-scripts && npm rebuild esbuild\n")
        self.assertIn("npm rebuild esbuild", self.assertOnly("NODE-SCRIPTS")[0].message)

    def test_sec_sc_08_every_npm_ci_step_of_this_repository_skips_scripts_by_its_parsed_yaml(self) -> None:
        # The rule reads lines; this reads each workflow as YAML, so a step the line reader misses still fails here.
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        steps = 0
        for path in sorted((REAL_REPO / ".github" / "workflows").glob("*.y*ml")):
            jobs = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("jobs") or {}
            for name, job in jobs.items():
                for step in job.get("steps") or []:
                    for cmd in re.split(r"&&|\|\||;|\n", str(step.get("run") or "")):
                        if re.search(r"\bnpm\s+ci\b", cmd) and not cmd.strip().startswith("#"):
                            steps += 1
                            self.assertIn("--ignore-scripts", cmd, f"{path.name} {name}: {cmd.strip()}")
        self.assertGreater(steps, 0)

    def test_sec_sc_08_only_a_rebuild_entry_may_run_its_script(self) -> None:
        self.scripted()
        self.fx.write("web/Dockerfile", f"FROM {BASE}\nRUN npm ci --ignore-scripts && npm rebuild esbuild\n")
        self.contract({"frontend/package-lock.json": {"esbuild": {"run": "skip", "why": "prebuilt"}}})
        self.assertIn("npm rebuild esbuild", self.assertOnly("NODE-SCRIPTS")[0].message)
        self.contract({"frontend/package-lock.json": {"esbuild": {"run": "rebuild", "why": "links its binary"}}})
        self.assertEqual(sc.run_checks(self.fx.root), [])


class TestSecSc11ImagePins(SupplyChainCase):
    """SEC-SC-11: a base or service image is pinned by digest, or listed in the report for review."""

    def test_sec_sc_11_a_new_tag_only_base_image_fails(self) -> None:
        self.fx.write("svc/Dockerfile", "FROM debian:bookworm-slim\n")
        findings = self.assertOnly("IMAGE-PIN")
        self.assertIn("debian:bookworm-slim is pinned by tag only", findings[0].message)

    def test_sec_sc_11_a_compose_image_through_a_default_is_resolved(self) -> None:
        self.fx.write("deploy/docker-compose.yml", "services:\n  db:\n    image: mongo:${MONGO_TAG:-8.0.17}\n"
                      "  app:\n    image: ${APP_IMAGE:?}\n")
        findings = self.assertOnly("IMAGE-PIN")
        self.assertEqual([f.message.split(" ")[0] for f in findings], ["mongo:8.0.17"])

    def test_sec_sc_11_a_listed_image_that_is_now_pinned_must_leave_the_list(self) -> None:
        self.fx.write("Dockerfile", "FROM pipeshubai/pipeshub-ai-base:runtime@sha256:" + "d" * 64 + "\nRUN npm ci --ignore-scripts\n")
        findings = self.assertOnly("IMAGE-PIN")
        self.assertIn("drop the entry", findings[0].message)

    def test_sec_sc_11_only_a_first_party_image_may_stay_listed_by_tag(self) -> None:
        """C6.44: every third-party image is pinned by digest; only an image PipesHub
        builds itself waits, for the release pipeline's signed digest."""
        self.fx.write("svc/Dockerfile", "FROM debian:bookworm-slim\n")
        pins = json.loads((self.fx.root / "contracts/supply-chain/image-pins.json").read_text(encoding="utf-8"))
        pins["tagOnly"].append({"ref": "debian:bookworm-slim", "category": "build-base", "files": ["svc/Dockerfile"]})
        self.fx.write("contracts/supply-chain/image-pins.json", json.dumps(pins))
        findings = self.assertOnly("IMAGE-PIN")
        self.assertIn("debian:bookworm-slim is third-party", findings[0].message)

    def test_sec_sc_11_a_user_run_store_stays_by_tag_only_in_the_files_install_sh_runs(self) -> None:
        self.fx.write("deploy/docker-compose.yml", "services:\n  db:\n    image: mongo:8.0.17\n")
        path = self.fx.root / "contracts/supply-chain/image-pins.json"
        pins = json.loads(path.read_text(encoding="utf-8"))
        pins["userRunFiles"] = ["deploy/docker-compose.yml"]
        pins["tagOnly"].append({"ref": "mongo:8.0.17", "category": "user-run-service", "files": ["deploy/docker-compose.yml"]})
        path.write_text(json.dumps(pins), encoding="utf-8")
        self.assertEqual(sc.run_checks(self.fx.root), [])
        self.fx.write("tests/docker-compose.yml", "services:\n  db:\n    image: mongo:8.0.17\n")
        pins["tagOnly"][-1]["files"].append("tests/docker-compose.yml")
        path.write_text(json.dumps(pins), encoding="utf-8")
        self.assertIn("outside the files install.sh runs", self.assertOnly("IMAGE-PIN")[0].message)

    def test_sec_sc_11_a_build_lists_its_own_tag_only_images_beside_the_shared_list(self) -> None:
        self.fx.write("svc/Dockerfile", "FROM debian:bookworm-slim\n")
        extra = {"tagOnly": [{"ref": "debian:bookworm-slim", "category": "build-base", "files": ["svc/Dockerfile"]}]}
        self.fx.write("contracts/supply-chain/extra/image-pins.json", json.dumps(extra))
        self.assertIn("debian:bookworm-slim is third-party", self.assertOnly("IMAGE-PIN")[0].message,
                      "an extra entry is held to the shared rules")

    def test_sec_sc_11_stage_names_and_scratch_are_not_images(self) -> None:
        refs = sc.image_refs(self.fx.root)
        self.assertNotIn("build", refs)
        self.assertIn("pipeshubai/pipeshub-ai-base:runtime", refs)


class TestSecD11FetchedCode(SupplyChainCase):
    """SEC-D-11: code fetched while building an image or running a CI job is pinned and checked before it runs."""

    def test_sec_d_11_the_fixture_has_no_fetched_code(self) -> None:
        self.assertEqual(sc.check_fetched_code(self.fx.root), [])

    def test_sec_d_11_a_remote_installer_piped_to_a_shell_fails(self) -> None:
        for line in ("RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
                     "RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable",
                     "RUN wget -qO- https://x.example/i.sh | sudo -E bash",
                     "RUN curl -fsSL https://x.example/i | sudo bash -s --",
                     "RUN curl -s https://x.example/i |bash",
                     "RUN curl -s https://x.example/i | /bin/sh",
                     "RUN curl -s https://x.example/i | env FOO=1 bash",
                     "RUN curl -s https://x.example/i.py | python3 -",
                     "RUN curl -s https://x.example/i.pl | perl",
                     "RUN curl -s https://x.example/i | zsh",
                     "RUN apt-get update && \\\n    curl -s https://x.example/i | bash"):
            with self.subTest(line=line):
                self.fx.write("svc/Dockerfile", "FROM scratch\n" + line + "\n")
                self.assertIn("PIPE-SHELL", self.fx.rules())

    def test_sec_d_11_the_pipe_scan_is_linear_in_env_assignments(self) -> None:
        # Each `env !==!` split two ways before: 30 of them took minutes (CodeQL py/redos).
        line = "curl x | " + "env !==! " * 5000 + "!"
        started = time.perf_counter()
        self.assertIsNone(sc._PIPE_SHELL.search(line))
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertIsNotNone(sc._PIPE_SHELL.search("curl -s https://x.example/i | env B=x=y bash"))

    def test_sec_d_11_a_pipe_in_a_workflow_or_a_composite_action_fails(self) -> None:
        self.fx.write(".github/workflows/x.yml", "jobs:\n  a:\n    steps:\n      - run: curl -fsSL https://get.x.example | bash\n")
        self.assertOnly("PIPE-SHELL")
        self.fx.write(".github/workflows/x.yml", "jobs: {}\n")
        self.fx.write(".github/actions/a/action.yml", "runs:\n  steps:\n    - run: wget -O- https://x.example | sh\n      shell: bash\n")
        self.assertOnly("PIPE-SHELL")

    def test_sec_d_11_a_response_read_as_data_is_not_a_pipe_to_a_shell(self) -> None:
        self.fx.write("scripts/probe.sh", "v=\"$(curl -fsSL https://api.x.example | python3 -c 'import json,sys; print(1)')\"\n"
                      "echo 'curl https://x | bash'  # a message, not a command\n")
        self.assertEqual(sc.check_fetched_code(self.fx.root), [])

    def test_sec_d_11_a_downloaded_archive_or_binary_must_be_checked(self) -> None:
        for line in ('curl -fsSL -o /tmp/tool.zip "https://x.example/tool.zip"',
                     "curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.1/kind-linux-amd64 && sudo install kind /usr/local/bin/kind",
                     "wget -O /usr/local/bin/tool https://x.example/tool",
                     "curl -fsSL https://x.example/t.tar.gz | tar -xz",
                     "curl -fsSL https://x.example/t.tgz > t.tgz"):
            with self.subTest(line=line):
                self.fx.write(".github/workflows/x.yml", f"jobs:\n  a:\n    steps:\n      - run: |\n          {line}\n")
                self.assertOnly("DOWNLOAD-HASH")

    def test_sec_d_11_a_download_checked_against_a_pinned_sum_passes(self) -> None:
        for check in ('echo "${SUM}  /tmp/tool.zip" | sha256sum -c -', "shasum -a 256 -c tool.sha256",
                      "cosign verify-blob --bundle t.sigstore /tmp/tool.zip"):
            with self.subTest(check=check):
                self.fx.write(".github/workflows/x.yml", "jobs:\n  a:\n    steps:\n      - run: |\n"
                              '          curl -fsSL -o /tmp/tool.zip "https://x.example/tool.zip"\n'
                              f"          {check}\n")
                self.assertEqual(sc.check_fetched_code(self.fx.root), [])

    def test_sec_d_11_an_upload_is_not_a_download(self) -> None:
        self.fx.write(".github/workflows/x.yml", "jobs:\n  a:\n    steps:\n      - run: |\n"
                      '          curl -s -X POST "$URL" -F "file=@reports.zip"\n')
        self.assertEqual(sc.check_fetched_code(self.fx.root), [])


class TestR41SupplyFu10OperatorScripts(SupplyChainCase):
    """R41SUPPLY-FU-10: a shell script an operator runs installs only downloads checked against a checksum it
    carries; a checksum fetched from the download's own server is not a pin."""

    VERIFY_FN = ('verify_pinned() {\n  printf \'%s  %s\\n\' "$(pinned "$2")" "$1" | sha256sum -c --status - || exit 1\n}\n')

    def script(self, body: str) -> list[sc.Finding]:
        self.fx.write("deployment/helm/aws/install-deps.sh", "#!/usr/bin/env bash\n" + body)
        return sc.check_fetched_code(self.fx.root)

    def test_r41supply_fu_10_an_unchecked_binary_or_archive_fails(self) -> None:
        for body in ('curl -fsSL -o "$TMP/kubectl" "$url"\ninstall -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"\n',
                     'curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64\nchmod +x ./kind\n',
                     'curl -fsSL -o "$TMP/awscliv2.zip" "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"\n',
                     'wget -qO "$T/h.tgz" "$U"\ntar -xzf "$T/h.tgz" -C "$T"\n'):
            with self.subTest(body=body):
                self.assertEqual([f.rule for f in self.script(body)], ["DOWNLOAD-HASH"])

    def test_r41supply_fu_10_the_base_install_deps_fails(self) -> None:
        # The base compared against a sum read from the same server (and the Linux AWS CLI against nothing).
        body = ('verify_sha256() { [[ "$(sha256_of "$1")" == "$2" ]] || die mismatch; }\n'
                'curl -fsSL -o "$TMP/kubectl" "$url"\n'
                'verify_sha256 "$TMP/kubectl" "$(curl -fsSL "${url}.sha256" | awk \'{print $1}\')"\n'
                'install -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"\n')
        self.assertEqual([f.rule for f in self.script(body)], ["DOWNLOAD-HASH"])

    def test_r41supply_fu_10_a_checksum_fetched_beside_the_download_is_not_a_pin(self) -> None:
        for body in (self.VERIFY_FN + 'curl -fsSL -o "$T/helm.tgz" "$U"\n'
                     'verify_pinned "$T/helm.tgz" "$(curl -fsSL "$U.sha256sum")"\ntar -xzf "$T/helm.tgz"\n',
                     'curl -fsSL -o "$d/g.tar.bz2" "$u/g.tar.bz2" \\\n  || ! curl -fsSL -o "$d/g.tar.bz2.sha512" "$u/g.tar.bz2.sha512"\n'
                     '(cd "$d" && sha512sum -c g.tar.bz2.sha512)\ntar -xjf "$d/g.tar.bz2" -C "$d"\n'):
            with self.subTest(body=body):
                findings = self.script(body)
                self.assertEqual([f.rule for f in findings], ["DOWNLOAD-HASH"])
                self.assertIn("proves the transfer", findings[0].message)

    def test_r41supply_fu_10_a_download_checked_against_a_carried_pin_passes(self) -> None:
        for body in (self.VERIFY_FN + 'curl -fsSL -o "$TMP/kubectl" "$url"\nverify_pinned "$TMP/kubectl" kubectl-linux\n'
                     'install -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"\n',
                     'curl -fsSLo ./kind https://x.example/kind\necho "${KIND_SHA256}  ./kind" | sha256sum -c -\nchmod +x ./kind\n'):
            with self.subTest(body=body):
                self.assertEqual(self.script(body), [])

    def test_r41supply_fu_10_data_a_script_fetches_is_not_code(self) -> None:
        self.assertEqual(self.script('curl -fsSL -o "${STATE}/policy.json" "$POLICY_URL"\n'
                                     'aws iam create-policy --policy-document "file://${STATE}/policy.json"\n'
                                     'curl -sf -o /dev/null https://1.1.1.1/ && sh -c true\n'), [])

    def test_r41supply_fu_10_the_repository_scripts_pass(self) -> None:
        for rel in ("deployment/helm/aws/install-deps.sh", "deployment/helm/local-setup-kind-cluster.sh",
                    "deployment/docker-compose/install.sh"):
            with self.subTest(script=rel):
                self.assertEqual(sc.check_operator_downloads(REAL_REPO / rel, rel), [])


class TestSecD11ActionPins(SupplyChainCase):
    """SEC-D-11: every non-local action is a 40-hex commit; a tag can be moved onto other code."""

    def test_sec_d_11_a_tag_pinned_action_fails(self) -> None:
        for ref in ("actions/checkout@v4", "actions/setup-python@v5.1.0", "sigstore/cosign-installer@main",
                    "actions/checkout@11d5960a", "'actions/checkout@v4'", "actions/checkout@" + "A" * 40,
                    "docker://alpine:3.20", "owner/repo/path@v1"):
            with self.subTest(ref=ref):
                self.fx.write(".github/workflows/x.yml", f"jobs:\n  a:\n    steps:\n      - uses: {ref}\n")
                self.assertOnly("ACTION-PIN")

    def test_sec_d_11_a_composite_actions_steps_are_checked_too(self) -> None:
        self.fx.write(".github/actions/a/action.yml", "runs:\n  using: composite\n  steps:\n    - uses: actions/cache@v4\n")
        self.assertOnly("ACTION-PIN")

    def test_sec_d_11_a_commit_a_digest_or_a_local_action_passes(self) -> None:
        self.fx.write(".github/workflows/x.yml", "jobs:\n  a:\n    steps:\n"
                      "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n"
                      "      - uses: ./.github/actions/pr-changes\n"
                      "      - uses: docker://alpine:3.20@sha256:" + "a" * 64 + "\n"
                      "  b:\n    uses: ./.github/workflows/y.yml\n")
        self.assertEqual(sc.check_actions(self.fx.root), [])


class TestSecD11ImageTagsAndHelm(SupplyChainCase):
    """SEC-D-11: a user-run store keeps an exact tag; a chart's third-party images carry a digest."""

    def user_run(self, ref: str) -> None:
        self.fx.write("deploy/docker-compose.yml", f"services:\n  db:\n    image: {ref}\n")
        path = self.fx.root / "contracts/supply-chain/image-pins.json"
        pins = json.loads(path.read_text(encoding="utf-8"))
        pins["userRunFiles"] = ["deploy/docker-compose.yml"]
        pins["tagOnly"].append({"ref": ref, "category": "user-run-service", "files": ["deploy/docker-compose.yml"]})
        path.write_text(json.dumps(pins), encoding="utf-8")

    def test_sec_d_11_a_floating_user_run_tag_fails(self) -> None:
        for ref in ("qdrant/qdrant:v1.15", "redis:7.4-bookworm", "redis:7", "mongo:latest", "nginx:stable-alpine",
                    "redis:7.4.x-bookworm"):
            with self.subTest(ref=ref):
                self.tearDown()
                self.setUp()
                self.user_run(ref)
                self.assertIn("is a floating tag", self.assertOnly("IMAGE-PIN")[0].message)

    def test_sec_d_11_an_exact_user_run_tag_passes(self) -> None:
        for ref in ("qdrant/qdrant:v1.15.5", "redis:7.4.12-bookworm", "nginx:1.27.5-alpine",
                    "searxng/searxng:2026.9.30-a9d990033", "quay.io/coreos/etcd:v3.5.17"):
            with self.subTest(ref=ref):
                self.tearDown()
                self.setUp()
                self.user_run(ref)
                self.assertEqual(sc.run_checks(self.fx.root), [])

    def test_sec_d_11_a_workflow_service_image_needs_a_digest(self) -> None:
        self.fx.write(".github/workflows/x.yml", "jobs:\n  a:\n    services:\n      redis:\n        image: redis:7.4-bookworm\n")
        self.assertIn("redis:7.4-bookworm is pinned by tag only", self.assertOnly("IMAGE-PIN")[0].message)

    def test_sec_d_11_an_image_a_build_copies_from_is_an_image(self) -> None:
        self.fx.write("svc/Dockerfile", "FROM scratch\nCOPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/\n")
        self.assertIn("node:22-slim is pinned by tag only", self.assertOnly("IMAGE-PIN")[0].message)
        self.fx.write("svc/Dockerfile", "FROM node:22-slim@sha256:" + "a" * 64 + " AS n\nFROM scratch\nCOPY --from=n /x /x\n")
        self.assertEqual(sc.run_checks(self.fx.root), [])

    def test_sec_d_11_a_chart_third_party_image_needs_a_digest(self) -> None:
        cases = {
            'dind:\n  image: "docker:27-dind"\n': "docker:27-dind",
            "qdrant:\n  image:\n    repository: qdrant/qdrant\n    tag: v1.15-unprivileged\n": "qdrant/qdrant:v1.15-unprivileged",
            'image:\n  registry: docker.io\n  repository: bitnami/redis\n  tag: latest\n  digest: ""\n': "bitnami/redis:latest",
            "image:\n  repository: mongo\n  tag: 8.0.17\n  digest: sha256:abc\n": "mongo:8.0.17",
        }
        for values, ref in cases.items():
            with self.subTest(ref=ref):
                self.fx.write("deployment/helm/chart/values.yaml", values)
                self.assertIn(f"{ref} is a third-party image", self.assertOnly("IMAGE-PIN")[0].message)

    def test_sec_d_11_a_pinned_chart_and_its_own_image_pass(self) -> None:
        self.fx.write("deployment/helm/chart/values.yaml",
                      'image:\n  repository: pipeshubai/pipeshub-ai\n  tag: ""\n  digest: ""\n'
                      'dind:\n  image: "docker:27.5.1-dind@sha256:' + "a" * 64 + '"\n'
                      "q:\n  image:\n    repository: qdrant/qdrant\n    tag: v1.15.5\n    digest: sha256:" + "b" * 64 + "\n"
                      'proxy:\n  image: ""\n')
        self.assertEqual(sc.check_helm_images(self.fx.root), [])

    def test_sec_d_04_a_chart_never_defaults_its_own_image_to_a_moving_tag(self) -> None:
        self.fx.write("deployment/helm/chart/values-cloud.yaml", 'image:\n  repository: pipeshubai/pipeshub-ai\n  tag: "latest"\n')
        self.assertIn("moves with every release", self.assertOnly("IMAGE-PIN")[0].message)
        self.fx.write("deployment/helm/chart/values-cloud.yaml", "")
        self.fx.write("deployment/helm/chart/values-local.yaml", 'image:\n  repository: pipeshubai/pipeshub-ai\n  tag: "latest"\n')
        self.assertEqual(sc.check_helm_images(self.fx.root), [])


class TestInstalledMatchesTheLock(SupplyChainCase):
    """`installed` (the e2e lane's preflight): a site-packages directory holds exactly what runtime.lock pins."""

    def site(self, **versions: str) -> Path:
        root = self.fx.root / "site"
        for name, version in versions.items():
            info = root / f"{name}-{version}.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
        return root

    def test_a_matching_directory_passes(self) -> None:
        self.assertEqual(sc.check_installed(self.fx.root, self.site(requests="2.34.2", litellm="1.106.2")), [])

    def test_an_older_or_missing_package_is_named(self) -> None:
        findings = sc.check_installed(self.fx.root, self.site(requests="2.31.0"))
        self.assertEqual(sorted(f.message for f in findings), [
            "litellm==1.106.2 is not installed",
            "requests is 2.31.0, backend/python/requirements/runtime.lock pins 2.34.2",
        ])

    def test_a_pin_for_another_platform_is_not_required(self) -> None:
        self.fx.locks["runtime"] += "pywin32==311 ; sys_platform == 'win32' \\\n    --hash=sha256:" + H1 + "\n"
        self.fx.write_locks()
        self.assertEqual(sc.check_installed(self.fx.root, self.site(requests="2.34.2", litellm="1.106.2")), [])


class TestThisRepository(unittest.TestCase):
    """The rules hold on the repository as committed."""

    def test_sec_sc_07_to_11_this_repository_passes_every_rule(self) -> None:
        findings = sc.run_checks(REAL_REPO)
        self.assertEqual(findings, [], "\n".join(map(str, findings)))

    def test_sec_sc_07_the_runtime_lock_keeps_the_existing_pins(self) -> None:
        locks, _ = sc.load_locks(REAL_REPO)
        runtime = {(e.key, e.version) for e in locks["runtime"].entries}
        # WP-106: main's opik 2.2.119 caps litellm below 1.106 and main moved torch to 2.13.0 (#4868, #4889).
        self.assertIn(("litellm", "1.105.7"), runtime)
        self.assertIn(("torch", "2.13.0+cpu"), runtime)
        # R15-SC-4: round 14's direct pin (WP-66's tokenizer) is the version the lock installs.
        self.assertIn(("tiktoken", "0.14.0"), runtime)
        self.assertFalse([k for k, _ in runtime if k.startswith("nvidia-")], "no CUDA libraries in the lock")


if __name__ == "__main__":
    unittest.main()

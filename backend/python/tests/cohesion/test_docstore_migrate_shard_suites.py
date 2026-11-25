"""The docstore-migrate workflow's rest shard pins a case count for every suite it runs.

The pins went stale twice in one change: a suite moved to the unit tests kept its pin (0 cases, expected 7) and five
new itest suites had none, so a suite that stopped running would have gone unnoticed. This checks the titles both
ways against the files the shard runs; the counts themselves are checked by the job.
"""

from __future__ import annotations

import json
import re
import shlex

import yaml

from tests.cohesion.support import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docstore-migrate.yml"
APPS = REPO_ROOT / "backend" / "nodejs" / "apps"
MIGRATE_DIR = APPS / "tests" / "scripts" / "docstore-migrate"
TITLE = re.compile(r"\b(?:suite|describe)\(\s*(?:`([^`]*)`|'([^']*)'|\"([^\"]*)\")", re.S)


def _job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["migrate"]


def _step(name_start: str) -> str:
    return next(s["run"] for s in _job()["steps"] if s.get("name", "").startswith(name_start))


def _rest_pins() -> list[str]:
    script = _step("No unexpected skips")
    rest = script[script.index("rest)"):]
    rest = rest[:rest.index(";;")]
    words = shlex.split(rest.replace("\\\n", " "))
    return [words[i + 1].rsplit("=", 1)[0] for i, w in enumerate(words) if w == "--expect-suite"]


def _rest_files() -> list:
    excluded = re.findall(r"-e (\S+\.itest\.ts)", _step("Migration tool suites"))
    assert excluded, "the rest shard names the files the other shards run"
    files = [f for f in sorted(MIGRATE_DIR.glob("*.itest.ts")) if f.name not in excluded]
    fence = json.loads((APPS / "package.json").read_text(encoding="utf-8"))["scripts"]["test:docstore-fence"]
    files += [APPS / p for p in re.findall(r"tests/\S+\.itest\.ts", fence)]
    return files


def _titles() -> set[str]:
    found = set()
    for path in _rest_files():
        for groups in TITLE.findall(path.read_text(encoding="utf-8")):
            found.add(re.split(r"\s*\(|\$\{", next(g for g in groups if g), maxsplit=1)[0].strip())
    return found


def test_every_pin_names_a_suite_the_shard_runs() -> None:
    titles = _titles()
    stale = [p for p in _rest_pins() if not any(t.startswith(p) for t in titles)]
    assert not stale, f"pinned suites no rest-shard file defines: {stale}"


def test_every_migrate_suite_the_shard_runs_has_a_pin() -> None:
    pins = _rest_pins()
    unpinned = sorted(t for t in _titles() if t.startswith("docstore-migrate:") and not any(t.startswith(p) for p in pins))
    assert not unpinned, f"rest-shard suites with no --expect-suite line: {unpinned}"

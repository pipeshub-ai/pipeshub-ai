"""A small fake monorepo for the engine's own tests: its own rules, a few
suites in each language, a rule overlay, and git history to diff against."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
import textwrap
from pathlib import Path

from qa import config, metrics

SUITES = """
[[suite]]
id = "py-unit"
family = "pytest"
edition = "oss"
cwd = "backend/python"
grain = "file"
tests = ["backend/python/tests/unit/**/test_*.py"]
mirror = [{ src = "backend/python/app/", test = "backend/python/tests/unit/", style = "py" }]
import_graph = "python"

[[suite]]
id = "node-unit"
family = "mocha"
edition = "oss"
cwd = "backend/nodejs/apps"
grain = "file"
tests = ["backend/nodejs/apps/tests/**/*.test.ts"]
mirror = [{ src = "backend/nodejs/apps/src/", test = "backend/nodejs/apps/tests/", style = "ts" }]
import_graph = "typescript"

[[suite]]
id = "e2e"
family = "playwright"
edition = "oss"
cwd = "frontend"
grain = "file"
stack = true
tests = ["frontend/tests/e2e/**/*.spec.ts"]

[[suite]]
id = "it-stack"
family = "it-pytest"
edition = "oss"
marker_rules = true
cwd = "integration-tests"
grain = "test"
stack = true
tests = ["integration-tests/**/test_*.py"]
exclude = ["integration-tests/unit/**"]
catalog = "integration-tests/catalog"

[suite.fixture_needs]
setup_test_llm_model = "env:TEST_OPENAI_API_KEY|TEST_AZURE_OPENAI_API_KEY+TEST_AZURE_OPENAI_ENDPOINT"

[compose_profiles]
kafka = ["requires_kafka"]
"""

ALWAYS = """
max_files = 300
test_files = ["**/test_*.py", "**/*.test.ts"]
matrix_entries = ["neo4j", "arangodb", "qdrant", "kafka"]

[[full_run]]
paths = ["backend/python/pyproject.toml"]
suites = ["py-unit"]

[[full_run]]
paths = ["scripts/*/qa/**"]
suites = "*"

[branch_overrides]
"oss-sync-*" = "full"

[profiles.fast]
planner = "none"
import_depth = 1
graph_db = "neo4j"
integration = false
extra_minutes = 0

[profiles.standard]
planner = "default"
import_depth = 2
graph_db = "neo4j"
integration = true
extra_minutes = 4

[profiles.conservative]
planner = "default"
import_depth = 4
graph_db = "both"
integration = true
extra_minutes = 60

[profiles.full]
planner = "none"
import_depth = 99
graph_db = "both"
integration = true
extra_minutes = 0
full = true

[[always]]
id = "e2e-smoke"
suite = "e2e"
grep = "@smoke"
when = ["frontend/**"]

[[review_risk]]
id = "migrations"
title = "Database or config migrations"
paths = ["backend/python/app/**/migrations/**"]

[[review_risk]]
id = "auth"
title = "Authentication and permission paths"
modules = ["auth"]

[default_seconds]
pytest = 3
it-pytest = 90
mocha = 3
playwright = 30
"""

IMPACT = """
[[rule]]
id = "graph-db"
module = "graph-db"
paths = ["backend/python/app/services/graph_db/**"]
it_all = true
graph_db = "both"
matrix = ["neo4j", "arangodb"]

[[rule]]
id = "connector-{name}"
names = ["jira"]
module = "connectors"
paths = ["backend/python/app/connectors/sources/{name}/**"]
markers = ["{name}"]
"""

MODULES = """
[[module]]
id = "graph-db"
title = "Graph database"
paths = ["backend/python/app/services/graph_db/**", "backend/python/tests/unit/services/graph_db/**"]

[[module]]
id = "retrieval"
title = "Retrieval"
paths = [
  "backend/python/app/services/retrieval.py",
  "backend/python/tests/unit/services/test_retrieval.py",
  "integration-tests/retrieval/**",
]

[[module]]
id = "connectors"
title = "Connectors"
paths = ["backend/python/app/connectors/**"]

[[module]]
id = "auth"
title = "Authentication"
paths = ["backend/nodejs/apps/src/modules/auth/**"]

[[module]]
id = "frontend"
title = "Frontend"
paths = ["frontend/app/**"]
"""

FLOWS = """
[[flow]]
id = "search"
title = "Search"
priority = "P0"
critical = true
modules = ["retrieval", "graph-db"]
triggers = ["backend/python/app/services/retrieval.py"]
tests = ["integration-tests/retrieval/**"]

[[flow]]
id = "login"
title = "Log in"
priority = "P1"
modules = ["auth", "frontend"]
triggers = ["backend/nodejs/apps/src/modules/auth/**"]
tests = ["frontend/tests/e2e/login.spec.ts"]
"""

PLUS_FLOWS = """
[[flow]]
id = "usage"
title = "Usage"
priority = "P0"
tests = ["integration-tests/plus/**"]

[[flow]]
id = "search"
triggers = ["backend/python/app/plus/**"]
"""

PLUS_SUITES = """
[[suite]]
id = "py-unit-plus"
family = "pytest"
edition = "plus"
cwd = "backend/python"
grain = "file"
tests = ["backend/python/plus/tests/unit/**/test_*.py"]
mirror = [{ src = "backend/python/app/plus/", test = "backend/python/plus/tests/unit/", style = "py" }]
import_graph = "python"

[[suite]]
id = "it-plus"
family = "it-pytest"
edition = "plus"
marker_rules = true
cwd = "integration-tests"
grain = "test"
stack = true
tests = ["integration-tests/plus/**/test_*.py"]
catalog = "integration-tests/plus/catalog"
"""

PLUS_HOOKS = """\
import os
import re

PLUS = re.compile(r"(^|/)plus(/|$)")


def edition(path):
    if PLUS.search(path):
        return "plus"
    if path == "backend/python/app/edition.py":
        return "switch"
    return None


def prepare_stack(work_dir):
    if os.environ.get("PLUS_FAIL"):
        raise RuntimeError("boom")
    (work_dir / "plus.json").write_text("{}", encoding="utf-8")
    return {"env": {"PLUS_STACK": "1"}, "compose_files": [str(work_dir / "plus.json")]}
"""

FILES = {
    "scripts/qa/prompts/planner.md": "# planner\n",
    "backend/python/app/__init__.py": "",
    "backend/python/app/edition.py": "# # OSS\n# FLAG = True\n\n# PLUS\nFLAG = False\n",
    "backend/python/app/services/__init__.py": "",
    "backend/python/app/services/graph_db/__init__.py": "",
    "backend/python/app/services/graph_db/provider.py": "def get_records():\n    return []\n",
    "backend/python/app/services/retrieval.py": "from app.services.graph_db.provider import get_records\n\n\ndef search():\n    return get_records()\n",
    "backend/python/app/services/untested.py": "def helper():\n    return 1\n",
    "backend/python/app/connectors/__init__.py": "",
    "backend/python/app/connectors/sources/__init__.py": "",
    "backend/python/app/connectors/sources/jira/__init__.py": "",
    "backend/python/app/connectors/sources/jira/sync.py": "def fetch():\n    return 1\n",
    "backend/python/app/plus/__init__.py": "",
    "backend/python/app/plus/usage.py": "LIMIT = 1\n",
    "backend/python/tests/unit/services/test_retrieval.py": "from app.services.retrieval import search\n\n\ndef test_search():\n    assert search() == []\n",
    "backend/python/tests/unit/services/graph_db/test_provider.py": "from app.services.graph_db import provider\n\n\ndef test_records():\n    assert provider.get_records() == []\n",
    "backend/python/tests/unit/test_patch_only.py": (
        "from unittest import mock\n\n\ndef test_patched():\n"
        "    with mock.patch('app.connectors.sources.jira.sync.fetch', return_value=2):\n        pass\n"
    ),
    "backend/python/plus/tests/unit/test_usage.py": "from app.plus.usage import LIMIT\n\n\ndef test_limit():\n    assert LIMIT == 1\n",
    "backend/nodejs/apps/src/modules/auth/auth.service.ts": "export const check = () => true;\n",
    "backend/nodejs/apps/src/modules/auth/auth.controller.ts": "import { check } from './auth.service';\nexport const handle = () => check();\n",
    "backend/nodejs/apps/tests/modules/auth/auth.controller.test.ts": (
        "import { handle } from '../../../src/modules/auth/auth.controller';\n"
        "describe('auth controller', () => { it('handles', () => handle()); });\n"
    ),
    "backend/nodejs/apps/tests/modules/health.test.ts": "describe('health', () => { it('is up', () => true); });\n",
    "frontend/tests/e2e/login.spec.ts": "test('renders the login form @smoke', async () => {});\ntest('rejects a bad password', async () => {});\n",
    "frontend/app/login/page.tsx": "export default function Page() { return null; }\n",
    "integration-tests/connectors/jira/conftest.py": (
        "def jira():\n    source_unavailable('no creds', secrets=['JIRA_TOKEN'])\n"
    ),
    "integration-tests/connectors/jira/test_jira.py": textwrap.dedent('''\
        import pytest

        pytestmark = [pytest.mark.integration, pytest.mark.jira]


        class TestJira:
            @pytest.mark.order(1)
            async def test_tc_sync_001_full_sync(self):
                """TC-SYNC-001: a full sync puts every issue in the graph."""

            @pytest.mark.order(2)
            async def test_tc_incr_001_incremental(self):
                """TC-INCR-001: a new issue appears after an incremental sync."""
        '''),
    "integration-tests/retrieval/test_search.py": textwrap.dedent('''\
        import pytest

        pytestmark = pytest.mark.retrieval


        def test_acl_filter():
            """TC-RET-001: search hides records the user cannot read.

            covers: backend/python/app/services/retrieval.py
            needs: service:graph-db
            """


        def test_ranking():
            """TC-RET-002: exact title matches rank first."""
        '''),
    "integration-tests/plus/test_ai_usage.py": 'def test_budget():\n    """TC-PLUS-USAGE-001: usage stops at the org budget."""\n',
}


def make_repo(overlay: bool = True) -> tuple[Path, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory(prefix="qa-fixture-")
    root = Path(tmp.name)
    files = dict(FILES)
    files["scripts/qa/rules/suites.toml"] = SUITES
    files["scripts/qa/rules/always.toml"] = ALWAYS
    files["scripts/qa/rules/impact.toml"] = IMPACT
    files["scripts/qa/rules/modules.toml"] = MODULES
    files["scripts/qa/rules/flows.toml"] = FLOWS
    if overlay:
        files["scripts/plus/qa/rules/suites.toml"] = PLUS_SUITES
        files["scripts/plus/qa/qa_hooks.py"] = PLUS_HOOKS
        files["scripts/plus/qa/rules/flows.toml"] = PLUS_FLOWS
    else:
        files = {k: v for k, v in files.items() if "/plus/" not in k}
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "qa@example.com")
    git(root, "config", "user.name", "QA")
    commit(root, "base")
    config.clear_cache()
    return root, tmp


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message, "--allow-empty")
    config.clear_cache()
    return git(root, "rev-parse", "HEAD")


def write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# --- reliability fixtures: history files, shadow records and metrics --------------

TODAY = dt.date(2026, 10, 31)


def ts(day: dt.date) -> int:
    return int(dt.datetime.combine(day, dt.time(12), tzinfo=dt.timezone.utc).timestamp())


def shadow_records(days: int, n: int, caught: int) -> list[dict]:
    """One old record so the data spans `days`, then n failures today, `caught` of them caught."""
    old = {"ts": ts(TODAY - dt.timedelta(days=days)), "head": "h0", "caught": [], "missed": [], "unmapped": []}
    ids = [f"py:t{i}.py" for i in range(n)]
    return [old, {"ts": ts(TODAY), "head": "h1", "caught": ids[:caught], "missed": ids[caught:], "unmapped": []}]


def history_run(day: dt.date, trigger: str, wall: float | None, counts: dict | None = None) -> dict:
    return {"ts": ts(day), "trigger": trigger, "wall_seconds": wall, "counts": counts or {"pass": 1}, "outcomes": {}}


def history_files(runs: list[dict], tests: dict | None = None, quarantined: list[dict] | None = None,
                  shadow: list[dict] | None = None) -> dict[str, str]:
    return {"runs.jsonl": "".join(json.dumps(r) + "\n" for r in runs),
            "tests.json": json.dumps({k: {"o": v, "seen": TODAY.isoformat()} for k, v in (tests or {}).items()}),
            "quarantine.json": json.dumps({"tests": quarantined or []}),
            "shadow.jsonl": "".join(json.dumps(r) + "\n" for r in shadow or [])}


def fixture_metrics(rows: list[dict] | None = None, **kw) -> dict:
    """Two triggers over the window, one flaky test and an auto quarantine entry."""
    days = [TODAY - dt.timedelta(days=d) for d in range(3)]
    runs = [history_run(d, "nightly", 100 + i, {"pass": 5, "fail": 1, "flaky": 1}) for i, d in enumerate(days)]
    runs += [history_run(TODAY, "local", 12.5, {"pass": 2, "not_run": 1}), history_run(TODAY - dt.timedelta(days=40), "nightly", 9)]
    files = history_files(runs, {"py:a.py": "ppkp"}, [{"id": "py:a.py", "expires": "2026-11-10", "auto": True}], shadow_records(15, 50, 49))
    rows = rows if rows is not None else [{"id": "py:a.py", "suite": "py-unit", "module": "m", "flows": ["f"], "priority": "P0"}]
    return metrics.compute(files, rows, TODAY, bugs=kw.get("bugs", {"caught": 0, "escaped": 0, "escaped_by_flow": {}, "unlinked": 0}),
                           coverage=kw.get("coverage", {"py-unit": 87.5, "node-unit": None}), pr_walls=kw.get("pr_walls"))

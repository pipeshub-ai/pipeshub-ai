"""Which shards and suites a pull request's changes select.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import it_select  # noqa: E402
import shard_balance as balance  # noqa: E402

FILTERS = balance.REPO / ".github/integration-test-paths.yml"
WORKFLOW_TEXT = balance.WORKFLOW.read_text(encoding="utf-8")
SHARDS = balance.shard_markers(WORKFLOW_TEXT)
ENV = {f"CONN_SHARD_{name.rsplit('-', 1)[1]}": " or ".join(markers) for name, markers in SHARDS.items()}
FILTER_NAMES = re.findall(r"^(\w+):\s*$", FILTERS.read_text(encoding="utf-8"), re.MULTILINE)
FULL = [*SHARDS, "core", "demo"]


def outputs(matches: dict[str, list[str]], renamed: bool = False) -> dict[str, str]:
    """dorny/paths-filter outputs with `list-files: json` for these per-filter matches."""
    changed = sorted({f for files in matches.values() for f in files})
    out = {"all": "true", "all_files": json.dumps(changed), "renamed": str(renamed).lower()}
    for name in set(FILTER_NAMES) - {"all", "renamed"}:
        files = matches.get(name, [])
        out[name] = str(bool(files)).lower()
        out[f"{name}_files"] = json.dumps(files)
    return out


def select(matches: dict[str, list[str]], renamed: bool = False):
    shards, markers, _ = it_select.select(outputs(matches, renamed), ENV)
    return shards, markers


class TestSelect(unittest.TestCase):
    def test_one_connector_runs_its_suite_alone(self) -> None:
        shards, markers = select({"notion": ["backend/python/app/connectors/sources/notion/connector.py"]})
        self.assertEqual(markers, ["notion"])
        self.assertEqual(shards, [s for s, names in SHARDS.items() if "notion" in names])

    def test_a_shared_dependency_selects_its_dependents(self) -> None:
        s3_file = "backend/python/app/connectors/sources/s3/base_connector.py"
        _, markers = select({"s3": [s3_file], "minio": [s3_file]})
        self.assertEqual(markers, ["minio", "s3"])

    def test_a_file_no_filter_names_runs_everything(self) -> None:
        matches = {"notion": ["integration-tests/connectors/notion/conftest.py"]}
        matches["all"] = ["backend/python/app/services/graph_db/provider.py"]
        shards, markers = select(matches)
        self.assertEqual((shards, markers), (FULL, []))

    def test_a_rename_runs_everything(self) -> None:
        shards, markers = select({"notion": ["integration-tests/connectors/notion/x.py"]}, renamed=True)
        self.assertEqual((shards, markers), (FULL, []))

    def test_docs_alone_run_nothing(self) -> None:
        self.assertEqual(select({"no_stack": ["docs/a.md", "README.md"]}), ([], []))

    def test_the_frontend_runs_core_for_the_browser_tests(self) -> None:
        self.assertEqual(select({"frontend": ["frontend/app/page.tsx"]}), (["core"], []))

    def test_a_pull_request_never_selects_ai_agents(self) -> None:
        self.assertNotIn("ai_agents", FULL)


class TestFiltersFile(unittest.TestCase):
    def test_every_filter_is_a_connector_marker_in_a_shard(self) -> None:
        """A marker filter no shard runs would select a suite that then never runs."""
        sharded = {m for markers in SHARDS.values() for m in markers}
        markers = set(FILTER_NAMES) - it_select.NOT_MARKERS
        self.assertEqual(markers - sharded, set())
        self.assertLessEqual(it_select.NOT_MARKERS, set(FILTER_NAMES))


if __name__ == "__main__":
    unittest.main()

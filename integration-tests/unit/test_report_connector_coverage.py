"""The run summary separates suites that ran from suites that covered nothing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_connector_coverage import report, suite_name, tally  # noqa: E402

pytestmark = pytest.mark.unit

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="connectors.jira.jira_integration_test.TestJira" name="a"/>
  <testcase classname="connectors.notion.notion_integration_test.TestNotion" name="b">
    <skipped message="Notion credentials are not set."/>
  </testcase>
  <testcase classname="connectors.notion.notion_integration_test.TestNotion" name="c">
    <skipped message="Notion credentials are not set."/>
  </testcase>
  <testcase classname="retrieval.test_search.TestSearch" name="d"/>
</testsuite></testsuites>
"""


@pytest.fixture
def junit(tmp_path: Path) -> Path:
    path = tmp_path / "results.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


def test_it_counts_tests_and_skips_per_connector(junit: Path) -> None:
    assert tally([junit]) == {"jira": (1, 0), "notion": (2, 2)}


def test_it_ignores_tests_outside_connectors(junit: Path) -> None:
    assert "retrieval" not in tally([junit])
    assert suite_name("retrieval.test_search.TestSearch") is None


def test_it_names_the_suites_that_covered_nothing(junit: Path) -> None:
    text = report(tally([junit]))
    assert "1 connector suites ran; 1 covered nothing." in text
    assert "Every test skipped in: notion." in text


def test_a_run_with_no_connector_tests_says_so(tmp_path: Path) -> None:
    assert "No connector tests were collected" in report(tally([]))


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert tally([tmp_path / "absent.xml"]) == {}

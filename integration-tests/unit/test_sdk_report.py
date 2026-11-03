"""The SDK test report never turns a case that did not run into a pass.

The verdict decides whether a change that breaks an SDK can merge. Speakeasy
writes a skipped placeholder for a case it could not generate, and a suite that
crashes writes no report at all; both must read as BLOCK, not as green.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sdk_tests import report
from sdk_tests.report import (
    COMMENT_MARKER,
    RunInfo,
    Status,
    Verdict,
    case_id,
    overall_verdict,
    read_junit,
    read_releases,
    read_results,
    render_summary,
    slack_payloads,
)

pytestmark = pytest.mark.unit

PASS = '<testcase name="{name}"/>'
FAIL = '<testcase name="{name}"><failure message="expected 200, got 500">trace</failure></testcase>'
ERROR = '<testcase name="{name}"><error message="connection refused"/></testcase>'
SKIP = '<testcase name="{name}"><skipped/></testcase>'


def write_report(directory: Path, run: str, language: str, *cases: str) -> None:
    body = "".join(cases)
    (directory / f"{run}-{language}.xml").write_text(
        f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8"
    )


def write_all_passing(directory: Path, run: str) -> None:
    write_report(
        directory, run, "typescript", PASS.format(name="Agents Agent Lifecycle")
    )
    write_report(
        directory, run, "python", PASS.format(name="test_agents_agent_lifecycle")
    )
    write_report(directory, run, "go", PASS.format(name="TestAgents_AgentLifecycle"))


@pytest.mark.parametrize(
    "name",
    [
        "Agents Agent Lifecycle",
        "test_agents_agent_lifecycle",
        "TestAgents_AgentLifecycle",
    ],
)
def test_the_same_case_has_one_id_in_every_sdk(name: str):
    assert case_id(name) == "agentsagentlifecycle"


def test_all_passing_is_ok(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")

    assert overall_verdict(read_results(tmp_path)) is Verdict.OK


def test_a_previous_run_with_no_results_is_flag(tmp_path: Path):
    """The previous run starts for every language whose current run passed."""
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    (tmp_path / "previous-go.xml").unlink()

    results = read_results(tmp_path)

    assert overall_verdict(results) is Verdict.FLAG
    assert (
        "go previous (last release): previous suite: the previous run wrote no results"
        in (render_summary(results))
    )


def test_no_previous_run_after_a_current_failure_is_not_reported(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path, "current", "go", FAIL.format(name="TestAgents_AgentLifecycle")
    )

    results = read_results(tmp_path)

    assert results[2].previous is None
    assert "| go | BLOCK | 0/1 passed | not run (last release) |" in render_summary(
        results
    )


@pytest.mark.parametrize("run", ["current", "previous"])
def test_a_suite_that_exits_non_zero_fails_even_with_passing_cases(
    tmp_path: Path, run: str
):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    (tmp_path / f"{run}-typescript.exit").write_text("1\n", encoding="utf-8")
    (tmp_path / "current-python.exit").write_text("0\n", encoding="utf-8")

    results = read_results(tmp_path)

    expected = Verdict.BLOCK if run == "current" else Verdict.FLAG
    assert results[0].verdict is expected
    assert results[1].verdict is Verdict.OK
    assert f"typescript {run}" in render_summary(results)
    assert "exited with code 1" in render_summary(results)


def test_the_summary_lists_the_failures_an_approval_covers(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    write_report(
        tmp_path, "previous", "python", FAIL.format(name="test_agents_agent_lifecycle")
    )
    write_report(
        tmp_path, "previous", "go", FAIL.format(name="TestAgents_AgentLifecycle")
    )

    lines = render_summary(read_results(tmp_path)).splitlines()

    assert (
        lines[1]
        == "<!-- sdk-flag-cases: go/agentsagentlifecycle python/agentsagentlifecycle -->"
    )


def test_previous_failure_is_flag(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    write_report(
        tmp_path, "previous", "python", FAIL.format(name="test_agents_agent_lifecycle")
    )

    results = read_results(tmp_path)

    assert overall_verdict(results) is Verdict.FLAG
    assert {r.language: r.verdict for r in results} == {
        "typescript": Verdict.OK,
        "python": Verdict.FLAG,
        "go": Verdict.OK,
    }


@pytest.mark.parametrize("broken", [FAIL, ERROR, SKIP])
def test_current_failure_error_or_skip_is_block(tmp_path: Path, broken: str):
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path, "current", "go", broken.format(name="TestAgents_AgentLifecycle")
    )

    assert overall_verdict(read_results(tmp_path)) is Verdict.BLOCK


def test_missing_current_report_is_block(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    (tmp_path / "current-go.xml").unlink()

    results = read_results(tmp_path)

    assert overall_verdict(results) is Verdict.BLOCK
    assert "| go | BLOCK | not run |" in render_summary(results)
    assert "- go current: no results" in render_summary(results)


def test_block_outranks_flag(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    write_report(
        tmp_path, "current", "typescript", FAIL.format(name="Agents Agent Lifecycle")
    )
    write_report(
        tmp_path, "previous", "python", FAIL.format(name="test_agents_agent_lifecycle")
    )

    assert overall_verdict(read_results(tmp_path)) is Verdict.BLOCK


def test_current_passed_prints_nothing_when_no_language_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "argv", ["report.py", str(tmp_path), "--current-passed"])

    assert report.main() == 0
    assert capsys.readouterr().out == ""


def test_case_missing_from_the_previous_release_is_not_a_failure(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path,
        "current",
        "python",
        PASS.format(name="test_agents_agent_lifecycle"),
        PASS.format(name="test_search_lifecycle"),
    )
    write_all_passing(tmp_path, "previous")

    results = read_results(tmp_path)

    assert overall_verdict(results) is Verdict.OK
    assert "no such case" in render_summary(results)


def test_a_skipped_case_is_read_as_skipped_with_a_reason(tmp_path: Path):
    write_report(
        tmp_path, "current", "typescript", SKIP.format(name="Agents Agent Lifecycle")
    )

    cases = read_junit(tmp_path / "current-typescript.xml")

    assert cases is not None
    assert cases["agentsagentlifecycle"].status is Status.SKIPPED
    assert "never ran" in cases["agentsagentlifecycle"].detail


def test_summary_and_slack_name_each_failed_case(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    (tmp_path / "previous-go.xml").unlink()
    write_report(
        tmp_path, "current", "go", FAIL.format(name="TestAgents_AgentLifecycle")
    )

    results = read_results(tmp_path)
    summary = render_summary(results)
    payloads = slack_payloads(
        results, RunInfo(trigger="Nightly", run_url="https://example.test/run/1")
    )

    assert summary.startswith(COMMENT_MARKER)
    assert "## SDK tests: BLOCK" in summary
    assert "- go current: TestAgents_AgentLifecycle: expected 200, got 500" in summary
    assert payloads["message"]["text"] == ":red_circle: SDK Tests: BLOCK - Nightly"
    assert payloads["message"]["blocks"][-1]["elements"][0]["url"] == (
        "https://example.test/run/1"
    )
    assert payloads["thread"] == {
        "text": "*Failed cases (1):*\n```\n"
        "go current: TestAgents_AgentLifecycle: expected 200, got 500\n```"
    }


def test_the_slack_table_has_one_row_per_sdk_and_run(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path,
        "current",
        "go",
        PASS.format(name="TestAgents_AgentLifecycle"),
        FAIL.format(name="TestSearch_SearchLifecycle"),
        SKIP.format(name="TestWebSearch_Providers"),
    )
    write_all_passing(tmp_path, "previous")
    (tmp_path / "previous-go.xml").unlink()

    message = slack_payloads(read_results(tmp_path), RunInfo(), {"go": "v1.7.0"})[
        "message"
    ]
    table = next(block for block in message["blocks"] if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in table["rows"]]

    assert rows[0] == [
        "SDK",
        "Run",
        "Total",
        "Passed",
        "Failed",
        "Skipped",
        "Pass rate",
    ]
    assert ["typescript", "current", "1", "1", "0", "0", "100%"] in rows
    assert ["go", "current", "3", "1", "1", "1", "33%"] in rows
    assert ["go", "previous (v1.7.0)", "not run", "-", "-", "-", "-"] in rows
    assert len(rows) == 7


def test_the_slack_message_links_the_pull_request_and_its_author(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    info = RunInfo(
        trigger="PR #7",
        pr_url="https://github.com/acme/app/pull/7",
        pr_title="fix <b> & co",
        author="ana",
        branch="fix/b",
        commit="0123456789abcdef",
        generator="Speakeasy v1.801.0",
    )

    blocks = slack_payloads(read_results(tmp_path), info)["message"]["blocks"]
    fields = [field["text"] for field in blocks[1]["fields"]]

    assert fields == [
        "*PR:*\n<https://github.com/acme/app/pull/7|fix &lt;b&gt; &amp; co>",
        "*Author:*\n<https://github.com/ana|@ana>",
        "*Branch:*\n`fix/b`",
        "*Commit:*\n`012345678`",
    ]
    assert blocks[-1]["type"] == "context"
    assert "Speakeasy v1.801.0" in blocks[-1]["elements"][0]["text"]


def test_a_passing_run_has_no_slack_thread(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")

    assert slack_payloads(read_results(tmp_path), RunInfo())["thread"] is None


def test_slack_thread_escapes_control_characters(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    (tmp_path / "previous-go.xml").unlink()
    write_report(
        tmp_path,
        "current",
        "go",
        '<testcase name="TestAgents_AgentLifecycle">'
        '<failure message="got &lt;nil&gt; &amp; 500"/></testcase>',
    )

    thread = slack_payloads(read_results(tmp_path), RunInfo())["thread"]

    assert (
        "go current: TestAgents_AgentLifecycle: got &lt;nil&gt; &amp; 500"
        in (thread["text"])
    )


def test_a_flag_names_the_release_that_breaks(tmp_path: Path):
    releases_file = tmp_path / "sdk-test-matrix.yaml"
    releases_file.write_text(
        "# The last released tag of each SDK.\ntypescript: v1.7.0\npython: v1.7.0  # note\n",
        encoding="utf-8",
    )
    write_all_passing(tmp_path, "current")
    write_all_passing(tmp_path, "previous")
    write_report(
        tmp_path, "previous", "python", FAIL.format(name="test_agents_agent_lifecycle")
    )

    releases = read_releases(releases_file)
    summary = render_summary(read_results(tmp_path), releases)

    assert releases == {"typescript": "v1.7.0", "python": "v1.7.0"}
    assert "sdk-break-approved" in summary
    assert "| python | FLAG | 1/1 passed | 0/1 passed (v1.7.0) |" in summary
    assert "| go | OK | 1/1 passed | 1/1 passed (last release) |" in summary
    assert "- python previous (v1.7.0): test_agents_agent_lifecycle" in summary


@pytest.mark.parametrize("content", ["", "<testsuites><testsuite>"])
def test_a_truncated_report_is_block(tmp_path: Path, content: str):
    write_all_passing(tmp_path, "current")
    (tmp_path / "current-go.xml").write_text(content, encoding="utf-8")

    results = read_results(tmp_path)

    assert results[2].current is None
    assert overall_verdict(results) is Verdict.BLOCK


def test_a_go_failure_shows_the_error_from_its_body(tmp_path: Path):
    """go-junit-report puts only "Failed" in the message attribute."""
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path,
        "current",
        "go",
        '<testcase name="TestAgents_AgentLifecycle"><failure message="Failed">'
        "    agents_test.go:33: \n"
        "        \tError Trace:\t/tmp/sdk/tests/agents_test.go:33\n"
        "        \tError:      \tReceived unexpected error:\n"
        "        \t            \tdial tcp 127.0.0.1:9: connection refused\n"
        "        \tTest:       \tTestAgents_AgentLifecycle\n"
        "</failure></testcase>",
    )

    summary = render_summary(read_results(tmp_path))

    assert (
        "- go current: TestAgents_AgentLifecycle: agents_test.go:33: Error: "
        "Received unexpected error: dial tcp 127.0.0.1:9: connection refused"
    ) in summary


def test_a_failure_with_no_message_still_renders(tmp_path: Path):
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path,
        "current",
        "go",
        '<testcase name="TestAgents_AgentLifecycle"><failure/></testcase>',
    )

    summary = render_summary(read_results(tmp_path))

    assert "- go current: TestAgents_AgentLifecycle: no detail" in summary


def test_current_passed_lists_only_languages_with_a_clean_current_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    write_all_passing(tmp_path, "current")
    write_report(
        tmp_path, "current", "python", SKIP.format(name="test_agents_agent_lifecycle")
    )
    (tmp_path / "current-go.xml").unlink()
    monkeypatch.setattr(sys, "argv", ["report.py", str(tmp_path), "--current-passed"])

    assert report.main() == 0
    assert capsys.readouterr().out.split() == ["typescript"]

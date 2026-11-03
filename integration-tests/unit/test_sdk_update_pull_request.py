"""update_pull_request.sh keeps one report comment, and sets the FLAG label and status.

A FLAG must leave a failed approval status until a person adds sdk-break-approved,
and a later OK must take the FLAG label away. The script runs here against a fake
`gh` that records each call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq"),
]

SCRIPT = Path(__file__).resolve().parents[1] / "sdk_tests/update_pull_request.sh"
MARKER = "<!-- sdk-tests-report -->"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
case "$*" in
  *"issues/7/comments"*) [ "$1 $2" = "api --paginate" ] && cat "$FAKE_DIR/comments.json" ;;
  *"issues/7/labels --jq"*) cat "$FAKE_DIR/labels.txt" ;;
  *"issues/7/events"*) cat "$FAKE_DIR/events.json" ;;
esac
exit 0
"""


def run(
    tmp_path: Path,
    verdict: str,
    *,
    labels: list[str],
    comments: list[dict] | None = None,
    events: list[dict] | None = None,
    flag_cases: str = "",
) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    (tmp_path / "comments.json").write_text(json.dumps(comments or []))
    (tmp_path / "labels.txt").write_text("".join(f"{label}\n" for label in labels))
    (tmp_path / "events.json").write_text(json.dumps(events or []))
    summary = tmp_path / "summary.md"
    summary.write_text(
        f"{MARKER}\n{flag_line(flag_cases)}\n## SDK tests: {verdict}\n",
        encoding="utf-8",
    )
    log = tmp_path / "gh.log"
    log.touch()

    subprocess.run(
        [str(SCRIPT), "7", verdict, str(summary)],
        check=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_DIR": str(tmp_path),
            "FAKE_GH_LOG": str(log),
            "GITHUB_REPOSITORY": "acme/app",
            "HEAD_SHA": "abc123",
            "RUN_URL": "https://example.test/run/1",
        },
    )
    return log.read_text(encoding="utf-8").splitlines()


def test_flag_without_approval_labels_the_pr_and_fails_the_status(tmp_path: Path):
    calls = run(tmp_path, "FLAG", labels=["bug"])

    assert (
        "api -X POST repos/acme/app/issues/7/comments -F body=@"
        + str(tmp_path / "summary.md")
        in calls
    )
    assert (
        "api -X POST repos/acme/app/issues/7/labels -f labels[]=breaks-previous-sdk"
        in calls
    )
    status = next(call for call in calls if "statuses/abc123" in call)
    assert "state=failure" in status
    assert "add the label sdk-break-approved" in status


def flag_line(cases: str) -> str:
    return f"<!-- sdk-flag-cases: {cases} -->"


def report_comment(comment_id: int, cases: str) -> dict:
    body = f"{MARKER}\n{flag_line(cases)}\n## SDK tests: FLAG\n"
    return {"id": comment_id, "user": {"login": "github-actions[bot]"}, "body": body}


def test_flag_with_approval_of_the_same_failures_names_who_approved(tmp_path: Path):
    calls = run(
        tmp_path,
        "FLAG",
        labels=["breaks-previous-sdk", "sdk-break-approved"],
        comments=[report_comment(5, "go/searchlifecycle")],
        flag_cases="go/searchlifecycle",
        events=[
            {
                "event": "labeled",
                "label": {"name": "sdk-break-approved"},
                "actor": {"login": "ana"},
            },
            {"event": "labeled", "label": {"name": "bug"}, "actor": {"login": "bo"}},
        ],
    )

    status = next(call for call in calls if "statuses/abc123" in call)
    assert "state=success" in status
    assert "description=Approved by @ana" in status
    assert not any("DELETE" in call for call in calls)


def test_an_approval_does_not_cover_new_failures(tmp_path: Path):
    calls = run(
        tmp_path,
        "FLAG",
        labels=["breaks-previous-sdk", "sdk-break-approved"],
        comments=[report_comment(5, "go/searchlifecycle")],
        flag_cases="go/searchlifecycle python/agentlifecycle",
    )

    assert "api -X DELETE repos/acme/app/issues/7/labels/sdk-break-approved" in calls
    status = next(call for call in calls if "statuses/abc123" in call)
    assert "state=failure" in status


def test_a_later_run_edits_its_own_comment(tmp_path: Path):
    calls = run(
        tmp_path,
        "OK",
        labels=[],
        comments=[
            {
                "id": 11,
                "user": {"login": "github-actions[bot]"},
                "body": "other bot comment",
            },
            {"id": 12, "user": {"login": "someone"}, "body": f"{MARKER} copied"},
            {
                "id": 13,
                "user": {"login": "github-actions[bot]"},
                "body": f"{MARKER}\nold",
            },
        ],
    )

    assert any(
        call.startswith("api -X PATCH repos/acme/app/issues/comments/13 ")
        for call in calls
    )
    assert not any("POST repos/acme/app/issues/7/comments" in call for call in calls)


def test_ok_removes_both_labels_and_sets_no_status(tmp_path: Path):
    calls = run(tmp_path, "OK", labels=["breaks-previous-sdk", "sdk-break-approved"])

    assert "api -X DELETE repos/acme/app/issues/7/labels/breaks-previous-sdk" in calls
    assert "api -X DELETE repos/acme/app/issues/7/labels/sdk-break-approved" in calls
    assert not any("statuses/" in call for call in calls)


def test_block_changes_no_label(tmp_path: Path):
    calls = run(tmp_path, "BLOCK", labels=["breaks-previous-sdk"])

    assert not any("/labels" in call and "-X" in call for call in calls)
    assert not any("statuses/" in call for call in calls)

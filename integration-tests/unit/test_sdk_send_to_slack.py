"""send_to_slack.sh posts the message, then the reports zip and the failures in its thread.

The script runs here against a fake `curl` that records each call and answers as Slack does.
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

SCRIPT = Path(__file__).resolve().parents[1] / "sdk_tests/send_to_slack.sh"

FAKE_CURL = """#!/usr/bin/env bash
url=""
for arg in "$@"; do case "$arg" in https://*) url="$arg" ;; esac; done
body=""
for arg in "$@"; do [ "$arg" = "@-" ] && body=$(cat); done
jq -cn --arg url "$url" --arg body "$body" '{url: $url, body: $body}' >> "$FAKE_CURL_LOG"
case "$url" in
  *chat.postMessage) echo "${FAKE_POST_ANSWER:-{\\"ok\\": true, \\"ts\\": \\"111.222\\"}}" ;;
  *files.getUploadURLExternal) echo '{"ok": true, "upload_url": "https://upload.test/f", "file_id": "F1"}' ;;
  *files.completeUploadExternal) echo '{"ok": true}' ;;
  *) echo OK ;;
esac
"""


def run(tmp_path: Path, *, thread: dict | None, token: str = "xoxb-test", **env: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    payloads = tmp_path / "slack.json"
    payloads.write_text(
        json.dumps({"message": {"text": "SDK Tests: BLOCK"}, "thread": thread}),
        encoding="utf-8",
    )
    reports = tmp_path / "sdk-test-reports.zip"
    reports.write_bytes(b"zip")
    log = tmp_path / "curl.log"
    log.touch()
    result = subprocess.run(
        [str(SCRIPT), str(payloads), str(reports)],
        check=False,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_CURL_LOG": str(log),
            "SLACK_BOT_TOKEN": token,
            "SLACK_CHANNEL_ID": "C1",
            **env,
        },
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return result, calls


def test_posts_the_message_then_the_zip_and_the_failures_in_its_thread(tmp_path: Path):
    result, calls = run(tmp_path, thread={"text": "*Failed cases (1):*"})

    assert result.returncode == 0, result.stderr
    assert [call["url"].rsplit("/", 1)[-1] for call in calls] == [
        "chat.postMessage",
        "files.getUploadURLExternal",
        "f",
        "files.completeUploadExternal",
        "chat.postMessage",
    ]
    assert json.loads(calls[0]["body"]) == {"text": "SDK Tests: BLOCK", "channel": "C1"}
    assert json.loads(calls[3]["body"]) == {
        "files": [{"id": "F1", "title": "SDK test reports"}],
        "channel_id": "C1",
        "thread_ts": "111.222",
    }
    assert json.loads(calls[4]["body"]) == {
        "text": "*Failed cases (1):*",
        "channel": "C1",
        "thread_ts": "111.222",
    }


def test_a_passing_run_posts_no_failures(tmp_path: Path):
    _, calls = run(tmp_path, thread=None)

    assert [call["url"].rsplit("/", 1)[-1] for call in calls][-1] == (
        "files.completeUploadExternal"
    )


def test_without_a_token_nothing_is_posted(tmp_path: Path):
    result, calls = run(tmp_path, thread=None, token="")

    assert result.returncode == 0
    assert calls == []


def test_a_refused_message_fails_the_script(tmp_path: Path):
    result, calls = run(
        tmp_path,
        thread=None,
        FAKE_POST_ANSWER='{"ok": false, "error": "channel_not_found"}',
    )

    assert result.returncode != 0
    assert "channel_not_found" in result.stderr
    assert len(calls) == 1

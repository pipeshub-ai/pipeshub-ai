"""The log reader the crash suite's ground-rule-9 check relies on."""

from __future__ import annotations

import stack

NODE = """2026-09-27 15:36:26,326 - nodejs_service - INFO - [run-admission-service.ts:165] - admission.accepted
metadata: {
  "runId": "run-1"
}
2026-09-27 15:36:27,032 - nodejs_service - DEBUG - [validation.middleware.ts:39] - Validation successful
2026-09-27 15:36:28,000 - nodejs_service - \x1b[31mERROR\x1b[0m - [error.middleware.utils.ts:84] - Application error
"""
WORKER = (
    "2026-09-27 11:02:44,708 - harness_core.driver - INFO - [req:anon-1 thr:MainThread task:run-1] "
    "[logs.py:108] - run.claimed orgId=o runId=run-1 fence=2\n"
    "2026-09-27 11:02:45,291 - app.harness.worker - WARNING - [logs.py:108] - journal.unreachable runId=run-1\n"
    "2026-09-27 11:02:46,000 - root - ERROR - plain message\n"
)


def test_a_node_record_spans_its_metadata_block() -> None:
    records = stack.log_records("node", NODE)

    assert [(r.level, r.event) for r in records] == [
        ("INFO", "admission.accepted"),
        ("DEBUG", "Validation"),
        ("ERROR", "Application"),
    ]
    assert '"runId": "run-1"' in records[0].text
    assert "run-1" not in records[1].text


def test_worker_records_with_and_without_context_brackets() -> None:
    records = stack.log_records("worker", WORKER)

    assert [(r.logger, r.level, r.event) for r in records] == [
        ("harness_core.driver", "INFO", "run.claimed"),
        ("app.harness.worker", "WARNING", "journal.unreachable"),
        ("root", "ERROR", "plain"),
    ]


def test_a_record_still_being_appended_is_not_read(tmp_path) -> None:
    """RU-4a failed once on a 20 KB disposition read between two of its writes (OPS1)."""
    log = tmp_path / "dispositions.jsonl"
    log.write_text('{"runId": "run-1"}\n{"runId": "run-2", "kernel": "xxxx')

    assert stack.jsonl(log) == [{"runId": "run-1"}]

    log.write_text('{"runId": "run-1"}\n{"runId": "run-2", "kernel": "xxxx"}\n')
    assert [r["runId"] for r in stack.jsonl(log)] == ["run-1", "run-2"]

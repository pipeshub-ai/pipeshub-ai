"""The replay kit on its own: scrubbing (J2), recording order, script and
frozen-tool round trips, and ``harness-diff`` (J3)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness_core.testing import ModelRequest, RequestMessage, ScriptedModel
from harness_core.testing.replay import (
    FinalOutcome,
    FrozenTools,
    Manifest,
    ModelStep,
    RecordedEffect,
    RecordedResponse,
    RecordedToolCall,
    Recording,
    RecordingModel,
    RunRecorder,
    Scrubber,
    SecretLeftBehind,
    ToolStep,
    diff_recordings,
    find_secrets,
    recording_to_script,
    scrub_recording,
)

BACKEND_PYTHON = Path(__file__).resolve().parents[3]

SECRETS = {
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEyMyJ9.c2lnbmF0dXJlLXZhbHVl",
    "openai": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234",
    "anthropic": "sk-ant-api03-AbCdEfGhIjKlMnOpQrSt",
    "slack": "xoxb-2231-99887766-AbCdEfGhIjKl",
    "github": "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "google": "AIzaSyA-1234567890abcdefghijklmnopqrstu",
    "pat": "phpat_4f2c9a8b7e6d5c4b3a2f1e0d",
}


def _texts() -> dict[str, str]:
    return {
        "bearer": "Authorization: Bearer abcDEF123456789xyz",
        "url_creds": "clone https://deploy:s3cr3tPass@git.corp.example/repo.git now",
        "query": "download https://storage.test/f.csv?X-Amz-Signature=abc123def&token=zzz999&page=2",
        "kv": 'config: password=hunter22, api_key: "k-778899", client_secret = topsecretvalue',
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
        **{f"token_{k}": f"use {v} here" for k, v in SECRETS.items()},
    }


class TestScrubber:
    """J2: tokens, emails and credential URLs never survive a scrub."""

    @pytest.mark.parametrize("name", sorted(_texts()))
    def test_each_secret_shape_is_removed(self, name: str) -> None:
        raw = _texts()[name]
        scrubbed = Scrubber().text(raw)
        assert find_secrets(scrubbed) == [], scrubbed
        assert "<redacted:" in scrubbed
        for secret in [
            *SECRETS.values(),
            "s3cr3tPass",
            "hunter22",
            "zzz999",
            "abc123def",
            "MIIEow",
        ]:
            assert secret not in scrubbed

    def test_url_keeps_its_host_and_harmless_query(self) -> None:
        out = Scrubber().text(_texts()["query"] + " " + _texts()["url_creds"])
        assert "storage.test/f.csv?X-Amz-Signature=<redacted:query>" in out
        assert "page=2" in out
        assert "https://<redacted:userinfo>@git.corp.example/repo.git" in out

    def test_emails_become_stable_pseudonyms(self) -> None:
        scrubber = Scrubber()
        a = scrubber.text("mail dana.lee@corp.example and Dana.Lee@corp.example")
        b = scrubber.text("cc dana.lee@corp.example, bob@partner.example")
        first = a.split()[1]
        assert first.startswith("person-") and first.endswith("@example.invalid")
        assert a.split()[3] == first, "case-insensitive: one person, one pseudonym"
        assert b.split()[1].rstrip(",") == first
        assert b.split()[2] != first
        assert "corp.example" not in a + b and "partner.example" not in b

    def test_pseudonyms_do_not_depend_on_order_but_on_the_key(self) -> None:
        one, two = Scrubber(key=b"k"), Scrubber(key=b"k")
        one.text("x@a.example")
        assert one.text("y@b.example") == two.text("y@b.example")
        assert Scrubber(key=b"other").text("y@b.example") != two.text("y@b.example")

    def test_scrubbing_is_idempotent(self) -> None:
        scrubber = Scrubber()
        once = scrubber.text(" ".join(_texts().values()) + " ann@corp.example")
        assert scrubber.text(once) == once

    def test_the_canary_scan_names_what_it_found_without_printing_it(self) -> None:
        # The findings reach CI logs and exception messages: the secret they caught must not ride along.
        texts = _texts()
        found = find_secrets([*texts.values(), "mail ann.person@corp.example"])
        assert found
        report = "; ".join(found)
        for secret in [*SECRETS.values(), "s3cr3tPass", "hunter22", "topsecretvalue", "abcDEF123456789xyz",
                       "zzz999", "ann.person"]:
            assert secret not in report, secret
        assert "email: …@corp.example" in found

    def test_the_golden_case_check_masks_personal_data(self) -> None:
        from harness_core.testing.replay.trace_redactor import leftovers

        found = leftovers([{"type": "x", "text": "call me on +1 415 555 0134 tomorrow"}])
        assert found and all("555 0134" not in f for f in found), found

    def test_a_planted_canary_blocks_the_recording(self) -> None:
        recording = _recording(final="the launch code is CANARY-51")
        with pytest.raises(SecretLeftBehind, match="CANARY-51"):
            scrub_recording(recording, canaries=["CANARY-51"])

    def test_scrub_recording_covers_every_field_and_stamps_the_version(self) -> None:
        recording = _recording(
            tool_result={"body": f"key {SECRETS['openai']} from ann@corp.example"},
            final="Sent to ann@corp.example",
            effect={"to": "ann@corp.example", "token": SECRETS["slack"]},
        )
        scrubbed = scrub_recording(recording)
        dumped = scrubbed.to_json()
        assert SECRETS["openai"] not in dumped and SECRETS["slack"] not in dumped
        assert "ann@corp.example" not in dumped
        assert scrubbed.manifest.scrub_version == "1"
        assert find_secrets(json.loads(dumped)) == []


def _recording(
    *,
    tool_args: dict | None = None,
    tool_result: object = "ok",
    tool_status: str = "succeeded",
    final: str = "done",
    effect: dict | None = None,
    extra_step: bool = False,
) -> Recording:
    args = tool_args or {"channel": "#eng"}
    steps: list[ModelStep | ToolStep] = [
        ModelStep(
            response=RecordedResponse(
                tool_calls=[RecordedToolCall(id="c1", name="read", args={})]
            )
        ),
        ToolStep(call_id="c1", name="read", args={}, status="succeeded", result="data"),
        ModelStep(
            response=RecordedResponse(
                tool_calls=[RecordedToolCall(id="c2", name="post", args=args)]
            )
        ),
        ToolStep(
            call_id="c2", name="post", args=args, status=tool_status, result=tool_result
        ),  # type: ignore[arg-type]
        ModelStep(response=RecordedResponse(text=final)),
    ]
    if extra_step:
        steps.insert(4, ModelStep(response=RecordedResponse(text="thinking")))
    return Recording(
        manifest=Manifest(scenario="unit"),
        steps=steps,
        effects=[
            RecordedEffect(
                op="slack.send_message", payload=effect or {"channel": "#eng"}
            )
        ],
        final=FinalOutcome(answer=final),
    )


class TestDiff:
    """J3: the report names the first step whose behaviour differs."""

    def test_identical_runs(self) -> None:
        report = diff_recordings(_recording(), _recording())
        assert report.identical
        assert report.render() == "Runs behave identically."

    def test_changed_tool_arguments_are_reported_at_their_step(self) -> None:
        report = diff_recordings(
            _recording(), _recording(tool_args={"channel": "#random"})
        )
        assert report.divergence is not None
        assert report.divergence.step == 3, (
            "the model step that issued the call differs first"
        )
        assert report.divergence.field == "tool_calls"
        rendered = report.render()
        assert "First divergence at step 3" in rendered
        assert "#random" in rendered and "#eng" in rendered
        assert "previous (same in both): tool read({}) -> succeeded" in rendered

    def test_a_denied_call_is_a_status_divergence(self) -> None:
        report = diff_recordings(_recording(), _recording(tool_status="denied"))
        assert report.divergence is not None
        assert (report.divergence.step, report.divergence.field) == (4, "status")

    def test_an_extra_model_call_is_reported(self) -> None:
        report = diff_recordings(_recording(), _recording(extra_step=True))
        assert report.divergence is not None
        assert report.divergence.step == 5

    def test_a_shorter_run_reports_step_count(self) -> None:
        full = _recording()
        short = full.model_copy(update={"steps": full.steps[:3]})
        report = diff_recordings(full, short)
        assert report.divergence is not None
        assert (report.divergence.step, report.divergence.field) == (4, "step count")

    def test_outcome_differences_come_after_the_steps(self) -> None:
        report = diff_recordings(_recording(), _recording(effect={"channel": "#other"}))
        assert report.divergence is not None
        assert (report.divergence.step, report.divergence.field) == (0, "effects")
        assert diff_recordings(
            _recording(),
            _recording(effect={"channel": "#other"}),
            compare_effects=False,
        ).identical

    def test_uuids_and_call_ids_are_not_behaviour(self) -> None:
        a = _recording(tool_result="question 0014423f-2894-48fc-b4b3-08ddf0536ac0")
        b = _recording(tool_result="question 7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11")
        assert diff_recordings(a, b).identical

    def test_context_changes_are_notes_unless_asked(self) -> None:
        a = _recording()
        steps = list(a.steps)
        first = steps[0]
        assert isinstance(first, ModelStep)
        steps[0] = first.model_copy(
            update={"request": first.request.model_copy(update={"digest": "x"})}
        )
        b = a.model_copy(update={"steps": steps})
        report = diff_recordings(a, b)
        assert report.identical and report.context_notes
        strict = diff_recordings(a, b, compare_context=True)
        assert (
            strict.divergence is not None
            and strict.divergence.field == "request context"
        )

    def test_cli_exit_code_and_report(self, tmp_path: Path) -> None:
        expected, actual = tmp_path / "a.json", tmp_path / "b.json"
        _recording().save(expected)
        _recording(tool_status="failed").save(actual)
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness_core.testing.replay",
                "diff",
                str(expected),
                str(actual),
            ],
            cwd=BACKEND_PYTHON,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 1, run.stderr
        assert "First divergence at step 4: status differs" in run.stdout
        same = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness_core.testing.replay",
                "diff",
                str(expected),
                str(expected),
            ],
            cwd=BACKEND_PYTHON,
            capture_output=True,
            text=True,
            check=False,
        )
        assert same.returncode == 0 and "identically" in same.stdout


class TestRecorderAndReplay:
    async def test_recording_model_records_and_script_round_trips(self) -> None:
        script = recording_to_script(_recording())
        recorder = RunRecorder()
        model = RecordingModel(ScriptedModel(script), recorder)
        request = ModelRequest((RequestMessage("user", "hi"),), tools=("read", "post"))
        for _ in range(3):
            await model.complete(request)
        steps = recorder.ordered_steps()
        assert [s.response.text if s.response else None for s in steps] == [
            "",
            "",
            "done",
        ]  # type: ignore[union-attr]
        assert [c.name for c in steps[1].response.tool_calls] == ["post"]  # type: ignore[union-attr]
        assert steps[0].request.tools == ["post", "read"]  # type: ignore[union-attr]

    async def test_errors_are_recorded_and_re_raised(self) -> None:
        recorder = RunRecorder()
        model = RecordingModel(
            ScriptedModel(recording_to_script(_recording(extra_step=False))), recorder
        )
        request = ModelRequest((RequestMessage("user", "hi"),))
        for _ in range(3):
            await model.complete(request)
        with pytest.raises(AssertionError):
            await model.complete(request)
        last = recorder.ordered_steps()[-1]
        assert isinstance(last, ModelStep) and last.error is not None
        assert last.error.kind == "ScriptExhausted"

    def test_parallel_tool_results_are_ordered_by_the_issuing_call(self) -> None:
        recorder = RunRecorder()
        request = ModelRequest((RequestMessage("user", "hi"),))
        from harness_core.testing import ModelResponse, ToolCall, Usage

        recorder.model(
            request,
            ModelResponse(
                0,
                "",
                "",
                (ToolCall("a", "one"), ToolCall("b", "two")),
                Usage(),
                "tool_use",
            ),
        )
        recorder.tool(call_id="b", name="two", args={}, status="succeeded", result=2)
        recorder.tool(call_id="a", name="one", args={}, status="succeeded", result=1)
        names = [s.name for s in recorder.ordered_steps() if isinstance(s, ToolStep)]
        assert names == ["one", "two"]

    def test_frozen_tools_serve_by_id_then_name_and_note_argument_drift(self) -> None:
        frozen = FrozenTools(_recording())
        assert frozen.lookup("c1", "read", {}).result == "data"
        drifted = frozen.lookup("other-id", "post", {"channel": "#random"})
        assert drifted.status == "succeeded"
        assert drifted.diverged and "#random" in drifted.diverged
        missing = frozen.lookup("c9", "delete", {})
        assert missing.status == "failed" and missing.diverged
        assert len(frozen.divergences) == 2
        assert frozen.unused() == []

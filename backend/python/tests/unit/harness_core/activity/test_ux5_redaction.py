"""UX5 RD: command output and command text never show a secret of the shared token vector, whole or in part.

Every example comes from the vector itself (QA-11, ``tests/support/token_vector.py``): a pattern added without an
example, or a redactor that stops reading the vector, fails these tests.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness_core.activity import (
    ActivityLimits,
    BoundedCommandActivity,
    CommandSpec,
    StreamRedactor,
)
from harness_core.activity.redaction import shared_redactor
from harness_core.journal.memory import InMemoryContentStore, InMemoryLiveBus
from harness_core.testing.clock import ManualClock
from tests.support.token_vector import ENTRIES, EXAMPLES, MASK

SECRET_MASK = MASK
IDS = [case[0] for case in EXAMPLES]
SECRETS = {case[0]: case[1] for case in EXAMPLES}
ONE_LINE = [case[0] for case in EXAMPLES if "\n" not in case[1]]
GITHUB = next(secret for name, secret, entry in EXAMPLES if entry.kind == "github_token")
PRIVATE_KEY = next(secret for name, secret, entry in EXAMPLES if entry.kind == "private_key")


def test_rd1_every_vector_entry_has_an_example() -> None:
    assert {entry.kind for entry in ENTRIES} == {entry.kind for _name, _secret, entry in EXAMPLES}


def _texts(frames: list[object]) -> str:
    return "".join(str(getattr(f, "data", {}).get("text", "")) for f in frames)  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.parametrize("case", IDS)
async def test_rd1_rd4_no_frame_tail_log_or_command_holds_a_secret(case: str) -> None:
    secret = SECRETS[case]
    bus, content = InMemoryLiveBus(), InMemoryContentStore()
    activity = BoundedCommandActivity(bus, run_id="r", attempt_id="a", fence=1, clock=ManualClock(), content=content)
    recorder = activity.begin(CommandSpec("c1", "engine", f"curl -H 'auth: {secret}' x"))
    recorder.write("stdout", f"token={secret}\n")
    recorder.write("stderr", f"{secret}\n")
    record = await recorder.finish(exit_code=0, status="completed")
    await activity.drained()
    frames = await bus.read("r")
    assert record.log is not None
    seen = [
        _texts(list(frames)),
        str([f.data.get("command") for f in frames]),
        record.spec.command,
        record.stdout.tail,
        record.stderr.tail,
        (await content.get(record.log)).decode(),
    ]
    for text in seen:
        assert secret not in text
        for part in secret.splitlines():
            if len(part) >= 16:
                assert part not in text
    assert SECRET_MASK in record.stdout.tail
    assert SECRET_MASK in record.spec.command
    await activity.aclose()


@pytest.mark.parametrize("case", ONE_LINE)
def test_rd2_a_secret_split_at_every_offset_is_never_shown(case: str) -> None:
    secret = SECRETS[case]
    line = f"before {secret} after\n"
    for cut in range(1, len(line)):
        redactor = StreamRedactor(shared_redactor(), hold_chars=4096, key_hold_chars=16384)
        shown = redactor.feed(line[:cut]) + redactor.feed(line[cut:]) + redactor.flush()
        assert secret not in shown
        assert shown == f"before {SECRET_MASK} after\n"


@settings(max_examples=60, deadline=None)
@given(cuts=st.lists(st.integers(min_value=1, max_value=200), max_size=12), case=st.sampled_from(IDS))
def test_rd2_any_chunking_never_shows_a_secret_in_part(cuts: list[int], case: str) -> None:
    secret = SECRETS[case]
    text = f"start\n{secret}\nend\n"
    redactor = StreamRedactor(shared_redactor(), hold_chars=64, key_hold_chars=16384)
    shown, at = "", 0
    for size in cuts:
        shown += redactor.feed(text[at : at + size])
        at += size
    shown += redactor.feed(text[at:]) + redactor.flush()
    for line in secret.splitlines():
        if len(line) >= 16:
            assert line not in shown
    assert shown.startswith("start\n")
    assert shown.endswith("end\n")


def test_rd3_a_private_key_is_held_to_its_end_line_and_masked_whole() -> None:
    redactor = StreamRedactor(shared_redactor(), hold_chars=64, key_hold_chars=16384)
    shown = ""
    for line in PRIVATE_KEY.splitlines(keepends=True):
        shown += redactor.feed(line)
        assert "MIIE" not in shown
    shown += redactor.feed("\nnext\n") + redactor.flush()
    assert shown == f"{SECRET_MASK}\nnext\n"


def test_rd3_an_unterminated_key_past_the_hold_is_masked_whole_and_the_rest_dropped() -> None:
    redactor = StreamRedactor(shared_redactor(), hold_chars=64, key_hold_chars=512)
    shown = redactor.feed("ok\n-----BEGIN " + "PRIVATE KEY-----\n")
    for _ in range(40):
        shown += redactor.feed("MIIE" + "B" * 60 + "\n")
    shown += redactor.feed("-----END " + "PRIVATE KEY-----\nafter\n") + redactor.flush()
    assert "MIIE" not in shown
    assert shown == f"ok\n{SECRET_MASK}\nafter\n"


def test_rd3_a_key_cut_off_by_the_end_of_output_is_masked() -> None:
    redactor = StreamRedactor(shared_redactor(), hold_chars=64, key_hold_chars=16384)
    shown = redactor.feed("-----BEGIN " + "PRIVATE KEY-----\nMIIEsecretbody\n") + redactor.flush()
    assert "MIIEsecretbody" not in shown


def test_rd6_the_redactor_reads_the_shared_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    import re

    from harness_core.security import token_patterns
    from harness_core.security.token_patterns import TokenPattern

    planted = TokenPattern("planted", re.compile(r"plant-[0-9]{6}"), forbidden=False)
    monkeypatch.setattr(token_patterns, "REDACTION_PATTERNS", (*token_patterns.REDACTION_PATTERNS, planted))
    assert shared_redactor()("x plant-123456 y") == f"x {SECRET_MASK} y"


def test_long_lines_are_released_past_the_hold_without_splitting_a_token() -> None:
    secret = GITHUB
    redactor = StreamRedactor(shared_redactor(), hold_chars=32, key_hold_chars=16384)
    line = ("word " * 40) + secret + (" tail" * 40)
    shown = ""
    for i in range(0, len(line), 7):
        shown += redactor.feed(line[i : i + 7])
    shown += redactor.flush()
    assert secret not in shown
    assert shown == line.replace(secret, SECRET_MASK)
    assert ActivityLimits().hold_chars >= 1024


@pytest.mark.parametrize("case", [name for name, _secret, entry in EXAMPLES if entry.redact_only])
def test_rd2_a_redact_only_shape_with_a_space_is_never_parted_on_a_long_line(case: str) -> None:
    secret = SECRETS[case]
    redactor = StreamRedactor(shared_redactor(), hold_chars=16, key_hold_chars=16384)
    line = ("word " * 30) + secret + (" tail" * 30)
    shown = "".join(redactor.feed(line[i : i + 5]) for i in range(0, len(line), 5)) + redactor.flush()
    assert secret.split()[-1] not in shown

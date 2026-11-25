"""RUN01-D1-PRESENT: ``OutcomePresenter`` is the only producer of the words a person reads about a run's outcome, from
locale keys, and they never claim more than the outcome holds (ADR-RUN-01 D1; "everything it gathered is
saved" when nothing had been gathered)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from harness_core.engines.outcome.ledger import ArtifactWritten, SourceGathered
from harness_core.engines.outcome.locales import en
from harness_core.engines.outcome.outcome import Completed, Delivered, Partial
from harness_core.engines.outcome.presenter import ANSWER_SUMMARY_CHARS, PRESENTER
from harness_core.engines.outcome.words import LOCALES, Words

PAGES = tuple(SourceGathered(f"https://docs.onyx.app/{n}", "fetch_url", f"https://docs.onyx.app/{n}", f"P{n}") for n in range(3))
PACKAGE = Path(__file__).resolve().parents[5] / "harness_core" / "engines" / "outcome"


def test_run01_d1_present_1_every_key_the_presenter_and_briefing_read_is_in_every_locale() -> None:
    keys = set(en.MESSAGES)
    for locale, messages in LOCALES.items():
        assert set(messages) == keys, locale
    used: set[str] = set()
    for module in ("presenter.py", "briefing.py"):
        for node in ast.walk(ast.parse((PACKAGE / module).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(("outcome.", "briefing.")):
                used.add(node.value)
    # A key built in an f-string names its family by prefix: some key of the catalog starts with it.
    exact = {key for key in used if not key.endswith(".")}
    assert exact and exact <= keys, exact - keys
    assert all(any(k.startswith(prefix) for k in keys) for prefix in used - exact)
    with pytest.raises(KeyError):
        Words()("outcome.no_such_key")


def test_run01_d1_present_2_nothing_gathered_offers_retry_and_says_nothing_was_saved() -> None:
    for reason in ("unfinished", "turns", "time"):
        outcome = Partial(reason, Delivered(), ("report.md",))  # type: ignore[arg-type]
        shown = PRESENTER.present(outcome)
        first = shown.answer.splitlines()[0]
        assert shown.action == "retry"
        assert first.startswith("**report.md isn't written yet.**") and "before it started on the work" in first
        assert "gathered nothing" in first and "saved" not in shown.answer and "gathering sources" not in shown.answer
        assert shown.answer.endswith('Send "continue" to try the task again.')
        assert PRESENTER.is_no_answer(shown.answer)


def test_run01_d1_present_3_gathered_work_says_it_is_saved_only_when_it_was() -> None:
    kept = PRESENTER.present(Partial("time", Delivered(sources=PAGES, kept=True), ("report.md",)))
    assert kept.action == "continue" and kept.status.startswith("This stopped early when its working time ran out")
    assert "**report.md isn't written yet.** This task stopped because it ran out of working time" in kept.answer
    assert "What it gathered: Fetch url (3 calls)." in kept.answer and "- [P0](https://docs.onyx.app/0)" in kept.answer
    assert "(notes/handoff.json lists it)" in kept.answer and 'Send "continue" to write report.md from it.' in kept.answer
    lost = PRESENTER.answer(Partial("time", Delivered(sources=PAGES[:1]), ("report.md",)))
    assert "Fetch url (1 call)" in lost and "could not be saved" in lost and "notes/handoff.json" not in lost
    assert lost.endswith('Send "continue" to try the task again.')


def test_run01_d1_present_4_the_engines_file_is_the_result_and_its_opening_is_the_answer() -> None:
    report = "# Onyx vs PipesHub\n\n## Executive summary\n\nOnyx documents its connectors.\n\n" + "## Finding\n\n" + "detail " * 2000
    written = ArtifactWritten("out/report.md", 7, len(report), "final", "engine")
    answer = PRESENTER.answer(Partial("turns", Delivered(written, PAGES, kept=True)), body=report)
    assert answer.startswith("# Onyx vs PipesHub") and len(answer) < ANSWER_SUMMARY_CHARS
    assert "The full report is in **report.md**." in answer and "**This result is partial.**" in answer
    assert "**report.md** holds what the task had written when it stopped" in answer
    saved = ArtifactWritten("out/report.md", 8, 900, "final", "wrap_up")
    assert "Saved so far: **report.md**, marked partial." in PRESENTER.answer(Partial("budget", Delivered(saved)), body="x")
    owed = PRESENTER.answer(Partial("unfinished", Delivered(saved, PAGES)), body="Report")
    assert owed.startswith("Report") and "**Written from what was gathered.**" in owed
    assert PRESENTER.answer(Completed(Delivered()), body=" done ") == "done"


def test_run01_d1_present_5_a_stop_with_no_answer_and_nothing_owed_says_how_to_carry_on() -> None:
    text = PRESENTER.answer(Partial("budget", Delivered()))
    assert text.startswith("This reply stopped early because it reached its spending limit") and PRESENTER.is_no_answer(text)
    listed = PRESENTER.no_answer("turns", ["jira", "jira", "web"])
    assert "- Jira (2 calls)" in listed and "- Web" in listed and listed.endswith('Send "continue" to carry on from here.')
    assert not PRESENTER.is_no_answer("# A report\n\nIt is done.")


def test_run01_d1_present_6_a_partial_files_banner_is_the_presenters_and_comes_off_again() -> None:
    synthesized = PRESENTER.partial_file("time", "Body", synthesized=True)
    assert synthesized.startswith("> **Partial**: written from the sources gathered before the task reached its working-time limit")
    assert PRESENTER.without_banner(synthesized) == "Body"
    assert "cut short when the task reached its working-time limit" in PRESENTER.banner("time", synthesized=False)

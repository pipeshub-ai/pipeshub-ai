"""The demo harness models who can see what, and scores every golden question.

It stands in for the connector's permissions when it uploads the fixture into
knowledge bases, and it is what the Build Pack questions are scored with. These
catch the ways it would quietly give a wrong answer: a restricted record landing
in the shared knowledge base, `--only` skipping a question and still passing, and
the installing admin being scored as if it were Alice.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

import app.connectors.sources.demo.connector as demo_connector
from app.connectors.sources.demo.harness import kb_harness

FIXTURE = Path(demo_connector.__file__).resolve().parent / "fixture" / "acme-corp.yaml"


@pytest.fixture(scope="module")
def fx() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _question(fx: dict, qid: str) -> dict:
    return next(q for q in kb_harness.all_questions(fx) if q["id"] == qid)


def test_every_lesson_group_is_restricted_and_no_team_group_is(fx: dict) -> None:
    closed = kb_harness.restricted_groups(fx)
    assert {"pricing-committee", "deal-desk", "launch-core", "payments-contract", "people-managers"} <= closed
    assert not closed & {"engineering-readers", "support-readers", "sales-readers", "people-readers"}


def test_every_restricted_record_stays_out_of_the_shared_knowledge_base(fx: dict) -> None:
    # Upload mode puts a record in the shared KB unless its group is restricted.
    records = {r["id"]: r for r in fx["records"]}
    closed = kb_harness.restricted_groups(fx)
    for q in kb_harness.all_questions(fx):
        for x in q.get("restricted", []):
            if x in records:
                assert kb_harness.group_of(records[x], fx) in closed, (q["id"], x)


def test_the_upload_models_each_persona_with_only_their_restricted_groups(fx: dict) -> None:
    alice = kb_harness.upload_groups(fx, "alice")
    bob = kb_harness.upload_groups(fx, "bob")
    assert alice == {"launch-core", "payments-contract"}
    assert bob == {"pricing-committee", "deal-desk", "people-managers"}


def test_every_question_is_asked_by_default(fx: dict) -> None:
    ids = [q["id"] for q in kb_harness.select_questions(fx, None)]
    packs = [q["id"] for qs in fx["pack_questions"].values() for q in qs]
    assert ids == [q["id"] for q in fx["questions"]] + packs


def test_only_selects_pack_questions(fx: dict) -> None:
    assert [q["id"] for q in kb_harness.select_questions(fx, {"s1", "q3"})] == ["q3", "s1"]


def test_only_with_an_unknown_id_fails_instead_of_passing_empty(fx: dict) -> None:
    with pytest.raises(SystemExit):
        kb_harness.select_questions(fx, {"s1", "nope"})


@pytest.mark.parametrize(
    ("qid", "installer", "alice"),
    [
        ("q1", "cites", "cites"),
        ("q5", "none", "none"),   # pricing committee
        ("s2", "none", "none"),   # deal desk
        ("m3", "none", "cites"),  # launch core: Alice yes, the installer no
        ("f3", "none", "cites"),  # payments contract: Alice yes, the installer no
        ("h3", "none", "none"),   # people managers
        ("s1", "cites", "cites"),
    ],
)
def test_the_installer_is_scored_on_its_own_groups_not_alices(fx: dict, qid: str, installer: str, alice: str) -> None:
    q = _question(fx, qid)
    assert kb_harness.expectation(q, "installer", fx) == installer
    assert kb_harness.expectation(q, "alice", fx) == alice


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Northwind is back on track; renewal expected on time.", True),
        ("Northwind is no longer at risk after the export fix.", True),
        ("Yes, Northwind is still at risk while they wait for the export fix.", False),
    ],
)
def test_s1_passes_only_an_answer_that_reports_the_recovery(fx: dict, answer: str, ok: bool) -> None:
    q = _question(fx, "s1")
    cited = {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}
    assert kb_harness.score(q, "cites", cited, answer)[0] is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Northwind is back on track; renewal expected on time.", True),
        ("Northwind is not on track for the renewal.", False),
        ("Northwind isn't on track yet.", False),
        ("Northwind won't renew on time.", False),
        ("Northwind's renewal is not on time.", False),
        ("Northwind is not at risk any more.", True),
    ],
)
def test_a_negated_recovery_phrase_does_not_count(fx: dict, answer: str, ok: bool) -> None:
    q = _question(fx, "s1")
    cited = {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}
    assert kb_harness.score(q, "cites", cited, answer)[0] is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("We launched on 21 April, a week after the fix shipped.", True),
        ("We launched background exports on Tuesday, Apr 21, 2026.", True),
        ("We launched on 14 April because PR #211 shipped.", False),
    ],
)
def test_m1_needs_the_launch_date_not_a_number_inside_a_pr_id(fx: dict, answer: str, ok: bool) -> None:
    q = _question(fx, "m1")
    assert kb_harness.score(q, "cites", {"drive-mkt-exports-launch-plan"}, answer)[0] is ok


@pytest.mark.parametrize(
    "answer",
    [
        "You can spend up to **$250** per purchase without approval.",
        "You don\u2019t need approval for purchases up to $250.",
        "Anything $250 or less: approval isn't required, just submit the receipt.",
        # The chat's own wording on the demo instance.
        "You can spend up to **$250 per purchase** without any approval.",
        "Up to $250 per purchase: no approval needed.",
        "You need no approval for purchases up to $250.",
        "You can spend up to $250 without approval. Above that, your manager approves.",
        "You can spend up to $250 without approval but your manager must approve more than that.",
        "Up to $250 per purchase: no approval needed. More than $250, up to $2,500: your manager approves.",
        "You can spend up to $250 with no approval, by Friday.",
        "Up to $250: no approval needed, from the expense policy.",
        "You can spend up to $250 without approval and your manager must approve amounts that exceed $250.",
        "You can spend up to $250 without approval and your manager approves purchases that exceed that.",
        "You can spend up to $250 without approval. Purchases that exceed $250 need your manager's approval.",
        "You can spend up to $250 without approval and your manager must approve amounts exceeding $250.",
        "You can spend up to $250 without approval and your manager approves purchases over $250.",
        "You can spend up to $250 with no approval, and above that your manager approves.",
        "You can spend up to $250 without approval. Your manager approves purchases above the $250 limit.",
        "You can spend up to $250 without approval and your manager must approve amounts that exceed the $250 limit.",
        "You can spend up to $250 without approval. Your manager approves purchases above that $250 limit.",
        "You can spend up to $250 without approval. Your manager approves purchases above this $250 limit.",
        "Up to $250 per purchase: no approval needed. Above that $250 limit your manager approves.",
        "You can spend up to $250 without approval. Your manager approves purchases above their $250 limit.",
        "For purchases up to $250, no approval is needed from your manager by submitting the receipt within 30 days.",
        "Up to $250: no approval needed from your manager by submitting the receipt within 30 days.",
        "For purchases up to $250, no approval is needed by your manager by submitting the receipt.",
        "You can spend up to $250 and no approval is needed from your manager by promptly submitting the receipt within 30 days.",
        "You can spend up to $250 with no approval and authorization is not needed.",
        "Purchases up to $250 need no approval; authorization isn't required.",
        "You can spend up to $250 without approval. Signing off is not required.",
        "Up to $250: no approval needed from your manager by submitting the receipt without signing off.",
        "Your manager approves purchases above $250. You can spend up to $250 with no approval.",
        "More than $250, up to $2,500: your manager approves. Up to $250: no approval needed.",
        "You can spend up to $250 with no approval and without your manager's sign-off.",
        "You can spend up to $250 with no approval and without the manager's sign-off.",
        "Up to $250. Your manager approves purchases above that. You can spend up to $250 with no approval.",
        "Up to $250, but your manager approves above that. You can spend up to $250 with no approval.",
        "Up to $250. Above that, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. Above $2,500, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. From $251 to $2,500, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. Between $251 and $2,500, your manager approves. You can spend up to $250 with no approval.",
        "Up to $250. Your manager's approval is required from $251 to $2,500. You can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval. From $251 to $2,500, your manager's approval is required.",
        "You can spend up to $250 with no approval. It is not rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Filing does not fail without your manager's sign-off.",
        "You can spend up to $250 with no approval. Expenses are not blocked without your manager's sign-off.",
        "You can spend up to $250 with no approval. We do not deny expenses without your manager's sign-off.",
        "You can spend up to $250 with no approval. A validation failure can be corrected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Your manager approves $251 to 2,500 purchases.",
        "You can spend up to $250 with no approval. Between $251 and $2,500, your manager approves.",
        "You can spend up to $250 with no approval. Your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager's approval is required, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves, between $251 and $2,500.",
        "You can spend up to $250 with no approval. Your manager approves from $251 to $2,500.",
        # A general rule and then its exception is a correct answer.
        "Purchases are rejected without your manager's sign-off. However, you can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval. Without your manager's sign-off, nothing above $2,500 goes through.",
        "You can spend up to $250 with no approval. Between $251 and $2,500 your manager approves.",
        "You can spend up to $250 with no approval. Your manager approves, and from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves, and between $251 and $2,500.",
        "You can spend up to $250 with no approval. From $251 to $2,500, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Between $251 and $2,500, expenses are blocked without your manager's sign-off.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off from $251 to $2,500.",
        "You can spend up to $250 with no approval. Above that, it is rejected without your manager's sign-off and nothing goes through.",
        "You can spend up to $250 with no approval. Above $2,500, it is rejected without your manager's sign-off and you can't submit it.",
        "You can spend up to $250 with no approval, except between $251 and $2,500.",
        "You can spend up to $250 with no approval. Above $2,500, without your manager's sign-off it is rejected.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off, above $2,500.",
        "You can spend up to $250 with no approval. Expenses are rejected without your manager's sign-off, above $2,500.",
        "More than $250, up to $2,500: your manager approves. Up to $250: no approval needed.",
        "You can spend up to $250 with no approval. From $251 up to $2,500, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Your manager approves, from $251 up to the $2,500 limit.",
        "You can spend up to $250 with no approval. Above that, your manager approves.",
        "You can spend up to $250 with no approval, or by Friday.",
        "You can spend up to $250 with no approval, or else.",
        "You can spend up to $250 with no approval, not above $250.",
        "You can spend up to $250 with no approval, not above that.",
        "You can spend up to $250 with no approval. Above $2,500 or more, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500 or above, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Over $2,500 or more, nothing goes through without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, it is rejected without your manager's sign-off, either you can't submit it.",
        "You can spend up to $250 with no approval, either by Friday.",
        "You can spend up to $250 with no approval, either from the expense policy.",
        "You can spend up to $250 with no approval, not greater than $250.",
        "You can spend up to $250 with no approval, not greater than that.",
        "You can spend up to $250 with no approval, not higher than $250.",
        "You can spend up to $250 with no approval, cannot exceed $250.",
        "You can spend up to $250 with no approval, nothing above $250.",
        "You can spend up to $250 with no approval, nothing above that.",
        "You can spend up to $250 with no approval, isn't greater than $250.",
        "You can spend up to $250 with no approval, isn't above $250.",
        "You can spend up to $250 with no approval, shall not exceed $250.",
        "You can spend up to $250 with no approval. Expenses are rejected without your manager's sign-off when above $2,500.",
        "You can spend up to $250 with no approval, not more than $250.",
        "You can spend up to $250 with no approval, up through $250.",
        "You can spend up to $250 with no approval, up until $250.",
        "You can spend up to $250 with no approval, did not exceed $250.",
        "You can spend up to $250 with no approval, hasn't exceeded $250.",
        "You can spend up to $250 with no approval, not in excess of $250.",
        "You can spend up to $250 with no approval. It isn't above $250.",
        "You can spend up to $250 with no approval, was not above $250.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off, from $251 to $2,500.",
        "You can spend up to $250 with no approval, never more than $250.",
        "You can spend up to $250 with no approval, not any more than $250.",
        "You can spend up to $250 with no approval, must not have exceeded $250.",
        "You can spend up to $250 with no approval, excluding amounts above $2,500.",
        "You can spend up to $250 with no approval, must not have any more than $250.",
        "You can spend up to $250 with no approval. Their total isn't above $250.",
        "You can spend up to $250 with no approval. The total is never above $250.",
        "You can spend up to $250 with no approval. The total has never been above $250.",
        "You can spend up to $250 with no approval. The total will never be above $250.",
        "You can spend up to $250 with no approval. The running total isn't above $250.",
        "You can spend up to $250 with no approval, apart from amounts above $2,500.",
        "You can spend up to $250 with no approval. Nothing is above $250.",
        "You can spend up to $250 with no approval, not to be any more than $250.",
        "You can spend up to $250 with no approval. Nothing will exceed $250.",
        "You can spend up to $250 with no approval. Nothing should be above $250.",
        "You can spend up to $250 with no approval. Nobody is above $250.",
        "You can spend up to $250 with no approval. The total will never exceed $250.",
        "You can spend up to $250 with no approval. The total is up to $250.",
        "You can spend up to $250 with no approval. Costs are under $250.",
        "You can spend up to $250 with no approval. The total never goes above $250.",
        "You can spend up to $250 with no approval. The total has never gone above $250.",
        "You can spend up to $250 with no approval. Expenses don't go above $250.",
        "You can spend up to $250 with no approval. The total isn't going to exceed $250.",
        "You can spend up to $250 with no approval, not much more than $250.",
        "You can spend up to $250 with no approval. No expense is above $250.",
        "You can spend up to $250 with no approval. The manager's total isn't above $250.",
        "You can spend up to $250 with no approval. The company's total has never been above $250.",
        "You can spend up to $250 with no approval. The average monthly total isn't above $250.",
        "You can spend up to $250 with no approval. Don't forget approval above $2,500.",
        "You can spend up to $250 with no approval. Not a single purchase is above $250.",
        "You can spend up to $250 with no approval. Not one expense is above $250.",
        "You can spend up to $250 with no approval. No other expense is above $250.",
        "You can spend up to $250 with no approval. The other total isn't above $250.",
        "You can spend up to $250 with no approval. However, the total is up to $250.",
        "You can spend up to $250 with no approval. The limit is $250.",
        "You can spend up to $250 with no approval. And yet, the total is up to $250.",
        "You can spend up to $250 with no approval. Whereas, the total is up to $250.",
        "You can spend up to $250 with no approval. So, the limit is $250.",
        "You can spend up to $250 with no approval. The limits are $250.",
        "You can spend up to $250 with no approval. The limit is at $250.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off for exceptional amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off exceptionally above $2,500.",
        "You can spend up to $250 with no approval. Your manager approves from $251 to $2,500.",
        "You can spend up to $250 with no approval. Above $2,500 or higher, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, it is rejected without your manager's sign-off or you can't submit it.",
        "You can spend up to $250 with no approval. Above that it is rejected without your manager's sign-off and nothing goes through.",
        "You can spend up to $250 with no approval. From $251 to $2,500 it is rejected without your manager's sign-off and you can't submit it.",
        "You can spend up to $250 with no approval, and your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. More than $250, your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. From $251 and $2,500, your manager's approval is required.",
        "You can spend up to $250 with no approval. Your manager approves between $251 and $2,500.",
        "You can spend up to $250 with no approval. Between $2,500 and $251, your manager's approval is required.",
        "You can spend up to $250 with no approval. Revenue is in decline without your manager's sign-off.",
        "You can spend up to $250 with no approval. Come to a full stop without your manager's sign-off.",
        "You can spend up to $250 with no approval. Put a block on the form without your manager's sign-off.",
        "You can spend up to $250 with no approval. The queue will halt without your manager's sign-off.",
        "You can spend up to $250 with no approval. It does not come to a halt without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above that, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Expenses are rejected without your manager's sign-off above $250.",
        "You can spend up to $250 with no approval. Your manager approves $251–$2,500 purchases.",
        "You can spend up to $250 with no approval, without your manager's sign-off.",
        "For purchases up to $250, no approval is needed.",
        "You can spend up to $250 without approval, and above $2,500 finance approves.",
        "You can spend up to $250 without approval and above that your manager approves.",
        "You can spend up to $250 with no approval from my manager.",
        "You can spend up to $250 without approval from your manager.",
        "You can spend up to $250 without your manager's approval.",
        "You can spend up to $250 without approval but your manager approves above that.",
        "For purchases up to $250, no approval is needed from your manager.",
        "Up to $250: no approval is needed from your manager.",
        "You can spend up to $250 and no approval is needed by your manager.",
        "You can spend up to $250 with no approval at all from your manager.",
        "Up to $250, no approval is needed and above that your manager approves.",
        "Purchases up to $250 need no approval and your manager approves amounts above that.",
        "You can spend up to $250 without approval, but your manager approves above that.",
        "You can spend up to $250 with no approval by submitting the receipt within 30 days.",
        "You can spend up to $250 and no approval is needed by submitting the receipt within 30 days.",
        "For purchases up to $250, no approval is needed from our manager.",
        "You can spend up to $250 with no approval from their manager.",
        "You can spend up to $250 and no approval is needed by her manager.",
        "You can spend up to $250 with no approval by the manager.",
        "Up to $250 per purchase: no approval needed; submit the receipt within 30 days.",
        "Up to $250 per purchase: no approval needed by submitting the receipt within 30 days.",
        "For purchases up to $250, no approval is needed by submitting the receipt within 30 days.",
        "You can spend up to $250 with no approval by promptly submitting the receipt within 30 days.",
        "Up to $250: no approval needed; your manager approves above that, up to $2,500.",
        "You can spend up to $250 without approval; above that, your manager approves.",
        "You can spend up to $250 without your manager\u2019s approval, and finance approves above $2,500.",
    ],
)
def test_f2_passes_the_amount_with_no_approval_however_the_chat_formats_it(fx: dict, answer: str) -> None:
    assert kb_harness.score(_question(fx, "f2"), "cites", {"drive-fin-expense-policy"}, answer)[0] is True


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Up to $250 per purchase needs no approval.", True),
        ("You can spend up to $2500 without your manager's approval.", False),
        ("$250 to $2,500 needs your manager's approval.", False),
        ("Anything above $250,000 needs finance approval.", False),
    ],
)
def test_f2_does_not_read_250_inside_2500(fx: dict, answer: str, ok: bool) -> None:
    q = _question(fx, "f2")
    assert kb_harness.score(q, "cites", {"drive-fin-expense-policy"}, answer)[0] is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Finance reissued Contoso's invoice on 22 April and fixed the VAT rule on 6 May.", True),
        ("Finance is reissuing Contoso's April invoice and issued a credit note.", True),
        ("Contoso got a credit note and a new invoice without VAT on the exempt line.", True),
        ("Contoso's invoice has not yet been reissued; the case is still open.", False),
        ("Contoso's invoice was not reissued.", False),
        ("Contoso reported it; SUP-121 remains open with finance.", False),
        ("Contoso's invoice has not been reissued.", False),
    ],
)
def test_u2_accepts_every_way_of_saying_it_was_reissued_but_not_still_open(fx: dict, answer: str, ok: bool) -> None:
    q = _question(fx, "u2")
    assert kb_harness.score(q, "cites", {"jira-fin-37", "jira-fin-38"}, answer)[0] is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Enterprise moves to a $48,000 annual platform fee.", True),
        ("A $48k platform fee covering 250 seats.", True),
    ],
)
def test_q5_still_accepts_the_pricing_documents_own_wording(fx: dict, answer: str, ok: bool) -> None:
    # The chat landing's questions are scored as on main: plain substrings.
    q = _question(fx, "q5")
    assert kb_harness.score(q, "cites", {"drive-pricing-2026"}, answer)[0] is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("You can now carry over five days of unused leave (it used to be three).", True),
        ("Up to 5 days carry over into 2027.", True),
        ("You can carry over three days, per the handbook.", False),
        ("It's three days, not five.", False),
        ("You can carry 25 days over.", False),
    ],
)
def test_h1_needs_the_current_five_day_limit(fx: dict, answer: str, ok: bool) -> None:
    q = _question(fx, "h1")
    assert kb_harness.score(q, "cites", {"slack-people-0302"}, answer)[0] is ok


def test_an_upload_run_asks_only_the_knowledge_bases_it_loaded() -> None:
    body = kb_harness.ask_body("What is the salary band?", "agent", ["kb-shared", "kb-launch"])
    assert body["filters"] == {"kb": ["kb-shared", "kb-launch"]}
    # Connector mode goes through the Demo connector's permissions, unscoped.
    assert "filters" not in kb_harness.ask_body("What is the salary band?", "agent")


def test_an_unknown_only_id_fails_before_logging_in_or_uploading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "bootstrap.env"
    env.write_text("PIPESHUB_ORIGIN=http://localhost:1\nPIPESHUB_ACCOUNT_EMAIL=a@b.c\nPIPESHUB_ACCOUNT_PASSWORD=x\n")

    def no_login(*_: object) -> str:
        raise AssertionError("logged in before checking --only")

    monkeypatch.setattr(kb_harness, "login", no_login)
    monkeypatch.setattr("sys.argv", ["kb_harness.py", "--env", str(env), "--fixture", str(FIXTURE), "--only", "s1,nope"])
    with pytest.raises(SystemExit, match="nope"):
        kb_harness.main()


@pytest.mark.parametrize(
    ("qid", "cited", "answer", "ok"),
    [
        # Negation up to three words back, and "no" counts.
        ("u2", {"jira-fin-37", "jira-fin-38"}, "Contoso's invoice has not yet been reissued.", False),
        ("u2", {"jira-fin-37", "jira-fin-38"}, "Contoso's invoice hasn't actually been reissued.", False),
        ("u2", {"jira-fin-37", "jira-fin-38"}, "Contoso's invoice has yet to be reissued.", False),
        ("u2", {"jira-fin-37", "jira-fin-38"}, "Contoso received no credit note.", False),
        ("s1", {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}, "Northwind is no longer on track.", False),
        ("s1", {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}, "Northwind is no longer on time.", False),
        ("s1", {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}, "Northwind is no longer at risk and is back on track.", True),
        ("h1", {"slack-people-0302"}, "The limit is no longer five days; it is three.", False),
        ("h1", {"slack-people-0302"}, "You cannot carry over five days; the handbook still says three.", False),
        # "and" keeps the clause, so the negation still reaches the phrase after it.
        ("s1", {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}, "Northwind is not healthy and on track for renewal.", False),
        ("u2", {"jira-fin-37", "jira-fin-38"}, "Contoso's invoice was not checked and reissued.", False),
        # A cap is a limit, not a denial; a minimum is not the cap.
        ("h1", {"slack-people-0302"}, "You can carry over no more than five days.", True),
        ("h1", {"slack-people-0302"}, "You cannot carry over more than five days.", True),
        ("h1", {"slack-people-0302"}, "You can carry over no less than five days.", False),
        ("h1", {"slack-people-0302"}, "You can carry over not fewer than five days.", False),
        # "no" further back doesn't cancel a stated limit.
        ("f2", {"drive-fin-expense-policy"}, "You need no approval for purchases up to $250.", True),
    ],
)
def test_negation_reaches_three_words_back_and_includes_no(
    fx: dict, qid: str, cited: set[str], answer: str, ok: bool
) -> None:
    assert kb_harness.score(_question(fx, qid), "cites", cited, answer)[0] is ok


@pytest.mark.parametrize(
    "answer",
    [
        "There is no approval above $2,500; your manager approves from $250.",
        "No approval is needed for a $5,000 purchase.",
        "Purchases without approval are not allowed above the manager band.",
        # The amount alone: the manager is the one approving it.
        "Purchases of up to $250 require your manager's approval.",
        "Your manager must approve every purchase up to $250.",
        # $250 itself needs no approval, so "under" gets the edge wrong.
        "Purchases under $250 need no approval.",
        # Both facts, but about different amounts.
        "Purchases of up to $250 require your manager's approval. There is no approval above $2,500.",
        "Your manager must approve every purchase up to $250. No approval is needed above $5,000.",
        "Up to $250: your manager's approval is required. Finance needs no approval above $2,500.",
        # Both in one sentence, but in clauses about different amounts.
        "Purchases of up to $250 require your manager's approval; there is no approval above $2,500.",
        "Purchases of up to $250 require your manager's approval, and there is no approval above $2,500.",
        "Up to $250 needs your manager's approval, but no approval is needed above $2,500.",
        "Purchases up to $250,000, no approval.",
        # "no approval" is nearest a different amount, however the clauses are joined.
        "Purchases of up to $250 require your manager's approval and there is no approval above $2,500.",
        "Up to $250, your manager approves, and there is no approval above $2,500.",
        "Up to $250: your manager's approval is required and there is no approval above $2,500.",
        "Purchases of up to $250 must be approved by your manager; there is no approval above $2,500.",
        "Purchases of up to $250 have to be signed off by your manager, and there is no approval above $2,500.",
        "The policy lists up to $250, and there is no approval above $2,500.",
        # A higher band by name, or another amount in the next part.
        "Purchases of up to $250 require your manager's approval and there is no approval above that.",
        "The policy lists up to $250, and there is no approval above that.",
        "Up to $250: your manager's approval is required and there is no approval above that.",
        "Up to $250, no approval above $2,500.",
        "For purchases up to $250, no approval above $2,500.",
        "Up to $250, approval isn't needed above $2,500.",
        # The higher band named before the phrase.
        "Up to $250, above that there is no approval.",
        "Up to $250, and above that no approval is needed.",
        "For purchases up to $250, amounts above that need no approval.",
        "Purchases of up to $250 require your manager's approval and above that there is no approval.",
        "Up to $250, more than that needs no approval.",
        "For purchases up to $250; above that no approval is needed.",
        # Other words for a higher band, and a next part about something else.
        "Up to $250, higher than that there is no approval.",
        "Up to $250, anything higher needs no approval.",
        "Up to $250 and higher, no approval is needed.",
        "Purchases of up to $250 require your manager's approval and higher than that there is no approval.",
        "Purchases of up to $250 require your manager's approval, and no approval is needed for larger amounts.",
        "Up to $250, higher amounts need no approval.",
        "For purchases up to $250, amounts past that need no approval.",
        "Purchases of up to $250 require your manager's approval, with no approval from finance.",
        # The $250 part says the manager approves, or "no approval" is someone else's.
        "Purchases of up to $250 require your manager's approval with no approval from finance.",
        "Purchases of up to $250 require your manager's approval and no approval is needed.",
        "Purchases of up to $250 require your manager's approval; no approval is needed.",
        "Purchases of up to $250 require your manager's approval: no approval is needed.",
        "Up to $250 with no approval from finance.",
        # Other ways of saying the $250 purchase is approved, or another approver.
        "Purchases of up to $250 must be approved, no approval is needed.",
        "Purchases of up to $250 need to be approved, no approval is needed.",
        "Purchases of up to $250 have to be approved, no approval is needed.",
        "Purchases of up to $250 require your manager to approve them, no approval is needed.",
        "Purchases of up to $250 are approved only by your manager, no approval is needed.",
        "Purchases of up to $250 require your manager's sign-off, no approval is needed.",
        "Up to $250 and your manager's approval is required and no approval is needed.",
        "You can spend up to $250 and no approval is needed from finance.",
        "Up to $250 with no approval needed from finance.",
        "No approval is needed from finance for purchases up to $250.",
        # Someone approves later in the sentence, or "by finance" / "at all from finance".
        "You can spend up to $250 with no approval and your manager must approve every purchase.",
        "Purchases of up to $250 need no approval and your manager must approve them.",
        "You can spend up to $250 without approval; your manager must approve every one of them.",
        "You can spend up to $250 with no approval and your manager must approve it.",
        "You can spend up to $250 without approval but your manager approves.",
        "You can spend up to $250 and no approval is needed by finance.",
        "No approval is needed by finance for purchases up to $250.",
        "You can spend up to $250 with no approval at all from finance.",
        "Anything $250 or less needs no approval by finance.",
        # A later band word doesn't clear an earlier approval; "over time" isn't a band.
        "You can spend up to $250 with no approval and your manager must approve every purchase and above that finance approves.",
        "Purchases of up to $250 need no approval and your manager must approve them and above that finance approves.",
        "You can spend up to $250 with no approval and your manager must sign off and above that finance approves.",
        "You can spend up to $250 without approval but your manager approves over time.",
        "You can spend up to $250 with no approval required by finance.",
        "You can spend up to $250 with no approval by the finance team.",
        # Another approver or a contradiction in a later piece or sentence; "above" as an ordinary word.
        "You can spend up to $250 with no approval, by finance.",
        "You can spend up to $250 with no approval, from finance.",
        "You can spend up to $250 with no approval and by finance.",
        "You can spend up to $250 with no approval and from the finance team.",
        "You can spend up to $250 with no approval and your manager must approve it as stated above.",
        "You can spend up to $250 without approval but your manager approves as stated above.",
        "You can spend up to $250 with no approval and your manager must approve it above all.",
        "You can spend up to $250 with no approval and your manager must approve it as noted above.",
        "You can spend up to $250 with no approval and your manager must approve it for a larger team.",
        "You can spend up to $250 without approval. Your manager must approve every purchase.",
        "You can spend up to $250 without approval. But your manager must approve every one of them.",
        "Up to $250: no approval needed. Your manager must approve every purchase.",
        # Band words need an amount; a band ends with its sentence or when $250 returns.
        "You can spend up to $250 with no approval and your manager must approve it more than once.",
        "You can spend up to $250 without approval but your manager approves beyond question.",
        "You can spend up to $250 with no approval and your manager must approve it exceedingly carefully.",
        "You can spend up to $250 without approval. More than once, your manager must approve every purchase.",
        "You can spend up to $250 without approval. We have more than one rule. Your manager must approve every purchase.",
        "You can spend up to $250 without approval. Above that, finance approves. Your manager must approve every purchase.",
        "You can spend up to $250 with no approval, above that your manager approves and your manager must approve the $250 purchase.",
        "You can spend up to $250 with no approval, by finance above that.",
        "You can spend up to $250 with no approval by signing off.",
        # $250 again after a band word, and a band word later in the phrase's own piece.
        "You can spend up to $250 without approval. Above that your manager must approve the $250 purchase.",
        "You can spend up to $250 with no approval and above that your manager must approve the $250 purchase.",
        "You can spend up to $250 without approval. Above $250 your manager approves the $250 purchases as well.",
        "You can spend up to $250 with no approval, above that your manager must approve the $250 purchase.",
        "You can spend up to $250 with no approval and your manager must approve the $250 purchase above that.",
        "You can spend up to $250 with no approval above $2,500.",
        "Purchases of up to $250 need no approval above $2,500.",
        "You can spend up to $250 without approval above that.",
        # The band with a determiner, and $250 again later in the sentence, by value.
        "You can spend up to $250 with no approval above the $250 limit.",
        "You can spend up to $250 without approval for amounts that exceed the $250 limit.",
        "You can spend up to $250 with no approval on amounts over the $250 limit.",
        "You can spend up to $250 without approval. Above that your manager must approve it, including the $250 purchase.",
        "You can spend up to $250 with no approval and above that your manager must approve it, including every $250 purchase.",
        "You can spend up to $250 without approval. Above that your manager must approve the 250 dollar purchase.",
        "You can spend up to 250 dollars without approval. Above that your manager must approve the $250 purchase.",
        "You can spend up to $250 without approval. Above that your manager must approve the $250.00 purchase.",
        # A filing phrase that still has the manager approving; $250 again after an earlier approval.
        "For purchases up to $250, no approval is needed by submitting it for your manager to approve.",
        "Up to $250: no approval needed by submitting the receipt for your manager to approve.",
        "Up to $250 per purchase: no approval needed by getting your manager to approve it.",
        "You can spend up to $250 with no approval and signing off.",
        "You can spend up to $250 with no approval, signing off is required.",
        "You can spend up to $250 with no approval by authorizing the purchase.",
        "You can spend up to $250 with no approval by authorising the purchase.",
        "Up to $250: no approval needed from your manager by submitting the receipt and signing off.",
        # An earlier sentence or part has the manager approve the $250 purchase; other sign-off forms.
        "Your manager must approve every purchase up to $250. You can spend up to $250 with no approval.",
        "Up to $250: your manager's approval is required. Up to $250: no approval needed.",
        "Your manager must approve every purchase up to $250, but you can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval and your manager must sign it off.",
        "You can spend up to $250 with no approval and signing it off is required.",
        "You can spend up to $250 with no approval and your manager's sign-offs are required.",
        "You can spend up to $250 with no approval and your manager must sign the purchase off.",
        "Your manager must sign the purchase off when it is up to $250. You can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval and your manager must sign the receipt off.",
        "You can spend up to $250 with no approval and your manager must sign this purchase off.",
        "Up to $250. Your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. Your manager's approval is required, but you can spend up to $250 with no approval.",
        "The band is up to $250. Manager approval is required. Purchases up to $250 need no approval.",
        "Up to $250, but your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. However, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. Please note: your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. For those, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. However, your manager must sign the purchase off. You can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval and your manager must sign the $250 purchase off.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off.",
        "Purchases up to $250 need no approval, but nothing goes through without your manager's sign-off.",
        "Purchases up to $250 need no approval. Without your manager's sign-off, nothing goes through.",
        "You can spend up to $250 with no approval. Without your manager's sign-off you can't submit it.",
        "You can spend up to $250 with no approval. You can't submit it, without your manager's sign-off.",
        "Purchases up to $250 need no approval. No purchase goes through without your manager's sign-off.",
        "Up to $250. However, your manager's approval is required and the cap is $2,500. You can spend up to $250 with no approval.",
        "Up to $250. However, compared with $2,500, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. However, compared to $2,500, your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. Purchases under $2,500 need your manager's approval. You can spend up to $250 with no approval.",
        "Up to $250. Within 30 days your manager's approval is required. You can spend up to $250 with no approval.",
        "Up to $250. Less than $2,500, your manager's approval is required. You can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Expenses are blocked without your manager's sign-off.",
        "You can spend up to $250 with no approval. Filing fails without your manager's sign-off.",
        "You can spend up to $250 with no approval. Do not file it without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $250 to $2,500, your manager's approval is required.",
        "Up to $250: no approval needed. Manager approval runs from $250 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves purchases of $250 to $500.",
        "You can spend up to $250 with no approval. Between $2,500 and $200, your manager's approval is required.",
        "Up to $250. Send the receipt for $250 to 1 approver. You can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval and your manager must approve the $250 purchase from $251 to $2,500.",
        "You can spend up to $250 with no approval. From $250, your manager's approval is required.",
        "Purchases up to $250 need no approval. Your manager's approval is required from $250 on.",
        "You can spend up to $250 with no approval. Starting from $250, your manager approves.",
        "Your manager must approve the $250 purchase from $251 to $2,500. You can spend up to $250 with no approval.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off, but above $2,500 finance approves.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off and above $2,500 finance approves.",
        "You can spend up to $250 with no approval. Above $2,500, finance approves and expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, finance approves, expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above that, finance approves, expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500, finance approves, expenses are rejected without your manager's sign-off.",
        "Purchases up to $250 need no approval, between $251 and $2,500.",
        "You can spend up to $250 with no approval: between $251 and $2,500.",
        "You can spend up to $250 with no approval, from $251 to $2,500.",
        "You can spend up to $250 with no approval, between $251 and $2,500.",
        "You can spend up to $250 with no approval, from $250 to $2,500.",
        "Purchases up to $250 need no approval, from $251 to $2,500.",
        "You can spend up to $250 with no approval and from $251 to $2,500.",
        "Up to $250: no approval needed, from $251 to $2,500.",
        "You can spend up to $250 with no approval. The $250 purchase, your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. The $250 purchase, your manager's approval is required, from $251 to $2,500.",
        "You can spend up to $250 with no approval. The $250.00 purchase, your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. The 250 dollar purchase, your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. The $250 purchase and your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. For $250, your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Up to $250, your manager approves, from $251 to $2,500.",
        "You can spend up to $250 with no approval, only from $251 to $2,500.",
        "You can spend up to $250 with no approval, for purchases between $251 and $2,500.",
        "Purchases up to $250 need no approval, but only between $251 and $2,500.",
        "You can spend up to $250 with no approval. Above $2,500, finance approves expenses rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, finance approves expenses that are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above that, finance approves expenses rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500, finance approves expenses rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 finance approves expenses rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, with finance approval expenses are rejected without your manager's sign-off.",
        "Purchases up to $250 need no approval from $251 up to $2,500.",
        "You can spend up to $250 with no approval, from $251 up to $2,500.",
        "You can spend up to $250 with no approval and from $251 up to $2,500.",
        "Up to $250: no approval needed, from $251 up to $2,500.",
        "You can spend up to $250 with no approval, however from $251 to $2,500.",
        "You can spend up to $250 with no approval, however, from $251 to $2,500.",
        "You can spend up to $250 with no approval, whereas between $251 and $2,500.",
        "You can spend up to $250 to $1,000 with no approval.",
        "Employees may spend up to $250 to $2,500 with no approval.",
        "You can spend up to $250 with no approval. Only from $251 to $2,500.",
        "You can spend up to $250 with no approval. For purchases between $251 and $2,500.",
        "Purchases up to $250 need no approval. But only between $251 and $2,500.",
        "You can spend up to $250 with no approval, which is from $251 to $2,500.",
        "You can spend up to $250 with no approval, that is from $251 to $2,500.",
        "You can spend up to $250 with no approval, from $251 up to the $2,500 limit.",
        "You can spend up to $250 with no approval from $251 up to a $2,500 cap.",
        "You can spend up to $250 with no approval, between $251 and the $2,500 limit.",
        "You can spend up to $250 with no approval, above $2,500.",
        "You can spend up to $250 with no approval, above that.",
        "You can spend up to $250 with no approval. Above $2,500.",
        "You can spend up to $250 with no approval. Above that.",
        "You can spend up to $250 with no approval, over $2,500.",
        "You can spend up to $250 with no approval, more than $2,500.",
        "You can spend up to $250 with no approval, beyond $2,500.",
        "You can spend up to $250 with no approval up to $2,500.",
        "You can spend up to $250 with no approval, up to $2,500.",
        "You can spend up to $250 with no approval to $2,500.",
        "You can spend up to $250 with no approval under $2,500.",
        "You can spend up to $250 with no approval below $2,500.",
        "You can spend up to $250 with no approval through $2,500.",
        "You can spend up to $250 with no approval until $2,500.",
        "You can spend up to $250 with no approval within $2,500.",
        "Purchases up to $250 need no approval for anything less than $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off and above $2,500.",
        "You can spend up to $250 with no approval. Expenses are blocked without your manager's sign-off and above $2,500.",
        "You can spend up to $250 with no approval. Filing fails without your manager's sign-off and above $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through without your manager's sign-off and above $2,500.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off and above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off and from $251 to $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off and between $251 and $2,500.",
        "You can spend up to $250 with no approval, or above $2,500.",
        "You can spend up to $250 with no approval, or up to $2,500.",
        "You can spend up to $250 with no approval, or from $251 to $2,500.",
        "You can spend up to $250 with no approval. Or above $2,500.",
        "You can spend up to $250 with no approval, not more than $2,500.",
        "You can spend up to $250 with no approval, no more than $2,500.",
        "You can spend up to $250 with no approval. Not more than $2,500.",
        "You can spend up to $250 with no approval, maximum $2,500.",
        "You can spend up to $250 with no approval. Maximum $2,500.",
        "Up to $250: no approval needed, maximum $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off or above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off or from $251 to $2,500.",
        "You can spend up to $250 with no approval. Expenses are blocked without your manager's sign-off or between $251 and $2,500.",
        "You can spend up to $250 with no approval. You can't file an expense without your manager's sign-off or above $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through without your manager's sign-off or above $2,500.",
        "You can spend up to $250 with no approval. Filing fails without your manager's sign-off or above $2,500 finance approves.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off, and above $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through without your manager's sign-off, and above $2,500.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off, and above $2,500.",
        "You can spend up to $250 with no approval but your manager approves: from $251 to $2,500.",
        "You can spend up to $250 with no approval but your manager approves; from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves, or from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager's approval is required, or from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves, or above $2,500.",
        "You can spend up to $250 with no approval. Your manager approves, or above that.",
        "You can spend up to $250 with no approval, or else above $2,500.",
        "You can spend up to $250 with no approval, or else up to $2,500.",
        "You can spend up to $250 with no approval. Or else above $2,500.",
        "Purchases up to $250 need no approval, or else up to $2,500.",
        "You can spend up to $250 with no approval, not above $2,500.",
        "You can spend up to $250 with no approval, not over $2,500.",
        "You can spend up to $250 with no approval, not beyond $2,500.",
        "You can spend up to $250 with no approval, not past $2,500.",
        "You can spend up to $250 with no approval. Not above $2,500.",
        "You can spend up to $250 with no approval. Above $2,500 or it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, or it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 or expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, or you can't submit it without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500 or it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500, or nothing goes through without your manager's sign-off.",
        "You can spend up to $250 with no approval, at most $2,500.",
        "You can spend up to $250 with no approval. At most $2,500.",
        "Up to $250: no approval needed, at most $2,500.",
        "Purchases up to $250 need no approval for at most $2,500.",
        "You can spend up to $250 with no approval, at most the $2,500 limit.",
        "You can spend up to $250 with no approval, capped at $2,500.",
        "You can spend up to $250 with no approval. Capped at $2,500.",
        "You can spend up to $250 with no approval, not to exceed $2,500.",
        "You can spend up to $250 with no approval, from $251 until $2,500.",
        "You can spend up to $250 with no approval, either from $251 to $2,500.",
        "You can spend up to $250 with no approval, either above $2,500.",
        "You can spend up to $250 with no approval. Either above $2,500.",
        "You can spend up to $250 with no approval. Above $2,500, either it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 either it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, either expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500, either you can't submit it without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500, either nothing goes through without your manager's sign-off.",
        "You can spend up to $250 with no approval. From $251 to $2,500 either it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval, either up to $2,500.",
        "Purchases up to $250 need no approval, either from $251 to $2,500.",
        "You can spend up to $250 with no approval, either between $251 and $2,500.",
        "You can spend up to $250 with no approval, not greater than $2,500.",
        "You can spend up to $250 with no approval, not higher than $2,500.",
        "You can spend up to $250 with no approval, not larger than $2,500.",
        "You can spend up to $250 with no approval, not bigger than $2,500.",
        "You can spend up to $250 with no approval, no greater than $2,500.",
        "You can spend up to $250 with no approval. Not greater than $2,500.",
        "You can spend up to $250 with no approval, cannot exceed $2,500.",
        "You can spend up to $250 with no approval. Cannot exceed $2,500.",
        "You can spend up to $250 with no approval, does not exceed $2,500.",
        "You can spend up to $250 with no approval. Can't exceed $2,500.",
        "You can spend up to $250 with no approval, nothing more than $2,500.",
        "You can spend up to $250 with no approval, at a maximum of $2,500.",
        "You can spend up to $250 with no approval, from $251 up until $2,500.",
        "You can spend up to $250 with no approval, $251 until $2,500.",
        "You can spend up to $250 with no approval, $251 through $2,500.",
        "You can spend up to $250 with no approval, nothing above $2,500.",
        "You can spend up to $250 with no approval, nothing over $2,500.",
        "You can spend up to $250 with no approval, nothing beyond $2,500.",
        "You can spend up to $250 with no approval, nothing past $2,500.",
        "You can spend up to $250 with no approval. Nothing above $2,500.",
        "You can spend up to $250 with no approval, nothing exceeding $2,500.",
        "You can spend up to $250 with no approval. Nothing over the $2,500 limit.",
        "You can spend up to $250 with no approval, isn't greater than $2,500.",
        "You can spend up to $250 with no approval, isn't above $2,500.",
        "You can spend up to $250 with no approval. Isn't greater than $2,500.",
        "You can spend up to $250 with no approval, aren't higher than $2,500.",
        "You can spend up to $250 with no approval, shall not exceed $2,500.",
        "You can spend up to $250 with no approval, from $251 up through $2,500.",
        "You can spend up to $250 with no approval, $251 up through $2,500.",
        "You can spend up to $250 with no approval, $251 up through the $2,500 limit.",
        "You can spend up to $250 with no approval, from $251 up through the $2,500 cap.",
        "You can spend up to $250 with no approval. It is rejected or higher than $2,500 without your manager's sign-off.",
        "You can spend up to $250 with no approval. Nothing goes through or higher without your manager's sign-off.",
        "You can spend up to $250 with no approval. Expenses are blocked or larger than $2,500 without your manager's sign-off.",
        "You can spend up to $250 with no approval. Filing fails or bigger than $2,500 without your manager's sign-off.",
        "You can spend up to $250 with no approval. You can't submit it or greater than $2,500 without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 or more documentation, it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Expenses are rejected without your manager's sign-off when not above $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through without your manager's sign-off for no more than $2,500.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off if it isn't greater than $2,500.",
        "You can spend up to $250 with no approval. Filing fails without your manager's sign-off when it shall not exceed $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off except above $2,500.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off unless it is above $2,500.",
        "You can spend up to $250 with no approval, mustn't exceed $2,500.",
        "You can spend up to $250 with no approval, shouldn't exceed $2,500.",
        "You can spend up to $250 with no approval, must not be more than $2,500.",
        "You can spend up to $250 with no approval. Mustn't exceed $2,500.",
        "You can spend up to $250 with no approval, up through $2,500.",
        "You can spend up to $250 with no approval. Up through $2,500.",
        "You can spend up to $250 with no approval, up until $2,500.",
        "You can spend up to $250 with no approval. Up until $2,500.",
        "You can spend up to $250 with no approval, did not exceed $2,500.",
        "You can spend up to $250 with no approval, hasn't exceeded $2,500.",
        "You can spend up to $250 with no approval, has not exceeded $2,500.",
        "You can spend up to $250 with no approval, not in excess of $2,500.",
        "You can spend up to $250 with no approval. It isn't above $2,500.",
        "You can spend up to $250 with no approval. This isn't greater than $2,500.",
        "You can spend up to $250 with no approval, was not above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off except from $251 to $2,500.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off unless between $251 and $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through without your manager's sign-off except between $251 and $2,500.",
        "You can spend up to $250 with no approval. Expenses are blocked without your manager's sign-off except between $251 and $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off unless from $251 to $2,500.",
        "You can spend up to $250 with no approval. You can't submit it without your manager's sign-off unless it is from $251 to $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through without your manager's sign-off except between $251 and the $2,500 limit.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off except for purchases from $251 to $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off with the exception of amounts above $2,500.",
        "You can spend up to $250 with no approval, never more than $2,500.",
        "You can spend up to $250 with no approval, never above $2,500.",
        "You can spend up to $250 with no approval, never exceed $2,500.",
        "You can spend up to $250 with no approval, never greater than $2,500.",
        "You can spend up to $250 with no approval, never to exceed $2,500.",
        "You can spend up to $250 with no approval, never in excess of $2,500.",
        "You can spend up to $250 with no approval. Never more than $2,500.",
        "You can spend up to $250 with no approval, not any more than $2,500.",
        "You can spend up to $250 with no approval, not any higher than $2,500.",
        "You can spend up to $250 with no approval, not any more than the $2,500 limit.",
        "You can spend up to $250 with no approval, must not have exceeded $2,500.",
        "You can spend up to $250 with no approval, cannot have exceeded $2,500.",
        "You can spend up to $250 with no approval, should not have exceeded $2,500.",
        "You can spend up to $250 with no approval, must not have been above $2,500.",
        "You can spend up to $250 with no approval. The total isn't above $2,500.",
        "You can spend up to $250 with no approval. The cost was not above $2,500.",
        "You can spend up to $250 with no approval. Costs aren't above $2,500.",
        "You can spend up to $250 with no approval, not above their $2,500 limit.",
        "You can spend up to $250 with no approval, isn't above their $2,500 limit.",
        "You can spend up to $250 with no approval, under their $2,500 limit.",
        "You can spend up to $250 with no approval, up to their $2,500 limit.",
        "You can spend up to $250 with no approval, no more than their $2,500 limit.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off when not above their $2,500 limit.",
        "You can spend up to $250 with no approval. Expenses are rejected without your manager's sign-off when not above this $2,500 cap.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off excluding amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off barring amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off besides amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off save for amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off excepting amounts above $2,500.",
        "You can spend up to $250 with no approval, must not have any more than $2,500.",
        "You can spend up to $250 with no approval, should not be any higher than $2,500.",
        "You can spend up to $250 with no approval, must not have been any higher than $2,500.",
        "You can spend up to $250 with no approval, has not been any higher than $2,500.",
        "You can spend up to $250 with no approval, never have any more than $2,500.",
        "You can spend up to $250 with no approval, never been any higher than $2,500.",
        "You can spend up to $250 with no approval. Their total isn't above $2,500.",
        "You can spend up to $250 with no approval. His cost was not above $2,500.",
        "You can spend up to $250 with no approval. This total isn't above $2,500.",
        "You can spend up to $250 with no approval. The total is never above $2,500.",
        "You can spend up to $250 with no approval. Costs are never above $2,500.",
        "You can spend up to $250 with no approval. The total is no more than $2,500.",
        "You can spend up to $250 with no approval. Costs are no more than $2,500.",
        "You can spend up to $250 with no approval. The total has never been above $2,500.",
        "You can spend up to $250 with no approval. Costs have never exceeded $2,500.",
        "You can spend up to $250 with no approval. The total will never be above $2,500.",
        "You can spend up to $250 with no approval. Purchases can never exceed $2,500.",
        "You can spend up to $250 with no approval. It has never been above $2,500.",
        "You can spend up to $250 with no approval. The running total isn't above $2,500.",
        "You can spend up to $250 with no approval. Their annual total isn't above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off apart from amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off aside from amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off other than amounts above $2,500.",
        "You can spend up to $250 with no approval. It is rejected without your manager's sign-off outside of amounts above $2,500.",
        "You can spend up to $250 with no approval. Nothing is above $2,500.",
        "You can spend up to $250 with no approval. Nothing was more than $2,500.",
        "You can spend up to $250 with no approval. Nothing is to exceed $2,500.",
        "You can spend up to $250 with no approval. Nothing has ever been above $2,500.",
        "You can spend up to $250 with no approval. Nothing has ever exceeded $2,500.",
        "You can spend up to $250 with no approval, not to be any more than $2,500.",
        "You can spend up to $250 with no approval, not to be any higher than $2,500.",
        "You can spend up to $250 with no approval. His cost has never been above $2,500.",
        "You can spend up to $250 with no approval. The total should never exceed $2,500.",
        "You can spend up to $250 with no approval. The total could never be above $2,500.",
        "You can spend up to $250 with no approval. Nothing will exceed $2,500.",
        "You can spend up to $250 with no approval. Nothing will be above $2,500.",
        "You can spend up to $250 with no approval. Nothing should be above $2,500.",
        "You can spend up to $250 with no approval. Nothing can ever be more than $2,500.",
        "You can spend up to $250 with no approval. Nothing could be more than $2,500.",
        "You can spend up to $250 with no approval. Nothing shall exceed $2,500.",
        "You can spend up to $250 with no approval. Nothing may exceed $2,500.",
        "You can spend up to $250 with no approval. Nothing might be above $2,500.",
        "You can spend up to $250 with no approval. Nothing would exceed $2,500.",
        "You can spend up to $250 with no approval. Nobody is above $2,500.",
        "You can spend up to $250 with no approval. No one is above $2,500.",
        "You can spend up to $250 with no approval. The total is up to $2,500.",
        "You can spend up to $250 with no approval. Costs are under $2,500.",
        "You can spend up to $250 with no approval. The total is at most $2,500.",
        "You can spend up to $250 with no approval. The total never goes above $2,500.",
        "You can spend up to $250 with no approval. The total has never gone above $2,500.",
        "You can spend up to $250 with no approval. Expenses don't go above $2,500.",
        "You can spend up to $250 with no approval. The total isn't going to exceed $2,500.",
        "You can spend up to $250 with no approval, not much more than $2,500.",
        "You can spend up to $250 with no approval. No expense is above $2,500.",
        "You can spend up to $250 with no approval. The manager's total isn't above $2,500.",
        "You can spend up to $250 with no approval. The company's total has never been above $2,500.",
        "You can spend up to $250 with no approval. The average monthly total isn't above $2,500.",
        "You can spend up to $250 with no approval. Not a single purchase is above $2,500.",
        "You can spend up to $250 with no approval. Not a single expense is above $2,500.",
        "You can spend up to $250 with no approval. Not one expense is above $2,500.",
        "You can spend up to $250 with no approval. No other expense is above $2,500.",
        "You can spend up to $250 with no approval. The other total isn't above $2,500.",
        "You can spend up to $250 with no approval. However, the total is up to $2,500.",
        "You can spend up to $250 with no approval. However, costs are under $2,500.",
        "You can spend up to $250 with no approval. But the total is up to $2,500.",
        "You can spend up to $250 with no approval. However, the total isn't above $2,500.",
        "You can spend up to $250 with no approval. However, the total is never above $2,500.",
        "You can spend up to $250 with no approval. The limit is $2,500.",
        "You can spend up to $250 with no approval. The cap is $2,500.",
        "You can spend up to $250 with no approval. The maximum is $2,500.",
        "You can spend up to $250 with no approval. The ceiling is $2,500.",
        "You can spend up to $250 with no approval. The threshold is $2,500.",
        "You can spend up to $250 with no approval. Its limit is $2,500.",
        "You can spend up to $250 with no approval. The limit was $2,500.",
        "You can spend up to $250 with no approval. Don't forget the limit is $2,500.",
        "You can spend up to $250 with no approval. And yet, the total is up to $2,500.",
        "You can spend up to $250 with no approval. But still, the total is up to $2,500.",
        "You can spend up to $250 with no approval. However, still, the total is up to $2,500.",
        "You can spend up to $250 with no approval. And yet, costs are under $2,500.",
        "You can spend up to $250 with no approval. Whereas, the total is up to $2,500.",
        "You can spend up to $250 with no approval. Whereas, the total isn't above $2,500.",
        "You can spend up to $250 with no approval. Whereas, costs are under $2,500.",
        "You can spend up to $250 with no approval. So, the limit is $2,500.",
        "You can spend up to $250 with no approval. Still, the cap is $2,500.",
        "You can spend up to $250 with no approval. Also, the ceiling is $2,500.",
        "You can spend up to $250 with no approval. Yet, the limit is $2,500.",
        "You can spend up to $250 with no approval. Still the maximum is $2,500.",
        "You can spend up to $250 with no approval. The limits are $2,500.",
        "You can spend up to $250 with no approval. Caps are $2,500.",
        "You can spend up to $250 with no approval. The ceilings are $2,500.",
        "You can spend up to $250 with no approval. The thresholds were $2,500.",
        "You can spend up to $250 with no approval. Maximums are $2,500.",
        "You can spend up to $250 with no approval. The limit is at $2,500.",
        "You can spend up to $250 with no approval. The cap was at $2,500.",
        "You can spend up to $250 with no approval. The upper limit is $2,500.",
        "You can spend up to $250 with no approval. The hard cap is $2,500.",
        "You can spend up to $250 with no approval. The maximum allowed is $2,500.",
        "You can spend up to $250 with no approval. The annual total is up to $2,500.",
        "You can spend up to $250 with no approval. Costs remain below $2,500.",
        "You can spend up to $250 with no approval. Spending is limited to $2,500.",
        "You can spend up to $250 with no approval. The company's average monthly total isn't above $2,500.",
        "You can spend up to $250 with no approval. Up to $2,500: no approval needed.",
        "You can spend up to $250 with no approval. The limit is $2,500 with no approval.",
        "You can spend up to $250 with no approval. Purchases up to $2,500 need no approval.",
        "You can spend up to $250 with no approval. The limit is $2,500 and approval is not required.",
        "You can spend up to $250 with no approval. Spending is limited to $2,500 and approval isn't required.",
        "You can spend up to $250 with no approval. Besides, the limit is $2,500.",
        "You can spend up to $250 with no approval. Unless I'm wrong, the limit is $2,500.",
        "You can spend up to $250 with no approval. Other than that, the limit is $2,500.",
        "You can spend up to $250 with no approval. Apart from travel, the limit is $2,500.",
        "You can spend up to $250 with no approval. Barring weekends, the cap is $2,500.",
        "You can spend up to $250 with no approval. Outside of payroll, the ceiling is $2,500.",
        "You can spend up to $250 with no approval. The limit is $2,500 and no sign-off is needed.",
        "You can spend up to $250 with no approval. Purchases up to $2,500 require no sign-off.",
        "You can spend up to $250 with no approval. No sign-off is needed up to $2,500.",
        "You can spend up to $250 with no approval. There is no authorization up to $2,500.",
        "You can spend up to $250 with no approval. Purchases up to $2,500 don't require approval.",
        "You can spend up to $250 with no approval. You don't need a sign-off up to $2,500.",
        "You can spend up to $250 with no approval. There isn't any approval needed up to $2,500.",
        "You can spend up to $250 with no approval. Unless the limit is $2,500.",
        "You can spend up to $250 with no approval. Besides the limit is $2,500.",
        "You can spend up to $250 with no approval, except the cap is $2,500.",
        "You can spend up to $250 with no approval. Unless I'm wrong but the limit is $2,500.",
        "You can spend up to $250 with no approval. Other than that: the limit is $2,500.",
        # Says nobody approves above $2,500 either, which the policy contradicts.
        "You can spend up to $250 without approval, and there is no approval above $2,500.",
        "You can spend up to $250 with no approval. Your manager approves up to $2,500.",
        "You can spend up to $250 with no approval. Expenses are blocked without your manager's sign-off and above $2,500 finance approves.",
        "You can spend up to $250 with no approval. Filing fails without your manager's sign-off and above $2,500 finance approves.",
        "You can spend up to $250 with no approval. Above that, it is rejected without your manager's sign-off, but nothing goes through.",
        "You can spend up to $250 with no approval. Above $2,500, it is rejected without your manager's sign-off, but you can't submit it.",
        "You can spend up to $250 with no approval. Above $2,500 finance approves, but expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 finance approves; expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval and your manager approves.",
        "Purchases up to $250 need no approval between $251 and $2,500.",
        "You can spend up to $250 with no approval from $251 to $2,500.",
        "Purchases up to $250 need no approval from $250 to $2,500.",
        "You can spend up to $250 with no approval. Nothing goes through and above $2,500 it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. No purchase goes through and above $2,500 it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. You can't submit it and above $2,500 it is rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 finance approves and expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Above $2,500 finance approves, expenses are rejected without your manager's sign-off.",
        "You can spend up to $250 with no approval. Your manager must approve the $250 purchase, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves $250, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves the $250.00 purchase, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager must approve the 250 dollar purchase, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager must sign the $250 purchase off, from $251 to $2,500.",
        "You can spend up to $250 with no approval. The $250 purchase needs your manager's approval, from $251 to $2,500.",
        "You can spend up to $250 with no approval. Your manager approves every purchase, from $251 to $2,500.",
        "You can spend up to $250 with no approval by submitting it for your manager to approve.",
        "You can spend up to $250 with no approval, above that your manager must approve it, and also the $250 purchase.",
        "You can spend up to $250 with no approval and your manager must approve it above that, including the $250 purchase.",
        "You can spend up to $250 without approval. Above that your manager must approve every one, including the $250 purchase.",
    ],
)
def test_f2_needs_the_no_approval_amount_not_just_the_words(fx: dict, answer: str) -> None:
    assert kb_harness.score(_question(fx, "f2"), "cites", {"drive-fin-expense-policy"}, answer)[0] is False


class _FakeKbs:
    """Stands in for the SDK's knowledge base calls, recording what main does."""

    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.kbs = dict(existing or {})
        self.deleted: list[str] = []
        self.created: list[str] = []

    def list_knowledge_bases(self) -> object:

        return SimpleNamespace(knowledge_bases=[SimpleNamespace(name=n, id=i) for n, i in self.kbs.items()])

    def create_knowledge_base(self, *, kb_name: str) -> object:

        kb_id = f"new-{len(self.created)}"
        self.created.append(kb_id)
        self.kbs[kb_name] = kb_id
        return SimpleNamespace(id=kb_id)

    def delete_knowledge_base(self, *, kb_id: str) -> None:
        self.deleted.append(kb_id)
        self.kbs = {n: i for n, i in self.kbs.items() if i != kb_id}


def _run_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kbs: _FakeKbs, extra: list[str],
    waited: list[tuple[str, int]] | None = None,
) -> list[list[str] | None]:
    waited = [] if waited is None else waited
    env = tmp_path / "bootstrap.env"
    env.write_text("PIPESHUB_ORIGIN=http://localhost:1\nPIPESHUB_ACCOUNT_EMAIL=a@b.c\nPIPESHUB_ACCOUNT_PASSWORD=x\n")

    class FakePipeshub:
        def __init__(self, *_: object, **__: object) -> None:
            self.knowledge_base = kbs

        def __enter__(self) -> "FakePipeshub":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    sdk = types.ModuleType("pipeshub_sdk")
    sdk.Pipeshub = FakePipeshub
    sdk.models = SimpleNamespace(Security=lambda **_: object())
    monkeypatch.setitem(sys.modules, "pipeshub_sdk", sdk)
    monkeypatch.setattr(kb_harness, "login", lambda *_: "jwt")
    monkeypatch.setattr(kb_harness, "upload", lambda *_: None)
    monkeypatch.setattr(kb_harness, "wait_kb_indexed", lambda _o, _j, kb_id, n: waited.append((kb_id, n)))
    asked: list[list[str] | None] = []

    def ask(*args: object) -> tuple[str, list[str]]:
        asked.append(args[-1])  # type: ignore[arg-type]
        return "", []

    monkeypatch.setattr(kb_harness, "ask", ask)
    monkeypatch.setattr("sys.argv", ["kb_harness.py", "--env", str(env), "--fixture", str(FIXTURE), "--runs", "1", *extra])
    kb_harness.main()
    return asked


def test_an_upload_run_asks_exactly_the_knowledge_bases_it_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs = _FakeKbs()
    asked = _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1"])
    assert asked == [kbs.created]
    assert len(kbs.created) == 4  # shared, plus Bob's pricing, deal-desk and people-managers


def test_an_upload_run_waits_for_every_file_in_every_knowledge_base_it_loads(
    fx: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kbs, waited = _FakeKbs(), []
    _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1"], waited)
    shared, restricted = kb_harness.upload_plan(fx)
    groups = sorted(kb_harness.upload_groups(fx, "bob") & set(restricted))
    assert waited == list(zip(kbs.created, [len(shared)] + [len(restricted[g]) for g in groups], strict=True))


def test_a_skip_shared_run_reuses_the_shared_knowledge_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs, waited = _FakeKbs({"Acme Corp (shared)": "old-shared"}), []
    asked = _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1", "--skip-shared"], waited)
    assert "old-shared" not in kbs.deleted
    assert asked == [["old-shared", *kbs.created]]
    assert waited[0][0] == "old-shared"


def test_a_skip_shared_run_without_the_shared_knowledge_base_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs = _FakeKbs()
    with pytest.raises(SystemExit, match="--skip-shared"):
        _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1", "--skip-shared"])
    assert kbs.created == []


def test_an_upload_run_replaces_knowledge_bases_left_by_an_earlier_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs = _FakeKbs({"Acme Corp (shared)": "old-shared"})
    _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1"])
    assert "old-shared" in kbs.deleted
    assert "old-shared" not in kbs.kbs.values()


def test_a_skip_upload_rerun_asks_the_same_knowledge_bases_the_persona_loaded(
    fx: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = {name: f"kb-{i}" for i, name in enumerate(kb_harness.kb_names_for(fx, "alice"))}
    existing["Acme Corp (deal desk)"] = "kb-bobs"  # another persona's knowledge base
    kbs = _FakeKbs(existing)
    asked = _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1", "--skip-upload", "--skip-restricted"])
    assert asked == [[existing[n] for n in kb_harness.kb_names_for(fx, "alice")]]
    assert kbs.created == [] and kbs.deleted == []


def test_a_skip_upload_rerun_without_the_knowledge_bases_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="no knowledge base named"):
        _run_main(tmp_path, monkeypatch, _FakeKbs(), ["--only", "q1", "--skip-upload"])


def test_an_incomplete_upload_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:

    sdk = types.ModuleType("pipeshub_sdk")
    sdk.models = SimpleNamespace(UploadRecordsFile=lambda **kw: kw)
    monkeypatch.setitem(sys.modules, "pipeshub_sdk", sdk)

    @contextmanager
    def one_of_two(**_: object) -> Iterator[list[SimpleNamespace]]:
        yield [SimpleNamespace(event="file:succeeded", data="")]

    ph = SimpleNamespace(knowledge_base=SimpleNamespace(upload_records=one_of_two))
    with pytest.raises(SystemExit, match="1 of 2"):
        kb_harness.upload(ph, "kb", [("a.md", "a"), ("b.md", "b")])


def _states(*rounds: list[str]) -> Callable[[str, str, str], list[str]]:
    it = iter(rounds)
    return lambda *_: next(it)


def test_waiting_for_a_knowledge_base_needs_every_file_indexed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_harness.time, "sleep", lambda _: None)
    rounds = [["COMPLETED"], ["COMPLETED", "IN_PROGRESS", "QUEUED"], ["COMPLETED"] * 3]
    polls: list[str] = []

    def states(*_: str) -> list[str]:
        polls.append("poll")
        return rounds[len(polls) - 1]

    kb_harness.wait_kb_indexed("o", "j", "kb", 3, states=states)
    assert len(polls) == 3  # kept waiting through the partial rounds, stopped once all three were done


def test_waiting_stops_when_a_file_fails_to_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_harness.time, "sleep", lambda _: None)
    with pytest.raises(SystemExit, match="did not index"):
        kb_harness.wait_kb_indexed("o", "j", "kb", 2, states=_states(["COMPLETED", "FAILED"]))


def test_waiting_gives_up_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_harness.time, "sleep", lambda _: None)
    with pytest.raises(SystemExit, match="1 of 2"):
        kb_harness.wait_kb_indexed("o", "j", "kb", 2, timeout=0, states=lambda *_: ["COMPLETED", "QUEUED"])


def test_record_states_read_every_page_of_the_knowledge_base() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        items = [{"indexingStatus": "COMPLETED"}] * (100 if page == 1 else 7)
        return httpx.Response(200, json={"items": items, "pagination": {"hasNext": page == 1}})

    states = kb_harness.kb_record_states("http://pipeshub", "jwt", "kb-1", transport=httpx.MockTransport(handler))
    assert len(states) == 107
    assert [r.url.path for r in seen] == ["/api/v1/knowledgeBase/knowledge-hub/nodes/app/kb-1"] * 2
    assert all(r.headers["Authorization"] == "Bearer jwt" for r in seen)
    assert seen[0].url.params["nodeTypes"] == "record" and seen[0].url.params["flattened"] == "true"


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Northwind is back on track; renewal expected on time.", True),
        ("Northwind is no longer at risk after the export fix.", True),
        ("Northwind's renewal is not on time.", False),
        # A phrase isn't read inside a longer word.
        ("Northwind is still at risk; the renewal depends on timeout fixes.", False),
        ("Yes, Northwind is still at risk while they wait on timeout resolution.", False),
        ("Northwind is still at risk; the renewal depends on time-out fixes.", False),
    ],
)
def test_s1_recovery_phrases_are_whole_words(fx: dict, answer: str, ok: bool) -> None:
    cited = {"drive-sales-northwind-plan", "drive-sales-northwind-call-0416"}
    assert kb_harness.score(_question(fx, "s1"), "cites", cited, answer)[0] is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("You can carry over five days of unused leave.", True),
        ("You can carry over twenty-five days.", False),
        ("You can carry over twenty five days.", False),
        ("It's twenty-five days, not five.", False),
        ("You can carry over five hundred days.", False),
        ("You can carry over 5 hundred days.", False),
        ("You can carry over five-hundred days.", False),
        ("It's five hundred days, not five.", False),
        ("Up to 5 days carry over.", True),
        ("Carry over starts on the 5th.", False),
        ("Policy v5 says three days.", False),
        ("The handbook says three. See section 5.", False),
    ],
)
def test_h1_five_is_not_the_end_of_twenty_five(fx: dict, answer: str, ok: bool) -> None:
    assert kb_harness.score(_question(fx, "h1"), "cites", {"slack-people-0302"}, answer)[0] is ok

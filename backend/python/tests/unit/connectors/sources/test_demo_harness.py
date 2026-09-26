"""The demo harness models who can see what, and scores every golden question.

It stands in for the connector's permissions when it uploads the fixture into
knowledge bases, and it is what the Build Pack questions are scored with. These
catch the ways it would quietly give a wrong answer: a restricted record landing
in the shared knowledge base, `--only` skipping a question and still passing, and
the installing admin being scored as if it were Alice.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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


def test_the_upload_waits_for_every_knowledge_base_the_persona_loads(fx: dict) -> None:
    shared, restricted = kb_harness.upload_plan(fx)
    records = {r["title"] for r in fx["records"]} | {t["title"] for t in fx.get("threads", [])}
    for persona, groups in (("alice", {"launch-core", "payments-contract"}),
                            ("bob", {"pricing-committee", "deal-desk", "people-managers"})):
        readable = kb_harness.upload_groups(fx, persona)
        probes = kb_harness.wait_probes(shared, restricted, readable)
        # One per loaded knowledge base, each the last file uploaded to it.
        assert len(probes) == 1 + len(groups), (persona, probes)
        assert probes[0] == shared[-1][0].removesuffix(".md")
        assert probes[1:] == [restricted[g][-1][0].removesuffix(".md") for g in sorted(groups)]
        assert all(any(kb_harness.safe_name(t) == p for t in records) for p in probes)


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

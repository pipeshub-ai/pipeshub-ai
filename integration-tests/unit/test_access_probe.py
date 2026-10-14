"""The access probes never read a failure, or a missing citation, as "no access".

The "cannot see it" checks in the permission suites pass when a probe says the
record stayed out of reach. If a server error or an expired login counted as
that, a broken stack would pass every one of them. For chat, the stream reports
citations but not what retrieval found, so only a refusal proves the record was
out of reach; an answer that simply did not cite it proves only that it did not
leak it.
"""

from __future__ import annotations

import pytest

from helper import access_probe
from helper.access_probe import (
    ChatAnswer,
    ChatVerdict,
    SearchOutcome,
    _cited_ids,
    chat_verdict,
)
from helper.second_user import SecondUser

pytestmark = pytest.mark.unit

RECORD, VIRTUAL = "rec-1", "vrec-1"
USER = SecondUser(
    user_id="u1", graph_id="g1", email="reader@example.com",
    token="t", base_url="http://pipeshub.test", timeout=5,
)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (SearchOutcome(200, ("other",)), True),
        (SearchOutcome(200, ()), True),
        (SearchOutcome(404, ()), True),
        (SearchOutcome(403, ()), True),
        (SearchOutcome(200, (VIRTUAL,)), False),
        (SearchOutcome(401, ()), False),
        (SearchOutcome(500, ()), False),
    ],
)
def test_search_counts_only_real_answers_as_out_of_reach(outcome: SearchOutcome, expected: bool) -> None:
    assert outcome.refused_or_missing(VIRTUAL) is expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (ChatAnswer(status=404), ChatVerdict.REFUSED),
        (ChatAnswer(status=403), ChatVerdict.REFUSED),
        (ChatAnswer(status=200, finished=True), ChatVerdict.NOT_CITED),
        (ChatAnswer(status=200, finished=True, cited_record_ids={RECORD}), ChatVerdict.CITED),
        (ChatAnswer(status=200, finished=True, cited_virtual_ids={VIRTUAL}), ChatVerdict.CITED),
        # A stream that stopped without finishing or failing proves nothing.
        (ChatAnswer(status=200), ChatVerdict.INCONCLUSIVE),
        (ChatAnswer(status=500), ChatVerdict.INCONCLUSIVE),
        (ChatAnswer(status=401), ChatVerdict.INCONCLUSIVE),
    ],
)
def test_chat_verdicts(answer: ChatAnswer, expected: ChatVerdict) -> None:
    assert chat_verdict(answer, RECORD, VIRTUAL) is expected


def test_a_run_error_is_not_a_refusal() -> None:
    """A RUN_ERROR, whatever its message, is not the product refusing access."""
    for message in ("no documents", "agent_error", "rate limited"):
        answer = ChatAnswer(status=200, error=message)
        assert chat_verdict(answer, RECORD, VIRTUAL) is ChatVerdict.INCONCLUSIVE


def test_an_uncited_answer_does_not_prove_the_record_out_of_reach() -> None:
    """Retrieval may have found the record and the model left the citation out."""
    answer = ChatAnswer(status=200, finished=True)
    assert chat_verdict(answer, RECORD, VIRTUAL) is not ChatVerdict.REFUSED


def test_an_inconclusive_answer_is_asked_again_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _ask(*_args: object, **_kwargs: object) -> ChatAnswer:
        calls.append("ask")
        return ChatAnswer(status=200, error="agent_error")

    monkeypatch.setattr(access_probe, "ask_once", _ask)
    with pytest.raises(AssertionError, match="no conclusive answer"):
        access_probe.assert_no_chat_leak(USER, "q", RECORD, VIRTUAL, "why")
    assert len(calls) == access_probe.CHAT_ATTEMPTS


def test_a_retry_that_finishes_without_citing_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([ChatAnswer(status=500), ChatAnswer(status=200, finished=True)])
    monkeypatch.setattr(access_probe, "ask_once", lambda *_a, **_k: next(answers))
    access_probe.assert_no_chat_leak(USER, "q", RECORD, VIRTUAL, "why")


def test_a_cited_answer_fails_as_a_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        access_probe, "ask_once",
        lambda *_a, **_k: ChatAnswer(status=200, finished=True, cited_record_ids={RECORD}),
    )
    with pytest.raises(AssertionError, match="cited it"):
        access_probe.assert_no_chat_leak(USER, "q", RECORD, VIRTUAL, "why")


def test_citations_are_read_in_either_shape() -> None:
    records, virtuals = _cited_ids([
        {"citationData": {"metadata": {"recordId": "a", "virtualRecordId": "va"}}},
        {"recordId": "b"},
        {"citation": {"metadata": {"virtualRecordId": "vc"}}},
    ])
    assert records == {"a", "b"}
    assert virtuals == {"va", "vc"}

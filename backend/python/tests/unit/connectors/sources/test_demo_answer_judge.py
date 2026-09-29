"""The demo answer judge, with the model replaced by a scripted client.

Nothing here calls a model. Each test hands the judge the reply a model might
give and checks what the harness makes of it: a pass only for a supported claim
quoted from the answer, and never a pass for a failed or garbled read.
"""

from __future__ import annotations

import json

import pytest

from app.connectors.sources.demo.harness import answer_judge as aj
from app.connectors.sources.demo.harness.answer_judge import (
    AnswerJudge,
    JudgeResult,
    LangChainJudgeClient,
    quote_in_answer,
)
from app.connectors.sources.demo.harness.kb_harness import score

ANSWER = "You can spend up to **$250** on a purchase without any approval. Submit the receipt within 30 days."
NO_APPROVAL = "A purchase of up to and including $250 needs no approval."
RECEIPT = "Receipts must be submitted within 30 days."


class ScriptedClient:
    def __init__(self, *replies: str | BaseException) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class RefusingClient:
    """Records any call; the judge would swallow an exception raised here."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return reply(("supported", "x"))


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def reply(*claims: tuple[str, str]) -> str:
    return json.dumps({"claims": [
        {"id": i, "reasoning": "because", "verdict": verdict, "quote": quote}
        for i, (verdict, quote) in enumerate(claims, start=1)
    ]})


def judge_with(*replies: str | BaseException) -> tuple[AnswerJudge, ScriptedClient]:
    client = ScriptedClient(*replies)
    return AnswerJudge(client, sleep=lambda _s: None), client


@pytest.fixture(autouse=True)
def _judge_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aj.REQUIRE_JUDGE_ENV, raising=False)


def test_a_supported_claim_quoted_from_the_answer_passes() -> None:
    judge, client = judge_with(reply(("supported", "up to $250 on a purchase without any approval")))
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judged" and result.passed
    assert result.claims[0].verdict == "supported"
    assert len(client.calls) == 1
    assert NO_APPROVAL in client.calls[0][1] and ANSWER in client.calls[0][1]


def test_every_claim_goes_in_one_call() -> None:
    judge, client = judge_with(reply(
        ("supported", "without any approval"), ("supported", "within 30 days"), ("missing", ""),
    ))
    result = judge.judge(ANSWER, [NO_APPROVAL, RECEIPT], ["Every purchase needs manager approval."])
    assert result.passed
    assert len(client.calls) == 1


def test_a_contradicted_claim_fails() -> None:
    answer = "Every purchase needs your manager's approval."
    judge, _ = judge_with(reply(("contradicted", "Every purchase needs your manager's approval")))
    result = judge.judge(answer, [NO_APPROVAL])
    assert not result.passed
    assert result.claims[0].verdict == "contradicted"


def test_a_missing_claim_fails() -> None:
    judge, _ = judge_with(reply(("missing", "")))
    assert not judge.judge("I couldn't find the expense policy.", [NO_APPROVAL]).passed


def test_a_quote_that_is_not_in_the_answer_is_downgraded_to_unverified() -> None:
    judge, _ = judge_with(reply(("supported", "purchases of $250 or less need no approval")))
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert not result.passed
    assert result.claims[0].verdict == "unverified"


def test_an_empty_quote_on_a_supported_verdict_is_unverified() -> None:
    judge, _ = judge_with(reply(("supported", "")))
    assert judge.judge(ANSWER, [NO_APPROVAL]).claims[0].verdict == "unverified"


@pytest.mark.parametrize(("quote", "found"), [
    ("You can spend up to $250", True),
    # Case, spacing, curly quotes and markdown emphasis don't make a quote invented.
    ("you  can SPEND up to $250", True),
    ("“up to $250 on a purchase”", True),
    ("spend up to **$250**", True),
    ("You can spend ... without any approval", True),
    ("without any approval ... You can spend", False),
    ("up to $2,500", False),
    ("", False),
])
def test_quote_matching_ignores_formatting_but_not_words(quote: str, found: bool) -> None:
    assert quote_in_answer(quote, ANSWER) is found


@pytest.mark.parametrize(("quote", "answer", "found"), [
    ("up to $250", "You can spend up to $2500 without approval.", False),
    ("up to $250", "You can spend up to $250 without approval.", True),
    ("up to $250", "You can spend up to $250.00 without approval.", True),
    ("up to $250", "You can spend up to $250.50 without approval.", False),
    ("up to $250", "Spend up to $250, then ask.", True),
    ("$250", "The limit is $12500.", False),
    ("250", "The limit is 12500.", False),
    ("$2", "The limit is $2,500.", False),
    ("$250", "The limit is $250,000.", False),
    # A later occurrence is still found when the first runs on into a longer number.
    ("up to $250", "Not up to $2500: up to $250 needs no approval.", True),
    ("up to $250 ... no approval", "Up to $2500 ... no approval", False),
    ("up to $250", "Deals up to $250k need a VP.", False),
    ("up to $250", "Deals up to $250K need a VP.", False),
    ("up to $250", "Deals up to $250m need a VP.", False),
    ("up to $250", "Deals up to $250bn need a VP.", False),
    ("up to $250", "Deals up to $250MM need a VP.", False),
    ("up to $250", "Deals up to $250 million need a VP.", False),
    ("up to $250", "Deals up to $250 thousand need a VP.", False),
    ("up to $250", "Deals up to $250billion need a VP.", False),
    ("up to $250", "No approval is needed up to $250.", True),
    ("up to $250", "No approval (up to $250) is needed.", True),
    ("up to $250", "Up to $250, no approval is needed.", True),
    ("up to $250", "Up to $250 more or less needs no approval.", True),
])
def test_a_quoted_number_must_not_be_part_of_a_longer_one(quote: str, answer: str, found: bool) -> None:
    assert quote_in_answer(quote, answer) is found


@pytest.mark.parametrize("q", [
    {"answer_must_state": "A purchase of up to $250 needs no approval."},
    {"answer_must_state": [NO_APPROVAL], "answer_must_not_state": "Every purchase needs manager approval."},
    {"answer_must_state": [NO_APPROVAL, ""]},
])
def test_facts_that_are_not_a_list_of_sentences_are_a_judge_error(q: dict) -> None:
    client = RefusingClient()
    result = aj.check_content(q, ANSWER, AnswerJudge(client))
    assert result is not None and result.status == "judge error" and not result.passed
    assert client.calls == 0


@pytest.mark.parametrize("raw", [
    "not json at all",
    '{"claims": [{"id": 1, "verdict": "probably", "quote": ""}]}',
    '{"claims": []}',
    '{"claims": [{"id": 2, "verdict": "missing", "quote": ""}]}',
    '{"verdicts": "supported"}',
])
def test_a_malformed_reply_is_a_judge_error_not_a_pass(raw: str) -> None:
    judge, _ = judge_with(raw)
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judge error"
    assert not result.passed


def test_a_fenced_json_reply_is_accepted() -> None:
    judge, _ = judge_with("```json\n" + reply(("supported", "without any approval")) + "\n```")
    assert judge.judge(ANSWER, [NO_APPROVAL]).passed


def test_a_model_error_is_a_judge_error_not_a_pass() -> None:
    judge, client = judge_with(ValueError("bad request"))
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judge error" and not result.passed
    assert len(client.calls) == 1, "a non-transient error is not retried"


def test_a_timeout_that_outlasts_the_retries_is_a_judge_error() -> None:
    judge, client = judge_with(TimeoutError(), TimeoutError(), TimeoutError())
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judge error" and not result.passed
    assert len(client.calls) == 3


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limits_and_server_errors_are_retried_with_backoff(status: int) -> None:
    slept: list[float] = []
    client = ScriptedClient(StatusError(status), StatusError(status), reply(("supported", "without any approval")))
    result = AnswerJudge(client, sleep=slept.append, backoff_s=1.0).judge(ANSWER, [NO_APPROVAL])
    assert result.passed
    assert slept == [1.0, 2.0]


def test_a_must_not_state_claim_fails_only_when_the_answer_states_it() -> None:
    forbidden = "Every purchase needs manager approval."
    for verdict, quote, passed in [
        ("missing", "", True),
        ("contradicted", "without any approval", True),
        ("supported", "without any approval", False),
    ]:
        judge, _ = judge_with(reply((verdict, quote)))
        assert judge.judge(ANSWER, [], [forbidden]).passed is passed, verdict


def test_not_judged_passes_unless_the_judge_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    assert JudgeResult.not_judged().passed
    monkeypatch.setenv(aj.REQUIRE_JUDGE_ENV, "1")
    result = JudgeResult.not_judged()
    assert result.status == "not judged" and not result.passed


# --- score() -------------------------------------------------------------------

QUESTION = {
    "id": "f2",
    "must_cite": ["drive-fin-expense-policy"],
    "answer_must_state": [NO_APPROVAL],
    "restricted": ["drive-fin-expense-policy"],
    "restricted_facts": ["$250"],
}
CITED = {"drive-fin-expense-policy"}


def test_score_passes_when_citations_and_judged_content_both_pass() -> None:
    judge, _ = judge_with(reply(("supported", "without any approval")))
    ok, verdict = score(QUESTION, "cites", CITED, ANSWER, judge=judge)
    assert ok
    assert verdict.startswith("PASS (full)") and "ok supported" in verdict


def test_score_fails_a_well_cited_answer_the_judge_rejects() -> None:
    answer = "Up to $250, your manager has to approve the purchase."
    judge, _ = judge_with(reply(("contradicted", "your manager has to approve")))
    ok, verdict = score(QUESTION, "cites", CITED, answer, judge=judge)
    assert not ok
    assert verdict.startswith("FAIL (full)")
    assert "FAIL contradicted" in verdict and NO_APPROVAL in verdict


def test_score_without_a_judge_is_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    ok, verdict = score(QUESTION, "cites", CITED, ANSWER)
    assert ok and "not judged" in verdict
    monkeypatch.setenv(aj.REQUIRE_JUDGE_ENV, "1")
    ok, verdict = score(QUESTION, "cites", CITED, ANSWER)
    assert not ok and "not judged" in verdict


@pytest.mark.parametrize(("answer", "cited", "ok"), [
    ("I couldn't find an expense policy you can see.", set(), True),
    ("The limit is $250.", set(), False),
    ("Here it is.", CITED, False),
    ("ERROR: upstream timeout", set(), False),
])
def test_permission_checks_never_ask_the_judge(answer: str, cited: set[str], ok: bool) -> None:
    client = RefusingClient()
    assert score(QUESTION, "none", cited, answer, judge=AnswerJudge(client))[0] is ok
    assert client.calls == 0


def test_a_question_without_facts_never_asks_the_judge() -> None:
    q = {"must_cite": ["drive-oncall-handbook"]}
    client = RefusingClient()
    assert score(q, "cites", {"drive-oncall-handbook"}, "Voluntary.", judge=AnswerJudge(client))[0]
    assert client.calls == 0


class FakeMessage:
    def __init__(self, content: object) -> None:
        self.content = content
        self.usage_metadata = {"input_tokens": 120, "output_tokens": 30}


class FakeChatModel:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def invoke(self, messages: list, **kwargs: object) -> FakeMessage:
        self.kwargs = kwargs
        return FakeMessage([{"type": "text", "text": '{"claims": []}'}])


def test_the_langchain_client_asks_for_json_with_a_timeout_and_counts_tokens() -> None:
    model = FakeChatModel()
    client = LangChainJudgeClient(model, timeout_s=30)
    assert client.complete("system", "user") == '{"claims": []}'
    assert model.kwargs == {"timeout": 30, "response_format": {"type": "json_object"}}
    assert (client.calls, client.input_tokens, client.output_tokens) == (1, 120, 30)

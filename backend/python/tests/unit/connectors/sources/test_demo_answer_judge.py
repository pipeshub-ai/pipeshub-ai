"""The demo answer judge, with the model replaced by a scripted client.

Nothing here calls a model. Each test hands the judge the reply a model might
give and checks what the harness makes of it: a pass only for a supported claim
that cites a real sentence of the answer, and never a pass for a failed or
garbled read.
"""

from __future__ import annotations

import json

import pytest

from app.connectors.sources.demo.harness import answer_judge as aj
from app.connectors.sources.demo.harness.answer_judge import (
    AnswerJudge,
    AnthropicJudgeClient,
    JudgeResult,
    LangChainJudgeClient,
    split_sentences,
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
        return reply(("supported", [1]))


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def reply(*claims: tuple[str, list[int]]) -> str:
    return json.dumps({"claims": [
        {"id": i, "reasoning": "because", "verdict": verdict, "evidence_sentence_ids": ids}
        for i, (verdict, ids) in enumerate(claims, start=1)
    ]})


def judge_with(*replies: str | BaseException) -> tuple[AnswerJudge, ScriptedClient]:
    client = ScriptedClient(*replies)
    return AnswerJudge(client, sleep=lambda _s: None), client


@pytest.fixture(autouse=True)
def _judge_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aj.REQUIRE_JUDGE_ENV, raising=False)


def test_a_supported_claim_citing_a_sentence_of_the_answer_passes() -> None:
    judge, client = judge_with(reply(("supported", [1])))
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judged" and result.passed
    assert result.claims[0].verdict == "supported"
    assert len(client.calls) == 1
    assert NO_APPROVAL in client.calls[0][1]


def test_every_claim_goes_in_one_call() -> None:
    judge, client = judge_with(reply(
        ("supported", [1]), ("supported", [2]), ("missing", []),
    ))
    result = judge.judge(ANSWER, [NO_APPROVAL, RECEIPT], ["Every purchase needs manager approval."])
    assert result.passed
    assert len(client.calls) == 1


def test_a_contradicted_claim_fails() -> None:
    answer = "Every purchase needs your manager's approval."
    judge, _ = judge_with(reply(("contradicted", [1])))
    result = judge.judge(answer, [NO_APPROVAL])
    assert not result.passed
    assert result.claims[0].verdict == "contradicted"


def test_a_missing_claim_fails() -> None:
    judge, _ = judge_with(reply(("missing", [])))
    assert not judge.judge("I couldn't find the expense policy.", [NO_APPROVAL]).passed


@pytest.mark.parametrize("ids", [[3], [0], [-1], [1, 7]])
def test_citing_a_sentence_the_answer_does_not_have_is_unverified(ids: list[int]) -> None:
    judge, _ = judge_with(reply(("supported", ids)))
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert not result.passed
    assert result.claims[0].verdict == "unverified"


@pytest.mark.parametrize("verdict", ["supported", "contradicted"])
def test_a_verdict_that_cites_no_sentence_is_unverified(verdict: str) -> None:
    judge, _ = judge_with(reply((verdict, [])))
    assert judge.judge(ANSWER, [NO_APPROVAL]).claims[0].verdict == "unverified"


def test_a_must_not_state_claim_with_no_citation_is_unverified_and_fails() -> None:
    judge, _ = judge_with(reply(("contradicted", [])))
    assert not judge.judge(ANSWER, [], ["Every purchase needs manager approval."]).passed


def test_the_cited_sentences_are_kept_for_the_report() -> None:
    judge, _ = judge_with(reply(("contradicted", [2])))
    claim = judge.judge(ANSWER, [NO_APPROVAL]).claims[0]
    assert claim.evidence_ids == [2]
    assert claim.evidence == ["Submit the receipt within 30 days."]
    assert "Submit the receipt" in claim.render()


def test_the_judge_sees_the_answer_as_numbered_sentences() -> None:
    judge, client = judge_with(reply(("supported", [1])))
    judge.judge(ANSWER, [NO_APPROVAL])
    prompt = client.calls[0][1]
    assert "[1] You can spend up to **$250** on a purchase without any approval." in prompt
    assert "[2] Submit the receipt within 30 days." in prompt


def test_an_empty_answer_leaves_nothing_to_cite() -> None:
    judge, _ = judge_with(reply(("supported", [1])))
    assert judge.judge("", [NO_APPROVAL]).claims[0].verdict == "unverified"


@pytest.mark.parametrize(("answer", "sentences"), [
    ("One. Two! Three? Four", ["One.", "Two!", "Three?", "Four"]),
    ("Up to $250 needs no approval. Submit within 30 days.", ["Up to $250 needs no approval.", "Submit within 30 days."]),
    # No split inside a number, an abbreviation followed by lower case, or a decimal.
    ("The rate is 2.2% and e.g. cards are 3.1% this year.", ["The rate is 2.2% and e.g. cards are 3.1% this year."]),
    ("## Approvals\n- Up to $250: no approval.\n* Over $2,500: finance.\n\n1. First. 2) Second",
     ["## Approvals", "Up to $250: no approval.", "Over $2,500: finance.", "First.", "2) Second"]),
    ('She said "yes." Then it merged.', ['She said "yes."', "Then it merged."]),
    ("**Marcus Webb** approved. **Dana** asked for a bucket.", ["**Marcus Webb** approved.", "**Dana** asked for a bucket."]),
    ("   \n\n  ", []),
])
def test_the_answer_is_split_into_lines_and_sentences(answer: str, sentences: list[str]) -> None:
    assert split_sentences(answer) == sentences


# The chat runs its tool-call preamble into the answer with no space after the full stop.
GLUED = (
    "I\u2019ll check the internal policy for purchase approval thresholds.You can spend up to **$250** "
    "on a purchase without your manager\u2019s approval; **more than $250 up to $2,500** needs manager "
    "approval [source](ref9)."
)


@pytest.mark.parametrize(("answer", "sentences"), [
    (GLUED, [
        "I\u2019ll check the internal policy for purchase approval thresholds.",
        GLUED.split("thresholds.", 1)[1],
    ]),
    ("Up to $250.Your manager approves above that.", ["Up to $250.", "Your manager approves above that."]),
    ("See [the policy](ref9).Then file it.", ["See [the policy](ref9).", "Then file it."]),
    ("It is **$250**.**Above** that, ask.", ["It is **$250**.", "**Above** that, ask."]),
    ("Done!Next? Yes.", ["Done!", "Next?", "Yes."]),
])
def test_a_full_stop_glued_to_a_capital_still_ends_the_sentence(answer: str, sentences: list[str]) -> None:
    assert split_sentences(answer) == sentences


@pytest.mark.parametrize("first", [
    "Shipped from the U.S.A last week.",
    "Some tools, e.g.Foo and i.e.Bar, need setup.",
    "Mr.Smith and Dr.Jones approved it.",
    "It costs $2.50 a month.",
    "Upgrade to v1.2 before Friday.",
    "Run it from ~/.Config first.",
])
def test_initials_abbreviations_and_numbers_inside_a_sentence_stay_whole(first: str) -> None:
    # Glued to a second sentence, so the break has to be found and the dots before it skipped.
    assert split_sentences(first + "Then it shipped.") == [first, "Then it shipped."]


def test_a_citation_of_the_sentence_after_a_glued_preamble_is_accepted() -> None:
    judge, client = judge_with(reply(("supported", [2])))
    result = judge.judge(GLUED, [NO_APPROVAL])
    assert result.passed
    assert result.claims[0].verdict == "supported"
    assert result.claims[0].evidence == [GLUED.split("thresholds.", 1)[1]]
    assert "[2] You can spend up to **$250**" in client.calls[0][1]


@pytest.mark.parametrize("raw", [
    "not json at all",
    '{"claims": [{"id": 1, "verdict": "probably", "evidence_sentence_ids": []}]}',
    '{"claims": [{"id": 1, "verdict": "supported", "evidence_sentence_ids": "one"}]}',
    '{"claims": []}',
    '{"claims": [{"id": 2, "verdict": "missing", "evidence_sentence_ids": []}]}',
    '{"verdicts": "supported"}',
])
def test_a_malformed_reply_is_a_judge_error_not_a_pass(raw: str) -> None:
    judge, _ = judge_with(raw)
    result = judge.judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judge error"
    assert not result.passed


def test_a_fenced_json_reply_is_accepted() -> None:
    judge, _ = judge_with("```json\n" + reply(("supported", [1])) + "\n```")
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
    client = ScriptedClient(StatusError(status), StatusError(status), reply(("supported", [1])))
    result = AnswerJudge(client, sleep=slept.append, backoff_s=1.0).judge(ANSWER, [NO_APPROVAL])
    assert result.passed
    assert slept == [1.0, 2.0]


def test_a_must_not_state_claim_fails_only_when_the_answer_states_it() -> None:
    forbidden = "Every purchase needs manager approval."
    for verdict, ids, passed in [
        ("missing", [], True),
        ("contradicted", [1], True),
        ("supported", [1], False),
    ]:
        judge, _ = judge_with(reply((verdict, ids)))
        assert judge.judge(ANSWER, [], [forbidden]).passed is passed, verdict


def reply_with_conflicts(verdict: str, ids: list[int], conflicts: list[int]) -> str:
    return json.dumps({"claims": [{
        "id": 1, "reasoning": "because", "conflicting_sentence_ids": conflicts,
        "verdict": verdict, "evidence_sentence_ids": ids,
    }]})


CONTRADICTS_ITSELF = "Up to $250, but your manager's approval is required. You can spend up to $250 with no approval."


def test_the_prompt_keeps_stating_a_claim_apart_from_conflicting_with_it() -> None:
    prompt = " ".join(aj.SYSTEM_PROMPT.split())
    # A stated claim stays "supported", so a forbidden claim taken back still reads as stated.
    assert 'Give "supported" even when another sentence says something incompatible with the claim' in prompt
    assert '"contradicted": no sentence asserts the claim' in prompt
    assert "never \"supported\"" not in prompt
    assert "Check every sentence against every claim" in prompt
    assert '"conflicting_sentence_ids": []' in prompt


def test_the_prompt_tells_a_step_still_ahead_from_a_condition_that_takes_the_claim_back() -> None:
    prompt = " ".join(aj.SYSTEM_PROMPT.split())
    # "On track, but the signature is still due" failed s1 on 2026-10-09 as a conflict.
    assert "A step still ahead on the way to an outcome does not take back a claim about where it stands now" in prompt
    assert 'but only if the board reverses its cancellation" does conflict' in prompt
    # The takeback example stays a conflict, and a step still ahead is never a step done.
    assert 'but every refund waits for the monthly payment run." is "supported" by sentence 1 with sentence 1 also conflicting' in prompt
    assert "never makes a claim that the step has already happened true" in prompt


def test_a_must_state_claim_with_a_conflicting_sentence_is_contradicted_whatever_the_verdict() -> None:
    judge, _ = judge_with(reply_with_conflicts("supported", [2], [1]))
    claim = judge.judge(CONTRADICTS_ITSELF, [NO_APPROVAL]).claims[0]
    assert claim.verdict == "contradicted" and not claim.passed
    assert claim.conflicting_ids == [1]
    assert claim.conflicting == ["Up to $250, but your manager's approval is required."]
    rendered = claim.render()
    assert "conflicts=" in rendered and "Up to $250, but your manager" in rendered.split("conflicts=", 1)[1]


def test_a_contradicted_claim_may_cite_only_its_conflicting_sentences() -> None:
    judge, _ = judge_with(reply_with_conflicts("contradicted", [], [1]))
    claim = judge.judge(CONTRADICTS_ITSELF, [NO_APPROVAL]).claims[0]
    assert claim.verdict == "contradicted"


def test_a_conflicting_sentence_the_answer_does_not_have_is_unverified() -> None:
    judge, _ = judge_with(reply_with_conflicts("supported", [2], [5]))
    claim = judge.judge(CONTRADICTS_ITSELF, [NO_APPROVAL]).claims[0]
    assert claim.verdict == "unverified" and not claim.passed


def test_an_unverified_claim_names_the_sentence_that_is_not_in_the_answer() -> None:
    judge, _ = judge_with(reply_with_conflicts("supported", [2], [5]))
    claim = judge.judge(CONTRADICTS_ITSELF, [NO_APPROVAL]).claims[0]
    assert claim.out_of_range_ids == [5]
    rendered = claim.render()
    assert "cited sentences=[2] conflicting sentences=[5]" in rendered
    assert rendered.endswith("(not in the answer: [5])")


def test_an_unverified_claim_that_cites_nothing_says_so() -> None:
    judge, _ = judge_with(reply(("supported", [])))
    assert judge.judge(ANSWER, [NO_APPROVAL]).claims[0].render().endswith("(no sentence cited)")


FORBIDDEN = "Every purchase needs manager approval."
STATED_THEN_TAKEN_BACK = "Every purchase needs your manager's approval. Actually, up to $250 needs no approval."


def test_a_forbidden_claim_stated_then_taken_back_fails_when_the_judge_calls_it_contradicted() -> None:
    # Evidence [1] states the claim; [2] is the conflict. The older prompt asked for exactly this.
    judge, _ = judge_with(reply_with_conflicts("contradicted", [1, 2], [2]))
    claim = judge.judge(STATED_THEN_TAKEN_BACK, [], [FORBIDDEN]).claims[0]
    assert claim.verdict == "contradicted"
    assert claim.stated_ids == [1]
    assert not claim.passed


def test_a_forbidden_claim_stated_then_taken_back_fails_when_the_judge_calls_it_supported() -> None:
    judge, _ = judge_with(reply_with_conflicts("supported", [1], [2]))
    claim = judge.judge(STATED_THEN_TAKEN_BACK, [], [FORBIDDEN]).claims[0]
    assert claim.stated_ids == [1] and not claim.passed


@pytest.mark.parametrize(("verdict", "ids", "conflicts"), [
    ("contradicted", [1], [1]),
    ("contradicted", [1], []),
    ("missing", [], []),
])
def test_a_forbidden_claim_the_answer_never_states_passes(verdict: str, ids: list[int], conflicts: list[int]) -> None:
    answer = "Up to $250 needs no approval; your manager approves above that."
    judge, _ = judge_with(reply_with_conflicts(verdict, ids, conflicts))
    claim = judge.judge(answer, [], [FORBIDDEN]).claims[0]
    assert claim.stated_ids == [] and claim.passed


def test_a_must_state_claim_the_judge_calls_contradicted_with_its_statement_cited_fails() -> None:
    judge, _ = judge_with(reply_with_conflicts("contradicted", [2, 1], [1]))
    claim = judge.judge(CONTRADICTS_ITSELF, [NO_APPROVAL]).claims[0]
    assert claim.verdict == "contradicted" and claim.stated_ids == [2] and not claim.passed


def test_a_forbidden_claim_the_answer_states_fails_even_with_a_conflicting_sentence() -> None:
    forbidden = "Purchases of up to $250 need your manager's approval."
    judge, _ = judge_with(reply_with_conflicts("supported", [1], [2]))
    claim = judge.judge(CONTRADICTS_ITSELF, [], [forbidden]).claims[0]
    assert claim.verdict == "supported" and not claim.passed
    assert claim.conflicting_ids == [2]


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
    judge, _ = judge_with(reply(("supported", [1])))
    ok, verdict = score(QUESTION, "cites", CITED, ANSWER, judge=judge)
    assert ok
    assert verdict.startswith("PASS (full)") and "ok supported" in verdict


def test_score_fails_a_well_cited_answer_the_judge_rejects() -> None:
    answer = "Up to $250, your manager has to approve the purchase."
    judge, _ = judge_with(reply(("contradicted", [1])))
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


class Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class Usage:
    input_tokens = 900
    output_tokens = 250


class Response:
    def __init__(self, content: list[Block], stop_reason: str = "end_turn") -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = Usage()


class FakeMessages:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.kwargs: dict = {}

    def create(self, **kwargs: object) -> Response:
        self.kwargs = kwargs
        return self.response


class FakeAnthropic:
    def __init__(self, response: Response) -> None:
        self.messages = FakeMessages(response)


def test_the_anthropic_client_sends_no_sampling_parameters_and_reads_only_text() -> None:
    body = reply(("supported", [1]))
    sdk = FakeAnthropic(Response([Block("thinking", "{not the answer}"), Block("text", body)]))
    client = AnthropicJudgeClient(sdk, "claude-sonnet-5.5")
    assert client.complete("system", "user") == body
    sent = sdk.messages.kwargs
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(sent)
    assert sent["model"] == "claude-sonnet-5.5" and sent["system"] == "system"
    assert sent["messages"] == [{"role": "user", "content": "user"}]
    assert sent["extra_body"] == {"output_config": {"effort": "medium"}}
    assert (client.calls, client.input_tokens, client.output_tokens) == (1, 900, 250)


def test_the_anthropic_client_reply_is_judged_like_any_other() -> None:
    sdk = FakeAnthropic(Response([Block("text", "Here is my assessment:\n" + reply(("supported", [1])))]))
    result = AnswerJudge(AnthropicJudgeClient(sdk, "claude-sonnet-5.5"), sleep=lambda _s: None).judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judged" and result.passed


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_a_refusal_or_a_cut_off_reply_is_a_judge_error(stop_reason: str) -> None:
    sdk = FakeAnthropic(Response([Block("text", reply(("supported", [1])))], stop_reason))
    result = AnswerJudge(AnthropicJudgeClient(sdk, "claude-sonnet-5.5"), sleep=lambda _s: None).judge(ANSWER, [NO_APPROVAL])
    assert result.status == "judge error" and not result.passed
    assert stop_reason in result.detail


def test_a_model_error_names_its_class_and_status_but_not_a_key() -> None:
    err = StatusError(401)
    err.message = "Incorrect API key provided: sk-abc123***************************xyz9."
    judge, _ = judge_with(err)
    detail = judge.judge(ANSWER, [NO_APPROVAL]).detail
    assert "StatusError (HTTP 401)" in detail and "Incorrect API key provided" in detail
    assert "sk-abc123" not in detail and "xyz9" not in detail

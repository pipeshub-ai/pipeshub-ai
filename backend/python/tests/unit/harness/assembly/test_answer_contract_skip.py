"""ANSWER-CONTRACT-SKIP: a skipped grounding question was asked again and again.

The grounding question (ADR-RUN-03 D1) is asked once per subject in a conversation. Skipping it sends
``[No preference]``, which was read as a record the person picked, named "[No preference]", and searched for; a new
message typed instead of an answer started a new request, whose turn may show cards again, so the same subject was
asked about again. Now skipping is going on with what can be found (the not-found fact kept, so the answer leads
with it and its confidence is Low), and a subject this conversation already asked about is never asked again, however
the person dealt with the card. A new request that names a different subject is asked about once.
"""

from __future__ import annotations

from typing import Any

from app.harness.assembly.grounding_gate import GroundingGate, asked_before
from app.harness.grounding import (
    SKIPPED_ANSWER,
    Grounding,
    grounding_answer,
    grounding_question,
)

from .test_answer_contract import (
    FAQ,
    LOG,
    REQUEST,
    RUNBOOK,
    _chat,
    _context,
    _prefetch,
    _run,
)

_, QUESTION = grounding_question(Grounding("not_found", "SAAS launch", 1, 2, near=("product-faq",)))
ASKED = str(QUESTION["question"])
# The card as a saved turn holds it: the pre-run card's text, or the tool row the director's card ran as.
PRE_RUN_CARD = {"role": "bot_response", "content": ASKED}
TOOL_CARD = {
    "role": "bot_response", "content": "",
    "tool_results": [{"tool_name": "internaltools__ask_user_question", "status": "success",
                      "args": {"user_intent": "u", "questions": [QUESTION]}}],
}
SKIPPED = f'User selections:\n1. "{ASKED}" → {SKIPPED_ANSWER}'


def test_skip_is_going_on_with_what_can_be_found() -> None:
    answer = grounding_answer(SKIPPED)
    assert answer is not None and (answer.subject, answer.choice) == ("SAAS launch", "use_found")


async def test_skip_asks_nothing_and_keeps_the_not_found_fact() -> None:
    run = _run(SKIPPED, previous_conversations=({"role": "user_query", "content": REQUEST}, PRE_RUN_CARD))
    checked = await GroundingGate(run, _context(), _chat(), LOG).check(_prefetch(FAQ, RUNBOOK), delegating=False)
    assert checked is not None and checked.question is None and checked.answered
    assert (checked.grounding.subject, checked.grounding.status, checked.grounding.proceeded) == (
        "SAAS launch", "not_found", True,
    )


async def test_a_new_message_instead_of_an_answer_is_not_asked_the_same_thing_again() -> None:
    for card in (PRE_RUN_CARD, TOOL_CARD):
        history = ({"role": "user_query", "content": REQUEST}, card)
        run = _run('Just write the "SAAS launch" risk report', previous_conversations=history)
        checked = await GroundingGate(run, _context(), _chat(), LOG).check(_prefetch(FAQ, RUNBOOK), delegating=True)
        assert checked is not None and checked.question is None, card
        assert checked.grounding.not_found and checked.grounding.proceeded
        assert asked_before(run, "saas LAUNCH")


async def test_a_new_request_naming_a_different_subject_is_asked_once() -> None:
    history: tuple[dict[str, Any], ...] = ({"role": "user_query", "content": REQUEST}, PRE_RUN_CARD,
                                           {"role": "user_query", "content": SKIPPED}, {"role": "bot_response", "content": "Report."})
    run = _run('Prepare a risk report for "Orbit GA"', previous_conversations=history)
    checked = await GroundingGate(run, _context(), _chat(), LOG).check(_prefetch(FAQ, RUNBOOK), delegating=True)
    assert checked is not None and checked.question is not None
    assert str(checked.question["question"]).startswith("I couldn't find anything about “Orbit GA”")
    assert not asked_before(run, "Orbit GA")

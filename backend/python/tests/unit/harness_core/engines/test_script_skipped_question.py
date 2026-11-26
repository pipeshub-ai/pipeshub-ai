"""The scripted model after a question skipped by a new message (QAFIX2): turn 1 ended on its question, with no final
reply, so the history holds both user messages in one turn; the answer is the latest message's ``say``, as a model
answers the message it was just sent (QA-FP4 u4 showed "I have a question first. Fine, no pictures.")."""

from __future__ import annotations

from harness_core.testing.engines.script import ChatMessage, ScriptedBrain, ToolCall

TOOLS = ["internaltools__ask_user_question"]


def test_the_answer_after_a_skipped_question_is_the_new_message_s_say() -> None:
    asked = ToolCall("c1", "internaltools__ask_user_question", '{"questions": []}')
    messages = [
        ChatMessage("user", 'qa4-u4 pictures\ntool ask_user_question {"questions": []}\nsay I have a question first.'),
        ChatMessage("assistant", tool_calls=(asked,)),
        ChatMessage("tool", "skipped: the user sent a new message", tool_call_id="c1"),
        ChatMessage("user", "qa4-u4 turn 2: never mind\nsay Fine, no pictures."),
    ]
    assert ScriptedBrain().reply(messages, TOOLS, "c2").content == "Fine, no pictures."


def test_a_turn_with_one_say_still_answers_with_it() -> None:
    messages = [ChatMessage("user", "hello\nsay Hi."), ChatMessage("user", "and another thing")]
    assert ScriptedBrain().reply(messages, TOOLS, "c1").content == "Hi."

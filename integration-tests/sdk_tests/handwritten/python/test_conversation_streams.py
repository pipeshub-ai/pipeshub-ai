"""Streaming chat, follow-up, regenerate and feedback on a conversation."""

from pipeshub_sdk import Pipeshub

from .streams import created_conversation_id, last_answer_id, read_finished_run

CHAT_MODE = "internal_search"


def test_handwritten_conversation_streams(pipeshub: Pipeshub):
    events = list(
        pipeshub.conversations.stream_chat(
            query="Reply with the single word OK.", chat_mode=CHAT_MODE
        )
    )
    conversation_id = created_conversation_id(events)
    try:
        read_finished_run(events)

        read_finished_run(
            pipeshub.conversations.add_message_stream(
                conversation_id=conversation_id,
                query="Reply with the single word YES.",
                chat_mode=CHAT_MODE,
            )
        )

        conversation = pipeshub.conversations.get_conversation_by_id(
            conversation_id=conversation_id
        ).conversation
        answer_id = last_answer_id(conversation.messages)

        read_finished_run(
            pipeshub.conversations.regenerate_answer(
                conversation_id=conversation_id,
                message_id=answer_id,
                chat_mode=CHAT_MODE,
            )
        )

        feedback = pipeshub.conversations.update_message_feedback(
            conversation_id=conversation_id, message_id=answer_id, is_helpful=True
        )
        assert feedback.message_id == answer_id
        assert feedback.feedback.is_helpful is True
    finally:
        pipeshub.conversations.delete_conversation_by_id(
            conversation_id=conversation_id
        )

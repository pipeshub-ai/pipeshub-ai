"""Streams, feedback, fork and attachments on an agent conversation."""

from pipeshub_sdk import Pipeshub

from .errors import raises_status
from .streams import created_conversation_id, last_answer_id, read_finished_run

CHAT_MODE = "quick"


def test_handwritten_agent_conversation_streams(pipeshub: Pipeshub):
    agent_key = pipeshub.agents.create_agent(name="sdk-test-agent-streams").agent.key
    try:
        events = read_finished_run(
            pipeshub.agents.stream_agent_conversation(
                agent_key=agent_key,
                query="Reply with the single word OK.",
                chat_mode=CHAT_MODE,
            )
        )
        conversation_id = created_conversation_id(events)

        read_finished_run(
            pipeshub.agents.stream_agent_conversation_message(
                agent_key=agent_key,
                conversation_id=conversation_id,
                query="Reply with the single word YES.",
                chat_mode=CHAT_MODE,
            )
        )

        conversation = pipeshub.agents.get_agent_conversation_by_id(
            agent_key=agent_key, conversation_id=conversation_id
        ).conversation
        answer_id = last_answer_id(conversation.messages)

        read_finished_run(
            pipeshub.agents.regenerate_agent_conversation_message(
                agent_key=agent_key,
                conversation_id=conversation_id,
                message_id=answer_id,
                chat_mode=CHAT_MODE,
            )
        )

        feedback = pipeshub.agents.update_agent_conversation_message_feedback(
            agent_key=agent_key,
            conversation_id=conversation_id,
            message_id=answer_id,
            is_helpful=True,
        )
        assert feedback.message_id == answer_id
        assert feedback.feedback.is_helpful is True

        fork = pipeshub.agents.fork_agent_conversation(
            agent_key=agent_key, conversation_id=conversation_id, message_id=answer_id
        )
        assert fork.forked_from.conversation_id == conversation_id
        assert fork.forked_from.message_id == answer_id

        upload = pipeshub.agents.upload_agent_conversation_chat_attachments(
            agent_key=agent_key,
            conversation_id=conversation_id,
            files=[
                {
                    "file_name": "sdk-test-attachment.txt",
                    "content": b"PipesHub SDK test attachment.\n",
                }
            ],
        )
        assert upload.conversation_id == conversation_id
        assert upload.attachments, "upload returned no attachment"
        record_id = upload.attachments[0].record_id
        pipeshub.agents.delete_agent_conversation_chat_attachment(
            agent_key=agent_key, record_id=record_id
        )
        with raises_status(404):
            pipeshub.agents.delete_agent_conversation_chat_attachment(
                agent_key=agent_key, record_id=record_id
            )
    finally:
        # Deleting the agent removes its conversations with it.
        pipeshub.agents.delete_agent(agent_key=agent_key)

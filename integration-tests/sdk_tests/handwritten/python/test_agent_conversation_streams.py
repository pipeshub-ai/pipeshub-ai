"""Streaming chat, follow-up, regenerate and feedback on an agent conversation."""

from pipeshub_sdk import Pipeshub

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
    finally:
        # Deleting the agent removes its conversations with it.
        pipeshub.agents.delete_agent(agent_key=agent_key)

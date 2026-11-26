"""Start a conversation with a streamed chat, then rename, archive, unarchive and delete it."""

from pipeshub_sdk import Pipeshub

from .streams import created_conversation_id, read_finished_run


def test_handwritten_conversation_lifecycle(session: Pipeshub):
    events = list(
        session.conversations.stream_chat(
            query="Reply with the single word OK.", chat_mode="internal_search"
        )
    )
    conversation_id = created_conversation_id(events)
    try:
        read_finished_run(events)

        titled = session.conversations.update_conversation_title(
            conversation_id=conversation_id, title="sdk-test-title"
        )
        assert titled.conversation.title == "sdk-test-title"

        archived = session.conversations.archive_conversation(
            conversation_id=conversation_id
        )
        assert archived.status == "archived"

        unarchived = session.conversations.unarchive_conversation(
            conversation_id=conversation_id
        )
        assert unarchived.status == "unarchived"
    finally:
        session.conversations.delete_conversation_by_id(
            conversation_id=conversation_id
        )

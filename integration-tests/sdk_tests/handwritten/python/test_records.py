"""Upload a file, then read, rename, move, reindex and delete the record it creates."""

from pipeshub_sdk import Pipeshub

FILE_NAME = "sdk-test-record.txt"
FILE_CONTENT = b"PipesHub SDK test file.\n"


def test_handwritten_record_lifecycle(pipeshub: Pipeshub):
    kb_id = pipeshub.knowledge_base.create_knowledge_base(kb_name="sdk-test-records").id
    try:
        events = list(
            pipeshub.knowledge_base.upload_records(
                kb_id=kb_id,
                files=[{"file_name": FILE_NAME, "content": FILE_CONTENT}],
            )
        )
        succeeded = [event.data for event in events if event.event == "file:succeeded"]
        assert len(succeeded) == 1, (
            f"upload did not succeed: {[e.event for e in events]}"
        )
        record_id = succeeded[0]["recordId"]
        summary = next(event.data for event in events if event.event == "done")[
            "summary"
        ]
        assert summary == {"total": 1, "succeeded": 1, "failed": 0}

        buffer = pipeshub.knowledge_base.stream_record_buffer(record_id=record_id)
        assert buffer.read() == FILE_CONTENT

        updated = pipeshub.knowledge_base.update_record(
            record_id=record_id, record_name="sdk-test-record-renamed"
        )
        assert updated.record.id == record_id

        folder_id = pipeshub.knowledge_base.create_folder(
            kb_id=kb_id, folder_name="sdk-test-destination"
        ).id
        moved = pipeshub.knowledge_base.move_record(
            kb_id=kb_id, record_id=record_id, new_parent_id=folder_id
        )
        assert moved.success is True

        reindexed = pipeshub.knowledge_base.reindex_record(record_id=record_id)
        assert reindexed.success is True

        deleted = pipeshub.knowledge_base.delete_record(record_id=record_id)
        assert deleted.success is True
    finally:
        pipeshub.knowledge_base.delete_knowledge_base(kb_id=kb_id)

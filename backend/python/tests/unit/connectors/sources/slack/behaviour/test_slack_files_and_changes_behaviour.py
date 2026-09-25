"""Files, edits and deletes in the Slack Workspace connector, over a fake Slack workspace.

Covers attachments during sync, opening a message or file later (streaming),
and how edits and deletes show up when records are reindexed or opened.
"""

import hashlib
import json

import httpx
import pytest
from fastapi import HTTPException
from slack_behaviour_fakes import (
    BOT_TOKEN,
    FakeCheckpoints,
    FakeSlackStore,
    RateLimited,
    SlackError,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    BOB,
    GENERAL,
    boolean_filter,
    standard_workspace,
    workspace_config,
    workspace_connector,
)

from app.connectors.sources.slack.team.connector import SlackConnector
from app.models.entities import FileRecord, MessageRecord, ProgressStatus


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


def files(store) -> dict[str, FileRecord]:
    return {k: r for k, r in store.records.items() if isinstance(r, FileRecord)}


async def body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def streamed_blocks(connector, record) -> list[dict]:
    return json.loads(await body(await connector.stream_record(record)))["blocks"]


class TestAttachments:
    async def test_a_file_is_stored_as_an_attachment_of_its_message(self, workspace, store, checkpoints) -> None:
        content = b"%PDF quarterly numbers"
        fd = workspace.add_file("F0REPORT", "q3.pdf", content)
        ts = ts_minutes_ago(60)
        workspace.post(GENERAL, ts, ALICE, "see attached", files=[fd])
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        report = files(store)["F0REPORT"]
        message = store.records[ts]
        assert (report.record_name, report.mime_type, report.extension, report.size_in_bytes) == (
            "q3.pdf", "application/pdf", "pdf", len(content),
        )
        assert report.sha256_hash == hashlib.sha256(content).hexdigest()
        assert report.parent_external_record_id == ts
        assert report.record_group_id == message.record_group_id == store.record_groups[GENERAL].id
        assert report.inherit_permissions is True
        assert f"q3.pdf (record_id: {report.id})" in message.block_containers.blocks[0].data
        assert workspace.downloads_seen[0].headers["authorization"] == f"Bearer {BOT_TOKEN}"

    async def test_a_file_in_a_thread_reply_belongs_to_the_thread(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(120)
        workspace.post(GENERAL, parent, ALICE, "logs please")
        fd = workspace.add_file("F0LOG", "app.log", b"boom", mimetype="text/plain", filetype="text")
        workspace.reply(GENERAL, parent, ts_minutes_ago(110), BOB, "here", files=[fd])
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        log = files(store)["F0LOG"]
        thread_id = f"thread_{GENERAL}_{parent}"
        assert log.external_record_group_id == thread_id
        assert log.record_group_id == store.record_groups[thread_id].id
        assert store.records[log.parent_external_record_id].external_record_group_id == thread_id

    async def test_a_failed_download_still_stores_the_message_and_its_file(self, workspace, store, checkpoints) -> None:
        fd = workspace.add_file("F0BROKEN", "diagram.png", mimetype="image/png", filetype="png")
        workspace.downloads[fd["url_private_download"]] = httpx.Response(500, text="oops")
        ts = ts_minutes_ago(30)
        workspace.post(GENERAL, ts, ALICE, "diagram", files=[fd])
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert ts in store.records
        assert files(store)["F0BROKEN"].sha256_hash is None

    async def test_a_generic_binary_type_is_replaced_by_the_type_its_name_implies(self, workspace, store, checkpoints) -> None:
        fd = workspace.add_file("F0NOTES", "notes.md", b"# notes", mimetype="application/octet-stream", filetype="binary")
        workspace.post(GENERAL, ts_minutes_ago(30), ALICE, "notes", files=[fd])
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        notes = files(store)["F0NOTES"]
        assert (notes.mime_type, notes.extension) == ("text/markdown", "md")

    async def test_switching_off_file_indexing_keeps_files_but_does_not_index_them(self, workspace, store, checkpoints) -> None:
        fd = workspace.add_file("F0SKIP", "skip.pdf")
        ts = ts_minutes_ago(30)
        workspace.post(GENERAL, ts, ALICE, "file", files=[fd])
        config = workspace_config(filters={"indexing": {"values": {"files": boolean_filter(False)}}})
        connector, _ = await workspace_connector(store, checkpoints, config)

        await connector.run_sync()

        assert files(store)["F0SKIP"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert store.records[ts].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


class TestOpeningFiles:
    async def _synced_file(
        self, workspace: SlackWorkspace, store: FakeSlackStore, checkpoints: FakeCheckpoints, content: bytes = b"hello file",
    ) -> tuple[SlackConnector, FileRecord, dict]:
        fd = workspace.add_file("F0OPEN", "open.txt", content, mimetype="text/plain", filetype="text")
        workspace.post(GENERAL, ts_minutes_ago(30), ALICE, "file", files=[fd])
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        return connector, files(store)["F0OPEN"], fd

    async def test_opening_a_file_streams_its_bytes(self, workspace, store, checkpoints) -> None:
        connector, record, _ = await self._synced_file(workspace, store, checkpoints, b"x" * 20000)

        assert await body(await connector.stream_record(record)) == b"x" * 20000

    async def test_a_file_deleted_in_slack_is_reported_as_not_found(self, workspace, store, checkpoints) -> None:
        connector, record, _ = await self._synced_file(workspace, store, checkpoints)
        del workspace.files["F0OPEN"]

        with pytest.raises(HTTPException) as err:
            await body(await connector.stream_record(record))
        assert err.value.status_code == 404

    async def test_a_rate_limited_file_lookup_tells_the_caller_when_to_retry(self, workspace, store, checkpoints) -> None:
        connector, record, _ = await self._synced_file(workspace, store, checkpoints)
        workspace.fail("files.info", RateLimited(12))

        with pytest.raises(HTTPException) as err:
            await body(await connector.stream_record(record))
        assert err.value.status_code == 429
        assert err.value.headers["Retry-After"] == "12"

    async def test_a_sign_in_page_instead_of_the_file_asks_the_user_to_reconnect(self, workspace, store, checkpoints) -> None:
        connector, record, fd = await self._synced_file(workspace, store, checkpoints)
        workspace.downloads[fd["url_private_download"]] = httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html>sign in</html>",
        )

        with pytest.raises(HTTPException) as err:
            await body(await connector.stream_record(record))
        assert err.value.status_code == 409
        assert "Reconnect" in err.value.detail

    async def test_a_rate_limited_download_passes_on_retry_after(self, workspace, store, checkpoints) -> None:
        connector, record, fd = await self._synced_file(workspace, store, checkpoints)
        workspace.downloads[fd["url_private_download"]] = httpx.Response(429, headers={"retry-after": "7"})

        with pytest.raises(HTTPException) as err:
            await body(await connector.stream_record(record))
        assert (err.value.status_code, err.value.headers["Retry-After"]) == (429, "7")


class TestOpeningMessages:
    async def test_a_conversation_is_rebuilt_from_slack_with_its_attachment(self, workspace, store, checkpoints) -> None:
        fd = workspace.add_file("F0CHART", "chart.png", mimetype="image/png", filetype="png")
        first, second = ts_minutes_ago(30), ts_minutes_ago(29)
        workspace.post(GENERAL, first, ALICE, "chart incoming")
        workspace.post(GENERAL, second, BOB, "here", files=[fd])
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (burst,) = [r for r in store.messages() if r.external_record_id.startswith("burst_")]

        blocks = await streamed_blocks(connector, burst)

        assert [b["source_id"] for b in blocks] == [first, second]
        assert blocks[1]["children_records"][0]["child_id"] == files(store)["F0CHART"].id

    async def test_a_thread_conversation_is_rebuilt_from_its_replies(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(100)
        workspace.post(GENERAL, parent, ALICE, "thread")
        fd = workspace.add_file("F0TRACE", "trace.txt", mimetype="text/plain", filetype="text")
        replies = [ts_minutes_ago(80), ts_minutes_ago(79)]
        workspace.reply(GENERAL, parent, replies[0], BOB, "trace", files=[fd])
        workspace.reply(GENERAL, parent, replies[1], ALICE, "thanks")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (burst,) = [r for r in store.messages() if r.start_ts == replies[0]]

        blocks = await streamed_blocks(connector, burst)

        assert [b["source_id"] for b in blocks] == replies
        assert "thanks" in blocks[1]["data"]
        assert blocks[0]["children_records"][0]["child_id"] == files(store)["F0TRACE"].id

    async def test_opening_an_edited_message_shows_its_current_text(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(60)
        msg = workspace.post(GENERAL, ts, ALICE, "meeting at 3")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        msg.update(text="meeting at 4", edited={"user": ALICE, "ts": ts_minutes_ago(5)})
        (block,) = await streamed_blocks(connector, store.records[ts])

        assert "meeting at 4" in block["data"]

    async def test_opening_a_message_deleted_in_slack_is_reported_as_not_found(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(60)
        workspace.post(GENERAL, ts, ALICE, "oops")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.history[GENERAL].clear()
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(store.records[ts])
        assert err.value.status_code == 404

    async def test_a_slack_outage_while_opening_a_message_is_not_reported_as_a_deletion(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(60)
        workspace.post(GENERAL, ts, ALICE, "still here")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.history", SlackError("internal_error"))
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(store.records[ts])
        assert err.value.status_code != 404


class TestReindexingChanges:
    async def test_reindexing_picks_up_an_edit(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(60)
        msg = workspace.post(GENERAL, ts, ALICE, "deploy on friday")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        stored = store.records[ts]

        msg.update(text="deploy on monday", edited={"user": ALICE, "ts": ts_minutes_ago(2)})
        await connector.reindex_records([stored])

        (updated,) = store.content_updates
        assert isinstance(updated, MessageRecord)
        assert (updated.id, updated.version, updated.is_edited) == (stored.id, stored.version + 1, True)
        assert "monday" in updated.content
        assert updated.root_record_group_id == store.record_groups[GENERAL].id
        assert store.reindexed == []

    async def test_reindexing_an_unchanged_message_only_reindexes_it(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(60)
        workspace.post(GENERAL, ts, ALICE, "unchanged")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        await connector.reindex_records([store.records[ts]])

        assert store.content_updates == []
        assert [r.external_record_id for r in store.reindexed] == [ts]

    async def test_reindexing_a_message_deleted_in_slack_keeps_the_record(self, workspace, store, checkpoints) -> None:
        ts = ts_minutes_ago(60)
        workspace.post(GENERAL, ts, ALICE, "soon gone")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.history[GENERAL].clear()
        await connector.reindex_records([store.records[ts]])

        assert store.content_updates == []
        assert [r.external_record_id for r in store.reindexed] == [ts]

"""The personal Slack connector waits out Slack's 429s while syncing and never while a user waits."""

import pytest
from fastapi import HTTPException
from slack_behaviour_fakes import RateLimited, SlackWorkspace, ts_minutes_ago
from slack_behaviour_setup import ALICE, BOB, personal_connector, standard_workspace

DM_BOB = "D0BOB"
RETRY_AFTER = 7


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    standard_workspace(slack)
    slack.auth_user_id = ALICE
    slack.add_channel(DM_BOB, "", kind="im", members=[ALICE, BOB], dm_with=BOB)
    return slack


def checkpoint(checkpoints) -> dict | None:
    return checkpoints.value(DM_BOB)


async def body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


def seed(workspace: SlackWorkspace) -> tuple[str, str]:
    parent, reply = ts_minutes_ago(30), ts_minutes_ago(20)
    workspace.post(DM_BOB, parent, BOB, "question")
    workspace.reply(DM_BOB, parent, reply, ALICE, "answer")
    return parent, reply


class TestSyncWaitsOutRateLimits:
    @pytest.mark.parametrize(
        "method",
        ["users.list", "conversations.list", "conversations.history", "conversations.replies"],
    )
    async def test_a_429_is_retried_after_its_retry_after_and_the_sync_still_completes(
        self, method, workspace, store, checkpoints, waits,
    ) -> None:
        parent, reply = seed(workspace)
        workspace.fail(method, RateLimited(RETRY_AFTER))
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert any(RETRY_AFTER <= w <= RETRY_AFTER + 0.25 for w in waits)
        assert len(workspace.calls_to(method)) >= 2
        assert connector._full_sync_read_failed is False
        assert checkpoint(checkpoints) is not None

    async def test_a_429_on_the_file_lookup_is_retried_when_checking_for_changes(
        self, workspace, store, checkpoints, waits,
    ) -> None:
        fd = workspace.add_file("F0DOC", "doc.txt", b"doc", mimetype="text/plain", filetype="text")
        workspace.post(DM_BOB, ts_minutes_ago(30), BOB, "file", files=[fd])
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        record = next(r for r in store.records.values() if getattr(r, "external_record_id", None) == "F0DOC")

        workspace.fail("files.info", RateLimited(RETRY_AFTER))
        await connector._check_updated(record)

        assert any(RETRY_AFTER <= w <= RETRY_AFTER + 0.25 for w in waits)

    async def test_retries_that_run_out_leave_the_checkpoint_and_the_run_incomplete(
        self, workspace, store, checkpoints, waits,
    ) -> None:
        seed(workspace)
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        before, kept = checkpoint(checkpoints), set(store.records)

        newer = ts_minutes_ago(2)
        workspace.post(DM_BOB, newer, BOB, "newer")
        workspace.fail("conversations.history", RateLimited(RETRY_AFTER), times=None,
                       when=lambda p: p.get("channel") == DM_BOB)
        await connector.run_sync()

        assert len(waits) >= 3  # max_retries on the channel read
        assert connector._full_sync_read_failed is True
        assert connector.full_sync_coverage().complete is False
        assert checkpoint(checkpoints) == before
        assert kept <= set(store.records)


class TestStreamingDoesNotWait:
    async def test_a_rate_limited_message_is_not_retried_and_passes_on_retry_after(
        self, workspace, store, checkpoints, waits,
    ) -> None:
        seed(workspace)
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        record = next(r for r in store.messages())
        workspace.fail("conversations.history", RateLimited(RETRY_AFTER), times=None)
        waits.clear()

        with pytest.raises(HTTPException) as err:
            await body(await connector.stream_record(record))

        assert (err.value.status_code, err.value.headers["Retry-After"]) == (429, str(RETRY_AFTER))
        assert waits == []

    async def test_a_rate_limited_file_lookup_is_not_retried(self, workspace, store, checkpoints, waits) -> None:
        fd = workspace.add_file("F0DOC", "doc.txt", b"doc", mimetype="text/plain", filetype="text")
        workspace.post(DM_BOB, ts_minutes_ago(30), BOB, "file", files=[fd])
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        record = next(r for r in store.records.values() if getattr(r, "external_record_id", None) == "F0DOC")
        workspace.fail("files.info", RateLimited(RETRY_AFTER), times=None)
        waits.clear()

        with pytest.raises(HTTPException) as err:
            await body(await connector.stream_record(record))

        assert err.value.status_code == 429
        assert waits == []

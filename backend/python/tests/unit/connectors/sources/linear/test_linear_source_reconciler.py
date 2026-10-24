"""Tests for SourceReconciler: removing what PipesHub still holds and Linear no longer
has in the sync's scope, and never on the strength of a listing that was not read to
its end."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.linear import source_reconciler
from app.connectors.sources.linear.api_gateway import (
    LinearAuthError,
    LinearGateway,
    LinearTransientError,
)
from app.connectors.sources.linear.source_reconciler import (
    CHEAP_PASS_CALLS,
    EXPENSIVE_PASS_INTERVAL_MS,
    SYNC_POINT_KEY,
    SourceReconciler,
)
from app.models.entities import RecordType
from app.sources.client.graphql.response import GraphQLResponse

CONNECTOR_ID = "linear-conn-1"
NOW = 1_700_000_000_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(data) -> GraphQLResponse:
    return GraphQLResponse(success=True, data=data)


def _failed(message="Internal error", status=500) -> GraphQLResponse:
    return GraphQLResponse(success=False, message=message, status_code=status)


def _page(root, nodes, has_next=False, end_cursor=None) -> dict:
    return _ok({root: {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}}})


def _record(record_id, external_id, record_type=RecordType.TICKET, team="team-1", parent=None,
            is_placeholder=False, is_deleted=False) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id, external_record_id=external_id, record_type=record_type,
        external_record_group_id=team, parent_external_record_id=parent,
        is_placeholder=is_placeholder, is_deleted=is_deleted,
    )


class FakeSyncPoint:
    def __init__(self, initial=None) -> None:
        self.store = dict(initial or {})

    async def read_sync_point(self, key) -> dict | None:
        return self.store.get(key)

    async def update_sync_point(self, key, data) -> None:
        self.store[key] = dict(data)


class Linear:
    """A scripted Linear: answers ``execute_operation`` and records what was asked."""

    def __init__(self, respond) -> None:
        self._respond = respond
        self.requests = []

    async def execute_operation(self, operation, variables) -> GraphQLResponse:
        self.requests.append((operation, dict(variables)))
        return self._respond(operation, variables)

    def asked(self, operation) -> list:
        return [variables for name, variables in self.requests if name == operation]


def _make(respond, stored=None, record_groups=None, parents=None, sync_state=None, now=NOW) -> SimpleNamespace:
    """``stored`` maps a RecordType to this connector's records of that type."""
    stored = stored or {}
    linear = Linear(respond)
    sleep = AsyncMock()
    gateway = LinearGateway(AsyncMock(return_value=linear), MagicMock(), sleep=sleep)

    processor = MagicMock()
    processor.get_records_by_record_type = AsyncMock(
        side_effect=lambda connector_id, record_type: list(stored.get(record_type, [])))
    processor.get_nodes_by_filters = AsyncMock(return_value=record_groups or [])
    processor.get_record_by_external_id = AsyncMock(
        side_effect=lambda connector_id, external_id: (parents or {}).get(external_id))

    remover = MagicMock()
    remover.remove_record = AsyncMock()
    remover.remove_group = AsyncMock(return_value=0)

    sync_point = FakeSyncPoint({SYNC_POINT_KEY: sync_state} if sync_state is not None else None)
    clock = MagicMock(return_value=now)
    reconciler = SourceReconciler(gateway, processor, remover, sync_point, CONNECTOR_ID, MagicMock(), clock)
    return SimpleNamespace(
        reconciler=reconciler, linear=linear, processor=processor, remover=remover,
        sync_point=sync_point, clock=clock, sleep=sleep,
    )


def _removed(env) -> list:
    return [c.args[0].external_record_id for c in env.remover.remove_record.await_args_list]


def _nothing_to_list(operation, variables) -> GraphQLResponse:
    root = {"issueIds": "issues", "projectIds": "projects",
            "attachmentIds": "attachments", "documentIds": "documents"}[operation]
    return _page(root, [])


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


class TestReconcileIssues:
    @pytest.mark.asyncio
    async def test_issue_missing_from_the_listing_is_confirmed_by_id_before_removal(self) -> None:
        live = _record("node-live", "issue-live")
        purged = _record("node-purged", "issue-purged")

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            if "team" in variables["filter"]:
                return _page("issues", [{"id": "issue-live", "trashed": False, "team": {"id": "team-1"}}])
            return _page("issues", [])  # asked by id: Linear does not have it

        env = _make(respond, stored={RecordType.TICKET: [live, purged]})

        counts = await env.reconciler.run({"team-1"})

        assert counts == {"issues": 1, "attachments": 0, "documents": 0}
        assert _removed(env) == ["issue-purged"]
        # First the listing of the synced teams, then the lookup of the suspect alone.
        assert env.linear.asked("issueIds") == [
            {"filter": {"team": {"id": {"in": ["team-1"]}}}, "includeArchived": True, "first": 250},
            {"filter": {"id": {"in": ["issue-purged"]}}, "includeArchived": True, "first": 250},
        ]

    @pytest.mark.asyncio
    async def test_issue_that_turns_up_on_confirmation_is_kept(self) -> None:
        # Created, or moved between synced teams, after the listing passed its page.
        late = _record("node-late", "issue-late")

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            if "team" in variables["filter"]:
                return _page("issues", [])
            return _page("issues", [{"id": "issue-late", "trashed": False, "team": {"id": "team-2"}}])

        env = _make(respond, stored={RecordType.TICKET: [late]})

        counts = await env.reconciler.run({"team-1", "team-2"})

        assert counts["issues"] == 0
        env.remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("found", [
        {"id": "issue-x", "trashed": False, "team": {"id": "team-9"}},  # moved out of the sync
        {"id": "issue-x", "trashed": True, "team": {"id": "team-1"}},   # in the trash
        {"id": "issue-x", "trashed": False, "team": None},
        {"id": "issue-x", "trashed": False},
    ])
    async def test_issue_confirmed_out_of_scope_is_removed(self, found) -> None:
        record = _record("node-x", "issue-x")

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            if "team" in variables["filter"]:
                return _page("issues", [])
            return _page("issues", [found])

        env = _make(respond, stored={RecordType.TICKET: [record]})

        assert (await env.reconciler.run({"team-1"}))["issues"] == 1
        assert _removed(env) == ["issue-x"]

    @pytest.mark.asyncio
    async def test_trashed_issue_in_the_listing_is_a_suspect_not_live(self) -> None:
        record = _record("node-t", "issue-trashed")
        trashed = {"id": "issue-trashed", "trashed": True, "team": {"id": "team-1"}}

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            return _page("issues", [trashed])

        env = _make(respond, stored={RecordType.TICKET: [record]})

        assert (await env.reconciler.run({"team-1"}))["issues"] == 1
        assert len(env.linear.asked("issueIds")) == 2

    @pytest.mark.asyncio
    async def test_nothing_removed_when_the_listing_raises(self) -> None:
        records = [_record(f"node-{i}", f"issue-{i}") for i in range(3)]
        calls = {"n": 0}

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            calls["n"] += 1
            if calls["n"] == 1:
                # The first page lists only one issue and promises more...
                return _page("issues", [{"id": "issue-0", "trashed": False}], True, "cursor-1")
            return _failed()  # ...which never arrive.

        env = _make(respond, stored={RecordType.TICKET: records}, sync_state={"last_run": 1, "calls": 2})

        with pytest.raises(LinearTransientError):
            await env.reconciler.run({"team-1"})

        # issue-1 and issue-2 were not on the page that was read. They are not gone.
        env.remover.remove_record.assert_not_awaited()
        # The pass did not happen: its record is left as it was.
        assert env.sync_point.store[SYNC_POINT_KEY] == {"last_run": 1, "calls": 2}

    @pytest.mark.asyncio
    async def test_nothing_removed_when_the_confirmation_raises(self) -> None:
        record = _record("node-x", "issue-x")

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            if "team" in variables["filter"]:
                return _page("issues", [])
            return GraphQLResponse(success=False, message="Unauthorized", status_code=401)

        env = _make(respond, stored={RecordType.TICKET: [record]})

        with pytest.raises(LinearAuthError):
            await env.reconciler.run({"team-1"})

        env.remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_listing_is_read_to_its_end_before_anything_is_compared(self) -> None:
        records = [_record("node-a", "issue-a"), _record("node-b", "issue-b")]

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            if "team" not in variables["filter"]:
                return _page("issues", [])
            if variables.get("after") is None:
                return _page("issues", [{"id": "issue-a", "trashed": False}], True, "cursor-1")
            return _page("issues", [{"id": "issue-b", "trashed": False}])

        env = _make(respond, stored={RecordType.TICKET: records})

        assert (await env.reconciler.run({"team-1"}))["issues"] == 0
        assert [v.get("after") for v in env.linear.asked("issueIds")] == [None, "cursor-1"]
        env.remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_live_records_of_synced_teams_are_compared(self) -> None:
        stored = [
            _record("node-1", "issue-other-team", team="team-9"),
            _record("node-2", "issue-stub", is_placeholder=True),
            _record("node-3", "issue-deleted", is_deleted=True),
            _record("node-4", "issue-no-team", team=None),
        ]
        env = _make(lambda operation, variables: _nothing_to_list(operation, variables),
                    stored={RecordType.TICKET: stored})

        assert (await env.reconciler.run({"team-1"}))["issues"] == 0
        # Nothing to compare, so Linear is not even asked.
        assert env.linear.asked("issueIds") == []
        env.remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_suspects_are_confirmed_in_batches(self) -> None:
        records = [_record(f"node-{i}", f"issue-{i}") for i in range(5)]

        def respond(operation, variables) -> GraphQLResponse:
            if operation != "issueIds":
                return _nothing_to_list(operation, variables)
            return _page("issues", [])

        env = _make(respond, stored={RecordType.TICKET: records})

        original = source_reconciler.CONFIRM_BATCH
        source_reconciler.CONFIRM_BATCH = 2
        try:
            counts = await env.reconciler.run({"team-1"})
        finally:
            source_reconciler.CONFIRM_BATCH = original

        assert counts["issues"] == 5
        confirmations = [v["filter"]["id"]["in"] for v in env.linear.asked("issueIds") if "id" in v["filter"]]
        assert confirmations == [["issue-0", "issue-1"], ["issue-2", "issue-3"], ["issue-4"]]

    @pytest.mark.asyncio
    async def test_scope_is_sent_sorted(self) -> None:
        env = _make(lambda operation, variables: _nothing_to_list(operation, variables),
                    stored={RecordType.TICKET: [_record("node-1", "issue-1", team="team-b")]})

        await env.reconciler.run(["team-b", "team-a", "team-b"])

        assert env.linear.asked("issueIds")[0]["filter"] == {"team": {"id": {"in": ["team-a", "team-b"]}}}


# ---------------------------------------------------------------------------
# Attachments and documents
# ---------------------------------------------------------------------------


class TestReconcileAttachmentsAndDocuments:
    @pytest.mark.asyncio
    async def test_deleted_issue_attachment_is_removed(self) -> None:
        kept = _record("node-kept", "att-kept", RecordType.LINK, parent="issue-1")
        gone = _record("node-gone", "att-gone", RecordType.LINK, parent="issue-1")
        issue = _record("node-issue", "issue-1")

        def respond(operation, variables) -> GraphQLResponse:
            if operation == "attachmentIds":
                return _page("attachments", [{"id": "att-kept"}])
            return _nothing_to_list(operation, variables)

        env = _make(respond, stored={RecordType.LINK: [kept, gone]}, parents={"issue-1": issue})

        counts = await env.reconciler.run({"team-1"})

        assert counts == {"issues": 0, "attachments": 1, "documents": 0}
        assert _removed(env) == ["att-gone"]
        # An archived issue's attachments are archived with it: they are asked for too
        assert env.linear.asked("attachmentIds") == [{"first": 250, "includeArchived": True}]

    @pytest.mark.asyncio
    async def test_project_links_are_not_attachments(self) -> None:
        # A project's external link is a LINK record too, but it is never in the
        # attachments listing; it is settled when its project is read.
        project_link = _record("node-plink", "link-1", RecordType.LINK, parent="proj-1")
        second_link = _record("node-plink-2", "link-2", RecordType.LINK, parent="proj-1")
        project = _record("node-project", "proj-1", RecordType.PROJECT)

        env = _make(lambda operation, variables: _nothing_to_list(operation, variables),
                    stored={RecordType.LINK: [project_link, second_link]}, parents={"proj-1": project})

        assert (await env.reconciler.run({"team-1"}))["attachments"] == 0
        env.remover.remove_record.assert_not_awaited()
        # The parent's type is looked up once per parent.
        env.processor.get_record_by_external_id.assert_awaited_once_with(CONNECTOR_ID, "proj-1")

    @pytest.mark.asyncio
    async def test_attachment_whose_parent_is_unknown_is_removed(self) -> None:
        orphan = _record("node-orphan", "att-orphan", RecordType.LINK, parent="issue-gone")
        parentless = _record("node-parentless", "att-parentless", RecordType.LINK, parent=None)

        env = _make(lambda operation, variables: _nothing_to_list(operation, variables),
                    stored={RecordType.LINK: [orphan, parentless]})

        assert (await env.reconciler.run({"team-1"}))["attachments"] == 2
        assert _removed(env) == ["att-orphan", "att-parentless"]

    @pytest.mark.asyncio
    async def test_deleted_document_is_removed(self) -> None:
        kept = _record("node-kept", "doc-kept", RecordType.WEBPAGE)
        gone = _record("node-gone", "doc-gone", RecordType.WEBPAGE)

        trashed = _record("node-trashed", "doc-trashed", RecordType.WEBPAGE)

        def respond(operation, variables) -> GraphQLResponse:
            if operation == "documentIds":
                # Deleting a document in Linear trashes it; archived ones are listed too
                return _page("documents", [{"id": "doc-kept", "trashed": False}, {"id": None},
                                           {"id": "doc-trashed", "trashed": True}])
            return _nothing_to_list(operation, variables)

        env = _make(respond, stored={RecordType.WEBPAGE: [kept, gone, trashed]})

        counts = await env.reconciler.run({"team-1"})

        assert counts == {"issues": 0, "attachments": 0, "documents": 2}
        assert _removed(env) == ["doc-gone", "doc-trashed"]
        assert env.linear.asked("documentIds") == [{"first": 250, "includeArchived": True}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation,record_type", [
        ("attachmentIds", RecordType.LINK),
        ("documentIds", RecordType.WEBPAGE),
    ])
    async def test_failed_listing_removes_nothing(self, operation, record_type) -> None:
        record = _record("node-1", "ext-1", record_type, parent="issue-1")

        def respond(asked, variables) -> GraphQLResponse:
            if asked == operation:
                return _failed()
            return _nothing_to_list(asked, variables)

        env = _make(respond, stored={record_type: [record]})

        with pytest.raises(LinearTransientError):
            await env.reconciler.run({"team-1"})

        env.remover.remove_record.assert_not_awaited()
        assert SYNC_POINT_KEY not in env.sync_point.store
        # Retried three times before giving up.
        assert len(env.linear.asked(operation)) == 4

    @pytest.mark.asyncio
    async def test_types_with_nothing_stored_are_not_listed(self) -> None:
        env = _make(lambda operation, variables: _nothing_to_list(operation, variables))

        counts = await env.reconciler.run({"team-1"})

        assert counts == {"issues": 0, "attachments": 0, "documents": 0}
        assert env.linear.requests == []


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class TestRunProjects:
    @pytest.mark.asyncio
    async def test_trashed_purged_and_unshared_projects_are_removed(self) -> None:
        stored = [
            _record("node-live", "proj-live", RecordType.PROJECT),
            _record("node-trashed", "proj-trashed", RecordType.PROJECT),
            _record("node-purged", "proj-purged", RecordType.PROJECT),
            _record("node-elsewhere", "proj-elsewhere", RecordType.PROJECT, team="team-9"),
        ]

        def respond(operation, variables) -> GraphQLResponse:
            assert operation == "projectIds"
            return _page("projects", [
                {"id": "proj-live", "trashed": False},
                {"id": "proj-trashed", "trashed": True},
            ])

        env = _make(respond, stored={RecordType.PROJECT: stored})

        removed, listed, kept = await env.reconciler.run_projects({"team-1"})

        assert removed == 2
        assert _removed(env) == ["proj-trashed", "proj-purged"]
        # What Linear still has, and the stored records that stay, go back to the caller
        # so it can spot projects whose links or milestones changed.
        assert list(listed) == ["proj-live"]
        assert {key: record.id for key, record in kept.items()} == {"proj-live": "node-live"}
        assert env.linear.asked("projectIds") == [{
            "filter": {"accessibleTeams": {"some": {"id": {"in": ["team-1"]}}}},
            "includeArchived": True,
            "first": 25,
        }]

    @pytest.mark.asyncio
    async def test_failed_listing_removes_nothing(self) -> None:
        env = _make(lambda operation, variables: _failed(),
                    stored={RecordType.PROJECT: [_record("node-1", "proj-1", RecordType.PROJECT)]})

        with pytest.raises(LinearTransientError):
            await env.reconciler.run_projects({"team-1"})

        env.remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_partial_listing_removes_nothing(self) -> None:
        stored = [_record(f"node-{i}", f"proj-{i}", RecordType.PROJECT) for i in range(3)]
        calls = {"n": 0}

        def respond(operation, variables) -> GraphQLResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return _page("projects", [{"id": "proj-0", "trashed": False}], True, "cursor-1")
            return GraphQLResponse(success=False, message="Unauthorized", status_code=401)

        env = _make(respond, stored={RecordType.PROJECT: stored})

        with pytest.raises(LinearAuthError):
            await env.reconciler.run_projects({"team-1"})

        env.remover.remove_record.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("team_ids", [set(), []])
    async def test_no_synced_team(self, team_ids) -> None:
        env = _make(lambda operation, variables: _failed())

        assert await env.reconciler.run_projects(team_ids) == (0, {}, {})

        env.processor.get_records_by_record_type.assert_not_awaited()
        assert env.linear.requests == []

    @pytest.mark.asyncio
    async def test_does_not_touch_the_record_of_the_last_pass(self) -> None:
        env = _make(lambda operation, variables: _page("projects", []),
                    stored={RecordType.PROJECT: [_record("node-1", "proj-1", RecordType.PROJECT)]},
                    sync_state={"last_run": 5, "calls": 7})

        await env.reconciler.run_projects({"team-1"})

        assert env.sync_point.store[SYNC_POINT_KEY] == {"last_run": 5, "calls": 7}


# ---------------------------------------------------------------------------
# The pass and its cadence
# ---------------------------------------------------------------------------


class TestRunBookkeeping:
    @pytest.mark.asyncio
    async def test_records_when_it_ran_and_what_it_cost(self) -> None:
        stored = {
            RecordType.TICKET: [_record("node-i", "issue-1"), _record("node-j", "issue-2")],
            RecordType.LINK: [_record("node-a", "att-1", RecordType.LINK, parent="issue-1")],
            RecordType.WEBPAGE: [_record("node-d", "doc-1", RecordType.WEBPAGE)],
        }

        def respond(operation, variables) -> GraphQLResponse:
            if operation == "issueIds" and "team" in variables["filter"]:
                if variables.get("after") is None:
                    return _page("issues", [{"id": "issue-1", "trashed": False}], True, "cursor-1")
                return _page("issues", [])
            if operation == "issueIds":
                return _page("issues", [])
            if operation == "attachmentIds":
                return _page("attachments", [{"id": "att-1"}])
            return _page("documents", [{"id": "doc-1"}])

        env = _make(respond, stored=stored)

        counts = await env.reconciler.run({"team-1"})

        assert counts == {"issues": 1, "attachments": 0, "documents": 0}
        # Two listing pages, one confirmation, one page each of attachments and documents.
        assert env.sync_point.store[SYNC_POINT_KEY] == {"last_run": NOW, "calls": 5}

    @pytest.mark.asyncio
    async def test_call_count_starts_again_on_every_pass(self) -> None:
        env = _make(lambda operation, variables: _nothing_to_list(operation, variables),
                    stored={RecordType.WEBPAGE: [_record("node-d", "doc-1", RecordType.WEBPAGE)]})

        await env.reconciler.run({"team-1"})
        await env.reconciler.run({"team-1"})

        assert env.sync_point.store[SYNC_POINT_KEY]["calls"] == 1

    @pytest.mark.asyncio
    async def test_no_synced_team_does_nothing_at_all(self) -> None:
        env = _make(lambda operation, variables: _failed(),
                    stored={RecordType.TICKET: [_record("node-i", "issue-1")]})

        assert await env.reconciler.run(set()) == {"issues": 0, "attachments": 0, "documents": 0}

        env.processor.get_records_by_record_type.assert_not_awaited()
        env.remover.remove_record.assert_not_awaited()
        assert env.sync_point.store == {}


class TestDue:
    @pytest.mark.asyncio
    async def test_forced_pass_always_runs(self) -> None:
        recent_and_expensive = {"last_run": NOW - 1000, "calls": CHEAP_PASS_CALLS + 1000}
        env = _make(None, sync_state=recent_and_expensive)
        assert await env.reconciler.due(forced=True) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [None, {}, {"calls": 5000}, {"last_run": 0, "calls": 5000}])
    async def test_never_ran_before(self, state) -> None:
        env = _make(None, sync_state=state)
        assert await env.reconciler.due(forced=False) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("calls", [0, None, 1, CHEAP_PASS_CALLS])
    async def test_cheap_pass_runs_on_every_sync(self, calls) -> None:
        env = _make(None, sync_state={"last_run": NOW - 1000, "calls": calls})
        assert await env.reconciler.due(forced=False) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("age_ms,expected", [
        (1000, False),
        (EXPENSIVE_PASS_INTERVAL_MS - 1, False),
        (EXPENSIVE_PASS_INTERVAL_MS, True),
        (EXPENSIVE_PASS_INTERVAL_MS + 1, True),
    ])
    async def test_expensive_pass_runs_once_a_day(self, age_ms, expected) -> None:
        env = _make(None, sync_state={"last_run": NOW - age_ms, "calls": CHEAP_PASS_CALLS + 1})
        assert await env.reconciler.due(forced=False) is expected

    def test_thresholds(self) -> None:
        assert CHEAP_PASS_CALLS == 200
        assert EXPENSIVE_PASS_INTERVAL_MS == 24 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


def _groups(*team_ids) -> list:
    return [{"externalGroupId": team_id} for team_id in team_ids]


class TestRemoveStaleTeams:
    @pytest.mark.asyncio
    async def test_synced_teams_are_left_alone_without_asking(self) -> None:
        env = _make(lambda operation, variables: _failed(), record_groups=_groups("team-1", "team-2"))

        removed = await env.reconciler.remove_stale_teams({"team-1", "team-2"})

        assert removed == []
        assert env.linear.requests == []
        env.remover.remove_group.assert_not_awaited()
        env.processor.get_nodes_by_filters.assert_awaited_once_with(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": CONNECTOR_ID},
            ["externalGroupId", "isDeletedAtSource"])

    @pytest.mark.asyncio
    async def test_a_team_already_marked_deleted_is_not_removed_again(self) -> None:
        """With the trash on, a removed team's group stays, marked, until its records are purged."""
        groups = [{"externalGroupId": "team-gone", "isDeletedAtSource": True}]
        env = _make(lambda operation, variables: _failed(), record_groups=groups)

        removed = await env.reconciler.remove_stale_teams(set())

        assert removed == []
        assert env.linear.requests == []
        env.remover.remove_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_team_the_filter_leaves_out_is_kept(self) -> None:
        """A deselected team, or the unsynced parent of a synced team, is still in Linear.
        Nothing of it is removed: the full sync that a filter change runs unlinks it."""
        env = _make(
            lambda operation, variables: _ok({"team": {"id": variables["id"], "archivedAt": None}}),
            record_groups=_groups("team-1", "team-off"),
        )
        env.remover.remove_group_records = AsyncMock(return_value=0)

        removed = await env.reconciler.remove_stale_teams({"team-1"})

        assert removed == []
        env.remover.remove_group.assert_not_awaited()
        env.remover.remove_group_records.assert_not_awaited()
        assert env.linear.asked("team") == [{"id": "team-off"}]

    @pytest.mark.asyncio
    async def test_team_linear_no_longer_has_is_removed(self) -> None:
        def respond(operation, variables) -> GraphQLResponse:
            assert operation == "team"
            return GraphQLResponse(success=False, message="Entity not found: Team", status_code=400)

        env = _make(respond, record_groups=_groups("team-gone"))

        removed = await env.reconciler.remove_stale_teams(set())

        assert removed == ["team-gone"]
        assert env.linear.asked("team") == [{"id": "team-gone"}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [{"team": None}, {}, {"team": {}}])
    async def test_team_that_comes_back_empty_is_removed(self, data) -> None:
        env = _make(lambda operation, variables: _ok(data), record_groups=_groups("team-hidden"))

        assert await env.reconciler.remove_stale_teams(set()) == ["team-hidden"]

    @pytest.mark.asyncio
    async def test_archived_team_is_removed(self) -> None:
        env = _make(
            lambda operation, variables: _ok({"team": {"id": "team-old", "archivedAt": "2024-06-01T00:00:00.000Z"}}),
            record_groups=_groups("team-old"),
        )

        assert await env.reconciler.remove_stale_teams(set()) == ["team-old"]
        env.remover.remove_group.assert_awaited_once_with("team-old")

    @pytest.mark.asyncio
    async def test_team_linear_still_returns_is_kept_whatever_the_listing_said(self) -> None:
        env = _make(
            lambda operation, variables: _ok({"team": {"id": variables["id"], "archivedAt": None}}),
            record_groups=_groups("team-missed"),
        )

        removed = await env.reconciler.remove_stale_teams(set())

        assert removed == []
        env.remover.remove_group.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response,expected", [
        (_failed(), LinearTransientError),
        (GraphQLResponse(success=False, message="Unauthorized", status_code=401), LinearAuthError),
    ])
    async def test_team_is_not_removed_when_linear_cannot_be_asked(self, response, expected) -> None:
        env = _make(lambda operation, variables: response, record_groups=_groups("team-1"))

        with pytest.raises(expected):
            await env.reconciler.remove_stale_teams(set())

        env.remover.remove_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_each_stored_team_gets_its_own_answer(self) -> None:
        answers = {
            "team-gone": GraphQLResponse(success=False, message="Entity not found: Team", status_code=400),
            "team-archived": _ok({"team": {"id": "team-archived", "archivedAt": "2024-06-01T00:00:00.000Z"}}),
            "team-missed": _ok({"team": {"id": "team-missed", "archivedAt": None}}),
            "team-off": _ok({"team": {"id": "team-off", "archivedAt": None}}),
        }
        env = _make(
            lambda operation, variables: answers[variables["id"]],
            record_groups=[*_groups("team-synced", "team-off", "team-gone", "team-archived", "team-missed"),
                           {"externalGroupId": None}, {}],
        )

        removed = await env.reconciler.remove_stale_teams({"team-synced"})

        assert removed == ["team-gone", "team-archived"]
        assert [c.args[0] for c in env.remover.remove_group.await_args_list] == removed
        assert [v["id"] for v in env.linear.asked("team")] == ["team-off", "team-gone", "team-archived", "team-missed"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", [None, []])
    async def test_nothing_stored(self, stored) -> None:
        env = _make(lambda operation, variables: _failed())
        env.processor.get_nodes_by_filters.return_value = stored

        assert await env.reconciler.remove_stale_teams({"team-1"}) == []

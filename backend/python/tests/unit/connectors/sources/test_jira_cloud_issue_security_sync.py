"""Jira Cloud connector: issue security levels become grants on the issue.

Fixtures are shaped like Jira Cloud's answers (project/{key}/issuesecuritylevelscheme,
issuesecurityschemes/level/member, issue.fields.security); the same shapes come from the connector simulator.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.atlassian.core.jira_issue_security import (
    LEVEL_MEMBERS_UNREAD,
    UNREADABLE_AUTH,
    UNREADABLE_PERMISSION,
    UNREADABLE_SCOPE,
    UNREADABLE_TRANSIENT,
    IssueSecurityContext,
    SchemeKnowledge,
    apply_issue_access_settled,
    browse_issue_holders,
    unreadable_reason,
    user_grant_key,
)
from app.connectors.sources.atlassian.core.read_outcome import JiraReadError
from app.connectors.sources.atlassian.jira_cloud.connector import (
    ANONYMOUS_GATE_KEY,
    ANONYMOUS_READ_PAGES,
    ISSUE_PLACEMENT,
    ISSUE_SEARCH_FIELDS,
    UNREAD_SYNCS_BEFORE_CLOSE,
    JiraConnector,
    _ParentLevel,
)
from app.models.entities import AppUser, RecordType, TicketRecord
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import NotificationOutcome

PROJECT_ID = "p-1"
SCHEME = {"id": 10002, "name": "PROJ issue security scheme", "levels": [{"id": "10102", "name": "Leadership"}]}
MEMBERS_PAGE_1 = {
    "startAt": 0, "maxResults": 2, "total": 3, "isLast": False,
    "values": [
        {"id": "10001", "issueSecurityLevelId": "10102", "holder": {"type": "projectLead"}},
        {"id": "10002", "issueSecurityLevelId": "10102",
         "holder": {"type": "group", "parameter": "devs", "value": "group-1", "group": {"groupId": "group-1"}}},
    ],
}
MEMBERS_PAGE_2 = {
    "startAt": 2, "maxResults": 2, "total": 3, "isLast": True,
    "values": [{"id": "10003", "issueSecurityLevelId": "10102",
                "holder": {"type": "user", "parameter": "acc-9", "value": "acc-9", "user": {"accountId": "acc-9"}}}],
}


def _resp(status: int = 200, body: object = None) -> MagicMock:
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=body if body is not None else {})
    r.text = MagicMock(return_value="")
    return r


def _connector() -> JiraConnector:
    dep = MagicMock()
    dep.org_id = "org-1"
    for name in ("on_new_records", "on_record_content_update", "on_new_record_groups"):
        setattr(dep, name, AsyncMock())
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_record_group_by_external_id = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])  # no comment records stored
    cs = MagicMock()
    cs.get_config = AsyncMock()
    c = JiraConnector(logging.getLogger("test.jira.sec"), dep, MagicMock(), cs, "conn-sec", "team", "user-1")
    c.site_url = "https://x.atlassian.net"
    c.indexing_filters = None
    c._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])
    c._fetch_issue_attachments = AsyncMock(return_value=[])
    return c


def _users() -> list[AppUser]:
    return [AppUser(app_name="JIRA", connector_id="conn-sec", source_user_id=acc, org_id="org-1", email=email,
                    full_name=email, is_active=True)
            for acc, email in (("acc-1", "rep@x.com"), ("acc-2", "asg@x.com"), ("acc-9", "mia@x.com"))]


def _issue(
    security: dict | None = None, updated: str = "2024-06-15T10:00:00.000+0000", key: str = "PROJ-1", issue_id: str = "1001",
) -> dict:
    return {"id": issue_id, "key": key, "fields": {
        "summary": "s", "description": None, "status": {"name": "Open"}, "priority": {"name": "High"},
        "creator": {"accountId": "acc-1"}, "reporter": {"accountId": "acc-1", "displayName": "R"},
        "assignee": {"accountId": "acc-2", "displayName": "A"}, "created": "2024-01-01T00:00:00.000+0000",
        "updated": updated, "issuetype": {"name": "Task", "hierarchyLevel": 0}, "project": {"id": PROJECT_ID, "key": "PROJ"},
        "parent": None, "attachment": [], "issuelinks": [], "security": security}}


def _present(members=None) -> IssueSecurityContext:
    return IssueSecurityContext(
        knowledge=SchemeKnowledge.PRESENT, scheme_id="10002", members_readable=True,
        members_by_level=members if members is not None else {"10102": [
            {"type": "group", "value": "group-1"},
            {"type": "user", "parameter": "acc-9", "user": {"accountId": "acc-9"}},
            {"type": "reporter"}, {"type": "assignee"}, {"type": "projectLead"},
            {"type": "projectRole", "parameter": "10003", "projectRole": {"id": "10003", "name": "Developers"}},
        ]})


async def _build(
    connector: JiraConnector, issue: dict, context: IssueSecurityContext, existing: MagicMock | None = None,
    *, full_sync: bool = False,
) -> tuple:
    connector._issue_security[PROJECT_ID] = context
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
    records, _ = await connector._build_issue_records([issue], PROJECT_ID, _users(), full_sync)
    return records[0]


class TestSecuredSubtasks:
    """A sub-task carries its parent's level, but a per-issue holder of that level is resolved on the sub-task."""

    @staticmethod
    def _subtask(level: str) -> dict:
        sub = _issue({"id": level, "name": "L"}, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        return sub

    @pytest.mark.asyncio
    async def test_its_own_assignee_reads_it_without_reading_the_parent(self) -> None:
        record, perms, _ = await _build(_connector(), self._subtask("10102"), _present())
        assert record.parent_external_record_id is None, "filed under the parent it would need the parent's readers"
        assert record.access_rule == AccessRule.RESTRICTED
        assert {"rep@x.com", "asg@x.com"} <= {p.email for p in perms}

    @pytest.mark.asyncio
    async def test_a_level_that_names_the_same_people_everywhere_keeps_it_under_the_parent(self) -> None:
        context = _present({"10102": [{"type": "group", "value": "group-1"}]})
        record, perms, _ = await _build(_connector(), self._subtask("10102"), context)
        assert record.parent_external_record_id == "1001"
        assert [(p.entity_type, p.external_id) for p in perms] == [(EntityType.GROUP, "group-1")]


class TestSecuredIssues:
    @pytest.mark.asyncio
    async def test_leveled_issue_is_granted_to_level_members_only(self) -> None:
        """T1/T2: no project inheritance is enough; only the level's members are granted. The holders
        that name the same people on every issue are held once by the level's record group, which the
        issue inherits instead of its project; the issue holds the ones it names itself."""
        c = _connector()
        record, perms, _ = await _build(c, _issue({"id": "10102", "name": "Leadership"}), _present())
        assert (record.access_rule, record.access_group_external_id) == (AccessRule.STRICT, "level:p-1:10102")
        assert record.rewrite_permissions is True
        assert {(p.entity_type, p.external_id, p.email) for p in perms} == {
            (EntityType.USER, None, "rep@x.com"),
            (EntityType.USER, None, "asg@x.com"),
        }
        await c._write_level_groups(PROJECT_ID, "PROJ", _present(), ["10102"],
                                    {u.source_user_id: u for u in _users()})
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert (group.external_group_id, group.parent_external_group_id, group.access_rule, group.inherit_permissions) == (
            "level:p-1:10102", PROJECT_ID, AccessRule.RESTRICTED, True)
        assert {(p.entity_type, p.external_id, p.email) for p in grants} == {
            (EntityType.GROUP, "group-1", None),
            (EntityType.USER, None, "mia@x.com"),
            (EntityType.ROLE, "PROJ_projectLead", None),
            (EntityType.ROLE, "PROJ_10003", None),
        }
        assert not any(p.entity_type == EntityType.ORG for p in [*perms, *grants])

    @pytest.mark.asyncio
    async def test_a_leveled_sub_task_holds_a_copy_of_its_level(self) -> None:
        """Atlassian documents only that it inherits its parent's level: no group, a copy. Its level names
        its own reporter and assignee, who need not read the parent, so it is not filed under it."""
        sub = _issue({"id": "10102", "name": "Leadership"}, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "hierarchyLevel": -1, "subtask": True}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        record, perms, _ = await _build(_connector(), sub, _present())
        assert (record.access_rule, record.access_group_external_id) == (AccessRule.RESTRICTED, "")
        assert record.parent_external_record_id is None
        assert {(p.entity_type, p.email) for p in perms} >= {(EntityType.USER, "rep@x.com"), (EntityType.USER, "asg@x.com")}
        assert (EntityType.GROUP, "group-1") in {(p.entity_type, p.external_id) for p in perms}

    @pytest.mark.asyncio
    async def test_leveled_issue_with_unreadable_members_is_closed(self) -> None:
        ctx = _present()
        ctx.members_readable = False
        record, perms, _ = await _build(_connector(), _issue({"id": "10102"}), ctx)
        assert (record.access_rule, perms) == (AccessRule.RESTRICTED, [])

    @pytest.mark.asyncio
    async def test_unsecured_issue_in_a_project_without_a_scheme_is_unchanged(self) -> None:
        """T4/O2: a null level is project-open with the stored grants replaced by none."""
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        record, perms, _ = await _build(_connector(), _issue(None), ctx)
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)

    @pytest.mark.asyncio
    async def test_unsecured_issue_when_the_scheme_could_not_be_read_is_unchanged(self) -> None:
        """A non-admin token (403 on the scheme) must not hide the issues that carry no level."""
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        record, perms, _ = await _build(_connector(), _issue(None), ctx)
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)
        record, perms, _ = await _build(_connector(), _issue({"id": "10102"}), ctx)
        assert (record.access_rule, perms) == (AccessRule.RESTRICTED, [])

    @pytest.mark.asyncio
    async def test_unsecured_issue_in_a_project_with_a_scheme_inherits_the_project(self) -> None:
        record, perms, _ = await _build(_connector(), _issue(None), _present())
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)

    def test_security_is_requested_with_the_issues(self) -> None:
        assert "security" in ISSUE_SEARCH_FIELDS


class TestLevelChangeOnIncrementalSync:
    @pytest.mark.asyncio
    async def test_a_changed_level_replaces_the_stored_grants(self) -> None:
        """T3: the issue's `updated` moved, so it is re-sent with its new grants."""
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue({"id": "10102"}), _present(), existing)
        assert changed is True and record.version == 3
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, perms, content_changed=True)
        assert perms

    @pytest.mark.asyncio
    async def test_a_cleared_level_removes_the_grants(self) -> None:
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue(None), _present(), existing)
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        # an explicit empty list replaces the stored grants; the rule goes back to the project's
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, [], content_changed=True)
        assert record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_cleared_level_replaces_grants_whatever_the_scheme_state(self) -> None:
        """O2: with the scheme NONE or UNKNOWN, a null level still clears the old level grants."""
        for knowledge in (SchemeKnowledge.NONE, SchemeKnowledge.UNKNOWN):
            c = _connector()
            existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
            record, perms, changed = await _build(c, _issue(None), IssueSecurityContext(knowledge=knowledge), existing)
            await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
            c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, [], content_changed=True)
            assert record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_record_with_nothing_to_rewrite_is_updated_without_grants(self) -> None:
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue(None), IssueSecurityContext(knowledge=SchemeKnowledge.NONE), existing)
        record.rewrite_permissions = False
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, content_changed=True)

    @pytest.mark.asyncio
    async def test_an_unchanged_issue_kept_for_its_grants_does_not_ask_for_a_reindex(self) -> None:
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, _, _ = await _build(c, _issue(None), IssueSecurityContext(knowledge=SchemeKnowledge.NONE), existing)
        record.rewrite_permissions = False
        c._keeps_stored_grants = lambda _record: True
        await c._process_new_records([(record, [], False)], "PROJ", {"new_count": 0, "updated_count": 0})
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, content_changed=False)


class TestLoadIssueSecurity:
    @pytest.mark.asyncio
    async def test_scheme_and_paged_members_are_read(self) -> None:
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[_resp(200, SCHEME), _resp(200, MEMBERS_PAGE_1), _resp(200, MEMBERS_PAGE_2)])
        ctx = await c._load_issue_security("PROJ")
        assert ctx.knowledge == SchemeKnowledge.PRESENT and ctx.members_readable
        assert [h["type"] for h in ctx.members_by_level["10102"]] == ["projectLead", "group", "user"]

    @pytest.mark.asyncio
    async def test_a_scheme_shared_by_projects_has_its_members_read_once(self) -> None:
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[
            _resp(200, SCHEME), _resp(200, {**MEMBERS_PAGE_2, "startAt": 0}), _resp(200, SCHEME)])
        first = await c._load_issue_security("PROJ")
        second = await c._load_issue_security("OTHER")
        assert c._call_with_retry.await_count == 3  # 2 scheme reads + 1 members read
        assert first.members_by_level == second.members_by_level

    @pytest.mark.asyncio
    async def test_a_project_without_a_scheme_is_known_to_have_none(self) -> None:
        c = _connector()
        c._call_with_retry = AsyncMock(return_value=_resp(404, {"errorMessages": ["Security level for project 10000 does not exist."]}))
        assert (await c._load_issue_security("PROJ")).knowledge == SchemeKnowledge.NONE

    @pytest.mark.asyncio
    async def test_a_failing_scheme_read_is_retried_next_sync_and_never_means_none(self) -> None:
        c = _connector()
        c._call_with_retry = AsyncMock(return_value=_resp(503, {}))
        ctx = await c._load_issue_security("PROJ")
        assert ctx.knowledge == SchemeKnowledge.UNKNOWN and ctx.defer_checkpoint is True

    @pytest.mark.asyncio
    async def test_failing_members_close_leveled_issues_only(self) -> None:
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[_resp(200, SCHEME), _resp(500, {})])
        ctx = await c._load_issue_security("PROJ")
        assert ctx.knowledge == SchemeKnowledge.PRESENT and ctx.members_readable is False

    @pytest.mark.asyncio
    async def test_a_scheme_with_no_levels_is_readable_without_reading_members(self) -> None:
        """A team-managed project's "Simplified" scheme lists no level, so no issue can carry one."""
        c = _connector()
        c._call_with_retry = AsyncMock(return_value=_resp(
            200, {"id": 10001, "name": "ESP: Simplified Issue Security Scheme", "levels": []}))
        ctx = await c._load_issue_security("ESP")
        assert ctx.knowledge == SchemeKnowledge.PRESENT and ctx.members_readable is True
        assert c._call_with_retry.await_count == 1
        assert unreadable_reason(ctx) is None

    @pytest.mark.asyncio
    async def test_members_refused_for_the_token_scope_are_told_from_a_missing_permission(self) -> None:
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[
            _resp(200, SCHEME), _resp(401, {"code": 401, "message": "Unauthorized; scope does not match"})])
        ctx = await c._load_issue_security("PROJ")
        assert ctx.members_readable is False and ctx.read_status == 401
        assert unreadable_reason(ctx) == UNREADABLE_SCOPE

    @pytest.mark.asyncio
    async def test_a_401_that_names_no_scope_is_a_token_jira_no_longer_accepts(self) -> None:
        """Reconnecting fixes an expired or revoked token; adding scopes would not."""
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[
            _resp(200, SCHEME), _resp(401, {"code": 401, "message": "Unauthorized"})])
        ctx = await c._load_issue_security("PROJ")
        assert ctx.read_status == 401 and ctx.scope_refused is False
        assert unreadable_reason(ctx) == UNREADABLE_AUTH


class TestUnreadableSecurityWarning:
    @pytest.mark.asyncio
    async def test_one_warning_per_reason_lists_its_projects(self, caplog: pytest.LogCaptureFixture) -> None:
        c = _connector()
        c.notify = AsyncMock()
        c._unreadable_security = {"PWEBSITE": UNREADABLE_SCOPE, "PST": UNREADABLE_SCOPE, "QA": UNREADABLE_PERMISSION}
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            await c._warn_unreadable_security()
        messages = sorted(r.getMessage() for r in caplog.records)
        assert len(messages) == 2
        scope, permission = (m for m in messages if "HTTP 401" in m), (m for m in messages if "HTTP 403" in m)
        assert next(scope).endswith("PST, PWEBSITE")
        assert next(permission).endswith("QA")

    @pytest.mark.asyncio
    async def test_the_owner_is_notified_once_with_every_project_and_its_fix(self) -> None:
        c = _connector()
        c.notify = AsyncMock()
        c._unreadable_security = {"PWEBSITE": UNREADABLE_SCOPE, "PST": UNREADABLE_SCOPE, "QA": UNREADABLE_PERMISSION}
        await c._warn_unreadable_security()
        c.notify.assert_awaited_once()
        message = c.notify.await_args.kwargs["message"]
        assert "PST, PWEBSITE" in message and "read:issue-security-level:jira" in message
        assert "QA" in message and "Administer Jira" in message

    @pytest.mark.asyncio
    async def test_the_notification_says_which_issues_are_hidden_and_which_keep_their_access(self) -> None:
        """After a transient failure the stored grants are kept; after a missing scope the issues close."""
        c = _connector()
        c.notify = AsyncMock()
        c._unreadable_security = {"PST": UNREADABLE_SCOPE, "QA": UNREADABLE_TRANSIENT}
        await c._warn_unreadable_security()
        message = c.notify.await_args.kwargs["message"]
        hidden, kept = message.split("QA:")
        assert "PST" in hidden and "are hidden from everyone" in hidden
        assert "keep the access they had" in kept and "hidden" not in kept

    @pytest.mark.asyncio
    async def test_nothing_unreadable_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        c = _connector()
        c.notify = AsyncMock()
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            await c._warn_unreadable_security()
        assert not caplog.records
        c.notify.assert_not_awaited()


class TestMemberChangeRereadsOnlyTheAffectedLevels:
    async def _sync(
        self,
        stored_levels: dict | None,
        ctx: IssueSecurityContext | None = None,
        fetch_fn: Callable[[JiraConnector, list], Awaitable[None]] | None = None,
        connector: JiraConnector | None = None,
        placement: str | None = ISSUE_PLACEMENT,
        stored_browse: str | None = None,
    ) -> tuple:
        c = connector or _connector()
        c.issues_sync_point = MagicMock()
        point = {"last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": placement}
        if stored_browse is not None:
            point["browse_holders"] = stored_browse
        if stored_levels is not None:
            point["security_levels"] = json.dumps(stored_levels)
        c.issues_sync_point.read_sync_point = AsyncMock(return_value=point)
        c.issues_sync_point.update_sync_point = AsyncMock()
        ctx = ctx or _present()
        c._load_issue_security = AsyncMock(return_value=ctx)
        calls = []

        async def fetch(
            key: str, pid: str, users: list, last_sync: int | None, resume: int | None, *,
            is_new_project: bool = False, level_ids: list[str] | None = None, leveled_only: bool = False,
            stored_only: bool = False,
        ) -> AsyncIterator:
            calls.append({"resume": resume, "is_new_project": is_new_project, "level_ids": level_ids,
                          "leveled_only": leveled_only, "stored_only": stored_only})
            if fetch_fn:
                await fetch_fn(c, calls)
            return
            yield

        c._fetch_issues_batched = fetch
        rg = MagicMock(short_name="PROJ", external_group_id=PROJECT_ID)
        result = await c._sync_project_issues(rg, [], 1)
        return c, ctx, calls, result

    @pytest.mark.asyncio
    async def test_a_changed_level_gets_a_second_pass_over_its_issues_only(self) -> None:
        _, ctx, calls, result = await self._sync(stored_levels={"10102": "old"})
        assert calls == [
            {"resume": 1_700_000_000_000, "is_new_project": False, "level_ids": None, "leveled_only": False, "stored_only": False},  # the normal incremental pass
            {"resume": None, "is_new_project": True, "level_ids": ["10102"], "leveled_only": False, "stored_only": True},
        ]
        assert result["is_new_project"] is False  # not a full sync: nothing is unlinked afterwards
        assert result["leveled_reread"] is True  # so the sweep re-secures the project's stubs too

    @staticmethod
    def _group_moved() -> dict:
        """The stored digests of the level before its group holder changed: a change its record group holds alone."""
        members = _present().members_by_level["10102"]
        return _present({"10102": [{"type": "group", "value": "group-old"}, *members[1:]]}).level_fingerprints(split=True)

    @pytest.mark.asyncio
    async def test_a_change_the_level_group_holds_alone_still_rereads_its_issues(self) -> None:
        """A leveled sub-task holds a copy of its level's holders (it is read only with its parent), which the group's
        write does not change."""
        c = _connector()
        c._browse_issue_holders["PROJ"] = []  # read, and naming nobody on an issue
        _, _, calls, _ = await self._sync(stored_levels=self._group_moved(), connector=c)
        assert calls[1:] == [
            {"resume": None, "is_new_project": True, "level_ids": ["10102"], "leveled_only": False, "stored_only": True}]

    @pytest.mark.asyncio
    async def test_a_change_the_level_group_holds_alone_rereads_its_issues_when_browse_holders_are_unread(self) -> None:
        """Browse holders that named people before and were not read now may still name a user holder of the level on an
        issue that reads it alone (OPEN, with a grant of its own); only a re-read takes that grant away."""
        _, _, calls, _ = await self._sync(stored_levels=self._group_moved(), stored_browse="abc")
        assert calls[1:] == [
            {"resume": None, "is_new_project": True, "level_ids": ["10102"], "leveled_only": False, "stored_only": True}]

    @pytest.mark.asyncio
    async def test_the_fingerprints_are_stored_after_the_walk_and_not_before(self) -> None:
        c, ctx, _, _ = await self._sync(stored_levels={})
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints(split=True) and saved["last_issue_updated"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_unchanged_levels_keep_the_incremental_window_alone(self) -> None:
        _, ctx, calls, _ = await self._sync(stored_levels=_present().level_fingerprints(split=True))
        assert calls == [{"resume": 1_700_000_000_000, "is_new_project": False, "level_ids": None, "leveled_only": False, "stored_only": False}]

    @pytest.mark.asyncio
    async def test_a_level_emptied_or_deleted_rereads_every_stored_issue(self) -> None:
        """O1c: the level left the member list. If it was deleted, Jira rejects its id in JQL,
        and an issue moved from it to no level matches no level predicate."""
        stored = {"10102": _present().level_fingerprints(split=True)["10102"], "10103": "gone-member"}
        c, ctx, calls, _ = await self._sync(stored_levels=stored)
        assert calls[1] == {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": False,
                            "stored_only": True}
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints(split=True)

    @pytest.mark.asyncio
    async def test_the_first_sync_after_upgrade_rereads_every_leveled_issue(self) -> None:
        """O1b: nothing stored -> `level is not EMPTY`, so holder-less levels are covered too."""
        c, ctx, calls, _ = await self._sync(stored_levels=None)
        assert calls[1] == {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": True,
                            "stored_only": True}
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints(split=True)

    @pytest.mark.asyncio
    async def test_an_unreadable_scheme_rereads_leveled_issues_to_close_them_and_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """O1a: a 403 on the scheme closes the issues that carry a level, with an actionable warning."""
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            c, _, calls, _ = await self._sync(stored_levels=None, ctx=ctx)
            c.notify = AsyncMock()
            await c._warn_unreadable_security()
        assert calls[1]["leveled_only"] is True
        assert len([r for r in caplog.records if "Administer Jira" in r.getMessage()]) == 1
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == {"*": "unreadable"}
        # once recorded, a still-unreadable scheme does not repeat the walk
        _, _, calls, _ = await self._sync(stored_levels={"*": "unreadable"}, ctx=ctx)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_failed_issue_keeps_the_old_digest_so_the_next_sync_retries(self) -> None:
        """O3."""
        async def fail(c: JiraConnector, calls: list) -> None:
            if len(calls) == 2:
                c._issue_build_failures += 1

        c, _, _, _ = await self._sync(stored_levels={"10102": "old"}, fetch_fn=fail)
        assert not any("security_levels" in (call.args[1] or {}) and call.args[1]["security_levels"] != json.dumps({"10102": "old"})
                       for call in c.issues_sync_point.update_sync_point.await_args_list)

    @pytest.mark.asyncio
    async def test_a_jql_400_on_level_in_falls_back_to_level_is_not_empty(self) -> None:
        """O6."""
        async def reject(c: JiraConnector, calls: list) -> None:
            if len(calls) == 2:
                raise JiraReadError(400, "level 10102 does not exist")

        _, _, calls, _ = await self._sync(stored_levels={"10102": "old"}, fetch_fn=reject)
        assert [c["leveled_only"] for c in calls] == [False, False, True]

    @pytest.mark.asyncio
    async def test_the_narrowed_query_filters_by_level_and_not_by_date(self) -> None:
        c = _connector()
        c.sync_filters = None
        c.data_source = MagicMock()
        seen = {}

        async def search(**kw: object) -> MagicMock:
            seen.update(kw)
            return _resp(200, {"issues": []})

        c._search_issues_with_retry = search
        async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, is_new_project=True, level_ids=["10102", "10103"]):
            pass
        assert seen["jql"].startswith('project = "PROJ" AND level in (10102, 10103) ORDER BY')
        assert "updated" not in seen["jql"].split("ORDER BY")[0]


@pytest.mark.asyncio
async def test_leveled_issue_jql_is_level_is_not_empty() -> None:
    c = _connector()
    c.sync_filters = None
    c.data_source = MagicMock()
    seen = {}

    async def search(**kw: object) -> MagicMock:
        seen.update(kw)
        return _resp(200, {"issues": []})

    c._search_issues_with_retry = search
    async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, is_new_project=True, leveled_only=True):
        pass
    assert seen["jql"].startswith('project = "PROJ" AND level is not EMPTY ORDER BY')


@pytest.mark.asyncio
async def test_the_failure_counter_is_not_reset_before_the_reread() -> None:
    """R2-2: an issue that failed in the main walk still blocks saving the digests."""
    async def fail_in_main_walk(c: JiraConnector, calls: list) -> None:
        if len(calls) == 1:
            c._issue_build_failures += 1

    c, _, _, _ = await TestMemberChangeRereadsOnlyTheAffectedLevels()._sync(
        stored_levels={"10102": "old"}, fetch_fn=fail_in_main_walk)
    assert not any(call.args[1].get("security_levels") != json.dumps({"10102": "old"})
                   for call in c.issues_sync_point.update_sync_point.await_args_list)


@pytest.mark.asyncio
async def test_a_stub_of_a_leveled_ancestor_takes_its_level_s_group() -> None:
    """R2-1: browsing lists stubs, so a stub must carry its issue's level, not the open project rule. It takes the level's
    group, so a holder leaving the level loses it with the group's write."""
    c = _connector()
    c._issue_security[PROJECT_ID] = _present()
    stub = TicketRecord(
        id="rid", org_id="org-1", record_name="x", record_type="TICKET", external_record_id="1001", version=0,
        origin="CONNECTOR", connector_name="JIRA", connector_id="conn-sec", external_record_group_id=PROJECT_ID,
        record_group_type="PROJECT", is_placeholder=True)
    c._bulk_fetch_ancestor_level = AsyncMock(return_value=[_issue({"id": "10102"})])
    c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
    c.data_entities_processor.get_all_app_users = AsyncMock(return_value=_users())
    c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    await c._sweep_placeholder_records({PROJECT_ID})
    record, perms = c.data_entities_processor.on_new_records.await_args.args[0][0]
    assert record.is_placeholder and record.access_rule == AccessRule.STRICT
    assert record.access_group_external_id == "level:p-1:10102"
    assert record.rewrite_permissions and {p.email for p in perms} == {"rep@x.com", "asg@x.com"}  # not the level's user


def test_security_is_requested_for_ancestor_stubs() -> None:
    from app.connectors.sources.atlassian.jira_cloud.connector import (
        ANCESTOR_STUB_FIELDS,
    )
    assert "security" in ANCESTOR_STUB_FIELDS


@pytest.mark.asyncio
async def test_the_upgrade_sync_hands_its_reread_projects_to_the_stub_sweep() -> None:
    """N1: stubs written open before this feature are re-submitted once, on the first sync after upgrade."""
    c = _connector()
    results = {"p-1": {"total_synced": 0, "new_count": 0, "updated_count": 0, "is_new_project": False, "leveled_reread": True},
               "p-2": {"total_synced": 0, "new_count": 0, "updated_count": 0, "is_new_project": False}}
    c._sync_project_issues = AsyncMock(side_effect=lambda rg, *a: results[rg.external_group_id])
    projects = [(MagicMock(short_name=k, external_group_id=k), []) for k in results]
    stats = await c._sync_all_project_issues(projects, [], 1)
    assert stats["leveled_reread_project_ids"] == {"p-1"} and stats["full_sync_project_ids"] == set()


@pytest.mark.asyncio
async def test_a_failed_read_of_the_synced_users_skips_the_stub_sweep() -> None:
    """An empty directory would take every stub's level grants away; the next sync sweeps."""
    c = _connector()
    c._issue_security[PROJECT_ID] = _present()
    stub = TicketRecord(
        id="rid", org_id="org-1", record_name="x", record_type="TICKET", external_record_id="1001", version=0,
        origin="CONNECTOR", connector_name="JIRA", connector_id="conn-sec", external_record_group_id=PROJECT_ID,
        record_group_type="PROJECT", is_placeholder=True, external_revision_id=None, access_rule=AccessRule.OPEN)
    c._bulk_fetch_ancestor_level = AsyncMock(return_value=[_issue({"id": "10102"})])
    c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
    c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=ConnectionError("graph unavailable"))
    await c._sweep_placeholder_records({PROJECT_ID}, set())
    c.data_entities_processor.get_all_app_users.assert_awaited_once_with("conn-sec", raise_on_error=True)
    c.data_entities_processor.on_new_records.assert_not_awaited()
    c._bulk_fetch_ancestor_level.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_legacy_open_stub_of_a_leveled_ancestor_is_restricted_after_the_upgrade_sync() -> None:
    """N1: the stub already has a revision, so only the re-read project hands it back to the sweep."""
    c = _connector()
    c._issue_security[PROJECT_ID] = _present()
    stub = TicketRecord(
        id="rid", org_id="org-1", record_name="x", record_type="TICKET", external_record_id="1001", version=0,
        origin="CONNECTOR", connector_name="JIRA", connector_id="conn-sec", external_record_group_id=PROJECT_ID,
        record_group_type="PROJECT", is_placeholder=True, external_revision_id="placeholder:1", access_rule=AccessRule.OPEN)
    c._bulk_fetch_ancestor_level = AsyncMock(return_value=[_issue({"id": "10102"})])
    c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
    c.data_entities_processor.get_all_app_users = AsyncMock(return_value=_users())
    c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    await c._sweep_placeholder_records({PROJECT_ID}, set())
    c.data_entities_processor.on_new_records.assert_not_awaited()
    await c._sweep_placeholder_records({PROJECT_ID}, {PROJECT_ID})
    record, perms = c.data_entities_processor.on_new_records.await_args.args[0][0]
    assert record.access_rule == AccessRule.STRICT and record.access_group_external_id == "level:p-1:10102" and perms


def _stored_ticket(**overrides: object) -> TicketRecord:
    fields: dict[str, object] = {
        "id": "rid", "org_id": "org-1", "record_name": "x", "record_type": "TICKET", "external_record_id": "1001",
        "version": 1, "origin": "CONNECTOR", "connector_name": "JIRA", "connector_id": "conn-sec",
        "source_updated_at": 1, "access_rule": AccessRule.STRICT, "weburl": "https://x/browse/PROJ-1",
        "external_record_group_id": PROJECT_ID, "record_group_type": "PROJECT"}
    fields.update(overrides)
    return TicketRecord(**fields)


def _reindex_connector(issue: dict, scheme_reads: list) -> JiraConnector:
    c = _connector()
    c.indexing_filters = MagicMock()  # already loaded
    ds = MagicMock()
    ds.get_issue = AsyncMock(return_value=_resp(200, issue))
    c._get_fresh_datasource = AsyncMock(return_value=ds)
    c._call_with_retry = AsyncMock(side_effect=scheme_reads)
    c._read_browse_issue_holders = AsyncMock(return_value=[])  # the scheme, read fresh by the reindex
    return c


class TestReindexAppliesTheLevel:
    LEVELED = _issue({"id": "10102", "name": "Leadership"}, updated="2025-01-01T00:00:00.000+0000")
    MEMBERS = {"isLast": True, "values": [
        {"issueSecurityLevelId": "10102", "holder": {"type": "group", "value": "group-1"}}]}

    def _reporter_reindex(self, members: tuple[list[str], bool]) -> tuple[JiraConnector, dict]:
        """The reporter acc-1, whom Browse names on the issue; its level holds group-1. The last sync saw the reporter in
        group-1; the reindex reads the group again and gets ``members``."""
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        c._read_browse_issue_holders = AsyncMock(return_value=[{"type": "reporter"}])
        c._group_members = {"group-1": [_users()[0]]}  # as the last sync saved them
        c._fetch_group_members = AsyncMock(return_value=members)
        return c, TestTheRetryListKeepsWhatAReindexAdds._store(c, [])

    @pytest.mark.asyncio
    async def test_a_reporter_jira_took_out_of_the_group_gets_no_grant_from_a_reindex(self) -> None:
        """No sync since Jira removed the reporter from group-1: the reindex reads the group again and does not open the
        issue to them through the members the last sync saw."""
        c, point = self._reporter_reindex((["acc-2"], True))
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0], "acc-2": _users()[1]})
        assert (record.access_rule, perms) == (AccessRule.STRICT, [])
        assert c._fetch_group_members.await_args.kwargs == {"note": False}  # outside a sync: no bookkeeping of its own
        assert [u.source_user_id for u in c._group_members["group-1"]] == ["acc-1"]  # the sync's map is left as it was

    @pytest.mark.asyncio
    async def test_the_reporter_in_the_level_through_a_group_reads_it_after_a_reindex(self) -> None:
        """Browse names the reporter on the issue and the group read now holds them: Jira shows it to them."""
        c, point = self._reporter_reindex((["acc-1"], True))
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        assert record.source_updated_at == c._parse_jira_timestamp("2025-01-01T00:00:00.000+0000")
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == []

    @pytest.mark.asyncio
    async def test_a_group_read_that_fails_in_a_reindex_grants_nobody_and_queues_the_issue(self) -> None:
        c, point = self._reporter_reindex(([], False))
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert (record.access_rule, perms, record.source_updated_at) == (AccessRule.STRICT, [], 1)
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == ["1001"]

    @pytest.mark.asyncio
    async def test_a_reindex_before_any_sync_grants_nobody_through_the_group_and_leaves_the_issue_to_the_sync(self) -> None:
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        c._read_browse_issue_holders = AsyncMock(return_value=[{"type": "reporter"}])
        c._get_project_sync_checkpoint = AsyncMock(return_value={})
        c._update_project_sync_checkpoint = AsyncMock()
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert (record.access_rule, perms, record.source_updated_at) == (AccessRule.STRICT, [], 1)

    @pytest.mark.asyncio
    async def test_a_reindex_before_any_sync_puts_the_issue_on_the_retry_list_the_next_sync_reads(self) -> None:
        """Its `updated` predates the cursor and the level digests match, so only the retry list makes the next sync read it."""
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        c._read_browse_issue_holders = AsyncMock(return_value=[{"type": "reporter"}])
        saved = TestTheRetryListKeepsWhatAReindexAdds._store(c, ["7"])
        await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert (json.loads(saved["issues_to_retry"]), TestTheRetryListKeepsWhatAReindexAdds._queued(saved)) == (["7"], ["1001"])

        nxt = _connector()
        nxt.issues_sync_point = MagicMock()
        nxt.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
            "issues_to_retry": saved["issues_to_retry"], "reindex_retry": saved["reindex_retry"]})
        nxt.issues_sync_point.update_sync_point = AsyncMock()
        nxt._load_issue_security = AsyncMock(return_value=_present())
        read: list = []

        async def fetch(*_a: object, issue_ids: list | None = None, **_kw: object) -> AsyncIterator:
            read.append(issue_ids)
            return
            yield
        nxt._fetch_issues_batched = fetch
        await nxt._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert read[0] == ["1001", "7"]

    @pytest.mark.asyncio
    async def test_a_level_set_since_the_last_sync_is_applied_by_the_reindex(self) -> None:
        """A project-open issue given a level, then reindexed, must not stay open to the project."""
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        assert (record.access_rule, record.access_group_external_id) == (AccessRule.STRICT, "level:p-1:10102")
        assert record.rewrite_permissions is True and perms == []
        # No sync has written the level's group yet, so the reindex writes it first.
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert group.external_group_id == "level:p-1:10102" and group.name == "Security level: Leadership"
        assert {(p.entity_type, p.external_id) for p in grants} == {(EntityType.GROUP, "group-1")}
        assert record.source_updated_at == c._parse_jira_timestamp("2025-01-01T00:00:00.000+0000")

    @pytest.mark.asyncio
    async def test_the_reindex_writes_the_level_s_current_holders_over_a_stored_group(self) -> None:
        """The issue inherits the group: left with the holders of the last sync, one who left the level since keeps it."""
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="lvl"))
        await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert group.external_group_id == "level:p-1:10102"
        assert {(p.entity_type, p.external_id) for p in grants} == {(EntityType.GROUP, "group-1")}

    @pytest.mark.asyncio
    async def test_a_transient_scheme_read_leaves_the_stored_record_alone(self) -> None:
        """Writing it would hide a project-open issue (RESTRICTED, no grant) and stamp its timestamp."""
        c = _reindex_connector(self.LEVELED, [_resp(503, {})])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("holder", [
        None,  # the level has no member row
        {"type": "user", "parameter": "acc-x", "user": {"accountId": "acc-x", "active": False}},
        # outside the directory, with an email a synced account may share: no access, not a holder left unread
        {"type": "user", "parameter": "acc-x", "user": {"accountId": "acc-x", "emailAddress": "mia@x.com"}},
        {"type": "projectRole", "parameter": "1", "projectRole": {"name": "atlassian-addons-project-access"}},
    ])
    async def test_a_level_read_in_full_that_grants_nobody_is_written(self, holder: dict | None) -> None:
        """A settled empty audience replaces the stored one; leaving it would keep the old audience."""
        values = [{"issueSecurityLevelId": "10102", "holder": holder}] if holder else []
        other = [{"issueSecurityLevelId": "10199", "holder": {"type": "group", "value": "g-other"}}]
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, {"isLast": True, "values": values + other})])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        if holder is None:  # a level with no member row is no group: the issue is closed on its own
            assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.RESTRICTED, [], True)
            return
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert (group.external_group_id, grants) == ("level:p-1:10102", [])

    @pytest.mark.asyncio
    async def test_a_forbidden_scheme_or_an_unreadable_field_leaves_the_stored_record_alone(self) -> None:
        c = _reindex_connector(self.LEVELED, [_resp(403, {})])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None
        members = {"isLast": True, "values": [{"issueSecurityLevelId": "10102", "holder": {
            "type": "userCustomField", "parameter": "customfield_99"}}]}
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, members)])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None

    @pytest.mark.asyncio
    async def test_a_cleared_level_is_applied_by_the_reindex_even_on_a_transient_read(self) -> None:
        """`security: null` is a positive "no level": the stored level grants are replaced by none."""
        cleared = _issue(None, updated="2025-01-01T00:00:00.000+0000")
        c = _reindex_connector(cleared, [_resp(503, {})])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(access_rule=AccessRule.RESTRICTED), {})
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)
        missing = _issue(None, updated="2025-01-01T00:00:00.000+0000")
        del missing["fields"]["security"]
        c = _reindex_connector(missing, [_resp(503, {})])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None  # not returned is not null

    @pytest.mark.asyncio
    @pytest.mark.parametrize("holders", [
        [{"type": "applicationRole", "parameter": "jira-software"}],
        [{"type": "applicationRole", "parameter": "jira-software"}, {"type": "group", "value": "group-1"}],
    ])
    async def test_an_application_role_holder_outside_a_sync_leaves_the_stored_record_alone(
        self, holders: list,
    ) -> None:
        """The reindex has no application-role map: a write would replace the role's groups the
        last sync granted with none, or with the other holders only."""
        members = {"isLast": True, "values": [{"issueSecurityLevelId": "10102", "holder": h} for h in holders]}
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, members)])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None

    @pytest.mark.asyncio
    async def test_a_role_list_read_in_part_leaves_the_stored_record_alone(self) -> None:
        """A reindex applies the role list of the last sync (``_level_roles``); with none, a role holder does not
        resolve and the stored record stays."""
        members = {"isLast": True, "values": [{"issueSecurityLevelId": "10102", "holder": {
            "type": "applicationRole", "parameter": "jira-software"}}]}
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, members)])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, members)])
        c._level_roles = {"jira-software": [{"groupId": "g-1"}]}
        _, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert perms == [] and {p.external_id for p in grants} == {"g-1"}

    @pytest.mark.asyncio
    async def test_a_reindex_reads_each_project_scheme_once(self) -> None:
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        cache: dict = {}
        await c._check_and_fetch_updated_issue(_stored_ticket(), {}, cache)
        await c._check_and_fetch_updated_issue(_stored_ticket(id="rid-2"), {}, cache)
        assert c._call_with_retry.await_count == 2  # one scheme read + one members page

    @pytest.mark.asyncio
    async def test_the_reindex_reads_members_afresh(self) -> None:
        """A member removed since the last sync must not be granted from the sync's cache."""
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        c._scheme_members["10002"] = (True, {"10102": [{"type": "group", "value": "removed-group"}]})
        await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert {p.external_id for p in grants} == {"group-1"}
        assert c._scheme_members["10002"][1]["10102"][0]["value"] == "removed-group"  # sync cache untouched


TWO_LEVELS = {"id": 10002, "name": "PROJ issue security scheme",
              "levels": [{"id": "10102", "name": "Leadership"}, {"id": "10103", "name": "Everyone"}]}
BIG_GROUP = 10_000  # members of group-big; Jira returns at most 50 per page of /group/member


class TestAReindexReadsOnlyTheGroupsItsIssuesNeed:
    """A level's group members matter to a reindex only through ``_reads_alone``: for the level of the issue being written,
    and only when Browse names people on each issue. Every other read is cost with no effect on access."""

    @staticmethod
    def _reindex(issue: dict, browse: list | None, level_a: list | None = None) -> tuple[JiraConnector, list[str], dict]:
        """Level 10102 holds group-1 (the reporter acc-1 alone), or ``level_a``; 10103 holds group-big, whose 10,000 members include
        acc-1. Returns the connector, the group of every /group/member page it asks for, and the project's checkpoint."""
        values = [*({"issueSecurityLevelId": "10102", "holder": h}
                    for h in (level_a or [{"type": "group", "value": "group-1"}])),
                  {"issueSecurityLevelId": "10103", "holder": {"type": "group", "value": "group-big"}}]
        sizes = {"group-1": 1, "group-big": BIG_GROUP}
        pages: list[str] = []

        async def users_from_group(*, groupId: str, startAt: int, maxResults: int, **_: object) -> MagicMock:
            pages.append(groupId)
            end = min(sizes[groupId], startAt + maxResults)
            return _resp(200, {"isLast": end >= sizes[groupId], "values": [
                {"accountId": "acc-1" if i == 0 else f"{groupId}-{i}"} for i in range(startAt, end)]})

        c = _connector()
        c.indexing_filters = MagicMock()
        ds = MagicMock()
        ds.get_issue = AsyncMock(return_value=_resp(200, issue))
        ds.get_project_issue_security_scheme = AsyncMock(return_value=_resp(200, TWO_LEVELS))
        ds.get_security_level_members = AsyncMock(return_value=_resp(200, {"isLast": True, "values": values}))
        ds.get_users_from_group = users_from_group
        c.data_source = ds
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._call_with_retry = lambda call, **_: call(ds)
        c._read_browse_issue_holders = AsyncMock(return_value=browse)
        return c, pages, TestTheRetryListKeepsWhatAReindexAdds._store(c, [])

    @pytest.mark.asyncio
    async def test_a_level_no_reindexed_issue_has_is_not_read(self) -> None:
        """The issue is in 10102: the 10,000 members of 10103's group are not read (200 pages before)."""
        c, pages, _ = self._reindex(TestReindexAppliesTheLevel.LEVELED, [{"type": "reporter"}])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert pages == ["group-1"]
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])

    @pytest.mark.asyncio
    async def test_the_level_of_the_issue_is_read_in_full_and_once_per_reindex(self) -> None:
        """An issue in the big group's level: its reporter is a member, so it reads the issue alone. A second issue of that
        level in the same reindex reads no page again."""
        issue = _issue({"id": "10103", "name": "Everyone"}, updated="2025-01-01T00:00:00.000+0000")
        c, pages, _ = self._reindex(issue, [{"type": "reporter"}])
        cache: dict = {}
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]}, cache)
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        await c._check_and_fetch_updated_issue(_stored_ticket(id="rid-2"), {"acc-1": _users()[0]}, cache)
        assert pages == ["group-big"] * (BIG_GROUP // 50)

    @pytest.mark.asyncio
    async def test_a_later_issue_in_another_level_reads_that_level_then(self) -> None:
        """The project's context is shared by the reindex's issues: a level first met on the second issue is read for it."""
        c, pages, _ = self._reindex(TestReindexAppliesTheLevel.LEVELED, [{"type": "reporter"}])
        cache: dict = {}
        await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]}, cache)
        c.data_source.get_issue = AsyncMock(return_value=_resp(
            200, _issue({"id": "10103", "name": "Everyone"}, updated="2025-01-01T00:00:00.000+0000", issue_id="1002")))
        record, perms = await c._check_and_fetch_updated_issue(
            _stored_ticket(id="rid-2", external_record_id="1002"), {"acc-1": _users()[0]}, cache)
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        assert pages == ["group-1"] + ["group-big"] * (BIG_GROUP // 50)

    @pytest.mark.asyncio
    async def test_a_group_past_the_cap_grants_nobody_alone_and_queues_the_issue(self) -> None:
        """Unread is never read as "not a member" nor as "a member": the reporter gets no grant and the next sync decides."""
        c, pages, point = self._reindex(TestReindexAppliesTheLevel.LEVELED, [{"type": "reporter"}])
        cache: dict = {}
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.REINDEX_GROUP_READS", 1):
            await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]}, cache)
            c.data_source.get_issue = AsyncMock(return_value=_resp(
                200, _issue({"id": "10103", "name": "Everyone"}, updated="2025-01-01T00:00:00.000+0000", issue_id="1002")))
            record, perms = await c._check_and_fetch_updated_issue(
                _stored_ticket(id="rid-2", external_record_id="1002"), {"acc-1": _users()[0]}, cache)
        assert pages == ["group-1"]
        assert (record.access_rule, perms, record.source_updated_at) == (AccessRule.STRICT, [], 1)
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == ["1002"]

    @pytest.mark.asyncio
    async def test_a_sub_task_reads_no_level_members_and_leaves_the_cap_to_the_next_issue(self) -> None:
        """A leveled sub-task holds a copy of its level's holders and never reads alone (``grouped``), so its level's
        members change nothing for it. With room for one group, the issue after it still gets its reporter's grant."""
        subtask = _issue({"id": "10103", "name": "Everyone"}, updated="2025-01-01T00:00:00.000+0000", issue_id="1003")
        subtask["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        c, pages, point = self._reindex(subtask, [{"type": "reporter"}])
        cache: dict = {}
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.REINDEX_GROUP_READS", 1):
            await c._check_and_fetch_updated_issue(_stored_ticket(id="rid-3", external_record_id="1003"),
                                                   {"acc-1": _users()[0]}, cache)
            assert pages == []
            c.data_source.get_issue = AsyncMock(return_value=_resp(200, TestReindexAppliesTheLevel.LEVELED))
            record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]}, cache)
        assert pages == ["group-1"]
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        assert "1001" not in TestTheRetryListKeepsWhatAReindexAdds._queued(point)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("browse", [[], None])
    async def test_no_group_is_read_when_browse_names_no_one_on_each_issue(self, browse: list | None) -> None:
        """With no per-issue Browse holder (or none read), no level member can read an issue alone, so nothing is read; the
        record is written as it would be with every group read (``test_level_readers_change_nothing_without_browse_holders``)."""
        issue = _issue({"id": "10103", "name": "Everyone"}, updated="2025-01-01T00:00:00.000+0000")
        c, pages, _ = self._reindex(issue, browse)
        out = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert pages == []
        if browse == []:
            record, perms = out
            assert (record.access_rule, record.access_group_external_id, perms) == (AccessRule.STRICT, "level:p-1:10103", [])


class TestAReindexResolvesProjectRolesAndLeads:
    """A level's project role and project lead holders, which the sync expands from the roles and leads it read: a reindex
    reads them too, once each, so a reporter Browse names who is in the level through one reads the issue alone."""

    LEAD = [{"type": "projectLead"}]
    ROLE = [{"type": "projectRole", "parameter": "10002", "projectRole": {"id": "10002", "name": "Developers"}}]

    @staticmethod
    async def _run(level_a: list, *, lead: dict | None = None, actors: list | None = None, status: int = 200,
             issues: int = 1) -> tuple[JiraConnector, list, list[str], dict]:
        c, pages, point = TestAReindexReadsOnlyTheGroupsItsIssuesNeed._reindex(
            TestReindexAppliesTheLevel.LEVELED, [{"type": "reporter"}], level_a)
        c.data_source.get_project = AsyncMock(return_value=_resp(status, {"key": "PROJ", "lead": lead}))
        c.data_source.get_project_role = AsyncMock(return_value=_resp(status, {"id": 10002, "actors": actors or []}))
        cache: dict = {}
        out = [await c._check_and_fetch_updated_issue(_stored_ticket(id=f"rid-{n}"), {"acc-1": _users()[0]}, cache)
               for n in range(issues)]
        return c, out, pages, point

    @pytest.mark.asyncio
    async def test_a_reporter_the_level_names_as_project_lead_reads_it_after_a_reindex(self) -> None:
        c, ((record, perms), _), _, point = await self._run(self.LEAD, lead={"accountId": "acc-1"}, issues=2)
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == []
        assert c.data_source.get_project.await_count == 1  # once per reindex, whatever the number of issues

    @pytest.mark.asyncio
    async def test_another_lead_gives_the_reporter_nothing_and_the_issue_is_settled(self) -> None:
        _, ((record, perms),), _, point = await self._run(self.LEAD, lead={"accountId": "acc-2"})
        assert (record.access_rule, perms) == (AccessRule.STRICT, [])
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("level_a", [LEAD, ROLE])
    async def test_a_lead_or_role_read_that_fails_grants_nobody_and_queues_the_issue(self, level_a: list) -> None:
        _, ((record, perms),), _, point = await self._run(level_a, lead={"accountId": "acc-1"},
                                                     actors=[{"type": "atlassian-user-role-actor",
                                                              "actorUser": {"accountId": "acc-1"}}], status=503)
        assert (record.access_rule, perms, record.source_updated_at) == (AccessRule.STRICT, [], 1)
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == ["1001"]

    @pytest.mark.asyncio
    async def test_a_role_group_without_a_known_id_leaves_the_level_unknown_and_queues_the_issue(self) -> None:
        """The reporter is a user actor of the role too, but the role's other group cannot be read: the level is unknown."""
        _, ((record, perms),), pages, point = await self._run(self.ROLE, actors=[
            {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}},
            {"type": "atlassian-group-role-actor", "displayName": "Renamed devs"}])
        assert (pages, record.access_rule, perms, record.source_updated_at) == ([], AccessRule.STRICT, [], 1)
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == ["1001"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("level_a", "status", "named"), [
        (LEAD, 403, "Get project PROJ"), (ROLE, 404, "Get project role PROJ_10002")])
    async def test_a_refused_lead_or_role_read_is_logged_with_its_status_and_jira_s_message(
        self, level_a: list, status: int, named: str, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """What failed, where, and Jira's own message (ErrorCollection.errorMessages), with an address in it masked."""
        c, pages, point = TestAReindexReadsOnlyTheGroupsItsIssuesNeed._reindex(
            TestReindexAppliesTheLevel.LEVELED, [{"type": "reporter"}], level_a)
        refused = _resp(status, {"errorMessages": ["Caller rep@x.com may not administer PROJ"], "errors": {"k": "v@x.com"}})
        c.data_source.get_project = c.data_source.get_project_role = AsyncMock(return_value=refused)
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert (record.access_rule, perms) == (AccessRule.STRICT, [])
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == ["1001"]
        (line,) = [r.getMessage() for r in caplog.records if named in r.getMessage()]
        assert f"HTTP {status}" in line and "may not administer PROJ" in line
        assert "@x.com" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_reporter_in_the_level_s_role_as_a_user_reads_it(self) -> None:
        c, ((record, perms), _), _, _ = await self._run(
            self.ROLE, actors=[{"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}}], issues=2)
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        assert c.data_source.get_project_role.await_args.kwargs == {
            "projectIdOrKey": "PROJ", "id": 10002, "excludeInactiveUsers": True}
        assert c.data_source.get_project_role.await_count == 1

    @pytest.mark.asyncio
    async def test_a_reporter_in_the_level_s_role_through_a_group_reads_it(self) -> None:
        """The role's group is read now, through the reindex's group cache, by the id ``actorGroup`` documents."""
        _, ((record, perms),), pages, point = await self._run(self.ROLE, actors=[{
            "type": "atlassian-group-role-actor", "displayName": "big", "actorGroup": {"groupId": "group-big"}}])
        assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
        assert pages == ["group-big"] * (BIG_GROUP // 50)

    @pytest.mark.asyncio
    async def test_a_role_group_past_the_cap_leaves_the_role_unknown(self) -> None:
        """Never read as a role without that group's members: the reporter gets no grant and the next sync decides."""
        with patch("app.connectors.sources.atlassian.jira_cloud.connector.REINDEX_GROUP_READS", 0):
            _, ((record, perms),), pages, point = await self._run(self.ROLE, actors=[{
                "type": "atlassian-group-role-actor", "actorGroup": {"groupId": "group-big"}}])
        assert (pages, record.access_rule, perms) == ([], AccessRule.STRICT, [])
        assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == ["1001"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("level_a", [LEAD, ROLE])
    async def test_the_reindex_reads_what_the_sync_would(self, level_a: list) -> None:
        """The readers a reindex reads equal those the sync derives from the same lead and role members."""
        c, _, _, _ = await self._run(level_a, lead={"accountId": "acc-1"},
                               actors=[{"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}}])
        security = await c._load_issue_security("PROJ", cached=False)
        security.browse_holders = [{"type": "reporter"}]
        await c._reindex_level_readers(security, "PROJ", {"acc-1": _users()[0]}, "10102")
        c._role_members = {"PROJ_projectLead": [_users()[0]], "PROJ_10002": [_users()[0]]}
        assert security.level_readers["10102"] == c._level_readers(security, "PROJ", {"acc-1": _users()[0]})["10102"]
        assert security.level_readers["10102"]


def test_a_role_group_actor_is_found_by_the_documented_actor_group_id() -> None:
    c = _connector()
    members, unread = c._role_actor_members("PROJ/Developers", [
        {"type": "atlassian-group-role-actor", "displayName": "Devs", "actorGroup": {"groupId": "group-1", "name": "devs"}}],
        {}, {}, {"group-1": [_users()[0]]})
    assert ([u.source_user_id for u in members], unread) == (["acc-1"], None)
    assert c._role_actor_members("x", [{"type": "atlassian-group-role-actor", "actorGroup": {"groupId": "g"}}],
                                 {}, {}, {"g": None})[1] == "g"
    # Left out of the group read on purpose, as the sync does: app accounts, no people to grant.
    assert c._role_actor_members("x", [{"type": "atlassian-group-role-actor", "actorGroup": {
        "groupId": "g-addons", "name": "atlassian-addons-admin"}}], {}, {}, {}) == ([], None)



@pytest.mark.parametrize("actor", [
    {"type": "atlassian-group-role-actor", "displayName": "Renamed devs"},  # no id, a name no group read resolves
    {"type": "atlassian-group-role-actor", "actorGroup": {}},
    {"type": "atlassian-group-role-actor", "actorGroup": {"groupId": "group-not-read"}},  # an id outside the groups read
])
def test_a_role_group_actor_whose_members_are_not_known_leaves_the_role_unread(actor: dict) -> None:
    """``actorGroup.groupId`` is not a required field (ProjectRoleGroup), so a group may come without one: its members are
    not known, which is never "no members"."""
    c = _connector()
    members, unread = c._role_actor_members("PROJ/Developers", [actor], {}, {}, {"group-1": [_users()[0]]})
    assert unread


@pytest.mark.asyncio
async def test_the_sync_keeps_a_role_whose_group_actor_it_cannot_resolve() -> None:
    """The role is not written with that group's members missing: its stored members stay, the project is reported failed,
    and the level readers hold no members for it (unknown, so no one reads a leveled issue alone through it)."""
    c = _connector()
    roles = _resp(200, {"Developers": "https://x.atlassian.net/rest/api/3/project/PROJ/role/10002"})
    role = _resp(200, {"name": "Developers", "actors": [
        {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}},
        {"type": "atlassian-group-role-actor", "displayName": "Renamed devs"}]})
    c._call_with_retry = AsyncMock(side_effect=[roles, role])
    users = {u.source_user_id: u for u in _users()}
    key, project_roles, failed = await c._fetch_project_roles("PROJ", {}, users, {"group-1": [_users()[0]]})
    assert (key, project_roles) == ("PROJ", [])
    # Held for the caller, which reports the project as failed unless the unread bound closes the role.
    assert [(k, r.source_role_id, g) for k, r, _, g in c._roles_unread] == [("PROJ", "PROJ_10002", "Renamed devs")]
    assert ("role", "PROJ_10002") in c._kept_after_refusal


GROUP_ONLY_LEVEL = {"10102": [{"type": "group", "value": "group-1"}]}
REPORTER_AND_ASSIGNEE = frozenset({user_grant_key("rep@x.com", "acc-1"), user_grant_key("asg@x.com", "acc-2")})


def _access_with_readers(
    level_readers: dict | None, browse: list | None, *, subtask: bool = False, members: dict | None = None,
) -> tuple[tuple, dict]:
    """``(the issue's access, the level digests)`` for issue 1001 in level 10102 with these level readers."""
    context = _present(members)
    context.browse_holders, context.level_readers = browse, level_readers
    record = _stored_ticket()
    perms, skipped, settled = apply_issue_access_settled(
        record, _issue({"id": "10102", "name": "Leadership"})["fields"], is_subtask=subtask, project_key="PROJ",
        context=context, user_by_account_id={u.source_user_id: u for u in _users()}, project_id=PROJECT_ID)
    access = (record.access_rule, record.access_group_external_id, record.rewrite_permissions,
              sorted((str(p.entity_type), p.email or p.external_id) for p in perms), skipped, settled)
    return access, context.level_fingerprints(split=True)


@pytest.mark.parametrize("browse", [[], None])
@pytest.mark.parametrize("subtask", [False, True])
@pytest.mark.parametrize("members", [None, GROUP_ONLY_LEVEL])
@pytest.mark.parametrize("readers", [
    None, {}, {"10102": None}, {"10102": frozenset()}, {"10102": REPORTER_AND_ASSIGNEE}, {"10102": frozenset({("*", "*")})},
])
def test_level_readers_change_nothing_without_browse_holders(
    browse: list | None, subtask: bool, members: dict | None, readers: dict | None,
) -> None:
    """The proof behind the skip: with no per-issue Browse holder, an issue's access and the level digests the sync saves are
    the same whatever the level readers hold (unread, empty, its reporter and assignee, or anyone). So not reading them
    neither adds nor drops a reader."""
    access, digests = _access_with_readers(readers, browse, subtask=subtask, members=members)
    unread_access, unread_digests = _access_with_readers(None, browse, subtask=subtask, members=members)
    assert access == unread_access
    assert digests == unread_digests


@pytest.mark.parametrize("readers", [REPORTER_AND_ASSIGNEE, frozenset({("*", "*")})])
def test_the_control_level_readers_do_change_access_once_browse_names_the_reporter(readers: frozenset) -> None:
    """The control for the proof above: the same readers do change who reads the issue once Browse names its reporter. The
    level holds groups only, so the readers are the only way the reporter is in it."""
    named, _ = _access_with_readers({"10102": readers}, [{"type": "reporter"}], members=GROUP_ONLY_LEVEL)
    not_named, _ = _access_with_readers({"10102": frozenset()}, [{"type": "reporter"}], members=GROUP_ONLY_LEVEL)
    assert (named[0], named[3]) == (AccessRule.OPEN, [("EntityType.USER", "rep@x.com")])
    assert (not_named[0], not_named[3]) == (AccessRule.STRICT, [])


class TestSubtasksFollowTheirParent:
    """Jira copies a parent's level onto its sub-tasks without moving their `updated`, so the
    sub-tasks of every issue read again are read again too."""

    async def _run(self, *, stored: dict, batch_issues: list[dict] | None = None, fail_subtasks: bool = False,
                   is_new_project: bool = False) -> tuple[JiraConnector, list, dict]:
        c = _connector()
        records = [await _build(c, issue, _present()) for issue in batch_issues or []]
        point = {"issue_placement": ISSUE_PLACEMENT,
                 "security_levels": json.dumps(_present().level_fingerprints(split=True)), **stored}
        if not is_new_project:  # a project is new when its checkpoint has neither timestamp
            point.update(last_sync_time=5, last_issue_updated=1_700_000_000_000)
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda _key: dict(point))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda _key, data: point.update(data))
        c._load_issue_security = AsyncMock(return_value=_present())
        c._process_new_records = AsyncMock()
        calls = []

        async def fetch(key, pid, users, last_sync, resume, *, is_new_project=False, level_ids=None,
                        leveled_only=False, stored_only=False, parent_ids=None) -> AsyncIterator:
            calls.append({"stored_only": stored_only, "parent_ids": parent_ids})
            if parent_ids and fail_subtasks:
                raise JiraReadError(503, "unavailable")
            if len(calls) == 1 and records:
                yield records, False, 1_700_000_100_000

        c._fetch_issues_batched = fetch
        with contextlib.suppress(JiraReadError):
            await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        return c, calls, point

    @staticmethod
    def _parent_and_subtask() -> list[dict]:
        sub = _issue(None, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        return [_issue(None, key="PROJ-1", issue_id="1001"), sub]

    @pytest.mark.asyncio
    async def test_a_story_filed_under_its_epic_names_its_children_too(self) -> None:
        """Its level reaches its sub-tasks whether or not it hangs under an epic."""
        story, sub = self._parent_and_subtask()
        story["fields"]["parent"] = {"id": "2001", "key": "PROJ-9"}
        _, calls, _ = await self._run(stored={}, batch_issues=[_epic(), story, sub])
        assert calls[1:] == [{"stored_only": True, "parent_ids": ["2001", "1001"]}]

    @pytest.mark.asyncio
    async def test_the_subtasks_of_issues_read_again_are_read_again(self) -> None:
        _, calls, point = await self._run(stored={}, batch_issues=self._parent_and_subtask())
        # Only the issue that is not itself a sub-task names its children.
        assert calls[1:] == [{"stored_only": True, "parent_ids": ["1001"]}]
        assert json.loads(point["subtask_parents"]) == []

    @pytest.mark.asyncio
    async def test_a_failed_subtask_read_keeps_the_parents_for_the_next_sync(self) -> None:
        """The parents wait in the checkpoint, and the cursor does not pass the batch."""
        _, _, point = await self._run(stored={}, batch_issues=self._parent_and_subtask(), fail_subtasks=True)
        assert json.loads(point["subtask_parents"]) == ["1001"]
        assert point["last_issue_updated"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_parents_left_by_a_failed_sync_are_read_again_with_nothing_new(self) -> None:
        _, calls, point = await self._run(stored={"subtask_parents": json.dumps(["1001"])})
        assert calls[0] == {"stored_only": True, "parent_ids": ["1001"]}
        assert [call["parent_ids"] for call in calls[1:]] == [None]
        assert json.loads(point["subtask_parents"]) == []

    @pytest.mark.asyncio
    async def test_a_project_read_from_the_start_reads_no_subtask_twice(self) -> None:
        _, calls, _ = await self._run(stored={}, batch_issues=self._parent_and_subtask(), is_new_project=True)
        assert all(call["parent_ids"] is None for call in calls)


class TestIssuePlacementPass:
    """A project synced under another placement rule has every stored issue read once, so
    each story moves to where the current rule files it; the marker then stops the pass."""

    _sync = TestMemberChangeRereadsOnlyTheAffectedLevels._sync

    @pytest.mark.asyncio
    async def test_a_project_filed_under_the_previous_rule_rereads_every_stored_issue_once(self) -> None:
        """Every story was filed under its project; those under a readable epic now move under it."""
        c, _, calls, _ = await self._sync(
            stored_levels=_present().level_fingerprints(split=True), placement="subtasks-under-parent")
        assert calls[1:] == [
            {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": False, "stored_only": True}]
        assert c.issues_sync_point.update_sync_point.await_args.args[1]["issue_placement"] == ISSUE_PLACEMENT

    @pytest.mark.asyncio
    async def test_a_project_without_the_marker_rereads_every_stored_issue_once(self) -> None:
        c, ctx, calls, result = await self._sync(stored_levels=_present().level_fingerprints(split=True), placement=None)
        assert calls[1:] == [
            {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": False, "stored_only": True}]
        assert c.issues_sync_point.update_sync_point.await_args.args[1]["issue_placement"] == ISSUE_PLACEMENT
        assert result["leveled_reread"] is True

    @pytest.mark.asyncio
    async def test_a_project_with_the_marker_is_not_reread(self) -> None:
        _, _, calls, result = await self._sync(stored_levels=_present().level_fingerprints(split=True))
        assert len(calls) == 1
        assert result["leveled_reread"] is False

    @pytest.mark.asyncio
    async def test_a_failed_pass_leaves_the_marker_unsaved_so_the_next_sync_retries(self) -> None:
        async def fail_second(c: JiraConnector, calls: list) -> None:
            if len(calls) == 2:
                raise JiraReadError(503, "unavailable")

        c = _connector()
        with pytest.raises(JiraReadError):
            await self._sync(
                stored_levels=_present().level_fingerprints(split=True), placement=None, fetch_fn=fail_second, connector=c)
        assert not any("issue_placement" in call.args[1] and call.args[1]["issue_placement"] == ISSUE_PLACEMENT
                       for call in c.issues_sync_point.update_sync_point.await_args_list)

    @pytest.mark.asyncio
    async def test_an_issue_the_pass_skipped_leaves_the_marker_unsaved(self) -> None:
        """A skipped issue may still hang off its epic, and nothing else would read it again."""
        async def skip_one(c: JiraConnector, calls: list) -> None:
            if len(calls) == 2:
                c._issue_build_failures += 1

        c, _, _, _ = await self._sync(
            stored_levels=_present().level_fingerprints(split=True), placement=None, fetch_fn=skip_one)
        assert not any(call.args[1].get("issue_placement") == ISSUE_PLACEMENT
                       for call in c.issues_sync_point.update_sync_point.await_args_list)


class TestLevelPassAfterFailuresAndRemovals:
    _sync = TestMemberChangeRereadsOnlyTheAffectedLevels._sync

    @pytest.mark.asyncio
    async def test_a_transient_failure_marks_the_project_and_the_next_settled_sync_rereads(self) -> None:
        """Issues updated during a 503 are closed with their old grants kept; the digests then
        match again, so only the marker makes the next sync re-read them."""
        digests = _present().level_fingerprints(split=True)
        transient = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True)
        c, _, calls, _ = await self._sync(stored_levels=digests, ctx=transient)
        assert len(calls) == 1  # nothing settled to re-read with
        saved = json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"])
        assert saved == {**digests, "*": "retry"}

        _, _, calls, _ = await self._sync(stored_levels=saved)
        assert calls[1]["leveled_only"] is True and calls[1]["stored_only"] is True

    @pytest.mark.asyncio
    async def test_the_marker_is_saved_before_the_walk(self) -> None:
        """A page that fails after the cursor moved leaves issues written with their level
        unapplied; only a marker saved up front re-reads them."""
        saved_before: list = []

        async def record_then_fail(c: JiraConnector, calls: list) -> None:
            saved_before.extend(
                json.loads(call.args[1]["security_levels"])
                for call in c.issues_sync_point.update_sync_point.await_args_list
                if "security_levels" in call.args[1])
            raise JiraReadError(503, "later page")

        for ctx in (IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True),
                    IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)):
            saved_before.clear()
            with pytest.raises(JiraReadError):
                await self._sync(stored_levels={"10102": "d"}, ctx=ctx, fetch_fn=record_then_fail)
            assert saved_before == [{"10102": "d", "*": "retry"}]

    @pytest.mark.asyncio
    async def test_a_settled_scheme_saves_nothing_before_the_walk(self) -> None:
        """A readable scheme, and a 403 already recorded, need no marker ahead of the walk."""
        saved_before: list = []

        async def record(c: JiraConnector, calls: list) -> None:
            if len(calls) == 1:
                saved_before.extend(
                    call for call in c.issues_sync_point.update_sync_point.await_args_list
                    if "security_levels" in call.args[1])

        forbidden = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        await self._sync(stored_levels={"10102": "old"}, fetch_fn=record)
        await self._sync(stored_levels={"*": "unreadable"}, ctx=forbidden, fetch_fn=record)
        await self._sync(stored_levels={"10102": "d"}, ctx=IssueSecurityContext(knowledge=SchemeKnowledge.NONE),
                         fetch_fn=record)
        assert saved_before == []

    @pytest.mark.asyncio
    async def test_a_level_naming_a_role_read_in_part_applies_the_role_list_kept(self) -> None:
        """The role's groups are the ones last read (``roles_for_levels``), not none: nothing changed, nothing re-read."""
        members = {"10102": [{"type": "applicationRole", "parameter": "jira-software"}]}
        roles = {"jira-software": [{"groupId": "g-1"}]}
        c = _connector()
        c._app_roles_unread, c._app_roles_mapping, c._level_roles = True, {}, roles
        stored = _present(members=members).level_fingerprints(roles, split=True)
        c, ctx, calls, _ = await self._sync(stored_levels=stored, ctx=_present(members=members), connector=c)
        assert ctx.members_readable is True and ctx.defer_checkpoint is False
        assert len(calls) == 1
        record, _, _ = await _build(c, _issue({"id": "10102"}), ctx)
        assert (record.access_group_external_id, record.rewrite_permissions) == ("level:p-1:10102", True)

    @pytest.mark.asyncio
    async def test_a_role_read_in_part_does_not_hold_levels_without_roles(self) -> None:
        c = _connector()
        c._app_roles_unread = True
        _, ctx, _, _ = await self._sync(stored_levels={"10102": "old"}, connector=c)
        assert ctx.members_readable is True and ctx.defer_checkpoint is False

    @pytest.mark.asyncio
    async def test_a_transient_members_read_is_marked_too(self) -> None:
        ctx = _present()
        ctx.members_readable, ctx.defer_checkpoint = False, True
        c, _, _, _ = await self._sync(stored_levels={"10102": "d"}, ctx=ctx)
        saved = json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"])
        assert saved == {"10102": "d", "*": "retry"}

    @pytest.mark.asyncio
    async def test_the_retry_marker_is_saved_even_when_an_issue_failed(self) -> None:
        async def fail(c: JiraConnector, calls: list) -> None:
            c._issue_build_failures += 1

        transient = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True)
        c, _, _, _ = await self._sync(stored_levels={"10102": "d"}, ctx=transient, fetch_fn=fail)
        saved = json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"])
        assert saved["*"] == "retry"

    @pytest.mark.asyncio
    async def test_a_removed_scheme_rereads_every_stored_issue(self) -> None:
        """Removing the scheme clears every issue's level without touching `updated`."""
        c, _, calls, _ = await self._sync(stored_levels={"10102": "d"}, ctx=IssueSecurityContext(knowledge=SchemeKnowledge.NONE))
        assert calls[1] == {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": False,
                            "stored_only": True}
        assert json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"]) == {}

    @pytest.mark.asyncio
    async def test_issues_closed_while_unreadable_are_reopened_when_the_scheme_is_gone(self) -> None:
        _, _, calls, _ = await self._sync(stored_levels={"*": "unreadable"}, ctx=IssueSecurityContext(knowledge=SchemeKnowledge.NONE))
        assert calls[1]["leveled_only"] is False and calls[1]["level_ids"] is None

    @pytest.mark.asyncio
    async def test_a_project_that_never_had_a_scheme_is_not_reread(self) -> None:
        for stored in ({}, None):
            _, _, calls, _ = await self._sync(stored_levels=stored, ctx=IssueSecurityContext(knowledge=SchemeKnowledge.NONE))
            assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_removed_scheme_keeps_the_old_map_when_an_issue_failed(self) -> None:
        async def fail(c: JiraConnector, calls: list) -> None:
            if len(calls) == 2:
                c._issue_build_failures += 1

        c, _, _, _ = await self._sync(
            stored_levels={"10102": "d"}, ctx=IssueSecurityContext(knowledge=SchemeKnowledge.NONE), fetch_fn=fail)
        assert not any("security_levels" in call.args[1] for call in c.issues_sync_point.update_sync_point.await_args_list)

    @pytest.mark.asyncio
    async def test_a_lasting_failure_after_a_transient_one_closes_the_leveled_issues(self) -> None:
        """A retry marker kept the old grants; a 403 must now clear them."""
        forbidden = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        c, _, calls, _ = await self._sync(stored_levels={"10102": "d", "*": "retry"}, ctx=forbidden)
        assert calls[1]["leveled_only"] is True
        saved = json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"])
        assert saved == {"10102": "d", "*": "unreadable"}

    @pytest.mark.asyncio
    async def test_a_group_leaving_an_application_role_rewrites_the_level_group_and_reads_its_issues_again(self) -> None:
        """The level's group holds the role's groups once, written before the walk; its issues are read again for the
        sub-tasks, which hold a copy."""
        ctx = _present(members={"10102": [{"type": "applicationRole", "parameter": "jira-software"}]})
        c = _connector()
        c._level_roles = {"jira-software": [{"groupId": "g-1"}]}
        stored = ctx.level_fingerprints({"jira-software": [{"groupId": "g-1"}, {"groupId": "g-removed"}]}, split=True)
        _, _, calls, _ = await self._sync(stored_levels=stored, ctx=ctx, connector=c)
        assert [call["level_ids"] for call in calls] == [None, ["10102"]]
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert group.external_group_id == "level:p-1:10102"
        assert [(g.entity_type, g.external_id) for g in grants] == [(EntityType.GROUP, "g-1")]

    @pytest.mark.asyncio
    async def test_a_project_stored_before_level_groups_reads_every_leveled_issue_again(self) -> None:
        """Its issues hold a copy of their level's holders; read again, each drops it for its level's group."""
        ctx = _present()
        c, _, calls, _ = await self._sync(stored_levels=ctx.level_fingerprints(), ctx=ctx)
        assert calls[1]["level_ids"] == ["10102"]
        assert json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"]) == \
            ctx.level_fingerprints(split=True)

    @pytest.mark.asyncio
    async def test_a_personal_connector_reads_no_scheme_and_enforces_nothing(self) -> None:
        from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
            JiraCloudPersonalConnector,
        )
        c = _connector()
        c._enforces_issue_security = JiraCloudPersonalConnector._enforces_issue_security
        c._call_with_retry = AsyncMock()
        ctx = await c._load_issue_security("PROJ")
        assert ctx.enforce is False and ctx.knowledge == SchemeKnowledge.NONE
        c._call_with_retry.assert_not_awaited()
        record, perms, _ = await _build(c, _issue({"id": "10102"}), ctx)
        assert perms == [] and record.access_rule == AccessRule.OPEN


class TestStoredOnlyPass:
    @pytest.mark.asyncio
    async def test_the_security_pass_drops_the_date_filters(self) -> None:
        """A stored issue outside the modified window must still be rewritten."""
        c = _connector()
        modified = MagicMock()
        modified.get_value = MagicMock(return_value=(1_600_000_000_000, None))
        created = MagicMock()
        created.get_value = MagicMock(return_value=(1_500_000_000_000, 1_650_000_000_000))
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: {"modified": modified, "created": created}.get(
            getattr(key, "value", key)))
        c.data_source = MagicMock()
        seen: list = []

        async def search(**kw: object) -> MagicMock:
            seen.append(kw["jql"])
            return _resp(200, {"issues": []})

        c._search_issues_with_retry = search
        async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, is_new_project=True, leveled_only=True):
            pass
        async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, is_new_project=True,
                                               leveled_only=True, stored_only=True):
            pass
        assert "updated >" in seen[0] and "created" in seen[0]  # the filters apply elsewhere
        where = seen[1].split("ORDER BY")[0]
        assert where == 'project = "PROJ" AND level is not EMPTY '

    @pytest.mark.asyncio
    async def test_the_security_pass_rewrites_stored_issues_only(self) -> None:
        """An issue the filters never stored stays unread, and a stub is not promoted to a record."""
        c = _connector()
        c._issue_security[PROJECT_ID] = _present()
        stored = MagicMock(id="rid", version=2, source_updated_at=c._parse_jira_timestamp("2024-06-15T10:00:00.000+0000"),
                           is_placeholder=False)
        stub = MagicMock(id="sid", version=0, source_updated_at=None, is_placeholder=True)
        by_id = {"1001": stored, "1002": stub, "1003": None}
        c.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: by_id[external_record_id])
        issues = [_issue({"id": "10102"}, key=f"PROJ-{n}", issue_id=f"100{n}") for n in (1, 2, 3)]
        records, _ = await c._build_issue_records(issues, PROJECT_ID, _users(), is_new_project=True, stored_only=True)
        assert [r.external_record_id for r, _, _ in records] == ["1001"]
        assert records[0][0].access_rule == AccessRule.STRICT  # rebuilt though unchanged
        assert records[0][0].access_group_external_id == "level:p-1:10102"

        records, _ = await c._build_issue_records(issues, PROJECT_ID, _users(), is_new_project=True)
        assert len(records) == 3  # the normal pass is unchanged


LEVEL = {"id": "10102", "name": "Leadership"}
# Holders that name the same people on every issue, so two issues in the level read alike.
SAME_ON_EVERY_ISSUE = {"10102": [
    {"type": "group", "value": "group-1"},
    {"type": "user", "parameter": "acc-9", "user": {"accountId": "acc-9"}},
    {"type": "projectRole", "parameter": "10003", "projectRole": {"id": "10003", "name": "Developers"}},
]}


def _epic(security: dict | None = None, *, issue_id: str = "2001", key: str = "PROJ-9",
          project: dict | None = None) -> dict:
    epic = _issue(security, key=key, issue_id=issue_id)
    epic["fields"]["issuetype"] = {"name": "Epic", "hierarchyLevel": 1}
    if project is not None:
        epic["fields"]["project"] = project
    return epic


def _story(security: dict | None = None, *, parent_id: str = "2001", parent_key: str = "PROJ-9") -> dict:
    story = _issue(security, key="PROJ-3", issue_id="3001")
    story["fields"]["issuetype"] = {"name": "Story", "hierarchyLevel": 0}
    story["fields"]["parent"] = {"id": parent_id, "key": parent_key}
    return story


def _bulk_fetch(*answers: MagicMock) -> MagicMock:
    ds = MagicMock()
    ds.bulk_fetch_issues = AsyncMock(side_effect=list(answers))
    return ds


async def _story_record(
    c: JiraConnector, issues: list[dict], context: IssueSecurityContext | None = None,
    existing: MagicMock | None = None,
) -> TicketRecord:
    c._issue_security[PROJECT_ID] = context or _present()
    c.data_entities_processor.get_record_by_external_id = AsyncMock(
        side_effect=lambda **kw: existing if kw.get("external_record_id") == "3001" else None)
    records, _ = await c._build_issue_records(issues, PROJECT_ID, _users())
    return next(r for r, _, _ in records if r.external_record_id == "3001")


class TestStoriesUnderTheirEpic:
    """A story inherits the record it is filed under, so it goes under its epic only when
    everyone who reads the story reads the epic; Jira does not copy an epic's level onto it."""

    @pytest.mark.asyncio
    async def test_a_story_goes_under_an_epic_with_no_level_in_its_project(self) -> None:
        c = _connector()
        c._get_fresh_datasource = AsyncMock()
        story = await _story_record(c, [_epic(), _story()])
        assert story.parent_external_record_id == "2001"
        c._get_fresh_datasource.assert_not_awaited()  # the epic was read in the same page

    @pytest.mark.asyncio
    async def test_a_leveled_story_goes_under_an_epic_with_no_level(self) -> None:
        """Filed under the epic, it takes its access from its own level's group, not from the epic."""
        story = await _story_record(_connector(), [_epic(), _story(LEVEL)])
        assert story.parent_external_record_id == "2001"
        assert (story.access_rule, story.access_group_external_id) == (AccessRule.STRICT, "level:p-1:10102")

    @pytest.mark.asyncio
    async def test_a_story_without_the_epics_level_hangs_off_its_project(self) -> None:
        story = await _story_record(_connector(), [_epic(LEVEL), _story()])
        assert story.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_a_story_with_another_level_hangs_off_its_project(self) -> None:
        other = {"id": "10103", "name": "Board"}
        context = _present({**SAME_ON_EVERY_ISSUE, "10103": SAME_ON_EVERY_ISSUE["10102"]})
        story = await _story_record(_connector(), [_epic(LEVEL), _story(other)], context)
        assert story.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_a_story_with_the_epics_level_goes_under_it_when_the_level_names_no_one_per_issue(self) -> None:
        story = await _story_record(_connector(), [_epic(LEVEL), _story(LEVEL)], _present(SAME_ON_EVERY_ISSUE))
        assert story.parent_external_record_id == "2001"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("holder", [
        {"type": "reporter"}, {"type": "assignee"},
        {"type": "userCustomField", "parameter": "customfield_10050"},
        {"type": "groupCustomField", "parameter": "customfield_10051"},
    ])
    async def test_a_level_that_names_people_per_issue_keeps_the_story_off_its_epic(self, holder: dict) -> None:
        """The epic's reporter is not the story's: the story's reporter could not read the epic."""
        context = _present({"10102": [*SAME_ON_EVERY_ISSUE["10102"], holder]})
        story = await _story_record(_connector(), [_epic(LEVEL), _story(LEVEL)], context)
        assert story.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_a_level_whose_members_are_unread_keeps_the_story_off_its_epic(self) -> None:
        context = _present(SAME_ON_EVERY_ISSUE)
        context.members_readable = False
        story = await _story_record(_connector(), [_epic(LEVEL), _story(LEVEL)], context)
        assert story.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_an_epic_in_another_project_is_not_asked_for(self) -> None:
        c = _connector()
        c._get_fresh_datasource = AsyncMock()
        story = await _story_record(c, [_story(parent_id="2001", parent_key="OTHER-9")])
        assert story.parent_external_record_id is None
        c._get_fresh_datasource.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_epic_jira_places_in_another_project_keeps_the_story_off_it(self) -> None:
        moved = _epic(project={"id": "p-2", "key": "PROJ"})
        story = await _story_record(_connector(), [moved, _story()])
        assert story.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_an_epic_not_read_this_sync_is_read_in_bulk_once(self) -> None:
        c = _connector()
        ds = _bulk_fetch(_resp(200, {"issues": [_epic()]}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        first = await _story_record(c, [_story()])
        again = await _story_record(c, [_story()])
        assert first.parent_external_record_id == again.parent_external_record_id == "2001"
        ds.bulk_fetch_issues.assert_awaited_once()
        kwargs = ds.bulk_fetch_issues.await_args.kwargs
        assert kwargs["issueIdsOrKeys"] == ["2001"]
        assert {"security", "project", "issuetype"} <= set(kwargs["fields"])

    @pytest.mark.asyncio
    async def test_an_epic_jira_does_not_return_keeps_the_story_off_it_without_retrying(self) -> None:
        """Deleted, or hidden from the connector's account: asking again would not help."""
        c = _connector()
        c._get_fresh_datasource = AsyncMock(return_value=_bulk_fetch(_resp(200, {"issues": []})))
        story = await _story_record(c, [_story()])
        assert story.parent_external_record_id is None
        assert c._parents_to_retry == set()

    @pytest.mark.asyncio
    async def test_an_epic_without_its_security_field_is_unknown(self) -> None:
        """A missing field is not "no level"; the story must not inherit an epic it may not read."""
        epic = _epic()
        del epic["fields"]["security"]
        story = await _story_record(_connector(), [epic, _story()])
        assert story.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_a_failed_epic_read_keeps_a_story_under_the_epic_it_was_filed_under(self) -> None:
        c = _connector()
        c._get_fresh_datasource = AsyncMock(return_value=_bulk_fetch(_resp(500)))
        stored = MagicMock(id="rid", version=1, source_updated_at=0, is_placeholder=False,
                           parent_external_record_id="2001")
        story = await _story_record(c, [_story()], existing=stored)
        assert story.parent_external_record_id == "2001"
        assert c._parents_to_retry == {"2001"}

    @pytest.mark.asyncio
    async def test_a_failed_epic_read_leaves_a_story_filed_elsewhere_on_its_project(self) -> None:
        c = _connector()
        c._get_fresh_datasource = AsyncMock(return_value=_bulk_fetch(_resp(500)))
        stored = MagicMock(id="rid", version=1, source_updated_at=0, is_placeholder=False,
                           parent_external_record_id=None)
        story = await _story_record(c, [_story()], existing=stored)
        assert story.parent_external_record_id is None
        assert c._parents_to_retry == {"2001"}

    @pytest.mark.asyncio
    async def test_an_epic_read_later_in_the_sync_replaces_a_failed_read(self) -> None:
        c = _connector()
        c._get_fresh_datasource = AsyncMock(return_value=_bulk_fetch(_resp(500)))
        await _story_record(c, [_story()])
        story = await _story_record(c, [_epic(), _story()])
        assert story.parent_external_record_id == "2001"

    @pytest.mark.asyncio
    async def test_a_sub_task_still_goes_under_a_parent_with_no_level(self) -> None:
        """Read in the same page, so its level is not asked for again."""
        sub = _story()
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        c = _connector()
        c._get_fresh_datasource = AsyncMock()
        assert (await _story_record(c, [_issue(None, key="PROJ-9", issue_id="2001"), sub])).parent_external_record_id == "2001"
        c._get_fresh_datasource.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_sub_task_read_with_no_level_stays_under_a_leveled_parent(self) -> None:
        """Jira gives a sub-task its parent's level, so a null is a payload behind it: off the parent it would be
        read by the whole project."""
        sub = _story()
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        parent = _issue(LEVEL, key="PROJ-9", issue_id="2001")
        assert (await _story_record(_connector(), [parent, sub])).parent_external_record_id == "2001"

    @pytest.mark.asyncio
    async def test_a_malformed_issue_in_the_page_does_not_stop_the_others(self) -> None:
        broken = {"id": "4001", "key": "PROJ-4", "fields": {"issuetype": "Story", "parent": "PROJ-9", "project": "PROJ"}}
        story = await _story_record(_connector(), [broken, _epic(), _story()])
        assert story.parent_external_record_id == "2001"


class TestEpicsReadAgainNextSync:
    _sync = TestMemberChangeRereadsOnlyTheAffectedLevels._sync

    @pytest.mark.asyncio
    async def test_epics_that_could_not_be_read_wait_in_the_checkpoint(self) -> None:
        """The stories under them are placed again first thing next sync."""
        async def unread_epic(c: JiraConnector, calls: list) -> None:
            c._parents_to_retry.add("2001")

        c, _, _, _ = await self._sync(stored_levels=_present().level_fingerprints(split=True), fetch_fn=unread_epic)
        saved = [json.loads(call.args[1]["subtask_parents"])
                 for call in c.issues_sync_point.update_sync_point.await_args_list if "subtask_parents" in call.args[1]]
        assert saved[-1] == ["2001"]

    @pytest.mark.asyncio
    async def test_epics_wait_in_the_checkpoint_before_the_cursor_passes_their_stories(self) -> None:
        """A later page that fails must not lose them: the cursor has already passed the stories."""
        c = _connector()
        record, _, _ = await _build(c, _issue(None), _present())
        point = {"issue_placement": ISSUE_PLACEMENT, "security_levels": json.dumps(_present().level_fingerprints(split=True)),
                 "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda _key: dict(point))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda _key, data: point.update(data))
        c._load_issue_security = AsyncMock(return_value=_present())
        c._process_new_records = AsyncMock()

        async def fetch(key, pid, users, last_sync, resume, *, is_new_project=False, level_ids=None,
                        leveled_only=False, stored_only=False, parent_ids=None) -> AsyncIterator:
            if parent_ids:
                return
            c._parents_to_retry.add("2001")
            yield [(record, [], True)], True, 1_700_000_100_000
            raise JiraReadError(503, "unavailable")

        c._fetch_issues_batched = fetch
        with pytest.raises(JiraReadError):
            await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert point["last_issue_updated"] == 1_700_000_100_000
        assert json.loads(point["subtask_parents"]) == ["2001"]

    @pytest.mark.asyncio
    async def test_nothing_is_saved_when_every_epic_was_read(self) -> None:
        c, _, _, _ = await self._sync(stored_levels=_present().level_fingerprints(split=True))
        assert not any("subtask_parents" in call.args[1]
                       for call in c.issues_sync_point.update_sync_point.await_args_list)


class TestReindexFollowsTheCurrentIssueType:
    @staticmethod
    async def _reindexed(parent: dict) -> TicketRecord:
        # The one read: the parent's level, which it does not have.
        c = _reindex_connector(_issue(None, key="PROJ-2", issue_id="1002", updated="2025-01-01T00:00:00.000+0000"),
                               [_resp(200, {"issues": [_issue(None, key="PROJ-1", issue_id="1001")]})])
        sub = _issue(None, key="PROJ-2", issue_id="1002", updated="2025-01-01T00:00:00.000+0000")
        sub["fields"].update(issuetype={"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}, parent=parent)
        c._get_fresh_datasource.return_value.get_issue.return_value = _resp(200, sub)
        c._load_issue_security = AsyncMock(return_value=_present())
        record, _ = await c._check_and_fetch_updated_issue(_stored_ticket(external_record_id="1002"), {}, {"PROJ": _present()})
        return record

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent", [{"id": "1001", "key": "PROJ-1"}, {"id": "1001"}])
    async def test_a_story_that_became_a_sub_task_is_filed_under_its_parent(self, parent: dict) -> None:
        """The stored copy had no parent (a story the sync did not file under it); a sub-task follows a parent with no level."""
        assert (await self._reindexed(parent)).parent_external_record_id == "1001"


class TestReindexPlacesTheStoryAgain:
    """A reindex runs between syncs, so it reads the epic's level afresh."""

    @staticmethod
    def _connector(epic_answer: MagicMock, **known: object) -> JiraConnector:
        c = _connector()
        c.indexing_filters = MagicMock()  # already loaded
        story = _story()
        story["fields"]["updated"] = "2025-01-01T00:00:00.000+0000"
        ds = MagicMock()
        ds.get_issue = AsyncMock(return_value=_resp(200, story))
        ds.bulk_fetch_issues = AsyncMock(return_value=epic_answer)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._parent_levels.update(known)
        return c

    @pytest.mark.asyncio
    async def test_a_level_the_epic_gained_since_the_sync_moves_the_story_off_it(self) -> None:
        c = self._connector(_resp(200, {"issues": [_epic(LEVEL)]}), **{"2001": _ParentLevel(PROJECT_ID, None)})
        record, _ = await c._check_and_fetch_updated_issue(
            _stored_ticket(external_record_id="3001", parent_external_record_id="2001"), {},
            {"PROJ": _present()})
        assert record.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_a_failed_epic_read_keeps_the_story_where_the_sync_filed_it(self) -> None:
        c = self._connector(_resp(500), **{"2001": _ParentLevel(PROJECT_ID, LEVEL["id"])})
        record, _ = await c._check_and_fetch_updated_issue(
            _stored_ticket(external_record_id="3001", parent_external_record_id="2001"), {},
            {"PROJ": _present()})
        assert record.parent_external_record_id == "2001"


class TestBrowseHoldersNamingPeoplePerIssue:
    """Browse Projects granted to the reporter or assignee: the grant goes on each issue."""

    HOLDERS = [{"type": "reporter"}, {"type": "assignee"}]

    @staticmethod
    def _scheme_connector(grants: list[dict]) -> JiraConnector:
        c = _connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(200, {"id": 7}))
        ds.get_permission_scheme_grants = AsyncMock(return_value=_resp(200, {"permissions": grants}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        return c

    @staticmethod
    def _none(holders: list[dict] | None) -> IssueSecurityContext:
        return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, browse_holders=holders)

    @pytest.mark.asyncio
    async def test_the_scheme_read_keeps_the_per_issue_holders_and_grants_the_project_nothing(self) -> None:
        c = self._scheme_connector([
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "reporter"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "g-1"}},
            {"permission": "CREATE_ISSUES", "holder": {"type": "assignee"}},
        ])
        project = await c._fetch_project_permission_scheme("PROJ")
        assert [(p.entity_type, p.external_id) for p in project] == [(EntityType.GROUP, "g-1")]
        assert c._browse_issue_holders["PROJ"] == [{"type": "reporter"}]

    @pytest.mark.asyncio
    async def test_an_unreadable_scheme_leaves_the_holders_unknown(self) -> None:
        c = self._scheme_connector([])
        c._browse_issue_holders["PROJ"] = [{"type": "reporter"}]
        c._get_fresh_datasource.return_value.get_assigned_permission_scheme.return_value = _resp(503, {})
        assert await c._fetch_project_permission_scheme("PROJ") is None
        assert "PROJ" not in c._browse_issue_holders

    @pytest.mark.asyncio
    async def test_the_reporter_and_assignee_of_a_synced_issue_are_granted_it(self) -> None:
        record, perms, _ = await _build(_connector(), _issue(), self._none(self.HOLDERS))
        assert {p.email for p in perms} == {"rep@x.com", "asg@x.com"}
        assert record.access_rule == AccessRule.OPEN and record.rewrite_permissions is True

    @pytest.mark.asyncio
    async def test_a_project_without_the_holders_grants_the_reporter_nothing(self) -> None:
        record, perms, _ = await _build(_connector(), _issue(), self._none([]))
        assert perms == [] and record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_reassigned_issue_is_rewritten_with_the_new_assignee_only(self) -> None:
        issue = _issue()
        issue["fields"]["assignee"] = {"accountId": "acc-9", "displayName": "M"}
        _, perms, _ = await _build(_connector(), issue, self._none([{"type": "assignee"}]))
        assert {p.email for p in perms} == {"mia@x.com"}

    @pytest.mark.asyncio
    async def test_an_account_outside_the_directory_is_not_granted_and_nothing_fails(self) -> None:
        issue = _issue()
        issue["fields"]["reporter"] = {"accountId": "acc-unknown"}
        _, perms, _ = await _build(_connector(), issue, self._none([{"type": "reporter"}]))
        assert perms == []

    @pytest.mark.asyncio
    async def test_a_sub_task_hangs_off_its_project_and_is_granted_its_own_people(self) -> None:
        # Jira grants Browse on each issue to that issue's reporter and assignee, so the sub-task's
        # own people read it without reading its parent.
        sub = _issue(None, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        record, perms, _ = await _build(_connector(), sub, self._none(self.HOLDERS))
        assert record.parent_external_record_id is None
        assert {p.email for p in perms} == {"rep@x.com", "asg@x.com"} and record.access_rule == AccessRule.OPEN

    @pytest.mark.asyncio
    async def test_a_sub_task_follows_its_parent_when_no_holder_names_people(self) -> None:
        sub = _issue(None, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        record, perms, _ = await _build(_connector(), sub, self._none([]))
        assert record.parent_external_record_id == "1001"
        assert perms == [] and record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_sub_task_stays_under_its_parent_while_the_holders_are_unknown(self) -> None:
        sub = _issue(None, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        record, _, _ = await _build(_connector(), sub, self._none(None))
        assert record.parent_external_record_id == "1001"

    @pytest.mark.asyncio
    async def test_a_stored_sub_task_off_its_parent_stays_off_it_while_the_holders_are_unknown(self) -> None:
        """A failed Browse read must not put it back under the parent, whose readers its own holders may lack."""
        sub = _issue(None, key="PROJ-2", issue_id="1002", updated="2025-01-01T00:00:00.000+0000")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        stored = _stored_ticket(external_record_id="1002", parent_external_record_id=None)
        record, _, _ = await _build(_connector(), sub, self._none(None), existing=stored)
        assert record.parent_external_record_id is None

    def test_a_story_is_not_filed_under_an_epic_its_reporter_may_not_read(self) -> None:
        c = _connector()
        c._parent_levels["500"] = _ParentLevel(level_id=None, project_id=PROJECT_ID)
        fields = {"project": {"id": PROJECT_ID}}
        assert c._files_under_parent(fields, "500", self._none([])) is True
        assert c._files_under_parent(fields, "500", self._none(self.HOLDERS)) is False

    @pytest.mark.asyncio
    async def test_user_fields_named_by_the_holders_are_requested_with_the_issues(self) -> None:
        c = self._scheme_connector([])
        c._browse_issue_holders["PROJ"] = [{"type": "userCustomField", "parameter": "10040"}]
        ctx = await c._attach_browse_holders(self._none([]), "PROJ")
        assert ctx.custom_field_ids == ["customfield_10040"]

    @pytest.mark.asyncio
    async def test_a_personal_connector_reads_no_holders(self) -> None:
        c = self._scheme_connector([])
        ctx = await c._attach_browse_holders(IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False), "PROJ", fresh=True)
        assert ctx.browse_holders == [] and c._get_fresh_datasource.await_count == 0

    # The checkpoint keeps a digest of the holders; no issue's `updated` moves when they change.
    _sync = TestMemberChangeRereadsOnlyTheAffectedLevels._sync

    async def _sync_with(self, holders: list[dict] | None, stored: str | None, new_project: bool = False) -> tuple:
        c = _connector()
        if holders is not None:
            c._browse_issue_holders["PROJ"] = holders
        digest = IssueSecurityContext(knowledge=SchemeKnowledge.NONE, browse_holders=holders).browse_fingerprint()
        ctx = _present()
        c, ctx, calls, _ = await self._sync(stored_levels=ctx.level_fingerprints(split=True), ctx=ctx, connector=c)
        return c, calls, digest

    @staticmethod
    def _saved_digests(c: JiraConnector) -> list:
        return [call.args[1]["browse_holders"] for call in c.issues_sync_point.update_sync_point.await_args_list
                if call.args[1].get("browse_holders") is not None]

    @pytest.mark.asyncio
    async def test_holders_added_since_the_last_sync_reread_every_stored_issue(self) -> None:
        c, calls, digest = await self._sync_with(self.HOLDERS, stored=None)
        assert calls[1:] == [
            {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": False, "stored_only": True}]
        assert self._saved_digests(c) == [digest]

    @pytest.mark.asyncio
    async def test_holders_that_did_not_change_reread_nothing(self) -> None:
        c = _connector()
        c._browse_issue_holders["PROJ"] = self.HOLDERS
        digest = self._none(self.HOLDERS).browse_fingerprint()
        # Saved by the last sync with the holders and the level's members as read (unread here: no group read).
        stored = _present()
        stored.browse_holders, stored.level_readers = self.HOLDERS, c._level_readers(stored, "PROJ", {})
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
            "security_levels": json.dumps(stored.level_fingerprints(split=True)), "browse_holders": digest})
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._load_issue_security = AsyncMock(return_value=_present())
        calls = []

        async def fetch(*a, **kw) -> AsyncIterator:
            calls.append(kw)
            return
            yield

        c._fetch_issues_batched = fetch
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert len(calls) == 1 and self._saved_digests(c) == []

    @pytest.mark.asyncio
    async def test_holders_removed_reread_so_the_old_grants_go(self) -> None:
        c = _connector()
        c._browse_issue_holders["PROJ"] = []
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
            "security_levels": json.dumps(_present().level_fingerprints(split=True)), "browse_holders": "abc"})
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._load_issue_security = AsyncMock(return_value=_present())
        calls = []

        async def fetch(*a, **kw) -> AsyncIterator:
            calls.append(kw)
            return
            yield

        c._fetch_issues_batched = fetch
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert len(calls) == 2 and self._saved_digests(c) == [""]

    @pytest.mark.asyncio
    async def test_an_unread_scheme_rereads_nothing_and_saves_nothing_for_a_project_without_holders(self) -> None:
        c, calls, _ = await self._sync_with(None, stored=None)  # no entry: not read this sync
        assert len(calls) == 1 and self._saved_digests(c) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("build_fails", [False, True], ids=["clean_walk", "an_issue_fails_to_build"])
    async def test_holders_unread_after_the_checkpoints_were_cleared_stay_unknown_and_stored_grants_stay(
        self, build_fails: bool,
    ) -> None:
        """A full resync clears the project's checkpoint, so no stored digest is not proof that the holders granted
        nothing: a stored issue a reporter or assignee reads through them keeps its grants until they are read. The
        unknown marker is saved before the walk, so a walk cut short by an issue that fails to build, which still
        advances the cursor, does not leave the next sync reading the holders as none."""
        c = _connector()  # no holders read this sync
        store: dict = {}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: dict(store.get(k) or {}))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, dict(v)))
        c._load_issue_security = AsyncMock(return_value=_present())

        async def fetch(*a, **kw) -> AsyncIterator:
            if build_fails:
                c._issue_build_failures += 1
            yield [], False, 1_700_000_000_000  # the batch moves the cursor

        c._fetch_issues_batched = fetch
        c._reread_leveled_issues = AsyncMock()
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert c._issue_security[PROJECT_ID].browse_holders is None
        assert c._keeps_stored_grants(_stored_ticket(version=0)) is True
        assert store["project_PROJ"]["last_sync_time"] and store["project_PROJ"]["browse_holders"] == "?"

        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert c._issue_security[PROJECT_ID].browse_holders is None, "the next sync, no longer new, still sees them unknown"
        assert c._keeps_stored_grants(_stored_ticket(version=0)) is True

    @pytest.mark.asyncio
    async def test_an_unread_scheme_marks_a_project_that_had_holders_so_the_next_read_rereads(self) -> None:
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
            "security_levels": json.dumps(_present().level_fingerprints(split=True)), "browse_holders": "abc"})
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._load_issue_security = AsyncMock(return_value=_present())

        async def fetch(*a, **kw) -> AsyncIterator:
            return
            yield

        c._fetch_issues_batched = fetch
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert self._saved_digests(c) == ["?"]

    @pytest.mark.asyncio
    async def test_a_reindex_applies_the_holders_to_the_issue(self) -> None:
        c = _reindex_connector(_issue(None, updated="2025-01-01T00:00:00.000+0000"), [_resp(404, {"errorMessages": [
            "Security level for project 10000 does not exist."]})])
        c._read_browse_issue_holders = AsyncMock(return_value=[{"type": "reporter"}])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert {p.email for p in perms} == {"rep@x.com"} and record.access_rule == AccessRule.OPEN

    @pytest.mark.asyncio
    async def test_a_reindex_reads_the_holders_fresh_and_leaves_the_sync_cache_alone(self) -> None:
        """A holder removed in Jira since the last sync must not be granted on a changed issue."""
        c = _reindex_connector(_issue(None, updated="2025-01-01T00:00:00.000+0000"), [_resp(404, {"errorMessages": [
            "Security level for project 10000 does not exist."]})])
        c._browse_issue_holders = {"PROJ": [{"type": "reporter"}]}  # the last sync saw the reporter holder
        c._call_with_retry = AsyncMock(side_effect=[
            _resp(404, {"errorMessages": ["Security level for project 10000 does not exist."]}),
            _resp(200, {"id": 7}), _resp(200, {"permissions": []})])  # the scheme, its holder since removed
        del c._read_browse_issue_holders  # use the real one
        _, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert perms == []
        assert c._browse_issue_holders == {"PROJ": [{"type": "reporter"}]}

    @pytest.mark.asyncio
    async def test_a_reindex_that_closes_an_issue_leaves_the_retry_marker_and_a_clean_sync_restores_the_reporter(
        self,
    ) -> None:
        digest = self._none(self.HOLDERS).browse_fingerprint()
        point = {"last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
                 "security_levels": json.dumps(_present().level_fingerprints(split=True)), "browse_holders": digest}
        c = _reindex_connector(_issue(None, updated="2025-01-01T00:00:00.000+0000"), [_resp(404, {"errorMessages": [
            "Security level for project 10000 does not exist."]})])
        c._read_browse_issue_holders = AsyncMock(return_value=None)
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda _key: dict(point))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda _key, data: point.update(data))
        _, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert perms == [] and point["browse_holders"] == "?"  # closed, as an explicit null level must be

        # The next sync reads the same holders (unchanged digest) and re-reads every stored issue.
        c._browse_issue_holders["PROJ"] = self.HOLDERS
        c._load_issue_security = AsyncMock(return_value=_present())
        calls = []

        async def fetch(*a, **kw) -> AsyncIterator:
            calls.append(kw)
            return
            yield

        c._fetch_issues_batched = fetch
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert len(calls) == 2 and calls[1]["stored_only"] is True and point["browse_holders"] == digest

    @pytest.mark.asyncio
    async def test_a_failed_marker_save_is_logged_and_the_issue_is_still_closed(self) -> None:
        c = _reindex_connector(_issue(None, updated="2025-01-01T00:00:00.000+0000"), [_resp(404, {"errorMessages": [
            "Security level for project 10000 does not exist."]})])
        c._read_browse_issue_holders = AsyncMock(return_value=None)
        c._get_project_sync_checkpoint = AsyncMock(side_effect=RuntimeError("store down"))
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert perms == [] and record.access_rule == AccessRule.STRICT

    def test_a_malformed_grant_or_holder_is_skipped_and_the_rest_are_kept(self) -> None:
        grants = ["x", None, {"permission": "BROWSE_PROJECTS", "holder": "reporter"},
                  {"permission": "BROWSE_PROJECTS", "holder": None},
                  {"permission": "BROWSE_PROJECTS", "holder": {"type": "assignee"}}]
        assert browse_issue_holders(grants) == [{"type": "assignee"}]
        assert browse_issue_holders(None) == []

    @pytest.mark.asyncio
    async def test_a_reindex_that_cannot_read_the_scheme_still_applies_the_change_and_keeps_the_grants(self) -> None:
        c = _reindex_connector(_issue(None, updated="2025-01-01T00:00:00.000+0000"), [_resp(404, {"errorMessages": [
            "Security level for project 10000 does not exist."]}), _resp(403, {})])
        c._read_browse_issue_holders = AsyncMock(return_value=None)
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
        assert record.source_updated_at == c._parse_jira_timestamp("2025-01-01T00:00:00.000+0000")
        # The payload says the level is null: the old level grants go, as on main.
        assert perms == [] and record.rewrite_permissions is True and record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_sync_with_unread_holders_does_not_rewrite_a_top_level_issue_whose_level_was_not_returned(self) -> None:
        issue = _issue()
        del issue["fields"]["security"]
        record, perms, _ = await _build(_connector(), issue, self._none(None))
        assert perms == [] and record.rewrite_permissions is False and record.access_rule == AccessRule.OPEN

    @pytest.mark.asyncio
    async def test_a_level_cleared_while_the_holders_are_unread_removes_the_old_level_grants(self) -> None:
        record, perms, _ = await _build(_connector(), _issue(None), self._none(None))
        assert perms == [] and record.rewrite_permissions is True and record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("stored_rule", "updated", "kept"), [
        (AccessRule.OPEN, False, True), (AccessRule.STRICT, False, True), (AccessRule.RESTRICTED, False, False),
        (AccessRule.OPEN, True, False)], ids=["open_unchanged", "strict_unchanged", "was_leveled", "changed"])
    async def test_an_unchanged_issue_stored_without_a_level_keeps_its_grants_while_the_holders_are_unread(
        self, stored_rule: AccessRule, updated: bool, kept: bool,
    ) -> None:
        """No level now and none stored, and the issue unchanged: the stored grants are the holders' grants for the
        same people, so a sync that cannot read the holders keeps them. A stored RESTRICTED issue had a level whose
        members must lose it, and a changed one may name other people: both are rewritten."""
        c = _connector()
        first, _, _ = await _build(c, _issue(None), self._none(None))
        existing = MagicMock(id="rid", version=0, is_placeholder=False, access_rule=stored_rule,
                             source_updated_at=first.source_updated_at - (1 if updated else 0))
        record, perms, _ = await _build(c, _issue(None), self._none(None), existing, full_sync=True)
        assert perms == [] and record.rewrite_permissions is not kept
        assert record.access_rule == (stored_rule if kept else AccessRule.STRICT)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("version", "changed", "kept"), [(1, False, True), (0, False, True), (0, True, False)],
                             ids=["changed_before", "never_changed_since_its_first_sync", "new_or_promoted"])
    async def test_an_issue_that_keeps_its_grants_is_written_by_the_content_update_not_a_full_sync_rewrite(
        self, version: int, changed: bool, kept: bool,
    ) -> None:
        """A full sync's first write through on_new_records replaces an existing record's grants. An issue still at
        version 0 (unchanged since its first sync) is just as existing; only a new issue, or a promoted placeholder
        (both reported changed), has no stored grants to keep."""
        c = _connector()
        first, _, _ = await _build(c, _issue(None), self._none(None))  # search returns "security": null
        existing = None if changed else MagicMock(
            id="rid", version=version, source_updated_at=first.source_updated_at, is_placeholder=False,
            access_rule=AccessRule.OPEN)
        record, perms, built_changed = await _build(c, _issue(None), self._none(None), existing, full_sync=True)
        assert built_changed is changed
        c.data_entities_processor.on_record_content_update = AsyncMock()
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        assert c.data_entities_processor.on_record_content_update.await_count == (1 if kept else 0)
        assert c.data_entities_processor.on_new_records.await_count == (0 if kept else 1)

    @pytest.mark.asyncio
    async def test_a_stored_unread_marker_followed_by_a_good_read_rereads_every_stored_issue(self) -> None:
        c = _connector()
        c._browse_issue_holders["PROJ"] = []
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
            "security_levels": json.dumps(_present().level_fingerprints(split=True)), "browse_holders": "?"})
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._load_issue_security = AsyncMock(return_value=_present())
        calls = []

        async def fetch(*a, **kw) -> AsyncIterator:
            calls.append(kw)
            return
            yield

        c._fetch_issues_batched = fetch
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert len(calls) == 2 and calls[1]["stored_only"] is True and self._saved_digests(c) == [""]

    def test_a_story_stays_where_it_was_filed_while_the_holders_are_unread(self) -> None:
        c = _connector()
        c._parent_levels["500"] = _ParentLevel(level_id=None, project_id=PROJECT_ID)
        assert c._files_under_parent({"project": {"id": PROJECT_ID}}, "500", self._none(None)) is None

    async def _sync_stored(self, stored: str | None, holders: list | None, fail_in_walk: bool = False) -> JiraConnector:
        c = _connector()
        if holders is not None:
            c._browse_issue_holders["PROJ"] = holders
        point = {"last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
                 "security_levels": json.dumps(_present().level_fingerprints(split=True))}
        if stored is not None:
            point["browse_holders"] = stored
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value=point)
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._load_issue_security = AsyncMock(return_value=_present())

        async def fetch(*a, **kw) -> AsyncIterator:
            if fail_in_walk:
                raise RuntimeError("crash mid-walk")
            return
            yield

        c._fetch_issues_batched = fetch
        with contextlib.suppress(RuntimeError):
            await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        return c

    @pytest.mark.asyncio
    async def test_the_unread_marker_is_saved_before_the_walk_so_a_crash_cannot_lose_it(self) -> None:
        c = await self._sync_stored("abc", None, fail_in_walk=True)
        assert self._saved_digests(c) == ["?"]

    @pytest.mark.asyncio
    async def test_unread_holders_of_a_project_that_never_had_any_are_taken_as_none(self) -> None:
        c = await self._sync_stored(None, None)
        assert c._issue_security[PROJECT_ID].browse_holders == [] and self._saved_digests(c) == []




class TestDeferredReadOnAProjectNeverTightened:
    """Issues stored open have no grants of their own to keep: while the levels cannot be read their leveled
    issues are closed once, the pass is marked done, and a read that keeps failing does not repeat it."""

    _sync = TestMemberChangeRereadsOnlyTheAffectedLevels._sync
    DOWN = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True, read_status=503)

    @staticmethod
    def _saved(c: JiraConnector) -> list[dict]:
        return [json.loads(call.args[1]["security_levels"])
                for call in c.issues_sync_point.update_sync_point.await_args_list if "security_levels" in call.args[1]]

    @pytest.mark.asyncio
    async def test_two_failed_reads_in_a_row_close_once_and_a_clean_read_grants_the_holders(self) -> None:
        c, _, calls, _ = await self._sync(stored_levels=None, ctx=self.DOWN)
        assert [x["leveled_only"] for x in calls] == [False, True], "the leveled pass runs once"
        assert self._saved(c)[-1] == {"*": "held"}

        c, _, calls, _ = await self._sync(stored_levels={"*": "held"}, ctx=self.DOWN)
        assert len(calls) == 1, "the second failed read does not walk the project again"
        assert self._saved(c) == [], "and does not write 'retry' over the marker"

        c, _, calls, _ = await self._sync(stored_levels={"*": "held"})
        assert calls[1]["leveled_only"] is True and calls[1]["stored_only"] is True, "a clean read grants the holders"

    @pytest.mark.asyncio
    async def test_the_marker_saved_before_the_walk_keeps_held(self) -> None:
        saved_before: list = []

        async def record_then_fail(c: JiraConnector, calls: list) -> None:
            saved_before.extend(TestDeferredReadOnAProjectNeverTightened._saved(c))
            raise JiraReadError(503, "later page")

        with pytest.raises(JiraReadError):
            await self._sync(stored_levels={"*": "held"}, ctx=self.DOWN, fetch_fn=record_then_fail)
        assert saved_before == []

    @pytest.mark.asyncio
    async def test_the_notice_says_hidden_from_the_first_failed_sync_and_after(self) -> None:
        for stored in (None, {"*": "held"}):
            c, _, _, _ = await self._sync(stored_levels=stored, ctx=self.DOWN)
            c.notify = AsyncMock()
            await c._warn_unreadable_security()
            assert "hidden from everyone" in c.notify.await_args.kwargs["message"]
            assert "keep the access they had" not in c.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_a_project_already_tightened_keeps_its_access_and_says_so(self) -> None:
        c, _, _, _ = await self._sync(stored_levels=_present().level_fingerprints(split=True), ctx=self.DOWN)
        c.notify = AsyncMock()
        await c._warn_unreadable_security()
        assert "keep the access they had" in c.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_an_issue_that_failed_to_build_in_the_closing_pass_keeps_it_scheduled(self) -> None:
        async def one_fails(c: JiraConnector, calls: list) -> None:
            if calls[-1]["stored_only"]:
                c._issue_build_failures += 1

        c, _, _, _ = await self._sync(stored_levels=None, ctx=self.DOWN, fetch_fn=one_fails)
        assert self._saved(c)[-1] == {"*": "retry"}, "not marked done"

        _, _, calls, _ = await self._sync(stored_levels={"*": "retry"}, ctx=self.DOWN)
        assert calls[1]["leveled_only"] is True, "the next sync closes it again"


class TestAPartialUserReadKeepsTheStoredLevelGroup:
    """A holder missing from an unfinished user read is unread, not out of the level: the stored group keeps its grants."""

    @staticmethod
    def _unread_holder() -> JiraConnector:
        c = _connector()
        c._issue_security[PROJECT_ID] = _present(members={"10102": [
            {"type": "group", "value": "group-1"}, {"type": "user", "parameter": "acc-404", "user": {"accountId": "acc-404"}}]})
        c._user_read_unfinished = True
        c.data_entities_processor.note_kept_record_group = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_a_stored_group_is_kept_and_seen(self) -> None:
        c = self._unread_holder()
        c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="lvl"))
        await c._write_level_groups(PROJECT_ID, "PROJ", c._issue_security[PROJECT_ID], ["10102"], {})
        c.data_entities_processor.on_new_record_groups.assert_not_awaited()
        c.data_entities_processor.note_kept_record_group.assert_awaited_once_with("conn-sec", "level:p-1:10102")

    @pytest.mark.asyncio
    async def test_a_new_group_is_written_with_what_was_read(self) -> None:
        c = self._unread_holder()
        await c._write_level_groups(PROJECT_ID, "PROJ", c._issue_security[PROJECT_ID], ["10102"], {})
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert [g.external_id for g in grants] == ["group-1"]

    @pytest.mark.asyncio
    async def test_a_refused_role_list_writes_the_group_with_the_role_list_kept(self) -> None:
        """A 403 from applicationrole ("Returned if the user is not an administrator") leaves the role's groups unknown,
        not empty: the group is written with the groups the roles had when last read."""
        c = _connector()
        c._issue_security[PROJECT_ID] = _present(members={"10102": [
            {"type": "group", "value": "group-1"}, {"type": "applicationRole", "parameter": "jira-software"}]})
        c._app_roles_forbidden, c._app_roles_mapping = True, {}
        c._level_roles = {"jira-software": [{"groupId": "g-1"}]}
        c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="lvl"))
        await c._write_level_groups(PROJECT_ID, "PROJ", c._issue_security[PROJECT_ID], ["10102"], {})
        (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert sorted(g.external_id for g in grants) == ["g-1", "group-1"]

    @pytest.mark.asyncio
    async def test_a_finished_read_writes_the_group_without_the_holder(self) -> None:
        c = self._unread_holder()
        c._user_read_unfinished = False
        c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="lvl"))
        await c._write_level_groups(PROJECT_ID, "PROJ", c._issue_security[PROJECT_ID], ["10102"], {})
        c.data_entities_processor.on_new_record_groups.assert_awaited_once()
        (group, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert group.external_group_id == "level:p-1:10102"
        assert {(g.entity_type, g.external_id) for g in grants} == {(EntityType.GROUP, "group-1")}


@pytest.mark.asyncio
async def test_an_issue_keeping_its_grants_is_not_replaced_by_a_full_sync() -> None:
    """A level read that will be retried keeps an unchanged issue's grants: a full sync's first write, which replaces
    grants, must not reach it, so it goes through the content update, which never does."""
    c = _connector()
    ctx = _present()
    ctx.members_readable, ctx.defer_checkpoint = False, True  # the members read failed for now
    issue = _issue({"id": "10102"})
    c._issue_security[PROJECT_ID] = ctx
    c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MagicMock(
        id="rid", version=2, is_placeholder=False, source_updated_at=c._parse_jira_timestamp(issue["fields"]["updated"])))
    (record, perms, changed), = (await c._build_issue_records([issue], PROJECT_ID, _users(), is_new_project=True))[0]
    assert (record.rewrite_permissions, changed) == (False, False)
    await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
    c.data_entities_processor.on_new_records.assert_not_awaited()
    c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, content_changed=False)


class _KeptSyncPoints:
    """Sync points kept across connectors, as the store keeps them across syncs."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict) -> None:
        self.points[key] = dict(data)


HOUR_MS = 60 * 60 * 1000


async def _roles_at(points: _KeptSyncPoints, now_ms: int, *, roles: dict | None) -> JiraConnector:
    """A connector whose sync read the roles (``roles``) or was refused them (None) at ``now_ms``."""
    c = _connector()
    c.issues_sync_point = points
    c.notify = AsyncMock()
    c._app_roles_forbidden, c._app_roles_mapping = roles is None, roles or {}
    with patch("app.connectors.sources.atlassian.jira_cloud.connector.get_epoch_timestamp_in_ms", return_value=now_ms):
        await c._read_level_roles()
    return c


class TestRolesKeptForLevels:
    """applicationrole needs "Administer Jira" and answers 403 "Returned if the user is not an administrator"
    (https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-application-roles/): the last role list read
    is applied for a bounded time, so a level's other holders still follow Jira."""
    ROLES = {"jira-software": [{"groupId": "g-1"}]}
    LEVEL = [{"type": "applicationRole", "parameter": "jira-software"},
             {"type": "group", "value": "g-2"}, {"type": "group", "value": "g-3"}]

    async def _group_written(self, c: JiraConnector, stored: dict, members: list) -> tuple[list[str], list]:
        _, _, calls, _ = await TestMemberChangeRereadsOnlyTheAffectedLevels()._sync(
            stored_levels=stored, ctx=_present(members={"10102": members}), connector=c)
        (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        return sorted(g.external_id for g in grants), [call["level_ids"] for call in calls]

    @pytest.mark.asyncio
    async def test_a_holder_removed_while_the_roles_are_refused_is_removed_and_the_roles_groups_stay(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        stored = _present(members={"10102": self.LEVEL}).level_fingerprints(self.ROLES, split=True)
        c = await _roles_at(points, HOUR_MS, roles=None)
        written, passes = await self._group_written(c, stored, self.LEVEL[:2])
        assert written == ["g-1", "g-2"] and passes == [None, ["10102"]]
        c.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_past_the_bound_the_roles_groups_are_dropped_and_the_owner_told_once(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        stored = _present(members={"10102": self.LEVEL}).level_fingerprints(self.ROLES, split=True)
        c = await _roles_at(points, 25 * HOUR_MS, roles=None)
        written, passes = await self._group_written(c, stored, self.LEVEL)
        assert written == ["g-2", "g-3"] and passes == [None, ["10102"]]  # its sub-tasks' copies are read again too
        c.notify.assert_awaited_once()
        assert "grant the connector account Jira admin" in c.notify.await_args.kwargs["message"]
        c = await _roles_at(points, 26 * HOUR_MS, roles=None)
        assert c._level_roles == {}
        c.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_role_group_change_during_the_refusal_does_not_outlast_the_bound(self) -> None:
        """Jira moved the role from g-1 to g-new while the list was refused: g-1 is granted only until the bound."""
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        within = await _roles_at(points, 23 * HOUR_MS, roles=None)
        assert within._level_roles == self.ROLES
        past = await _roles_at(points, 25 * HOUR_MS, roles=None)
        assert past._level_roles == {}
        read = await _roles_at(points, 26 * HOUR_MS, roles={"jira-software": [{"groupId": "g-new"}]})
        assert read._level_roles == {"jira-software": [{"groupId": "g-new"}]}
        assert (await _roles_at(points, 27 * HOUR_MS, roles=None))._level_roles == read._level_roles

    @pytest.mark.asyncio
    async def test_past_the_bound_an_unread_role_list_drops_the_roles_groups_as_a_refused_one_does(self) -> None:
        """Unread (not refused): the project naming the role is marked partial for its Browse grants, but the level
        group follows the role list the levels apply, so it is rewritten with what resolves, not kept."""
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        c = await _roles_at(points, 25 * HOUR_MS, roles=None)
        c._app_roles_forbidden, c._app_roles_unread = False, True
        c._projects_naming_unread_roles = {"PROJ"}
        c.data_entities_processor.note_kept_record_group = AsyncMock()
        c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="lvl"))
        stored = _present(members={"10102": self.LEVEL}).level_fingerprints(self.ROLES, split=True)
        written, passes = await self._group_written(c, stored, self.LEVEL[:2])
        assert written == ["g-2"] and passes == [None, ["10102"]]

    @pytest.mark.asyncio
    async def test_a_sub_task_keeps_its_roles_groups_while_the_roles_are_refused(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        c = await _roles_at(points, HOUR_MS, roles=None)
        sub = _issue({"id": "10102"}, key="PROJ-2", issue_id="1002")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "hierarchyLevel": -1, "subtask": True}
        sub["fields"]["parent"] = {"id": "1001", "key": "PROJ-1"}
        record, perms, _ = await _build(c, sub, _present(members={"10102": self.LEVEL}))
        assert record.access_rule == AccessRule.RESTRICTED
        assert sorted(p.external_id for p in perms if p.entity_type == EntityType.GROUP) == ["g-1", "g-2", "g-3"]


class TestSkippedHoldersAreReportedOncePerLevel:
    @pytest.mark.asyncio
    async def test_a_holder_with_no_e_mail_is_one_warning_not_one_per_issue(self, caplog: pytest.LogCaptureFixture) -> None:
        c = _connector()
        c._issue_security[PROJECT_ID] = _present(members={"10102": [
            {"type": "user", "parameter": "acc-404", "user": {"accountId": "acc-404"}}]})
        issues = [_issue({"id": "10102"}, key=f"PROJ-{n}", issue_id=str(1000 + n)) for n in range(5)]
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            # The level's group holds the user, so the warning comes from the one write of the group.
            await c._write_level_groups(PROJECT_ID, "PROJ", c._issue_security[PROJECT_ID], ["10102"], {})
            await c._build_issue_records(issues, PROJECT_ID, _users())
        assert len([r for r in caplog.records if "were not granted" in r.getMessage()]) == 1

    @pytest.mark.asyncio
    async def test_a_field_holder_with_no_e_mail_is_one_warning_not_one_per_issue(self, caplog: pytest.LogCaptureFixture) -> None:
        c = _connector()
        c._issue_security[PROJECT_ID] = _present(members={"10102": [{"type": "userCustomField", "parameter": "customfield_7"}]})
        issues = [_issue({"id": "10102"}, key=f"PROJ-{n}", issue_id=str(1000 + n)) for n in range(5)]
        for issue in issues:
            issue["fields"]["customfield_7"] = {"accountId": "acc-404"}
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            await c._build_issue_records(issues, PROJECT_ID, _users())
        assert len([r for r in caplog.records if "were not granted" in r.getMessage()]) == 1


class TestIssueGrantsNameListedAccountsOnly:
    """An issue's own grants name only accounts in the synced directory (licensed and active); an account listed
    again, or for the first time, gets them back when the issues naming it are read again."""

    @staticmethod
    def _ctx(holders: list | None = None, levels: dict | None = None) -> IssueSecurityContext:
        return IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, scheme_id="1", members_readable=True,
                                    members_by_level=levels or {},
                                    browse_holders=[{"type": "reporter"}] if holders is None else holders)

    @pytest.mark.asyncio
    async def test_a_reporter_outside_the_directory_is_not_granted_even_with_an_email_in_the_payload(self) -> None:
        issue = _issue()
        issue["fields"]["reporter"] = {"accountId": "acc-x", "emailAddress": "former@x.com", "displayName": "F"}
        _, perms, _ = await _build(_connector(), issue, IssueSecurityContext(
            knowledge=SchemeKnowledge.NONE, browse_holders=[{"type": "reporter"}, {"type": "assignee"}]))
        assert {p.email for p in perms} == {"asg@x.com"}

    @pytest.mark.asyncio
    async def test_a_level_user_holder_outside_the_directory_is_not_granted(self) -> None:
        holders = {"10102": [{"type": "user", "parameter": "acc-x",
                              "user": {"accountId": "acc-x", "emailAddress": "former@x.com"}}, {"type": "assignee"}]}
        _, perms, _ = await _build(_connector(), _issue({"id": "10102"}), _present(holders))
        assert {p.email for p in perms} == {"asg@x.com"}

    @pytest.mark.asyncio
    async def test_an_account_outside_the_directory_is_not_granted_through_an_email_a_synced_account_shares(self) -> None:
        """acc-x has no access in Jira but carries mia's email (acc-9): mia must not get acc-x's reporter or level grant."""
        issue = _issue({"id": "10102"})
        issue["fields"]["reporter"] = {"accountId": "acc-x", "emailAddress": "mia@x.com", "displayName": "X"}
        holders = {"10102": [{"type": "user", "parameter": "acc-x", "user": {"accountId": "acc-x", "emailAddress": "mia@x.com"}},
                             {"type": "reporter"}, {"type": "assignee"}]}
        _, perms, _ = await _build(_connector(), issue, _present(holders))
        assert {p.email for p in perms} == {"asg@x.com"}

    @pytest.mark.asyncio
    async def test_a_synced_account_whose_directory_entry_has_no_email_keeps_its_level_grant_by_account(self) -> None:
        """acc-9 is synced but stored without an email (as a stored user missing one is read back); the level names it
        with its email in the payload. Its level's group grants it by account, or the write would drop it."""
        c = _connector()
        security = _present({"10102": [
            {"type": "user", "parameter": "acc-9", "user": {"accountId": "acc-9", "emailAddress": "mia@x.com"}}]})
        users = [*_users()[:2], AppUser(app_name="JIRA", connector_id="conn-sec", source_user_id="acc-9", org_id="org-1",
                                         email="", full_name="Mia", is_active=True)]
        # The directory the sync writes the levels' groups from.
        await c._write_level_groups(PROJECT_ID, "PROJ", security, ["10102"],
                                    c._grant_directory({u.source_user_id: u for u in users}))
        (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert {(p.email, p.external_id) for p in grants} == {(None, "acc-9")}  # by account, reaching that user

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("listed", "licensed", "granted"), [
        (True, True, True), (False, True, False), (True, False, False), (True, None, False)],
        ids=["listed_and_licensed", "not_listed", "not_licensed", "product_access_unknown"])
    async def test_a_linked_account_stored_without_an_email_is_in_the_directory_a_sync_resolves_against(
        self, listed: bool, licensed: bool | None, granted: bool,
    ) -> None:
        """The user list read in full lists acc-9 active, and it is linked, but its email is hidden, so the directory a sync
        builds (`_fetch_users` keeps a cached account only with an email) does not hold it. The level names it: its
        record group grants it by account when the list shows it active and it has product access; not otherwise. The
        issue holds no grant of its own for a holder that names the same person on every issue."""
        c = _connector()
        mia = AppUser(app_name="JIRA", connector_id="conn-sec", source_user_id="acc-9", org_id="org-1", email="",
                      full_name="Mia", is_active=True)
        c._stored_app_users = [*_users()[:2], mia]
        c._listed_account_ids = {"acc-1", "acc-2"} | ({"acc-9"} if listed else set())
        c._seen_account_ids = {"acc-1", "acc-2", "acc-9"}
        c._licensed_account_ids = None if licensed is None else ({"acc-1", "acc-2"} | ({"acc-9"} if licensed else set()))
        c._issue_security[PROJECT_ID] = _present({"10102": [
            {"type": "user", "parameter": "acc-9", "user": {"accountId": "acc-9", "emailAddress": "mia@x.com"}}]})
        records, _ = await c._build_issue_records([_issue({"id": "10102"})], PROJECT_ID, _users()[:2])
        assert not any(p.external_id == "acc-9" for p in records[0][1])
        await c._write_level_groups(PROJECT_ID, "PROJ", c._issue_security[PROJECT_ID], ["10102"],
                                    c._grant_directory({u.source_user_id: u for u in _users()[:2]}))
        (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert {(p.email, p.external_id) for p in grants} == ({(None, "acc-9")} if granted else set())

    @pytest.mark.parametrize("not_in_full", ["_user_read_unfinished", "_user_bulk_forbidden", "_user_bulk_refused", "empty"])
    def test_a_directory_not_read_in_full_counts_the_linked_accounts_and_no_one_else(self, not_in_full: str) -> None:
        c = _connector()
        c._stored_app_users = [AppUser(app_name="JIRA", connector_id="c", source_user_id="acc-l", org_id="org-1",
                                       email="linked@x.com", full_name="L", is_active=True)]
        c._listed_account_ids = {"acc-l"}
        perms = [Permission(entity_type=EntityType.USER, email="Linked@x.com", type=PermissionType.READ),
                 Permission(entity_type=EntityType.USER, email="withdrawn@x.com", type=PermissionType.READ),
                 Permission(entity_type=EntityType.GROUP, external_id="g", type=PermissionType.READ)]
        assert c._directory_grants(perms, {}) == perms[2:]
        if not_in_full == "empty":
            c._listed_account_ids = set()  # no one listed, though accounts were linked: not a read of the whole list
        else:
            setattr(c, not_in_full, True)
        # A linked account the read missed keeps its grant; one only the payload names (withdrawn) does not.
        assert c._directory_grants(perms, {}) == [perms[0], perms[2]]

    @staticmethod
    def _pass(c: JiraConnector, stored: str | None = None) -> tuple[list, list]:
        saved: list = []
        naming: list = []
        c._update_project_sync_checkpoint = AsyncMock(
            side_effect=lambda _k, **kw: saved.append(kw["people_reread"]) if "people_reread" in kw else None)

        async def fetch(*_a: object, naming: str | None = None, **_kw: object) -> AsyncIterator:
            naming_seen.append(naming)
            return
            yield

        naming_seen = naming
        c._fetch_issues_batched = fetch
        return saved, naming

    @pytest.mark.asyncio
    async def test_newly_listed_accounts_have_their_issues_read_again_and_are_then_cleared(self) -> None:
        """Reporter, assignee and level: the JQL fields the docs say take an account id or a level id."""
        c = _connector()
        saved, naming = self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 1  # the sync that queued them
        ctx = self._ctx(levels={"10102": [{"type": "user", "parameter": "acc-1"}], "10103": [{"type": "group", "value": "g"}],
                                "10104": [{"type": "groupCustomField", "parameter": "customfield_10050"}]})
        ctx.custom_field_ids = ["customfield_10050"]
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, ctx, {"people_reread": '["acc-0"]'}, covered=False)
        assert saved == ['["acc-0", "acc-1"]', "[]"]  # saved before the pass, cleared after it
        assert naming == ['reporter in ("acc-0", "acc-1") OR assignee in ("acc-0", "acc-1") OR level in (10102)']

    @pytest.mark.asyncio
    @pytest.mark.parametrize("queued", [[], ["acc-1"]], ids=["queue_empty", "accounts_queued"])
    @pytest.mark.parametrize("failed", [False, True], ids=["read", "an_issue_failed"])
    async def test_a_project_that_missed_an_emptied_queue_reads_every_stored_issue_again(self, failed: bool, queued: list) -> None:
        """It covered generation 1; the queue was emptied at 2 while it was missing from the project list, so the
        accounts it missed are no longer named: every stored issue is read again. It is due its generation only
        after a pass in which no issue failed (and the sweep, which saves it)."""
        c = _connector()
        self._pass(c)
        c._newly_listed_account_ids = queued  # queued since base: they do not name what it missed before
        c._people_generation, c._people_base = 3, 2

        async def reread(*_a: object, **_kw: object) -> None:
            c._issue_build_failures += int(failed)

        c._reread_leveled_issues = AsyncMock(side_effect=reread)
        c._reread_issues_naming = AsyncMock()
        assert await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, self._ctx(), {"people_read": 1}, covered=False)
        assert c._reread_leveled_issues.await_args.kwargs == {"every_issue": True}
        c._reread_issues_naming.assert_not_awaited()
        assert c._people_read_due == ([] if failed else ["PROJ"])

    @pytest.mark.asyncio
    async def test_a_project_that_covered_the_emptied_queue_reads_only_the_issues_naming_the_queued_accounts(self) -> None:
        """It covered generation 2, where the queue was emptied: the queue names every account it missed since."""
        c = _connector()
        self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation, c._people_base = 3, 2
        c._reread_leveled_issues = AsyncMock()
        c._reread_issues_naming = AsyncMock()
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, self._ctx(), {"people_read": 2}, covered=False)
        c._reread_leveled_issues.assert_not_awaited()
        assert c._reread_issues_naming.await_args.args[4] == ["acc-1"]

    @pytest.mark.asyncio
    async def test_a_project_that_covered_the_current_generation_reads_nothing_for_the_queue(self) -> None:
        """Another project failed, so the queue stays; this one took it last sync and its sweep ran."""
        c = _connector()
        self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 3
        c._reread_issues_naming = AsyncMock()
        assert not await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, self._ctx(), {"people_read": 3}, covered=False)
        c._reread_issues_naming.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_queue_and_its_base_stay_while_a_listed_project_still_owes_its_pass(self) -> None:
        """An issue failed in PROJ's targeted pass, so PROJ keeps the accounts and its generation. Emptying the queue
        (moving base past PROJ) would turn its next pass into a read of every stored issue."""
        c = _connector()
        self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation, c._people_base = 3, 2

        async def failing(*_a: object, **_kw: object) -> None:
            c._issue_build_failures += 1

        c._reread_issues_naming = AsyncMock(side_effect=failing)
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, self._ctx(), {"people_read": 2}, covered=False)
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.update_sync_point = AsyncMock()
        await c._people_to_reread_handed_on({"failed_project_keys": []}, swept=True)
        c.issues_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("swept", [True, False])
    async def test_a_project_saves_its_generation_only_once_the_stub_sweep_ran(self, swept: bool) -> None:
        """Its stubs may name the account too; a skipped sweep leaves it owed, so the next sync passes and sweeps again."""
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._update_project_sync_checkpoint = AsyncMock()
        c._people_generation, c._people_read_due = 3, ["PROJ"]
        await c._people_to_reread_handed_on({"failed_project_keys": []}, swept=swept)
        saved = [kw.get("people_read") for _a, kw in c._update_project_sync_checkpoint.await_args_list]
        assert saved == ([3] if swept else [])

    @pytest.mark.asyncio
    async def test_a_project_that_failed_after_its_pass_does_not_save_its_generation(self) -> None:
        """PROJ's people pass went through, then a later step of PROJ failed: it was not reported as re-read, so the sweep
        may have skipped its stubs. It keeps its older generation; OPS, which finished, saves its own."""
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._update_project_sync_checkpoint = AsyncMock()
        c._people_generation, c._people_read_due = 3, ["PROJ", "OPS"]
        await c._people_to_reread_handed_on({"failed_project_keys": ["PROJ"]}, swept=True)
        assert [(a[0], kw.get("people_read")) for a, kw in c._update_project_sync_checkpoint.await_args_list] == [("OPS", 3)]

    @pytest.mark.asyncio
    async def test_an_account_admitted_again_while_still_queued_is_a_new_generation(self) -> None:
        c = _connector()
        store: dict = {"people_reread": {"accounts": '["acc-1"]', "generation": 4, "base": 3}}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        await c._note_people_to_reread({"acc-1"})
        assert (c._people_generation, c._people_base) == (5, 3) and store["people_reread"]["generation"] == 5

    @pytest.mark.asyncio
    @pytest.mark.parametrize("why", ["user_field", "unquotable_id", "query_refused"])
    async def test_what_the_naming_search_cannot_cover_has_every_stored_issue_read_again(self, why: str) -> None:
        """A user-picker field searched by account id is not documented, an id JQL cannot carry is not searched, and
        a refused query (400) names nothing: none is skipped, every stored issue of the project is read instead."""
        c = _connector()
        saved, naming = self._pass(c)
        c._newly_listed_account_ids = ['bad"id'] if why == "unquotable_id" else ["acc-1"]
        c._people_generation = 1  # the sync that queued them
        holders = [{"type": "reporter"}, {"type": "userCustomField", "parameter": "customfield_10040"}] \
            if why == "user_field" else None
        ctx = self._ctx(holders=holders)
        if why == "query_refused":
            async def refused(*_a: object, **_kw: object) -> AsyncIterator:
                raise JiraReadError(400, "JQL refused")
                yield
            c._fetch_issues_batched = refused
        c._reread_leveled_issues = AsyncMock()
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, ctx, None, covered=False)
        c._reread_leveled_issues.assert_awaited_once_with("PROJ", PROJECT_ID, [], {}, None, every_issue=True)
        assert saved[-1] == "[]"
        assert not any(n and "cf[" in n for n in naming)

    @pytest.mark.asyncio
    async def test_a_search_answer_with_warnings_counts_as_unfinished(self) -> None:
        """warnings: "e.g. when a JQL clause exceeded its argument limit or when the result set was truncated due
        to an ingestion limit": the pass may be partial, so it is repeated."""
        c = _connector()
        c.data_source = MagicMock()
        c._search_issues_with_retry = AsyncMock(return_value=MagicMock(
            status=200, json=MagicMock(return_value={"issues": [], "warnings": ["The JQL argument limit was exceeded"]})))
        async for _batch, _more, _ts in c._fetch_issues_batched(
            "PROJ", PROJECT_ID, [], None, None, is_new_project=True, stored_only=True, naming='reporter in ("acc-1")',
        ):
            pass
        assert c._issue_build_failures == 1

    @pytest.mark.asyncio
    async def test_a_pass_that_skipped_an_issue_keeps_the_accounts_for_the_next_sync(self) -> None:
        c = _connector()
        saved, _ = self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 1  # the sync that queued them

        async def fail(*_a: object, **_kw: object) -> None:
            c._issue_build_failures += 1
        c._reread_issues_naming = fail
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, self._ctx(), None, covered=False)
        assert saved == ['["acc-1"]']

    @pytest.mark.asyncio
    async def test_unread_holders_keep_the_accounts_without_a_pass(self) -> None:
        c = _connector()
        saved, naming = self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 1  # the sync that queued them
        ctx = self._ctx()
        ctx.browse_holders = None
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, ctx, None, covered=False)
        assert saved == ['["acc-1"]'] and naming == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("covered", "holders"), [(True, None), (False, [])], ids=["read_in_full", "names_nobody"])
    async def test_a_project_read_in_full_or_naming_nobody_needs_no_pass(self, covered: bool, holders: list | None) -> None:
        c = _connector()
        saved, naming = self._pass(c)
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 1  # the sync that queued them
        await c._reread_for_newly_listed("PROJ", PROJECT_ID, [], {}, self._ctx(holders=holders), {"people_reread": '["acc-0"]'},
                                         covered=covered)
        assert naming == [] and saved == ["[]"]

    @pytest.mark.asyncio
    async def test_newly_listed_accounts_are_saved_before_they_are_linked_and_kept_until_handed_on(self) -> None:
        c = _connector()
        store: dict = {"people_reread": {"accounts": '["acc-0"]'}}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        assert await c._note_people_to_reread({"acc-1"}) == ["acc-0", "acc-1"]
        assert json.loads(store["people_reread"]["accounts"]) == ["acc-0", "acc-1"]
        assert await c._note_people_to_reread(set()) == ["acc-0", "acc-1"]  # an earlier sync failed before handing them on

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("failed", "skipped", "unresolved", "swept", "cleared"), [
        ([], [], set(), True, True), (["PROJ"], [], set(), True, False), ([], ["OPS"], set(), True, False),
        ([], [], {"OPS"}, True, False), ([], [], set(), False, False)],
        ids=["all_handed_on", "a_project_failed", "a_project_not_built", "a_selected_key_not_looked_up", "stub_sweep_skipped"])
    async def test_the_saved_accounts_are_cleared_only_once_every_project_took_them(
        self, failed: list, skipped: list, unresolved: set, swept: bool, cleared: bool,
    ) -> None:
        """A selected project the search missed and whose former key could not be looked up (a 503) was not read:
        it still owes its re-read. So do the stubs of a project read again while the sweep was skipped."""
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.update_sync_point = AsyncMock()
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 1  # the sync that queued them
        c._skipped_project_keys = skipped
        c._unresolved_selected_keys = unresolved
        await c._people_to_reread_handed_on({"failed_project_keys": failed}, swept=swept)
        assert c.issues_sync_point.update_sync_point.await_count == (1 if cleared else 0)

    @pytest.mark.asyncio
    async def test_a_project_that_read_the_accounts_issues_again_has_its_stubs_re_secured(self) -> None:
        """Its stubs (ancestors backfilled earlier) may name the account too; the sweep only revisits a
        backfilled stub in a project reported as read again."""
        c = _connector()
        c._newly_listed_account_ids = ["acc-1"]
        c._people_generation = 1  # the sync that queued them
        c._reread_issues_naming = AsyncMock()
        _, _, _, result = await TestMemberChangeRereadsOnlyTheAffectedLevels._sync(
            self, stored_levels=_present().level_fingerprints(), connector=c)
        c._reread_issues_naming.assert_awaited_once()
        assert result["leveled_reread"] is True

    @pytest.mark.asyncio
    async def test_on_a_list_not_read_in_full_a_linked_level_holder_named_by_account_id_alone_keeps_the_grant(self) -> None:
        """mia (acc-9) is linked but missing from a list cut short, and the level names her by account id with no
        email. Read in full, she is gone from Jira and gets nothing; nor does she when the short list showed her
        deactivated."""
        for complete, shown_deactivated in ((True, False), (False, False), (False, True)):
            c = _connector()
            c._stored_app_users = _users()
            c._listed_account_ids = {"acc-1", "acc-2"}
            c._seen_account_ids = {"acc-1", "acc-2"} | ({"acc-9"} if shown_deactivated else set())
            c._user_read_unfinished = not complete
            security = _present(members={"10102": [
                {"type": "user", "parameter": "acc-9", "user": {"accountId": "acc-9"}}]})
            c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)
            # The level's group, written from the directory the sync passes (the synced users, and the linked accounts a
            # list cut short did not show).
            await c._write_level_groups(PROJECT_ID, "PROJ", security, ["10102"],
                                        c._grant_directory({u.source_user_id: u for u in _users()[:2]}))
            (_, grants), = c.data_entities_processor.on_new_record_groups.await_args.args[0]
            assert {p.email for p in grants} == (set() if complete or shown_deactivated else {"mia@x.com"})

    @pytest.mark.asyncio
    async def test_a_sync_on_a_list_cut_short_writes_a_linked_level_holder_into_the_level_group(self) -> None:
        """The sync writes the levels' groups before the walk. mia (acc-9) is linked but missing from a list cut short, and
        the level names her by account id: the group grants her, as an issue's own grants would, not only the users read."""
        c = _connector()
        c._stored_app_users = _users()
        c._listed_account_ids = c._seen_account_ids = {"acc-1", "acc-2"}
        c._user_read_unfinished = True
        security = _present(members={"10102": [{"type": "user", "parameter": "acc-9", "user": {"accountId": "acc-9"}}]})
        point: dict = {"issue_placement": ISSUE_PLACEMENT, "security_levels": json.dumps(security.level_fingerprints(split=True)),
                       "last_sync_time": 5, "last_issue_updated": 1_700_000_000_000}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda _key: dict(point))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda _key, data: point.update(data))
        c._load_issue_security = AsyncMock(return_value=security)
        c._process_new_records = AsyncMock()
        c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)

        async def fetch(*_a: object, **_k: object) -> AsyncIterator:
            return
            yield

        c._fetch_issues_batched = fetch
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), _users()[:2], 1)
        written = [g for call in c.data_entities_processor.on_new_record_groups.await_args_list for g in call.args[0]]
        grants = {p.email for group, perms in written if group.external_group_id == "level:p-1:10102" for p in perms}
        assert grants == {"mia@x.com"}

    @pytest.mark.asyncio
    async def test_linking_users_between_syncs_saves_their_re_read_first(self) -> None:
        c = _connector()
        c.data_source = MagicMock()
        order: list[str] = []
        listed = _users()
        c._fetch_users = AsyncMock(return_value=listed)
        c._stored_app_users = [listed[0]]
        c._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        c._keep_licensed_users = AsyncMock(side_effect=lambda users, _m: users)
        c._note_people_to_reread = AsyncMock(side_effect=lambda ids: order.append(("note", sorted(ids))))
        c.data_entities_processor.on_new_app_users = AsyncMock(side_effect=lambda _u: order.append(("link",)))
        c._sync_user_groups = AsyncMock(return_value=None)
        c._list_projects_with_filter = AsyncMock(return_value=[])
        c._sync_project_lead_roles = AsyncMock()
        await c._link_users_groups_and_roles()
        assert order == [("note", ["acc-2", "acc-9"]), ("link",)]


class TestAnonymousBrowseReachesEveryone:
    """Browse held by `anyone` makes a project public only where the site allows anonymous access (not on Free, not
    switched off in Atlassian Administration, not blocked by a "Prevent anonymous access" policy). The org's All
    team passes the gate only while every project granting `anyone` answers a search made with no credentials."""

    PUBLIC = [(MagicMock(short_name="PUB", external_group_id="10001"), [Permission(entity_type=EntityType.ORG, external_id="anyone_authenticated",
                                                        type=PermissionType.READ)])]
    PRIVATE = [(MagicMock(short_name="PRV", external_group_id="10002"), [Permission(entity_type=EntityType.GROUP, external_id="g-1",
                                                         type=PermissionType.READ)])]

    @staticmethod
    def _gated(scope: str = "team", *, readable: bool | None = True, confirmed_hours_ago: float | None = None,
               ) -> JiraConnector:
        c = _connector()
        c.scope = scope
        # The All-team edge, open when a confirmation was saved; the store says whether the edge is there afterwards.
        c.edge = confirmed_hours_ago is not None

        async def open_gate(_connector_id: str) -> bool:
            c.edge = True
            return True

        async def close_gate(_connector_id: str) -> None:
            c.edge = False

        c.data_entities_processor.open_app_to_org_team = AsyncMock(side_effect=open_gate)
        c.data_entities_processor.withdraw_team_app_edge = AsyncMock(side_effect=close_gate)
        c.data_entities_processor.org_team_app_edge_exists = AsyncMock(side_effect=lambda _c: c.edge)
        c._read_anonymously = AsyncMock(return_value=readable)
        c.notify = AsyncMock()
        c.notify_and_wait = AsyncMock(return_value=NotificationOutcome.SENT)
        c.stored = {}
        if confirmed_hours_ago is not None:
            c.stored[ANONYMOUS_GATE_KEY] = {"confirmed_at": int(time.time() * 1000 - confirmed_hours_ago * 3_600_000),
                                            "projects": json.dumps(["10001"])}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: c.stored.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: c.stored.__setitem__(k, v))
        return c

    @pytest.mark.asyncio
    async def test_no_answer_within_a_day_of_the_last_confirmation_keeps_the_gate(self) -> None:
        c = self._gated(readable=None, confirmed_hours_ago=23)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.notify_and_wait.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_answer_for_more_than_a_day_closes_the_gate_and_tells_the_owner_once(self) -> None:
        """A project made private while every signed-out search goes unanswered must not stay open to the org."""
        c = self._gated(readable=None, confirmed_hours_ago=25)
        for _ in range(2):
            await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        assert c.data_entities_processor.withdraw_team_app_edge.await_count == 2
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.notify_and_wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_confirmation_opens_the_gate_again_and_restarts_the_day(self) -> None:
        c = self._gated(readable=None, confirmed_hours_ago=25)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c._read_anonymously = AsyncMock(return_value=True)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        await c._settle_org_gate(set())
        c.data_entities_processor.open_app_to_org_team.assert_awaited_once_with("conn-sec")
        c._read_anonymously = AsyncMock(return_value=None)
        c.data_entities_processor.withdraw_team_app_edge.reset_mock()
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_answer_keeps_the_gate_only_for_the_projects_last_confirmed(self) -> None:
        """A project granting `anyone` since the last confirmation was never shown public: a "Prevent anonymous
        access" policy may cover it alone. With no answer for it the gate closes; the owner is told nothing, since
        Jira has not gone a day unanswered."""
        c = self._gated(readable=None, confirmed_hours_ago=1)
        new = [(MagicMock(short_name="NEW", external_group_id="10003"), self.PUBLIC[0][1])]
        await c._admit_everyone_to_public_projects(self.PUBLIC + new, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.notify_and_wait.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_confirmation_records_the_projects_it_covers(self) -> None:
        c = self._gated()
        await c._admit_everyone_to_public_projects(self.PRIVATE + self.PUBLIC, written_in_full=True)
        await c._settle_org_gate(set())
        assert json.loads(c.stored[ANONYMOUS_GATE_KEY]["projects"]) == ["10001"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("left_out", ["_unresolved_selected_keys", "_skipped_project_keys", "_missing_selected_keys"])
    async def test_a_selected_project_this_sync_left_out_keeps_the_gate_closed(self, left_out: str) -> None:
        """A selected project whose key could not be looked up, that Jira no longer finds, or that could not be built,
        keeps its stored grants, `anyone` among them, without being asked: opening the gate would open it unchecked."""
        c = self._gated()
        setattr(c, left_out, ["OLD"] if left_out == "_skipped_project_keys" else {"OLD"})
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    async def test_a_project_a_signed_out_search_reads_opens_the_gate_to_the_org(self) -> None:
        c = self._gated()
        await c._admit_everyone_to_public_projects(self.PRIVATE + self.PUBLIC, written_in_full=True)
        c._read_anonymously.assert_awaited_once_with("PUB")
        await c._settle_org_gate(set())
        c.data_entities_processor.open_app_to_org_team.assert_awaited_once_with("conn-sec")
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("readable", "written_in_full"), [(False, True), (True, False)],
                             ids=["signed_out_search_reads_nothing", "a_project_kept_as_stored"])
    async def test_anyone_without_proof_of_public_access_keeps_the_gate_closed(
        self, readable: bool, written_in_full: bool,
    ) -> None:
        """A failed or empty signed-out search (a Free site, anonymous access off, a Guard policy) means the ORG
        grant reaches only linked accounts; so does a sync that kept a project's access as stored."""
        c = self._gated(readable=readable)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=written_in_full)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    async def test_a_signed_out_search_without_an_answer_leaves_the_gate_as_it_is(self) -> None:
        """A search cut short (page bound hit on a fresh token, or no response) neither opens the gate nor removes
        an open one confirmed within the day; the next sync asks again."""
        c = self._gated(readable=None, confirmed_hours_ago=1)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("answers", "closed"), [([None, None], False), ([None, False], True)],
                             ids=["both_unanswered_keep_it", "a_later_no_closes_it"])
    async def test_one_project_signed_out_visitors_cannot_read_closes_the_gate_whatever_the_others(
        self, answers: list, closed: bool,
    ) -> None:
        """Both projects were confirmed within the day, so no answer keeps the gate; a no after it still closes it."""
        c = self._gated(confirmed_hours_ago=1)
        c.stored[ANONYMOUS_GATE_KEY]["projects"] = json.dumps(["10001", "10004"])
        other = [(MagicMock(short_name="PUB2", external_group_id="10004"), self.PUBLIC[0][1])]
        c._read_anonymously = AsyncMock(side_effect=answers)
        await c._admit_everyone_to_public_projects(self.PUBLIC + other, written_in_full=True)
        assert c.data_entities_processor.withdraw_team_app_edge.await_count == int(closed)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_project_that_answers_no_ends_the_probe(self) -> None:
        """A no closes the gate whatever the others answer, so they are not asked (each may wait out rate limits)."""
        c = self._gated(confirmed_hours_ago=1)
        other = [(MagicMock(short_name="PUB2", external_group_id="10004"), self.PUBLIC[0][1])]
        c._read_anonymously = AsyncMock(side_effect=[False, True])
        await c._admit_everyone_to_public_projects(self.PUBLIC + other, written_in_full=True)
        c._read_anonymously.assert_awaited_once_with("PUB")
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    async def test_a_no_closes_the_gate_before_the_gate_state_is_read(self) -> None:
        c = self._gated(readable=False, confirmed_hours_ago=1)
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=ConnectionError("store down"))
        with contextlib.suppress(ConnectionError):
            await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    async def test_no_answer_a_day_to_the_millisecond_after_the_confirmation_closes_the_gate(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.connectors.sources.atlassian.jira_cloud import connector as module

        c = self._gated(readable=None, confirmed_hours_ago=1)
        confirmed_at = c.stored[ANONYMOUS_GATE_KEY]["confirmed_at"]
        monkeypatch.setattr(module, "get_epoch_timestamp_in_ms", lambda: confirmed_at + module.ANONYMOUS_GATE_MAX_AGE_MS)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("failed", "opened"), [(set(), True), ({"PUB"}, False)],
                             ids=["issue_pass_done", "issue_pass_failed"])
    async def test_a_closed_gate_opens_only_after_the_issue_pass(self, failed: set, opened: bool) -> None:
        """A stored issue that gained a level since its last sync still inherits its project until the issue pass
        writes the level: the org passes the gate only after that pass, and not when it failed for a public project."""
        c = self._gated()
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        await c._settle_org_gate(failed)
        assert c.data_entities_processor.open_app_to_org_team.await_count == int(opened)
        assert bool((c.stored.get(ANONYMOUS_GATE_KEY) or {}).get("confirmed_at")) is opened

    @pytest.mark.asyncio
    async def test_an_open_gate_a_search_confirms_stays_open_at_once(self) -> None:
        c = self._gated(confirmed_hours_ago=1)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    async def test_a_project_the_last_confirmation_did_not_cover_closes_the_gate_before_its_grants_are_written(
        self,
    ) -> None:
        """Written first through an open gate, a new `anyone` grant would reach the org before the search asks."""
        c = self._gated(confirmed_hours_ago=1)
        new = [(MagicMock(short_name="NEW", external_group_id="10003"), self.PUBLIC[0][1])]
        await c._close_org_gate_for_unconfirmed(self.PUBLIC)
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()
        await c._close_org_gate_for_unconfirmed(self.PUBLIC + new)
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        assert not c.stored[ANONYMOUS_GATE_KEY]["confirmed_at"]

    @pytest.mark.asyncio
    async def test_an_expired_confirmation_closes_the_gate_on_a_sync_that_stops_before_it(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.connectors.sources.atlassian.jira_cloud import connector as module

        c = self._gated(confirmed_hours_ago=25)
        c.data_source = MagicMock()
        c.register_authenticated_source_user = AsyncMock()
        monkeypatch.setattr(module, "load_connector_filters", AsyncMock(side_effect=RuntimeError("config store down")))
        with pytest.raises(RuntimeError):
            await c.run_sync()
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        c.notify_and_wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_closing_notice_the_broker_did_not_take_is_sent_again(self) -> None:
        c = self._gated(confirmed_hours_ago=25)
        c.notify_and_wait = AsyncMock(return_value=NotificationOutcome.FAILED)
        await c._expire_org_gate()
        c.notify_and_wait = AsyncMock(return_value=NotificationOutcome.SENT)
        await c._expire_org_gate()
        c.notify_and_wait.assert_awaited_once()
        await c._expire_org_gate()
        c.notify_and_wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_saved_confirmation_without_the_edge_opens_only_after_the_issue_pass(self) -> None:
        """A withdrawal whose state write failed leaves the time: the live edge, not the time, says the gate is open."""
        c = self._gated(confirmed_hours_ago=1)
        c.edge = False
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        await c._settle_org_gate(set())
        c.data_entities_processor.open_app_to_org_team.assert_awaited_once_with("conn-sec")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("open_already", [True, False], ids=["refresh", "first_opening"])
    async def test_no_confirmation_is_saved_when_the_org_has_no_all_team(self, open_already: bool) -> None:
        c = self._gated(confirmed_hours_ago=1 if open_already else None)
        c.data_entities_processor.open_app_to_org_team = AsyncMock(return_value=False)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        await c._settle_org_gate(set())
        assert not (c.stored.get(ANONYMOUS_GATE_KEY) or {}).get("confirmed_at")

    @pytest.mark.asyncio
    async def test_an_open_edge_with_no_saved_confirmation_closes_at_the_start_of_a_sync(self) -> None:
        c = self._gated()
        c.edge = True
        await c._expire_org_gate()
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        c.notify_and_wait.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_confirmation_that_cannot_be_saved_closes_the_edge_it_opened(self) -> None:
        c = self._gated()
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=ConnectionError("store down"))
        with pytest.raises(ConnectionError):
            await c._settle_org_gate(set())
        assert c.edge is False

    @pytest.mark.asyncio
    async def test_the_confirmation_is_dated_by_the_signed_out_check_not_the_issue_pass(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.connectors.sources.atlassian.jira_cloud import connector as module

        clock = [1_000_000]
        monkeypatch.setattr(module, "get_epoch_timestamp_in_ms", lambda: clock[0])
        c = self._gated()
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        clock[0] += 3_600_000
        await c._settle_org_gate(set())
        assert c.stored[ANONYMOUS_GATE_KEY]["confirmed_at"] == 1_000_000
        c = self._gated()
        clock[0] = 1_000_000
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        clock[0] += module.ANONYMOUS_GATE_MAX_AGE_MS
        await c._settle_org_gate(set())
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_public_project_whose_issue_pass_skipped_an_issue_closes_an_open_gate(self) -> None:
        """An issue that failed to build (or a partial search) may still inherit the project it gained a level in."""
        c = self._gated(confirmed_hours_ago=1)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_awaited_once_with("conn-sec")
        await c._settle_org_gate({"PUB"})
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        assert not c.stored[ANONYMOUS_GATE_KEY]["confirmed_at"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("stored", "selected", "granted", "left_out"), [
        ([("10001", "PUB")], [], False, False), ([("10001", "PUB"), ("10009", "GONE")], [], False, True),
        ([("10001", "PUB"), ("10009", "OUT")], ["PUB"], False, False),
        ([("10001", "PUB"), ("10009", "OUT")], ["PUB"], True, True),
        ([("10001", "PUB"), ("10009", "OUT")], ["PUB"], ConnectionError("store down"), True),
        (ConnectionError("store down"), [], False, True)],
        ids=["all_listed", "one_missing", "filtered_out_and_unlinked", "filtered_out_still_granted",
             "filtered_out_unreadable", "unreadable"])
    async def test_a_stored_project_missing_from_the_listing_keeps_the_gate_closed(
        self, stored: object, selected: list, granted: object, left_out: bool,
    ) -> None:
        """It keeps its stored grants, `anyone` among them, until Jira says it is gone (a 503 keeps it). A project the filter
        now leaves out keeps them until the full sync's unlink takes them, which runs after the gate."""
        c = _connector()
        c._stored_projects = AsyncMock(side_effect=stored) if isinstance(stored, Exception) else AsyncMock(return_value=stored)
        c._project_still_selected = lambda project: not selected or project["key"] in selected
        c.data_entities_processor.has_stored_permissions = (
            AsyncMock(side_effect=granted) if isinstance(granted, Exception) else AsyncMock(return_value=granted))
        assert await c._stored_projects_left_out({"10001"}) is left_out

    @pytest.mark.asyncio
    async def test_a_failed_read_of_the_stored_projects_is_not_read_as_none(self) -> None:
        """The stores answer a failed read with no rows unless asked to raise: read as none, nothing would be left out."""
        c = _connector()

        async def nodes(*_a: object, raise_on_error: bool = False, **_k: object) -> list:
            if raise_on_error:
                raise ConnectionError("store down")
            return []

        c.data_entities_processor.get_nodes_by_filters = AsyncMock(side_effect=nodes)
        assert await c._stored_projects_left_out({"10001"}) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("where", ["refresh_cleanup", "first_opening_read", "first_opening_cancelled_save"])
    async def test_an_opening_that_fails_part_way_closes_the_gate(self, where: str) -> None:
        """The edge may be written before the failure (the cleanup, the read-back or the save): the gate is closed again
        and the failure goes on, so no edge stays open without its saved confirmation or with a cleanup undone."""
        import asyncio

        c = self._gated(confirmed_hours_ago=1 if where == "refresh_cleanup" else None)

        async def open_then_fail(_connector_id: str) -> bool:
            c.edge = True
            raise ConnectionError("store down")

        error: type[BaseException] = ConnectionError
        if where == "first_opening_cancelled_save":
            error = asyncio.CancelledError
            c.issues_sync_point.update_sync_point = AsyncMock(side_effect=asyncio.CancelledError())
        else:
            c.data_entities_processor.open_app_to_org_team = AsyncMock(side_effect=open_then_fail)
        with pytest.raises(error):
            await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
            await c._settle_org_gate(set())
        assert c.edge is False

    @pytest.mark.asyncio
    async def test_a_live_edge_whose_confirmation_expired_closes_before_the_new_opening_waits(self) -> None:
        """Fresh at sync start, a day old once the checks answered: the edge closes, and only a clean issue pass reopens it."""
        c = self._gated(confirmed_hours_ago=25)
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        await c._settle_org_gate({"PUB"})
        assert c.edge is False

    @pytest.mark.asyncio
    async def test_a_stale_confirmation_closes_the_edge_without_reading_it(self) -> None:
        """A failed edge read must not stop the withdrawal of a gate whose confirmation is a day old."""
        c = self._gated(confirmed_hours_ago=25)
        c.data_entities_processor.org_team_app_edge_exists = AsyncMock(side_effect=ConnectionError("store down"))
        with contextlib.suppress(ConnectionError):
            await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        assert c.edge is False

    @pytest.mark.asyncio
    async def test_a_gate_kept_without_an_answer_closes_if_its_day_ends_during_the_issue_pass(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.connectors.sources.atlassian.jira_cloud import connector as module

        c = self._gated(readable=None, confirmed_hours_ago=1)
        confirmed_at = c.stored[ANONYMOUS_GATE_KEY]["confirmed_at"]
        clock = [confirmed_at + module.ANONYMOUS_GATE_MAX_AGE_MS - 1]
        monkeypatch.setattr(module, "get_epoch_timestamp_in_ms", lambda: clock[0])
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        assert c.edge is True
        clock[0] += 2
        await c._settle_org_gate(set())
        assert c.edge is False
        c.notify_and_wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_public_project_closes_the_gate(self) -> None:
        c = self._gated()
        await c._admit_everyone_to_public_projects(self.PRIVATE, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_awaited_once_with("conn-sec")
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_personal_connector_never_opens_the_gate(self) -> None:
        c = self._gated("personal")
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("pages", "public"), [
        ([{"issues": [], "isLast": False, "nextPageToken": "t"}, {"issues": [{"id": "1"}], "isLast": True}], True),
        ([{"issues": [], "isLast": True}], False),
        ([{"issues": [], "isLast": False, "nextPageToken": "t"}, {"issues": [], "isLast": False, "nextPageToken": "t"}],
         False),
        ([{"issues": [], "isLast": False, "nextPageToken": f"t{i}"} for i in range(5)] + [{"issues": [{"id": "1"}]}],
         True),
        ([{"issues": [], "isLast": False, "nextPageToken": f"t{i}"} for i in range(ANONYMOUS_READ_PAGES)], None),
        ([{"issues": [], "isLast": False, "nextPageToken": {"t": 1}}], False),
        ([{"issues": [None], "isLast": True}], False),
        ([{"issues": [{"key": "PUB-1"}], "isLast": True}], False),
    ], ids=["issue_on_a_later_page", "terminal_empty_page", "repeated_token", "five_empty_pages_then_an_issue",
            "page_bound_is_unknown", "token_not_a_string", "null_row", "row_without_an_id"])
    async def test_the_signed_out_search_follows_empty_pages_that_are_not_the_last(
        self, monkeypatch: pytest.MonkeyPatch, pages: list, public: bool,
    ) -> None:
        """Enhanced search can answer an empty page with isLast false and a nextPageToken before a page with issues:
        only a terminal page or a repeated token is an answer; the page bound reached on a fresh token is none."""
        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        answers = iter(pages)
        sent: list[dict] = []

        def answer(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=next(answers))

        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
        c = _connector()
        c.site_url = "https://acme.atlassian.net"
        assert await c._read_anonymously("PUB") is public
        assert len(sent) <= module.ANONYMOUS_READ_PAGES
        assert {body["maxResults"] for body in sent} == {module.ISSUE_ID_LISTING_PAGE_SIZE}
        if len(sent) > 1:
            assert sent[1]["nextPageToken"] == pages[0]["nextPageToken"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("status", "body", "public"), [
        (200, {"issues": [{"id": "1"}]}, True), (200, {"issues": []}, False), (401, {}, False),
        (200, "<html>log in</html>", False), (400, {}, False), (403, {}, False), (429, {}, None), (503, {}, None)],
        ids=["issue_returned", "nothing_returned", "refused", "not_json", "invalid", "forbidden", "rate_limited",
             "unavailable"])
    async def test_the_signed_out_search_sends_no_credentials_and_reads_public_only_from_an_issue(
        self, monkeypatch: pytest.MonkeyPatch, status: int, body: object, public: bool,
    ) -> None:
        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)

        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
        monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
        c = _connector()
        c.site_url = "https://acme.atlassian.net"
        assert await c._read_anonymously("PUB") is public
        assert str(seen[0].url) == "https://acme.atlassian.net/rest/api/3/search/jql"
        assert "authorization" not in {k.lower() for k in seen[0].headers}
        assert json.loads(seen[0].content)["jql"] == 'project = "PUB"'


    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 503])
    async def test_a_rate_limited_or_unavailable_signed_out_search_leaves_the_gate_as_it_is(
        self, monkeypatch: pytest.MonkeyPatch, status: int,
    ) -> None:
        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(
            transport=httpx.MockTransport(lambda _request: httpx.Response(status, json={})), **kw))
        monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
        c = self._gated(confirmed_hours_ago=1)
        del c._read_anonymously
        c.site_url = "https://acme.atlassian.net"
        await c._admit_everyone_to_public_projects(self.PUBLIC, written_in_full=True)
        c.data_entities_processor.withdraw_team_app_edge.assert_not_awaited()
        c.data_entities_processor.open_app_to_org_team.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_rate_limit_that_passes_within_the_probe_is_an_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rate limiting: Retry-After is "Only returned with 429 responses. Indicates how many seconds to wait before
        retrying." The probe waits that long and asks again, a bounded number of times."""
        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        answers = iter([httpx.Response(429, headers={"Retry-After": "2"}, json={}),
                        httpx.Response(200, json={"issues": [{"id": "1"}], "isLast": True})])
        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(
            transport=httpx.MockTransport(lambda _request: next(answers)), **kw))
        slept = AsyncMock()
        monkeypatch.setattr(module.asyncio, "sleep", slept)
        c = _connector()
        assert await c._read_anonymously("PUB") is True
        slept.assert_awaited_once_with(2.0)

    @pytest.mark.asyncio
    async def test_a_rate_limit_that_lasts_past_the_attempts_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        sent: list = []

        def answer(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(503, json={})

        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
        monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
        c = _connector()
        assert await c._read_anonymously("PUB") is None
        assert len(sent) == module.ANONYMOUS_READ_ATTEMPTS

    @pytest.mark.asyncio
    async def test_a_signed_out_check_past_its_deadline_is_no_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A read timeout applies per chunk: a server that keeps trickling bytes would hold the check open."""
        import asyncio

        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200, json={"issues": [{"id": "1"}]})

        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(slow), **kw))
        monkeypatch.setattr(module, "ANONYMOUS_READ_DEADLINE_S", 0.05)
        c = _connector()
        c.site_url = "https://acme.atlassian.net"
        assert await c._read_anonymously("PUB") is None

    @pytest.mark.asyncio
    async def test_a_signed_out_search_with_no_response_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        from app.connectors.sources.atlassian.jira_cloud import connector as module

        def answer(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        real = httpx.AsyncClient
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
        c = _connector()
        c.site_url = "https://acme.atlassian.net"
        assert await c._read_anonymously("PUB") is None


class TestSweepAncestorReads:
    """A stub whose ancestor could not be read (not "Jira has no such issue") is not swept: the sweep says so, and
    an account listed again stays owed until a sweep reads it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("status", "errors", "swept"), [(200, [], True), (503, [], False), (200, ["77"], False)],
                             ids=["read", "unavailable", "issue_errors"])
    async def test_a_failed_ancestor_read_reports_the_sweep_incomplete(self, status: int, errors: list, swept: bool) -> None:
        """issueErrors: "When Jira can't return an issue enumerated in a request due to a retriable error or payload
        constraint, we'll return the respective issue ID with a corresponding error message": not an answer."""
        c = _connector()
        stub = MagicMock(record_type=RecordType.TICKET, external_record_group_id=PROJECT_ID, external_record_id="77",
                         external_revision_id="1", parent_external_record_id=None)
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        response = MagicMock(status=status, json=MagicMock(return_value={"issues": [], "issueErrors": errors}))
        c._call_with_retry = AsyncMock(return_value=response)
        assert await c._sweep_placeholder_records({PROJECT_ID}, {PROJECT_ID}) is swept


class TestLevelReadersFollowTheMembersRead:
    """The reporter (Browse names them on the issue) reads a leveled issue on their own while the level holds them through
    ``group-1``. Removed from the group in Jira, they must lose that grant: the level's issues are read again."""

    REPORTER = [{"type": "reporter"}]
    REP, OTHER = _users()[:2]

    @staticmethod
    def _group_read(c: JiraConnector, members: list[AppUser], *, cut_short: bool) -> None:
        c._fetch_groups = AsyncMock(return_value=([{"groupId": "group-1", "name": "G1"}], cut_short))
        c._group_fetch_interrupted = cut_short
        c.notify = AsyncMock()
        c._process_group = AsyncMock(return_value=("group-1", "G1", MagicMock(), members))
        c.data_entities_processor.on_new_user_groups = AsyncMock()

    def _stored_digests(self, c: JiraConnector) -> dict:
        """The level digests the last sync saved, with the reporter in ``group-1``."""
        c._group_members = {"group-1": [self.REP], "G1": [self.REP]}
        c._role_members = {"PROJ_10003": [], "PROJ_projectLead": []}
        ctx = _present()  # its user holder is outside the directory here: it reads nothing and leaves the others known
        ctx.browse_holders, ctx.level_readers = self.REPORTER, c._level_readers(ctx, "PROJ", {})
        assert ctx.level_readers["10102"] is not None
        return ctx.level_fingerprints(split=True)

    async def _next_issue_pass(self, c: JiraConnector, stored: dict) -> list:
        c._browse_issue_holders["PROJ"] = self.REPORTER
        browse = IssueSecurityContext(knowledge=SchemeKnowledge.NONE, browse_holders=self.REPORTER).browse_fingerprint()
        _, _, calls, _ = await TestMemberChangeRereadsOnlyTheAffectedLevels()._sync(
            stored_levels=stored, connector=c, stored_browse=browse)
        return calls[1:]

    @pytest.mark.asyncio
    async def test_a_group_list_cut_short_still_applies_the_members_it_read(self) -> None:
        c = _connector()
        stored = self._stored_digests(c)
        self._group_read(c, [self.OTHER], cut_short=True)
        assert await c._sync_user_groups([self.REP, self.OTHER]) is None
        assert c._group_members["group-1"] == [self.OTHER]
        assert await self._next_issue_pass(c, stored) == [
            {"resume": None, "is_new_project": True, "level_ids": ["10102"], "leveled_only": False, "stored_only": True}]

    def _reporter_grant(self, c: JiraConnector) -> tuple[list, list]:
        """What a leveled issue the reporter (``self.REP``) reports writes, with the level holding ``group-1`` and a role."""
        ctx = _present({"10102": [{"type": "group", "value": "group-1"}, {"type": "projectLead"},
                                  {"type": "projectRole", "parameter": "10003", "projectRole": {"id": "10003", "name": "Developers"}}]})
        ctx.browse_holders = self.REPORTER
        users = {self.REP.source_user_id: self.REP}
        ctx.level_readers = c._level_readers(ctx, "PROJ", users)
        rec = _stored_ticket()
        grants, skipped, _ = apply_issue_access_settled(
            rec, _issue({"id": "10102"})["fields"], is_subtask=False, project_key="PROJ", context=ctx,
            user_by_account_id=users, project_id=PROJECT_ID)
        return [g.email for g in grants], skipped

    @pytest.mark.asyncio
    async def test_a_group_whose_members_were_not_read_grants_nobody_on_its_own(self) -> None:
        """The reporter was in group-1 when an earlier read cached it; Jira has since removed them, and this read of the group
        fails. The stale members must not give the reporter a grant of their own (the graph keeps the group's stored members)."""
        c = _connector()
        c._role_members = {"PROJ_10003": [], "PROJ_projectLead": []}
        c._group_members = {"group-1": [self.REP], "G1": [self.REP]}
        assert self._reporter_grant(c)[0] == ["rep@x.com"]  # read in full: the reporter reads it alone
        self._group_read(c, None, cut_short=False)  # the group's member read fails
        await c._sync_user_groups([self.REP, self.OTHER])
        assert "group-1" not in c._group_members
        c._role_members = {"PROJ_10003": [], "PROJ_projectLead": []}
        assert self._reporter_grant(c) == ([], [LEVEL_MEMBERS_UNREAD])

    @pytest.mark.asyncio
    async def test_a_group_list_that_fails_leaves_every_group_unknown(self) -> None:
        c = _connector()
        c._group_members = {"group-1": [self.REP]}
        c._fetch_groups = AsyncMock(return_value=([], True))
        c._group_fetch_interrupted = True
        c.notify = AsyncMock()
        assert await c._sync_user_groups([self.REP]) is None
        assert c._group_members == {}

    @pytest.mark.asyncio
    async def test_a_role_or_lead_not_read_grants_nobody_on_its_own(self) -> None:
        c = _connector()
        c._group_members = {"group-1": []}
        c._role_members = {"PROJ_10003": [self.REP], "PROJ_projectLead": []}
        assert self._reporter_grant(c)[0] == ["rep@x.com"]
        # The role's read fails this sync (left out, its stored members kept in the graph).
        c._fetch_project_roles = AsyncMock(return_value=("PROJ", [], True))
        c.notify = AsyncMock()
        c.data_source = MagicMock()
        c.data_entities_processor.on_new_app_roles = AsyncMock()
        await c._sync_project_roles(["PROJ"], [self.REP], {})
        await c._sync_project_lead_roles([{"key": "PROJ", "lead": {"accountId": "acc-1", "displayName": "R"}}], [self.REP])
        assert self._reporter_grant(c) == ([], [LEVEL_MEMBERS_UNREAD])
        # The lead is the reporter, read: they read it alone again through the lead holder.
        c._role_members["PROJ_10003"] = []
        assert c._role_members["PROJ_projectLead"] == [self.REP]
        assert self._reporter_grant(c)[0] == ["rep@x.com"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("licensed", [True, False], ids=["product_access", "no_product_access"])
    async def test_a_browse_named_group_member_whose_email_is_hidden_reads_it_by_account(self, licensed: bool) -> None:
        """The reporter is a linked account whose email Jira hides (``_listed_without_email``) and a member of the level's
        group: they read the issue alone, granted by account. Without product access they are anonymous to Jira: no grant."""
        hidden = AppUser(app_name="JIRA", connector_id="conn-sec", source_user_id="acc-h", org_id="org-1", email="",
                         full_name="H", is_active=True)
        c = _connector()
        c._stored_app_users = [hidden]
        c._listed_account_ids = {"acc-h"}
        c._licensed_account_ids = {"acc-h", self.OTHER.source_user_id} if licensed else {self.OTHER.source_user_id}
        c._fetch_groups = AsyncMock(return_value=([{"groupId": "group-1", "name": "G1"}], False))
        c._fetch_group_members = AsyncMock(return_value=(["acc-h"], True))
        c.data_entities_processor.on_new_user_groups = AsyncMock()
        await c._sync_user_groups([self.OTHER])
        assert c.data_entities_processor.on_new_user_groups.await_args.args[0][0][1] == []  # the graph holds members by email
        ctx = _present({"10102": [{"type": "group", "value": "group-1"}]})
        ctx.browse_holders = self.REPORTER
        directory = {self.OTHER.source_user_id: self.OTHER}
        users = c._grant_directory(directory)
        ctx.level_readers = c._level_readers(ctx, "PROJ", users)
        fields = _issue({"id": "10102"})["fields"]
        fields["reporter"] = {"accountId": "acc-h"}
        rec = _stored_ticket()
        grants, _, _ = apply_issue_access_settled(rec, fields, is_subtask=False, project_key="PROJ", context=ctx,
                                                  user_by_account_id=users, project_id=PROJECT_ID)
        grants = c._directory_grants(grants, directory)
        assert [(g.email, g.external_id) for g in grants] == ([(None, "acc-h")] if licensed else [])
        assert rec.access_rule == (AccessRule.OPEN if licensed else AccessRule.STRICT)

    @pytest.mark.asyncio
    async def test_a_member_removed_by_a_link_pass_is_reread_by_the_next_sync(self) -> None:
        c = _connector()
        stored = self._stored_digests(c)
        c.data_source = MagicMock()
        c._fetch_users = AsyncMock(return_value=[self.REP, self.OTHER])
        c._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        c._keep_licensed_users = AsyncMock(side_effect=lambda users, _mapping: users)
        c._note_newly_listed = AsyncMock()
        c.data_entities_processor.on_new_app_users = AsyncMock()
        c._list_projects_with_filter = AsyncMock(return_value=[])
        c._sync_project_roles = AsyncMock()
        c._sync_project_lead_roles = AsyncMock()
        self._group_read(c, [self.OTHER], cut_short=False)
        await c._link_users_groups_and_roles()
        assert await self._next_issue_pass(c, stored) == [
            {"resume": None, "is_new_project": True, "level_ids": ["10102"], "leveled_only": False, "stored_only": True}]


class TestTheRetryListKeepsWhatAReindexAdds:
    """An issue a reindex queues for the next sync survives any overlapping write; a sync removes a queued issue only for the
    request its read covered, so one queued again after that read stays."""

    @staticmethod
    def _store(c: JiraConnector, initial: list[str], requests: dict | None = None) -> dict:
        """A checkpoint store that yields between a read and a write, as a real one may."""
        point: dict = {"issues_to_retry": json.dumps(initial), "reindex_retry": json.dumps(requests or {})}

        async def read(_key: str) -> dict:
            await asyncio.sleep(0)
            return dict(point)

        async def write(_key: str, data: dict) -> None:
            await asyncio.sleep(0)
            point.update(data)
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=read)
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=write)
        return point

    @staticmethod
    def _queued(point: dict) -> list[str]:
        return sorted(json.loads(point["reindex_retry"]))

    @pytest.mark.asyncio
    async def test_a_sync_write_after_a_reindex_add_keeps_the_issue(self) -> None:
        c = _connector()
        point = self._store(c, [])
        c._issues_to_retry, c._retry_read = {"5"}, {}  # the sync read nothing queued, and could not finish issue 5
        await c._retry_in_next_sync("PROJ", "9")  # a reindex during that sync
        await c._update_project_sync_checkpoint("PROJ", last_issue_updated=10)
        assert (json.loads(point["issues_to_retry"]), self._queued(point)) == (["5"], ["9"])

    @pytest.mark.asyncio
    async def test_a_sync_removes_only_the_requests_it_read(self) -> None:
        c = _connector()
        point = self._store(c, [], {"3": "r3", "9": "r9"})
        c._issues_to_retry, c._retry_read = set(), {"3": "r3"}
        assert await c._save_issues_to_retry("PROJ", []) == []
        assert self._queued(point) == ["9"]

    @pytest.mark.asyncio
    async def test_an_issue_queued_again_after_the_sync_read_it_stays(self) -> None:
        """The sync read issue 1001's request; a reindex queues 1001 again before the sync acknowledges its read."""
        c = _connector()
        point = self._store(c, [])
        await c._retry_in_next_sync("PROJ", "1001")
        c._issues_to_retry, c._retry_read = set(), json.loads(point["reindex_retry"])  # the sync read that request
        await c._retry_in_next_sync("PROJ", "1001")  # queued again: a newer request
        await c._save_issues_to_retry("PROJ", [])
        assert self._queued(point) == ["1001"]

    @pytest.mark.asyncio
    async def test_overlapping_writes_keep_every_issue(self) -> None:
        c = _connector()
        point = self._store(c, [])
        c._issues_to_retry, c._retry_read = {"5"}, {}
        await asyncio.gather(c._retry_in_next_sync("PROJ", "7"), c._retry_in_next_sync("PROJ", "8"),
                             c._update_project_sync_checkpoint("PROJ", last_issue_updated=10))
        assert (json.loads(point["issues_to_retry"]), self._queued(point)) == (["5"], ["7", "8"])

    @pytest.mark.asyncio
    async def test_a_retry_reads_its_issues_whatever_the_date_filters(self) -> None:
        """A reindex may have written an issue the date window no longer returns; the retry names it, so it is read."""
        c = _connector()
        modified = MagicMock()
        modified.get_value = MagicMock(return_value=(1_600_000_000_000, 1_650_000_000_000))
        created = MagicMock()
        created.get_value = MagicMock(return_value=(1_500_000_000_000, None))
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: {"modified": modified, "created": created}.get(
            getattr(key, "value", key)))
        c.data_source = MagicMock()
        seen: list = []

        async def search(**kw: object) -> MagicMock:
            seen.append(kw["jql"])
            return _resp(200, {"issues": []})

        c._search_issues_with_retry = search
        async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, issue_ids=["1001", "1002"]):
            pass
        assert seen[0].split("ORDER BY")[0] == 'project = "PROJ" AND id in (1001, 1002) '


class TestGroupMembersAreHeldByGroupId:
    """Group B's name equals group A's id. A level that holds A must not count B's members."""

    REP, OTHER = _users()[:2]

    async def _two_groups(self) -> JiraConnector:
        c = _connector()
        c._fetch_groups = AsyncMock(return_value=([{"groupId": "gA", "name": "Alpha"}, {"groupId": "gB", "name": "gA"}], False))
        members = {"gA": [self.OTHER], "gB": [self.REP]}
        c._process_group = AsyncMock(side_effect=lambda g, _users: (g["groupId"], g["name"], MagicMock(), members[g["groupId"]]))
        c.data_entities_processor.on_new_user_groups = AsyncMock()
        c.notify = AsyncMock()
        await c._sync_user_groups([self.REP, self.OTHER])
        return c

    def _reporter_grants(self, c: JiraConnector, holder: dict) -> list:
        ctx = _present({"10102": [holder]})
        ctx.browse_holders = [{"type": "reporter"}]
        users = {self.REP.source_user_id: self.REP, self.OTHER.source_user_id: self.OTHER}
        ctx.level_readers = c._level_readers(ctx, "PROJ", users)
        grants, _, _ = apply_issue_access_settled(_stored_ticket(), _issue({"id": "10102"})["fields"], is_subtask=False,
                                                  project_key="PROJ", context=ctx, user_by_account_id=users, project_id=PROJECT_ID)
        return [g.email for g in grants]

    @pytest.mark.asyncio
    async def test_a_member_of_the_group_named_like_the_level_s_group_id_gets_no_grant(self) -> None:
        c = await self._two_groups()
        assert c._group_members == {"gA": [self.OTHER], "gB": [self.REP]}
        assert self._reporter_grants(c, {"type": "group", "value": "gA", "parameter": "Alpha"}) == []

    @pytest.mark.asyncio
    async def test_a_group_named_by_its_name_alone_resolves_to_its_id(self) -> None:
        c = await self._two_groups()
        assert self._reporter_grants(c, {"type": "group", "parameter": "gA"}) == ["rep@x.com"]  # B, named "gA": rep is in it
        assert self._reporter_grants(c, {"type": "group", "parameter": "Alpha"}) == []  # A: rep is not
        assert self._reporter_grants(c, {"type": "group", "parameter": "Nobody"}) == []  # no such group: unknown


class TestTheRetryReplay:
    """The issues a sync's retry pass reads first (its own list, and the reindex requests)."""

    @staticmethod
    def _connector_with(point: dict, returned: list[str], *, cut_short: bool) -> tuple[JiraConnector, list]:
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda _key: dict(point))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda _key, data: point.update(data))
        c._load_issue_security = AsyncMock(return_value=_present())
        c._process_new_records = AsyncMock()
        c._settle_subtasks = AsyncMock(return_value=[])
        asked: list = []

        async def fetch(*_a: object, issue_ids: list | None = None, **_kw: object) -> AsyncIterator:
            asked.append(issue_ids)
            if issue_ids:
                if cut_short:
                    c._issue_build_failures += 1  # as a search warning or pages that did not end count it
                yield [(_stored_ticket(external_record_id=i), [], True) for i in issue_ids if i in returned], False, None
        c._fetch_issues_batched = fetch
        return c, asked

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cut_short", [True, False], ids=["cut_short", "complete"])
    async def test_an_issue_a_search_cut_short_left_out_stays_on_the_list(self, cut_short: bool) -> None:
        point = {"last_sync_time": 5, "last_issue_updated": 1_700_000_000_000, "issue_placement": ISSUE_PLACEMENT,
                 "issues_to_retry": json.dumps(["1001", "1002"]), "reindex_retry": json.dumps({"1003": "r3", "1004": "r4"})}
        c, asked = self._connector_with(point, ["1001", "1003"], cut_short=cut_short)
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert asked[0] == ["1001", "1002", "1003", "1004"]
        kept = (json.loads(point["issues_to_retry"]), sorted(json.loads(point["reindex_retry"])))
        # Returned ones leave either way; one not returned leaves only when the search was complete (it is gone).
        assert kept == ((["1002"], ["1004"]) if cut_short else ([], []))

    @pytest.mark.asyncio
    async def test_a_new_project_reads_its_retry_list_too(self) -> None:
        """Its checkpoint lost its timestamps (cleared): the first walk may save none, so the requests are read first."""
        point = {"issue_placement": ISSUE_PLACEMENT, "issues_to_retry": json.dumps(["1001"]),
                 "reindex_retry": json.dumps({"1002": "r2"})}
        c, asked = self._connector_with(point, ["1001", "1002"], cut_short=False)
        await c._sync_project_issues(MagicMock(short_name="PROJ", external_group_id=PROJECT_ID), [], 1)
        assert asked[0] == ["1001", "1002"] and json.loads(point["reindex_retry"]) == {}


class TestALastingUnreadReadStopsKeepingStoredMembers:
    """A group whose members, or a role whose group, cannot be read keeps its stored members for a transient failure. Read
    so for ``UNREAD_SYNCS_BEFORE_CLOSE`` syncs in a row it is a lasting one, and the stored members may hold someone Jira
    removed: the group is written with no members and the role with the members read. Its level readers stay unknown."""

    @staticmethod
    async def _role_sync(points: _KeptSyncPoints, *, short_user_read: bool = False, unread: bool = True,
                         write_fails: bool = False, notice: NotificationOutcome = NotificationOutcome.SENT) -> JiraConnector:
        c = _connector()
        c._user_read_unfinished = short_user_read
        c._has_stored_access = AsyncMock(return_value=True)  # stored members, which a short user read keeps
        c.issues_sync_point, c.notify = points, AsyncMock()
        c.notify_and_wait = AsyncMock(return_value=notice)
        c.data_source = MagicMock()
        c.data_entities_processor.on_new_app_roles = AsyncMock(side_effect=RuntimeError("graph down") if write_fails else None)
        c._call_with_retry = AsyncMock(side_effect=[
            _resp(200, {"Developers": "https://x.atlassian.net/rest/api/3/project/PROJ/role/10002"}),
            _resp(200, {"name": "Developers", "actors": [
                {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}},
                *([{"type": "atlassian-group-role-actor", "actorGroup": {"groupId": "group-unread"}}] if unread else [])]})])
        with contextlib.suppress(RuntimeError) if write_fails else contextlib.nullcontext():
            await c._sync_project_roles(["PROJ"], _users(), {"group-unread": None})
        return c

    @staticmethod
    def _preserved(c: JiraConnector) -> list[str]:
        return [n.kwargs["message"] for n in c.notify.await_args_list if "couldn't sync project roles" in n.kwargs["title"]]

    @staticmethod
    def _closed_notices(c: JiraConnector) -> list[str]:
        return [n.kwargs["message"] for n in c.notify_and_wait.await_args_list]

    @staticmethod
    def _written(c: JiraConnector) -> dict[str, list[str]]:
        return {role.source_role_id: [u.source_user_id for u in members]
                for call in c.data_entities_processor.on_new_app_roles.await_args_list for role, members in call.args[0]}

    @pytest.mark.asyncio
    async def test_a_role_whose_group_stays_unread_is_written_with_the_members_read_after_the_bound(self) -> None:
        points = _KeptSyncPoints()
        for _ in range(UNREAD_SYNCS_BEFORE_CLOSE - 1):
            c = await self._role_sync(points)
            assert self._written(c) == {}, "a transient failure keeps the stored members"
            assert self._preserved(c) and "PROJ" in self._preserved(c)[0], "kept, and reported as preserved"
        c = await self._role_sync(points)
        assert self._written(c) == {"PROJ_10002": ["acc-1"]}  # the stored members are not kept: only those read now
        assert self._preserved(c) == [], "a closed role is not reported as preserved"
        (notice,) = self._closed_notices(c)
        assert "Developers" in notice and "saved with the members read this sync" in notice
        assert "PROJ_10002" not in c._role_members  # its level readers stay unknown, so leveled issues wait

    @pytest.mark.asyncio
    async def test_a_sync_that_reads_the_role_in_full_starts_the_count_again(self) -> None:
        points = _KeptSyncPoints()
        for _ in range(UNREAD_SYNCS_BEFORE_CLOSE - 1):
            await self._role_sync(points)
        c = _connector()
        c.issues_sync_point = points
        assert await c._unread_streaks("role", set(), {"PROJ_10002"}) == {}
        c = await self._role_sync(points)
        assert self._written(c) == {}, "one failure after a full read is transient again"

    @staticmethod
    async def _group_sync(points: _KeptSyncPoints, *, short_user_read: bool = False, unread: bool = True,
                          write_fails: bool = False, notice: NotificationOutcome = NotificationOutcome.SENT) -> JiraConnector:
        c = _connector()
        c._user_read_unfinished = short_user_read
        c._has_stored_access = AsyncMock(return_value=True)
        c.issues_sync_point, c.notify = points, AsyncMock()
        c.notify_and_wait = AsyncMock(return_value=notice)
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=RuntimeError("graph down") if write_fails else None)
        c._fetch_groups = AsyncMock(return_value=([{"groupId": "group-1", "name": "devs"}], False))
        c._fetch_group_members = AsyncMock(return_value=([], False) if unread else (["acc-1"], True))
        await c._sync_user_groups(_users())
        return c

    @pytest.mark.asyncio
    async def test_a_group_whose_members_stay_unread_is_written_with_none_after_the_bound(self) -> None:
        points = _KeptSyncPoints()
        for _ in range(UNREAD_SYNCS_BEFORE_CLOSE - 1):
            c = await self._group_sync(points)
            assert c.data_entities_processor.on_new_user_groups.await_count == 0, "a transient failure keeps them"
        c = await self._group_sync(points)
        ((group, members),) = c.data_entities_processor.on_new_user_groups.await_args.args[0]
        assert (group.source_user_group_id, members) == ("group-1", [])
        assert "group-1" not in c._group_members  # unknown to level readers, not "no members"
        (notice,) = self._closed_notices(c)  # the owner is told on the sync that closes it
        assert "devs" in notice and "5 syncs in a row" in notice
        c = await self._group_sync(points)
        assert c.data_entities_processor.on_new_user_groups.await_count == 1 and self._closed_notices(c) == [], \
            "still closed on the next unread sync, and told once"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["role", "user_group"])
    @pytest.mark.parametrize("failure", ["write", "notice"])
    async def test_a_close_is_told_once_and_only_after_it_was_saved(self, kind: str, failure: str) -> None:
        """A close whose write fails, or whose notice is not delivered, is told on the next close; then never again."""
        points = _KeptSyncPoints()
        sync = self._role_sync if kind == "role" else self._group_sync
        for _ in range(UNREAD_SYNCS_BEFORE_CLOSE - 1):
            await sync(points)
        c = await (sync(points, write_fails=True) if failure == "write" else sync(points, notice=NotificationOutcome.FAILED))
        assert len(self._closed_notices(c)) == (0 if failure == "write" else 1), "nothing is told before the write"
        c = await sync(points)
        assert len(self._closed_notices(c)) == 1, "told after the next close was saved"
        c = await sync(points)
        assert self._closed_notices(c) == [], "and only once"

    @pytest.mark.asyncio
    async def test_a_full_read_then_a_new_close_is_told_again(self) -> None:
        points = _KeptSyncPoints()
        for _ in range(UNREAD_SYNCS_BEFORE_CLOSE):
            await self._group_sync(points)
        await self._group_sync(points, unread=False)
        for _ in range(UNREAD_SYNCS_BEFORE_CLOSE - 1):
            c = await self._group_sync(points)
            assert self._closed_notices(c) == []
        c = await self._group_sync(points)
        assert len(self._closed_notices(c)) == 1

    @pytest.mark.asyncio
    async def test_a_count_that_cannot_be_saved_closes_nothing_and_the_sync_goes_on(self) -> None:
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("store down"))
        assert await c._unread_streaks("role", {"PROJ_10002"}, set()) == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["role", "user_group"])
    async def test_the_bound_closes_even_while_the_user_list_is_read_in_part(self, kind: str) -> None:
        """A short user read keeps stored members against a partial write, but not against the bound: the stored members
        are what the bound drops, and the user read may stay short for good."""
        points = _KeptSyncPoints()
        points.points["unread_streaks"] = {kind: json.dumps(
            {"PROJ_10002" if kind == "role" else "group-1": UNREAD_SYNCS_BEFORE_CLOSE - 1})}
        c = await (self._role_sync(points, short_user_read=True) if kind == "role"
                   else self._group_sync(points, short_user_read=True))
        if kind == "role":
            assert self._written(c) == {"PROJ_10002": ["acc-1"]}
        else:
            ((group, members),) = c.data_entities_processor.on_new_user_groups.await_args.args[0]
            assert (group.source_user_group_id, members) == ("group-1", [])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["role", "user_group"])
    @pytest.mark.parametrize("unread", [False, True])
    async def test_below_the_bound_a_short_user_read_still_keeps_the_stored_members(self, kind: str, unread: bool) -> None:
        """The control: a role or group with stored members is still left out under a short user read, read in full (an
        ordinary write from a partial directory) or unread below the bound."""
        points = _KeptSyncPoints()
        c = await (self._role_sync(points, short_user_read=True, unread=unread) if kind == "role"
                   else self._group_sync(points, short_user_read=True, unread=unread))
        writes = c.data_entities_processor.on_new_app_roles if kind == "role" else c.data_entities_processor.on_new_user_groups
        assert writes.await_count == 0


@pytest.mark.asyncio
async def test_a_reindex_reads_no_add_on_group_of_a_role_and_leaves_the_cap_to_the_level_s_groups() -> None:
    """The sync never reads an ``atlassian-addons`` group (app accounts), so a reindex does not either: with room for one
    group, the role's add-on group must not take it from the group the role really needs."""
    c, pages, point = TestAReindexReadsOnlyTheGroupsItsIssuesNeed._reindex(
        TestReindexAppliesTheLevel.LEVELED, [{"type": "reporter"}], TestAReindexResolvesProjectRolesAndLeads.ROLE)
    c.data_source.get_project_role = AsyncMock(return_value=_resp(200, {"id": 10002, "actors": [
        {"type": "atlassian-group-role-actor", "actorGroup": {"groupId": "group-addons", "name": "atlassian-addons-admin"}},
        {"type": "atlassian-group-role-actor", "actorGroup": {"groupId": "group-1", "name": "devs"}}]}))
    with patch("app.connectors.sources.atlassian.jira_cloud.connector.REINDEX_GROUP_READS", 1):
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {"acc-1": _users()[0]})
    assert pages == ["group-1"]
    assert (record.access_rule, [p.email for p in perms]) == (AccessRule.OPEN, ["rep@x.com"])
    assert TestTheRetryListKeepsWhatAReindexAdds._queued(point) == []

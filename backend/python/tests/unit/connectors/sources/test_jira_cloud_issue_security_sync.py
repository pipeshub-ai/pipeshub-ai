"""Jira Cloud connector: issue security levels become grants on the issue.

Fixtures are shaped like Jira Cloud's answers (project/{key}/issuesecuritylevelscheme,
issuesecurityschemes/level/member, issue.fields.security); the same shapes come from the connector simulator.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
)
from app.connectors.sources.atlassian.core.read_outcome import JiraReadError
from app.connectors.sources.atlassian.jira_cloud.connector import (
    ISSUE_SEARCH_FIELDS,
    JiraConnector,
)
from app.models.entities import AppUser, TicketRecord
from app.models.permission import EntityType

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


def _resp(status=200, body=None):
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


def _issue(security=None, updated="2024-06-15T10:00:00.000+0000", key="PROJ-1", issue_id="1001"):
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


async def _build(connector, issue, context, existing=None):
    connector._issue_security[PROJECT_ID] = context
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
    records, _ = await connector._build_issue_records([issue], PROJECT_ID, _users())
    return records[0]


class TestSecuredIssues:
    @pytest.mark.asyncio
    async def test_leveled_issue_is_restricted_and_granted_to_level_members_only(self):
        """T1/T2: no project inheritance is enough; only the level's members are granted."""
        record, perms, _ = await _build(_connector(), _issue({"id": "10102", "name": "Leadership"}), _present())
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.rewrite_permissions is True
        got = {(p.entity_type, p.external_id, p.email) for p in perms}
        assert got == {
            (EntityType.GROUP, "group-1", None),
            (EntityType.USER, None, "mia@x.com"),
            (EntityType.USER, None, "rep@x.com"),
            (EntityType.USER, None, "asg@x.com"),
            (EntityType.ROLE, "PROJ_projectLead", None),
            (EntityType.ROLE, "PROJ_10003", None),
        }
        assert not any(p.entity_type == EntityType.ORG for p in perms)

    @pytest.mark.asyncio
    async def test_leveled_issue_with_unreadable_members_is_closed(self):
        ctx = _present()
        ctx.members_readable = False
        record, perms, _ = await _build(_connector(), _issue({"id": "10102"}), ctx)
        assert (record.access_rule, perms) == (AccessRule.RESTRICTED, [])

    @pytest.mark.asyncio
    async def test_unsecured_issue_in_a_project_without_a_scheme_is_unchanged(self):
        """T4/O2: a null level is project-open with the stored grants replaced by none."""
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        record, perms, _ = await _build(_connector(), _issue(None), ctx)
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)

    @pytest.mark.asyncio
    async def test_unsecured_issue_when_the_scheme_could_not_be_read_is_unchanged(self):
        """A non-admin token (403 on the scheme) must not hide the issues that carry no level."""
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        record, perms, _ = await _build(_connector(), _issue(None), ctx)
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)
        record, perms, _ = await _build(_connector(), _issue({"id": "10102"}), ctx)
        assert (record.access_rule, perms) == (AccessRule.RESTRICTED, [])

    @pytest.mark.asyncio
    async def test_unsecured_issue_in_a_project_with_a_scheme_inherits_the_project(self):
        record, perms, _ = await _build(_connector(), _issue(None), _present())
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.STRICT, [], True)

    def test_security_is_requested_with_the_issues(self):
        assert "security" in ISSUE_SEARCH_FIELDS


class TestLevelChangeOnIncrementalSync:
    @pytest.mark.asyncio
    async def test_a_changed_level_replaces_the_stored_grants(self):
        """T3: the issue's `updated` moved, so it is re-sent with its new grants."""
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue({"id": "10102"}), _present(), existing)
        assert changed is True and record.version == 3
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, perms)
        assert perms

    @pytest.mark.asyncio
    async def test_a_cleared_level_removes_the_grants(self):
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue(None), _present(), existing)
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        # an explicit empty list replaces the stored grants; the rule goes back to the project's
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, [])
        assert record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_cleared_level_replaces_grants_whatever_the_scheme_state(self):
        """O2: with the scheme NONE or UNKNOWN, a null level still clears the old level grants."""
        for knowledge in (SchemeKnowledge.NONE, SchemeKnowledge.UNKNOWN):
            c = _connector()
            existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
            record, perms, changed = await _build(c, _issue(None), IssueSecurityContext(knowledge=knowledge), existing)
            await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
            c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, [])
            assert record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_record_with_nothing_to_rewrite_is_updated_without_grants(self):
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue(None), IssueSecurityContext(knowledge=SchemeKnowledge.NONE), existing)
        record.rewrite_permissions = False
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record)


class TestLoadIssueSecurity:
    @pytest.mark.asyncio
    async def test_scheme_and_paged_members_are_read(self):
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[_resp(200, SCHEME), _resp(200, MEMBERS_PAGE_1), _resp(200, MEMBERS_PAGE_2)])
        ctx = await c._load_issue_security("PROJ")
        assert ctx.knowledge == SchemeKnowledge.PRESENT and ctx.members_readable
        assert [h["type"] for h in ctx.members_by_level["10102"]] == ["projectLead", "group", "user"]

    @pytest.mark.asyncio
    async def test_a_scheme_shared_by_projects_has_its_members_read_once(self):
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[
            _resp(200, SCHEME), _resp(200, {**MEMBERS_PAGE_2, "startAt": 0}), _resp(200, SCHEME)])
        first = await c._load_issue_security("PROJ")
        second = await c._load_issue_security("OTHER")
        assert c._call_with_retry.await_count == 3  # 2 scheme reads + 1 members read
        assert first.members_by_level == second.members_by_level

    @pytest.mark.asyncio
    async def test_a_project_without_a_scheme_is_known_to_have_none(self):
        c = _connector()
        c._call_with_retry = AsyncMock(return_value=_resp(404, {"errorMessages": ["Security level for project 10000 does not exist."]}))
        assert (await c._load_issue_security("PROJ")).knowledge == SchemeKnowledge.NONE

    @pytest.mark.asyncio
    async def test_a_failing_scheme_read_is_retried_next_sync_and_never_means_none(self):
        c = _connector()
        c._call_with_retry = AsyncMock(return_value=_resp(503, {}))
        ctx = await c._load_issue_security("PROJ")
        assert ctx.knowledge == SchemeKnowledge.UNKNOWN and ctx.defer_checkpoint is True

    @pytest.mark.asyncio
    async def test_failing_members_close_leveled_issues_only(self):
        c = _connector()
        c._call_with_retry = AsyncMock(side_effect=[_resp(200, SCHEME), _resp(500, {})])
        ctx = await c._load_issue_security("PROJ")
        assert ctx.knowledge == SchemeKnowledge.PRESENT and ctx.members_readable is False


class TestMemberChangeRereadsOnlyTheAffectedLevels:
    async def _sync(self, stored_levels, ctx=None, fetch_fn=None):
        c = _connector()
        c.issues_sync_point = MagicMock()
        point = {"last_sync_time": 5, "last_issue_updated": 1_700_000_000_000}
        if stored_levels is not None:
            point["security_levels"] = json.dumps(stored_levels)
        c.issues_sync_point.read_sync_point = AsyncMock(return_value=point)
        c.issues_sync_point.update_sync_point = AsyncMock()
        ctx = ctx or _present()
        c._load_issue_security = AsyncMock(return_value=ctx)
        calls = []

        async def fetch(key, pid, users, last_sync, resume, is_new_project=False, level_ids=None, leveled_only=False):
            calls.append({"resume": resume, "is_new_project": is_new_project, "level_ids": level_ids, "leveled_only": leveled_only})
            if fetch_fn:
                await fetch_fn(c, calls)
            return
            yield

        c._fetch_issues_batched = fetch
        rg = MagicMock(short_name="PROJ", external_group_id=PROJECT_ID)
        result = await c._sync_project_issues(rg, [], 1)
        return c, ctx, calls, result

    @pytest.mark.asyncio
    async def test_a_changed_level_gets_a_second_pass_over_its_issues_only(self):
        _, ctx, calls, result = await self._sync(stored_levels={"10102": "old"})
        assert calls == [
            {"resume": 1_700_000_000_000, "is_new_project": False, "level_ids": None, "leveled_only": False},  # the normal incremental pass
            {"resume": None, "is_new_project": True, "level_ids": ["10102"], "leveled_only": False},
        ]
        assert result["is_new_project"] is False  # not a full sync: nothing is unlinked afterwards

    @pytest.mark.asyncio
    async def test_the_fingerprints_are_stored_after_the_walk_and_not_before(self):
        c, ctx, _, _ = await self._sync(stored_levels={})
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints() and saved["last_issue_updated"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_unchanged_levels_keep_the_incremental_window_alone(self):
        _, ctx, calls, _ = await self._sync(stored_levels=_present().level_fingerprints())
        assert calls == [{"resume": 1_700_000_000_000, "is_new_project": False, "level_ids": None, "leveled_only": False}]

    @pytest.mark.asyncio
    async def test_a_level_emptied_of_its_last_holder_is_reread(self):
        """O1c: the level left the scheme's member list, so only the stored digest names it."""
        stored = {"10102": _present().level_fingerprints()["10102"], "10103": "gone-member"}
        _, _, calls, _ = await self._sync(stored_levels=stored)
        assert [c["level_ids"] for c in calls] == [None, ["10103"]]

    @pytest.mark.asyncio
    async def test_the_first_sync_after_upgrade_rereads_every_leveled_issue(self):
        """O1b: nothing stored -> `level is not EMPTY`, so holder-less levels are covered too."""
        c, ctx, calls, _ = await self._sync(stored_levels=None)
        assert calls[1] == {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": True}
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints()

    @pytest.mark.asyncio
    async def test_an_unreadable_scheme_rereads_leveled_issues_to_close_them_and_warns_once(self, caplog):
        """O1a: a 403 on the scheme closes the issues that carry a level, with an actionable warning."""
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        with caplog.at_level(logging.WARNING, logger="test.jira.sec"):
            c, _, calls, _ = await self._sync(stored_levels=None, ctx=ctx)
        assert calls[1]["leveled_only"] is True
        assert len([r for r in caplog.records if "Administer Jira" in r.getMessage()]) == 1
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == {"*": "unreadable"}
        # once recorded, a still-unreadable scheme does not repeat the walk
        _, _, calls, _ = await self._sync(stored_levels={"*": "unreadable"}, ctx=ctx)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_failed_issue_keeps_the_old_digest_so_the_next_sync_retries(self):
        """O3."""
        async def fail(c, calls):
            if len(calls) == 2:
                c._issue_build_failures += 1

        c, _, _, _ = await self._sync(stored_levels={"10102": "old"}, fetch_fn=fail)
        assert not any("security_levels" in (call.args[1] or {}) and call.args[1]["security_levels"] != json.dumps({"10102": "old"})
                       for call in c.issues_sync_point.update_sync_point.await_args_list)

    @pytest.mark.asyncio
    async def test_a_jql_400_on_level_in_falls_back_to_level_is_not_empty(self):
        """O6."""
        async def reject(c, calls):
            if len(calls) == 2:
                raise JiraReadError(400, "level 10102 does not exist")

        _, _, calls, _ = await self._sync(stored_levels={"10102": "old"}, fetch_fn=reject)
        assert [c["leveled_only"] for c in calls] == [False, False, True]

    @pytest.mark.asyncio
    async def test_the_narrowed_query_filters_by_level_and_not_by_date(self):
        c = _connector()
        c.sync_filters = None
        c.data_source = MagicMock()
        seen = {}

        async def search(**kw):
            seen.update(kw)
            return _resp(200, {"issues": []})

        c._search_issues_with_retry = search
        async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, is_new_project=True, level_ids=["10102", "10103"]):
            pass
        assert seen["jql"].startswith('project = "PROJ" AND level in (10102, 10103) ORDER BY')
        assert "updated" not in seen["jql"].split("ORDER BY")[0]


@pytest.mark.asyncio
async def test_leveled_issue_jql_is_level_is_not_empty():
    c = _connector()
    c.sync_filters = None
    c.data_source = MagicMock()
    seen = {}

    async def search(**kw):
        seen.update(kw)
        return _resp(200, {"issues": []})

    c._search_issues_with_retry = search
    async for _ in c._fetch_issues_batched("PROJ", PROJECT_ID, [], None, None, is_new_project=True, leveled_only=True):
        pass
    assert seen["jql"].startswith('project = "PROJ" AND level is not EMPTY ORDER BY')


@pytest.mark.asyncio
async def test_reindex_keeps_the_stored_access_rule():
    c = _connector()
    c.indexing_filters = MagicMock()  # already loaded
    resp = MagicMock(status=200)
    resp.json = MagicMock(return_value=_issue({"id": "10102"}, updated="2025-01-01T00:00:00.000+0000"))
    ds = MagicMock()
    ds.get_issue = AsyncMock(return_value=resp)
    c._get_fresh_datasource = AsyncMock(return_value=ds)
    stored = TicketRecord(
        id="rid", org_id="org-1", record_name="x", record_type="TICKET", external_record_id="1001", version=1,
        origin="CONNECTOR", connector_name="JIRA", connector_id="conn-sec", source_updated_at=1,
        access_rule=AccessRule.RESTRICTED, weburl="https://x/browse/PROJ-1", external_record_group_id=PROJECT_ID,
        record_group_type="PROJECT")
    result = await c._check_and_fetch_updated_issue(stored, {})
    assert result is not None and result[0].access_rule == AccessRule.RESTRICTED


@pytest.mark.asyncio
async def test_the_failure_counter_is_not_reset_before_the_reread():
    """R2-2: an issue that failed in the main walk still blocks saving the digests."""
    async def fail_in_main_walk(c, calls):
        if len(calls) == 1:
            c._issue_build_failures += 1

    c, _, _, _ = await TestMemberChangeRereadsOnlyTheAffectedLevels()._sync(
        stored_levels={"10102": "old"}, fetch_fn=fail_in_main_walk)
    assert not any(call.args[1].get("security_levels") != json.dumps({"10102": "old"})
                   for call in c.issues_sync_point.update_sync_point.await_args_list)


@pytest.mark.asyncio
async def test_a_stub_of_a_leveled_ancestor_is_restricted_with_the_level_grants():
    """R2-1: browsing lists stubs, so a stub must carry its issue's level, not the open project rule."""
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
    assert record.is_placeholder and record.access_rule == AccessRule.RESTRICTED
    assert record.rewrite_permissions and perms


def test_security_is_requested_for_ancestor_stubs():
    from app.connectors.sources.atlassian.jira_cloud.connector import (
        ANCESTOR_STUB_FIELDS,
    )
    assert "security" in ANCESTOR_STUB_FIELDS

"""Jira Cloud connector: issue security levels become grants on the issue.

Fixtures are shaped like Jira Cloud's answers (project/{key}/issuesecuritylevelscheme,
issuesecurityschemes/level/member, issue.fields.security); the same shapes come from the connector simulator.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
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
) -> tuple:
    connector._issue_security[PROJECT_ID] = context
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
    records, _ = await connector._build_issue_records([issue], PROJECT_ID, _users())
    return records[0]


class TestSecuredIssues:
    @pytest.mark.asyncio
    async def test_leveled_issue_is_restricted_and_granted_to_level_members_only(self) -> None:
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
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, perms)
        assert perms

    @pytest.mark.asyncio
    async def test_a_cleared_level_removes_the_grants(self) -> None:
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue(None), _present(), existing)
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        # an explicit empty list replaces the stored grants; the rule goes back to the project's
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, [])
        assert record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_cleared_level_replaces_grants_whatever_the_scheme_state(self) -> None:
        """O2: with the scheme NONE or UNKNOWN, a null level still clears the old level grants."""
        for knowledge in (SchemeKnowledge.NONE, SchemeKnowledge.UNKNOWN):
            c = _connector()
            existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
            record, perms, changed = await _build(c, _issue(None), IssueSecurityContext(knowledge=knowledge), existing)
            await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
            c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, [])
            assert record.access_rule == AccessRule.STRICT

    @pytest.mark.asyncio
    async def test_a_record_with_nothing_to_rewrite_is_updated_without_grants(self) -> None:
        c = _connector()
        existing = MagicMock(id="rid", version=2, source_updated_at=1, is_placeholder=False)
        record, perms, changed = await _build(c, _issue(None), IssueSecurityContext(knowledge=SchemeKnowledge.NONE), existing)
        record.rewrite_permissions = False
        await c._process_new_records([(record, perms, changed)], "PROJ", {"new_count": 0, "updated_count": 0})
        c.data_entities_processor.on_record_content_update.assert_awaited_once_with(record)


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


class TestMemberChangeRereadsOnlyTheAffectedLevels:
    async def _sync(
        self,
        stored_levels: dict | None,
        ctx: IssueSecurityContext | None = None,
        fetch_fn: Callable[[JiraConnector, list], Awaitable[None]] | None = None,
        connector: JiraConnector | None = None,
    ) -> tuple:
        c = connector or _connector()
        c.issues_sync_point = MagicMock()
        point = {"last_sync_time": 5, "last_issue_updated": 1_700_000_000_000}
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

    @pytest.mark.asyncio
    async def test_the_fingerprints_are_stored_after_the_walk_and_not_before(self) -> None:
        c, ctx, _, _ = await self._sync(stored_levels={})
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints() and saved["last_issue_updated"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_unchanged_levels_keep_the_incremental_window_alone(self) -> None:
        _, ctx, calls, _ = await self._sync(stored_levels=_present().level_fingerprints())
        assert calls == [{"resume": 1_700_000_000_000, "is_new_project": False, "level_ids": None, "leveled_only": False, "stored_only": False}]

    @pytest.mark.asyncio
    async def test_a_level_emptied_or_deleted_rereads_every_stored_issue(self) -> None:
        """O1c: the level left the member list. If it was deleted, Jira rejects its id in JQL,
        and an issue moved from it to no level matches no level predicate."""
        stored = {"10102": _present().level_fingerprints()["10102"], "10103": "gone-member"}
        c, ctx, calls, _ = await self._sync(stored_levels=stored)
        assert calls[1] == {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": False,
                            "stored_only": True}
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints()

    @pytest.mark.asyncio
    async def test_the_first_sync_after_upgrade_rereads_every_leveled_issue(self) -> None:
        """O1b: nothing stored -> `level is not EMPTY`, so holder-less levels are covered too."""
        c, ctx, calls, _ = await self._sync(stored_levels=None)
        assert calls[1] == {"resume": None, "is_new_project": True, "level_ids": None, "leveled_only": True,
                            "stored_only": True}
        saved = c.issues_sync_point.update_sync_point.await_args.args[1]
        assert json.loads(saved["security_levels"]) == ctx.level_fingerprints()

    @pytest.mark.asyncio
    async def test_an_unreadable_scheme_rereads_leveled_issues_to_close_them_and_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
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
async def test_a_stub_of_a_leveled_ancestor_is_restricted_with_the_level_grants() -> None:
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
    assert record.access_rule == AccessRule.RESTRICTED and perms


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
    return c


class TestReindexAppliesTheLevel:
    LEVELED = _issue({"id": "10102", "name": "Leadership"}, updated="2025-01-01T00:00:00.000+0000")
    MEMBERS = {"isLast": True, "values": [
        {"issueSecurityLevelId": "10102", "holder": {"type": "group", "value": "group-1"}}]}

    @pytest.mark.asyncio
    async def test_a_level_set_since_the_last_sync_is_applied_by_the_reindex(self) -> None:
        """A project-open issue given a level, then reindexed, must not stay open to the project."""
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, self.MEMBERS)])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        assert record.access_rule == AccessRule.RESTRICTED and record.rewrite_permissions is True
        assert {(p.entity_type, p.external_id) for p in perms} == {(EntityType.GROUP, "group-1")}
        assert record.source_updated_at == c._parse_jira_timestamp("2025-01-01T00:00:00.000+0000")

    @pytest.mark.asyncio
    async def test_a_transient_scheme_read_leaves_the_stored_record_alone(self) -> None:
        """Writing it would hide a project-open issue (RESTRICTED, no grant) and stamp its timestamp."""
        c = _reindex_connector(self.LEVELED, [_resp(503, {})])
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("holder", [
        None,  # the level has no member row
        {"type": "user", "parameter": "acc-x", "user": {"accountId": "acc-x", "active": False}},
        {"type": "projectRole", "parameter": "1", "projectRole": {"name": "atlassian-addons-project-access"}},
    ])
    async def test_a_level_read_in_full_that_grants_nobody_is_written(self, holder: dict | None) -> None:
        """A settled empty audience replaces the stored one; leaving it would keep the old audience."""
        values = [{"issueSecurityLevelId": "10102", "holder": holder}] if holder else []
        other = [{"issueSecurityLevelId": "10199", "holder": {"type": "group", "value": "g-other"}}]
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, {"isLast": True, "values": values + other})])
        record, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        assert (record.access_rule, perms, record.rewrite_permissions) == (AccessRule.RESTRICTED, [], True)

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
        members = {"isLast": True, "values": [{"issueSecurityLevelId": "10102", "holder": {
            "type": "applicationRole", "parameter": "jira-software"}}]}
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, members)])
        c._app_roles_mapping, c._app_roles_unread = {"jira-software": [{"groupId": "g-1"}]}, True
        assert await c._check_and_fetch_updated_issue(_stored_ticket(), {}) is None
        c = _reindex_connector(self.LEVELED, [_resp(200, SCHEME), _resp(200, members)])
        c._app_roles_mapping = {"jira-software": [{"groupId": "g-1"}]}
        _, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        assert {p.external_id for p in perms} == {"g-1"}

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
        _, perms = await c._check_and_fetch_updated_issue(_stored_ticket(), {})
        assert {p.external_id for p in perms} == {"group-1"}
        assert c._scheme_members["10002"][1]["10102"][0]["value"] == "removed-group"  # sync cache untouched


class TestLevelPassAfterFailuresAndRemovals:
    _sync = TestMemberChangeRereadsOnlyTheAffectedLevels._sync

    @pytest.mark.asyncio
    async def test_a_transient_failure_marks_the_project_and_the_next_settled_sync_rereads(self) -> None:
        """Issues updated during a 503 are closed with their old grants kept; the digests then
        match again, so only the marker makes the next sync re-read them."""
        digests = _present().level_fingerprints()
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
    async def test_a_level_naming_a_role_read_in_part_keeps_its_grants(self) -> None:
        """The role's groups are unknown, not fewer: hold the level like a transient read."""
        ctx = _present(members={"10102": [{"type": "applicationRole", "parameter": "jira-software"}]})
        c = _connector()
        c._app_roles_unread = True
        c, ctx, calls, _ = await self._sync(stored_levels={"10102": "d"}, ctx=ctx, connector=c)
        assert ctx.members_readable is False and ctx.defer_checkpoint is True
        assert len(calls) == 1
        saved = json.loads(c.issues_sync_point.update_sync_point.await_args.args[1]["security_levels"])
        assert saved == {"10102": "d", "*": "retry"}
        record, perms, _ = await _build(c, _issue({"id": "10102"}), ctx)
        assert (perms, record.rewrite_permissions) == ([], False)

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
    async def test_a_group_leaving_an_application_role_rereads_that_level(self) -> None:
        ctx = _present(members={"10102": [{"type": "applicationRole", "parameter": "jira-software"}]})
        c = _connector()
        c._app_roles_mapping = {"jira-software": [{"groupId": "g-1"}]}
        stored = ctx.level_fingerprints({"jira-software": [{"groupId": "g-1"}, {"groupId": "g-removed"}]})
        _, _, calls, _ = await self._sync(stored_levels=stored, ctx=ctx, connector=c)
        assert calls[1]["level_ids"] == ["10102"]

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
        assert records[0][0].access_rule == AccessRule.RESTRICTED  # rebuilt though unchanged

        records, _ = await c._build_issue_records(issues, PROJECT_ID, _users(), is_new_project=True)
        assert len(records) == 3  # the normal pass is unchanged

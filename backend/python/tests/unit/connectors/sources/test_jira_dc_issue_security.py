"""Jira Data Center issue security: the parts of the connector that are not a whole sync."""

import json
import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
    apply_issue_access,
    plan_level_reread,
)
from app.connectors.sources.atlassian.core.read_outcome import JiraReadError
from app.connectors.sources.atlassian.jira_data_center.connector import (
    NO_LEVEL_READ,
    JiraDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.models.entities import AppUser, RecordType, TicketRecord
from app.models.permission import EntityType, Permission, PermissionType


def _connector(cls=JiraDataCenterConnector) -> JiraDataCenterConnector:
    dep = MagicMock()
    dep.org_id = "org"
    dep.on_new_records = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return cls(logging.getLogger("t"), dep, MagicMock(), cs, "c", "team", "u")


def _ticket(**overrides) -> TicketRecord:
    fields = {
        "id": "t1", "org_id": "org", "record_name": "[P-1] x", "record_type": RecordType.TICKET,
        "external_record_id": "1", "origin": "CONNECTOR", "connector_name": "JIRA DATA CENTER",
        "connector_id": "c", "version": 1, "mime_type": "text/plain", "external_record_group_id": "10",
    }
    return TicketRecord(**{**fields, **overrides})


def _user(key: str, email: str) -> AppUser:
    return AppUser(app_name="JIRA DATA CENTER", connector_id="c", source_user_id=key, org_id="org",
                   email=email, full_name=key, is_active=True)


class TestPersonalConnector:
    async def test_a_personal_connector_applies_no_level(self) -> None:
        connector = _connector(JiraDataCenterPersonalConnector)
        context = await connector._load_issue_security("P")
        assert context.enforce is False and context.knowledge == SchemeKnowledge.NONE

    def test_a_team_connector_does(self) -> None:
        assert _connector()._enforces_issue_security is True


class TestUsernames:
    def test_a_user_holder_named_by_username_resolves_to_the_user_by_key(self) -> None:
        connector = _connector()
        connector._dc_name_to_source_id = {"carol": "carol-key"}
        users = {"carol-key": _user("carol-key", "carol@example.com")}
        resolved = connector._users_with_usernames(users)
        assert resolved["carol"].email == "carol@example.com" and resolved["carol-key"] is users["carol-key"]

    def test_a_username_that_is_a_listed_accounts_key_is_no_alias(self) -> None:
        """Alice was renamed to alice.old and kept "alice" as her key; a newer account took the username "alice"."""
        connector = _connector()
        connector._dc_name_to_source_id = {"alice": "JIRAUSER10100", "alice.old": "alice"}
        users = {"JIRAUSER10100": _user("JIRAUSER10100", "new-alice@example.com")}
        assert "alice" not in connector._users_with_usernames(users)


class TestStoredGrantsKept:
    """A leveled issue whose level could not be read yet keeps the grants it has: a full sync's first
    write of an existing record would otherwise replace them with none."""

    async def test_a_closed_stored_issue_without_grants_goes_through_the_content_update(self) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = _ticket(is_placeholder=False)
        record = _ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=False)

        await connector._process_new_records([(record, [])], "P", {"new_count": 0, "updated_count": 0})

        connector.data_entities_processor.on_record_content_update.assert_awaited_once_with(record, content_changed=False)
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.parametrize(
        ("record", "stored"),
        [
            pytest.param(_ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=True), True, id="grants-replaced"),
            pytest.param(_ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=False), False, id="new-issue"),
            pytest.param(_ticket(), True, id="no-level-involved"),
        ],
    )
    async def test_everything_else_is_written_as_before(self, record, stored) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = _ticket() if stored else None

        await connector._process_new_records([(record, [])], "P", {"new_count": 0, "updated_count": 0})

        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()


class TestAncestorStub:
    async def test_a_sync_starts_with_no_security_context_left_from_the_last_one(self) -> None:
        # The placeholder sweep runs after the walk: a holder removed since the last sync must not
        # still reach a stub because a project's read failed before its context was refreshed.
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True, members_by_level={"1": []},
        )
        await connector._sync_all_project_issues([], [], None)
        assert connector._issue_security == {}

    def test_a_stub_of_a_leveled_issue_is_granted_to_the_level_only(self) -> None:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "alice-key", "user": {"key": "alice-key"}}]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"}}}
        stub = _ticket(is_placeholder=True)

        grants = connector._secure_ancestor_stub(stub, issue, {"alice-key": _user("alice-key", "alice@example.com")})

        assert stub.access_rule == AccessRule.RESTRICTED
        assert [g.email for g in grants] == ["alice@example.com"]

    def test_a_leveled_stub_in_a_project_this_sync_did_not_read_is_closed_not_open(self) -> None:
        connector = _connector()
        issue = {"id": "9", "fields": {"project": {"id": "99", "key": "Q"}, "security": {"id": "1"}}}
        stub = _ticket(is_placeholder=True)
        assert connector._secure_ancestor_stub(stub, issue, {}) == []
        assert stub.access_rule == AccessRule.RESTRICTED and stub.rewrite_permissions is True

    @pytest.mark.parametrize("security", [None, "missing"])
    def test_a_stub_with_no_level_in_such_a_project_is_left_alone(self, security) -> None:
        connector = _connector()
        fields = {"project": {"id": "99", "key": "Q"}}
        if security != "missing":
            fields["security"] = None
        stub = _ticket(is_placeholder=True)
        assert connector._secure_ancestor_stub(stub, {"id": "9", "fields": fields}, {}) == []
        assert stub.access_rule == AccessRule.OPEN


class TestParentGate:
    async def test_a_project_without_a_scheme_files_under_a_parent_of_its_own_as_before_and_reads_nothing(self) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock()
        connector._issue_id_to_key_cache = {"5": "P-5"}
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert await connector._files_under_parent({"project": {"id": "10", "key": "P"}}, "5", none) is True
        connector._get_issue_with_retry.assert_not_awaited()

    @pytest.mark.parametrize(("parent_level", "files"), [(None, True), ("20", False)])
    async def test_a_parent_in_another_project_is_read_even_when_the_story_project_has_no_scheme(
        self, parent_level, files,
    ) -> None:
        """Its level decides: a story under another project's leveled epic would be hidden from its own readers."""
        connector = _connector()
        connector._issue_id_to_key_cache = {"5": "Q-5"}
        parent = {"id": "5", "fields": {"project": {"id": "99", "key": "Q"},
                                        "security": {"id": parent_level} if parent_level else None}}
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value=parent)))
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert await connector._files_under_parent({"project": {"id": "10", "key": "P"}}, "5", none) is files
        connector._get_issue_with_retry.assert_awaited_once()

    async def test_a_parent_that_cannot_be_read_holds_the_story_instead_of_guessing(self) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=503))
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        with pytest.raises(Exception, match="parent issue 5"):
            await connector._files_under_parent({"project": {"id": "10"}}, "5", security)

    @pytest.mark.parametrize(
        ("knowledge", "holders", "own_level", "parent_level", "files"),
        [
            pytest.param(SchemeKnowledge.PRESENT, [{"type": "reporter"}], None, None, False, id="neither has a level"),
            pytest.param(SchemeKnowledge.PRESENT, [{"type": "reporter"}], None, "20", True, id="the parent has a level"),
            pytest.param(SchemeKnowledge.PRESENT, [{"type": "reporter"}], "20", None, True, id="it has a level"),
            pytest.param(SchemeKnowledge.PRESENT, [], None, None, True, id="no per-issue holder"),
            pytest.param(SchemeKnowledge.PRESENT, None, None, None, True, id="holders unread, a new sub-task"),
            pytest.param(SchemeKnowledge.NONE, [{"type": "reporter"}], None, None, False, id="no scheme"),
            pytest.param(SchemeKnowledge.UNKNOWN, [{"type": "reporter"}], None, None, False,
                         id="scheme unread, neither has a level"),
            pytest.param(SchemeKnowledge.UNKNOWN, [{"type": "reporter"}], None, "20", True,
                         id="scheme unread, the parent has a level"),
            pytest.param(SchemeKnowledge.UNKNOWN, [{"type": "reporter"}], "absent", None, True,
                         id="its level not returned"),
        ],
    )
    async def test_a_sub_task_leaves_its_parent_only_where_a_holder_may_read_it_alone(
        self, knowledge: SchemeKnowledge, holders: list | None, own_level: str | None, parent_level: str | None,
        files: bool,
    ) -> None:
        """Then it hangs off its project with its own holders' grants, which on the parent would reach the parent's readers."""
        connector = _connector()
        parent = {"id": "5", "fields": {"project": {"id": "10", "key": "P"},
                                        "security": {"id": parent_level} if parent_level else None}}
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value=parent)))
        # A level naming the same people on every issue: one naming the sub-task's own people takes it off its parent.
        security = IssueSecurityContext(knowledge=knowledge, members_readable=True, browse_holders=holders,
                                        members_by_level={"20": [{"type": "group", "parameter": "g"}]})
        fields = {"project": {"id": "10", "key": "P"}, "security": {"id": own_level} if own_level else None}
        if own_level == "absent":
            del fields["security"]
        assert await connector._files_under_parent(fields, "5", security, is_subtask=True) is files

    @pytest.mark.parametrize(("stored_parent", "files"), [(None, False), ("5", True)])
    async def test_a_stored_sub_task_stays_where_it_was_filed_while_the_holders_are_unread(
        self, stored_parent: str | None, files: bool,
    ) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock()
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True, browse_holders=None)
        fields = {"project": {"id": "10", "key": "P"}, "security": None}
        assert await connector._files_under_parent(
            fields, "5", security, stored_parent=stored_parent, stored=True, is_subtask=True) is files
        connector._get_issue_with_retry.assert_not_awaited()

    async def test_a_parent_read_refused_with_403_is_a_failed_read_not_a_hidden_parent(self) -> None:
        """GET /rest/api/2/issue/{id} documents 404 for an issue that is missing or hidden from the caller, and no
        403 (https://developer.atlassian.com/server/jira/platform/rest/v11002/api-group-issue/)."""
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=403))
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        with pytest.raises(Exception, match="parent issue 5"):
            await connector._files_under_parent({"project": {"id": "10"}}, "5", security)

    async def test_a_parent_the_account_cannot_see_is_not_a_parent(self) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=404))
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        assert await connector._files_under_parent({"project": {"id": "10"}}, "5", security) is False


def _issue(iid: str, key: str, *, level: str | None = None) -> dict:
    return {"id": iid, "key": key, "fields": {
        "summary": key, "status": {"name": "Open"}, "priority": {"name": "High"},
        "issuetype": {"name": "Task", "subtask": False}, "project": {"id": "10", "key": "P"},
        "created": "2024-01-01T00:00:00.000+0000", "updated": "2024-01-02T00:00:00.000+0000",
        "reporter": None, "assignee": None, "creator": None, "attachment": [],
        "security": {"id": level} if level else None}}


class TestCostPerIssue:
    async def test_the_users_are_aliased_once_for_a_batch_not_once_per_issue(self) -> None:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "a-key", "user": {"key": "a-key"}}]})
        users = [_user(f"u{n}", f"u{n}@example.com") for n in range(50)]
        calls: list[int] = []
        real = connector._users_with_usernames
        connector._users_with_usernames = lambda by_key: (calls.append(len(by_key)), real(by_key))[1]

        built = await connector._build_issue_records(
            [_issue(str(n), f"P-{n}", level="1" if n % 2 else None) for n in range(1, 21)], "10", users)

        assert len([r for r, _ in built if isinstance(r, TicketRecord)]) == 20
        assert calls == [50], "one pass over the users for the batch of twenty issues"


class TestGroupHolders:
    def test_a_group_holder_is_the_group_named_in_parameter_as_the_permission_scheme_reads_it(self) -> None:
        rows = [{"issueSecurityLevelId": "1", "holder": {"type": "group", "parameter": "devs", "value": "gid-7"}},
                {"issueSecurityLevelId": "1", "holder": {"type": "projectLead"}}]
        fixed = JiraDataCenterConnector._groups_by_name(rows)
        assert fixed[0]["holder"]["value"] == "devs" and fixed[1] == rows[1]
        assert rows[0]["holder"]["value"] == "gid-7", "the server's rows are not changed"


class TestSkippedHoldersAreReportedOncePerLevel:
    async def test_a_holder_with_no_e_mail_is_one_warning_not_one_per_issue(self, caplog) -> None:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "ghost", "user": {"name": "ghost"}}]})
        with caplog.at_level(logging.WARNING, logger="t"):
            await connector._build_issue_records([_issue(str(n), f"P-{n}", level="1") for n in range(1, 6)], "10", [])
        assert len([r for r in caplog.records if "were not granted" in r.getMessage()]) == 1


class TestParentKeyLookup:
    async def test_the_key_of_a_parent_comes_from_the_reverse_cache_not_a_scan(self) -> None:
        class NoScan(dict):
            def items(self):  # noqa: ANN202
                raise AssertionError("the key-to-id cache must not be scanned")

        connector = _connector()
        connector._issue_key_to_id_cache = NoScan({"P-5": "5"})
        connector._issue_id_to_key_cache = {"5": "P-5"}
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert await connector._files_under_parent({"project": {"id": "10", "key": "P"}}, "5", none) is True

    async def test_both_caches_are_filled_together(self) -> None:
        connector = _connector()
        assert await connector._resolve_issue_id_from_key_or_id(issue_id="7", issue_key="P-7", context="x") == "7"
        assert connector._issue_id_to_key_cache == {"7": "P-7"}
        connector._get_fresh_datasource = AsyncMock(return_value=MagicMock(get_issue_v2=AsyncMock(
            return_value=MagicMock(status=200, json=MagicMock(return_value={"id": "8"})))))
        assert await connector._resolve_issue_id_from_key_or_id(issue_id=None, issue_key="P-8", context="x") == "8"
        assert connector._issue_id_to_key_cache["8"] == "P-8"


class _KeptSyncPoints:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict) -> None:
        self.points[key] = dict(data)


HOUR_MS = 60 * 60 * 1000


async def _roles_at(points: _KeptSyncPoints, now_ms: int, *, roles: dict | None) -> JiraDataCenterConnector:
    connector = _connector()
    connector.issues_sync_point = points
    connector.notify = AsyncMock()
    connector._app_roles_forbidden, connector._app_roles_mapping = roles is None, roles or {}
    with patch("app.connectors.sources.atlassian.jira_data_center.connector.get_epoch_timestamp_in_ms",
               return_value=now_ms):
        await connector._read_level_roles()
    return connector


class TestRolesKeptForLevels:
    """applicationrole answers 403 to an account that is not an administrator: the last role list read is applied for
    a bounded time, so a level's other holders still follow Jira on the issues' copies."""
    ROLES = {"jira-software": [{"groupId": "g-1"}]}
    LEVEL = [{"type": "applicationRole", "parameter": "jira-software"},
             {"type": "group", "value": "g-2"}, {"type": "group", "value": "g-3"}]

    @staticmethod
    def _copy(connector: JiraDataCenterConnector, members: list, stored: dict) -> tuple[tuple, list[str]]:
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True, members_by_level={"1": members})
        plan = plan_level_reread(security, stored, is_new_project=False, app_roles_mapping=connector._level_roles)
        grants, _ = apply_issue_access(_ticket(), {"security": {"id": "1"}}, is_subtask=False, project_key="P",
                                       context=security, app_roles_mapping=connector._level_roles)
        return plan[:2], sorted(g.external_id for g in grants)

    def _stored(self) -> dict:
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True, members_by_level={"1": self.LEVEL})
        return security.level_fingerprints(self.ROLES)

    @pytest.mark.asyncio
    async def test_a_holder_removed_while_the_roles_are_refused_is_removed_and_the_roles_groups_stay(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        connector = await _roles_at(points, HOUR_MS, roles=None)
        plan, grants = self._copy(connector, self.LEVEL[:2], self._stored())
        assert (plan, grants) == (("levels", ["1"]), ["g-1", "g-2"])

    @pytest.mark.asyncio
    async def test_past_the_bound_the_roles_groups_are_dropped(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        connector = await _roles_at(points, 25 * HOUR_MS, roles=None)
        plan, grants = self._copy(connector, self.LEVEL, self._stored())
        assert (plan, grants) == (("levels", ["1"]), ["g-2", "g-3"])
        connector.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_role_group_change_during_the_refusal_does_not_outlast_the_bound(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        assert (await _roles_at(points, 23 * HOUR_MS, roles=None))._level_roles == self.ROLES
        assert (await _roles_at(points, 25 * HOUR_MS, roles=None))._level_roles == {}

    @pytest.mark.asyncio
    async def test_past_the_bound_an_unread_role_list_drops_the_roles_groups(self) -> None:
        points = _KeptSyncPoints()
        await _roles_at(points, 0, roles=self.ROLES)
        connector = await _roles_at(points, 25 * HOUR_MS, roles=None)
        connector._app_roles_forbidden, connector._app_roles_unread = False, True
        await connector._read_level_roles()
        plan, grants = self._copy(connector, self.LEVEL[:2], self._stored())
        assert (plan, grants) == (("levels", ["1"]), ["g-2"])


class TestIssuesNamingNewAccounts:
    @pytest.mark.parametrize(("status", "whole_project"), [(400, True), (503, None)])
    async def test_a_refused_jql_reads_the_whole_project_and_a_failed_one_fails_the_walk(
        self, status: int, whole_project: bool | None,
    ) -> None:
        from app.connectors.sources.atlassian.core.read_outcome import JiraReadError

        connector = _connector()
        connector._dc_name_to_source_id = {"carol": "carol-key", "bob": "bob-key"}
        connector._accounts_to_hand = {"carol-key"}
        clauses: list[str] = []

        async def search(*_: object, extra_jql: str, **__: object) -> AsyncIterator[tuple[list, bool, None]]:
            clauses.append(extra_jql)
            raise JiraReadError(status, "no")
            yield

        connector._fetch_issues_batched = search
        connector._update_project_sync_checkpoint = AsyncMock()
        holders = [{"type": "assignee"}, {"type": "userCustomField", "parameter": "customfield_20000"},
                   {"type": "groupCustomField", "parameter": "customfield_20001"}]
        if whole_project is None:
            with pytest.raises(JiraReadError):
                await connector._reread_issues_naming("P", "10", [], {}, holders)
        else:
            assert await connector._reread_issues_naming("P", "10", [], {}, holders) is True
            assert connector._reread_whole_project is True
            # Marked before the whole-project read that replaces the refused one, which may fail too.
            connector._update_project_sync_checkpoint.assert_awaited_once_with("P", browse_holders="?")
        assert clauses == ['(assignee in ("carol") OR cf[20000] in ("carol"))']


class TestWithdrawnAccounts:
    """An account the complete active-user list no longer shows loses the connector and its own grants; an
    issue's level grants name only listed accounts; an account listed again has the leveled issues read again."""

    @staticmethod
    def _stored(c: JiraDataCenterConnector) -> list[AppUser]:
        c._stored_app_users = [_user("gone", "g@x.com"), _user("kept", "k@x.com"), _user("moved", "m@x.com")]
        c._listed_user_keys = {"kept", "moved-new"}
        c.data_entities_processor.withdraw_app_user_access = AsyncMock()
        c.hold = {}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: c.hold.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: c.hold.__setitem__(k, v))
        return c._stored_app_users

    @pytest.mark.parametrize("listed", [{"gone", "kept", "moved-new"}, {"kept", "moved-new"}],
                             ids=["none_missing", "one_missing"])
    async def test_a_complete_read_with_few_or_none_missing_clears_a_held_set(self, listed: set) -> None:
        c = _connector()
        self._stored(c)
        c.hold["withdrawals_held"] = {"accounts": '["a", "b"]', "seen": 2, "since": 1}
        c._listed_user_keys = c._seen_user_keys = listed
        await c._withdraw_unlisted_app_users([])
        assert c.hold["withdrawals_held"]["seen"] == 0

    async def test_a_read_not_complete_leaves_a_held_set_alone(self) -> None:
        c = _connector()
        self._stored(c)
        c.hold["withdrawals_held"] = held = {"accounts": '["a", "b"]', "seen": 2, "since": 1}
        c._user_read_unfinished = True
        await c._withdraw_unlisted_app_users([])
        assert c.hold["withdrawals_held"] is held

    async def test_an_unlisted_account_is_withdrawn_with_its_grants(self) -> None:
        c = _connector()
        stored = self._stored(c)
        await c._withdraw_unlisted_app_users([_user("kept", "k@x.com"), _user("moved-new", "m@x.com")])
        c.data_entities_processor.withdraw_app_user_access.assert_awaited_once_with("c", [stored[0]], drop_grants=True)
        assert c._stored_app_users == stored[1:], "the withdrawn no longer count as linked this sync"

    async def test_an_account_without_product_access_is_withdrawn_with_its_grants(self) -> None:
        """Still on the active user list, but in no product group: like an unlisted account it loses the connector and
        its own grants, and no longer counts as linked this sync, where a list cut short would grant it again."""
        c = _connector()
        stored = self._stored(c)
        c._licensed_keys = {"kept", "moved"}
        await c._withdraw_ineligible_app_users(stored[1:])
        c.data_entities_processor.withdraw_app_user_access.assert_awaited_once_with("c", [stored[0]], drop_grants=True)
        assert c._stored_app_users == stored[1:], "the withdrawn no longer count as linked this sync"

    async def test_an_account_with_a_hidden_email_and_no_product_access_is_withdrawn_and_never_admitted(self) -> None:
        """frank and gina both have a hidden email; only gina has product access. The empty email they share keeps
        nobody: frank is withdrawn, so the directory never holds him and no re-read is queued for him."""
        c = _connector()
        frank, gina = _user("frank-key", ""), _user("gina-key", "")
        c._stored_app_users = [frank, gina]
        c._listed_user_keys = c._seen_user_keys = {"frank-key", "gina-key"}
        c._licensed_keys = {"gina-key"}
        c.data_entities_processor.withdraw_app_user_access = AsyncMock()
        await c._withdraw_ineligible_app_users([])
        c.data_entities_processor.withdraw_app_user_access.assert_awaited_once_with("c", [frank], drop_grants=True)
        assert [u.source_user_id for u in c._directory_extra()] == ["gina-key"]

    @pytest.mark.parametrize("flag", ["_user_bulk_incomplete", "_user_bulk_refused"])
    async def test_a_deactivated_row_from_the_search_fallback_or_a_refused_list_withdraws_nobody(self, flag: str) -> None:
        """A release without /user/list reads users through the search fallback; neither it nor a refused list is
        the user list the withdrawal is checked against."""
        c = _connector()
        self._stored(c)
        c._seen_user_keys = {"gone"}  # shown deactivated
        setattr(c, flag, True)
        await c._withdraw_unlisted_app_users([])
        c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()

    async def test_the_user_list_is_asked_only_for_documented_parameters(self) -> None:
        """GET /rest/api/2/user/list documents cursor and maxResults only."""
        c = _connector()
        ds = MagicMock(get_user_list_v2=AsyncMock(return_value=MagicMock(
            status=200, json=MagicMock(return_value={"values": [], "isLast": True}))))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._fetch_users_via_list()
        assert set(ds.get_user_list_v2.await_args.kwargs) == {"cursor", "maxResults"}

    @pytest.mark.parametrize("shown_inactive", [False, True], ids=["missing", "shown_deactivated"])
    async def test_many_accounts_missing_from_a_complete_list_wait_for_three_reads(self, shown_inactive: bool) -> None:
        c = _connector()
        store: dict = {}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        c.notify = AsyncMock()
        c.data_entities_processor.withdraw_app_user_access = AsyncMock()
        c._stored_app_users = [_user(f"k{i}", f"u{i}@x.com") for i in range(40)]
        c._listed_user_keys = {f"k{i}" for i in range(11, 40)}
        c._seen_user_keys = {f"k{i}" for i in range(40 if shown_inactive else 11, 40)} | (
            {f"k{i}" for i in range(11)} if shown_inactive else set())
        if shown_inactive:  # rows that show the accounts deactivated: evidence whatever the count
            await c._withdraw_unlisted_app_users([])
            assert len(c.data_entities_processor.withdraw_app_user_access.await_args.args[1]) == 11
            return
        for _ in range(2):
            await c._withdraw_unlisted_app_users([])
        c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
        c.notify.assert_awaited_once()
        await c._withdraw_unlisted_app_users([])
        assert len(c.data_entities_processor.withdraw_app_user_access.await_args.args[1]) == 11

    @pytest.mark.parametrize("flag", ["_user_read_unfinished", "_user_bulk_forbidden", "_user_bulk_incomplete"])
    async def test_a_list_that_is_not_complete_withdraws_nobody(self, flag: str) -> None:
        c = _connector()
        self._stored(c)
        setattr(c, flag, True)
        await c._withdraw_unlisted_app_users([])
        c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()

    async def test_an_empty_list_when_accounts_were_linked_withdraws_nobody(self) -> None:
        c = _connector()
        self._stored(c)
        c._listed_user_keys = set()
        await c._withdraw_unlisted_app_users([])
        c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()

    @pytest.mark.parametrize("flag", ["_user_read_unfinished", "_user_bulk_forbidden", "_user_bulk_refused",
                                      "_user_bulk_incomplete"])
    def test_a_directory_not_read_in_full_keeps_every_level_grant(self, flag: str) -> None:
        """Of the level's members, a linked account the read missed keeps its grant; an account no longer linked
        (withdrawn) that only the payload names gets none."""
        c = _connector()
        c._listed_user_keys = {"alice-key"}
        c._stored_app_users = [_user("alice-key", "alice@example.com"), _user("gone", "unresolved@x.com")]
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [
                {"type": "user", "parameter": "alice-key", "user": {"key": "alice-key"}},
                {"type": "user", "parameter": "gone", "user": {"key": "gone", "emailAddress": "unresolved@x.com"}},
                {"type": "user", "parameter": "left", "user": {"key": "left", "emailAddress": "withdrawn@x.com"}},
            ]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"}}}
        setattr(c, flag, True)
        grants = c._secure_ancestor_stub(_ticket(is_placeholder=True), issue,
                                         {"alice-key": _user("alice-key", "alice@example.com")})
        assert sorted(g.email for g in grants) == ["alice@example.com", "unresolved@x.com"]

    def test_a_level_member_outside_the_directory_is_not_granted(self) -> None:
        c = _connector()
        c._listed_user_keys = {"alice-key"}
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [
                {"type": "user", "parameter": "alice-key", "user": {"key": "alice-key"}},
                {"type": "user", "parameter": "gone", "user": {"key": "gone", "emailAddress": "former@x.com"}},
            ]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"}}}
        grants = c._secure_ancestor_stub(_ticket(is_placeholder=True), issue,
                                         {"alice-key": _user("alice-key", "alice@example.com")})
        assert [g.email for g in grants] == ["alice@example.com"]

    def test_a_synced_account_whose_directory_entry_has_no_email_keeps_its_level_grant_by_account(self) -> None:
        c = _connector()
        c._listed_user_keys = {"bob-key"}
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "bob-key",
                                     "user": {"key": "bob-key", "emailAddress": "bob@example.com"}}]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"}}}
        grants = c._secure_ancestor_stub(_ticket(is_placeholder=True), issue, {"bob-key": _user("bob-key", "")})
        assert [(g.email, g.external_id) for g in grants] == [(None, "bob-key")]  # by account, reaching that user

    @pytest.mark.parametrize("listed", [True, False], ids=["listed", "not_listed"])
    def test_a_linked_account_stored_without_an_email_is_in_the_directory_a_sync_resolves_against(self, listed: bool) -> None:
        """The active-user list shows bob-key, linked but stored without an email, so the directory a sync builds does not
        hold it; the level names it with an email: it is granted by account when the list shows it active."""
        c = _connector()
        c._stored_app_users = [_user("bob-key", "")]
        c._listed_user_keys = {"alice-key"} | ({"bob-key"} if listed else set())  # a complete read, bob shown or not
        c._seen_user_keys = {"alice-key", "bob-key"}
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "bob-key",
                                     "user": {"key": "bob-key", "emailAddress": "bob@example.com"}}]},
        )
        users = c._grant_directory({})
        grants = c._directory_grants(
            [Permission(entity_type=EntityType.USER, external_id="bob-key", type=PermissionType.READ)], {})
        assert ("bob-key" in users) is listed and bool(grants) is listed

    def test_a_level_holder_named_by_username_resolves_to_a_linked_account_with_a_hidden_email(self) -> None:
        """The level names bob by username; bob is linked with a hidden email, so only the extra accounts hold him. The
        username aliases cover them too."""
        c = _connector()
        c._stored_app_users = [_user("bob-key", "")]
        c._listed_user_keys = {"alice-key", "bob-key"}
        c._seen_user_keys = {"alice-key", "bob-key"}
        c._dc_name_to_source_id = {"bob": "bob-key"}
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "bob", "user": {"name": "bob"}}]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"}}}
        grants = c._secure_ancestor_stub(_ticket(is_placeholder=True), issue, c._users_with_usernames({}))
        assert [(g.email, g.external_id) for g in grants] == [(None, "bob-key")]

    def test_a_synced_users_username_never_takes_the_key_of_a_linked_account(self) -> None:
        """Alice's username is ``bob-key``, the key of Bob, linked with a hidden email: the key resolves to Bob."""
        c = _connector()
        alice, bob = _user("alice-key", "alice@x.com"), _user("bob-key", "")
        c._stored_app_users = [bob]
        c._listed_user_keys = {"alice-key", "bob-key"}
        c._seen_user_keys = {"alice-key", "bob-key"}
        c._dc_name_to_source_id = {"bob-key": "alice-key"}
        directory = c._grant_directory(c._users_with_usernames({"alice-key": alice}))
        assert directory["bob-key"] is bob and directory["alice-key"] is alice

    def test_a_level_member_outside_the_directory_is_not_granted_through_an_email_a_synced_account_shares(self) -> None:
        """"left" has no access in Jira (not in the directory) but carries alice's email: alice must not get left's level."""
        c = _connector()
        c._listed_user_keys = {"alice-key"}
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [
                {"type": "user", "parameter": "left", "user": {"key": "left", "emailAddress": "alice@example.com"}},
                {"type": "reporter"},
            ]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"},
            "reporter": {"key": "left", "name": "left", "emailAddress": "alice@example.com"}}}
        grants = c._secure_ancestor_stub(_ticket(is_placeholder=True), issue,
                                         {"alice-key": _user("alice-key", "alice@example.com")})
        assert grants == []

    @pytest.mark.parametrize(("complete", "shown"), [(True, False), (False, False), (False, True)],
                             ids=["read_in_full", "not_read_in_full", "shown_deactivated_by_a_short_list"])
    async def test_a_linked_level_holder_the_list_missed_keeps_its_grant_and_an_unnamed_one_marks_a_re_read(
        self, complete: bool, shown: bool,
    ) -> None:
        """bob is linked but missing from a list cut short, and the level names him by key alone (no email); zed is
        named by a username no listed or linked account has. bob keeps his grant; zed's issues are read again."""
        c = _connector()
        c._listed_user_keys = {"alice-key"}
        c._stored_app_users = [_user("alice-key", "alice@example.com"), _user("bob-key", "bob@example.com")]
        c._user_read_unfinished = not complete
        c._seen_user_keys = {"alice-key"} | ({"bob-key"} if shown else set())
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "bob-key", "user": {"key": "bob-key"}},
                                    {"type": "user", "parameter": "zed", "user": {"name": "zed"}}]})
        c._save_levels_retry = AsyncMock()
        built = await c._build_issue_records([_issue("2", "P-2", level="1")], "10",
                                             [_user("alice-key", "alice@example.com")])
        grants = {p.email for r, perms in built if isinstance(r, TicketRecord) for p in perms}
        # Read in full, bob is gone from Jira (deactivated or removed) and is granted nothing.
        assert grants == (set() if complete or shown else {"bob@example.com"})
        assert c._holders_unresolved is (not complete)
        assert c._save_levels_retry.await_count == (0 if complete else 1), "saved before the issue is written"

    @pytest.mark.parametrize("flag", ["_user_bulk_incomplete", "_user_bulk_refused"])
    async def test_a_list_that_is_never_complete_does_not_mark_a_re_read_every_sync(self, flag: str) -> None:
        """The search fallback (capped on Jira 10+) and a refused list stay that way: a marker armed by them would
        re-read every leveled issue on every sync and still not name the holder."""
        c = _connector()
        c._listed_user_keys = {"alice-key"}
        c._stored_app_users = [_user("alice-key", "alice@example.com")]
        setattr(c, flag, True)
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "zed", "user": {"name": "zed"}}]})
        c._save_levels_retry = AsyncMock()
        await c._build_issue_records([_issue("2", "P-2", level="1")], "10", [_user("alice-key", "alice@example.com")])
        assert c._holders_unresolved is False
        c._save_levels_retry.assert_not_awaited()

    async def test_a_marker_that_cannot_be_saved_holds_the_issue_rather_than_write_it_short(self) -> None:
        c = _connector()
        c._listed_user_keys = {"alice-key"}
        c._stored_app_users = [_user("alice-key", "alice@example.com")]
        c._user_read_unfinished = True
        c._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "zed", "user": {"name": "zed"}}]})
        c._get_project_sync_checkpoint = AsyncMock(return_value={"security_levels": '{"1": "d"}'})
        c._update_project_sync_checkpoint = AsyncMock(side_effect=ConnectionError("store unavailable"))
        built = await c._build_issue_records([_issue("2", "P-2", level="1")], "10", [_user("alice-key", "alice@example.com")])
        assert not [r for r, _ in built if isinstance(r, TicketRecord)], "the issue waits for a sync that can save it"
        assert c._issue_build_failures == 1

    async def test_a_failed_read_of_the_linked_accounts_fails_the_user_read(self) -> None:
        """The linked accounts stand in for those a short user list missed; read as none after a failure, a level
        holder the list left out would lose the grant it has in Jira. The read fails instead."""
        c = _connector()
        c.data_source = MagicMock()

        async def get_all_app_users(connector_id: str, *, raise_on_error: bool = False) -> list:
            if raise_on_error:
                raise ConnectionError("graph unavailable")
            return []  # what every store answers for a failed read without the flag

        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=get_all_app_users)
        c.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        c._fetch_all_jira_users_bulk = AsyncMock(return_value=[{"key": "alice-key", "emailAddress": "a@x.com"}])
        with pytest.raises(ConnectionError):
            await c._fetch_users()

    async def test_newly_listed_accounts_are_saved_before_they_are_linked(self) -> None:
        c = _connector()
        store: dict = {}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        assert await c._note_people_to_reread({"carol-key"}) == {"carol-key"}
        assert store["people_reread"] == {"accounts": '["carol-key"]', "generation": 1, "base": 0}
        assert await c._note_people_to_reread(set()) == {"carol-key"}
        assert c._people_generation == 1, "a sync that admits no one is no new work"

    async def test_an_account_admitted_again_while_still_queued_is_a_new_generation(self) -> None:
        """carol was queued, then withdrawn (her grants dropped) and listed again before the queue emptied: a project
        that covered the earlier generation, and missed this sync, must still read its issues again for her."""
        c = _connector()
        store: dict = {"people_reread": {"accounts": '["carol-key"]', "generation": 4}}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        await c._note_people_to_reread({"carol-key"})
        assert c._people_generation == 5 and store["people_reread"]["generation"] == 5

    async def test_a_queue_saved_before_generations_is_owed_to_every_project(self) -> None:
        c = _connector()
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={"accounts": '["carol-key"]'})
        await c._note_people_to_reread(set())
        assert c._people_generation == 1  # above a project's missing people_read (0)

    @pytest.mark.parametrize("shown", [True, False], ids=["listed_again", "still_missing"])
    async def test_a_linked_account_the_list_missed_is_read_again_once_a_list_shows_it(self, shown: bool) -> None:
        """frank was missing from the user list (the search fallback), so he resolved by key alone and a level naming
        him by username did not reach him. A later list shows him: his username resolves, so the issues are read again."""
        c = _connector()
        store: dict = {"admitted_without_email": {"accounts": "[]", "key_only": '["frank-key"]'}}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        c._stored_app_users = [_user("frank-key", "frank@example.com")]
        c._user_bulk_incomplete = not shown
        c._listed_user_keys = c._seen_user_keys = {"alice-key"} | ({"frank-key"} if shown else set())
        pending = await c._note_newly_listed([_user("alice-key", "alice@example.com")])
        assert ("frank-key" in pending) is shown
        assert json.loads(store["admitted_without_email"]["key_only"]) == ([] if shown else ["frank-key"])

    async def test_a_failed_read_of_the_synced_users_skips_the_stub_sweep(self) -> None:
        c = _connector()
        stub = _ticket(is_placeholder=True, external_revision_id=None, record_group_type="PROJECT")
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=ConnectionError("graph unavailable"))
        c._fetch_ancestor_level = AsyncMock()
        assert await c._sweep_placeholder_records({"10"}, set()) is None, "a skip, not a sweep with nothing to do"
        c._fetch_ancestor_level.assert_not_awaited()

    async def test_linking_users_between_syncs_saves_their_re_read_first(self) -> None:
        c = _connector()
        c.data_source = MagicMock()
        order: list = []
        listed = [_user("alice-key", "a@x.com"), _user("carol-key", "c@x.com")]
        c._fetch_users = AsyncMock(return_value=listed)
        c._eligible_users = AsyncMock(side_effect=lambda users: users)  # both have Jira product access
        c._stored_app_users = [listed[0]]
        c._note_people_to_reread = AsyncMock(side_effect=lambda ids: order.append(("note", sorted(ids))))
        c.data_entities_processor.on_new_app_users = AsyncMock(side_effect=lambda _u: order.append(("link",)))
        c._sync_user_groups = AsyncMock()
        await c._link_users_and_groups()
        assert order == [("note", ["carol-key"]), ("link",)]


class TestSweepAncestorReads:
    """A stub whose ancestor could not be read (not "Jira has no such issue") is not swept: the sweep says so,
    and an account listed again stays owed until a sweep reads it."""

    @pytest.mark.parametrize(("status", "swept"), [(404, True), (403, False), (503, False)], ids=["gone", "403_undocumented", "unavailable"])
    async def test_a_failed_ancestor_read_reports_the_sweep_incomplete(self, status: int, swept: bool) -> None:
        c = _connector()
        stub = _ticket(is_placeholder=True, external_revision_id="1", record_group_type="PROJECT")
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        c._search_ancestors_by_jql = AsyncMock(return_value=None)
        c._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=status))
        result = await c._sweep_placeholder_records({"10"}, {"10"})
        assert (result is not None) is swept


class TestLevelsJiraLeavesOut:
    """Data Center leaves ``security`` out of an issue with no level, where Cloud returns null."""

    @staticmethod
    def _search(status: int, ids: list[str]) -> AsyncMock:
        body = {"issues": [{"id": i} for i in ids], "total": len(ids)}
        return AsyncMock(return_value=MagicMock(status=status, json=MagicMock(return_value=body)))

    async def test_an_issue_jql_finds_without_a_level_gets_an_explicit_null(self) -> None:
        connector = _connector()
        connector._search_issues_with_retry = self._search(200, ["1"])
        open_issue = {"id": "1", "fields": {"project": {"id": "10"}}}
        hidden_field = {"id": "2", "fields": {"project": {"id": "10"}}}
        leveled = {"id": "3", "fields": {"security": {"id": "7"}}}

        await connector._null_levels_jira_left_out([open_issue, hidden_field, leveled, None])

        jql = connector._search_issues_with_retry.await_args.kwargs["jql"]
        assert jql == "id in (1, 2) AND level IS EMPTY", "only the issues missing the key are asked about"
        assert open_issue["fields"]["security"] is None
        assert "security" not in hidden_field["fields"], "a key hidden by the field configuration stays missing"
        assert leveled["fields"]["security"] == {"id": "7"}

    @pytest.mark.parametrize("failure", ["http", "raise"])
    async def test_a_failed_check_is_returned_unsettled_with_the_key_still_missing(self, failure) -> None:
        connector = _connector()
        connector._search_issues_with_retry = (
            self._search(503, ["1"]) if failure == "http" else AsyncMock(side_effect=RuntimeError("down")))
        issue = {"id": "1", "fields": {}}
        assert await connector._null_levels_jira_left_out([issue]) == {"1"}
        assert "security" not in issue["fields"]

    async def test_a_batch_rejected_for_a_deleted_id_is_split_until_that_id_is_alone(self) -> None:
        """Jira answers 400 to ``id in (...)`` naming an id it no longer knows; the others are still settled."""
        async def search(**kw: object) -> MagicMock:
            ids = kw["jql"].split("(")[1].split(")")[0].split(", ")
            if "3" in ids:
                return MagicMock(status=400, json=MagicMock(return_value={}))
            body = {"issues": [{"id": i} for i in ids]}
            return MagicMock(status=200, json=MagicMock(return_value=body))

        connector = _connector()
        connector._search_issues_with_retry = AsyncMock(side_effect=search)
        issues = [{"id": i, "fields": {}} for i in ("1", "2", "3", "4")]
        assert await connector._null_levels_jira_left_out(issues) == set()
        assert [("security" in i["fields"]) for i in issues] == [True, True, False, True]

    async def test_a_failed_check_holds_the_page_rather_than_storing_its_issues_closed(self) -> None:
        """Stored closed under Jira's timestamp, the next sync would skip them and they would stay closed."""
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        issue = _issue("1", "P-1")
        del issue["fields"]["security"]
        page = MagicMock(status=200, json=MagicMock(return_value={"issues": [issue], "total": 1}))
        connector._search_issues_with_retry = AsyncMock(side_effect=[page, RuntimeError("down")])
        connector._build_issue_records = AsyncMock()
        connector.data_source = MagicMock()

        with pytest.raises(JiraReadError):
            async for _ in connector._fetch_issues_batched("P", "10", []):
                pass
        connector._build_issue_records.assert_not_awaited()

    async def test_a_parent_whose_level_check_failed_holds_its_child(self) -> None:
        connector = _connector()
        parent = {"id": "5", "fields": {"project": {"id": "10"}}}
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value=parent)))
        connector._search_issues_with_retry = AsyncMock(side_effect=RuntimeError("down"))
        with pytest.raises(JiraReadError):
            await connector._parent_level("5")
        assert "5" not in connector._parent_levels

    @pytest.mark.parametrize(("answer", "unsettled"), [({"total": 1}, {"1"}), ({}, {"1"}), ({"total": 0}, set())],
                             ids=["matches but no list", "nothing", "no match"])
    async def test_an_answer_without_an_issues_list_settles_only_a_zero_match(self, answer, unsettled) -> None:
        """SearchResultsBean declares issues but does not require it: a zero total is an answer (none of them has no
        level, so the key stays missing and the issue closed); anything else without the list is not."""
        connector = _connector()
        connector._search_issues_with_retry = AsyncMock(
            return_value=MagicMock(status=200, json=MagicMock(return_value=answer)))
        issue = {"id": "1", "fields": {}}
        assert await connector._null_levels_jira_left_out([issue]) == unsettled
        assert "security" not in issue["fields"]

    async def test_nothing_is_asked_when_every_issue_carries_the_key(self) -> None:
        connector = _connector()
        connector._search_issues_with_retry = AsyncMock()
        await connector._null_levels_jira_left_out([{"id": "1", "fields": {"security": None}}])
        connector._search_issues_with_retry.assert_not_awaited()

    async def test_a_parent_with_no_level_is_read_as_one_not_failed(self) -> None:
        connector = _connector()
        parent = {"id": "5", "fields": {"project": {"id": "10"}}}
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value=parent)))
        connector._search_issues_with_retry = self._search(200, ["5"])
        assert await connector._parent_level("5") == ("10", None)

    async def test_a_no_level_issue_in_a_project_with_a_scheme_follows_the_project(self) -> None:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        issue = _issue("1", "P-1")
        del issue["fields"]["security"]
        connector._search_issues_with_retry = self._search(200, ["1"])
        await connector._null_levels_jira_left_out([issue])

        built = await connector._build_issue_records([issue], "10", [])

        record = next(r for r, _ in built if isinstance(r, TicketRecord))
        assert record.access_rule == AccessRule.STRICT, "readable by the project, not hidden from everyone"


class TestSecuredSubtasks:
    async def test_a_sub_task_whose_level_names_its_assignee_hangs_off_the_project(self) -> None:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "group", "parameter": "admins"}, {"type": "assignee"}]})
        sub = _issue("2", "P-2", level="1")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True}
        sub["fields"]["parent"] = {"id": "1", "key": "P-1"}
        sub["fields"]["assignee"] = {"key": "vansh-key", "name": "vansh"}

        built = await connector._build_issue_records([sub], "10", [_user("vansh-key", "vansh@example.com")])

        record, grants = next((r, g) for r, g in built if isinstance(r, TicketRecord))
        assert record.parent_external_record_id is None
        assert record.access_rule == AccessRule.RESTRICTED
        assert "vansh@example.com" in {g.email for g in grants}

    @staticmethod
    async def _placed(parent_level: str | None) -> TicketRecord:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        sub = _issue("2", "P-2")
        sub["fields"]["issuetype"] = {"name": "Sub-task", "subtask": True}
        sub["fields"]["parent"] = {"id": "1", "key": "P-1"}
        built = await connector._build_issue_records([_issue("1", "P-1", level=parent_level), sub], "10", [])
        return next(r for r, _ in built if isinstance(r, TicketRecord) and r.external_record_id == "2")

    async def test_a_sub_task_with_no_level_stays_under_a_parent_with_none(self) -> None:
        assert (await self._placed(None)).parent_external_record_id == "1"

    async def test_a_sub_task_read_with_no_level_stays_under_a_leveled_parent(self) -> None:
        """Jira gives a sub-task its parent's level, so a null is a payload behind it, never read wider than the parent."""
        assert (await self._placed("7")).parent_external_record_id == "1"



class TestGroupsAreKeyedByName:
    """Data Center names a group by its name alone (``/groups/picker`` lists ``name``, ``html`` and ``labels``; members
    are read by ``groupname``). A picker row that also carries an id equal to another group's name must not put its
    members under that other group."""

    async def test_a_group_whose_id_is_another_groups_name_does_not_take_its_members(self) -> None:
        c = _connector()
        c.data_source = MagicMock()
        ds = MagicMock()
        ds.groups_picker_get_v2 = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value={
            "groups": [{"name": "beta"}, {"name": "alpha", "groupId": "beta"}], "total": 2})))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        alice, bob = _user("alice-key", "alice@x.com"), _user("bob-key", "bob@x.com")
        c._fetch_group_members = AsyncMock(side_effect=lambda _gid, name: {"beta": ["bob-key"], "alpha": ["alice-key"]}[name])
        c.data_entities_processor.on_new_user_groups = AsyncMock()
        result = await c._sync_user_groups([alice, bob])
        assert result.members == {"beta": [bob], "alpha": [alice]}
        written = {g.source_user_group_id: m for (g, m), in (call.args[0] for call in
                                                           c.data_entities_processor.on_new_user_groups.await_args_list)}
        assert written == {"beta": [bob], "alpha": [alice]}


class TestStoredNoLevelIssuesHealOnce:
    """Issues an earlier version stored closed for a missing ``security`` key are read again once."""

    @staticmethod
    async def _sync(checkpoint: dict, *, pass_fails: bool = False) -> tuple[AsyncMock, AsyncMock]:
        connector = _connector()
        context = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True,
                                       members_by_level={"7": [{"type": "group", "parameter": "admins"}]})
        connector._load_issue_security = AsyncMock(return_value=context)
        connector._get_project_sync_checkpoint = AsyncMock(return_value={
            "last_sync_time": 100, "last_issue_updated": 200,
            "security_levels": json.dumps(context.level_fingerprints(), sort_keys=True), **checkpoint})

        async def no_changes(*_a: object, **_kw: object) -> AsyncIterator[None]:
            return
            yield  # pragma: no cover - makes an async generator

        async def reread(*_a, **_kw) -> None:
            if pass_fails:
                connector._issue_build_failures += 1

        connector._fetch_issues_batched = no_changes
        connector._reread_stored_issues = AsyncMock(side_effect=reread)
        connector._update_project_sync_checkpoint = AsyncMock()
        await connector._sync_project_issues(MagicMock(short_name="P", external_group_id="10"), [], None)
        return connector._reread_stored_issues, connector._update_project_sync_checkpoint

    @staticmethod
    def _marked(update: AsyncMock) -> bool:
        return any(c.kwargs.get("no_level_read") == NO_LEVEL_READ for c in update.await_args_list)

    async def test_a_project_synced_before_reads_every_stored_issue_once_and_marks_it(self) -> None:
        """Unchanged at source, they would never reach the incremental pass."""
        reread, update = await self._sync({})
        reread.assert_awaited_once()
        assert reread.await_args.kwargs["every_issue"] is True
        assert self._marked(update)

    async def test_a_marked_project_is_not_read_again(self) -> None:
        reread, _ = await self._sync({"no_level_read": NO_LEVEL_READ})
        reread.assert_not_awaited()

    async def test_a_failed_pass_is_not_marked_so_the_next_sync_repeats_it(self) -> None:
        reread, update = await self._sync({}, pass_fails=True)
        reread.assert_awaited_once()
        assert not self._marked(update)

"""Jira Data Center issue security: the parts of the connector that are not a whole sync."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
)
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.models.entities import AppUser, RecordType, TicketRecord


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


class TestStoredGrantsKept:
    """A leveled issue whose level could not be read yet keeps the grants it has: a full sync's first
    write of an existing record would otherwise replace them with none."""

    async def test_a_closed_stored_issue_without_grants_goes_through_the_content_update(self) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = _ticket(is_placeholder=False)
        record = _ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=False)

        await connector._process_new_records([(record, [])], "P", {"new_count": 0, "updated_count": 0})

        connector.data_entities_processor.on_record_content_update.assert_awaited_once_with(record)
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


class TestWithdrawnAccounts:
    """An account the complete active-user list no longer shows loses the connector and its own grants; an
    issue's level grants name only listed accounts; an account listed again has the leveled issues read again."""

    @staticmethod
    def _stored(c: JiraDataCenterConnector) -> list[AppUser]:
        c._stored_app_users = [_user("gone", "g@x.com"), _user("kept", "k@x.com"), _user("moved", "m@x.com")]
        c._listed_user_keys = {"kept", "moved-new"}
        c.data_entities_processor.withdraw_app_user_access = AsyncMock()
        return c._stored_app_users

    async def test_an_unlisted_account_is_withdrawn_with_its_grants(self) -> None:
        c = _connector()
        stored = self._stored(c)
        await c._withdraw_unlisted_app_users([_user("kept", "k@x.com"), _user("moved-new", "m@x.com")])
        c.data_entities_processor.withdraw_app_user_access.assert_awaited_once_with("c", [stored[0]], drop_grants=True)
        assert c._stored_app_users == stored[1:], "the withdrawn no longer count as linked this sync"

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

    async def test_newly_listed_accounts_are_saved_before_they_are_linked(self) -> None:
        c = _connector()
        store: dict = {}
        c.issues_sync_point = MagicMock()
        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=lambda k: store.get(k))
        c.issues_sync_point.update_sync_point = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        assert await c._note_people_to_reread({"carol-key"}) == {"carol-key"}
        assert store["people_reread"] == {"accounts": '["carol-key"]'}
        assert await c._note_people_to_reread(set()) == {"carol-key"}

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
        c._stored_app_users = [listed[0]]
        c._note_people_to_reread = AsyncMock(side_effect=lambda ids: order.append(("note", sorted(ids))))
        c.data_entities_processor.on_new_app_users = AsyncMock(side_effect=lambda _u: order.append(("link",)))
        c._sync_user_groups = AsyncMock()
        await c._link_users_and_groups()
        assert order == [("note", ["carol-key"]), ("link",)]


class TestSweepAncestorReads:
    """A stub whose ancestor could not be read (not "Jira has no such issue") is not swept: the sweep says so,
    and an account listed again stays owed until a sweep reads it."""

    @pytest.mark.parametrize(("status", "swept"), [(404, True), (503, False)], ids=["gone", "unavailable"])
    async def test_a_failed_ancestor_read_reports_the_sweep_incomplete(self, status: int, swept: bool) -> None:
        c = _connector()
        stub = _ticket(is_placeholder=True, external_revision_id="1", record_group_type="PROJECT")
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        c._search_ancestors_by_jql = AsyncMock(return_value=None)
        c._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=status))
        result = await c._sweep_placeholder_records({"10"}, {"10"})
        assert (result is not None) is swept

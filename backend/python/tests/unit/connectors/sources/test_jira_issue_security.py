"""Issue security decisions for Jira Cloud and Data Center team connectors."""

from collections.abc import Awaitable, Callable

import pytest

from app.config.constants.arangodb import AccessRule, Connectors, OriginTypes
from app.connectors.sources.atlassian.core.jira_issue_security import (
    UNREADABLE_AUTH,
    UNREADABLE_KEEPS_STORED_ACCESS,
    UNREADABLE_NOT_LISTED,
    UNREADABLE_OTHER,
    UNREADABLE_PERMISSION,
    UNREADABLE_REASONS,
    UNREADABLE_ROLES,
    UNREADABLE_SCOPE,
    UNREADABLE_TRANSIENT,
    UNREADABLE_UNCLEAR_404,
    IssueSecurityContext,
    SchemeKnowledge,
    apply_attachment_access,
    apply_issue_access,
    apply_issue_access_settled,
    classify_project_mapping,
    classify_scheme_response,
    collect_paged_values,
    hold_levels_naming_unread_roles,
    level_reads_alike,
    levels_after_walk,
    plan_level_reread,
    pre_walk_marker,
    refuses_a_scope,
    resolve_scheme_knowledge,
    scheme_has_no_levels,
    unreadable_reason,
    unreadable_security_notices,
)
from app.models.entities import Record, RecordType
from app.models.permission import EntityType


def _record(**overrides) -> Record:
    fields = {
        "record_name": "[P-1] ticket",
        "record_type": RecordType.TICKET,
        "external_record_id": "100",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.JIRA,
        "connector_id": "conn",
        "org_id": "org",
    }
    fields.update(overrides)
    return Record(**fields)


def _present(scheme_id: str = "9", members=None, readable: bool = True) -> IssueSecurityContext:
    return IssueSecurityContext(
        knowledge=SchemeKnowledge.PRESENT,
        scheme_id=scheme_id,
        members_by_level=members or {},
        members_readable=readable,
    )


class TestSchemeDetection:
    def test_200_with_id_is_a_scheme(self) -> None:
        knowledge, scheme_id = classify_scheme_response(200, {"id": 9, "name": "Sec"})
        assert knowledge == SchemeKnowledge.PRESENT
        assert scheme_id == "9"

    def test_200_without_id_is_not_no_scheme(self) -> None:
        assert classify_scheme_response(200, {})[0] == SchemeKnowledge.UNKNOWN

    def test_404_and_403_are_not_no_scheme(self) -> None:
        assert classify_scheme_response(404, {"errorMessages": ["gone"]})[0] == SchemeKnowledge.UNKNOWN
        assert classify_scheme_response(404, None)[0] == SchemeKnowledge.UNKNOWN
        assert classify_scheme_response(403, None)[0] == SchemeKnowledge.UNKNOWN

    def test_jira_no_scheme_404_is_no_scheme_for_a_classic_project(self) -> None:
        knowledge, scheme_id = classify_scheme_response(
            404,
            {"errorMessages": ["Security level for project 14607 does not exist."]},
            project_style="classic",
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_jira_no_scheme_404_does_not_decide_a_next_gen_project(self) -> None:
        knowledge, _scheme_id = classify_scheme_response(
            404,
            {"errorMessages": ["Security level for project 14607 does not exist."]},
            project_style="next-gen",
        )
        assert knowledge == SchemeKnowledge.UNKNOWN

    def test_direct_no_scheme_wins_over_an_unreadable_mapping(self) -> None:
        knowledge, scheme_id = resolve_scheme_knowledge(
            (SchemeKnowledge.NONE, None),
            (SchemeKnowledge.UNKNOWN, None),
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_completed_mapping_without_a_classic_project_is_no_scheme(self) -> None:
        knowledge, scheme_id = classify_project_mapping(
            "100",
            [{"projectId": "200", "issueSecuritySchemeId": "9"}],
            complete=True,
            http_status=200,
            project_style="classic",
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_classic_row_without_a_scheme_id_is_no_scheme(self) -> None:
        knowledge, scheme_id = classify_project_mapping(
            "14607",
            [{"projectId": "14607"}],
            complete=True,
            http_status=200,
            project_style="classic",
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_next_gen_row_without_a_scheme_id_is_not_no_scheme(self) -> None:
        knowledge, _scheme_id = classify_project_mapping(
            "14607",
            [{"projectId": "14607"}],
            complete=True,
            http_status=200,
            project_style="next-gen",
        )
        assert knowledge == SchemeKnowledge.UNKNOWN

    def test_mapping_hit_is_a_scheme(self) -> None:
        knowledge, scheme_id = classify_project_mapping(
            "100",
            [{"projectId": "100", "issueSecuritySchemeId": "9"}],
            complete=True,
            http_status=200,
            project_style="classic",
        )
        assert (knowledge, scheme_id) == (SchemeKnowledge.PRESENT, "9")

    def test_next_gen_absent_from_the_classic_mapping_is_not_no_scheme(self) -> None:
        knowledge, _scheme_id = classify_project_mapping(
            "100",
            [],
            complete=True,
            http_status=200,
            project_style="next-gen",
        )
        assert knowledge == SchemeKnowledge.UNKNOWN

    def test_incomplete_or_forbidden_mapping_is_not_no_scheme(self) -> None:
        assert classify_project_mapping(
            "100", [], complete=False, http_status=200, project_style="classic",
        )[0] == SchemeKnowledge.UNKNOWN
        assert classify_project_mapping(
            "100", None, complete=False, http_status=403, project_style="classic",
        )[0] == SchemeKnowledge.UNKNOWN


class TestIssueAccess:
    def test_no_scheme_null_security_follows_the_project(self) -> None:
        record = _record()
        permissions, _skipped = apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=IssueSecurityContext(knowledge=SchemeKnowledge.NONE),
        )
        assert permissions == []
        assert record.access_rule == AccessRule.STRICT
        assert record.rewrite_permissions is True
        assert record.inherit_permissions_from_group is False

    def test_null_security_in_a_scheme_is_not_the_default_level(self) -> None:
        record = _record()
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
        )
        assert record.access_rule == AccessRule.STRICT
        assert record.rewrite_permissions is True

    def test_missing_security_field_does_not_open_the_issue(self) -> None:
        record = _record()
        permissions, _skipped = apply_issue_access(
            record,
            {"summary": "x"},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
        )
        assert permissions == []
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.rewrite_permissions is True

    def test_unknown_scheme_hides_a_leveled_issue_and_opens_a_null_level_one(self) -> None:
        ctx = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True)
        record = _record()
        apply_issue_access(record, {"security": None}, is_subtask=False, project_key="PROJ", context=ctx)
        assert record.access_rule == AccessRule.STRICT  # a null level is a positive "no level"
        record = _record()
        permissions, _skipped = apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True),
        )
        assert permissions == []
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.rewrite_permissions is True

    def test_transient_unknown_scheme_keeps_stored_grants(self) -> None:
        record = _record()
        apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=IssueSecurityContext(
                knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True,
            ),
        )
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.rewrite_permissions is False

    def test_level_members_are_direct_grants_and_not_an_org_grant(self) -> None:
        record = _record()
        permissions, skipped = apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={
                "20": [
                    {"type": "group", "value": "group-1"},
                    {"type": "user", "parameter": "acc-1", "user": {"accountId": "acc-1", "active": True}},
                    {"type": "projectRole", "parameter": "100", "projectRole": {"id": "100", "name": "Developers"}},
                    {"type": "reporter"},
                    {"type": "assignee"},
                    {"type": "projectLead"},
                    {"type": "applicationRole", "parameter": "jira-software"},
                    {"type": "user", "user": {"accountId": "gone", "active": False, "emailAddress": "old@ex.com"}},
                ],
            }),
            user_by_account_id={"acc-1": type("U", (), {"email": "mia@ex.com"})()},
            app_roles_mapping={"jira-software": [{"groupId": "sw-users"}]},
            reporter_email="rep@ex.com",
            assignee_email="asg@ex.com",
        )
        kinds = {(p.entity_type, p.external_id, p.email) for p in permissions}
        assert (EntityType.GROUP, "group-1", None) in kinds
        assert (EntityType.USER, None, "mia@ex.com") in kinds
        assert (EntityType.ROLE, "PROJ_100", None) in kinds
        assert (EntityType.USER, None, "rep@ex.com") in kinds
        assert (EntityType.USER, None, "asg@ex.com") in kinds
        assert (EntityType.ROLE, "PROJ_projectLead", None) in kinds
        assert (EntityType.GROUP, "sw-users", None) in kinds
        assert (EntityType.USER, None, "old@ex.com") not in kinds
        assert all(p.entity_type != EntityType.ORG for p in permissions)
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.rewrite_permissions is True
        assert skipped == []

    def test_unreadable_custom_field_hides_the_issue(self) -> None:
        record = _record()
        permissions, _skipped = apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={
                "20": [{"type": "userCustomField", "parameter": "customfield_10001"}],
            }),
        )
        assert permissions == []
        assert record.rewrite_permissions is True
        assert record.access_rule == AccessRule.RESTRICTED

    def test_empty_user_picker_writes_no_user_and_replaces(self) -> None:
        record = _record()
        permissions, _skipped = apply_issue_access(
            record,
            {"security": {"id": "20"}, "customfield_10001": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={
                "20": [{"type": "userCustomField", "parameter": "customfield_10001"}],
            }),
        )
        assert permissions == []
        assert record.rewrite_permissions is True
        assert record.access_rule == AccessRule.RESTRICTED

    def test_members_unreadable_hides_a_secured_issue(self) -> None:
        record = _record()
        apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(readable=False),
        )
        assert record.rewrite_permissions is True
        assert record.access_rule == AccessRule.RESTRICTED

    def test_transient_unreadable_members_keep_stored_grants(self) -> None:
        record = _record()
        apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=IssueSecurityContext(
                knowledge=SchemeKnowledge.PRESENT,
                scheme_id="9",
                members_readable=False,
                defer_checkpoint=True,
            ),
        )
        assert record.rewrite_permissions is False
        assert record.access_rule == AccessRule.RESTRICTED

    def test_unsecured_story_under_epic_inherits_the_epic(self) -> None:
        record = _record(parent_external_record_id="epic-1")
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
        )
        assert record.inherit_permissions_from_group is False
        assert record.access_rule == AccessRule.STRICT

    def test_secured_story_under_epic_has_its_own_level_and_still_inherits_the_epic(self) -> None:
        record = _record(parent_external_record_id="epic-1")
        apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={"20": [{"type": "group", "value": "g"}]}),
        )
        assert record.inherit_permissions_from_group is False
        assert record.access_rule == AccessRule.RESTRICTED

    def test_subtask_with_null_security_follows_its_parent_issue(self) -> None:
        record = _record(parent_external_record_id="story-1")
        permissions, _skipped = apply_issue_access(
            record,
            {"security": None},
            is_subtask=True,
            project_key="PROJ",
            context=_present(),
        )
        assert permissions == []
        assert record.inherit_permissions_from_group is False
        assert record.access_rule == AccessRule.STRICT
        assert record.inherit_permissions is True

    def test_subtask_with_its_own_level_is_restricted_and_still_follows_the_parent(self) -> None:
        record = _record(parent_external_record_id="story-1")
        apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=True,
            project_key="PROJ",
            context=_present(members={"20": [{"type": "group", "value": "g"}]}),
        )
        assert record.inherit_permissions_from_group is False
        assert record.access_rule == AccessRule.RESTRICTED

    def test_attachment_takes_the_strict_rule_with_no_grants(self) -> None:
        record = _record(record_type=RecordType.FILE, parent_external_record_id="100")
        permissions = apply_attachment_access(record)
        assert permissions == []
        assert record.access_rule == AccessRule.STRICT
        assert record.rewrite_permissions is True
        assert record.inherit_permissions_from_group is False


class TestApplicationRoleHolders:
    MAPPING = {
        "jira-software": [{"groupId": "g-sw"}, {"groupId": "g-shared"}],
        "jira-servicedesk": [{"groupId": "g-sd"}, {"groupId": "g-shared"}],
    }

    def test_a_bare_level_holder_is_every_product_group_not_the_org(self) -> None:
        """"Any logged in user" on a level: the accounts with product access, as on Browse."""
        record = _record()
        permissions, skipped = apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={"20": [{"type": "applicationRole"}]}),
            app_roles_mapping=self.MAPPING,
        )
        assert {(p.entity_type, p.external_id) for p in permissions} == {
            (EntityType.GROUP, "g-sw"), (EntityType.GROUP, "g-shared"), (EntityType.GROUP, "g-sd")}
        assert skipped == [] and record.access_rule == AccessRule.RESTRICTED

    def test_a_bare_level_holder_with_no_roles_known_grants_nothing(self) -> None:
        permissions, skipped = apply_issue_access(
            _record(),
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={"20": [{"type": "applicationRole"}]}),
        )
        assert permissions == [] and skipped == ["applicationRole"]

    def test_a_group_leaving_an_application_role_changes_the_level_digest(self) -> None:
        """Otherwise the removed group's members keep the issue until it is edited."""
        ctx = _present(members={
            "20": [{"type": "applicationRole", "parameter": "jira-software"}],
            "21": [{"type": "group", "value": "g"}],
        })
        before = ctx.level_fingerprints(self.MAPPING)
        after = ctx.level_fingerprints({**self.MAPPING, "jira-software": [{"groupId": "g-sw"}]})
        assert before["20"] != after["20"]
        assert before["21"] == after["21"]

    def test_a_bare_holder_digest_follows_every_role(self) -> None:
        ctx = _present(members={"20": [{"type": "applicationRole"}]})
        before = ctx.level_fingerprints(self.MAPPING)
        after = ctx.level_fingerprints({**self.MAPPING, "jira-servicedesk": []})
        assert before["20"] != after["20"]



class TestSettledResult:
    def test_a_closed_issue_is_not_settled_but_an_empty_level_read_in_full_is(self) -> None:
        unread = _present(readable=False)
        assert apply_issue_access_settled(
            _record(), {"security": {"id": "20"}}, is_subtask=False, project_key="PROJ", context=unread,
        ) == ([], [], False)
        assert apply_issue_access_settled(
            _record(), {"security": {"id": "20"}}, is_subtask=False, project_key="PROJ", context=_present(),
        ) == ([], [], True)
        assert apply_issue_access_settled(
            _record(), {"security": None}, is_subtask=False, project_key="PROJ", context=unread,
        )[2] is True

    def test_deactivating_a_holder_changes_the_level_digest(self) -> None:
        """The deactivated user loses the grant, so the level's issues must be re-read."""
        holder = {"type": "user", "parameter": "acc-1", "user": {"accountId": "acc-1", "active": True}}
        before = _present(members={"20": [holder]}).level_fingerprints()
        after = _present(members={"20": [{**holder, "user": {**holder["user"], "active": False}}]}).level_fingerprints()
        assert before["20"] != after["20"]

class TestUnreadableReason:
    """Why levels cannot be applied, so the warning names the fix."""

    def test_a_readable_scheme_or_no_scheme_has_no_reason(self) -> None:
        assert unreadable_reason(_present()) is None
        assert unreadable_reason(IssueSecurityContext(knowledge=SchemeKnowledge.NONE)) is None

    def test_a_401_is_the_token_scope_and_a_403_the_account_permission(self) -> None:
        """A site admin's token without the issue security scope gets 401, not 403."""
        members_401 = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, scheme_id="9", read_status=401, scope_refused=True)
        assert unreadable_reason(members_401) == UNREADABLE_SCOPE
        # A 401 that names no scope is a token Jira no longer accepts.
        expired = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, scheme_id="9", read_status=401)
        assert unreadable_reason(expired) == UNREADABLE_AUTH
        members_403 = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, scheme_id="9", read_status=403)
        assert unreadable_reason(members_403) == UNREADABLE_PERMISSION
        scheme_403 = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True, read_status=403)
        assert unreadable_reason(scheme_403) == UNREADABLE_PERMISSION

    def test_members_read_but_held_for_unread_roles(self) -> None:
        held = _present(members={"10": [{"type": "applicationRole", "parameter": "jira-software"}]}, readable=False)
        assert unreadable_reason(held) == UNREADABLE_ROLES

    def test_a_transient_failure_is_told_apart(self) -> None:
        """It keeps the stored grants, so the warning must not say the issues are hidden."""
        transient = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, read_status=503, defer_checkpoint=True)
        assert unreadable_reason(transient) == UNREADABLE_TRANSIENT
        assert UNREADABLE_TRANSIENT in UNREADABLE_KEEPS_STORED_ACCESS and UNREADABLE_ROLES in UNREADABLE_KEEPS_STORED_ACCESS
        assert UNREADABLE_SCOPE not in UNREADABLE_KEEPS_STORED_ACCESS

    def test_any_other_failure(self) -> None:
        assert unreadable_reason(IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, read_status=503)) == UNREADABLE_OTHER
        assert unreadable_reason(IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN)) == UNREADABLE_OTHER


class TestRefusesAScope:
    def test_only_jiras_scope_message_counts(self) -> None:
        assert refuses_a_scope({"code": 401, "message": "Unauthorized; scope does not match"})
        assert not refuses_a_scope({"code": 401, "message": "Unauthorized"})
        assert not refuses_a_scope(None)


class TestSchemeHasNoLevels:
    def test_only_an_empty_levels_list_counts(self) -> None:
        assert scheme_has_no_levels({"id": 10001, "name": "ESP: Simplified Issue Security Scheme", "levels": []})
        assert not scheme_has_no_levels({"id": 10101, "levels": [{"id": "10102", "name": "admin only"}]})
        # Absent or malformed: the levels are not known to be none.
        assert not scheme_has_no_levels({"id": 10101})
        assert not scheme_has_no_levels({"id": 10101, "levels": None})
        assert not scheme_has_no_levels(None)


class TestLevelReadsAlike:
    """Whether two issues of one project in a level are read by the same people, which is
    when a story with its epic's level may hang under the epic."""

    @staticmethod
    def _context(holders: list[dict], **kw: object) -> IssueSecurityContext:
        return IssueSecurityContext(**{
            "knowledge": SchemeKnowledge.PRESENT, "scheme_id": "1", "members_readable": True,
            "members_by_level": {"10": holders}, **kw})

    @pytest.mark.parametrize("holder_type", ["user", "group", "projectRole", "projectLead", "applicationRole", "anyone"])
    def test_holders_that_name_the_same_people_on_every_issue(self, holder_type: str) -> None:
        assert level_reads_alike(self._context([{"type": holder_type}]), "10")

    @pytest.mark.parametrize("holder_type", ["reporter", "assignee", "userCustomField", "groupCustomField", "unknown"])
    def test_a_holder_that_names_people_per_issue_or_is_unknown(self, holder_type: str) -> None:
        assert not level_reads_alike(self._context([{"type": "group"}, {"type": holder_type}]), "10")

    def test_unread_members_or_an_unlisted_level_are_not_known(self) -> None:
        assert not level_reads_alike(self._context([{"type": "group"}], members_readable=False), "10")
        assert not level_reads_alike(self._context([{"type": "group"}]), "11")
        assert not level_reads_alike(self._context([]), "10")

    def test_a_personal_connector_enforces_no_levels(self) -> None:
        assert not level_reads_alike(self._context([{"type": "group"}], enforce=False), "10")


class TestBrowseHoldersNamingPeoplePerIssue:
    """Browse Projects granted to the reporter, the assignee or a user or group field."""

    @staticmethod
    def _apply(browse: list[dict] | None, fields: dict | None = None, record: Record | None = None,
               level_returned: bool = True, **kw: object) -> tuple[Record, set, bool]:
        record = record or _record()
        context = IssueSecurityContext(knowledge=SchemeKnowledge.NONE, browse_holders=browse)
        grants, _skipped, settled = apply_issue_access_settled(
            record, {**({"security": None} if level_returned else {}), **(fields or {})}, is_subtask=False, project_key="PROJ", context=context,
            **{"reporter_email": "rep@ex.com", "assignee_email": "asg@ex.com", **kw},
        )
        return record, {(p.entity_type, p.email or p.external_id) for p in grants}, settled

    def test_the_reporter_of_a_top_level_issue_reads_it_though_the_project_is_closed_to_them(self) -> None:
        record, grants, _ = self._apply([{"type": "reporter"}])
        assert grants == {(EntityType.USER, "rep@ex.com")}
        # A direct grant must be enough: STRICT would also ask for the project.
        assert record.access_rule == AccessRule.OPEN and record.rewrite_permissions is True

    def test_a_reassigned_assignee_loses_the_issue_and_the_new_one_gains_it(self) -> None:
        holders = [{"type": "assignee"}]
        _, before, _ = self._apply(holders, assignee_email="old@ex.com")
        _, after, _ = self._apply(holders, assignee_email="new@ex.com")
        assert before == {(EntityType.USER, "old@ex.com")}
        assert after == {(EntityType.USER, "new@ex.com")}  # the write replaces the grants

    def test_a_scheme_with_no_such_holder_gives_the_reporter_nothing(self) -> None:
        record, grants, _ = self._apply([])
        assert grants == set()
        assert record.access_rule == AccessRule.STRICT

    def test_only_the_named_role_is_granted(self) -> None:
        _, grants, _ = self._apply([{"type": "reporter"}])
        assert (EntityType.USER, "asg@ex.com") not in grants

    def test_an_account_outside_the_directory_gets_no_grant_and_the_issue_stays_strict(self) -> None:
        record, grants, _ = self._apply([{"type": "reporter"}], reporter_email=None)
        assert grants == set() and record.access_rule == AccessRule.STRICT

    def test_an_issue_filed_under_another_follows_it_and_gets_no_grant(self) -> None:
        record, grants, _ = self._apply([{"type": "reporter"}], record=_record(parent_external_record_id="99"))
        assert grants == set() and record.access_rule == AccessRule.STRICT

    def test_a_stub_gets_no_grant(self) -> None:
        record, grants, _ = self._apply([{"type": "reporter"}], record=_record(is_placeholder=True))
        assert grants == set() and record.access_rule == AccessRule.STRICT

    def test_a_leveled_issue_keeps_the_level_grants_alone(self) -> None:
        record = _record()
        context = _present(members={"20": [{"type": "group", "value": "g-1"}]})
        context.browse_holders = [{"type": "reporter"}]
        grants, _ = apply_issue_access(
            record, {"security": {"id": "20"}}, is_subtask=False, project_key="PROJ", context=context,
            reporter_email="rep@ex.com",
        )
        assert {(p.entity_type, p.external_id) for p in grants} == {(EntityType.GROUP, "g-1")}
        assert record.access_rule == AccessRule.RESTRICTED

    def test_user_and_group_fields_are_granted_and_an_absent_field_spares_the_other_holders(self) -> None:
        holders = [
            {"type": "reporter"},
            {"type": "userCustomField", "parameter": "customfield_10"},
            {"type": "groupCustomField", "parameter": "customfield_11"},
            {"type": "userCustomField", "parameter": "customfield_12"},
        ]
        _, grants, _ = self._apply(holders, {
            "customfield_10": [{"accountId": "acc-7"}], "customfield_11": {"groupId": "g-9"},
        }, user_by_account_id={"acc-7": type("U", (), {"email": "seven@ex.com"})()})
        assert grants == {(EntityType.USER, "rep@ex.com"), (EntityType.USER, "seven@ex.com"), (EntityType.GROUP, "g-9")}

    def test_the_same_person_in_two_roles_is_granted_once(self) -> None:
        _, grants, _ = self._apply([{"type": "reporter"}, {"type": "assignee"}], assignee_email="rep@ex.com")
        assert grants == {(EntityType.USER, "rep@ex.com")}

    def test_unread_holders_keep_the_stored_grants_of_a_top_level_issue(self) -> None:
        record, grants, settled = self._apply(None, level_returned=False)
        assert grants == set() and settled is True
        # Not rewritten, so a stored reporter grant stays; OPEN so that grant still opens the issue.
        assert record.rewrite_permissions is False and record.access_rule == AccessRule.OPEN

    def test_a_level_cleared_while_the_holders_are_unread_drops_the_old_level_grants(self) -> None:
        """An explicit null level: the issue was RESTRICTED (project and grant), OPEN would take the grant alone."""
        record, grants, _ = self._apply(None)
        assert grants == set() and record.rewrite_permissions is True and record.access_rule == AccessRule.STRICT

    def test_unread_holders_still_close_a_child_to_its_parent_alone(self) -> None:
        record, grants, _ = self._apply(None, record=_record(parent_external_record_id="99"))
        assert grants == set() and record.access_rule == AccessRule.STRICT and record.rewrite_permissions is True

    def test_the_fingerprint_tells_none_unknown_and_a_change(self) -> None:
        def fp(holders: list[dict] | None) -> str:
            return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, browse_holders=holders).browse_fingerprint()
        assert fp([]) == "" and fp(None) == "?"
        assert fp([{"type": "reporter"}]) not in ("", "?")
        assert fp([{"type": "reporter"}]) != fp([{"type": "assignee"}])

    def test_the_fingerprint_tells_two_field_holders_apart_by_the_field_they_carry(self) -> None:
        def fp(field_id: str) -> str:
            holder = {"type": "userCustomField", "field": {"id": field_id}}
            return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, browse_holders=[holder]).browse_fingerprint()
        assert fp("customfield_1") != fp("customfield_2")





class TestVisibleProjectNotFound:
    BODY = {"errorMessages": ["The project has no issue security scheme."], "errors": {}}

    def test_a_404_with_error_messages_from_a_project_the_caller_lists_is_no_scheme(self) -> None:
        assert classify_scheme_response(404, self.BODY, project_is_visible=True) == (SchemeKnowledge.NONE, None)

    @pytest.mark.parametrize("body", [
        None, {}, {"message": "Not Found"}, {"errorMessages": []}, {"errorMessages": ["Not Found"]},
        {"errorMessages": ["No project could be found with key 'P'."]},
        {"errorMessages": ["The requested resource was not found"], "errors": {}},
    ], ids=str)
    def test_a_404_without_a_message_may_be_a_route_the_server_lacks_and_stays_unknown(self, body) -> None:
        assert classify_scheme_response(404, body, project_is_visible=True) == (SchemeKnowledge.UNKNOWN, None)

    def test_without_the_flag_it_is_unknown(self) -> None:
        assert classify_scheme_response(404, self.BODY) == (SchemeKnowledge.UNKNOWN, None)

    def test_a_forbidden_read_is_never_no_scheme(self) -> None:
        assert classify_scheme_response(403, self.BODY, project_is_visible=True) == (SchemeKnowledge.UNKNOWN, None)


class TestMembersRouteMissing:
    @pytest.mark.parametrize("status", [404, 405, 501])
    def test_a_scheme_whose_members_route_the_server_lacks_has_its_own_reason(self, status) -> None:
        context = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, scheme_id="1", read_status=status)
        assert unreadable_reason(context) == UNREADABLE_NOT_LISTED
        assert UNREADABLE_NOT_LISTED not in UNREADABLE_KEEPS_STORED_ACCESS  # such issues are hidden

    def test_a_transient_read_is_still_transient(self) -> None:
        context = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, scheme_id="1", read_status=503, defer_checkpoint=True)
        assert unreadable_reason(context) == UNREADABLE_TRANSIENT


def _with_users(**levels: list) -> IssueSecurityContext:
    return IssueSecurityContext(
        knowledge=SchemeKnowledge.PRESENT, scheme_id="1", members_readable=True,
        members_by_level={lid: [{"type": "user", "parameter": p} for p in people] for lid, people in levels.items()},
    )


class TestPlanLevelReread:
    def test_a_project_stored_before_levels_were_tracked_reads_every_leveled_issue_once(self) -> None:
        security = _with_users(**{"1": ["a"]})
        reread, levels, to_save = plan_level_reread(security, None, is_new_project=False, app_roles_mapping={})
        assert (reread, levels) == ("leveled", None)
        assert to_save == security.level_fingerprints()
        # With the digest saved, the next sync reads nothing.
        assert plan_level_reread(security, to_save, is_new_project=False, app_roles_mapping={})[0] is None

    def test_a_project_with_no_scheme_and_no_digest_reads_nothing(self) -> None:
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert plan_level_reread(none, None, is_new_project=False, app_roles_mapping={}) == (None, None, {})

    def test_a_new_project_reads_nothing_and_saves_the_digests(self) -> None:
        security = _with_users(**{"1": ["a"]})
        assert plan_level_reread(security, None, is_new_project=True, app_roles_mapping={}) == (
            None, None, security.level_fingerprints())

    def test_only_the_level_whose_holders_changed_is_read(self) -> None:
        before = _with_users(**{"1": ["a"], "2": ["b"]}).level_fingerprints()
        now = _with_users(**{"1": ["a", "c"], "2": ["b"]})
        assert plan_level_reread(now, before, is_new_project=False, app_roles_mapping={})[:2] == ("levels", ["1"])

    def test_a_level_gone_or_a_scheme_removed_reads_the_whole_project(self) -> None:
        before = _with_users(**{"1": ["a"], "2": ["b"]}).level_fingerprints()
        assert plan_level_reread(_with_users(**{"1": ["a"]}), before, is_new_project=False, app_roles_mapping={})[0] == "project"
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert plan_level_reread(none, before, is_new_project=False, app_roles_mapping={})[0] == "project"

    def test_a_transient_read_keeps_the_digests_and_marks_a_retry(self) -> None:
        before = _with_users(**{"1": ["a"]}).level_fingerprints()
        down = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True)
        assert plan_level_reread(down, before, is_new_project=False, app_roles_mapping={}) == (
            None, None, {**before, "*": "retry"})
        # Once readable again, the marker reads every leveled issue.
        assert plan_level_reread(_with_users(**{"1": ["a"]}), {**before, "*": "retry"}, is_new_project=False,
                                 app_roles_mapping={})[0] == "leveled"

    def test_a_lasting_refusal_closes_once_and_is_not_walked_again(self) -> None:
        refused = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, read_status=403, scheme_forbidden=True)
        reread, _, to_save = plan_level_reread(refused, None, is_new_project=False, app_roles_mapping={})
        assert (reread, to_save) == ("leveled", {"*": "unreadable"})
        assert plan_level_reread(refused, to_save, is_new_project=False, app_roles_mapping={})[0] is None


class TestHoldLevelsNamingUnreadRoles:
    def test_a_level_naming_an_application_role_is_unknown_while_the_roles_are_unread(self) -> None:
        security = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "applicationRole", "parameter": "jira-software"}]})
        held = hold_levels_naming_unread_roles(security, app_roles_unread=True)
        assert held.members_readable is False and held.defer_checkpoint is True

    def test_other_levels_and_read_roles_are_untouched(self) -> None:
        security = _with_users(**{"1": ["a"]})
        assert hold_levels_naming_unread_roles(security, app_roles_unread=True).members_readable is True


class TestUnreadableNotices:
    def test_one_notice_per_reason_names_the_effect_and_the_projects(self) -> None:
        notices = unreadable_security_notices({"B": UNREADABLE_PERMISSION, "A": UNREADABLE_PERMISSION, "C": UNREADABLE_TRANSIENT})
        by_reason = {n[0]: n for n in notices}
        assert by_reason[UNREADABLE_PERMISSION][1:3] == ("are hidden from everyone", ["A", "B"])
        assert "Administer Jira" in by_reason[UNREADABLE_PERMISSION][3]
        assert by_reason[UNREADABLE_TRANSIENT][1] == "keep the access they had"


class TestDeferredReadOnAProjectNeverTightened:
    """Issues stored open have no grants of their own to keep, so a read that fails for now closes them."""

    DOWN = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True, read_status=503)

    def test_the_first_deferred_read_closes_the_leveled_issues_and_marks_the_pass_done(self) -> None:
        reread, levels, to_save = plan_level_reread(self.DOWN, None, is_new_project=False, app_roles_mapping={})
        assert (reread, levels, to_save) == ("leveled", None, {"*": "held"})
        # A read that keeps failing does not repeat the pass.
        assert plan_level_reread(self.DOWN, to_save, is_new_project=False, app_roles_mapping={})[:3] == (
            None, None, {"*": "held"})
        # Once it reads, every leveled issue is read again.
        assert plan_level_reread(_with_users(**{"1": ["a"]}), to_save, is_new_project=False,
                                 app_roles_mapping={})[0] == "leveled"

    def test_a_project_already_tightened_keeps_its_grants_and_a_new_one_has_none_to_close(self) -> None:
        tightened = _with_users(**{"1": ["a"]}).level_fingerprints()
        assert plan_level_reread(self.DOWN, tightened, is_new_project=False, app_roles_mapping={}) == (
            None, None, {**tightened, "*": "retry"})
        assert plan_level_reread(self.DOWN, None, is_new_project=True, app_roles_mapping={})[0] is None

    def test_a_project_with_no_scheme_has_nothing_to_close(self) -> None:
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE, defer_checkpoint=True)
        assert plan_level_reread(none, None, is_new_project=False, app_roles_mapping={})[0] is None

    def test_a_level_naming_unread_roles_on_a_project_never_tightened_is_closed_too(self) -> None:
        security = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "applicationRole", "parameter": "jira-software"}]})
        held = hold_levels_naming_unread_roles(security, app_roles_unread=True)
        assert plan_level_reread(held, None, is_new_project=False, app_roles_mapping={})[0] == "leveled"

    def test_the_notice_says_hidden_for_such_a_project_and_kept_for_the_others(self) -> None:
        notices = unreadable_security_notices(
            {"A": UNREADABLE_TRANSIENT, "B": UNREADABLE_TRANSIENT}, never_tightened={"A"})
        effects = {tuple(n[2]): n[1] for n in notices}
        assert effects == {("A",): "are hidden from everyone", ("B",): "keep the access they had"}


class TestUnresolvedUserHolders:
    def _grants(self, holder, users=None) -> tuple:
        record = _record()
        context = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True, members_by_level={"1": [holder]})
        return apply_issue_access_settled(
            record, {"security": {"id": "1"}}, is_subtask=False, project_key="P", context=context,
            user_by_account_id=users or {},
        )

    def test_a_user_who_resolves_to_no_email_is_skipped_not_dropped_silently(self) -> None:
        grants, skipped, _ = self._grants({"type": "user", "parameter": "someone", "user": {"name": "someone"}})
        assert grants == [] and skipped == ["user"]

    def test_a_user_with_an_email_on_the_holder_is_granted(self) -> None:
        grants, skipped, _ = self._grants(
            {"type": "user", "parameter": "k", "user": {"key": "k", "emailAddress": "k@example.com"}})
        assert [g.email for g in grants] == ["k@example.com"] and skipped == []

    def test_a_deactivated_user_is_not_a_skipped_holder(self) -> None:
        _, skipped, _ = self._grants({"type": "user", "parameter": "k", "user": {"key": "k", "active": False}})
        assert skipped == []


class TestNotListedWording:
    def test_the_reason_names_the_server_without_saying_data_center(self) -> None:
        assert "Data Center" not in UNREADABLE_REASONS[UNREADABLE_NOT_LISTED]
        assert "does not list who holds" in UNREADABLE_REASONS[UNREADABLE_NOT_LISTED]


class TestMarkers:
    def test_the_pre_walk_marker_keeps_a_pass_already_done(self) -> None:
        assert pre_walk_marker({"1": "d", "*": "held"}) == {"1": "d", "*": "held"}
        assert pre_walk_marker({"1": "d"}) == {"1": "d", "*": "retry"}
        assert pre_walk_marker({"*": "retry"}) == {"*": "retry"}
        assert pre_walk_marker(None) == {"*": "retry"}

    def test_a_closing_pass_with_a_build_failure_is_not_marked_done(self) -> None:
        assert levels_after_walk({"*": "held"}, 1) == {"*": "retry"}
        assert levels_after_walk({"*": "held"}, 0) == {"*": "held"}
        assert levels_after_walk({"1": "d", "*": "retry"}, 3) == {"1": "d", "*": "retry"}
        assert levels_after_walk({"1": "d"}, 3) == {"1": "d"}


class TestAmbiguousNotFound:
    def test_the_cloud_message_is_no_scheme_with_or_without_the_flag(self) -> None:
        body = {"errorMessages": ["Security level for project 10000 does not exist"]}
        assert classify_scheme_response(404, body) == (SchemeKnowledge.NONE, None)
        assert classify_scheme_response(404, body, project_is_visible=True) == (SchemeKnowledge.NONE, None)

    def test_a_generic_not_found_is_unknown_even_for_a_visible_project(self) -> None:
        body = {"errorMessages": ["Not Found"]}
        assert classify_scheme_response(404, body) == (SchemeKnowledge.UNKNOWN, None)
        assert classify_scheme_response(404, body, project_is_visible=True) == (SchemeKnowledge.UNKNOWN, None)

    def test_a_deferred_unclear_not_found_has_its_own_reason_that_keeps_stored_access(self) -> None:
        context = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, read_status=404, defer_checkpoint=True)
        assert unreadable_reason(context) == UNREADABLE_UNCLEAR_404
        assert UNREADABLE_UNCLEAR_404 in UNREADABLE_KEEPS_STORED_ACCESS
        # A 404 that was not deferred keeps its reason.
        assert unreadable_reason(IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, read_status=404)) == UNREADABLE_OTHER


class TestPagingWithTotalOnly:
    @staticmethod
    def _server(rows: int, *, total: bool, page_size: int = 100) -> tuple[Callable[[int], Awaitable[tuple[int, dict]]], list[int]]:
        calls: list[int] = []

        async def fetch(start: int) -> tuple[int, dict]:
            calls.append(start)
            page = [{"n": n} for n in range(start, min(start + page_size, rows))]
            body: dict = {"values": page, "maxResults": page_size, "startAt": start}
            if total:
                body["total"] = rows
            return 200, body

        return fetch, calls

    async def test_a_server_that_pages_with_total_and_no_islast_is_read_past_the_first_page(self) -> None:
        fetch, calls = self._server(250, total=True)
        status, values = await collect_paged_values(fetch)
        assert status == 200 and len(values) == 250 and calls == [0, 100, 200]

    async def test_with_neither_islast_nor_total_it_reads_until_an_empty_page(self) -> None:
        fetch, calls = self._server(250, total=False)
        _, values = await collect_paged_values(fetch)
        assert len(values) == 250 and calls == [0, 100, 200, 300]

    async def test_a_page_marked_last_ends_it_and_a_total_reached_ends_it(self) -> None:
        async def last(start: int) -> tuple[int, dict]:
            return 200, {"values": [{"n": 1}], "isLast": True}
        assert (await collect_paged_values(last))[1] == [{"n": 1}]
        fetch, calls = self._server(100, total=True)
        assert len((await collect_paged_values(fetch))[1]) == 100 and calls == [0]

    async def test_it_stays_bounded(self) -> None:
        async def endless(start: int) -> tuple[int, dict]:
            return 200, {"values": [{"n": start}], "maxResults": 1000}
        status, values = await collect_paged_values(endless)
        assert values is None

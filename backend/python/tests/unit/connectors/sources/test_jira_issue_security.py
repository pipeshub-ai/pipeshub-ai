"""Issue security decisions for Jira Cloud and Data Center team connectors."""

import pytest

from app.config.constants.arangodb import AccessRule, Connectors, OriginTypes
from app.connectors.sources.atlassian.core.jira_issue_security import (
    UNREADABLE_AUTH,
    UNREADABLE_KEEPS_STORED_ACCESS,
    UNREADABLE_OTHER,
    UNREADABLE_PERMISSION,
    UNREADABLE_ROLES,
    UNREADABLE_SCOPE,
    UNREADABLE_TRANSIENT,
    IssueSecurityContext,
    SchemeKnowledge,
    apply_attachment_access,
    apply_issue_access,
    apply_issue_access_settled,
    classify_project_mapping,
    classify_scheme_response,
    level_reads_alike,
    refuses_a_scope,
    resolve_scheme_knowledge,
    scheme_has_no_levels,
    unreadable_reason,
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

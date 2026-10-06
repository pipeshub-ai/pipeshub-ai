"""Issue security decisions for Jira Cloud and Data Center team connectors."""

from app.config.constants.arangodb import AccessRule, Connectors, OriginTypes
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
    apply_attachment_access,
    apply_issue_access,
    classify_project_mapping,
    classify_scheme_response,
    resolve_scheme_knowledge,
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
    def test_200_with_id_is_a_scheme(self):
        knowledge, scheme_id = classify_scheme_response(200, {"id": 9, "name": "Sec"})
        assert knowledge == SchemeKnowledge.PRESENT
        assert scheme_id == "9"

    def test_200_without_id_is_not_no_scheme(self):
        assert classify_scheme_response(200, {})[0] == SchemeKnowledge.UNKNOWN

    def test_404_and_403_are_not_no_scheme(self):
        assert classify_scheme_response(404, {"errorMessages": ["gone"]})[0] == SchemeKnowledge.UNKNOWN
        assert classify_scheme_response(404, None)[0] == SchemeKnowledge.UNKNOWN
        assert classify_scheme_response(403, None)[0] == SchemeKnowledge.UNKNOWN

    def test_jira_no_scheme_404_is_no_scheme_for_a_classic_project(self):
        knowledge, scheme_id = classify_scheme_response(
            404,
            {"errorMessages": ["Security level for project 14607 does not exist."]},
            project_style="classic",
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_jira_no_scheme_404_does_not_decide_a_next_gen_project(self):
        knowledge, _scheme_id = classify_scheme_response(
            404,
            {"errorMessages": ["Security level for project 14607 does not exist."]},
            project_style="next-gen",
        )
        assert knowledge == SchemeKnowledge.UNKNOWN

    def test_direct_no_scheme_wins_over_an_unreadable_mapping(self):
        knowledge, scheme_id = resolve_scheme_knowledge(
            (SchemeKnowledge.NONE, None),
            (SchemeKnowledge.UNKNOWN, None),
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_completed_mapping_without_a_classic_project_is_no_scheme(self):
        knowledge, scheme_id = classify_project_mapping(
            "100",
            [{"projectId": "200", "issueSecuritySchemeId": "9"}],
            complete=True,
            http_status=200,
            project_style="classic",
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_classic_row_without_a_scheme_id_is_no_scheme(self):
        knowledge, scheme_id = classify_project_mapping(
            "14607",
            [{"projectId": "14607"}],
            complete=True,
            http_status=200,
            project_style="classic",
        )
        assert knowledge == SchemeKnowledge.NONE
        assert scheme_id is None

    def test_next_gen_row_without_a_scheme_id_is_not_no_scheme(self):
        knowledge, _scheme_id = classify_project_mapping(
            "14607",
            [{"projectId": "14607"}],
            complete=True,
            http_status=200,
            project_style="next-gen",
        )
        assert knowledge == SchemeKnowledge.UNKNOWN

    def test_mapping_hit_is_a_scheme(self):
        knowledge, scheme_id = classify_project_mapping(
            "100",
            [{"projectId": "100", "issueSecuritySchemeId": "9"}],
            complete=True,
            http_status=200,
            project_style="classic",
        )
        assert (knowledge, scheme_id) == (SchemeKnowledge.PRESENT, "9")

    def test_next_gen_absent_from_the_classic_mapping_is_not_no_scheme(self):
        knowledge, _scheme_id = classify_project_mapping(
            "100",
            [],
            complete=True,
            http_status=200,
            project_style="next-gen",
        )
        assert knowledge == SchemeKnowledge.UNKNOWN

    def test_incomplete_or_forbidden_mapping_is_not_no_scheme(self):
        assert classify_project_mapping(
            "100", [], complete=False, http_status=200, project_style="classic",
        )[0] == SchemeKnowledge.UNKNOWN
        assert classify_project_mapping(
            "100", None, complete=False, http_status=403, project_style="classic",
        )[0] == SchemeKnowledge.UNKNOWN


class TestIssueAccess:
    def test_no_scheme_null_security_follows_the_project(self):
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

    def test_null_security_in_a_scheme_is_not_the_default_level(self):
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

    def test_missing_security_field_does_not_open_the_issue(self):
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

    def test_unknown_scheme_hides_the_issue(self):
        record = _record()
        permissions, _skipped = apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, scheme_forbidden=True),
        )
        assert permissions == []
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.rewrite_permissions is True

    def test_transient_unknown_scheme_keeps_stored_grants(self):
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

    def test_level_members_are_direct_grants_and_not_an_org_grant(self):
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

    def test_unreadable_custom_field_hides_the_issue(self):
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

    def test_empty_user_picker_writes_no_user_and_replaces(self):
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

    def test_members_unreadable_hides_a_secured_issue(self):
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

    def test_transient_unreadable_members_keep_stored_grants(self):
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

    def test_story_under_epic_inherits_the_epic(self):
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

    def test_secured_story_under_epic_inherits_the_epic(self):
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

    def test_subtask_with_null_security_follows_its_parent_issue(self):
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

    def test_subtask_with_its_own_level_is_restricted_and_still_follows_the_parent(self):
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

    def test_attachment_is_strict_with_no_grants(self):
        record = _record(record_type=RecordType.FILE, parent_external_record_id="100")
        permissions = apply_attachment_access(record)
        assert permissions == []
        assert record.access_rule == AccessRule.STRICT
        assert record.rewrite_permissions is True
        assert record.inherit_permissions_from_group is False

"""Issue security decisions for Jira Cloud and Data Center team connectors."""

import pytest

from app.config.constants.arangodb import AccessRule, Connectors, OriginTypes
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
    apply_attachment_access,
    apply_issue_access,
    classify_project_mapping,
    classify_scheme_response,
    level_changed,
    level_presence,
    parent_level,
    resolve_scheme_knowledge,
    unreadable_issue_security_cause,
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

    def test_story_under_an_open_epic_inherits_only_the_epic(self):
        record = _record(parent_external_record_id="epic-1")
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
            parent_secured=False,
        )
        assert record.inherit_permissions_from_group is False
        assert record.access_rule == AccessRule.STRICT

    def test_secured_story_under_an_open_epic_inherits_only_the_epic(self):
        record = _record(parent_external_record_id="epic-1")
        apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={"20": [{"type": "group", "value": "g"}]}),
            parent_secured=False,
        )
        assert record.inherit_permissions_from_group is False
        assert record.access_rule == AccessRule.RESTRICTED

    def test_story_under_a_secured_epic_also_takes_the_project(self):
        """Jira does not apply an epic's level to its stories (JC-02)."""
        record = _record(parent_external_record_id="epic-1")
        permissions, _skipped = apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
            parent_secured=True,
        )
        assert permissions == []
        assert record.inherit_permissions_from_group is True
        assert record.inherit_permissions is True
        assert record.access_rule == AccessRule.STRICT

    def test_story_whose_epic_is_unknown_also_takes_the_project(self):
        record = _record(parent_external_record_id="epic-1")
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
        )
        assert record.inherit_permissions_from_group is True

    def test_secured_story_under_a_secured_epic_keeps_its_own_level(self):
        record = _record(parent_external_record_id="epic-1")
        permissions, _skipped = apply_issue_access(
            record,
            {"security": {"id": "20"}},
            is_subtask=False,
            project_key="PROJ",
            context=_present(members={"20": [{"type": "group", "value": "g"}]}),
            parent_secured=True,
        )
        assert [p.external_id for p in permissions] == ["g"]
        assert record.inherit_permissions_from_group is True
        assert record.access_rule == AccessRule.RESTRICTED

    def test_issue_without_a_parent_needs_no_group_flag(self):
        record = _record()
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=_present(),
        )
        assert record.inherit_permissions_from_group is False

    def test_subtask_under_a_secured_parent_follows_only_the_parent(self):
        record = _record(parent_external_record_id="story-1")
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=True,
            project_key="PROJ",
            context=_present(),
            parent_secured=True,
        )
        assert record.inherit_permissions_from_group is False

    def test_personal_connector_never_takes_the_group_flag(self):
        record = _record(parent_external_record_id="epic-1")
        apply_issue_access(
            record,
            {"security": None},
            is_subtask=False,
            project_key="PROJ",
            context=IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False),
            parent_secured=True,
        )
        assert record.inherit_permissions_from_group is False

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

    def test_attachment_takes_the_strict_rule_with_no_grants(self):
        record = _record(record_type=RecordType.FILE, parent_external_record_id="100")
        permissions = apply_attachment_access(record)
        assert permissions == []
        assert record.access_rule == AccessRule.STRICT
        assert record.rewrite_permissions is True
        assert record.inherit_permissions_from_group is False


class TestUnreadableCause:
    def test_cloud_401_names_the_missing_oauth_scopes_and_reconsent(self):
        text = unreadable_issue_security_cause(401, cloud=True)
        assert "read:issue-security-level:jira" in text
        assert "read:field:jira" in text
        assert "re-authorize" in text

    def test_403_names_the_jira_permission(self):
        assert "Administer Jira" in unreadable_issue_security_cause(403, cloud=True)
        assert "Administer Jira" in unreadable_issue_security_cause(403, cloud=False)

    def test_data_center_401_is_the_credentials(self):
        text = unreadable_issue_security_cause(401, cloud=False)
        assert "scope" not in text
        assert "credentials" in text

    def test_transient_status_says_it_is_retried(self):
        assert "next sync" in unreadable_issue_security_cause(503, cloud=True)


class TestParentLevel:
    def test_presence_reads_the_security_field(self):
        assert level_presence({"security": {"id": "20"}}) is True
        assert level_presence({"security": None}) is False
        assert level_presence({}) is None
        assert level_presence({"security": {"name": "no id"}}) is None

    @pytest.mark.asyncio
    async def test_a_parent_is_read_once_and_remembered(self):
        reads: list[str] = []

        async def fetch(issue_id):
            reads.append(issue_id)
            return {"security": {"id": "20"}}

        known: dict = {}
        assert await parent_level("1001", _present(), known, fetch) is True
        assert await parent_level("1001", _present(), known, fetch) is True
        assert reads == ["1001"]

    @pytest.mark.asyncio
    async def test_an_unreadable_parent_is_unknown_and_not_read_again(self):
        reads: list[str] = []

        async def fetch(issue_id):
            reads.append(issue_id)
            return None

        known: dict = {}
        assert await parent_level("1001", _present(), known, fetch) is None
        assert await parent_level("1001", _present(), known, fetch) is None
        assert reads == ["1001"]

    @pytest.mark.asyncio
    async def test_a_project_without_a_scheme_reads_nothing(self):
        async def fetch(issue_id):
            raise AssertionError("no read expected")

        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert await parent_level("1001", none, {}, fetch) is False

    def test_a_stored_open_issue_that_gains_a_level_has_changed(self):
        stored = _record(access_rule=AccessRule.STRICT)
        assert level_changed(stored, {"security": {"id": "20"}}, _present()) is True
        assert level_changed(stored, {"security": None}, _present()) is False

    def test_a_stored_secured_issue_that_loses_its_level_has_changed(self):
        stored = _record(access_rule=AccessRule.RESTRICTED)
        assert level_changed(stored, {"security": None}, _present()) is True
        assert level_changed(stored, {"security": {"id": "20"}}, _present()) is False

    def test_a_new_issue_or_a_placeholder_has_no_change(self):
        assert level_changed(None, {"security": {"id": "20"}}, _present()) is False
        stub = _record(access_rule=AccessRule.STRICT, is_placeholder=True)
        assert level_changed(stub, {"security": {"id": "20"}}, _present()) is False

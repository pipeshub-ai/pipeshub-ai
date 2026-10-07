"""Projects feature integration tests.

Two independent layers:

1. ``Test*OpenApiRequestContract`` / ``Test*OpenApiResponseContract`` — pure,
   offline validation of representative request/response bodies against
   ``pipeshub-openapi.yaml``. No network calls; safe to run without a live
   stack (mirrors ``TestConversationStreamOpenApiRequestContract`` in
   ``integration_test_conversation.py``).
2. ``@pytest.mark.integration`` classes — full CRUD, access-matrix, member,
   file, and conversation-linking lifecycle tests against a live backend.

Routes covered (Node `backend/nodejs/apps/src/modules/projects/`):
    POST/GET     /api/v1/projects
    GET/PATCH/DELETE /api/v1/projects/{projectId}
    POST         /api/v1/projects/{projectId}/{archive,unarchive,pin,unpin}
    GET          /api/v1/projects/{projectId}/conversations
    POST         /api/v1/projects/{projectId}/knowledge-base
    GET/PUT      /api/v1/projects/{projectId}/members
    DELETE       /api/v1/projects/{projectId}/members/{memberUserId}
    PUT/PATCH    /api/v1/conversations/{conversationId}/project[-visibility]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.clients.conversations_client import ConversationsClient
from helper.clients.kb_client import KBClient
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from openapi_schema_validator import (
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_operation,
)

# ---------------------------------------------------------------------------
# Part 1 — offline OpenAPI contract tests (no network, no live stack needed)
# ---------------------------------------------------------------------------


class TestProjectRequestOpenApiContract:
    def test_create_project_minimal(self) -> None:
        assert_request_body_matches_openapi_operation({"name": "Q3 Plan"}, "createProject")

    def test_create_project_full(self) -> None:
        assert_request_body_matches_openapi_operation(
            {
                "name": "Q3 Plan",
                "description": "Coordinate the Q3 launch",
                "icon": "folder",
                "color": "blue",
                "instructions": "Always cite the launch doc.",
                "knowledgeScope": {"apps": ["app-1"], "kb": ["kb-1"]},
                "appliedFilters": {"apps": [], "kb": []},
            },
            "createProject",
        )

    def test_create_project_rejects_missing_name(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation({}, "createProject")

    def test_create_project_rejects_unknown_field(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"name": "x", "unexpectedField": 1}, "createProject"
            )

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "Renamed"},
            {"visibility": "org"},
            {"chatSharing": "members"},
            {"name": "Renamed", "visibility": "org", "chatSharing": "members"},
            {"instructions": "Cite sources.", "knowledgeScope": {"apps": ["a1"]}},
        ],
    )
    def test_update_project_accepts_owner_only_fields_alongside_others(
        self, payload: dict[str, Any]
    ) -> None:
        # Regression guard: UpdateProjectRequest used to be `allOf:
        # [CreateProjectRequest, {additionalProperties: false, ...}]`, which
        # combines two *closed* schemas — every real update (including the
        # documented owner-only visibility/chatSharing case) would fail
        # against CreateProjectRequest's additionalProperties:false. Flattened
        # into a single schema; this pins that fix.
        assert_request_body_matches_openapi_operation(payload, "updateProject")

    def test_set_conversation_project_link(self) -> None:
        assert_request_body_matches_openapi_operation(
            {"projectId": "aaaaaaaaaaaaaaaaaaaaaaaa"}, "setConversationProject"
        )

    def test_set_conversation_project_unlink_with_null(self) -> None:
        assert_request_body_matches_openapi_operation(
            {"projectId": None}, "setConversationProject"
        )

    def test_set_conversation_project_requires_the_field(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation({}, "setConversationProject")

    @pytest.mark.parametrize("visibility", ["private", "project"])
    def test_set_conversation_project_visibility(self, visibility: str) -> None:
        assert_request_body_matches_openapi_operation(
            {"visibility": visibility}, "setConversationProjectVisibility"
        )

    def test_set_conversation_project_visibility_rejects_bad_enum(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"visibility": "public"}, "setConversationProjectVisibility"
            )

    def test_upsert_project_members(self) -> None:
        assert_request_body_matches_openapi_operation(
            {
                "members": [
                    {"principalId": "aaaaaaaaaaaaaaaaaaaaaaaa", "role": "viewer"},
                    {"principalId": "bbbbbbbbbbbbbbbbbbbbbbbb", "role": "editor"},
                ]
            },
            "upsertProjectMembers",
        )

    def test_upsert_project_members_rejects_bad_role(self) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(
                {"members": [{"principalId": "aaaaaaaaaaaaaaaaaaaaaaaa", "role": "owner"}]},
                "upsertProjectMembers",
            )


class TestProjectResponseOpenApiContract:
    def _full_project(self, **overrides: Any) -> dict[str, Any]:
        project = {
            "_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
            "orgId": "bbbbbbbbbbbbbbbbbbbbbbbb",
            "userId": "cccccccccccccccccccccccc",
            "name": "Q3 Plan",
            "description": "d",
            "instructions": "i",
            "files": [
                {
                    "recordId": "r1",
                    "recordName": "doc.pdf",
                    "sizeBytes": 100,
                    "uploadedBy": "cccccccccccccccccccccccc",
                    "uploadedAt": "2024-01-01T00:00:00Z",
                }
            ],
            "visibility": "private",
            "chatSharing": "private",
            "members": [
                {
                    "principalType": "user",
                    "principalId": "dddddddddddddddddddddddd",
                    "role": "viewer",
                    "addedBy": "cccccccccccccccccccccccc",
                    "addedAt": "2024-01-01T00:00:00Z",
                }
            ],
            "isPinned": False,
            "isArchived": False,
            "isDeleted": False,
            "lastActivityAt": 1700000000000,
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }
        project.update(overrides)
        return project

    def test_create_project_response(self) -> None:
        assert_response_matches_openapi_operation(
            {"project": self._full_project()}, "createProject", status_code="201"
        )

    def test_get_project_by_id_response_includes_role(self) -> None:
        assert_response_matches_openapi_operation(
            {"project": self._full_project(role="owner")}, "getProjectById"
        )

    def test_list_projects_response(self) -> None:
        assert_response_matches_openapi_operation(
            {
                "projects": [self._full_project(role="owner", conversationCount=3)],
                "pagination": {"page": 1, "limit": 20, "totalCount": 1, "totalPages": 1},
            },
            "listProjects",
        )

    def test_ensure_project_knowledge_base_response(self) -> None:
        # Files go into the project's hidden Collection; there is no files route.
        assert_response_matches_openapi_operation(
            {"kbId": "eeeeeeeeeeeeeeeeeeeeeeee"}, "ensureProjectKnowledgeBase"
        )

    def test_list_project_members_response(self) -> None:
        assert_response_matches_openapi_operation(
            {
                "members": [
                    {
                        "principalType": "user",
                        "principalId": "dddddddddddddddddddddddd",
                        "role": "editor",
                        "addedBy": "cccccccccccccccccccccccc",
                        "addedAt": "2024-01-01T00:00:00Z",
                    }
                ]
            },
            "listProjectMembers",
        )

    def test_set_conversation_project_response(self) -> None:
        assert_response_matches_openapi_operation(
            {
                "conversationId": "aaaaaaaaaaaaaaaaaaaaaaaa",
                "projectId": "bbbbbbbbbbbbbbbbbbbbbbbb",
                "projectVisibility": "private",
            },
            "setConversationProject",
        )

    def test_set_conversation_project_response_when_unlinked(self) -> None:
        assert_response_matches_openapi_operation(
            {"conversationId": "aaaaaaaaaaaaaaaaaaaaaaaa", "projectId": None, "projectVisibility": None},
            "setConversationProject",
        )


# ---------------------------------------------------------------------------
# Part 2 — live integration tests (@pytest.mark.integration; need a running stack)
# ---------------------------------------------------------------------------


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


class ProjectTestBase:
    @pytest.fixture(autouse=True)
    def _setup(
        self,
        projects_client: ProjectsClient,
        conversations_client: ConversationsClient,
    ) -> None:
        self.projects = projects_client
        self.conversations = conversations_client
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))

    def _create_project(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("name", f"it-project-{uuid4().hex[:8]}")
        resp = self.projects.create_project(timeout=self.timeout, **kwargs)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        project = body.get("project")
        assert isinstance(project, dict) and project.get("_id"), (
            f"createProject response missing project._id: {body!r}"
        )
        return project

    @pytest.fixture
    def created_project(self) -> Iterator[dict[str, Any]]:
        project = self._create_project()
        try:
            yield project
        finally:
            try:
                self.projects.delete_project(project["_id"])
            except Exception:
                pass


@pytest.mark.integration
class TestProjectCrudLifecycle(ProjectTestBase):
    def test_create_get_update_archive_pin_delete(self) -> None:
        project = self._create_project(
            description="lifecycle test", instructions="Cite sources."
        )
        project_id = project["_id"]
        try:
            get_resp = self.projects.get_project(project_id)
            assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"
            fetched = _response_json(get_resp)["project"]
            assert fetched["role"] == "owner"
            assert fetched["instructions"] == "Cite sources."

            update_resp = self.projects.update_project(project_id, name="Renamed Project")
            assert update_resp.status_code == 200, f"{update_resp.status_code}: {update_resp.text}"
            assert _response_json(update_resp)["project"]["name"] == "Renamed Project"

            pin_resp = self.projects.pin_project(project_id)
            assert pin_resp.status_code == 200, f"{pin_resp.status_code}: {pin_resp.text}"
            assert _response_json(pin_resp)["project"]["isPinned"] is True

            unpin_resp = self.projects.unpin_project(project_id)
            assert _response_json(unpin_resp)["project"]["isPinned"] is False

            archive_resp = self.projects.archive_project(project_id)
            assert archive_resp.status_code == 200, f"{archive_resp.status_code}: {archive_resp.text}"
            assert _response_json(archive_resp)["project"]["isArchived"] is True

            unarchive_resp = self.projects.unarchive_project(project_id)
            assert _response_json(unarchive_resp)["project"]["isArchived"] is False
        finally:
            delete_resp = self.projects.delete_project(project_id)
            assert delete_resp.status_code == 200, f"{delete_resp.status_code}: {delete_resp.text}"

        # Deleted projects are invisible — 404, not 200-with-isDeleted.
        after_delete = self.projects.get_project(project_id)
        assert after_delete.status_code == 404, (
            f"expected 404 for a deleted project, got {after_delete.status_code}: {after_delete.text}"
        )

        # Idempotent: deleting again still returns 200.
        second_delete = self.projects.delete_project(project_id)
        assert second_delete.status_code == 200, (
            f"expected idempotent delete, got {second_delete.status_code}: {second_delete.text}"
        )

    def test_list_projects_includes_created_project_with_role_and_conversation_count(
        self, created_project: dict[str, Any]
    ) -> None:
        resp = self.projects.list_projects(scope="mine", limit=100)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = _response_json(resp)
        rows = {p["_id"]: p for p in body.get("projects", [])}
        assert created_project["_id"] in rows, (
            f"created project not found in listProjects(scope=mine): {sorted(rows)}"
        )
        row = rows[created_project["_id"]]
        assert row["role"] == "owner"
        assert row["conversationCount"] == 0

    def test_get_nonexistent_project_returns_404(self) -> None:
        resp = self.projects.get_project("aaaaaaaaaaaaaaaaaaaaaaaa")
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestProjectAccessMatrix(ProjectTestBase):
    """A second, non-admin identity distinguishes "enforced" from "ignored"."""

    def test_non_member_gets_404_not_403(
        self, created_project: dict[str, Any], second_user: SecondUser
    ) -> None:
        # 404 (never 403) on a project the caller cannot see — avoids leaking
        # project existence across an org/user boundary.
        resp = requests.get(
            f"{second_user.base_url}/api/v1/projects/{created_project['_id']}",
            headers=second_user.headers,
            timeout=second_user.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_viewer_member_can_read_but_not_update(
        self, created_project: dict[str, Any], second_user: SecondUser
    ) -> None:
        project_id = created_project["_id"]
        upsert_resp = self.projects.upsert_members(
            project_id, [{"principalId": second_user.user_id, "role": "viewer"}]
        )
        assert upsert_resp.status_code == 200, f"{upsert_resp.status_code}: {upsert_resp.text}"

        get_resp = requests.get(
            f"{second_user.base_url}/api/v1/projects/{project_id}",
            headers=second_user.headers,
            timeout=second_user.timeout,
        )
        assert get_resp.status_code == 200, f"{get_resp.status_code}: {get_resp.text}"
        assert _response_json(get_resp)["project"]["role"] == "viewer"

        # A viewer can see the project, so an editor-level action is 403, not 404.
        patch_resp = requests.patch(
            f"{second_user.base_url}/api/v1/projects/{project_id}",
            headers=second_user.headers,
            json={"name": "hijacked"},
            timeout=second_user.timeout,
        )
        assert patch_resp.status_code == 403, f"{patch_resp.status_code}: {patch_resp.text}"
        unchanged = _response_json(get_resp)["project"]["name"]
        refetched = _response_json(self.projects.get_project(project_id))["project"]["name"]
        assert refetched == unchanged, f"a viewer's update was applied: {refetched!r}"

    def test_editor_member_cannot_manage_members_or_change_visibility(
        self, created_project: dict[str, Any], second_user: SecondUser
    ) -> None:
        project_id = created_project["_id"]
        self.projects.upsert_members(
            project_id, [{"principalId": second_user.user_id, "role": "editor"}]
        )

        # Editors may update ordinary metadata...
        ok_resp = requests.patch(
            f"{second_user.base_url}/api/v1/projects/{project_id}",
            headers=second_user.headers,
            json={"description": "edited by an editor"},
            timeout=second_user.timeout,
        )
        assert ok_resp.status_code == 200, f"{ok_resp.status_code}: {ok_resp.text}"

        # ...but not the owner-only visibility/chatSharing fields.
        forbidden_resp = requests.patch(
            f"{second_user.base_url}/api/v1/projects/{project_id}",
            headers=second_user.headers,
            json={"visibility": "org"},
            timeout=second_user.timeout,
        )
        assert forbidden_resp.status_code == 403, (
            f"{forbidden_resp.status_code}: {forbidden_resp.text}"
        )

        # Nor manage members (owner-only).
        forbidden_member_resp = requests.put(
            f"{second_user.base_url}/api/v1/projects/{project_id}/members",
            headers=second_user.headers,
            json={"members": [{"principalId": second_user.user_id, "role": "editor"}]},
            timeout=second_user.timeout,
        )
        assert forbidden_member_resp.status_code == 403, (
            f"{forbidden_member_resp.status_code}: {forbidden_member_resp.text}"
        )

    def test_upsert_members_rejects_unknown_iam_user(
        self, created_project: dict[str, Any]
    ) -> None:
        resp = self.projects.upsert_members(
            created_project["_id"],
            [{"principalId": "ffffffffffffffffffffffff", "role": "viewer"}],
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"

    def test_remove_member_revokes_access(
        self, created_project: dict[str, Any], second_user: SecondUser
    ) -> None:
        project_id = created_project["_id"]
        self.projects.upsert_members(
            project_id, [{"principalId": second_user.user_id, "role": "viewer"}]
        )
        remove_resp = self.projects.remove_member(project_id, second_user.user_id)
        assert remove_resp.status_code == 200, f"{remove_resp.status_code}: {remove_resp.text}"

        get_resp = requests.get(
            f"{second_user.base_url}/api/v1/projects/{project_id}",
            headers=second_user.headers,
            timeout=second_user.timeout,
        )
        assert get_resp.status_code == 404, f"{get_resp.status_code}: {get_resp.text}"


@pytest.mark.integration
class TestProjectFileLifecycle(ProjectTestBase):
    """Project files live in the project's hidden Collection, as in the UI's Files card."""

    def test_files_upload_into_the_projects_knowledge_base(
        self, created_project: dict[str, Any], kb_client: KBClient
    ) -> None:
        project_id = created_project["_id"]

        ensure_resp = self.projects.ensure_knowledge_base(project_id, timeout=self.timeout)
        assert ensure_resp.status_code == 200, f"{ensure_resp.status_code}: {ensure_resp.text}"
        kb_id = _response_json(ensure_resp).get("kbId")
        assert isinstance(kb_id, str) and kb_id, f"ensureKnowledgeBase returned no kbId: {ensure_resp.text}"

        again = self.projects.ensure_knowledge_base(project_id, timeout=self.timeout)
        assert again.status_code == 200, f"{again.status_code}: {again.text}"
        assert _response_json(again)["kbId"] == kb_id, "a second call must reuse the same Collection"

        project = _response_json(self.projects.get_project(project_id))["project"]
        assert project.get("linkedKnowledgeBaseId") == kb_id, (
            f"project not linked to its Collection: {project.get('linkedKnowledgeBaseId')!r} != {kb_id!r}"
        )

        uploaded = kb_client.upload_file(
            kb_id, f"it-project-{uuid4().hex[:8]}.txt", b"Project file for the integration test.\n"
        )
        records = uploaded.get("records") or []
        assert len(records) == 1 and records[0].get("recordId"), f"upload returned no record: {uploaded!r}"
        record = kb_client.get_record(records[0]["recordId"])
        assert record, f"uploaded record {records[0]['recordId']} could not be fetched"


@pytest.mark.integration
class TestProjectConversationLinking(ProjectTestBase):
    def test_link_and_list_conversation_in_project(
        self, created_project: dict[str, Any]
    ) -> None:
        project_id = created_project["_id"]
        create_resp = self.conversations.stream_conversation(
            json={"query": "hello", "chatMode": "internal_search"},
            stream=False,
            timeout=self.timeout,
        )
        assert create_resp.status_code == 200, f"{create_resp.status_code}: {create_resp.text}"

        # `stream_conversation` normally streams SSE; when `stream=False` the
        # server still returns the full event-stream body as plain text, so
        # pull the conversation id straight from the persisted list instead
        # of parsing SSE frames here (kept intentionally simple — the SSE
        # parsing path itself is covered by integration_test_conversation.py).
        list_resp = self.conversations.list_conversations(
            source="owned", limit=1, timeout=self.timeout
        )
        assert list_resp.status_code == 200, f"{list_resp.status_code}: {list_resp.text}"
        conversations = _response_json(list_resp).get("conversations", [])
        assert conversations, "expected at least one conversation after streaming"
        conversation_id = conversations[0]["_id"]

        link_resp = self.conversations.set_project(conversation_id, project_id)
        assert link_resp.status_code == 200, f"{link_resp.status_code}: {link_resp.text}"
        link_body = _response_json(link_resp)
        assert link_body["projectId"] == project_id

        list_project_conv_resp = self.projects.list_project_conversations(project_id)
        assert list_project_conv_resp.status_code == 200, (
            f"{list_project_conv_resp.status_code}: {list_project_conv_resp.text}"
        )
        project_conv_ids = {
            c["_id"] for c in _response_json(list_project_conv_resp).get("conversations", [])
        }
        assert conversation_id in project_conv_ids, (
            f"linked conversation not returned by getProjectConversations: {project_conv_ids}"
        )

        unlink_resp = self.conversations.set_project(conversation_id, None)
        assert unlink_resp.status_code == 200, f"{unlink_resp.status_code}: {unlink_resp.text}"
        assert _response_json(unlink_resp)["projectId"] is None

    def test_link_to_project_caller_cannot_see_returns_404(self) -> None:
        create_resp = self.conversations.stream_conversation(
            json={"query": "hello", "chatMode": "internal_search"},
            stream=False,
            timeout=self.timeout,
        )
        assert create_resp.status_code == 200, f"{create_resp.status_code}: {create_resp.text}"
        list_resp = self.conversations.list_conversations(
            source="owned", limit=1, timeout=self.timeout
        )
        conversation_id = _response_json(list_resp)["conversations"][0]["_id"]

        resp = self.conversations.set_project(conversation_id, "aaaaaaaaaaaaaaaaaaaaaaaa")
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestProjectScopedStream(ProjectTestBase):
    def test_new_conversation_with_project_id_is_linked_on_creation(
        self, created_project: dict[str, Any]
    ) -> None:
        project_id = created_project["_id"]
        create_resp = self.conversations.stream_conversation(
            json={
                "query": "hello from a project-scoped chat",
                "chatMode": "internal_search",
                "projectId": project_id,
            },
            stream=False,
            timeout=self.timeout,
        )
        assert create_resp.status_code == 200, f"{create_resp.status_code}: {create_resp.text}"

        list_project_conv_resp = self.projects.list_project_conversations(project_id)
        assert list_project_conv_resp.status_code == 200, (
            f"{list_project_conv_resp.status_code}: {list_project_conv_resp.text}"
        )
        rows = _response_json(list_project_conv_resp).get("conversations", [])
        assert rows, (
            "expected the newly streamed conversation to already be linked to "
            "the project (Node links it inside the create transaction, before "
            "the AI payload is even sent)"
        )

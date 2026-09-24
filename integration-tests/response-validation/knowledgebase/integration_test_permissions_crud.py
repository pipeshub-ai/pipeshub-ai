from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import assert_response_matches_openapi_operation  # noqa: E402

_NONEXISTENT_KB_ID = "00000000-0000-4000-8000-000000000001"


def _user_id(user: dict[str, object]) -> str:
    """Mongo userId for KB permission request body ``userIds``."""
    uid = user.get("_id") or user.get("id")
    assert uid, f"User object has no id: {user}"
    return str(uid)


def _graph_user_id(user: dict[str, object]) -> str:
    """Graph user document id echoed in KB permission response ``userIds``."""
    graph_id = user.get("graphId")
    assert graph_id, (
        f"User missing graphId (fixture should wait for graph sync): {user}"
    )
    return str(graph_id)


def _team_id(team: dict[str, object]) -> str:
    """Mongo _id from createUserGroup — used in negative validation cases."""
    tid = team.get("_id") or team.get("id")
    assert tid, f"Team object has no id: {team}"
    return str(tid)


def _permission_team_id(team: dict[str, object]) -> str:
    """Graph team document id (teams._key) for KB permission create/delete teamIds body."""
    graph_id = team.get("graphId")
    assert graph_id, (
        f"Team missing graphId (fixture should create graph team): {team}"
    )
    return str(graph_id)


def _permissions_url(base_url: str, kb_id: str) -> str:
    return f"{base_url}/api/v1/knowledgeBase/{kb_id}/permissions"


def _granted_count(create_body: dict[str, object]) -> int:
    result = create_body.get("permissionResult") or {}
    count = result.get("grantedCount")
    return int(count) if count is not None else 0


def _list_permissions(
    base_url: str, headers: dict[str, str], kb_id: str, *, timeout: int
) -> list[dict[str, object]]:
    resp = requests.get(
        _permissions_url(base_url, kb_id), headers=headers, timeout=timeout
    )
    assert resp.status_code == 200, f"listKBPermissions failed: {resp.text}"
    return list(resp.json().get("permissions") or [])


# Sentinel: the KB lists a permission for this user but reports no role.
_ROLE_PRESENT_BUT_UNSET = "<present-without-role>"


def _granted_role(
    base_url: str,
    headers: dict[str, str],
    kb_id: str,
    graph_user_id: str,
    *,
    timeout: int,
) -> str | None:
    """The role this KB *actually* records for a user, or None if absent.

    The mutation endpoints answer with the role that was requested, so asserting
    on their response only proves the request was echoed. A provider write can
    fail and still be reported as success (a failure dict is truthy), which the
    response-shape assertions cannot see. Reading the state back is what makes
    these tests able to fail.
    """
    for entry in _list_permissions(base_url, headers, kb_id, timeout=timeout):
        if entry.get("type") != "USER":
            continue
        if str(entry.get("id") or "") == graph_user_id:
            # A surviving row with a null role is NOT the same as no row: the
            # delete test asserts the permission is gone, and collapsing both
            # to None would let a still-present permission pass it.
            role = entry.get("role")
            return str(role) if role is not None else _ROLE_PRESENT_BUT_UNSET
    return None


def _remove_permission(
    base_url: str,
    headers: dict[str, str],
    kb_id: str,
    *,
    user_ids: list[str] | None = None,
    team_ids: list[str] | None = None,
    timeout: int,
) -> None:
    body = {
        "userIds": user_ids or [],
        "teamIds": team_ids or [],
    }
    if not body["userIds"] and not body["teamIds"]:
        return
    try:
        requests.delete(
            _permissions_url(base_url, kb_id),
            headers=headers,
            json=body,
            timeout=timeout,
        )
    except requests.RequestException:
        pass


@pytest.mark.integration
class TestKBPermissionCreate:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.base_url = self.client.base_url
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def test_create_kb_permission_success_user(
        self,
        six_kb_records: dict[str, object],
        four_new_users: list[dict[str, object]],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        grantee = four_new_users[0]
        grantee_id = _user_id(grantee)
        grantee_graph_id = _graph_user_id(grantee)

        resp = requests.post(
            _permissions_url(self.base_url, kb_id),
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": [], "role": "READER"},
            timeout=self.client.timeout_seconds,
        )
        try:
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(
                body, "createKBPermission", status_code="201"
            )
            assert body["kbId"] == kb_id
            assert body.get("permissionResult") is not None
            assert _granted_count(body) >= 1, (
                "Permission create returned grantedCount=0 — grantee Mongo userId "
                "not found in graph yet"
            )

            assert _granted_role(
                self.base_url, self.headers, kb_id, grantee_graph_id,
                timeout=self.client.timeout_seconds,
            ) == "READER", (
                "createKBPermission reported success but the knowledge base does "
                "not list the grantee with that role."
            )
        finally:
            _remove_permission(
                self.base_url,
                self.headers,
                kb_id,
                user_ids=[grantee_id],
                timeout=self.client.timeout_seconds,
            )

    def test_create_kb_permission_success_team(
        self,
        six_kb_records: dict[str, object],
        new_team: dict[str, object],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        team_id = _permission_team_id(new_team)

        resp = requests.post(
            _permissions_url(self.base_url, kb_id),
            headers=self.headers,
            json={"userIds": [], "teamIds": [team_id]},
            timeout=self.client.timeout_seconds,
        )
        try:
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(
                body, "createKBPermission", status_code="201"
            )
            assert body["kbId"] == kb_id
            assert body.get("permissionResult") is not None
        finally:
            _remove_permission(
                self.base_url,
                self.headers,
                kb_id,
                team_ids=[team_id],
                timeout=self.client.timeout_seconds,
            )

    def test_create_kb_permission_negative(
        self,
        six_kb_records: dict[str, object],
        four_new_users: list[dict[str, object]],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        grantee_id = _user_id(four_new_users[0])
        perms_url = _permissions_url(self.base_url, kb_id)
        valid_body = {"userIds": [grantee_id], "teamIds": [], "role": "READER"}

        resp = requests.post(
            perms_url,
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="400"
        )

        resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="400"
        )

        resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [], "teamIds": []},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="400"
        )

        resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "role": "INVALID"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="400"
        )

        resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "role": "COMMENTER"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="400"
        )

        resp = requests.post(
            _permissions_url(self.base_url, "not-a-uuid"),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="400"
        )

        resp = requests.post(
            _permissions_url(self.base_url, _NONEXISTENT_KB_ID),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="404"
        )

        resp = requests.post(
            perms_url,
            headers={"Content-Type": "application/json"},
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="401"
        )

        resp = requests.post(
            perms_url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKBPermission", status_code="401"
        )


@pytest.mark.integration
class TestKBPermissionList:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.base_url = self.client.base_url
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def test_list_kb_permissions_success(
        self,
        six_kb_records: dict[str, object],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])

        resp = requests.get(
            _permissions_url(self.base_url, kb_id),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listKBPermissions")
        assert body["kbId"] == kb_id
        assert isinstance(body["permissions"], list)
        assert "totalCount" in body

    def test_list_kb_permissions_negative(self) -> None:
        # Double-slash URL collapses to /knowledgeBase/permissions, so kbId is
        # "permissions" — Zod min(1) passes and the connector returns 404.
        resp = requests.get(
            _permissions_url(self.base_url, ""),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKBPermissions", status_code="404"
        )

        # Non-uuid kbId passes Zod (min length only); connector returns 404.
        resp = requests.get(
            _permissions_url(self.base_url, "not-a-uuid"),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKBPermissions", status_code="404"
        )

        resp = requests.get(
            _permissions_url(self.base_url, _NONEXISTENT_KB_ID),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKBPermissions", status_code="404"
        )

        resp = requests.get(
            _permissions_url(self.base_url, _NONEXISTENT_KB_ID),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKBPermissions", status_code="401"
        )

        resp = requests.get(
            _permissions_url(self.base_url, _NONEXISTENT_KB_ID),
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKBPermissions", status_code="401"
        )


@pytest.mark.integration
class TestKBPermissionUpdate:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.base_url = self.client.base_url
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def test_update_kb_permission_success_user(
        self,
        six_kb_records: dict[str, object],
        four_new_users: list[dict[str, object]],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        grantee = four_new_users[0]
        grantee_id = _user_id(grantee)
        grantee_graph_id = _graph_user_id(grantee)
        perms_url = _permissions_url(self.base_url, kb_id)

        create_resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": [], "role": "READER"},
            timeout=self.client.timeout_seconds,
        )
        try:
            assert create_resp.status_code == 201, create_resp.text
            assert _granted_count(create_resp.json()) >= 1, (
                "Permission create returned grantedCount=0 — grantee Mongo userId "
                "not found in graph yet"
            )

            resp = requests.put(
                perms_url,
                headers=self.headers,
                json={"userIds": [grantee_id], "teamIds": [], "role": "WRITER"},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateKBPermissions")
            assert body["kbId"] == kb_id
            assert grantee_graph_id in body["userIds"]
            assert body.get("teamIds") == []
            assert body["newRole"] == "WRITER"

            assert _granted_role(
                self.base_url, self.headers, kb_id, grantee_graph_id,
                timeout=self.client.timeout_seconds,
            ) == "WRITER", (
                "updateKBPermissions reported success but the knowledge base still "
                "records a different role for this user."
            )
        finally:
            _remove_permission(
                self.base_url,
                self.headers,
                kb_id,
                user_ids=[grantee_id],
                timeout=self.client.timeout_seconds,
            )

    def test_update_kb_permission_negative(
        self,
        six_kb_records: dict[str, object],
        four_new_users: list[dict[str, object]],
        new_team: dict[str, object],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        grantee_id = _user_id(four_new_users[0])
        no_permission_user_id = _user_id(four_new_users[1])
        team_id = _permission_team_id(new_team)
        perms_url = _permissions_url(self.base_url, kb_id)
        valid_body = {
            "userIds": [grantee_id],
            "teamIds": [],
            "role": "WRITER",
        }

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": []},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={"userIds": [], "teamIds": [], "role": "WRITER"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={
                "userIds": [grantee_id],
                "teamIds": [team_id],
                "role": "WRITER",
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": [], "role": "INVALID"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": [], "role": "COMMENTER"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            _permissions_url(self.base_url, "not-a-uuid"),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="400"
        )

        resp = requests.put(
            _permissions_url(self.base_url, _NONEXISTENT_KB_ID),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="404"
        )

        resp = requests.put(
            perms_url,
            headers=self.headers,
            json={
                "userIds": [no_permission_user_id],
                "teamIds": [],
                "role": "WRITER",
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="404"
        )

        resp = requests.put(
            perms_url,
            headers={"Content-Type": "application/json"},
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="401"
        )

        resp = requests.put(
            perms_url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateKBPermissions", status_code="401"
        )


@pytest.mark.integration
class TestKBPermissionDelete:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.base_url = self.client.base_url
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def test_delete_kb_permission_success_user(
        self,
        six_kb_records: dict[str, object],
        four_new_users: list[dict[str, object]],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        grantee = four_new_users[0]
        grantee_id = _user_id(grantee)
        grantee_graph_id = _graph_user_id(grantee)
        perms_url = _permissions_url(self.base_url, kb_id)

        create_resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": [], "role": "READER"},
            timeout=self.client.timeout_seconds,
        )
        try:
            assert create_resp.status_code == 201, create_resp.text
            assert _granted_count(create_resp.json()) >= 1, (
                "Permission create returned grantedCount=0 — grantee Mongo userId "
                "not found in graph yet"
            )

            resp = requests.delete(
                perms_url,
                headers=self.headers,
                json={"userIds": [grantee_id], "teamIds": []},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(body, "deleteKBPermissions")
            assert body["kbId"] == kb_id
            assert grantee_graph_id in body["userIds"]
            assert body.get("teamIds") == []

            assert _granted_role(
                self.base_url, self.headers, kb_id, grantee_graph_id,
                timeout=self.client.timeout_seconds,
            ) is None, (
                "deleteKBPermissions reported success but the knowledge base still "
                "lists a permission for this user."
            )
        finally:
            _remove_permission(
                self.base_url,
                self.headers,
                kb_id,
                user_ids=[grantee_id],
                timeout=self.client.timeout_seconds,
            )

    def test_delete_kb_permission_success_team(
        self,
        six_kb_records: dict[str, object],
        new_team: dict[str, object],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        team_id = _permission_team_id(new_team)
        perms_url = _permissions_url(self.base_url, kb_id)

        create_resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [], "teamIds": [team_id]},
            timeout=self.client.timeout_seconds,
        )
        try:
            assert create_resp.status_code == 201, create_resp.text
            assert create_resp.json().get("permissionResult") is not None

            resp = requests.delete(
                perms_url,
                headers=self.headers,
                json={"userIds": [], "teamIds": [team_id]},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(body, "deleteKBPermissions")
            assert body["kbId"] == kb_id
            assert body.get("userIds") == []
            assert team_id in body["teamIds"]
        finally:
            _remove_permission(
                self.base_url,
                self.headers,
                kb_id,
                team_ids=[team_id],
                timeout=self.client.timeout_seconds,
            )

    def test_delete_kb_permission_negative(
        self,
        six_kb_records: dict[str, object],
        four_new_users: list[dict[str, object]],
        new_team: dict[str, object],
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        grantee_id = _user_id(four_new_users[0])
        no_permission_user_id = _user_id(four_new_users[1])
        team_id = _permission_team_id(new_team)
        perms_url = _permissions_url(self.base_url, kb_id)
        valid_body = {
            "userIds": [grantee_id],
            "teamIds": [],
        }

        resp = requests.delete(
            perms_url,
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        # deletePermissionsSchema allows empty body; controller reads .length on
        # undefined userIds/teamIds before its empty-array guard.
        assert resp.status_code == 500, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="500"
        )

        resp = requests.delete(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 500, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="500"
        )

        resp = requests.delete(
            perms_url,
            headers=self.headers,
            json={"userIds": [], "teamIds": []},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="400"
        )

        resp = requests.delete(
            _permissions_url(self.base_url, "not-a-uuid"),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="400"
        )

        resp = requests.delete(
            _permissions_url(self.base_url, _NONEXISTENT_KB_ID),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="404"
        )

        resp = requests.delete(
            perms_url,
            headers=self.headers,
            json={
                "userIds": [no_permission_user_id],
                "teamIds": [],
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="404"
        )

        resp = requests.delete(
            perms_url,
            headers={"Content-Type": "application/json"},
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="401"
        )

        resp = requests.delete(
            perms_url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKBPermissions", status_code="401"
        )

        create_resp = requests.post(
            perms_url,
            headers=self.headers,
            json={"userIds": [grantee_id], "teamIds": [], "role": "READER"},
            timeout=self.client.timeout_seconds,
        )
        try:
            assert create_resp.status_code == 201, create_resp.text
            assert _granted_count(create_resp.json()) >= 1, create_resp.text

            resp = requests.delete(
                perms_url,
                headers=self.headers,
                json={"userIds": [grantee_id], "teamIds": []},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, resp.text

            resp = requests.delete(
                perms_url,
                headers=self.headers,
                json={"userIds": [], "teamIds": [team_id]},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 404, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "deleteKBPermissions", status_code="404"
            )
        finally:
            _remove_permission(
                self.base_url,
                self.headers,
                kb_id,
                user_ids=[grantee_id],
                timeout=self.client.timeout_seconds,
            )

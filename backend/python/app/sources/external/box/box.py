import json
from typing import Any, Dict, Optional

from app.sources.client.box.box import BoxClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse


class BoxDataSource:
    def __init__(self, client: BoxClient) -> None:
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self._client.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def _auth_headers(self, headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: Dict[str, Any] = dict(headers or {})
        _headers["Authorization"] = f"Bearer {self._client.access_token}"
        return _headers

    def get_data_source(self) -> "BoxDataSource":
        return self

    # ---------------- Core Requests ----------------
    async def _request(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        body: Any = None,
        multipart: dict = None,
    ) -> HTTPResponse:
        url = f"{self.base_url}{endpoint}"
        req_kwargs = dict(
            method=method,
            url=url,
            headers=self._auth_headers(headers),
            path_params={},
            query_params=query_params or {},
        )
        if multipart:
            req_kwargs["multipart"] = multipart
        else:
            req_kwargs["body"] = body
        req = HTTPRequest(**req_kwargs)
        return await self._client.execute(req)

    # ---------------- Users ----------------
    async def get_user_info(self, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", "/users/me", headers)

    async def list_users(self, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", "/users", headers)

    async def get_user(self, user_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/users/{user_id}", headers)

    async def update_user(self, user_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/users/{user_id}", headers, body=data)

    # ---------------- Folders ----------------
    async def list_folder_items(self, folder_id: str = "0", headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/folders/{folder_id}/items", headers)

    async def create_folder(self, name: str, parent_id: str = "0", headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"name": name, "parent": {"id": parent_id}}
        return await self._request("POST", "/folders", headers, body=body)

    async def get_folder_info(self, folder_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/folders/{folder_id}", headers)

    async def update_folder(self, folder_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/folders/{folder_id}", headers, body=data)

    async def delete_folder(self, folder_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/folders/{folder_id}", headers)

    async def copy_folder(self, folder_id: str, parent_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"parent": {"id": parent_id}}
        return await self._request("POST", f"/folders/{folder_id}/copy", headers, body=body)

    async def move_folder(self, folder_id: str, parent_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"parent": {"id": parent_id}}
        return await self._request("PUT", f"/folders/{folder_id}", headers, body=body)

    # ---------------- Files ----------------
    async def upload_file(
        self,
        folder_id: str,
        file_name: str,
        file_content: bytes,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        url = f"{self.base_url}/files/content"
        multipart = {
            "attributes": json.dumps({
                "name": file_name,
                "parent": {"id": folder_id}
            }),
            # This matches the (filename, bytes, content_type) convention in HTTPClient
            "file": (file_name, file_content, "application/octet-stream"),
        }

        req = HTTPRequest(
            method="POST",
            url=url,
            headers=self._auth_headers(headers),
            multipart=multipart,
        )
        return await self._client.execute(req)

    # ---------------- Create Collaboration (missing in original) ----------------
    async def create_collaboration(self, folder_id: str, accessible_by: dict, role: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {
            "item": {"type": "folder", "id": folder_id},
            "accessible_by": accessible_by,
            "role": role
        }
        return await self._request("POST", "/collaborations", headers, body=body)

    # ---------------- Helper to extract response body ----------------
    @staticmethod
    def extract_body(response: HTTPResponse):
        """
        Usage:
            resp = await data_source.get_user_info()
            print(BoxDataSource.extract_body(resp))
            # Or pretty:
            # import json; print(json.dumps(BoxDataSource.extract_body(resp), indent=2))
        """
        if hasattr(response, 'json') and response.is_json:
            try:
                return response.json()
            except Exception:
                return response.text()
        try:
            return response.text()
        except Exception:
            return response.bytes()

    async def download_file(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/files/{file_id}/content", headers)

    async def delete_file(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/files/{file_id}", headers)

    async def get_file_info(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/files/{file_id}", headers)

    async def update_file(self, file_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/files/{file_id}", headers, body=data)

    async def copy_file(self, file_id: str, parent_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"parent": {"id": parent_id}}
        return await self._request("POST", f"/files/{file_id}/copy", headers, body=body)

    async def move_file(self, file_id: str, parent_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"parent": {"id": parent_id}}
        return await self._request("PUT", f"/files/{file_id}", headers, body=body)

    async def lock_file(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"lock": {"type": "lock"}}
        return await self._request("PUT", f"/files/{file_id}", headers, body=body)

    async def unlock_file(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"lock": None}
        return await self._request("PUT", f"/files/{file_id}", headers, body=body)

    # ---------------- Collaborations ----------------
    async def add_collaborator(self, item_id: str, item_type: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"item": {"id": item_id, "type": item_type}, **data}
        return await self._request("POST", "/collaborations", headers, body=body)

    async def update_collaborator(self, collaboration_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/collaborations/{collaboration_id}", headers, body=data)

    async def remove_collaborator(self, collaboration_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/collaborations/{collaboration_id}", headers)

    # ---------------- Comments ----------------
    async def add_comment(self, item_id: str, item_type: str, message: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"item": {"id": item_id, "type": item_type}, "message": message}
        return await self._request("POST", "/comments", headers, body=body)

    async def get_comment(self, comment_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/comments/{comment_id}", headers)

    async def delete_comment(self, comment_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/comments/{comment_id}", headers)

    # ---------------- Tasks ----------------
    async def create_task(self, item_id: str, item_type: str, action: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"item": {"id": item_id, "type": item_type}, "action": action}
        return await self._request("POST", "/tasks", headers, body=body)

    async def get_task(self, task_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/tasks/{task_id}", headers)

    async def update_task(self, task_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/tasks/{task_id}", headers, body=data)

    async def delete_task(self, task_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/tasks/{task_id}", headers)

    # ---------------- Webhooks ----------------
    async def create_webhook(self, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("POST", "/webhooks", headers, body=data)

    async def get_webhook(self, webhook_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/webhooks/{webhook_id}", headers)

    async def update_webhook(self, webhook_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/webhooks/{webhook_id}", headers, body=data)

    async def delete_webhook(self, webhook_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/webhooks/{webhook_id}", headers)

    # ---------------- Metadata ----------------
    async def get_metadata(self, file_id: str, scope: str, template_key: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/files/{file_id}/metadata/{scope}/{template_key}", headers)

    async def create_metadata(self, file_id: str, scope: str, template_key: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("POST", f"/files/{file_id}/metadata/{scope}/{template_key}", headers, body=data)

    async def update_metadata(self, file_id: str, scope: str, template_key: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/files/{file_id}/metadata/{scope}/{template_key}", headers, body=data)

    async def delete_metadata(self, file_id: str, scope: str, template_key: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/files/{file_id}/metadata/{scope}/{template_key}", headers)

    # ---------------- Search ----------------
    async def search(self, query: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", "/search", headers, query_params={"query": query})

    # ---------------- Groups ----------------
    async def list_groups(self, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", "/groups", headers)

    async def get_group(self, group_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/groups/{group_id}", headers)

    # ---------------- Collections ----------------
    async def list_collections(self, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", "/collections", headers)

    # ---------------- Shared Links ----------------
    async def create_shared_link(self, file_id: str, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("PUT", f"/files/{file_id}", headers, body=data)

    # ---------------- Events ----------------
    async def get_events(self, stream_position: str = "now", headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", "/events", headers, query_params={"stream_position": stream_position})

    # ---------------- Retention ----------------
    async def get_retention_policy(self, policy_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/retention_policies/{policy_id}", headers)

    # ---------------- Legal Holds ----------------
    async def get_legal_hold(self, legal_hold_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/legal_holds/{legal_hold_id}", headers)

    # ---------------- Watermarking ----------------
    async def apply_watermark(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        body = {"watermark": {"imprint": "default"}}
        return await self._request("PUT", f"/files/{file_id}/watermark", headers, body=body)

    async def remove_watermark(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("DELETE", f"/files/{file_id}/watermark", headers)

    # ---------------- Skills ----------------
    async def get_skill_invocation(self, skill_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("GET", f"/skill_invocations/{skill_id}", headers)

    # ---------------- AI ----------------
    async def ai_ask_question(self, data: dict, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        return await self._request("POST", "/ai/ask", headers, body=data)

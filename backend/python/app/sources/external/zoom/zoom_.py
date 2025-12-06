# backend/python/app/sources/external/zoom/zoom_.py

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Union

from app.sources.client.zoom.zoom import ZoomRESTClientViaToken

LOG = logging.getLogger(__name__)


# ============================================================================
# RESPONSE WRAPPER
# ============================================================================
@dataclass
class ZoomResponse:
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


# ============================================================================
# DATASOURCE 
# ============================================================================
class ZoomDataSource:
    """
    High-level Zoom API wrapper.
    Public entrypoint for Zoom connector .
    """

    def __init__(self, zoom_client) -> None:
        """
        Accepts a ZoomClient instance (builder).
        Extracts internal REST client (ZoomRESTClientViaToken).
        """
        self._zoom_client = zoom_client
        self._rest_client: ZoomRESTClientViaToken = zoom_client.get_client()

    # ============================================================================
    # INTERNAL REQUEST WRAPPER
    # ============================================================================
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> ZoomResponse:
        try:
            resp = await self._rest_client.request(
                method, endpoint, params=params or {}, body=body or {}
            )
            return ZoomResponse(success=True, data=resp)
        except Exception as e:
            LOG.exception("Zoom API call failed: %s %s", method, endpoint)
            return ZoomResponse(success=False, error=str(e))

    # ============================================================================
    # USERS API
    # ============================================================================
    async def users_list(
        self,
        page_size: int = 30,
        page_number: int = 1,
        status: Optional[str] = None,
    ) -> ZoomResponse:
        params = {"page_size": page_size, "page_number": page_number}
        if status:
            params["status"] = status
        return await self._request("GET", "/users", params=params)

    async def users_get(self, user_id: str) -> ZoomResponse:
        return await self._request("GET", f"/users/{user_id}")

    async def users_create(self, body: Dict[str, object]) -> ZoomResponse:
        return await self._request("POST", "/users", body=body)

    async def users_update(self, user_id: str, body: Dict[str, object]) -> ZoomResponse:
        return await self._request("PATCH", f"/users/{user_id}", body=body)

    async def users_delete(
        self,
        user_id: str,
        action: Optional[str] = None,
        transfer_email: Optional[str] = None,
    ) -> ZoomResponse:
        params = {}
        if action:
            params["action"] = action
        if transfer_email:
            params["transfer_email"] = transfer_email
        return await self._request("DELETE", f"/users/{user_id}", params=params)

    # ============================================================================
    # MEETINGS API
    # ============================================================================
    async def create_meeting(self, user_id: str, payload: dict) -> ZoomResponse:
        return await self._request("POST", f"/users/{user_id}/meetings", body=payload)

    async def get_meeting(self, meeting_id: Union[str, int]) -> ZoomResponse:
        return await self._request("GET", f"/meetings/{meeting_id}")

    async def update_meeting(self, meeting_id: Union[str, int], payload: dict) -> ZoomResponse:
        return await self._request("PATCH", f"/meetings/{meeting_id}", body=payload)

    async def delete_meeting(self, meeting_id: Union[str, int]) -> ZoomResponse:
        return await self._request("DELETE", f"/meetings/{meeting_id}")

    async def list_meetings(
        self,
        user_id: str,
        page_size: int = 30,
        page_number: int = 1,
        meeting_type: Optional[int] = None,
    ) -> ZoomResponse:
        params = {"page_size": page_size, "page_number": page_number}
        if meeting_type is not None:
            params["type"] = meeting_type
        return await self._request("GET", f"/users/{user_id}/meetings", params=params)

    # ============================================================================
    # PARTICIPANTS / METRICS
    # ============================================================================
    async def list_participants(
        self, meeting_id: Union[str, int], params: Optional[dict] = None
    ) -> ZoomResponse:
        # Metrics API for live/past meetings
        endpoint = f"/metrics/meetings/{meeting_id}/participants"
        return await self._request("GET", endpoint, params=params)

    async def past_meeting_participants(
        self, meeting_uuid: str, page_size: int = 30, next_page_token: Optional[str] = None
    ) -> ZoomResponse:
        params = {"page_size": page_size}
        if next_page_token:
            params["next_page_token"] = next_page_token
        return await self._request("GET", f"/past_meetings/{meeting_uuid}/participants", params=params)

    async def end_meeting(self, meeting_id: Union[str, int]) -> ZoomResponse:
        return await self._request("PUT", f"/meetings/{meeting_id}/status", body={"action": "end"})

    async def add_registrant(self, meeting_id: Union[str, int], payload: dict) -> ZoomResponse:
        return await self._request("POST", f"/meetings/{meeting_id}/registrants", body=payload)

    async def list_registrants(
        self, meeting_id: Union[str, int], page_size: int = 30, page_number: int = 1
    ) -> ZoomResponse:
        params = {"page_size": page_size, "page_number": page_number}
        return await self._request("GET", f"/meetings/{meeting_id}/registrants", params=params)

    # ============================================================================
    # WEBINARS
    # ============================================================================
    async def create_webinar(self, user_id: str, payload: dict) -> ZoomResponse:
        return await self._request("POST", f"/users/{user_id}/webinars", body=payload)

    async def get_webinar(self, webinar_id: Union[str, int]) -> ZoomResponse:
        return await self._request("GET", f"/webinars/{webinar_id}")

    async def update_webinar(self, webinar_id: Union[str, int], payload: dict) -> ZoomResponse:
        return await self._request("PATCH", f"/webinars/{webinar_id}", body=payload)

    async def delete_webinar(self, webinar_id: Union[str, int]) -> ZoomResponse:
        return await self._request("DELETE", f"/webinars/{webinar_id}")

    async def list_webinars(
        self,
        user_id: str,
        page_size: int = 30,
        page_number: int = 1,
    ) -> ZoomResponse:
        params = {"page_size": page_size, "page_number": page_number}
        return await self._request("GET", f"/users/{user_id}/webinars", params=params)

    # ============================================================================
    # RECORDINGS
    # ============================================================================
    async def get_recordings(self, meeting_id: Union[str, int]) -> ZoomResponse:
        return await self._request("GET", f"/meetings/{meeting_id}/recordings")

    async def delete_recording(
        self, meeting_id: Union[str, int], recording_id: Optional[str] = None
    ) -> ZoomResponse:
        if recording_id:
            endpoint = f"/meetings/{meeting_id}/recordings/{recording_id}"
        else:
            endpoint = f"/meetings/{meeting_id}/recordings"
        return await self._request("DELETE", endpoint)

    async def get_recording_settings(self, meeting_id: Union[str, int]) -> ZoomResponse:
        return await self._request("GET", f"/meetings/{meeting_id}/recordings/settings")

    async def update_recording_settings(self, meeting_id: Union[str, int], payload: dict) -> ZoomResponse:
        return await self._request("PATCH", f"/meetings/{meeting_id}/recordings/settings", body=payload)

    # ============================================================================
    # REPORTS & METRICS
    # ============================================================================
    async def meeting_report(self, meeting_id: Union[str, int]) -> ZoomResponse:
        return await self._request("GET", f"/report/meetings/{meeting_id}")

    async def meeting_participant_report(
        self,
        meeting_id: Union[str, int],
        page_size: int = 30,
        page_number: int = 1,
    ) -> ZoomResponse:
        params = {"page_size": page_size, "page_number": page_number}
        return await self._request("GET", f"/report/meetings/{meeting_id}/participants", params=params)

    async def account_metrics_meetings(
        self,
        from_date: str,
        to_date: str,
        type: Optional[str] = None,
    ) -> ZoomResponse:
        params = {"from": from_date, "to": to_date}
        if type:
            params["type"] = type
        return await self._request("GET", "/metrics/meetings", params=params)

    async def dashboard_activity(
        self,
        from_time: str,
        to_time: str,
        page_size: int = 30,
        page_number: int = 1,
    ) -> ZoomResponse:
        params = {
            "from": from_time,
            "to": to_time,
            "page_size": page_size,
            "page_number": page_number,
        }
        return await self._request("GET", "/metrics/daily", params=params)

    # ============================================================================
    # ACCOUNT & ROLES
    # ============================================================================
    async def account_settings(self) -> ZoomResponse:
        return await self._request("GET", "/accounts/me")

    async def roles_list(self) -> ZoomResponse:
        return await self._request("GET", "/roles")

    async def roles_get(self, role_id: str) -> ZoomResponse:
        return await self._request("GET", f"/roles/{role_id}")

    # ============================================================================
    # WEBHOOKS
    # ============================================================================
    async def webhook_event_list(self) -> ZoomResponse:
        return await self._request("GET", "/webhooks")

    async def webhook_create(self, payload: dict) -> ZoomResponse:
        return await self._request("POST", "/webhooks", body=payload)

    async def webhook_delete(self, webhook_id: str) -> ZoomResponse:
        return await self._request("DELETE", f"/webhooks/{webhook_id}")

    # ============================================================================
    # EXTEND MEETING (BUSINESS TASK)
    # ============================================================================
    async def extend_meeting(self, meeting_id: Union[str, int], extra_minutes: int) -> ZoomResponse:
        """
        Extend a meeting by increasing its duration.
        - GET meeting
        - Compute new duration
        - PATCH duration
        """
        try:
            meeting_resp = await self.get_meeting(meeting_id)
            if not meeting_resp.success:
                return ZoomResponse(False, error="Failed to fetch meeting")

            meeting = meeting_resp.data or {}
            current_duration = meeting.get("duration")
            start_time = meeting.get("start_time")

            if isinstance(current_duration, int):
                new_duration = current_duration + extra_minutes
            else:
                if start_time:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    elapsed = int((now - dt).total_seconds() // 60)
                    new_duration = max(elapsed + extra_minutes, extra_minutes)
                else:
                    new_duration = extra_minutes

            patch_resp = await self.update_meeting(meeting_id, {"duration": new_duration})
            if not patch_resp.success:
                return ZoomResponse(False, error="Failed to extend meeting")

            return ZoomResponse(True, data={"meeting_id": meeting_id, "new_duration": new_duration})

        except Exception as e:
            LOG.exception("Extend meeting failed")
            return ZoomResponse(False, error=str(e))

    # ============================================================================
    # RAW REQUEST (fallback)
    # ============================================================================
    async def raw_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> ZoomResponse:
        return await self._request(method, endpoint, params=params, body=body)
    
    # ========================================================================
    # CHAT MESSAGES (New)
    # ========================================================================
    async def chat_messages_list(
        self,
        user_id: str,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        page_size: int = 30,
        page_number: int = 1,
        search: Optional[str] = None,
    ) -> ZoomResponse:
        """
        GET /chat/users/{userId}/messages
        """
        params = {
            "page_size": page_size,
            "page_number": page_number,
        }
        if from_ts:
            params["from"] = from_ts
        if to_ts:
            params["to"] = to_ts
        if search:
            params["search"] = search

        return await self._request("GET", f"/chat/users/{user_id}/messages", params=params)

    async def chat_message_get(self, message_id: str) -> ZoomResponse:
        """GET /chat/messages/{messageId}"""
        return await self._request("GET", f"/chat/messages/{message_id}")

    async def chat_message_delete(self, message_id: str) -> ZoomResponse:
        """DELETE /chat/messages/{messageId}"""
        return await self._request("DELETE", f"/chat/messages/{message_id}")


    # ========================================================================
    # CLOUD RECORDING HELPERS (New)
    # ========================================================================
    async def get_recording_file_info(
        self,
        meeting_id: Union[str, int],
        recording_file_id: str
    ) -> ZoomResponse:
        """
        GET /meetings/{meetingId}/recordings/{recordingFileId}
        Returns metadata (including download_url if available).
        """
        return await self._request(
            "GET",
            f"/meetings/{meeting_id}/recordings/{recording_file_id}"
        )


    async def get_recording_download_info(
        self,
        meeting_id: Union[str, int],
        recording_file_id: str
    ) -> ZoomResponse:
        """
        Convenience helper: return file metadata (may contain download_url).
        """
        info = await self.get_recording_file_info(meeting_id, recording_file_id)
        if not info.success:
            return info

        # return the metadata directly
        return ZoomResponse(True, data=info.data)


    # ========================================================================
    # DOWNLOAD RECORDING BYTES (New)
    # ========================================================================
    async def download_recording_to_bytes(
        self,
        download_url: str
    ) -> ZoomResponse:
        """
        Download a Zoom cloud recording file (binary content).
        Uses underlying HTTP client (Authorization applied automatically
        if Zoom requires it; some download URLs are public).
        """
        try:
            from app.sources.client.http.http_request import HTTPRequest

            http_client = getattr(self._rest_client, "http_client", None)
            if http_client is None:
                return ZoomResponse(False, error="HTTP client unavailable")

            req = HTTPRequest(
                method="GET",
                url=download_url,
                headers={},
                query_params={},
                body=None,
                path_params={}
            )

            resp = await http_client.execute(req)

            return ZoomResponse(
                True,
                data={
                    "status_code": resp.status_code,
                    "content": resp.content,
                }
            )

        except Exception as e:
            LOG.exception("Recording download failed")
            return ZoomResponse(False, error=str(e))


"""
AUTO-GENERATED ZOOM DATASOURCE — DO NOT MODIFY MANUALLY
"""
from typing import Any, Dict, Optional
from backend.python.app.sources.client.iclient import IClient


class ZoomDataSource:
    def __init__(self, client: IClient, base_url: str = "https://api.zoom.us/v2"):
        self._rest = client
        self._base_url = base_url.rstrip("/")


    async def get_account_lock_settings(self, accountId: Optional[Any] = None, option: Optional[Any] = None, custom_query_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAccountLockSettings
        method: GET
        path: /accounts/{accountId}/lock_settings
        summary: Get locked settings
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/lock_settings"
        params = { 'accountId': accountId, 'option': option, 'custom_query_fields': custom_query_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_locked_settings(self, accountId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateLockedSettings
        method: PATCH
        path: /accounts/{accountId}/lock_settings
        summary: Update locked settings
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/lock_settings"
        params = { 'accountId': accountId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_managed_domain(self, accountId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountManagedDomain
        method: GET
        path: /accounts/{accountId}/managed_domains
        summary: Get account's managed domains
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/managed_domains"
        params = { 'accountId': accountId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_the_account_owner(self, accountId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateTheAccountOwner
        method: PUT
        path: /accounts/{accountId}/owner
        summary: Update the account owner
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/owner"
        params = { 'accountId': accountId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_settings(self, accountId: Optional[Any] = None, option: Optional[Any] = None, custom_query_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountSettings
        method: GET
        path: /accounts/{accountId}/settings
        summary: Get account settings
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/settings"
        params = { 'accountId': accountId, 'option': option, 'custom_query_fields': custom_query_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_settings_update(self, accountId: Optional[Any] = None, option: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountSettingsUpdate
        method: PATCH
        path: /accounts/{accountId}/settings
        summary: Update account settings
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/settings"
        params = { 'accountId': accountId, 'option': option }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_settings_registration(self, accountId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountSettingsRegistration
        method: GET
        path: /accounts/{accountId}/settings/registration
        summary: Get an account's webinar registration settings
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/settings/registration"
        params = { 'accountId': accountId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_settings_registration_update(self, accountId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountSettingsRegistrationUpdate
        method: PATCH
        path: /accounts/{accountId}/settings/registration
        summary: Update an account's webinar registration settings
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/settings/registration"
        params = { 'accountId': accountId, 'type': type_ }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_vb(self, accountId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: uploadVB
        method: POST
        path: /accounts/{accountId}/settings/virtual_backgrounds
        summary: Upload virtual background files
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/settings/virtual_backgrounds"
        params = { 'accountId': accountId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def del_vb(self, accountId: Optional[Any] = None, file_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delVB
        method: DELETE
        path: /accounts/{accountId}/settings/virtual_backgrounds
        summary: Delete virtual background files
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/settings/virtual_backgrounds"
        params = { 'accountId': accountId, 'file_ids': file_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_trusted_domain(self, accountId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountTrustedDomain
        method: GET
        path: /accounts/{accountId}/trusted_domains
        summary: Get account's trusted domains
        """
        endpoint = f"{self._base_url}/accounts/{accountId}/trusted_domains"
        params = { 'accountId': accountId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_chat(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardChat
        method: GET
        path: /metrics/chat
        summary: Get chat metrics
        """
        endpoint = f"{self._base_url}/metrics/chat"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_client_feedback(self, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardClientFeedback
        method: GET
        path: /metrics/client/feedback
        summary: List Zoom meetings client feedback
        """
        endpoint = f"{self._base_url}/metrics/client/feedback"
        params = { 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_client_feedback_detail(self, feedbackId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardClientFeedbackDetail
        method: GET
        path: /metrics/client/feedback/{feedbackId}
        summary: Get zoom meetings client feedback
        """
        endpoint = f"{self._base_url}/metrics/client/feedback/{feedbackId}"
        params = { 'feedbackId': feedbackId, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_meeting_satisfaction(self, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listMeetingSatisfaction
        method: GET
        path: /metrics/client/satisfaction
        summary: List client meeting satisfaction
        """
        endpoint = f"{self._base_url}/metrics/client/satisfaction"
        params = { 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_client_versions(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getClientVersions
        method: GET
        path: /metrics/client_versions
        summary: List the client versions
        """
        endpoint = f"{self._base_url}/metrics/client_versions"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_crc(self, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardCRC
        method: GET
        path: /metrics/crc
        summary: Get CRC port usage
        """
        endpoint = f"{self._base_url}/metrics/crc"
        params = { 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_issue_zoom_room(self, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardIssueZoomRoom
        method: GET
        path: /metrics/issues/zoomrooms
        summary: Get top 25 Zoom Rooms with issues
        """
        endpoint = f"{self._base_url}/metrics/issues/zoomrooms"
        params = { 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_issue_detail_zoom_room(self, zoomroomId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardIssueDetailZoomRoom
        method: GET
        path: /metrics/issues/zoomrooms/{zoomroomId}
        summary: Get issues of Zoom Rooms
        """
        endpoint = f"{self._base_url}/metrics/issues/zoomrooms/{zoomroomId}"
        params = { 'zoomroomId': zoomroomId, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meetings(self, type_: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, group_id: Optional[Any] = None, group_include_participant: Optional[Any] = None, include_fields: Optional[Any] = None, query_date_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetings
        method: GET
        path: /metrics/meetings
        summary: List meetings
        """
        endpoint = f"{self._base_url}/metrics/meetings"
        params = { 'type': type_, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'group_id': group_id, 'group_include_participant': group_include_participant, 'include_fields': include_fields, 'query_date_type': query_date_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meeting_detail(self, meetingId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetingDetail
        method: GET
        path: /metrics/meetings/{meetingId}
        summary: Get meeting details
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}"
        params = { 'meetingId': meetingId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meeting_participants(self, meetingId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, include_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetingParticipants
        method: GET
        path: /metrics/meetings/{meetingId}/participants
        summary: List meeting participants
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}/participants"
        params = { 'meetingId': meetingId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token, 'include_fields': include_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meeting_participants_qos(self, meetingId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetingParticipantsQOS
        method: GET
        path: /metrics/meetings/{meetingId}/participants/qos
        summary: List meeting participants QoS
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}/participants/qos"
        params = { 'meetingId': meetingId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def participant_feedback(self, meetingId: Optional[Any] = None, type_: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: participantFeedback
        method: GET
        path: /metrics/meetings/{meetingId}/participants/satisfaction
        summary: Get post meeting feedback
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}/participants/satisfaction"
        params = { 'meetingId': meetingId, 'type': type_, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meeting_participant_share(self, meetingId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetingParticipantShare
        method: GET
        path: /metrics/meetings/{meetingId}/participants/sharing
        summary: Get meeting sharing/recording details
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}/participants/sharing"
        params = { 'meetingId': meetingId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meeting_participant_qos(self, meetingId: Optional[Any] = None, participantId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetingParticipantQOS
        method: GET
        path: /metrics/meetings/{meetingId}/participants/{participantId}/qos
        summary: Get meeting participant QoS
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}/participants/{participantId}/qos"
        params = { 'meetingId': meetingId, 'participantId': participantId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_quality(self, from_: Optional[Any] = None, to: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardQuality
        method: GET
        path: /metrics/quality
        summary: Get meeting quality scores
        """
        endpoint = f"{self._base_url}/metrics/quality"
        params = { 'from': from_, 'to': to, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinars(self, type_: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, group_id: Optional[Any] = None, group_include_participant: Optional[Any] = None, query_date_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinars
        method: GET
        path: /metrics/webinars
        summary: List webinars
        """
        endpoint = f"{self._base_url}/metrics/webinars"
        params = { 'type': type_, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'group_id': group_id, 'group_include_participant': group_include_participant, 'query_date_type': query_date_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinar_detail(self, webinarId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinarDetail
        method: GET
        path: /metrics/webinars/{webinarId}
        summary: Get webinar details
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}"
        params = { 'webinarId': webinarId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinar_participants(self, webinarId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, include_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinarParticipants
        method: GET
        path: /metrics/webinars/{webinarId}/participants
        summary: Get webinar participants
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}/participants"
        params = { 'webinarId': webinarId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token, 'include_fields': include_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinar_participants_qos(self, webinarId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinarParticipantsQOS
        method: GET
        path: /metrics/webinars/{webinarId}/participants/qos
        summary: List webinar participant QoS
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}/participants/qos"
        params = { 'webinarId': webinarId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def participant_webinar_feedback(self, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: participantWebinarFeedback
        method: GET
        path: /metrics/webinars/{webinarId}/participants/satisfaction
        summary: Get post webinar feedback
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}/participants/satisfaction"
        params = { 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token, 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinar_participant_share(self, webinarId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinarParticipantShare
        method: GET
        path: /metrics/webinars/{webinarId}/participants/sharing
        summary: Get webinar sharing/recording details
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}/participants/sharing"
        params = { 'webinarId': webinarId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinar_participant_qos(self, webinarId: Optional[Any] = None, participantId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinarParticipantQOS
        method: GET
        path: /metrics/webinars/{webinarId}/participants/{participantId}/qos
        summary: Get webinar participant QoS
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}/participants/{participantId}/qos"
        params = { 'webinarId': webinarId, 'participantId': participantId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_zoom_rooms(self, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardZoomRooms
        method: GET
        path: /metrics/zoomrooms
        summary: List Zoom Rooms
        """
        endpoint = f"{self._base_url}/metrics/zoomrooms"
        params = { 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_zoom_room_issue(self, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardZoomRoomIssue
        method: GET
        path: /metrics/zoomrooms/issues
        summary: Get top 25 issues of Zoom Rooms
        """
        endpoint = f"{self._base_url}/metrics/zoomrooms/issues"
        params = { 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_zoom_room(self, zoomroomId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardZoomRoom
        method: GET
        path: /metrics/zoomrooms/{zoomroomId}
        summary: Get Zoom Rooms details
        """
        endpoint = f"{self._base_url}/metrics/zoomrooms/{zoomroomId}"
        params = { 'zoomroomId': zoomroomId, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def downloadfilesfrom_data_request(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DownloadfilesfromDataRequest
        method: GET
        path: /data_requests/files/{fileId}/url
        summary: Get download link for data access request file
        """
        endpoint = f"{self._base_url}/data_requests/files/{fileId}/url"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_data_requests_history(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetDataRequestsHistory
        method: GET
        path: /data_requests/requests
        summary: List data request history
        """
        endpoint = f"{self._base_url}/data_requests/requests"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_data_access_request(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CreateDataAccessRequest
        method: POST
        path: /data_requests/requests
        summary: Create data  (export/deletion) request
        """
        endpoint = f"{self._base_url}/data_requests/requests"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_downloadable_filesfor_data_request(self, requestId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetDownloadableFilesforDataRequest
        method: GET
        path: /data_requests/requests/{requestId}
        summary: List downloadable files for export data request 
        """
        endpoint = f"{self._base_url}/data_requests/requests/{requestId}"
        params = { 'requestId': requestId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def cancel_data_request(self, requestId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CancelDataRequest
        method: DELETE
        path: /data_requests/requests/{requestId}
        summary: Cancel data deletion request
        """
        endpoint = f"{self._base_url}/data_requests/requests/{requestId}"
        params = { 'requestId': requestId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def information_barriers_list(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InformationBarriersList
        method: GET
        path: /information_barriers/policies
        summary: List information Barrier policies
        """
        endpoint = f"{self._base_url}/information_barriers/policies"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def information_barriers_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InformationBarriersCreate
        method: POST
        path: /information_barriers/policies
        summary: Create an Information Barrier policy
        """
        endpoint = f"{self._base_url}/information_barriers/policies"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def information_barriers_get(self, policyId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InformationBarriersGet
        method: GET
        path: /information_barriers/policies/{policyId}
        summary: Get an Information Barrier policy by ID
        """
        endpoint = f"{self._base_url}/information_barriers/policies/{policyId}"
        params = { 'policyId': policyId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def information_barriers_delete(self, policyId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InformationBarriersDelete
        method: DELETE
        path: /information_barriers/policies/{policyId}
        summary: Remove an Information Barrier policy
        """
        endpoint = f"{self._base_url}/information_barriers/policies/{policyId}"
        params = { 'policyId': policyId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def information_barriers_update(self, policyId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InformationBarriersUpdate
        method: PATCH
        path: /information_barriers/policies/{policyId}
        summary: Update an Information Barriers policy
        """
        endpoint = f"{self._base_url}/information_barriers/policies/{policyId}"
        params = { 'policyId': policyId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def roles(self, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: roles
        method: GET
        path: /roles
        summary: List roles
        """
        endpoint = f"{self._base_url}/roles"
        params = { 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_role(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createRole
        method: POST
        path: /roles
        summary: Create a role
        """
        endpoint = f"{self._base_url}/roles"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_role_information(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getRoleInformation
        method: GET
        path: /roles/{roleId}
        summary: Get role information
        """
        endpoint = f"{self._base_url}/roles/{roleId}"
        params = { 'roleId': roleId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_role(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteRole
        method: DELETE
        path: /roles/{roleId}
        summary: Delete a role
        """
        endpoint = f"{self._base_url}/roles/{roleId}"
        params = { 'roleId': roleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_role(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateRole
        method: PATCH
        path: /roles/{roleId}
        summary: Update role information
        """
        endpoint = f"{self._base_url}/roles/{roleId}"
        params = { 'roleId': roleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def role_members(self, roleId: Optional[Any] = None, page_count: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: roleMembers
        method: GET
        path: /roles/{roleId}/members
        summary: List members in a role
        """
        endpoint = f"{self._base_url}/roles/{roleId}/members"
        params = { 'roleId': roleId, 'page_count': page_count, 'page_number': page_number, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_role_members(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddRoleMembers
        method: POST
        path: /roles/{roleId}/members
        summary: Assign a role
        """
        endpoint = f"{self._base_url}/roles/{roleId}/members"
        params = { 'roleId': roleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def role_member_delete(self, roleId: Optional[Any] = None, memberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: roleMemberDelete
        method: DELETE
        path: /roles/{roleId}/members/{memberId}
        summary: Unassign a role
        """
        endpoint = f"{self._base_url}/roles/{roleId}/members/{memberId}"
        params = { 'roleId': roleId, 'memberId': memberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_account_surveys(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAccountSurveys
        method: GET
        path: /surveys
        summary: Get surveys
        """
        endpoint = f"{self._base_url}/surveys"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_survey_info(self, surveyId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSurveyInfo
        method: GET
        path: /surveys/{surveyId}
        summary: Get survey info
        """
        endpoint = f"{self._base_url}/surveys/{surveyId}"
        params = { 'surveyId': surveyId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_survey_answers(self, surveyId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, instance_id: Optional[Any] = None, submit_time_start: Optional[Any] = None, submit_time_end: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSurveyAnswers
        method: GET
        path: /surveys/{surveyId}/answers
        summary: Get survey answers
        """
        endpoint = f"{self._base_url}/surveys/{surveyId}/answers"
        params = { 'surveyId': surveyId, 'page_size': page_size, 'next_page_token': next_page_token, 'instance_id': instance_id, 'submit_time_start': submit_time_start, 'submit_time_end': submit_time_end }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_survey_instances_info(self, surveyId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, instance_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSurveyInstancesInfo
        method: GET
        path: /surveys/{surveyId}/instances
        summary: Get survey instances
        """
        endpoint = f"{self._base_url}/surveys/{surveyId}/instances"
        params = { 'surveyId': surveyId, 'page_size': page_size, 'next_page_token': next_page_token, 'instance_id': instance_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_ai_cconversationarchives(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAICconversationarchives
        method: GET
        path: /aic/users/{userId}/conversation_archive
        summary: Get AI Companion conversation archives
        """
        endpoint = f"{self._base_url}/aic/users/{userId}/conversation_archive"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def listacl(self, calId: Optional[Any] = None, maxResults: Optional[Any] = None, showDeleted: Optional[Any] = None, pageToken: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listacl
        method: GET
        path: /calendars/{calId}/acl
        summary: List ACL rules of specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/acl"
        params = { 'calId': calId, 'maxResults': maxResults, 'showDeleted': showDeleted, 'pageToken': pageToken }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def insertacl(self, calId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Insertacl
        method: POST
        path: /calendars/{calId}/acl
        summary: Create a new ACL rule
        """
        endpoint = f"{self._base_url}/calendars/{calId}/acl"
        params = { 'calId': calId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getacl(self, calId: Optional[Any] = None, aclId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getacl
        method: GET
        path: /calendars/{calId}/acl/{aclId}
        summary: Get the specified ACL rule
        """
        endpoint = f"{self._base_url}/calendars/{calId}/acl/{aclId}"
        params = { 'calId': calId, 'aclId': aclId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteacl(self, calId: Optional[Any] = None, aclId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteacl
        method: DELETE
        path: /calendars/{calId}/acl/{aclId}
        summary: Delete an existing ACL rule
        """
        endpoint = f"{self._base_url}/calendars/{calId}/acl/{aclId}"
        params = { 'calId': calId, 'aclId': aclId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patchacl(self, calId: Optional[Any] = None, aclId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Patchacl
        method: PATCH
        path: /calendars/{calId}/acl/{aclId}
        summary: Update the specified ACL rule
        """
        endpoint = f"{self._base_url}/calendars/{calId}/acl/{aclId}"
        params = { 'calId': calId, 'aclId': aclId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def listcalendar_list(self, userIdentifier: Optional[Any] = None, maxResults: Optional[Any] = None, minAccessRole: Optional[Any] = None, pageToken: Optional[Any] = None, showDeleted: Optional[Any] = None, showHidden: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListcalendarList
        method: GET
        path: /calendars/users/{userIdentifier}/calendarList
        summary: List the calendars in the user's own calendarList
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/calendarList"
        params = { 'userIdentifier': userIdentifier, 'maxResults': maxResults, 'minAccessRole': minAccessRole, 'pageToken': pageToken, 'showDeleted': showDeleted, 'showHidden': showHidden }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def insertcalendar_list(self, userIdentifier: Optional[Any] = None, colorRgbFormat: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InsertcalendarList
        method: POST
        path: /calendars/users/{userIdentifier}/calendarList
        summary: Insert an existing calendar to the user's own calendarList
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/calendarList"
        params = { 'userIdentifier': userIdentifier, 'colorRgbFormat': colorRgbFormat }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getcalendar_list(self, userIdentifier: Optional[Any] = None, calendarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetcalendarList
        method: GET
        path: /calendars/users/{userIdentifier}/calendarList/{calendarId}
        summary: Get a specified calendar from the user's own calendarList
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/calendarList/{calendarId}"
        params = { 'userIdentifier': userIdentifier, 'calendarId': calendarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deletecalendar_list(self, userIdentifier: Optional[Any] = None, calendarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeletecalendarList
        method: DELETE
        path: /calendars/users/{userIdentifier}/calendarList/{calendarId}
        summary: Delete an existing calendar from the user's own calendarList
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/calendarList/{calendarId}"
        params = { 'userIdentifier': userIdentifier, 'calendarId': calendarId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patchcalendar_list(self, userIdentifier: Optional[Any] = None, calendarId: Optional[Any] = None, colorRgbFormat: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: PatchcalendarList
        method: PATCH
        path: /calendars/users/{userIdentifier}/calendarList/{calendarId}
        summary: Update an existing calendar in the user's own calendarList
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/calendarList/{calendarId}"
        params = { 'userIdentifier': userIdentifier, 'calendarId': calendarId, 'colorRgbFormat': colorRgbFormat }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def insertcalendar(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Insertcalendar
        method: POST
        path: /calendars
        summary: Create a new secondary calendar
        """
        endpoint = f"{self._base_url}/calendars"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getcalendar(self, calId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getcalendar
        method: GET
        path: /calendars/{calId}
        summary: Get the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}"
        params = { 'calId': calId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deletecalendar(self, calId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deletecalendar
        method: DELETE
        path: /calendars/{calId}
        summary: Delete a calendar owned by a user
        """
        endpoint = f"{self._base_url}/calendars/{calId}"
        params = { 'calId': calId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patchcalendar(self, calId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Patchcalendar
        method: PATCH
        path: /calendars/{calId}
        summary: Update the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}"
        params = { 'calId': calId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getcolor(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getcolor
        method: GET
        path: /calendars/colors
        summary: Get the color definitions for calendars and events
        """
        endpoint = f"{self._base_url}/calendars/colors"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def listevent(self, calId: Optional[Any] = None, maxResults: Optional[Any] = None, orderBy: Optional[Any] = None, showDeleted: Optional[Any] = None, singleEvents: Optional[Any] = None, pageToken: Optional[Any] = None, timeMax: Optional[Any] = None, timeMin: Optional[Any] = None, timeZone: Optional[Any] = None, syncToken: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listevent
        method: GET
        path: /calendars/{calId}/events
        summary: List events on the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events"
        params = { 'calId': calId, 'maxResults': maxResults, 'orderBy': orderBy, 'showDeleted': showDeleted, 'singleEvents': singleEvents, 'pageToken': pageToken, 'timeMax': timeMax, 'timeMin': timeMin, 'timeZone': timeZone, 'syncToken': syncToken }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def insertevent(self, calId: Optional[Any] = None, sendUpdates: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Insertevent
        method: POST
        path: /calendars/{calId}/events
        summary: Insert a new event to the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events"
        params = { 'calId': calId, 'sendUpdates': sendUpdates }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def importevent(self, calId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Importevent
        method: POST
        path: /calendars/{calId}/events/import
        summary: Import event to the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/import"
        params = { 'calId': calId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def quickaddevent(self, calId: Optional[Any] = None, text: Optional[Any] = None, sendUpdates: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Quickaddevent
        method: POST
        path: /calendars/{calId}/events/quickAdd
        summary: Quick add an event to the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/quickAdd"
        params = { 'calId': calId, 'text': text, 'sendUpdates': sendUpdates }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getevent(self, calId: Optional[Any] = None, eventId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getevent
        method: GET
        path: /calendars/{calId}/events/{eventId}
        summary: Get the specified event on the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/{eventId}"
        params = { 'calId': calId, 'eventId': eventId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteevent(self, calId: Optional[Any] = None, eventId: Optional[Any] = None, sendUpdates: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteevent
        method: DELETE
        path: /calendars/{calId}/events/{eventId}
        summary: Delete an existing event from the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/{eventId}"
        params = { 'calId': calId, 'eventId': eventId, 'sendUpdates': sendUpdates }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patchevent(self, calId: Optional[Any] = None, eventId: Optional[Any] = None, sendUpdates: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Patchevent
        method: PATCH
        path: /calendars/{calId}/events/{eventId}
        summary: Update the specified event on the specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/{eventId}"
        params = { 'calId': calId, 'eventId': eventId, 'sendUpdates': sendUpdates }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def instanceevent(self, calId: Optional[Any] = None, eventId: Optional[Any] = None, maxResults: Optional[Any] = None, showDeleted: Optional[Any] = None, pageToken: Optional[Any] = None, timeMax: Optional[Any] = None, timeMin: Optional[Any] = None, timeZone: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Instanceevent
        method: GET
        path: /calendars/{calId}/events/{eventId}/instances
        summary: List all instances of the specified recurring event
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/{eventId}/instances"
        params = { 'calId': calId, 'eventId': eventId, 'maxResults': maxResults, 'showDeleted': showDeleted, 'pageToken': pageToken, 'timeMax': timeMax, 'timeMin': timeMin, 'timeZone': timeZone }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def moveevent(self, calId: Optional[Any] = None, eventId: Optional[Any] = None, destination: Optional[Any] = None, sendUpdates: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Moveevent
        method: POST
        path: /calendars/{calId}/events/{eventId}/move
        summary: Move the specified event from a calendar to another specified calendar
        """
        endpoint = f"{self._base_url}/calendars/{calId}/events/{eventId}/move"
        params = { 'calId': calId, 'eventId': eventId, 'destination': destination, 'sendUpdates': sendUpdates }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def queryfreebusy(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Queryfreebusy
        method: POST
        path: /calendars/freeBusy
        summary: Query freebusy information for a set of calendars
        """
        endpoint = f"{self._base_url}/calendars/freeBusy"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def listsettings(self, userIdentifier: Optional[Any] = None, maxResults: Optional[Any] = None, pageToken: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listsettings
        method: GET
        path: /calendars/users/{userIdentifier}/settings
        summary: List all user calendar settings of the authenticated user
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/settings"
        params = { 'userIdentifier': userIdentifier, 'maxResults': maxResults, 'pageToken': pageToken }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def getsetting(self, userIdentifier: Optional[Any] = None, settingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getsetting
        method: GET
        path: /calendars/users/{userIdentifier}/settings/{settingId}
        summary: Get the specified user calendar settings of the authenticated user
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/settings/{settingId}"
        params = { 'userIdentifier': userIdentifier, 'settingId': settingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def patchsetting(self, userIdentifier: Optional[Any] = None, settingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Patchsetting
        method: PATCH
        path: /calendars/users/{userIdentifier}/settings/{settingId}
        summary: Patch the specified user calendar settings of the authenticated user 
        """
        endpoint = f"{self._base_url}/calendars/users/{userIdentifier}/settings/{settingId}"
        params = { 'userIdentifier': userIdentifier, 'settingId': settingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def send_chatbot(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: sendChatbot
        method: POST
        path: /im/chat/messages
        summary: Send Chatbot messages
        """
        endpoint = f"{self._base_url}/im/chat/messages"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def edit_chatbot_message(self, message_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: editChatbotMessage
        method: PUT
        path: /im/chat/messages/{message_id}
        summary: Edit a Chatbot message
        """
        endpoint = f"{self._base_url}/im/chat/messages/{message_id}"
        params = { 'message_id': message_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_chatbot_message(self, message_id: Optional[Any] = None, account_id: Optional[Any] = None, user_jid: Optional[Any] = None, robot_jid: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAChatbotMessage
        method: DELETE
        path: /im/chat/messages/{message_id}
        summary: Delete a Chatbot message
        """
        endpoint = f"{self._base_url}/im/chat/messages/{message_id}"
        params = { 'message_id': message_id, 'account_id': account_id, 'user_jid': user_jid, 'robot_jid': robot_jid }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def unfurling_link(self, userId: Optional[Any] = None, triggerId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unfurlingLink
        method: POST
        path: /im/chat/users/{userId}/unfurls/{triggerId}
        summary: Link Unfurls
        """
        endpoint = f"{self._base_url}/im/chat/users/{userId}/unfurls/{triggerId}"
        params = { 'userId': userId, 'triggerId': triggerId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_clip_collaborators(self, clipId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetClipCollaborators
        method: GET
        path: /clips/{clipId}/collaborators
        summary: Get collaborators of a clip
        """
        endpoint = f"{self._base_url}/clips/{clipId}/collaborators"
        params = { 'clipId': clipId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_collaborator(self, clipId: Optional[Any] = None, user_key: Optional[Any] = None, channel_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteCollaborator
        method: DELETE
        path: /clips/{clipId}/collaborators
        summary: Remove the collaborator from a clip
        """
        endpoint = f"{self._base_url}/clips/{clipId}/collaborators"
        params = { 'clipId': clipId, 'user_key': user_key, 'channel_id': channel_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def listclipcomments(self, clipId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listclipcomments
        method: GET
        path: /clips/{clipId}/comments
        summary: List clip comments
        """
        endpoint = f"{self._base_url}/clips/{clipId}/comments"
        params = { 'clipId': clipId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteacomment(self, clipId: Optional[Any] = None, commentId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteacomment
        method: DELETE
        path: /clips/{clipId}/comments/{commentId}
        summary: Delete a comment
        """
        endpoint = f"{self._base_url}/clips/{clipId}/comments/{commentId}"
        params = { 'clipId': clipId, 'commentId': commentId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_clip_by_id(self, clipId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetClipById
        method: GET
        path: /clips/{clipId}
        summary: Get a clip
        """
        endpoint = f"{self._base_url}/clips/{clipId}"
        params = { 'clipId': clipId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_clip(self, clipId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteClip
        method: DELETE
        path: /clips/{clipId}
        summary: Delete a clip(soft delete)
        """
        endpoint = f"{self._base_url}/clips/{clipId}"
        params = { 'clipId': clipId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def transferclipsowner(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Transferclipsowner
        method: POST
        path: /clips/transfers
        summary: Transfer clips owner
        """
        endpoint = f"{self._base_url}/clips/transfers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def transfertaskstatuscheck(self, taskId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Transfertaskstatuscheck
        method: GET
        path: /clips/transfers/{taskId}
        summary: Transfer task status check
        """
        endpoint = f"{self._base_url}/clips/transfers/{taskId}"
        params = { 'taskId': taskId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_clip_file(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UploadClipFile
        method: POST
        path: /clips/files
        summary: Upload clip file
        """
        endpoint = f"{self._base_url}/clips/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_iq_multipart_clip_file(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UploadIqMultipartClipFile
        method: POST
        path: /clips/files/multipart
        summary: Upload clip multipart files
        """
        endpoint = f"{self._base_url}/clips/files/multipart"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def initiate_and_complete_a_clip_multipart_upload(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InitiateAndCompleteAClipMultipartUpload.
        method: POST
        path: /clips/files/multipart/upload_events
        summary: Initiate and complete the multipart file upload for a clip
        """
        endpoint = f"{self._base_url}/clips/files/multipart/upload_events"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_clips(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, user_id: Optional[Any] = None, search_key: Optional[Any] = None, date_filter_type: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetUserClips
        method: GET
        path: /clips
        summary: List all clips
        """
        endpoint = f"{self._base_url}/clips"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'user_id': user_id, 'search_key': search_key, 'date_filter_type': date_filter_type, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_cisco_polycom_room_account_setting(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCiscoPolycomRoomAccountSetting
        method: GET
        path: /crc/managed_rooms/account_setting
        summary: Get Cisco/Polycom Room Account Setting
        """
        endpoint = f"{self._base_url}/crc/managed_rooms/account_setting"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_cisco_polycom_room_account_setting(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateCiscoPolycomRoomAccountSetting
        method: PATCH
        path: /crc/managed_rooms/account_setting
        summary: Update Cisco/Polycom Room Account Setting
        """
        endpoint = f"{self._base_url}/crc/managed_rooms/account_setting"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_list_api_connectors(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetListAPIConnectors
        method: GET
        path: /crc/api_connectors
        summary: List API Connectors
        """
        endpoint = f"{self._base_url}/crc/api_connectors"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_api_connector(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CreateAPIConnector
        method: POST
        path: /crc/api_connectors
        summary: Create an API Connector
        """
        endpoint = f"{self._base_url}/crc/api_connectors"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_api_connector(self, connectorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAPIConnector
        method: GET
        path: /crc/api_connectors/{connectorId}
        summary: Get an API Connector
        """
        endpoint = f"{self._base_url}/crc/api_connectors/{connectorId}"
        params = { 'connectorId': connectorId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_api_connector(self, connectorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAPIConnector
        method: DELETE
        path: /crc/api_connectors/{connectorId}
        summary: Delete an API Connector
        """
        endpoint = f"{self._base_url}/crc/api_connectors/{connectorId}"
        params = { 'connectorId': connectorId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_api_connector(self, connectorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAPIConnector
        method: PATCH
        path: /crc/api_connectors/{connectorId}
        summary: Update an API Connector
        """
        endpoint = f"{self._base_url}/crc/api_connectors/{connectorId}"
        params = { 'connectorId': connectorId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getan_api_connector_sprivatekey(self, connectorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetanAPIConnector'sprivatekey
        method: GET
        path: /crc/api_connectors/{connectorId}/private_key
        summary: Get an API Connector's private key
        """
        endpoint = f"{self._base_url}/crc/api_connectors/{connectorId}/private_key"
        params = { 'connectorId': connectorId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_api_connector_private_key(self, connectorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAPIConnectorPrivateKey
        method: PATCH
        path: /crc/api_connectors/{connectorId}/private_key
        summary: Update an API Connector's private key
        """
        endpoint = f"{self._base_url}/crc/api_connectors/{connectorId}/private_key"
        params = { 'connectorId': connectorId }
        body = None
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_managed_rooms(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListManagedRooms
        method: GET
        path: /crc/managed_rooms
        summary: List Managed Rooms
        """
        endpoint = f"{self._base_url}/crc/managed_rooms"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def createa_managed_room(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CreateaManagedRoom
        method: POST
        path: /crc/managed_rooms
        summary: Create a Managed Room
        """
        endpoint = f"{self._base_url}/crc/managed_rooms"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def geta_managed_room(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetaManagedRoom
        method: GET
        path: /crc/managed_rooms/{deviceId}
        summary: Get a Managed Room
        """
        endpoint = f"{self._base_url}/crc/managed_rooms/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteamanagedroom(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteamanagedroom
        method: DELETE
        path: /crc/managed_rooms/{deviceId}
        summary: Delete a managed room
        """
        endpoint = f"{self._base_url}/crc/managed_rooms/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updatea_managed_room(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateaManagedRoom
        method: PATCH
        path: /crc/managed_rooms/{deviceId}
        summary: Update a Managed Room
        """
        endpoint = f"{self._base_url}/crc/managed_rooms/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_participant_identifier_code(self, expires_in: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_participant_identifier_code
        method: GET
        path: /crc/participant_identifier_code
        summary: Get participant identifier code
        """
        endpoint = f"{self._base_url}/crc/participant_identifier_code"
        params = { 'expires_in': expires_in }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_room_templates(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListRoomTemplates
        method: GET
        path: /crc/room_templates
        summary: List Room Templates
        """
        endpoint = f"{self._base_url}/crc/room_templates"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def createa_room_template(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CreateaRoomTemplate
        method: POST
        path: /crc/room_templates
        summary: Create a Room Template
        """
        endpoint = f"{self._base_url}/crc/room_templates"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def geta_room_template(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetaRoomTemplate
        method: GET
        path: /crc/room_templates/{templateId}
        summary: Get a Room Template
        """
        endpoint = f"{self._base_url}/crc/room_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deletearoomtemplate(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deletearoomtemplate
        method: DELETE
        path: /crc/room_templates/{templateId}
        summary: Delete a room template
        """
        endpoint = f"{self._base_url}/crc/room_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updatea_room_template(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateaRoomTemplate
        method: PATCH
        path: /crc/room_templates/{templateId}
        summary: Update a Room Template
        """
        endpoint = f"{self._base_url}/crc/room_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_draft_emails(self, email: Optional[Any] = None, includeSpamTrash: Optional[Any] = None, maxResults: Optional[Any] = None, pageToken: Optional[Any] = None, q: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_draft_emails
        method: GET
        path: /emails/mailboxes/{email}/drafts
        summary: List emails from draft folder
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/drafts"
        params = { 'email': email, 'includeSpamTrash': includeSpamTrash, 'maxResults': maxResults, 'pageToken': pageToken, 'q': q }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_draft_email(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: create_draft_email
        method: POST
        path: /emails/mailboxes/{email}/drafts
        summary: Create a new draft email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/drafts"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def send_draft_email(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: send_draft_email
        method: POST
        path: /emails/mailboxes/{email}/drafts/send
        summary: Send out a draft email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/drafts/send"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_draft_email(self, email: Optional[Any] = None, draftId: Optional[Any] = None, format: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_draft_email
        method: GET
        path: /emails/mailboxes/{email}/drafts/{draftId}
        summary: Get the specified draft email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/drafts/{draftId}"
        params = { 'email': email, 'draftId': draftId, 'format': format }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_draft_email(self, email: Optional[Any] = None, draftId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: update_draft_email
        method: PUT
        path: /emails/mailboxes/{email}/drafts/{draftId}
        summary: Update the specified draft email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/drafts/{draftId}"
        params = { 'email': email, 'draftId': draftId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_draft_email(self, email: Optional[Any] = None, draftId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_draft_email
        method: DELETE
        path: /emails/mailboxes/{email}/drafts/{draftId}
        summary: Delete an existing draft email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/drafts/{draftId}"
        params = { 'email': email, 'draftId': draftId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_mailbox_history(self, email: Optional[Any] = None, maxResults: Optional[Any] = None, pageToken: Optional[Any] = None, startHistoryId: Optional[Any] = None, historyTypes: Optional[Any] = None, excludeHistoryTypes: Optional[Any] = None, labelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_mailbox_history
        method: GET
        path: /emails/mailboxes/{email}/history
        summary: List history of events for mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/history"
        params = { 'email': email, 'maxResults': maxResults, 'pageToken': pageToken, 'startHistoryId': startHistoryId, 'historyTypes': historyTypes, 'excludeHistoryTypes': excludeHistoryTypes, 'labelId': labelId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_labels_in_mailbox(self, email: Optional[Any] = None, format: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_labels_in_mailbox
        method: GET
        path: /emails/mailboxes/{email}/labels
        summary: List labels in the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/labels"
        params = { 'email': email, 'format': format }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_label_in_mailbox(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: create_label_in_mailbox
        method: POST
        path: /emails/mailboxes/{email}/labels
        summary: Create a new label in mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/labels"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_label_in_mailbox(self, email: Optional[Any] = None, labelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_label_in_mailbox
        method: GET
        path: /emails/mailboxes/{email}/labels/{labelId}
        summary: Get the specified label in mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/labels/{labelId}"
        params = { 'email': email, 'labelId': labelId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_label_in_mailbox(self, email: Optional[Any] = None, labelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: update_label_in_mailbox
        method: PUT
        path: /emails/mailboxes/{email}/labels/{labelId}
        summary: Update the specified label in mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/labels/{labelId}"
        params = { 'email': email, 'labelId': labelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_label_from_mailbox(self, email: Optional[Any] = None, labelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_label_from_mailbox
        method: DELETE
        path: /emails/mailboxes/{email}/labels/{labelId}
        summary: Delete an existing label from mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/labels/{labelId}"
        params = { 'email': email, 'labelId': labelId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patch_label_in_mailbox(self, email: Optional[Any] = None, labelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: patch_label_in_mailbox
        method: PATCH
        path: /emails/mailboxes/{email}/labels/{labelId}
        summary: Patch the specified label in mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/labels/{labelId}"
        params = { 'email': email, 'labelId': labelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_mailbox_profile(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_mailbox_profile
        method: GET
        path: /emails/mailboxes/{email}/profile
        summary: Get the mailbox profile
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/profile"
        params = { 'email': email }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_emails(self, email: Optional[Any] = None, maxResults: Optional[Any] = None, pageToken: Optional[Any] = None, labelIds: Optional[Any] = None, q: Optional[Any] = None, includeSpamTrash: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_emails
        method: GET
        path: /emails/mailboxes/{email}/messages
        summary: List emails from the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages"
        params = { 'email': email, 'maxResults': maxResults, 'pageToken': pageToken, 'labelIds': labelIds, 'q': q, 'includeSpamTrash': includeSpamTrash }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_email(self, email: Optional[Any] = None, deleted: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: create_email
        method: POST
        path: /emails/mailboxes/{email}/messages
        summary: Create a new email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages"
        params = { 'email': email, 'deleted': deleted }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_delete_emails(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batch_delete_emails
        method: POST
        path: /emails/mailboxes/{email}/messages/batchDelete
        summary: Batch delete the specified emails
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/batchDelete"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_modify_emails(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batch_modify_emails
        method: POST
        path: /emails/mailboxes/{email}/messages/batchModify
        summary: Batch modify the specified emails
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/batchModify"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def send_email(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: send_email
        method: POST
        path: /emails/mailboxes/{email}/messages/send
        summary: Send out an email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/send"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_email(self, email: Optional[Any] = None, messageId: Optional[Any] = None, format: Optional[Any] = None, metadataHeaders: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_email
        method: GET
        path: /emails/mailboxes/{email}/messages/{messageId}
        summary: Get the specified email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/{messageId}"
        params = { 'email': email, 'messageId': messageId, 'format': format, 'metadataHeaders': metadataHeaders }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_email(self, email: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_email
        method: DELETE
        path: /emails/mailboxes/{email}/messages/{messageId}
        summary: Delete an existing email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/{messageId}"
        params = { 'email': email, 'messageId': messageId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_email(self, email: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: update_email
        method: POST
        path: /emails/mailboxes/{email}/messages/{messageId}/modify
        summary: Update the specified email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/{messageId}/modify"
        params = { 'email': email, 'messageId': messageId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def trash_email(self, email: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trash_email
        method: POST
        path: /emails/mailboxes/{email}/messages/{messageId}/trash
        summary: Move the specified email to TRASH folder
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/{messageId}/trash"
        params = { 'email': email, 'messageId': messageId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def untrash_email(self, email: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: untrash_email
        method: POST
        path: /emails/mailboxes/{email}/messages/{messageId}/untrash
        summary: Move the specified email out of TRASH folder
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/{messageId}/untrash"
        params = { 'email': email, 'messageId': messageId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_email_attachment(self, email: Optional[Any] = None, messageId: Optional[Any] = None, attachmentId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_email_attachment
        method: GET
        path: /emails/mailboxes/{email}/messages/{messageId}/attachments/{attachmentId}
        summary: Get the specified attachment for an email
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/messages/{messageId}/attachments/{attachmentId}"
        params = { 'email': email, 'messageId': messageId, 'attachmentId': attachmentId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_mail_vacation_response_setting(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_mail_vacation_response_setting
        method: GET
        path: /emails/mailboxes/{email}/settings/vacation
        summary: Get mailbox vacation response setting
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/vacation"
        params = { 'email': email }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_mailbox_vacation_response_setting(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: update_mailbox_vacation_response_setting
        method: PUT
        path: /emails/mailboxes/{email}/settings/vacation
        summary: Update mailbox vacation response setting
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/vacation"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_mailbox_delegates(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_mailbox_delegates
        method: GET
        path: /emails/mailboxes/{email}/settings/delegates
        summary: List delegates on the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/delegates"
        params = { 'email': email }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def grant_mailbox_delegate(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: grant_mailbox_delegate
        method: POST
        path: /emails/mailboxes/{email}/settings/delegates
        summary: Grant a new delegate access on the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/delegates"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_mailbox_delegate(self, email: Optional[Any] = None, delegateEmail: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_mailbox_delegate
        method: GET
        path: /emails/mailboxes/{email}/settings/delegates/{delegateEmail}
        summary: Get the specified delegate on the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/delegates/{delegateEmail}"
        params = { 'email': email, 'delegateEmail': delegateEmail }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def revoke_mailbox_delegate(self, email: Optional[Any] = None, delegateEmail: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: revoke_mailbox_delegate
        method: DELETE
        path: /emails/mailboxes/{email}/settings/delegates/{delegateEmail}
        summary: Revoke an existing delegate access from the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/delegates/{delegateEmail}"
        params = { 'email': email, 'delegateEmail': delegateEmail }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_email_filters(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_email_filters
        method: GET
        path: /emails/mailboxes/{email}/settings/filters
        summary: List email filters
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/filters"
        params = { 'email': email }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_email_filter(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: create_email_filter
        method: POST
        path: /emails/mailboxes/{email}/settings/filters
        summary: Create an email filter
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/filters"
        params = { 'email': email }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_email_filter(self, email: Optional[Any] = None, filterId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_email_filter
        method: GET
        path: /emails/mailboxes/{email}/settings/filters/{filterId}
        summary: Get the specified email filter
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/filters/{filterId}"
        params = { 'email': email, 'filterId': filterId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_email_filter(self, email: Optional[Any] = None, filterId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_email_filter
        method: DELETE
        path: /emails/mailboxes/{email}/settings/filters/{filterId}
        summary: Delete the specified email filter
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/settings/filters/{filterId}"
        params = { 'email': email, 'filterId': filterId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_email_threads(self, email: Optional[Any] = None, includeSpamTrash: Optional[Any] = None, labelIds: Optional[Any] = None, maxResults: Optional[Any] = None, pageToken: Optional[Any] = None, q: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_email_threads
        method: GET
        path: /emails/mailboxes/{email}/threads
        summary: List email threads from the mailbox
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/threads"
        params = { 'email': email, 'includeSpamTrash': includeSpamTrash, 'labelIds': labelIds, 'maxResults': maxResults, 'pageToken': pageToken, 'q': q }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_email_thread(self, email: Optional[Any] = None, threadId: Optional[Any] = None, format: Optional[Any] = None, metadataHeaders: Optional[Any] = None, maxResults: Optional[Any] = None, pageToken: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_email_thread
        method: GET
        path: /emails/mailboxes/{email}/threads/{threadId}
        summary: Get the specified email thread
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/threads/{threadId}"
        params = { 'email': email, 'threadId': threadId, 'format': format, 'metadataHeaders': metadataHeaders, 'maxResults': maxResults, 'pageToken': pageToken }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_email_thread(self, email: Optional[Any] = None, threadId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_email_thread
        method: DELETE
        path: /emails/mailboxes/{email}/threads/{threadId}
        summary: Delete an existing email thread
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/threads/{threadId}"
        params = { 'email': email, 'threadId': threadId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_email_thread(self, email: Optional[Any] = None, threadId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: update_email_thread
        method: POST
        path: /emails/mailboxes/{email}/threads/{threadId}/modify
        summary: Update the specified thread
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/threads/{threadId}/modify"
        params = { 'email': email, 'threadId': threadId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def trash_email_thread(self, email: Optional[Any] = None, threadId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trash_email_thread
        method: POST
        path: /emails/mailboxes/{email}/threads/{threadId}/trash
        summary: Move the specified thread to TRASH folder
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/threads/{threadId}/trash"
        params = { 'email': email, 'threadId': threadId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def untrash_email_thread(self, email: Optional[Any] = None, threadId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: untrash_email_thread
        method: POST
        path: /emails/mailboxes/{email}/threads/{threadId}/untrash
        summary: Move the specified thread out of TRASH folder
        """
        endpoint = f"{self._base_url}/emails/mailboxes/{email}/threads/{threadId}/untrash"
        params = { 'email': email, 'threadId': threadId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_archived_files(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, query_date_type: Optional[Any] = None, group_id: Optional[Any] = None, group_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listArchivedFiles
        method: GET
        path: /archive_files
        summary: List archived files
        """
        endpoint = f"{self._base_url}/archive_files"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'query_date_type': query_date_type, 'group_id': group_id, 'group_ids': group_ids }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_archived_file_statistics(self, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getArchivedFileStatistics
        method: GET
        path: /archive_files/statistics
        summary: Get archived file statistics
        """
        endpoint = f"{self._base_url}/archive_files/statistics"
        params = { 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_archived_file(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateArchivedFile
        method: PATCH
        path: /archive_files/{fileId}
        summary: Update an archived file's auto-delete status
        """
        endpoint = f"{self._base_url}/archive_files/{fileId}"
        params = { 'fileId': fileId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_archived_files(self, meetingUUID: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getArchivedFiles
        method: GET
        path: /past_meetings/{meetingUUID}/archive_files
        summary: Get a meeting's archived files
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingUUID}/archive_files"
        params = { 'meetingUUID': meetingUUID }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_archived_files(self, meetingUUID: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteArchivedFiles
        method: DELETE
        path: /past_meetings/{meetingUUID}/archive_files
        summary: Delete a meeting's archived files
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingUUID}/archive_files"
        params = { 'meetingUUID': meetingUUID }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_get(self, meetingId: Optional[Any] = None, include_fields: Optional[Any] = None, ttl: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingGet
        method: GET
        path: /meetings/{meetingId}/recordings
        summary: Get meeting recordings
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings"
        params = { 'meetingId': meetingId, 'include_fields': include_fields, 'ttl': ttl }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_delete(self, meetingId: Optional[Any] = None, action: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingDelete
        method: DELETE
        path: /meetings/{meetingId}/recordings
        summary: Delete meeting or webinar recordings
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings"
        params = { 'meetingId': meetingId, 'action': action }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def analytics_details(self, meetingId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: analytics_details
        method: GET
        path: /meetings/{meetingId}/recordings/analytics_details
        summary: Get a meeting or webinar recording's analytics details
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/analytics_details"
        params = { 'meetingId': meetingId, 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def analytics_summary(self, meetingId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: analytics_summary
        method: GET
        path: /meetings/{meetingId}/recordings/analytics_summary
        summary: Get a meeting or webinar recording's analytics summary
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/analytics_summary"
        params = { 'meetingId': meetingId, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_recording_registrants(self, meetingId: Optional[Any] = None, status: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRecordingRegistrants
        method: GET
        path: /meetings/{meetingId}/recordings/registrants
        summary: List recording registrants
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/registrants"
        params = { 'meetingId': meetingId, 'status': status, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_recording_registrant_create(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRecordingRegistrantCreate
        method: POST
        path: /meetings/{meetingId}/recordings/registrants
        summary: Create a recording registrant
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/registrants"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_registrants_questions_get(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingRegistrantsQuestionsGet
        method: GET
        path: /meetings/{meetingId}/recordings/registrants/questions
        summary: Get registration questions
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/registrants/questions"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_registrant_question_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingRegistrantQuestionUpdate
        method: PATCH
        path: /meetings/{meetingId}/recordings/registrants/questions
        summary: Update registration questions
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/registrants/questions"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_recording_registrant_status(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRecordingRegistrantStatus
        method: PUT
        path: /meetings/{meetingId}/recordings/registrants/status
        summary: Update a registrant's status
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/registrants/status"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_setting_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingSettingUpdate
        method: GET
        path: /meetings/{meetingId}/recordings/settings
        summary: Get meeting recording settings
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/settings"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_settings_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingSettingsUpdate
        method: PATCH
        path: /meetings/{meetingId}/recordings/settings
        summary: Update meeting recording settings
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/settings"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_delete_one(self, meetingId: Optional[Any] = None, recordingId: Optional[Any] = None, action: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingDeleteOne
        method: DELETE
        path: /meetings/{meetingId}/recordings/{recordingId}
        summary: Delete a recording file for a meeting or webinar
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/{recordingId}"
        params = { 'meetingId': meetingId, 'recordingId': recordingId, 'action': action }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_status_update_one(self, meetingId: Optional[Any] = None, recordingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingStatusUpdateOne
        method: PUT
        path: /meetings/{meetingId}/recordings/{recordingId}/status
        summary: Recover a single recording
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/recordings/{recordingId}/status"
        params = { 'meetingId': meetingId, 'recordingId': recordingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_meeting_transcript(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetMeetingTranscript
        method: GET
        path: /meetings/{meetingId}/transcript
        summary: Get a meeting transcript
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/transcript"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_meeting_transcript(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteMeetingTranscript
        method: DELETE
        path: /meetings/{meetingId}/transcript
        summary: Delete a meeting or webinar transcript
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/transcript"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def recording_status_update(self, meetingUUID: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingStatusUpdate
        method: PUT
        path: /meetings/{meetingUUID}/recordings/status
        summary: Recover meeting recordings
        """
        endpoint = f"{self._base_url}/meetings/{meetingUUID}/recordings/status"
        params = { 'meetingUUID': meetingUUID }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def recordings_list(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, mc: Optional[Any] = None, trash: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, trash_type: Optional[Any] = None, meeting_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: recordingsList
        method: GET
        path: /users/{userId}/recordings
        summary: List all recordings
        """
        endpoint = f"{self._base_url}/users/{userId}/recordings"
        params = { 'userId': userId, 'page_size': page_size, 'next_page_token': next_page_token, 'mc': mc, 'trash': trash, 'from': from_, 'to': to, 'trash_type': trash_type, 'meeting_id': meeting_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_devices(self, search_text: Optional[Any] = None, platform_os: Optional[Any] = None, is_enrolled_in_zdm: Optional[Any] = None, device_type: Optional[Any] = None, device_vendor: Optional[Any] = None, device_model: Optional[Any] = None, device_status: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listDevices
        method: GET
        path: /devices
        summary: List devices
        """
        endpoint = f"{self._base_url}/devices"
        params = { 'search_text': search_text, 'platform_os': platform_os, 'is_enrolled_in_zdm': is_enrolled_in_zdm, 'device_type': device_type, 'device_vendor': device_vendor, 'device_model': device_model, 'device_status': device_status, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_device(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addDevice
        method: POST
        path: /devices
        summary: Add a new device
        """
        endpoint = f"{self._base_url}/devices"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getzdmgroupinfo(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getzdmgroupinfo
        method: GET
        path: /devices/groups
        summary: Get ZDM group info
        """
        endpoint = f"{self._base_url}/devices/groups"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def assigndevicetoauser_commonarea(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Assigndevicetoauser/commonarea
        method: POST
        path: /devices/zpa/assignment
        summary: Assign a device to a user or commonarea
        """
        endpoint = f"{self._base_url}/devices/zpa/assignment"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zpa_device_list_profile_setting_ofa_user(self, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetZpaDeviceListProfileSettingOfaUser
        method: GET
        path: /devices/zpa/settings
        summary: Get Zoom Phone Appliance settings by user ID
        """
        endpoint = f"{self._base_url}/devices/zpa/settings"
        params = { 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def upgrade_zpas_app(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpgradeZpas/app
        method: POST
        path: /devices/zpa/upgrade
        summary: Upgrade ZPA firmware or app
        """
        endpoint = f"{self._base_url}/devices/zpa/upgrade"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_zpa_device_by_vendor_and_mac_address(self, vendor: Optional[Any] = None, macAddress: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteZpaDeviceByVendorAndMacAddress
        method: DELETE
        path: /devices/zpa/vendors/{vendor}/mac_addresses/{macAddress}
        summary: Delete ZPA device by vendor and mac address
        """
        endpoint = f"{self._base_url}/devices/zpa/vendors/{vendor}/mac_addresses/{macAddress}"
        params = { 'vendor': vendor, 'macAddress': macAddress }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zpa_versioninfo(self, zdmGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetZpaVersioninfo
        method: GET
        path: /devices/zpa/zdm_groups/{zdmGroupId}/versions
        summary: Get ZPA version info
        """
        endpoint = f"{self._base_url}/devices/zpa/zdm_groups/{zdmGroupId}/versions"
        params = { 'zdmGroupId': zdmGroupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getDevice
        method: GET
        path: /devices/{deviceId}
        summary: Get device detail
        """
        endpoint = f"{self._base_url}/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteDevice
        method: DELETE
        path: /devices/{deviceId}
        summary: Delete device
        """
        endpoint = f"{self._base_url}/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateDevice
        method: PATCH
        path: /devices/{deviceId}
        summary: Change device 
        """
        endpoint = f"{self._base_url}/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def assgin_group(self, deviceId: Optional[Any] = None, group_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assginGroup
        method: PATCH
        path: /devices/{deviceId}/assign_group
        summary: Assign a device to a group
        """
        endpoint = f"{self._base_url}/devices/{deviceId}/assign_group"
        params = { 'deviceId': deviceId, 'group_id': group_id }
        body = None
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def change_device_association(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: changeDeviceAssociation
        method: PATCH
        path: /devices/{deviceId}/assignment
        summary: Change device association
        """
        endpoint = f"{self._base_url}/devices/{deviceId}/assignment"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def device_list(self, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deviceList
        method: GET
        path: /h323/devices
        summary: List H.323/SIP devices
        """
        endpoint = f"{self._base_url}/h323/devices"
        params = { 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def device_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deviceCreate
        method: POST
        path: /h323/devices
        summary: Create a H.323/SIP device
        """
        endpoint = f"{self._base_url}/h323/devices"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def device_delete(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deviceDelete
        method: DELETE
        path: /h323/devices/{deviceId}
        summary: Delete a H.323/SIP device
        """
        endpoint = f"{self._base_url}/h323/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def device_update(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deviceUpdate
        method: PATCH
        path: /h323/devices/{deviceId}
        summary: Update a H.323/SIP device
        """
        endpoint = f"{self._base_url}/h323/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_meeting_chat_message_by_id(self, meetingId: Optional[Any] = None, messageId: Optional[Any] = None, file_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteMeetingChatMessageById
        method: DELETE
        path: /live_meetings/{meetingId}/chat/messages/{messageId}
        summary: Delete a live meeting message
        """
        endpoint = f"{self._base_url}/live_meetings/{meetingId}/chat/messages/{messageId}"
        params = { 'meetingId': meetingId, 'messageId': messageId, 'file_ids': file_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_meeting_chat_message_by_id(self, meetingId: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateMeetingChatMessageById
        method: PATCH
        path: /live_meetings/{meetingId}/chat/messages/{messageId}
        summary: Update a live meeting message
        """
        endpoint = f"{self._base_url}/live_meetings/{meetingId}/chat/messages/{messageId}"
        params = { 'meetingId': meetingId, 'messageId': messageId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def in_meeting_control(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: inMeetingControl
        method: PATCH
        path: /live_meetings/{meetingId}/events
        summary: Use in-meeting controls
        """
        endpoint = f"{self._base_url}/live_meetings/{meetingId}/events"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_rtms_status_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRTMSStatusUpdate
        method: PATCH
        path: /live_meetings/{meetingId}/rtms_app/status
        summary: Update participant Real-Time Media Streams (RTMS) app status
        """
        endpoint = f"{self._base_url}/live_meetings/{meetingId}/rtms_app/status"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def listmeetingsummaries(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listmeetingsummaries
        method: GET
        path: /meetings/meeting_summaries
        summary: List an account's meeting or webinar summaries
        """
        endpoint = f"{self._base_url}/meetings/meeting_summaries"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting(self, meetingId: Optional[Any] = None, occurrence_id: Optional[Any] = None, show_previous_occurrences: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meeting
        method: GET
        path: /meetings/{meetingId}
        summary: Get a meeting
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}"
        params = { 'meetingId': meetingId, 'occurrence_id': occurrence_id, 'show_previous_occurrences': show_previous_occurrences }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_delete(self, meetingId: Optional[Any] = None, occurrence_id: Optional[Any] = None, schedule_for_reminder: Optional[Any] = None, cancel_meeting_reminder: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingDelete
        method: DELETE
        path: /meetings/{meetingId}
        summary: Delete a meeting
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}"
        params = { 'meetingId': meetingId, 'occurrence_id': occurrence_id, 'schedule_for_reminder': schedule_for_reminder, 'cancel_meeting_reminder': cancel_meeting_reminder }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_update(self, meetingId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingUpdate
        method: PATCH
        path: /meetings/{meetingId}
        summary: Update a meeting
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}"
        params = { 'meetingId': meetingId, 'occurrence_id': occurrence_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_batch_polls(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createBatchPolls
        method: POST
        path: /meetings/{meetingId}/batch_polls
        summary: Perform batch poll creation
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/batch_polls"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_batch_registrants(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addBatchRegistrants
        method: POST
        path: /meetings/{meetingId}/batch_registrants
        summary: Perform batch registration
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/batch_registrants"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_invitation(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingInvitation
        method: GET
        path: /meetings/{meetingId}/invitation
        summary: Get meeting invitation
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/invitation"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_invite_links_create(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingInviteLinksCreate
        method: POST
        path: /meetings/{meetingId}/invite_links
        summary: Create a meeting's invite links
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/invite_links"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_live_streaming_join_token(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingLiveStreamingJoinToken
        method: GET
        path: /meetings/{meetingId}/jointoken/live_streaming
        summary: Get a meeting's join token for live streaming
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/jointoken/live_streaming"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_local_archiving_archive_token(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingLocalArchivingArchiveToken
        method: GET
        path: /meetings/{meetingId}/jointoken/local_archiving
        summary: Get a meeting's archive token for local archiving
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/jointoken/local_archiving"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_local_recording_join_token(self, meetingId: Optional[Any] = None, bypass_waiting_room: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingLocalRecordingJoinToken
        method: GET
        path: /meetings/{meetingId}/jointoken/local_recording
        summary: Get a meeting's join token for local recording
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/jointoken/local_recording"
        params = { 'meetingId': meetingId, 'bypass_waiting_room': bypass_waiting_room }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_meeting_live_stream_details(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getMeetingLiveStreamDetails
        method: GET
        path: /meetings/{meetingId}/livestream
        summary: Get livestream details
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/livestream"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_live_stream_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingLiveStreamUpdate
        method: PATCH
        path: /meetings/{meetingId}/livestream
        summary: Update a livestream
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/livestream"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_live_stream_status_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingLiveStreamStatusUpdate
        method: PATCH
        path: /meetings/{meetingId}/livestream/status
        summary: Update livestream status
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/livestream/status"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getameetingsummary(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getameetingsummary
        method: GET
        path: /meetings/{meetingId}/meeting_summary
        summary: Get a meeting or webinar summary
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/meeting_summary"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deletemeetingorwebinarsummary(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deletemeetingorwebinarsummary
        method: DELETE
        path: /meetings/{meetingId}/meeting_summary
        summary: Delete a meeting or webinar summary
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/meeting_summary"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_app_add(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingAppAdd
        method: POST
        path: /meetings/{meetingId}/open_apps
        summary: Add a meeting app
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/open_apps"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_app_delete(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingAppDelete
        method: DELETE
        path: /meetings/{meetingId}/open_apps
        summary: Delete a meeting app
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/open_apps"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_polls(self, meetingId: Optional[Any] = None, anonymous: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingPolls
        method: GET
        path: /meetings/{meetingId}/polls
        summary: List meeting polls
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/polls"
        params = { 'meetingId': meetingId, 'anonymous': anonymous }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_poll_create(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingPollCreate
        method: POST
        path: /meetings/{meetingId}/polls
        summary: Create a meeting poll
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/polls"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_poll_get(self, meetingId: Optional[Any] = None, pollId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingPollGet
        method: GET
        path: /meetings/{meetingId}/polls/{pollId}
        summary: Get a meeting poll
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/polls/{pollId}"
        params = { 'meetingId': meetingId, 'pollId': pollId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_poll_update(self, meetingId: Optional[Any] = None, pollId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingPollUpdate
        method: PUT
        path: /meetings/{meetingId}/polls/{pollId}
        summary: Update a meeting poll
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/polls/{pollId}"
        params = { 'meetingId': meetingId, 'pollId': pollId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_poll_delete(self, meetingId: Optional[Any] = None, pollId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingPollDelete
        method: DELETE
        path: /meetings/{meetingId}/polls/{pollId}
        summary: Delete a meeting poll
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/polls/{pollId}"
        params = { 'meetingId': meetingId, 'pollId': pollId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_registrants(self, meetingId: Optional[Any] = None, occurrence_id: Optional[Any] = None, status: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRegistrants
        method: GET
        path: /meetings/{meetingId}/registrants
        summary: List meeting registrants
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants"
        params = { 'meetingId': meetingId, 'occurrence_id': occurrence_id, 'status': status, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_registrant_create(self, meetingId: Optional[Any] = None, occurrence_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRegistrantCreate
        method: POST
        path: /meetings/{meetingId}/registrants
        summary: Add a meeting registrant
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants"
        params = { 'meetingId': meetingId, 'occurrence_ids': occurrence_ids }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_registrants_questions_get(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRegistrantsQuestionsGet
        method: GET
        path: /meetings/{meetingId}/registrants/questions
        summary: List registration questions 
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants/questions"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_registrant_question_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRegistrantQuestionUpdate
        method: PATCH
        path: /meetings/{meetingId}/registrants/questions
        summary: Update registration questions
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants/questions"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_registrant_status(self, meetingId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRegistrantStatus
        method: PUT
        path: /meetings/{meetingId}/registrants/status
        summary: Update registrant's status
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants/status"
        params = { 'meetingId': meetingId, 'occurrence_id': occurrence_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_registrant_get(self, meetingId: Optional[Any] = None, registrantId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingRegistrantGet
        method: GET
        path: /meetings/{meetingId}/registrants/{registrantId}
        summary: Get a meeting registrant
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants/{registrantId}"
        params = { 'meetingId': meetingId, 'registrantId': registrantId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meetingregistrantdelete(self, meetingId: Optional[Any] = None, registrantId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingregistrantdelete
        method: DELETE
        path: /meetings/{meetingId}/registrants/{registrantId}
        summary: Delete a meeting registrant
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/registrants/{registrantId}"
        params = { 'meetingId': meetingId, 'registrantId': registrantId, 'occurrence_id': occurrence_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_sip_dialing_with_passcode(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSipDialingWithPasscode
        method: POST
        path: /meetings/{meetingId}/sip_dialing
        summary: Get a meeting SIP URI with passcode
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/sip_dialing"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_status(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingStatus
        method: PUT
        path: /meetings/{meetingId}/status
        summary: Update meeting status
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/status"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_survey_get(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingSurveyGet
        method: GET
        path: /meetings/{meetingId}/survey
        summary: Get a meeting survey
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/survey"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_survey_delete(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingSurveyDelete
        method: DELETE
        path: /meetings/{meetingId}/survey
        summary: Delete a meeting survey
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/survey"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_survey_update(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingSurveyUpdate
        method: PATCH
        path: /meetings/{meetingId}/survey
        summary: Update a meeting survey
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/survey"
        params = { 'meetingId': meetingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_token(self, meetingId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingToken
        method: GET
        path: /meetings/{meetingId}/token
        summary: Get meeting's token
        """
        endpoint = f"{self._base_url}/meetings/{meetingId}/token"
        params = { 'meetingId': meetingId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def past_meeting_details(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: pastMeetingDetails
        method: GET
        path: /past_meetings/{meetingId}
        summary: Get past meeting details
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingId}"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def past_meetings(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: pastMeetings
        method: GET
        path: /past_meetings/{meetingId}/instances
        summary: List past meeting instances
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingId}/instances"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def past_meeting_participants(self, meetingId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: pastMeetingParticipants
        method: GET
        path: /past_meetings/{meetingId}/participants
        summary: Get past meeting participants
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingId}/participants"
        params = { 'meetingId': meetingId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_past_meeting_polls(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPastMeetingPolls
        method: GET
        path: /past_meetings/{meetingId}/polls
        summary: List past meeting's poll results
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingId}/polls"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_past_meeting_qa(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPastMeetingQA
        method: GET
        path: /past_meetings/{meetingId}/qa
        summary: List past meetings' Q&A
        """
        endpoint = f"{self._base_url}/past_meetings/{meetingId}/qa"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_meeting_templates(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listMeetingTemplates
        method: GET
        path: /users/{userId}/meeting_templates
        summary: List meeting templates
        """
        endpoint = f"{self._base_url}/users/{userId}/meeting_templates"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_template_create(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingTemplateCreate
        method: POST
        path: /users/{userId}/meeting_templates
        summary: Create a meeting template from an existing meeting
        """
        endpoint = f"{self._base_url}/users/{userId}/meeting_templates"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def meetings(self, userId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, page_number: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timezone: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetings
        method: GET
        path: /users/{userId}/meetings
        summary: List meetings
        """
        endpoint = f"{self._base_url}/users/{userId}/meetings"
        params = { 'userId': userId, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token, 'page_number': page_number, 'from': from_, 'to': to, 'timezone': timezone }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def meeting_create(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: meetingCreate
        method: POST
        path: /users/{userId}/meetings
        summary: Create a meeting
        """
        endpoint = f"{self._base_url}/users/{userId}/meetings"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_upcoming_meeting(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUpcomingMeeting
        method: GET
        path: /users/{userId}/upcoming_meetings
        summary: List upcoming meetings
        """
        endpoint = f"{self._base_url}/users/{userId}/upcoming_meetings"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_pa_cs(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userPACs
        method: GET
        path: /users/{userId}/pac
        summary: List a user's PAC accounts
        """
        endpoint = f"{self._base_url}/users/{userId}/pac"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_sign_in_sign_out_activities(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportSignInSignOutActivities
        method: GET
        path: /report/activities
        summary: Get sign In / sign out activity report
        """
        endpoint = f"{self._base_url}/report/activities"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_billing_report(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getBillingReport
        method: GET
        path: /report/billing
        summary: Get billing reports
        """
        endpoint = f"{self._base_url}/report/billing"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_billing_invoices_reports(self, billing_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getBillingInvoicesReports
        method: GET
        path: /report/billing/invoices
        summary: Get billing invoice reports
        """
        endpoint = f"{self._base_url}/report/billing/invoices"
        params = { 'billing_id': billing_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_cloud_recording(self, from_: Optional[Any] = None, to: Optional[Any] = None, group_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportCloudRecording
        method: GET
        path: /report/cloud_recording
        summary: Get cloud recording usage report
        """
        endpoint = f"{self._base_url}/report/cloud_recording"
        params = { 'from': from_, 'to': to, 'group_id': group_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_daily(self, year: Optional[Any] = None, month: Optional[Any] = None, group_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportDaily
        method: GET
        path: /report/daily
        summary: Get daily usage report
        """
        endpoint = f"{self._base_url}/report/daily"
        params = { 'year': year, 'month': month, 'group_id': group_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def gethistorymeetingandwebinarlist(self, from_: Optional[Any] = None, to: Optional[Any] = None, date_type: Optional[Any] = None, meeting_type: Optional[Any] = None, report_type: Optional[Any] = None, search_key: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, group_id: Optional[Any] = None, meeting_feature: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Gethistorymeetingandwebinarlist
        method: GET
        path: /report/history_meetings
        summary: Get history meeting and webinar list
        """
        endpoint = f"{self._base_url}/report/history_meetings"
        params = { 'from': from_, 'to': to, 'date_type': date_type, 'meeting_type': meeting_type, 'report_type': report_type, 'search_key': search_key, 'page_size': page_size, 'next_page_token': next_page_token, 'group_id': group_id, 'meeting_feature': meeting_feature }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meetingactivitylogs(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, meeting_number: Optional[Any] = None, search_key: Optional[Any] = None, activity_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetingactivitylogs
        method: GET
        path: /report/meeting_activities
        summary: Get a meeting activities report
        """
        endpoint = f"{self._base_url}/report/meeting_activities"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'meeting_number': meeting_number, 'search_key': search_key, 'activity_type': activity_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meeting_details(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetingDetails
        method: GET
        path: /report/meetings/{meetingId}
        summary: Get meeting detail reports
        """
        endpoint = f"{self._base_url}/report/meetings/{meetingId}"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meeting_participants(self, meetingId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, include_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetingParticipants
        method: GET
        path: /report/meetings/{meetingId}/participants
        summary: Get meeting participant reports
        """
        endpoint = f"{self._base_url}/report/meetings/{meetingId}/participants"
        params = { 'meetingId': meetingId, 'page_size': page_size, 'next_page_token': next_page_token, 'include_fields': include_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meeting_polls(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetingPolls
        method: GET
        path: /report/meetings/{meetingId}/polls
        summary: Get meeting poll reports
        """
        endpoint = f"{self._base_url}/report/meetings/{meetingId}/polls"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meeting_qa(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetingQA
        method: GET
        path: /report/meetings/{meetingId}/qa
        summary: Get meeting Q&A report
        """
        endpoint = f"{self._base_url}/report/meetings/{meetingId}/qa"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meeting_survey(self, meetingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetingSurvey
        method: GET
        path: /report/meetings/{meetingId}/survey
        summary: Get meeting survey report
        """
        endpoint = f"{self._base_url}/report/meetings/{meetingId}/survey"
        params = { 'meetingId': meetingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_operation_logs(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, category_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportOperationLogs
        method: GET
        path: /report/operationlogs
        summary: Get operation logs report
        """
        endpoint = f"{self._base_url}/report/operationlogs"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'category_type': category_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def getremotesupportreport(self, from_: Optional[Any] = None, to: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getremotesupportreport
        method: GET
        path: /report/remote_support
        summary: Get remote support report
        """
        endpoint = f"{self._base_url}/report/remote_support"
        params = { 'from': from_, 'to': to, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_telephone(self, type_: Optional[Any] = None, query_date_type: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportTelephone
        method: GET
        path: /report/telephone
        summary: Get telephone reports
        """
        endpoint = f"{self._base_url}/report/telephone"
        params = { 'type': type_, 'query_date_type': query_date_type, 'from': from_, 'to': to, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_upcoming_events(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, type_: Optional[Any] = None, group_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportUpcomingEvents
        method: GET
        path: /report/upcoming_events
        summary: Get upcoming events report
        """
        endpoint = f"{self._base_url}/report/upcoming_events"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'type': type_, 'group_id': group_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_users(self, type_: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, group_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportUsers
        method: GET
        path: /report/users
        summary: Get active or inactive host reports
        """
        endpoint = f"{self._base_url}/report/users"
        params = { 'type': type_, 'from': from_, 'to': to, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token, 'group_id': group_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_meetings(self, userId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportMeetings
        method: GET
        path: /report/users/{userId}/meetings
        summary: Get meeting reports
        """
        endpoint = f"{self._base_url}/report/users/{userId}/meetings"
        params = { 'userId': userId, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_webinar_details(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportWebinarDetails
        method: GET
        path: /report/webinars/{webinarId}
        summary: Get webinar detail reports
        """
        endpoint = f"{self._base_url}/report/webinars/{webinarId}"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_webinar_participants(self, webinarId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, include_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportWebinarParticipants
        method: GET
        path: /report/webinars/{webinarId}/participants
        summary: Get webinar participant reports
        """
        endpoint = f"{self._base_url}/report/webinars/{webinarId}/participants"
        params = { 'webinarId': webinarId, 'page_size': page_size, 'next_page_token': next_page_token, 'include_fields': include_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_webinar_polls(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportWebinarPolls
        method: GET
        path: /report/webinars/{webinarId}/polls
        summary: Get webinar poll reports
        """
        endpoint = f"{self._base_url}/report/webinars/{webinarId}/polls"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_webinar_qa(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportWebinarQA
        method: GET
        path: /report/webinars/{webinarId}/qa
        summary: Get webinar Q&A report
        """
        endpoint = f"{self._base_url}/report/webinars/{webinarId}/qa"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_webinar_survey(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportWebinarSurvey
        method: GET
        path: /report/webinars/{webinarId}/survey
        summary: Get webinar survey report
        """
        endpoint = f"{self._base_url}/report/webinars/{webinarId}/survey"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_sip_phone_phones(self, search_key: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListSIPPhonePhones
        method: GET
        path: /sip_phones/phones
        summary: List SIP phones
        """
        endpoint = f"{self._base_url}/sip_phones/phones"
        params = { 'search_key': search_key, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def enable_sip_phone_phones(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: EnableSIPPhonePhones
        method: POST
        path: /sip_phones/phones
        summary: Enable SIP phone
        """
        endpoint = f"{self._base_url}/sip_phones/phones"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_sip_phone_phones(self, phoneId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteSIPPhonePhones
        method: DELETE
        path: /sip_phones/phones/{phoneId}
        summary: Delete SIP phone
        """
        endpoint = f"{self._base_url}/sip_phones/phones/{phoneId}"
        params = { 'phoneId': phoneId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_sip_phone_phones(self, phoneId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateSIPPhonePhones
        method: PATCH
        path: /sip_phones/phones/{phoneId}
        summary: Update SIP phone
        """
        endpoint = f"{self._base_url}/sip_phones/phones/{phoneId}"
        params = { 'phoneId': phoneId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def tsp(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: tsp
        method: GET
        path: /tsp
        summary: Get account's TSP information
        """
        endpoint = f"{self._base_url}/tsp"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def tsp_update(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: tspUpdate
        method: PATCH
        path: /tsp
        summary: Update an account's TSP information
        """
        endpoint = f"{self._base_url}/tsp"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_ts_ps(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userTSPs
        method: GET
        path: /users/{userId}/tsp
        summary: List user's TSP accounts
        """
        endpoint = f"{self._base_url}/users/{userId}/tsp"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_tsp_create(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userTSPCreate
        method: POST
        path: /users/{userId}/tsp
        summary: Add a user's TSP account
        """
        endpoint = f"{self._base_url}/users/{userId}/tsp"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def tsp_url_update(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: tspUrlUpdate
        method: PATCH
        path: /users/{userId}/tsp/settings
        summary: Set global dial-in URL for a TSP user
        """
        endpoint = f"{self._base_url}/users/{userId}/tsp/settings"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_tsp(self, userId: Optional[Any] = None, tspId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userTSP
        method: GET
        path: /users/{userId}/tsp/{tspId}
        summary: Get a user's TSP account
        """
        endpoint = f"{self._base_url}/users/{userId}/tsp/{tspId}"
        params = { 'userId': userId, 'tspId': tspId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_tsp_delete(self, userId: Optional[Any] = None, tspId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userTSPDelete
        method: DELETE
        path: /users/{userId}/tsp/{tspId}
        summary: Delete a user's TSP account
        """
        endpoint = f"{self._base_url}/users/{userId}/tsp/{tspId}"
        params = { 'userId': userId, 'tspId': tspId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_tsp_update(self, userId: Optional[Any] = None, tspId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userTSPUpdate
        method: PATCH
        path: /users/{userId}/tsp/{tspId}
        summary: Update a TSP account
        """
        endpoint = f"{self._base_url}/users/{userId}/tsp/{tspId}"
        params = { 'userId': userId, 'tspId': tspId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def trackingfield_list(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trackingfieldList
        method: GET
        path: /tracking_fields
        summary: List tracking fields
        """
        endpoint = f"{self._base_url}/tracking_fields"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def trackingfield_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trackingfieldCreate
        method: POST
        path: /tracking_fields
        summary: Create a tracking field
        """
        endpoint = f"{self._base_url}/tracking_fields"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def trackingfield_get(self, fieldId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trackingfieldGet
        method: GET
        path: /tracking_fields/{fieldId}
        summary: Get a tracking field
        """
        endpoint = f"{self._base_url}/tracking_fields/{fieldId}"
        params = { 'fieldId': fieldId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def trackingfield_delete(self, fieldId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trackingfieldDelete
        method: DELETE
        path: /tracking_fields/{fieldId}
        summary: Delete a tracking field
        """
        endpoint = f"{self._base_url}/tracking_fields/{fieldId}"
        params = { 'fieldId': fieldId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def trackingfield_update(self, fieldId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: trackingfieldUpdate
        method: PATCH
        path: /tracking_fields/{fieldId}
        summary: Update a tracking field
        """
        endpoint = f"{self._base_url}/tracking_fields/{fieldId}"
        params = { 'fieldId': fieldId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_webinar_chat_message_by_id(self, webinarId: Optional[Any] = None, messageId: Optional[Any] = None, file_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteWebinarChatMessageById
        method: DELETE
        path: /live_webinars/{webinarId}/chat/messages/{messageId}
        summary: Delete a live webinar message
        """
        endpoint = f"{self._base_url}/live_webinars/{webinarId}/chat/messages/{messageId}"
        params = { 'webinarId': webinarId, 'messageId': messageId, 'file_ids': file_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_absentees(self, webinarId: Optional[Any] = None, occurrence_id: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarAbsentees
        method: GET
        path: /past_webinars/{webinarId}/absentees
        summary: Get webinar absentees
        """
        endpoint = f"{self._base_url}/past_webinars/{webinarId}/absentees"
        params = { 'webinarId': webinarId, 'occurrence_id': occurrence_id, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def past_webinars(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: pastWebinars
        method: GET
        path: /past_webinars/{webinarId}/instances
        summary: List past webinar instances
        """
        endpoint = f"{self._base_url}/past_webinars/{webinarId}/instances"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_webinar_participants(self, webinarId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listWebinarParticipants
        method: GET
        path: /past_webinars/{webinarId}/participants
        summary: List webinar participants
        """
        endpoint = f"{self._base_url}/past_webinars/{webinarId}/participants"
        params = { 'webinarId': webinarId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_past_webinar_poll_results(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPastWebinarPollResults
        method: GET
        path: /past_webinars/{webinarId}/polls
        summary: List past webinar poll results
        """
        endpoint = f"{self._base_url}/past_webinars/{webinarId}/polls"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_past_webinar_qa(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPastWebinarQA
        method: GET
        path: /past_webinars/{webinarId}/qa
        summary: List Q&As of a past webinar
        """
        endpoint = f"{self._base_url}/past_webinars/{webinarId}/qa"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_webinar_templates(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listWebinarTemplates
        method: GET
        path: /users/{userId}/webinar_templates
        summary: List webinar templates
        """
        endpoint = f"{self._base_url}/users/{userId}/webinar_templates"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_template_create(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarTemplateCreate
        method: POST
        path: /users/{userId}/webinar_templates
        summary: Create a webinar template
        """
        endpoint = f"{self._base_url}/users/{userId}/webinar_templates"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinars(self, userId: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinars
        method: GET
        path: /users/{userId}/webinars
        summary: List webinars
        """
        endpoint = f"{self._base_url}/users/{userId}/webinars"
        params = { 'userId': userId, 'type': type_, 'page_size': page_size, 'page_number': page_number }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_create(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarCreate
        method: POST
        path: /users/{userId}/webinars
        summary: Create a webinar
        """
        endpoint = f"{self._base_url}/users/{userId}/webinars"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar(self, webinarId: Optional[Any] = None, occurrence_id: Optional[Any] = None, show_previous_occurrences: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinar
        method: GET
        path: /webinars/{webinarId}
        summary: Get a webinar
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}"
        params = { 'webinarId': webinarId, 'occurrence_id': occurrence_id, 'show_previous_occurrences': show_previous_occurrences }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_delete(self, webinarId: Optional[Any] = None, occurrence_id: Optional[Any] = None, cancel_webinar_reminder: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarDelete
        method: DELETE
        path: /webinars/{webinarId}
        summary: Delete a webinar
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}"
        params = { 'webinarId': webinarId, 'occurrence_id': occurrence_id, 'cancel_webinar_reminder': cancel_webinar_reminder }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_update(self, webinarId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarUpdate
        method: PATCH
        path: /webinars/{webinarId}
        summary: Update a webinar
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}"
        params = { 'webinarId': webinarId, 'occurrence_id': occurrence_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_batch_webinar_registrants(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addBatchWebinarRegistrants
        method: POST
        path: /webinars/{webinarId}/batch_registrants
        summary: Perform batch registration
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/batch_registrants"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_webinar_branding(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getWebinarBranding
        method: GET
        path: /webinars/{webinarId}/branding
        summary: Get webinar's session branding
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_webinar_branding_name_tag(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createWebinarBrandingNameTag
        method: POST
        path: /webinars/{webinarId}/branding/name_tags
        summary: Create a webinar's branding name tag
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/name_tags"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_webinar_branding_name_tag(self, webinarId: Optional[Any] = None, name_tag_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteWebinarBrandingNameTag
        method: DELETE
        path: /webinars/{webinarId}/branding/name_tags
        summary: Delete a webinar's branding name tag
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/name_tags"
        params = { 'webinarId': webinarId, 'name_tag_ids': name_tag_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_webinar_branding_name_tag(self, webinarId: Optional[Any] = None, nameTagId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateWebinarBrandingNameTag
        method: PATCH
        path: /webinars/{webinarId}/branding/name_tags/{nameTagId}
        summary: Update a webinar's branding name tag
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/name_tags/{nameTagId}"
        params = { 'webinarId': webinarId, 'nameTagId': nameTagId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_webinar_branding_vb(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: uploadWebinarBrandingVB
        method: POST
        path: /webinars/{webinarId}/branding/virtual_backgrounds
        summary: Upload a webinar's branding virtual background
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/virtual_backgrounds"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_webinar_branding_vb(self, webinarId: Optional[Any] = None, ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteWebinarBrandingVB
        method: DELETE
        path: /webinars/{webinarId}/branding/virtual_backgrounds
        summary: Delete a webinar's branding virtual backgrounds
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/virtual_backgrounds"
        params = { 'webinarId': webinarId, 'ids': ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def set_webinar_branding_vb(self, webinarId: Optional[Any] = None, id: Optional[Any] = None, set_default_for_all_panelists: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: setWebinarBrandingVB
        method: PATCH
        path: /webinars/{webinarId}/branding/virtual_backgrounds
        summary: Set webinar's default branding virtual background
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/virtual_backgrounds"
        params = { 'webinarId': webinarId, 'id': id, 'set_default_for_all_panelists': set_default_for_all_panelists }
        body = None
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_webinar_branding_wallpaper(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: uploadWebinarBrandingWallpaper
        method: POST
        path: /webinars/{webinarId}/branding/wallpaper
        summary: Upload a webinar's branding wallpaper
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/wallpaper"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_webinar_branding_wallpaper(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteWebinarBrandingWallpaper
        method: DELETE
        path: /webinars/{webinarId}/branding/wallpaper
        summary: Delete a webinar's branding wallpaper
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/branding/wallpaper"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_invite_links_create(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarInviteLinksCreate
        method: POST
        path: /webinars/{webinarId}/invite_links
        summary: Create webinar's invite links
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/invite_links"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_live_streaming_join_token(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarLiveStreamingJoinToken
        method: GET
        path: /webinars/{webinarId}/jointoken/live_streaming
        summary: Get a webinar's join token for live streaming
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/jointoken/live_streaming"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_local_archiving_archive_token(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarLocalArchivingArchiveToken
        method: GET
        path: /webinars/{webinarId}/jointoken/local_archiving
        summary: Get a webinar's archive token for local archiving
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/jointoken/local_archiving"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_local_recording_join_token(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarLocalRecordingJoinToken
        method: GET
        path: /webinars/{webinarId}/jointoken/local_recording
        summary: Get a webinar's join token for local recording
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/jointoken/local_recording"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_webinar_live_stream_details(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getWebinarLiveStreamDetails
        method: GET
        path: /webinars/{webinarId}/livestream
        summary: Get live stream details
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/livestream"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_live_stream_update(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarLiveStreamUpdate
        method: PATCH
        path: /webinars/{webinarId}/livestream
        summary: Update a live stream
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/livestream"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_live_stream_status_update(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarLiveStreamStatusUpdate
        method: PATCH
        path: /webinars/{webinarId}/livestream/status
        summary: Update live stream status
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/livestream/status"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_panelists(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPanelists
        method: GET
        path: /webinars/{webinarId}/panelists
        summary: List panelists
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/panelists"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_panelist_create(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPanelistCreate
        method: POST
        path: /webinars/{webinarId}/panelists
        summary: Add panelists
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/panelists"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_panelists_delete(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPanelistsDelete
        method: DELETE
        path: /webinars/{webinarId}/panelists
        summary: Remove all panelists
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/panelists"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_panelist_delete(self, webinarId: Optional[Any] = None, panelistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPanelistDelete
        method: DELETE
        path: /webinars/{webinarId}/panelists/{panelistId}
        summary: Remove a panelist
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/panelists/{panelistId}"
        params = { 'webinarId': webinarId, 'panelistId': panelistId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_polls(self, webinarId: Optional[Any] = None, anonymous: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPolls
        method: GET
        path: /webinars/{webinarId}/polls
        summary: List a webinar's polls 
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/polls"
        params = { 'webinarId': webinarId, 'anonymous': anonymous }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_poll_create(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPollCreate
        method: POST
        path: /webinars/{webinarId}/polls
        summary: Create a webinar's poll
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/polls"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_poll_get(self, webinarId: Optional[Any] = None, pollId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPollGet
        method: GET
        path: /webinars/{webinarId}/polls/{pollId}
        summary: Get a webinar poll
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/polls/{pollId}"
        params = { 'webinarId': webinarId, 'pollId': pollId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_poll_update(self, webinarId: Optional[Any] = None, pollId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPollUpdate
        method: PUT
        path: /webinars/{webinarId}/polls/{pollId}
        summary: Update a webinar poll
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/polls/{pollId}"
        params = { 'webinarId': webinarId, 'pollId': pollId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_poll_delete(self, webinarId: Optional[Any] = None, pollId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarPollDelete
        method: DELETE
        path: /webinars/{webinarId}/polls/{pollId}
        summary: Delete a webinar poll
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/polls/{pollId}"
        params = { 'webinarId': webinarId, 'pollId': pollId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_registrants(self, webinarId: Optional[Any] = None, occurrence_id: Optional[Any] = None, status: Optional[Any] = None, tracking_source_id: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarRegistrants
        method: GET
        path: /webinars/{webinarId}/registrants
        summary: List webinar registrants
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants"
        params = { 'webinarId': webinarId, 'occurrence_id': occurrence_id, 'status': status, 'tracking_source_id': tracking_source_id, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_registrant_create(self, webinarId: Optional[Any] = None, occurrence_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarRegistrantCreate
        method: POST
        path: /webinars/{webinarId}/registrants
        summary: Add a webinar registrant
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants"
        params = { 'webinarId': webinarId, 'occurrence_ids': occurrence_ids }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_registrants_questions_get(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarRegistrantsQuestionsGet
        method: GET
        path: /webinars/{webinarId}/registrants/questions
        summary: List registration questions
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants/questions"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_registrant_question_update(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarRegistrantQuestionUpdate
        method: PATCH
        path: /webinars/{webinarId}/registrants/questions
        summary: Update registration questions
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants/questions"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_registrant_status(self, webinarId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarRegistrantStatus
        method: PUT
        path: /webinars/{webinarId}/registrants/status
        summary: Update registrant's status
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants/status"
        params = { 'webinarId': webinarId, 'occurrence_id': occurrence_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_registrant_get(self, webinarId: Optional[Any] = None, registrantId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarRegistrantGet
        method: GET
        path: /webinars/{webinarId}/registrants/{registrantId}
        summary: Get a webinar registrant
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants/{registrantId}"
        params = { 'webinarId': webinarId, 'registrantId': registrantId, 'occurrence_id': occurrence_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_webinar_registrant(self, webinarId: Optional[Any] = None, registrantId: Optional[Any] = None, occurrence_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteWebinarRegistrant
        method: DELETE
        path: /webinars/{webinarId}/registrants/{registrantId}
        summary: Delete a webinar registrant
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/registrants/{registrantId}"
        params = { 'webinarId': webinarId, 'registrantId': registrantId, 'occurrence_id': occurrence_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_webinar_sip_dialing_with_passcode(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getWebinarSipDialingWithPasscode
        method: POST
        path: /webinars/{webinarId}/sip_dialing
        summary: Get a webinar SIP URI with passcode
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/sip_dialing"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_status(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarStatus
        method: PUT
        path: /webinars/{webinarId}/status
        summary: Update webinar status
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/status"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_survey_get(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarSurveyGet
        method: GET
        path: /webinars/{webinarId}/survey
        summary: Get a webinar survey
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/survey"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_survey_delete(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarSurveyDelete
        method: DELETE
        path: /webinars/{webinarId}/survey
        summary: Delete a webinar survey
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/survey"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_survey_update(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarSurveyUpdate
        method: PATCH
        path: /webinars/{webinarId}/survey
        summary: Update a webinar survey
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/survey"
        params = { 'webinarId': webinarId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def webinar_token(self, webinarId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: webinarToken
        method: GET
        path: /webinars/{webinarId}/token
        summary: Get webinar's token
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/token"
        params = { 'webinarId': webinarId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_tracking_sources(self, webinarId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getTrackingSources
        method: GET
        path: /webinars/{webinarId}/tracking_sources
        summary: Get webinar tracking sources
        """
        endpoint = f"{self._base_url}/webinars/{webinarId}/tracking_sources"
        params = { 'webinarId': webinarId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zoom_phone_account_settings(self, setting_types: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listZoomPhoneAccountSettings
        method: GET
        path: /phone/account_settings
        summary: List an account's Zoom phone settings
        """
        endpoint = f"{self._base_url}/phone/account_settings"
        params = { 'setting_types': setting_types }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_customize_outbound_caller_numbers(self, selected: Optional[Any] = None, site_id: Optional[Any] = None, extension_type: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCustomizeOutboundCallerNumbers
        method: GET
        path: /phone/outbound_caller_id/customized_numbers
        summary: List an account's customized outbound caller ID phone numbers
        """
        endpoint = f"{self._base_url}/phone/outbound_caller_id/customized_numbers"
        params = { 'selected': selected, 'site_id': site_id, 'extension_type': extension_type, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_outbound_caller_numbers(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addOutboundCallerNumbers
        method: POST
        path: /phone/outbound_caller_id/customized_numbers
        summary: Add phone numbers for an account's customized outbound caller ID
        """
        endpoint = f"{self._base_url}/phone/outbound_caller_id/customized_numbers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_outbound_caller_numbers(self, customize_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteOutboundCallerNumbers
        method: DELETE
        path: /phone/outbound_caller_id/customized_numbers
        summary: Delete phone numbers for an account's customized outbound caller ID
        """
        endpoint = f"{self._base_url}/phone/outbound_caller_id/customized_numbers"
        params = { 'customize_ids': customize_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_alert_settings_with_paging_query(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, module: Optional[Any] = None, rule: Optional[Any] = None, status: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListAlertSettingsWithPagingQuery
        method: GET
        path: /phone/alert_settings
        summary: List alert settings with paging query
        """
        endpoint = f"{self._base_url}/phone/alert_settings"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'module': module, 'rule': rule, 'status': status }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_an_alert_setting(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddAnAlertSetting
        method: POST
        path: /phone/alert_settings
        summary: Add an alert setting
        """
        endpoint = f"{self._base_url}/phone/alert_settings"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_alert_setting_details(self, alertSettingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAlertSettingDetails
        method: GET
        path: /phone/alert_settings/{alertSettingId}
        summary: Get alert setting details
        """
        endpoint = f"{self._base_url}/phone/alert_settings/{alertSettingId}"
        params = { 'alertSettingId': alertSettingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_an_alert_setting(self, alertSettingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAnAlertSetting
        method: DELETE
        path: /phone/alert_settings/{alertSettingId}
        summary: Delete an alert setting
        """
        endpoint = f"{self._base_url}/phone/alert_settings/{alertSettingId}"
        params = { 'alertSettingId': alertSettingId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_an_alert_setting(self, alertSettingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAnAlertSetting
        method: PATCH
        path: /phone/alert_settings/{alertSettingId}
        summary: Update an alert setting
        """
        endpoint = f"{self._base_url}/phone/alert_settings/{alertSettingId}"
        params = { 'alertSettingId': alertSettingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_audio_item(self, audioId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAudioItem
        method: GET
        path: /phone/audios/{audioId}
        summary: Get an audio item
        """
        endpoint = f"{self._base_url}/phone/audios/{audioId}"
        params = { 'audioId': audioId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_audio_item(self, audioId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAudioItem
        method: DELETE
        path: /phone/audios/{audioId}
        summary: Delete an audio item
        """
        endpoint = f"{self._base_url}/phone/audios/{audioId}"
        params = { 'audioId': audioId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_audio_item(self, audioId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAudioItem
        method: PATCH
        path: /phone/audios/{audioId}
        summary: Update an audio item
        """
        endpoint = f"{self._base_url}/phone/audios/{audioId}"
        params = { 'audioId': audioId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_audio_items(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListAudioItems
        method: GET
        path: /phone/users/{userId}/audios
        summary: List audio items
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/audios"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_an_audio(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddAnAudio
        method: POST
        path: /phone/users/{userId}/audios
        summary: Add an audio item for text-to-speech conversion
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/audios"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_audio_item(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddAudioItem
        method: POST
        path: /phone/users/{userId}/audios/batch
        summary: Add audio items
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/audios/batch"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_auto_receptionists(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listAutoReceptionists
        method: GET
        path: /phone/auto_receptionists
        summary: List auto receptionists
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_auto_receptionist(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addAutoReceptionist
        method: POST
        path: /phone/auto_receptionists
        summary: Add an auto receptionist
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_auto_receptionist_detail(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAutoReceptionistDetail
        method: GET
        path: /phone/auto_receptionists/{autoReceptionistId}
        summary: Get an auto receptionist
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_auto_receptionist(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAutoReceptionist
        method: DELETE
        path: /phone/auto_receptionists/{autoReceptionistId}
        summary: Delete a non-primary auto receptionist
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_auto_receptionist(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateAutoReceptionist
        method: PATCH
        path: /phone/auto_receptionists/{autoReceptionistId}
        summary: Update an auto receptionist
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_phone_numbers_auto_receptionist(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignPhoneNumbersAutoReceptionist
        method: POST
        path: /phone/auto_receptionists/{autoReceptionistId}/phone_numbers
        summary: Assign phone numbers
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/phone_numbers"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_all_phone_nums_auto_receptionist(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignAllPhoneNumsAutoReceptionist
        method: DELETE
        path: /phone/auto_receptionists/{autoReceptionistId}/phone_numbers
        summary: Unassign all phone numbers
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/phone_numbers"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_a_phone_num_auto_receptionist(self, autoReceptionistId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignAPhoneNumAutoReceptionist
        method: DELETE
        path: /phone/auto_receptionists/{autoReceptionistId}/phone_numbers/{phoneNumberId}
        summary: Unassign a phone number
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/phone_numbers/{phoneNumberId}"
        params = { 'autoReceptionistId': autoReceptionistId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_auto_receptionists_policy(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAutoReceptionistsPolicy
        method: GET
        path: /phone/auto_receptionists/{autoReceptionistId}/policies
        summary: Get an auto receptionist policy
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/policies"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_auto_receptionist_policy(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateAutoReceptionistPolicy
        method: PATCH
        path: /phone/auto_receptionists/{autoReceptionistId}/policies
        summary: Update an auto receptionist policy
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/policies"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_policy(self, autoReceptionistId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddPolicy
        method: POST
        path: /phone/auto_receptionists/{autoReceptionistId}/policies/{policyType}
        summary: Add a policy subsetting
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/policies/{policyType}"
        params = { 'autoReceptionistId': autoReceptionistId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_policy(self, autoReceptionistId: Optional[Any] = None, policyType: Optional[Any] = None, shared_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeletePolicy
        method: DELETE
        path: /phone/auto_receptionists/{autoReceptionistId}/policies/{policyType}
        summary: Delete a policy subsetting
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/policies/{policyType}"
        params = { 'autoReceptionistId': autoReceptionistId, 'policyType': policyType, 'shared_ids': shared_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_policy(self, autoReceptionistId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updatePolicy
        method: PATCH
        path: /phone/auto_receptionists/{autoReceptionistId}/policies/{policyType}
        summary: Update a policy subsetting
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/policies/{policyType}"
        params = { 'autoReceptionistId': autoReceptionistId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_billing_account(self, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listBillingAccount
        method: GET
        path: /phone/billing_accounts
        summary: List billing accounts
        """
        endpoint = f"{self._base_url}/phone/billing_accounts"
        params = { 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_billing_account(self, billingAccountId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetABillingAccount
        method: GET
        path: /phone/billing_accounts/{billingAccountId}
        summary: Get billing account details
        """
        endpoint = f"{self._base_url}/phone/billing_accounts/{billingAccountId}"
        params = { 'billingAccountId': billingAccountId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_blocked_list(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listBlockedList
        method: GET
        path: /phone/blocked_list
        summary: List blocked lists
        """
        endpoint = f"{self._base_url}/phone/blocked_list"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_anumber_to_blocked_list(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addAnumberToBlockedList
        method: POST
        path: /phone/blocked_list
        summary: Create a blocked list
        """
        endpoint = f"{self._base_url}/phone/blocked_list"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_blocked_list(self, blockedListId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getABlockedList
        method: GET
        path: /phone/blocked_list/{blockedListId}
        summary: Get blocked list details
        """
        endpoint = f"{self._base_url}/phone/blocked_list/{blockedListId}"
        params = { 'blockedListId': blockedListId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_blocked_list(self, blockedListId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteABlockedList
        method: DELETE
        path: /phone/blocked_list/{blockedListId}
        summary: Delete a blocked list
        """
        endpoint = f"{self._base_url}/phone/blocked_list/{blockedListId}"
        params = { 'blockedListId': blockedListId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_blocked_list(self, blockedListId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateBlockedList
        method: PATCH
        path: /phone/blocked_list/{blockedListId}
        summary: Update a blocked list
        """
        endpoint = f"{self._base_url}/phone/blocked_list/{blockedListId}"
        params = { 'blockedListId': blockedListId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_handling(self, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallHandling
        method: GET
        path: /phone/extension/{extensionId}/call_handling/settings
        summary: Get call handling settings
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/call_handling/settings"
        params = { 'extensionId': extensionId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_call_handling(self, extensionId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addCallHandling
        method: POST
        path: /phone/extension/{extensionId}/call_handling/settings/{settingType}
        summary: Add a call handling setting
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/call_handling/settings/{settingType}"
        params = { 'extensionId': extensionId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_call_handling(self, extensionId: Optional[Any] = None, settingType: Optional[Any] = None, call_forwarding_id: Optional[Any] = None, holiday_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCallHandling
        method: DELETE
        path: /phone/extension/{extensionId}/call_handling/settings/{settingType}
        summary: Delete a call handling setting
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/call_handling/settings/{settingType}"
        params = { 'extensionId': extensionId, 'settingType': settingType, 'call_forwarding_id': call_forwarding_id, 'holiday_id': holiday_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_call_handling(self, extensionId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateCallHandling
        method: PATCH
        path: /phone/extension/{extensionId}/call_handling/settings/{settingType}
        summary: Update a call handling setting
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/call_handling/settings/{settingType}"
        params = { 'extensionId': extensionId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_element(self, callElementId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallElement
        method: GET
        path: /phone/call_element/{callElementId}
        summary: Get call element
        """
        endpoint = f"{self._base_url}/phone/call_element/{callElementId}"
        params = { 'callElementId': callElementId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_call_history(self, page_size: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, next_page_token: Optional[Any] = None, keyword: Optional[Any] = None, directions: Optional[Any] = None, connect_types: Optional[Any] = None, number_types: Optional[Any] = None, call_types: Optional[Any] = None, extension_types: Optional[Any] = None, call_results: Optional[Any] = None, group_ids: Optional[Any] = None, site_ids: Optional[Any] = None, department: Optional[Any] = None, cost_center: Optional[Any] = None, time_type: Optional[Any] = None, recording_status: Optional[Any] = None, with_voicemail: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountCallHistory
        method: GET
        path: /phone/call_history
        summary: Get account's call history
        """
        endpoint = f"{self._base_url}/phone/call_history"
        params = { 'page_size': page_size, 'from': from_, 'to': to, 'next_page_token': next_page_token, 'keyword': keyword, 'directions': directions, 'connect_types': connect_types, 'number_types': number_types, 'call_types': call_types, 'extension_types': extension_types, 'call_results': call_results, 'group_ids': group_ids, 'site_ids': site_ids, 'department': department, 'cost_center': cost_center, 'time_type': time_type, 'recording_status': recording_status, 'with_voicemail': with_voicemail }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_path(self, callHistoryUuid: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallPath
        method: GET
        path: /phone/call_history/{callHistoryUuid}
        summary: Get call history
        """
        endpoint = f"{self._base_url}/phone/call_history/{callHistoryUuid}"
        params = { 'callHistoryUuid': callHistoryUuid }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_client_code_to_call_history(self, callLogId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addClientCodeToCallHistory
        method: PATCH
        path: /phone/call_history/{callLogId}/client_code
        summary: Add a client code to a call history
        """
        endpoint = f"{self._base_url}/phone/call_history/{callLogId}/client_code"
        params = { 'callLogId': callLogId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_history_detail(self, callHistoryId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallHistoryDetail
        method: GET
        path: /phone/call_history_detail/{callHistoryId}
        summary: Get call history detail
        """
        endpoint = f"{self._base_url}/phone/call_history_detail/{callHistoryId}"
        params = { 'callHistoryId': callHistoryId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_call_logs(self, page_size: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, type_: Optional[Any] = None, next_page_token: Optional[Any] = None, path: Optional[Any] = None, time_type: Optional[Any] = None, site_id: Optional[Any] = None, charged_call_logs: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountCallLogs
        method: GET
        path: /phone/call_logs
        summary: Get account's call logs
        """
        endpoint = f"{self._base_url}/phone/call_logs"
        params = { 'page_size': page_size, 'from': from_, 'to': to, 'type': type_, 'next_page_token': next_page_token, 'path': path, 'time_type': time_type, 'site_id': site_id, 'charged_call_logs': charged_call_logs }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_log_details(self, callLogId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallLogDetails
        method: GET
        path: /phone/call_logs/{callLogId}
        summary: Get call log details
        """
        endpoint = f"{self._base_url}/phone/call_logs/{callLogId}"
        params = { 'callLogId': callLogId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_client_code_to_call_log(self, callLogId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addClientCodeToCallLog
        method: PUT
        path: /phone/call_logs/{callLogId}/client_code
        summary: Add a client code to a call log
        """
        endpoint = f"{self._base_url}/phone/call_logs/{callLogId}/client_code"
        params = { 'callLogId': callLogId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_ai_call_summary(self, userId: Optional[Any] = None, aiCallSummaryId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserAICallSummary
        method: GET
        path: /phone/user/{userId}/ai_call_summary/{aiCallSummaryId}
        summary: Get User AI Call Summary Detail
        """
        endpoint = f"{self._base_url}/phone/user/{userId}/ai_call_summary/{aiCallSummaryId}"
        params = { 'userId': userId, 'aiCallSummaryId': aiCallSummaryId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_user_call_history(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, next_page_token: Optional[Any] = None, keyword: Optional[Any] = None, directions: Optional[Any] = None, connect_types: Optional[Any] = None, number_types: Optional[Any] = None, call_types: Optional[Any] = None, extension_types: Optional[Any] = None, call_results: Optional[Any] = None, group_ids: Optional[Any] = None, site_ids: Optional[Any] = None, department: Optional[Any] = None, cost_center: Optional[Any] = None, time_type: Optional[Any] = None, recording_status: Optional[Any] = None, with_voicemail: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneUserCallHistory
        method: GET
        path: /phone/users/{userId}/call_history
        summary: Get user's call history
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_history"
        params = { 'userId': userId, 'page_size': page_size, 'from': from_, 'to': to, 'next_page_token': next_page_token, 'keyword': keyword, 'directions': directions, 'connect_types': connect_types, 'number_types': number_types, 'call_types': call_types, 'extension_types': extension_types, 'call_results': call_results, 'group_ids': group_ids, 'site_ids': site_ids, 'department': department, 'cost_center': cost_center, 'time_type': time_type, 'recording_status': recording_status, 'with_voicemail': with_voicemail }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def sync_user_call_history(self, userId: Optional[Any] = None, sync_type: Optional[Any] = None, count: Optional[Any] = None, sync_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: syncUserCallHistory
        method: GET
        path: /phone/users/{userId}/call_history/sync
        summary: Sync user's call history
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_history/sync"
        params = { 'userId': userId, 'sync_type': sync_type, 'count': count, 'sync_token': sync_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_user_call_history(self, userId: Optional[Any] = None, callLogId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteUserCallHistory
        method: DELETE
        path: /phone/users/{userId}/call_history/{callLogId}
        summary: Delete a user's call history
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_history/{callLogId}"
        params = { 'userId': userId, 'callLogId': callLogId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_user_call_logs(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, type_: Optional[Any] = None, next_page_token: Optional[Any] = None, phone_number: Optional[Any] = None, time_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneUserCallLogs
        method: GET
        path: /phone/users/{userId}/call_logs
        summary: Get user's call logs
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_logs"
        params = { 'userId': userId, 'page_size': page_size, 'from': from_, 'to': to, 'type': type_, 'next_page_token': next_page_token, 'phone_number': phone_number, 'time_type': time_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def sync_user_call_logs(self, userId: Optional[Any] = None, sync_type: Optional[Any] = None, count: Optional[Any] = None, sync_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: syncUserCallLogs
        method: GET
        path: /phone/users/{userId}/call_logs/sync
        summary: Sync user's call logs
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_logs/sync"
        params = { 'userId': userId, 'sync_type': sync_type, 'count': count, 'sync_token': sync_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_call_log(self, userId: Optional[Any] = None, callLogId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCallLog
        method: DELETE
        path: /phone/users/{userId}/call_logs/{callLogId}
        summary: Delete a user's call log
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_logs/{callLogId}"
        params = { 'userId': userId, 'callLogId': callLogId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def callqueueanalytics(self, page_size: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, next_page_token: Optional[Any] = None, site_id: Optional[Any] = None, call_queue_ext_ids: Optional[Any] = None, department: Optional[Any] = None, cost_center: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: callqueueanalytics
        method: GET
        path: /phone/call_queue_analytics
        summary: List call queue analytics
        """
        endpoint = f"{self._base_url}/phone/call_queue_analytics"
        params = { 'page_size': page_size, 'from': from_, 'to': to, 'next_page_token': next_page_token, 'site_id': site_id, 'call_queue_ext_ids': call_queue_ext_ids, 'department': department, 'cost_center': cost_center }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_call_queues(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, site_id: Optional[Any] = None, cost_center: Optional[Any] = None, department: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCallQueues
        method: GET
        path: /phone/call_queues
        summary: List call queues
        """
        endpoint = f"{self._base_url}/phone/call_queues"
        params = { 'next_page_token': next_page_token, 'page_size': page_size, 'site_id': site_id, 'cost_center': cost_center, 'department': department }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_call_queue(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createCallQueue
        method: POST
        path: /phone/call_queues
        summary: Create a call queue
        """
        endpoint = f"{self._base_url}/phone/call_queues"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_call_queue(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getACallQueue
        method: GET
        path: /phone/call_queues/{callQueueId}
        summary: Get call queue details
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}"
        params = { 'callQueueId': callQueueId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_call_queue(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteACallQueue
        method: DELETE
        path: /phone/call_queues/{callQueueId}
        summary: Delete a call queue
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}"
        params = { 'callQueueId': callQueueId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_call_queue(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateCallQueue
        method: PATCH
        path: /phone/call_queues/{callQueueId}
        summary: Update call queue details
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}"
        params = { 'callQueueId': callQueueId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_call_queue_members(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCallQueueMembers
        method: GET
        path: /phone/call_queues/{callQueueId}/members
        summary: List call queue members
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/members"
        params = { 'callQueueId': callQueueId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_members_to_call_queue(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addMembersToCallQueue
        method: POST
        path: /phone/call_queues/{callQueueId}/members
        summary: Add members to a call queue
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/members"
        params = { 'callQueueId': callQueueId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_all_members(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignAllMembers
        method: DELETE
        path: /phone/call_queues/{callQueueId}/members
        summary: Unassign all members
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/members"
        params = { 'callQueueId': callQueueId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_member_from_call_queue(self, callQueueId: Optional[Any] = None, memberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignMemberFromCallQueue
        method: DELETE
        path: /phone/call_queues/{callQueueId}/members/{memberId}
        summary: Unassign a member
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/members/{memberId}"
        params = { 'callQueueId': callQueueId, 'memberId': memberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_phone_to_call_queue(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignPhoneToCallQueue
        method: POST
        path: /phone/call_queues/{callQueueId}/phone_numbers
        summary: Assign numbers to a call queue
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/phone_numbers"
        params = { 'callQueueId': callQueueId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_a_phone_num_call_queue(self, callQueueId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignAPhoneNumCallQueue
        method: DELETE
        path: /phone/call_queues/{callQueueId}/phone_numbers
        summary: Unassign all phone numbers
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/phone_numbers"
        params = { 'callQueueId': callQueueId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def un_assign_phone_num_call_queue(self, callQueueId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unAssignPhoneNumCallQueue
        method: DELETE
        path: /phone/call_queues/{callQueueId}/phone_numbers/{phoneNumberId}
        summary: Unassign a phone number
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/phone_numbers/{phoneNumberId}"
        params = { 'callQueueId': callQueueId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_cq_policy_sub_setting(self, callQueueId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addCQPolicySubSetting
        method: POST
        path: /phone/call_queues/{callQueueId}/policies/{policyType}
        summary: Add a policy subsetting to a call queue
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/policies/{policyType}"
        params = { 'callQueueId': callQueueId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_cq_policy_sub_setting(self, callQueueId: Optional[Any] = None, policyType: Optional[Any] = None, shared_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeCQPolicySubSetting
        method: DELETE
        path: /phone/call_queues/{callQueueId}/policies/{policyType}
        summary: Delete a CQ policy setting
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/policies/{policyType}"
        params = { 'callQueueId': callQueueId, 'policyType': policyType, 'shared_ids': shared_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_cq_policy_sub_setting(self, callQueueId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateCQPolicySubSetting
        method: PATCH
        path: /phone/call_queues/{callQueueId}/policies/{policyType}
        summary: Update a call queue's policy subsetting
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/policies/{policyType}"
        params = { 'callQueueId': callQueueId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_queue_recordings(self, callQueueId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallQueueRecordings
        method: GET
        path: /phone/call_queues/{callQueueId}/recordings
        summary: Get call queue recordings
        """
        endpoint = f"{self._base_url}/phone/call_queues/{callQueueId}/recordings"
        params = { 'callQueueId': callQueueId, 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_cr_phone_numbers(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, assigned_status: Optional[Any] = None, sub_account_id: Optional[Any] = None, keyword: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCRPhoneNumbers
        method: GET
        path: /phone/carrier_reseller/numbers
        summary: List phone numbers
        """
        endpoint = f"{self._base_url}/phone/carrier_reseller/numbers"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'assigned_status': assigned_status, 'sub_account_id': sub_account_id, 'keyword': keyword }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_cr_phone_numbers(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createCRPhoneNumbers
        method: POST
        path: /phone/carrier_reseller/numbers
        summary: Create phone numbers
        """
        endpoint = f"{self._base_url}/phone/carrier_reseller/numbers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def active_cr_phone_numbers(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: activeCRPhoneNumbers
        method: PATCH
        path: /phone/carrier_reseller/numbers
        summary: Activate phone numbers
        """
        endpoint = f"{self._base_url}/phone/carrier_reseller/numbers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_cr_phone_number(self, number: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCRPhoneNumber
        method: DELETE
        path: /phone/carrier_reseller/numbers/{number}
        summary: Delete a phone number
        """
        endpoint = f"{self._base_url}/phone/carrier_reseller/numbers/{number}"
        params = { 'number': number }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_common_areas(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCommonAreas
        method: GET
        path: /phone/common_areas
        summary: List common areas
        """
        endpoint = f"{self._base_url}/phone/common_areas"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_common_area(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addCommonArea
        method: POST
        path: /phone/common_areas
        summary: Add a common area
        """
        endpoint = f"{self._base_url}/phone/common_areas"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def generateactivationcodesforcommonareas(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Generateactivationcodesforcommonareas
        method: POST
        path: /phone/common_areas/activation_code
        summary: Generate activation codes for common areas
        """
        endpoint = f"{self._base_url}/phone/common_areas/activation_code"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_activation_codes(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listActivationCodes
        method: GET
        path: /phone/common_areas/activation_codes
        summary: List activation codes
        """
        endpoint = f"{self._base_url}/phone/common_areas/activation_codes"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def apply_templateto_common_areas(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ApplyTemplatetoCommonAreas
        method: POST
        path: /phone/common_areas/template_id/{templateId}
        summary: Apply template to common areas
        """
        endpoint = f"{self._base_url}/phone/common_areas/template_id/{templateId}"
        params = { 'templateId': templateId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_common_area(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getACommonArea
        method: GET
        path: /phone/common_areas/{commonAreaId}
        summary: Get common area details
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}"
        params = { 'commonAreaId': commonAreaId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_common_area(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCommonArea
        method: DELETE
        path: /phone/common_areas/{commonAreaId}
        summary: Delete a common area
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}"
        params = { 'commonAreaId': commonAreaId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_common_area(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateCommonArea
        method: PATCH
        path: /phone/common_areas/{commonAreaId}
        summary: Update common area
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}"
        params = { 'commonAreaId': commonAreaId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_calling_plans_to_common_area(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignCallingPlansToCommonArea
        method: POST
        path: /phone/common_areas/{commonAreaId}/calling_plans
        summary: Assign calling plans to a common area
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/calling_plans"
        params = { 'commonAreaId': commonAreaId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_calling_plans_from_common_area(self, commonAreaId: Optional[Any] = None, type_: Optional[Any] = None, billing_account_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignCallingPlansFromCommonArea
        method: DELETE
        path: /phone/common_areas/{commonAreaId}/calling_plans/{type}
        summary: Unassign a calling plan from the common area
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/calling_plans/{type_}"
        params = { 'commonAreaId': commonAreaId, 'type': type_, 'billing_account_id': billing_account_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_phone_numbers_to_common_area(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignPhoneNumbersToCommonArea
        method: POST
        path: /phone/common_areas/{commonAreaId}/phone_numbers
        summary: Assign phone numbers to a common area
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/phone_numbers"
        params = { 'commonAreaId': commonAreaId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_phone_numbers_from_common_area(self, commonAreaId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignPhoneNumbersFromCommonArea
        method: DELETE
        path: /phone/common_areas/{commonAreaId}/phone_numbers/{phoneNumberId}
        summary: Unassign phone numbers from common area
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/phone_numbers/{phoneNumberId}"
        params = { 'commonAreaId': commonAreaId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_common_area_pin_code(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateCommonAreaPinCode
        method: PATCH
        path: /phone/common_areas/{commonAreaId}/pin_code
        summary: Update common area pin code
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/pin_code"
        params = { 'commonAreaId': commonAreaId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_common_area_settings(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCommonAreaSettings
        method: GET
        path: /phone/common_areas/{commonAreaId}/settings
        summary: Get common area settings
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/settings"
        params = { 'commonAreaId': commonAreaId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_common_area_setting(self, commonAreaId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddCommonAreaSetting
        method: POST
        path: /phone/common_areas/{commonAreaId}/settings/{settingType}
        summary: Add common area setting
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/settings/{settingType}"
        params = { 'commonAreaId': commonAreaId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_common_area_setting(self, commonAreaId: Optional[Any] = None, settingType: Optional[Any] = None, device_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCommonAreaSetting
        method: DELETE
        path: /phone/common_areas/{commonAreaId}/settings/{settingType}
        summary: Delete common area setting
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/settings/{settingType}"
        params = { 'commonAreaId': commonAreaId, 'settingType': settingType, 'device_id': device_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_common_area_setting(self, commonAreaId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateCommonAreaSetting
        method: PATCH
        path: /phone/common_areas/{commonAreaId}/settings/{settingType}
        summary: Update common area setting
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/settings/{settingType}"
        params = { 'commonAreaId': commonAreaId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_call_logs_metrics(self, from_: Optional[Any] = None, to: Optional[Any] = None, site_id: Optional[Any] = None, quality_type: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCallLogsMetrics
        method: GET
        path: /phone/metrics/call_logs
        summary: List call logs
        """
        endpoint = f"{self._base_url}/phone/metrics/call_logs"
        params = { 'from': from_, 'to': to, 'site_id': site_id, 'quality_type': quality_type, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_qo_s(self, callId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallQoS
        method: GET
        path: /phone/metrics/call_logs/{callId}/qos
        summary: Get call QoS
        """
        endpoint = f"{self._base_url}/phone/metrics/call_logs/{callId}/qos"
        params = { 'callId': callId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_log_metrics_details(self, call_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCallLogMetricsDetails
        method: GET
        path: /phone/metrics/call_logs/{call_id}
        summary: Get call details from call log
        """
        endpoint = f"{self._base_url}/phone/metrics/call_logs/{call_id}"
        params = { 'call_id': call_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_default_emergency_address(self, status: Optional[Any] = None, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserDefaultEmergencyAddress
        method: GET
        path: /phone/metrics/emergency_services/default_emergency_address/users
        summary: List default emergency address users
        """
        endpoint = f"{self._base_url}/phone/metrics/emergency_services/default_emergency_address/users"
        params = { 'status': status, 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_detectable_personal_location(self, status: Optional[Any] = None, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserDetectablePersonalLocation
        method: GET
        path: /phone/metrics/emergency_services/detectable_personal_location/users
        summary: List detectable personal location users
        """
        endpoint = f"{self._base_url}/phone/metrics/emergency_services/detectable_personal_location/users"
        params = { 'status': status, 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_location_sharing_permission(self, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserLocationSharingPermission
        method: GET
        path: /phone/metrics/emergency_services/location_sharing_permission/users
        summary: List users permission for location sharing
        """
        endpoint = f"{self._base_url}/phone/metrics/emergency_services/location_sharing_permission/users"
        params = { 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_nomadic_emergency_services(self, status: Optional[Any] = None, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserNomadicEmergencyServices
        method: GET
        path: /phone/metrics/emergency_services/nomadic_emergency_services/users
        summary: List nomadic emergency services users
        """
        endpoint = f"{self._base_url}/phone/metrics/emergency_services/nomadic_emergency_services/users"
        params = { 'status': status, 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_realtimelocation(self, location_type: Optional[Any] = None, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPhoneRealtimelocation
        method: GET
        path: /phone/metrics/emergency_services/realtime_location/devices
        summary: List real time location for IP phones
        """
        endpoint = f"{self._base_url}/phone/metrics/emergency_services/realtime_location/devices"
        params = { 'location_type': location_type, 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_realtime_location(self, location_type: Optional[Any] = None, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserRealtimeLocation
        method: GET
        path: /phone/metrics/emergency_services/realtime_location/users
        summary: List real time location for users
        """
        endpoint = f"{self._base_url}/phone/metrics/emergency_services/realtime_location/users"
        params = { 'location_type': location_type, 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_tracked_locations(self, type_: Optional[Any] = None, site_id: Optional[Any] = None, location_type: Optional[Any] = None, keyword: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listTrackedLocations
        method: GET
        path: /phone/metrics/location_tracking
        summary: List tracked locations
        """
        endpoint = f"{self._base_url}/phone/metrics/location_tracking"
        params = { 'type': type_, 'site_id': site_id, 'location_type': location_type, 'keyword': keyword }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_past_call_metrics(self, from_: Optional[Any] = None, to: Optional[Any] = None, phone_number: Optional[Any] = None, extension_number: Optional[Any] = None, quality_type: Optional[Any] = None, department: Optional[Any] = None, cost_center: Optional[Any] = None, directions: Optional[Any] = None, durations: Optional[Any] = None, site_id: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPastCallMetrics
        method: GET
        path: /phone/metrics/past_calls
        summary: List past call metrics
        """
        endpoint = f"{self._base_url}/phone/metrics/past_calls"
        params = { 'from': from_, 'to': to, 'phone_number': phone_number, 'extension_number': extension_number, 'quality_type': quality_type, 'department': department, 'cost_center': cost_center, 'directions': directions, 'durations': durations, 'site_id': site_id, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_device_line_key_setting(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listDeviceLineKeySetting
        method: GET
        path: /phone/devices/{deviceId}/line_keys
        summary: Get device line keys information
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}/line_keys"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_update_device_line_key_setting(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchUpdateDeviceLineKeySetting
        method: PATCH
        path: /phone/devices/{deviceId}/line_keys
        summary: Batch update device line key position
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}/line_keys"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_users_from_directory(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, in_directory: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListUsersFromDirectory
        method: GET
        path: /phone/dial_by_name_directory/extensions
        summary: List users in directory
        """
        endpoint = f"{self._base_url}/phone/dial_by_name_directory/extensions"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'in_directory': in_directory, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_users_to_directory(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddUsersToDirectory
        method: POST
        path: /phone/dial_by_name_directory/extensions
        summary: Add users to a directory
        """
        endpoint = f"{self._base_url}/phone/dial_by_name_directory/extensions"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_users_from_directory(self, site_id: Optional[Any] = None, extension_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteUsersFromDirectory
        method: DELETE
        path: /phone/dial_by_name_directory/extensions
        summary: Delete users from a directory
        """
        endpoint = f"{self._base_url}/phone/dial_by_name_directory/extensions"
        params = { 'site_id': site_id, 'extension_ids': extension_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_users_from_directory_by_site(self, siteId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, in_directory: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListUsersFromDirectoryBySite
        method: GET
        path: /phone/sites/{siteId}/dial_by_name_directory/extensions
        summary: List users in a directory by site
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/dial_by_name_directory/extensions"
        params = { 'siteId': siteId, 'page_size': page_size, 'next_page_token': next_page_token, 'in_directory': in_directory, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_users_to_directory_by_site(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddUsersToDirectoryBySite
        method: POST
        path: /phone/sites/{siteId}/dial_by_name_directory/extensions
        summary: Add users to a directory of a site
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/dial_by_name_directory/extensions"
        params = { 'siteId': siteId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_users_from_directory_by_site(self, siteId: Optional[Any] = None, site_id: Optional[Any] = None, extension_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteUsersFromDirectoryBySite
        method: DELETE
        path: /phone/sites/{siteId}/dial_by_name_directory/extensions
        summary: Delete users from a directory of a site
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/dial_by_name_directory/extensions"
        params = { 'siteId': siteId, 'site_id': site_id, 'extension_ids': extension_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_emergency_addresses(self, site_id: Optional[Any] = None, user_id: Optional[Any] = None, level: Optional[Any] = None, status: Optional[Any] = None, address_keyword: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listEmergencyAddresses
        method: GET
        path: /phone/emergency_addresses
        summary: List emergency addresses
        """
        endpoint = f"{self._base_url}/phone/emergency_addresses"
        params = { 'site_id': site_id, 'user_id': user_id, 'level': level, 'status': status, 'address_keyword': address_keyword, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_emergency_address(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addEmergencyAddress
        method: POST
        path: /phone/emergency_addresses
        summary: Add an emergency address
        """
        endpoint = f"{self._base_url}/phone/emergency_addresses"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_emergency_address(self, emergencyAddressId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getEmergencyAddress
        method: GET
        path: /phone/emergency_addresses/{emergencyAddressId}
        summary: Get emergency address details
        """
        endpoint = f"{self._base_url}/phone/emergency_addresses/{emergencyAddressId}"
        params = { 'emergencyAddressId': emergencyAddressId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_emergency_address(self, emergencyAddressId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteEmergencyAddress
        method: DELETE
        path: /phone/emergency_addresses/{emergencyAddressId}
        summary: Delete an emergency address
        """
        endpoint = f"{self._base_url}/phone/emergency_addresses/{emergencyAddressId}"
        params = { 'emergencyAddressId': emergencyAddressId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_emergency_address(self, emergencyAddressId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateEmergencyAddress
        method: PATCH
        path: /phone/emergency_addresses/{emergencyAddressId}
        summary: Update an emergency address
        """
        endpoint = f"{self._base_url}/phone/emergency_addresses/{emergencyAddressId}"
        params = { 'emergencyAddressId': emergencyAddressId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_add_locations(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchAddLocations
        method: POST
        path: /phone/batch_locations
        summary: Batch add emergency service locations
        """
        endpoint = f"{self._base_url}/phone/batch_locations"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_locations(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listLocations
        method: GET
        path: /phone/locations
        summary: List emergency service locations
        """
        endpoint = f"{self._base_url}/phone/locations"
        params = { 'next_page_token': next_page_token, 'page_size': page_size, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_location(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addLocation
        method: POST
        path: /phone/locations
        summary: Add an emergency service location
        """
        endpoint = f"{self._base_url}/phone/locations"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_location(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getLocation
        method: GET
        path: /phone/locations/{locationId}
        summary: Get emergency service location details
        """
        endpoint = f"{self._base_url}/phone/locations/{locationId}"
        params = { 'locationId': locationId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_location(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteLocation
        method: DELETE
        path: /phone/locations/{locationId}
        summary: Delete an emergency location
        """
        endpoint = f"{self._base_url}/phone/locations/{locationId}"
        params = { 'locationId': locationId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_location(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateLocation
        method: PATCH
        path: /phone/locations/{locationId}
        summary: Update emergency service location
        """
        endpoint = f"{self._base_url}/phone/locations/{locationId}"
        params = { 'locationId': locationId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_external_contacts(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listExternalContacts
        method: GET
        path: /phone/external_contacts
        summary: List external contacts
        """
        endpoint = f"{self._base_url}/phone/external_contacts"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_external_contact(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addExternalContact
        method: POST
        path: /phone/external_contacts
        summary: Add an external contact
        """
        endpoint = f"{self._base_url}/phone/external_contacts"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_external_contact(self, externalContactId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAExternalContact
        method: GET
        path: /phone/external_contacts/{externalContactId}
        summary: Get external contact details
        """
        endpoint = f"{self._base_url}/phone/external_contacts/{externalContactId}"
        params = { 'externalContactId': externalContactId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_external_contact(self, externalContactId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAExternalContact
        method: DELETE
        path: /phone/external_contacts/{externalContactId}
        summary: Delete an external contact
        """
        endpoint = f"{self._base_url}/phone/external_contacts/{externalContactId}"
        params = { 'externalContactId': externalContactId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_external_contact(self, externalContactId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateExternalContact
        method: PATCH
        path: /phone/external_contacts/{externalContactId}
        summary: Update external contact
        """
        endpoint = f"{self._base_url}/phone/external_contacts/{externalContactId}"
        params = { 'externalContactId': externalContactId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getuser_sfaxlogs(self, extensionId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, sender_number: Optional[Any] = None, receiver_number: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getuser'sfaxlogs
        method: GET
        path: /phone/extension/{extensionId}/fax/logs
        summary: Get extension's fax logs
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/fax/logs"
        params = { 'extensionId': extensionId, 'page_size': page_size, 'next_page_token': next_page_token, 'sender_number': sender_number, 'receiver_number': receiver_number }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_account_s_fax_logs(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, site_id: Optional[Any] = None, sender_number: Optional[Any] = None, receiver_number: Optional[Any] = None, extension_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAccount'sFaxLogs
        method: GET
        path: /phone/fax/logs
        summary: Get account's fax logs
        """
        endpoint = f"{self._base_url}/phone/fax/logs"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'site_id': site_id, 'sender_number': sender_number, 'receiver_number': receiver_number, 'extension_type': extension_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_fax_log_details(self, faxLogId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetFaxLogDetails
        method: GET
        path: /phone/fax/logs/{faxLogId}
        summary: Get fax log details
        """
        endpoint = f"{self._base_url}/phone/fax/logs/{faxLogId}"
        params = { 'faxLogId': faxLogId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def downloadfaxfile(self, faxLogId: Optional[Any] = None, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Downloadfaxfile
        method: GET
        path: /phone/fax/logs/{faxLogId}/file/{fileId}
        summary: Download fax file
        """
        endpoint = f"{self._base_url}/phone/fax/logs/{faxLogId}/file/{fileId}"
        params = { 'faxLogId': faxLogId, 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_firmware_rules(self, site_id: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListFirmwareRules
        method: GET
        path: /phone/firmware_update_rules
        summary: List firmware update rules
        """
        endpoint = f"{self._base_url}/phone/firmware_update_rules"
        params = { 'site_id': site_id, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_firmware_rule(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddFirmwareRule
        method: POST
        path: /phone/firmware_update_rules
        summary: Add a firmware update rule
        """
        endpoint = f"{self._base_url}/phone/firmware_update_rules"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_firmware_rule_detail(self, ruleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetFirmwareRuleDetail
        method: GET
        path: /phone/firmware_update_rules/{ruleId}
        summary: Get firmware update rule information
        """
        endpoint = f"{self._base_url}/phone/firmware_update_rules/{ruleId}"
        params = { 'ruleId': ruleId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_firmware_update_rule(self, ruleId: Optional[Any] = None, restart_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteFirmwareUpdateRule
        method: DELETE
        path: /phone/firmware_update_rules/{ruleId}
        summary: Delete firmware update rule
        """
        endpoint = f"{self._base_url}/phone/firmware_update_rules/{ruleId}"
        params = { 'ruleId': ruleId, 'restart_type': restart_type }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_firmware_rule(self, ruleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateFirmwareRule
        method: PATCH
        path: /phone/firmware_update_rules/{ruleId}
        summary: Update firmware update rule
        """
        endpoint = f"{self._base_url}/phone/firmware_update_rules/{ruleId}"
        params = { 'ruleId': ruleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_firmwares(self, is_update: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListFirmwares
        method: GET
        path: /phone/firmwares
        summary: List updatable firmwares
        """
        endpoint = f"{self._base_url}/phone/firmwares"
        params = { 'is_update': is_update, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_gcp(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listGCP
        method: GET
        path: /phone/group_call_pickup
        summary: List group call pickup objects
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_gcp(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addGCP
        method: POST
        path: /phone/group_call_pickup
        summary: Add a group call pickup object
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_gcp(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetGCP
        method: GET
        path: /phone/group_call_pickup/{groupId}
        summary: Get call pickup group by ID
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_gcp(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteGCP
        method: DELETE
        path: /phone/group_call_pickup/{groupId}
        summary: Delete group call pickup objects
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_gcp(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateGCP
        method: PATCH
        path: /phone/group_call_pickup/{groupId}
        summary: Update the group call pickup information
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup/{groupId}"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_gcp_members(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, groupId: Optional[Any] = None, site_id: Optional[Any] = None, extension_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listGCPMembers
        method: GET
        path: /phone/group_call_pickup/{groupId}/members
        summary: List call pickup group members
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup/{groupId}/members"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'groupId': groupId, 'site_id': site_id, 'extension_type': extension_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_gcp_members(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addGCPMembers
        method: POST
        path: /phone/group_call_pickup/{groupId}/members
        summary: Add members to a call pickup group
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup/{groupId}/members"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_gcp_members(self, groupId: Optional[Any] = None, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeGCPMembers
        method: DELETE
        path: /phone/group_call_pickup/{groupId}/members/{extensionId}
        summary: Remove members from call pickup group
        """
        endpoint = f"{self._base_url}/phone/group_call_pickup/{groupId}/members/{extensionId}"
        params = { 'groupId': groupId, 'extensionId': extensionId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_group_policy_details(self, groupId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetGroupPolicyDetails
        method: GET
        path: /phone/groups/{groupId}/policies/{policyType}
        summary: Get group policy details
        """
        endpoint = f"{self._base_url}/phone/groups/{groupId}/policies/{policyType}"
        params = { 'groupId': groupId, 'policyType': policyType }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_group_policy(self, groupId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateGroupPolicy
        method: PATCH
        path: /phone/groups/{groupId}/policies/{policyType}
        summary: Update group policy
        """
        endpoint = f"{self._base_url}/phone/groups/{groupId}/policies/{policyType}"
        params = { 'groupId': groupId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_group_phone_settings(self, groupId: Optional[Any] = None, setting_types: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getGroupPhoneSettings
        method: GET
        path: /phone/groups/{groupId}/settings
        summary: Get group phone settings
        """
        endpoint = f"{self._base_url}/phone/groups/{groupId}/settings"
        params = { 'groupId': groupId, 'setting_types': setting_types }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_auto_receptionist_ivr(self, autoReceptionistId: Optional[Any] = None, hours_type: Optional[Any] = None, holiday_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAutoReceptionistIVR
        method: GET
        path: /phone/auto_receptionists/{autoReceptionistId}/ivr
        summary: Get auto receptionist IVR
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/ivr"
        params = { 'autoReceptionistId': autoReceptionistId, 'hours_type': hours_type, 'holiday_id': holiday_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_auto_receptionist_ivr(self, autoReceptionistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateAutoReceptionistIVR
        method: PATCH
        path: /phone/auto_receptionists/{autoReceptionistId}/ivr
        summary: Update auto receptionist IVR
        """
        endpoint = f"{self._base_url}/phone/auto_receptionists/{autoReceptionistId}/ivr"
        params = { 'autoReceptionistId': autoReceptionistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_extension_level_inbound_block_rules(self, extensionId: Optional[Any] = None, keyword: Optional[Any] = None, match_type: Optional[Any] = None, type_: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListExtensionLevelInboundBlockRules
        method: GET
        path: /phone/extension/{extensionId}/inbound_blocked/rules
        summary: List an extension's inbound block rules
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/inbound_blocked/rules"
        params = { 'extensionId': extensionId, 'keyword': keyword, 'match_type': match_type, 'type': type_, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_extensiont_level_inbound_block_rules(self, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddExtensiontLevelInboundBlockRules
        method: POST
        path: /phone/extension/{extensionId}/inbound_blocked/rules
        summary: Add an extension's inbound block rule
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/inbound_blocked/rules"
        params = { 'extensionId': extensionId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_extensiont_level_inbound_block_rules(self, extensionId: Optional[Any] = None, blocked_rule_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteExtensiontLevelInboundBlockRules
        method: DELETE
        path: /phone/extension/{extensionId}/inbound_blocked/rules
        summary: Delete an extension's inbound block rule
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/inbound_blocked/rules"
        params = { 'extensionId': extensionId, 'blocked_rule_id': blocked_rule_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_account_level_inbound_blocked_statistics(self, keyword: Optional[Any] = None, match_type: Optional[Any] = None, type_: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListAccountLevelInboundBlockedStatistics
        method: GET
        path: /phone/inbound_blocked/extension_rules/statistics
        summary: List an account's inbound blocked statistics
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/extension_rules/statistics"
        params = { 'keyword': keyword, 'match_type': match_type, 'type': type_, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_account_level_inbound_blocked_statistics(self, blocked_statistic_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAccountLevelInboundBlockedStatistics
        method: DELETE
        path: /phone/inbound_blocked/extension_rules/statistics
        summary: Delete an account's inbound blocked statistics
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/extension_rules/statistics"
        params = { 'blocked_statistic_id': blocked_statistic_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def mark_phone_number_as_blocked_for_all_extensions(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: MarkPhoneNumberAsBlockedForAllExtensions
        method: PATCH
        path: /phone/inbound_blocked/extension_rules/statistics/blocked_for_all
        summary: Mark a phone number as blocked for all extensions
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/extension_rules/statistics/blocked_for_all"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_account_level_inbound_block_rules(self, keyword: Optional[Any] = None, match_type: Optional[Any] = None, type_: Optional[Any] = None, status: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListAccountLevelInboundBlockRules
        method: GET
        path: /phone/inbound_blocked/rules
        summary: List an account's inbound block rules
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/rules"
        params = { 'keyword': keyword, 'match_type': match_type, 'type': type_, 'status': status, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_account_level_inbound_block_rules(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddAccountLevelInboundBlockRules
        method: POST
        path: /phone/inbound_blocked/rules
        summary: Add an account's inbound block rule
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/rules"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_account_level_inbound_block_rules(self, blocked_rule_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAccountLevelInboundBlockRules
        method: DELETE
        path: /phone/inbound_blocked/rules
        summary: Delete an account's inbound block rule
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/rules"
        params = { 'blocked_rule_id': blocked_rule_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_account_level_inbound_block_rule(self, blockedRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAccountLevelInboundBlockRule
        method: PATCH
        path: /phone/inbound_blocked/rules/{blockedRuleId}
        summary: Update an account's inbound block rule
        """
        endpoint = f"{self._base_url}/phone/inbound_blocked/rules/{blockedRuleId}"
        params = { 'blockedRuleId': blockedRuleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_line_key_setting(self, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listLineKeySetting
        method: GET
        path: /phone/extension/{extensionId}/line_keys
        summary: Get line key position and settings information
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/line_keys"
        params = { 'extensionId': extensionId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_update_line_key_setting(self, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: BatchUpdateLineKeySetting
        method: PATCH
        path: /phone/extension/{extensionId}/line_keys
        summary: Batch update line key position and settings information
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/line_keys"
        params = { 'extensionId': extensionId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_line_key(self, extensionId: Optional[Any] = None, lineKeyId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteLineKey
        method: DELETE
        path: /phone/extension/{extensionId}/line_keys/{lineKeyId}
        summary: Delete a line key setting.
        """
        endpoint = f"{self._base_url}/phone/extension/{extensionId}/line_keys/{lineKeyId}"
        params = { 'extensionId': extensionId, 'lineKeyId': lineKeyId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_monitoring_group(self, type_: Optional[Any] = None, site_id: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listMonitoringGroup
        method: GET
        path: /phone/monitoring_groups
        summary: Get a list of monitoring groups on an account
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups"
        params = { 'type': type_, 'site_id': site_id, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_monitoring_group(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createMonitoringGroup
        method: POST
        path: /phone/monitoring_groups
        summary: Create a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_monitoring_group_by_id(self, monitoringGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getMonitoringGroupById
        method: GET
        path: /phone/monitoring_groups/{monitoringGroupId}
        summary: Get monitoring group by ID
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}"
        params = { 'monitoringGroupId': monitoringGroupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_monitoring_group(self, monitoringGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteMonitoringGroup
        method: DELETE
        path: /phone/monitoring_groups/{monitoringGroupId}
        summary: Delete a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}"
        params = { 'monitoringGroupId': monitoringGroupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_monitoring_group(self, monitoringGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateMonitoringGroup
        method: PATCH
        path: /phone/monitoring_groups/{monitoringGroupId}
        summary: Update a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}"
        params = { 'monitoringGroupId': monitoringGroupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_members(self, monitoringGroupId: Optional[Any] = None, member_type: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listMembers
        method: GET
        path: /phone/monitoring_groups/{monitoringGroupId}/monitor_members
        summary: Get members of a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}/monitor_members"
        params = { 'monitoringGroupId': monitoringGroupId, 'member_type': member_type, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_members(self, monitoringGroupId: Optional[Any] = None, member_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addMembers
        method: POST
        path: /phone/monitoring_groups/{monitoringGroupId}/monitor_members
        summary: Add members to a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}/monitor_members"
        params = { 'monitoringGroupId': monitoringGroupId, 'member_type': member_type }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_members(self, monitoringGroupId: Optional[Any] = None, member_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeMembers
        method: DELETE
        path: /phone/monitoring_groups/{monitoringGroupId}/monitor_members
        summary: Remove all monitors or monitored members from a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}/monitor_members"
        params = { 'monitoringGroupId': monitoringGroupId, 'member_type': member_type }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_member(self, monitoringGroupId: Optional[Any] = None, memberExtensionId: Optional[Any] = None, member_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeMember
        method: DELETE
        path: /phone/monitoring_groups/{monitoringGroupId}/monitor_members/{memberExtensionId}
        summary: Remove a member from a monitoring group
        """
        endpoint = f"{self._base_url}/phone/monitoring_groups/{monitoringGroupId}/monitor_members/{memberExtensionId}"
        params = { 'monitoringGroupId': monitoringGroupId, 'memberExtensionId': memberExtensionId, 'member_type': member_type }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_common_area_outbound_calling_countries_and_regions(self, commonAreaId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetCommonAreaOutboundCallingCountriesAndRegions
        method: GET
        path: /phone/common_areas/{commonAreaId}/outbound_calling/countries_regions
        summary: Get common area level outbound calling countries and regions
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/outbound_calling/countries_regions"
        params = { 'commonAreaId': commonAreaId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_common_area_outbound_calling_countries_or_regions(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateCommonAreaOutboundCallingCountriesOrRegions
        method: PATCH
        path: /phone/common_areas/{commonAreaId}/outbound_calling/countries_regions
        summary: Update common area level outbound calling countries or regions
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/outbound_calling/countries_regions"
        params = { 'commonAreaId': commonAreaId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_common_area_outbound_calling_exception_rule(self, commonAreaId: Optional[Any] = None, country: Optional[Any] = None, keyword: Optional[Any] = None, match_type: Optional[Any] = None, status: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCommonAreaOutboundCallingExceptionRule
        method: GET
        path: /phone/common_areas/{commonAreaId}/outbound_calling/exception_rules
        summary: List common area level outbound calling exception rules
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/outbound_calling/exception_rules"
        params = { 'commonAreaId': commonAreaId, 'country': country, 'keyword': keyword, 'match_type': match_type, 'status': status, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_common_area_outbound_calling_exception_rule(self, commonAreaId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddCommonAreaOutboundCallingExceptionRule
        method: POST
        path: /phone/common_areas/{commonAreaId}/outbound_calling/exception_rules
        summary: Add common area level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/outbound_calling/exception_rules"
        params = { 'commonAreaId': commonAreaId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_common_area_outbound_calling_exception_rule(self, commonAreaId: Optional[Any] = None, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCommonAreaOutboundCallingExceptionRule
        method: DELETE
        path: /phone/common_areas/{commonAreaId}/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Delete common area level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'commonAreaId': commonAreaId, 'exceptionRuleId': exceptionRuleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_common_area_outbound_calling_exception_rule(self, commonAreaId: Optional[Any] = None, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateCommonAreaOutboundCallingExceptionRule
        method: PATCH
        path: /phone/common_areas/{commonAreaId}/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Update common area level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/common_areas/{commonAreaId}/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'commonAreaId': commonAreaId, 'exceptionRuleId': exceptionRuleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_account_outbound_calling_countries_and_regions(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAccountOutboundCallingCountriesAndRegions
        method: GET
        path: /phone/outbound_calling/countries_regions
        summary: Get account level outbound calling countries and regions
        """
        endpoint = f"{self._base_url}/phone/outbound_calling/countries_regions"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_account_outbound_calling_countries_or_regions(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAccountOutboundCallingCountriesOrRegions
        method: PATCH
        path: /phone/outbound_calling/countries_regions
        summary: Update account level outbound calling countries or regions
        """
        endpoint = f"{self._base_url}/phone/outbound_calling/countries_regions"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_account_outbound_calling_exception_rule(self, country: Optional[Any] = None, keyword: Optional[Any] = None, match_type: Optional[Any] = None, status: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listAccountOutboundCallingExceptionRule
        method: GET
        path: /phone/outbound_calling/exception_rules
        summary: List account level outbound calling exception rules
        """
        endpoint = f"{self._base_url}/phone/outbound_calling/exception_rules"
        params = { 'country': country, 'keyword': keyword, 'match_type': match_type, 'status': status, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_account_outbound_calling_exception_rule(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddAccountOutboundCallingExceptionRule
        method: POST
        path: /phone/outbound_calling/exception_rules
        summary: Add account level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/outbound_calling/exception_rules"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_account_outbound_calling_exception_rule(self, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAccountOutboundCallingExceptionRule
        method: DELETE
        path: /phone/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Delete account level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'exceptionRuleId': exceptionRuleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_account_outbound_calling_exception_rule(self, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAccountOutboundCallingExceptionRule
        method: PATCH
        path: /phone/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Update account level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'exceptionRuleId': exceptionRuleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_site_outbound_calling_countries_and_regions(self, siteId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetSiteOutboundCallingCountriesAndRegions
        method: GET
        path: /phone/sites/{siteId}/outbound_calling/countries_regions
        summary: Get site level outbound calling countries and regions
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_calling/countries_regions"
        params = { 'siteId': siteId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_site_outbound_calling_countries_or_regions(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateSiteOutboundCallingCountriesOrRegions
        method: PATCH
        path: /phone/sites/{siteId}/outbound_calling/countries_regions
        summary: Update site level outbound calling countries or regions
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_calling/countries_regions"
        params = { 'siteId': siteId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_site_outbound_calling_exception_rule(self, siteId: Optional[Any] = None, country: Optional[Any] = None, keyword: Optional[Any] = None, match_type: Optional[Any] = None, status: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSiteOutboundCallingExceptionRule
        method: GET
        path: /phone/sites/{siteId}/outbound_calling/exception_rules
        summary: List site level outbound calling exception rules
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_calling/exception_rules"
        params = { 'siteId': siteId, 'country': country, 'keyword': keyword, 'match_type': match_type, 'status': status, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_site_outbound_calling_exception_rule(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddSiteOutboundCallingExceptionRule
        method: POST
        path: /phone/sites/{siteId}/outbound_calling/exception_rules
        summary: Add site level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_calling/exception_rules"
        params = { 'siteId': siteId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_site_outbound_calling_exception_rule(self, siteId: Optional[Any] = None, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteSiteOutboundCallingExceptionRule
        method: DELETE
        path: /phone/sites/{siteId}/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Delete site level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'siteId': siteId, 'exceptionRuleId': exceptionRuleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_site_outbound_calling_exception_rule(self, siteId: Optional[Any] = None, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateSiteOutboundCallingExceptionRule
        method: PATCH
        path: /phone/sites/{siteId}/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Update site level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'siteId': siteId, 'exceptionRuleId': exceptionRuleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_outbound_calling_countries_and_regions(self, userId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetUserOutboundCallingCountriesAndRegions
        method: GET
        path: /phone/users/{userId}/outbound_calling/countries_regions
        summary: Get user level outbound calling countries and regions
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_calling/countries_regions"
        params = { 'userId': userId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_outbound_calling_countries_or_regions(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateUserOutboundCallingCountriesOrRegions
        method: PATCH
        path: /phone/users/{userId}/outbound_calling/countries_regions
        summary: Update user level outbound calling countries or regions
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_calling/countries_regions"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_outbound_calling_exception_rule(self, userId: Optional[Any] = None, country: Optional[Any] = None, keyword: Optional[Any] = None, match_type: Optional[Any] = None, status: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserOutboundCallingExceptionRule
        method: GET
        path: /phone/users/{userId}/outbound_calling/exception_rules
        summary: List user level outbound calling exception rules
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_calling/exception_rules"
        params = { 'userId': userId, 'country': country, 'keyword': keyword, 'match_type': match_type, 'status': status, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_user_outbound_calling_exception_rule(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddUserOutboundCallingExceptionRule
        method: POST
        path: /phone/users/{userId}/outbound_calling/exception_rules
        summary: Add user level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_calling/exception_rules"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_user_outbound_calling_exception_rule(self, userId: Optional[Any] = None, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteUserOutboundCallingExceptionRule
        method: DELETE
        path: /phone/users/{userId}/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Delete user level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'userId': userId, 'exceptionRuleId': exceptionRuleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_outbound_calling_exception_rule(self, userId: Optional[Any] = None, exceptionRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateUserOutboundCallingExceptionRule
        method: PATCH
        path: /phone/users/{userId}/outbound_calling/exception_rules/{exceptionRuleId}
        summary: Update user level outbound calling exception rule
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_calling/exception_rules/{exceptionRuleId}"
        params = { 'userId': userId, 'exceptionRuleId': exceptionRuleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_devices(self, type_: Optional[Any] = None, assignee_type: Optional[Any] = None, device_source: Optional[Any] = None, location_status: Optional[Any] = None, site_id: Optional[Any] = None, device_type: Optional[Any] = None, keyword: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPhoneDevices
        method: GET
        path: /phone/devices
        summary: List devices
        """
        endpoint = f"{self._base_url}/phone/devices"
        params = { 'type': type_, 'assignee_type': assignee_type, 'device_source': device_source, 'location_status': location_status, 'site_id': site_id, 'device_type': device_type, 'keyword': keyword, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_phone_device(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addPhoneDevice
        method: POST
        path: /phone/devices
        summary: Add a device
        """
        endpoint = f"{self._base_url}/phone/devices"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def sync_phone_device(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: syncPhoneDevice
        method: POST
        path: /phone/devices/sync
        summary: Sync deskphones
        """
        endpoint = f"{self._base_url}/phone/devices/sync"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getADevice
        method: GET
        path: /phone/devices/{deviceId}
        summary: Get device details
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteADevice
        method: DELETE
        path: /phone/devices/{deviceId}
        summary: Delete a device
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateADevice
        method: PATCH
        path: /phone/devices/{deviceId}
        summary: Update a device
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_extensions_to_a_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addExtensionsToADevice
        method: POST
        path: /phone/devices/{deviceId}/extensions
        summary: Assign an entity to a device
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}/extensions"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_extension_from_a_device(self, deviceId: Optional[Any] = None, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteExtensionFromADevice
        method: DELETE
        path: /phone/devices/{deviceId}/extensions/{extensionId}
        summary: Unassign an entity from the device
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}/extensions/{extensionId}"
        params = { 'deviceId': deviceId, 'extensionId': extensionId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_provision_template_to_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateProvisionTemplateToDevice
        method: PUT
        path: /phone/devices/{deviceId}/provision_templates
        summary: Update provision template of a device
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}/provision_templates"
        params = { 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def reboot_phone_device(self, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: rebootPhoneDevice
        method: POST
        path: /phone/devices/{deviceId}/reboot
        summary: Reboot a desk phone
        """
        endpoint = f"{self._base_url}/phone/devices/{deviceId}/reboot"
        params = { 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_smartphones(self, site_id: Optional[Any] = None, keyword: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListSmartphones
        method: GET
        path: /phone/smartphones
        summary: List Smartphones
        """
        endpoint = f"{self._base_url}/phone/smartphones"
        params = { 'site_id': site_id, 'keyword': keyword, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_byoc_number(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addBYOCNumber
        method: POST
        path: /phone/byoc_numbers
        summary: Add BYOC phone numbers
        """
        endpoint = f"{self._base_url}/phone/byoc_numbers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_account_phone_numbers(self, next_page_token: Optional[Any] = None, type_: Optional[Any] = None, extension_type: Optional[Any] = None, page_size: Optional[Any] = None, number_type: Optional[Any] = None, pending_numbers: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listAccountPhoneNumbers
        method: GET
        path: /phone/numbers
        summary: List phone numbers
        """
        endpoint = f"{self._base_url}/phone/numbers"
        params = { 'next_page_token': next_page_token, 'type': type_, 'extension_type': extension_type, 'page_size': page_size, 'number_type': number_type, 'pending_numbers': pending_numbers, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_unassigned_phone_numbers(self, phone_numbers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteUnassignedPhoneNumbers
        method: DELETE
        path: /phone/numbers
        summary: Delete unassigned phone numbers
        """
        endpoint = f"{self._base_url}/phone/numbers"
        params = { 'phone_numbers': phone_numbers }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_site_for_unassigned_phone_numbers(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSiteForUnassignedPhoneNumbers
        method: PATCH
        path: /phone/numbers/sites/{siteId}
        summary: Update a site's unassigned phone numbers
        """
        endpoint = f"{self._base_url}/phone/numbers/sites/{siteId}"
        params = { 'siteId': siteId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_phone_number_details(self, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getPhoneNumberDetails
        method: GET
        path: /phone/numbers/{phoneNumberId}
        summary: Get a phone number
        """
        endpoint = f"{self._base_url}/phone/numbers/{phoneNumberId}"
        params = { 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_phone_number_details(self, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updatePhoneNumberDetails
        method: PATCH
        path: /phone/numbers/{phoneNumberId}
        summary: Update a phone number
        """
        endpoint = f"{self._base_url}/phone/numbers/{phoneNumberId}"
        params = { 'phoneNumberId': phoneNumberId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_phone_number(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignPhoneNumber
        method: POST
        path: /phone/users/{userId}/phone_numbers
        summary: Assign a phone number to a user
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/phone_numbers"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_phone_number(self, userId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UnassignPhoneNumber
        method: DELETE
        path: /phone/users/{userId}/phone_numbers/{phoneNumberId}
        summary: Unassign a phone number
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/phone_numbers/{phoneNumberId}"
        params = { 'userId': userId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_calling_plans(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCallingPlans
        method: GET
        path: /phone/calling_plans
        summary: List calling plans
        """
        endpoint = f"{self._base_url}/phone/calling_plans"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_plans(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPhonePlans
        method: GET
        path: /phone/plans
        summary: List plan information
        """
        endpoint = f"{self._base_url}/phone/plans"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_roles(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListPhoneRoles
        method: GET
        path: /phone/roles
        summary: List phone roles
        """
        endpoint = f"{self._base_url}/phone/roles"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def duplicate_phone_role(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DuplicatePhoneRole
        method: POST
        path: /phone/roles
        summary: Duplicate a phone role
        """
        endpoint = f"{self._base_url}/phone/roles"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_role_information_1(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getRoleInformation
        method: GET
        path: /phone/roles/{roleId}
        summary: Get role information
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}"
        params = { 'roleId': roleId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_phone_role(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeletePhoneRole
        method: DELETE
        path: /phone/roles/{roleId}
        summary: Delete a phone role
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}"
        params = { 'roleId': roleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_phone_role(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdatePhoneRole
        method: PATCH
        path: /phone/roles/{roleId}
        summary: Update a phone role
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}"
        params = { 'roleId': roleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_role_members(self, roleId: Optional[Any] = None, in_role: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListRoleMembers
        method: GET
        path: /phone/roles/{roleId}/members
        summary: List members in a role
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}/members"
        params = { 'roleId': roleId, 'in_role': in_role }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_role_members_1(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddRoleMembers
        method: POST
        path: /phone/roles/{roleId}/members
        summary: Add members to roles
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}/members"
        params = { 'roleId': roleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def del_role_members(self, roleId: Optional[Any] = None, user_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DelRoleMembers
        method: DELETE
        path: /phone/roles/{roleId}/members
        summary: Delete members in a role
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}/members"
        params = { 'roleId': roleId, 'user_ids': user_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_role_targets(self, roleId: Optional[Any] = None, is_default: Optional[Any] = None, user_id: Optional[Any] = None, selected: Optional[Any] = None, target_type: Optional[Any] = None, site_id: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListPhoneRoleTargets
        method: GET
        path: /phone/roles/{roleId}/targets
        summary: List phone role targets
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}/targets"
        params = { 'roleId': roleId, 'is_default': is_default, 'user_id': user_id, 'selected': selected, 'target_type': target_type, 'site_id': site_id, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_phone_role_targets(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddPhoneRoleTargets
        method: POST
        path: /phone/roles/{roleId}/targets
        summary: Add phone role targets
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}/targets"
        params = { 'roleId': roleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_phone_role_targets(self, roleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeletePhoneRoleTargets
        method: DELETE
        path: /phone/roles/{roleId}/targets
        summary: Delete phone role targets
        """
        endpoint = f"{self._base_url}/phone/roles/{roleId}/targets"
        params = { 'roleId': roleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_private_directory_members(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, keyword: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPrivateDirectoryMembers
        method: GET
        path: /phone/private_directory/members
        summary: List private directory members
        """
        endpoint = f"{self._base_url}/phone/private_directory/members"
        params = { 'next_page_token': next_page_token, 'page_size': page_size, 'keyword': keyword, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_members_to_a_private_directory(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addMembersToAPrivateDirectory
        method: POST
        path: /phone/private_directory/members
        summary: Add members to a private directory
        """
        endpoint = f"{self._base_url}/phone/private_directory/members"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_a_member_from_a_private_directory(self, extensionId: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeAMemberFromAPrivateDirectory
        method: DELETE
        path: /phone/private_directory/members/{extensionId}
        summary: Remove a member from a private directory
        """
        endpoint = f"{self._base_url}/phone/private_directory/members/{extensionId}"
        params = { 'extensionId': extensionId, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_private_directory_member(self, extensionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateAPrivateDirectoryMember
        method: PATCH
        path: /phone/private_directory/members/{extensionId}
        summary: Update a private directory member
        """
        endpoint = f"{self._base_url}/phone/private_directory/members/{extensionId}"
        params = { 'extensionId': extensionId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_carrier_peering_phone_numbers(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, phone_number: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCarrierPeeringPhoneNumbers
        method: GET
        path: /phone/carrier_peering/numbers
        summary: List carrier peering phone numbers.
        """
        endpoint = f"{self._base_url}/phone/carrier_peering/numbers"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'phone_number': phone_number }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_peering_phone_numbers(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, phone_number: Optional[Any] = None, carrier_code: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPeeringPhoneNumbers
        method: GET
        path: /phone/peering/numbers
        summary: List peering phone numbers
        """
        endpoint = f"{self._base_url}/phone/peering/numbers"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'phone_number': phone_number, 'carrier_code': carrier_code }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_peering_phone_numbers(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addPeeringPhoneNumbers
        method: POST
        path: /phone/peering/numbers
        summary: Add peering phone numbers
        """
        endpoint = f"{self._base_url}/phone/peering/numbers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_peering_phone_numbers(self, carrier_code: Optional[Any] = None, phone_numbers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deletePeeringPhoneNumbers
        method: DELETE
        path: /phone/peering/numbers
        summary: Remove peering phone numbers
        """
        endpoint = f"{self._base_url}/phone/peering/numbers"
        params = { 'carrier_code': carrier_code, 'phone_numbers': phone_numbers }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_peering_phone_numbers(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updatePeeringPhoneNumbers
        method: PATCH
        path: /phone/peering/numbers
        summary: Update peering phone numbers
        """
        endpoint = f"{self._base_url}/phone/peering/numbers"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_account_provision_template(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listAccountProvisionTemplate
        method: GET
        path: /phone/provision_templates
        summary: List provision templates
        """
        endpoint = f"{self._base_url}/phone/provision_templates"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_provision_template(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addProvisionTemplate
        method: POST
        path: /phone/provision_templates
        summary: Add a provision template
        """
        endpoint = f"{self._base_url}/phone/provision_templates"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_provision_template(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetProvisionTemplate
        method: GET
        path: /phone/provision_templates/{templateId}
        summary: Get a provision template
        """
        endpoint = f"{self._base_url}/phone/provision_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_provision_template(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteProvisionTemplate
        method: DELETE
        path: /phone/provision_templates/{templateId}
        summary: Delete a provision template
        """
        endpoint = f"{self._base_url}/phone/provision_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_provision_template(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateProvisionTemplate
        method: PATCH
        path: /phone/provision_templates/{templateId}
        summary: Update a provision template
        """
        endpoint = f"{self._base_url}/phone/provision_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_phone_recordings_by_call_id_or_call_log_id(self, id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getPhoneRecordingsByCallIdOrCallLogId
        method: GET
        path: /phone/call_logs/{id}/recordings
        summary: Get recording by call ID
        """
        endpoint = f"{self._base_url}/phone/call_logs/{id}/recordings"
        params = { 'id': id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_download_recording_file(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneDownloadRecordingFile
        method: GET
        path: /phone/recording/download/{fileId}
        summary: Download a phone recording
        """
        endpoint = f"{self._base_url}/phone/recording/download/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_download_recording_transcript(self, recordingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneDownloadRecordingTranscript
        method: GET
        path: /phone/recording_transcript/download/{recordingId}
        summary: Download a phone recording transcript
        """
        endpoint = f"{self._base_url}/phone/recording_transcript/download/{recordingId}"
        params = { 'recordingId': recordingId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_phone_recordings(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, owner_type: Optional[Any] = None, recording_type: Optional[Any] = None, site_id: Optional[Any] = None, query_date_type: Optional[Any] = None, group_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getPhoneRecordings
        method: GET
        path: /phone/recordings
        summary: Get call recordings
        """
        endpoint = f"{self._base_url}/phone/recordings"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'owner_type': owner_type, 'recording_type': recording_type, 'site_id': site_id, 'query_date_type': query_date_type, 'group_id': group_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_call_recording(self, recordingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteCallRecording
        method: DELETE
        path: /phone/recordings/{recordingId}
        summary: Delete a call recording
        """
        endpoint = f"{self._base_url}/phone/recordings/{recordingId}"
        params = { 'recordingId': recordingId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_auto_delete_field(self, recordingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAutoDeleteField
        method: PATCH
        path: /phone/recordings/{recordingId}
        summary: Update auto delete field
        """
        endpoint = f"{self._base_url}/phone/recordings/{recordingId}"
        params = { 'recordingId': recordingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_recording_status(self, recordingId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateRecordingStatus
        method: PUT
        path: /phone/recordings/{recordingId}/status
        summary: Update Recording Status
        """
        endpoint = f"{self._base_url}/phone/recordings/{recordingId}/status"
        params = { 'recordingId': recordingId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_user_recordings(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneUserRecordings
        method: GET
        path: /phone/users/{userId}/recordings
        summary: Get user's recordings
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/recordings"
        params = { 'userId': userId, 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_call_charges_usage_report(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, billing_account_id: Optional[Any] = None, show_charges_only: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetCallChargesUsageReport
        method: GET
        path: /phone/reports/call_charges
        summary: Get call charges usage report
        """
        endpoint = f"{self._base_url}/phone/reports/call_charges"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'billing_account_id': billing_account_id, 'show_charges_only': show_charges_only }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def getfaxchargesusagereport(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, fax_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getfaxchargesusagereport
        method: GET
        path: /phone/reports/fax_charges
        summary: Get fax charges usage report
        """
        endpoint = f"{self._base_url}/phone/reports/fax_charges"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'fax_id': fax_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_ps_operation_logs(self, from_: Optional[Any] = None, to: Optional[Any] = None, category_type: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getPSOperationLogs
        method: GET
        path: /phone/reports/operationlogs
        summary: Get operation logs report
        """
        endpoint = f"{self._base_url}/phone/reports/operationlogs"
        params = { 'from': from_, 'to': to, 'category_type': category_type, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_sms_charges_usage_report(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, show_charges_only: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetSMSChargesUsageReport
        method: GET
        path: /phone/reports/sms_charges
        summary: Get SMS/MMS charges usage report
        """
        endpoint = f"{self._base_url}/phone/reports/sms_charges"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'show_charges_only': show_charges_only }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_routing_rule(self, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listRoutingRule
        method: GET
        path: /phone/routing_rules
        summary: List directory backup routing rules
        """
        endpoint = f"{self._base_url}/phone/routing_rules"
        params = { 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_routing_rule(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addRoutingRule
        method: POST
        path: /phone/routing_rules
        summary: Add directory backup routing rule
        """
        endpoint = f"{self._base_url}/phone/routing_rules"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_routing_rule(self, routingRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getRoutingRule
        method: GET
        path: /phone/routing_rules/{routingRuleId}
        summary: Get directory backup routing rule
        """
        endpoint = f"{self._base_url}/phone/routing_rules/{routingRuleId}"
        params = { 'routingRuleId': routingRuleId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_routing_rule(self, routingRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteRoutingRule
        method: DELETE
        path: /phone/routing_rules/{routingRuleId}
        summary: Delete directory backup routing rule
        """
        endpoint = f"{self._base_url}/phone/routing_rules/{routingRuleId}"
        params = { 'routingRuleId': routingRuleId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_routing_rule(self, routingRuleId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateRoutingRule
        method: PATCH
        path: /phone/routing_rules/{routingRuleId}
        summary: Update directory backup routing rule
        """
        endpoint = f"{self._base_url}/phone/routing_rules/{routingRuleId}"
        params = { 'routingRuleId': routingRuleId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def post_sms_message(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: postSmsMessage
        method: POST
        path: /phone/sms/messages
        summary: Post SMS message
        """
        endpoint = f"{self._base_url}/phone/sms/messages"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_sms_session(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, session_type: Optional[Any] = None, phone_number: Optional[Any] = None, filter_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountSmsSession
        method: GET
        path: /phone/sms/sessions
        summary: Get account's SMS sessions
        """
        endpoint = f"{self._base_url}/phone/sms/sessions"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'session_type': session_type, 'phone_number': phone_number, 'filter_type': filter_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def sms_session_details(self, sessionId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, sort: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: smsSessionDetails
        method: GET
        path: /phone/sms/sessions/{sessionId}
        summary: Get SMS session details
        """
        endpoint = f"{self._base_url}/phone/sms/sessions/{sessionId}"
        params = { 'sessionId': sessionId, 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'sort': sort }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def sms_by_message_id(self, sessionId: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: smsByMessageId
        method: GET
        path: /phone/sms/sessions/{sessionId}/messages/{messageId}
        summary: Get SMS by message ID
        """
        endpoint = f"{self._base_url}/phone/sms/sessions/{sessionId}/messages/{messageId}"
        params = { 'sessionId': sessionId, 'messageId': messageId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def sms_session_sync(self, sessionId: Optional[Any] = None, sync_type: Optional[Any] = None, count: Optional[Any] = None, sync_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: smsSessionSync
        method: GET
        path: /phone/sms/sessions/{sessionId}/sync
        summary: Sync SMS by session ID
        """
        endpoint = f"{self._base_url}/phone/sms/sessions/{sessionId}/sync"
        params = { 'sessionId': sessionId, 'sync_type': sync_type, 'count': count, 'sync_token': sync_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_sms_session(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, session_type: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, phone_number: Optional[Any] = None, filter_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSmsSession
        method: GET
        path: /phone/users/{userId}/sms/sessions
        summary: Get user's SMS sessions
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/sms/sessions"
        params = { 'userId': userId, 'page_size': page_size, 'next_page_token': next_page_token, 'session_type': session_type, 'from': from_, 'to': to, 'phone_number': phone_number, 'filter_type': filter_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_sms_sessions(self, userId: Optional[Any] = None, sync_type: Optional[Any] = None, sync_token: Optional[Any] = None, count: Optional[Any] = None, session_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetSmsSessions
        method: GET
        path: /phone/users/{userId}/sms/sessions/sync
        summary: List user's SMS sessions in descending order
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/sms/sessions/sync"
        params = { 'userId': userId, 'sync_type': sync_type, 'sync_token': sync_token, 'count': count, 'session_type': session_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_account_sms_campaigns(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listAccountSMSCampaigns
        method: GET
        path: /phone/sms_campaigns
        summary: List SMS campaigns
        """
        endpoint = f"{self._base_url}/phone/sms_campaigns"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_sms_campaign(self, smsCampaignId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetSMSCampaign
        method: GET
        path: /phone/sms_campaigns/{smsCampaignId}
        summary: Get an SMS campaign
        """
        endpoint = f"{self._base_url}/phone/sms_campaigns/{smsCampaignId}"
        params = { 'smsCampaignId': smsCampaignId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_campaign_phone_numbers(self, smsCampaignId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignCampaignPhoneNumbers
        method: POST
        path: /phone/sms_campaigns/{smsCampaignId}/phone_numbers
        summary: Assign a phone number to SMS campaign
        """
        endpoint = f"{self._base_url}/phone/sms_campaigns/{smsCampaignId}/phone_numbers"
        params = { 'smsCampaignId': smsCampaignId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_number_campaign_opt_status(self, smsCampaignId: Optional[Any] = None, consumer_phone_number: Optional[Any] = None, zoom_phone_user_numbers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getNumberCampaignOptStatus
        method: GET
        path: /phone/sms_campaigns/{smsCampaignId}/phone_numbers/opt_status
        summary: List opt statuses of phone numbers assigned to SMS campaign
        """
        endpoint = f"{self._base_url}/phone/sms_campaigns/{smsCampaignId}/phone_numbers/opt_status"
        params = { 'smsCampaignId': smsCampaignId, 'consumer_phone_number': consumer_phone_number, 'zoom_phone_user_numbers': zoom_phone_user_numbers }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_number_campaign_opt_status(self, smsCampaignId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateNumberCampaignOptStatus
        method: PATCH
        path: /phone/sms_campaigns/{smsCampaignId}/phone_numbers/opt_status
        summary: Update opt statuses of phone numbers assigned to SMS campaign
        """
        endpoint = f"{self._base_url}/phone/sms_campaigns/{smsCampaignId}/phone_numbers/opt_status"
        params = { 'smsCampaignId': smsCampaignId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_campaign_phone_number(self, smsCampaignId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignCampaignPhoneNumber
        method: DELETE
        path: /phone/sms_campaigns/{smsCampaignId}/phone_numbers/{phoneNumberId}
        summary: Unassign a phone number
        """
        endpoint = f"{self._base_url}/phone/sms_campaigns/{smsCampaignId}/phone_numbers/{phoneNumberId}"
        params = { 'smsCampaignId': smsCampaignId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_number_campaign_opt_status(self, userId: Optional[Any] = None, consumer_phone_numbers: Optional[Any] = None, zoom_phone_user_numbers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserNumberCampaignOptStatus
        method: GET
        path: /phone/user/{userId}/sms_campaigns/phone_numbers/opt_status
        summary: List user's opt statuses of phone numbers
        """
        endpoint = f"{self._base_url}/phone/user/{userId}/sms_campaigns/phone_numbers/opt_status"
        params = { 'userId': userId, 'consumer_phone_numbers': consumer_phone_numbers, 'zoom_phone_user_numbers': zoom_phone_user_numbers }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_setting_templates(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSettingTemplates
        method: GET
        path: /phone/setting_templates
        summary: List setting templates
        """
        endpoint = f"{self._base_url}/phone/setting_templates"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'site_id': site_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_setting_template(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addSettingTemplate
        method: POST
        path: /phone/setting_templates
        summary: Add a setting template
        """
        endpoint = f"{self._base_url}/phone/setting_templates"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_setting_template(self, templateId: Optional[Any] = None, custom_query_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSettingTemplate
        method: GET
        path: /phone/setting_templates/{templateId}
        summary: Get setting template details
        """
        endpoint = f"{self._base_url}/phone/setting_templates/{templateId}"
        params = { 'templateId': templateId, 'custom_query_fields': custom_query_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_setting_template(self, templateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSettingTemplate
        method: PATCH
        path: /phone/setting_templates/{templateId}
        summary: Update a setting template
        """
        endpoint = f"{self._base_url}/phone/setting_templates/{templateId}"
        params = { 'templateId': templateId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_account_policy_details(self, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAccountPolicyDetails
        method: GET
        path: /phone/policies/{policyType}
        summary: Get account policy details
        """
        endpoint = f"{self._base_url}/phone/policies/{policyType}"
        params = { 'policyType': policyType }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_account_policy(self, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateAccountPolicy
        method: PATCH
        path: /phone/policies/{policyType}
        summary: Update account policy
        """
        endpoint = f"{self._base_url}/phone/policies/{policyType}"
        params = { 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_ported_numbers(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPortedNumbers
        method: GET
        path: /phone/ported_numbers/orders
        summary: List ported numbers
        """
        endpoint = f"{self._base_url}/phone/ported_numbers/orders"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_ported_numbers_details(self, orderId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getPortedNumbersDetails
        method: GET
        path: /phone/ported_numbers/orders/{orderId}
        summary: Get ported number details
        """
        endpoint = f"{self._base_url}/phone/ported_numbers/orders/{orderId}"
        params = { 'orderId': orderId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_setting(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneSetting
        method: GET
        path: /phone/settings
        summary: Get phone account settings
        """
        endpoint = f"{self._base_url}/phone/settings"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_phone_settings(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updatePhoneSettings
        method: PATCH
        path: /phone/settings
        summary: Update phone account settings
        """
        endpoint = f"{self._base_url}/phone/settings"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_sip_groups(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSipGroups
        method: GET
        path: /phone/sip_groups
        summary: List SIP groups
        """
        endpoint = f"{self._base_url}/phone/sip_groups"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_byocsip_trunk(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listBYOCSIPTrunk
        method: GET
        path: /phone/sip_trunk/trunks
        summary: List BYOC SIP trunks
        """
        endpoint = f"{self._base_url}/phone/sip_trunk/trunks"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_shared_line_appearances(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSharedLineAppearances
        method: GET
        path: /phone/shared_line_appearances
        summary: List shared line appearances
        """
        endpoint = f"{self._base_url}/phone/shared_line_appearances"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_shared_line_groups(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSharedLineGroups
        method: GET
        path: /phone/shared_line_groups
        summary: List shared line groups
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_a_shared_line_group(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createASharedLineGroup
        method: POST
        path: /phone/shared_line_groups
        summary: Create a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_shared_line_group(self, sharedLineGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getASharedLineGroup
        method: GET
        path: /phone/shared_line_groups/{sharedLineGroupId}
        summary: Get a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{sharedLineGroupId}"
        params = { 'sharedLineGroupId': sharedLineGroupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_shared_line_group_policy(self, sharedLineGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSharedLineGroupPolicy
        method: GET
        path: /phone/shared_line_groups/{sharedLineGroupId}/policies
        summary: Get a shared line group policy
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{sharedLineGroupId}/policies"
        params = { 'sharedLineGroupId': sharedLineGroupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_shared_line_group_policy(self, sharedLineGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSharedLineGroupPolicy
        method: PATCH
        path: /phone/shared_line_groups/{sharedLineGroupId}/policies
        summary: Update a shared line group policy
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{sharedLineGroupId}/policies"
        params = { 'sharedLineGroupId': sharedLineGroupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_shared_line_group(self, slgId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteASharedLineGroup
        method: DELETE
        path: /phone/shared_line_groups/{slgId}
        summary: Delete a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}"
        params = { 'slgId': slgId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_shared_line_group(self, slgId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateASharedLineGroup
        method: PATCH
        path: /phone/shared_line_groups/{slgId}
        summary: Update a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}"
        params = { 'slgId': slgId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_members_to_shared_line_group(self, slgId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addMembersToSharedLineGroup
        method: POST
        path: /phone/shared_line_groups/{slgId}/members
        summary: Add members to a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/members"
        params = { 'slgId': slgId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_members_of_slg(self, slgId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteMembersOfSLG
        method: DELETE
        path: /phone/shared_line_groups/{slgId}/members
        summary: Unassign members from a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/members"
        params = { 'slgId': slgId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_member_slg(self, slgId: Optional[Any] = None, memberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAMemberSLG
        method: DELETE
        path: /phone/shared_line_groups/{slgId}/members/{memberId}
        summary: Unassign a member from a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/members/{memberId}"
        params = { 'slgId': slgId, 'memberId': memberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_phone_numbers_slg(self, slgId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignPhoneNumbersSLG
        method: POST
        path: /phone/shared_line_groups/{slgId}/phone_numbers
        summary: Assign phone numbers
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/phone_numbers"
        params = { 'slgId': slgId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_phone_numbers_slg(self, slgId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deletePhoneNumbersSLG
        method: DELETE
        path: /phone/shared_line_groups/{slgId}/phone_numbers
        summary: Unassign all phone numbers
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/phone_numbers"
        params = { 'slgId': slgId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_phone_number_slg(self, slgId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAPhoneNumberSLG
        method: DELETE
        path: /phone/shared_line_groups/{slgId}/phone_numbers/{phoneNumberId}
        summary: Unassign a phone number
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/phone_numbers/{phoneNumberId}"
        params = { 'slgId': slgId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_slg_policy_sub_setting(self, slgId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addSLGPolicySubSetting
        method: POST
        path: /phone/shared_line_groups/{slgId}/policies/{policyType}
        summary: Add a policy setting to a shared line group
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/policies/{policyType}"
        params = { 'slgId': slgId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_slg_policy_sub_setting(self, slgId: Optional[Any] = None, policyType: Optional[Any] = None, shared_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeSLGPolicySubSetting
        method: DELETE
        path: /phone/shared_line_groups/{slgId}/policies/{policyType}
        summary: Delete an SLG policy setting
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/policies/{policyType}"
        params = { 'slgId': slgId, 'policyType': policyType, 'shared_ids': shared_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_slg_policy_sub_setting(self, slgId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSLGPolicySubSetting
        method: PATCH
        path: /phone/shared_line_groups/{slgId}/policies/{policyType}
        summary: Update an SLG policy setting
        """
        endpoint = f"{self._base_url}/phone/shared_line_groups/{slgId}/policies/{policyType}"
        params = { 'slgId': slgId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_sites(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPhoneSites
        method: GET
        path: /phone/sites
        summary: List phone sites
        """
        endpoint = f"{self._base_url}/phone/sites"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_phone_site(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createPhoneSite
        method: POST
        path: /phone/sites
        summary: Create a phone site
        """
        endpoint = f"{self._base_url}/phone/sites"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_site(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getASite
        method: GET
        path: /phone/sites/{siteId}
        summary: Get phone site details
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}"
        params = { 'siteId': siteId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_phone_site(self, siteId: Optional[Any] = None, transfer_site_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deletePhoneSite
        method: DELETE
        path: /phone/sites/{siteId}
        summary: Delete a phone site
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}"
        params = { 'siteId': siteId, 'transfer_site_id': transfer_site_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_site_details(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSiteDetails
        method: PATCH
        path: /phone/sites/{siteId}
        summary: Update phone site details
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}"
        params = { 'siteId': siteId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_site_customize_outbound_caller_numbers(self, siteId: Optional[Any] = None, selected: Optional[Any] = None, site_id: Optional[Any] = None, extension_type: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSiteCustomizeOutboundCallerNumbers
        method: GET
        path: /phone/sites/{siteId}/outbound_caller_id/customized_numbers
        summary: List customized outbound caller ID phone numbers
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_caller_id/customized_numbers"
        params = { 'siteId': siteId, 'selected': selected, 'site_id': site_id, 'extension_type': extension_type, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_site_outbound_caller_numbers(self, siteId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addSiteOutboundCallerNumbers
        method: POST
        path: /phone/sites/{siteId}/outbound_caller_id/customized_numbers
        summary: Add customized outbound caller ID phone numbers
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_caller_id/customized_numbers"
        params = { 'siteId': siteId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_site_outbound_caller_numbers(self, siteId: Optional[Any] = None, customize_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteSiteOutboundCallerNumbers
        method: DELETE
        path: /phone/sites/{siteId}/outbound_caller_id/customized_numbers
        summary: Remove customized outbound caller ID phone numbers
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/outbound_caller_id/customized_numbers"
        params = { 'siteId': siteId, 'customize_ids': customize_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_site_setting_for_type(self, siteId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getSiteSettingForType
        method: GET
        path: /phone/sites/{siteId}/settings/{settingType}
        summary: Get a phone site setting
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/settings/{settingType}"
        params = { 'siteId': siteId, 'settingType': settingType }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_site_setting(self, siteId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addSiteSetting
        method: POST
        path: /phone/sites/{siteId}/settings/{settingType}
        summary: Add a site setting
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/settings/{settingType}"
        params = { 'siteId': siteId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_site_setting(self, siteId: Optional[Any] = None, settingType: Optional[Any] = None, device_type: Optional[Any] = None, holiday_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteSiteSetting
        method: DELETE
        path: /phone/sites/{siteId}/settings/{settingType}
        summary: Delete a site setting
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/settings/{settingType}"
        params = { 'siteId': siteId, 'settingType': settingType, 'device_type': device_type, 'holiday_id': holiday_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_site_setting(self, siteId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSiteSetting
        method: PATCH
        path: /phone/sites/{siteId}/settings/{settingType}
        summary: Update the site setting
        """
        endpoint = f"{self._base_url}/phone/sites/{siteId}/settings/{settingType}"
        params = { 'siteId': siteId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_phone_users(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, site_id: Optional[Any] = None, calling_type: Optional[Any] = None, status: Optional[Any] = None, department: Optional[Any] = None, cost_center: Optional[Any] = None, keyword: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listPhoneUsers
        method: GET
        path: /phone/users
        summary: List phone users
        """
        endpoint = f"{self._base_url}/phone/users"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'site_id': site_id, 'calling_type': calling_type, 'status': status, 'department': department, 'cost_center': cost_center, 'keyword': keyword }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_users_properties_in_batch(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateUsersPropertiesInBatch
        method: PUT
        path: /phone/users/batch
        summary: Update multiple users' properties in batch
        """
        endpoint = f"{self._base_url}/phone/users/batch"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_add_users(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchAddUsers
        method: POST
        path: /phone/users/batch
        summary: Batch add users
        """
        endpoint = f"{self._base_url}/phone/users/batch"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_user(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneUser
        method: GET
        path: /phone/users/{userId}
        summary: Get a user's profile
        """
        endpoint = f"{self._base_url}/phone/users/{userId}"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_profile(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateUserProfile
        method: PATCH
        path: /phone/users/{userId}
        summary: Update a user's profile
        """
        endpoint = f"{self._base_url}/phone/users/{userId}"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_calling_plan(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateCallingPlan
        method: PUT
        path: /phone/users/{userId}/calling_plans
        summary: Update user's calling plan
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/calling_plans"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_calling_plan(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignCallingPlan
        method: POST
        path: /phone/users/{userId}/calling_plans
        summary: Assign calling plan to a user
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/calling_plans"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_calling_plan(self, userId: Optional[Any] = None, planType: Optional[Any] = None, billing_account_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignCallingPlan
        method: DELETE
        path: /phone/users/{userId}/calling_plans/{planType}
        summary: Unassign user's calling plan
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/calling_plans/{planType}"
        params = { 'userId': userId, 'planType': planType, 'billing_account_id': billing_account_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_customize_outbound_caller_numbers(self, userId: Optional[Any] = None, selected: Optional[Any] = None, site_id: Optional[Any] = None, extension_type: Optional[Any] = None, keyword: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserCustomizeOutboundCallerNumbers
        method: GET
        path: /phone/users/{userId}/outbound_caller_id/customized_numbers
        summary: List users' phone numbers for a customized outbound caller ID
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_caller_id/customized_numbers"
        params = { 'userId': userId, 'selected': selected, 'site_id': site_id, 'extension_type': extension_type, 'keyword': keyword, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_user_outbound_caller_numbers(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addUserOutboundCallerNumbers
        method: POST
        path: /phone/users/{userId}/outbound_caller_id/customized_numbers
        summary: Add phone numbers for users' customized outbound caller ID
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_caller_id/customized_numbers"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_user_outbound_caller_numbers(self, userId: Optional[Any] = None, customize_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteUserOutboundCallerNumbers
        method: DELETE
        path: /phone/users/{userId}/outbound_caller_id/customized_numbers
        summary: Remove users' customized outbound caller ID phone numbers
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/outbound_caller_id/customized_numbers"
        params = { 'userId': userId, 'customize_ids': customize_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_policy_details(self, userId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetUserPolicyDetails
        method: GET
        path: /phone/users/{userId}/policies/{policyType}
        summary: Get user policy details
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/policies/{policyType}"
        params = { 'userId': userId, 'policyType': policyType }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_policy(self, userId: Optional[Any] = None, policyType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateUserPolicy
        method: PATCH
        path: /phone/users/{userId}/policies/{policyType}
        summary: Update user policy
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/policies/{policyType}"
        params = { 'userId': userId, 'policyType': policyType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_user_settings(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneUserSettings
        method: GET
        path: /phone/users/{userId}/settings
        summary: Get a user's profile settings
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/settings"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_settings(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateUserSettings
        method: PATCH
        path: /phone/users/{userId}/settings
        summary: Update a user's profile settings
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/settings"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_user_setting(self, userId: Optional[Any] = None, settingType: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addUserSetting
        method: POST
        path: /phone/users/{userId}/settings/{settingType}
        summary: Add a user's shared access setting
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/settings/{settingType}"
        params = { 'userId': userId, 'settingType': settingType }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_user_setting(self, userId: Optional[Any] = None, settingType: Optional[Any] = None, shared_id: Optional[Any] = None, assistant_extension_id: Optional[Any] = None, device_id: Optional[Any] = None, intercom_extension_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteUserSetting
        method: DELETE
        path: /phone/users/{userId}/settings/{settingType}
        summary: Delete a user's shared access setting
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/settings/{settingType}"
        params = { 'userId': userId, 'settingType': settingType, 'shared_id': shared_id, 'assistant_extension_id': assistant_extension_id, 'device_id': device_id, 'intercom_extension_id': intercom_extension_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_setting(self, settingType: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateUserSetting
        method: PATCH
        path: /phone/users/{userId}/settings/{settingType}
        summary: Update a user's shared access setting
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/settings/{settingType}"
        params = { 'settingType': settingType, 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_voicemail_details_by_call_id_or_call_log_id(self, userId: Optional[Any] = None, id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getVoicemailDetailsByCallIdOrCallLogId
        method: GET
        path: /phone/users/{userId}/call_logs/{id}/voice_mail
        summary: Get user voicemail details from a call log
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/call_logs/{id}/voice_mail"
        params = { 'userId': userId, 'id': id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_user_voice_mails(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, status: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, trash: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneUserVoiceMails
        method: GET
        path: /phone/users/{userId}/voice_mails
        summary: Get user's voicemails
        """
        endpoint = f"{self._base_url}/phone/users/{userId}/voice_mails"
        params = { 'userId': userId, 'page_size': page_size, 'status': status, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'trash': trash }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def account_voice_mails(self, page_size: Optional[Any] = None, status: Optional[Any] = None, site_id: Optional[Any] = None, owner_type: Optional[Any] = None, voicemail_type: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, trashed: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: accountVoiceMails
        method: GET
        path: /phone/voice_mails
        summary: Get account voicemails
        """
        endpoint = f"{self._base_url}/phone/voice_mails"
        params = { 'page_size': page_size, 'status': status, 'site_id': site_id, 'owner_type': owner_type, 'voicemail_type': voicemail_type, 'next_page_token': next_page_token, 'from': from_, 'to': to, 'trashed': trashed }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def phone_download_voicemail_file(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: phoneDownloadVoicemailFile
        method: GET
        path: /phone/voice_mails/download/{fileId}
        summary: Download a phone voicemail
        """
        endpoint = f"{self._base_url}/phone/voice_mails/download/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_voicemail_details(self, voicemailId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getVoicemailDetails
        method: GET
        path: /phone/voice_mails/{voicemailId}
        summary: Get voicemail details
        """
        endpoint = f"{self._base_url}/phone/voice_mails/{voicemailId}"
        params = { 'voicemailId': voicemailId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_voicemail(self, voicemailId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteVoicemail
        method: DELETE
        path: /phone/voice_mails/{voicemailId}
        summary: Delete a voicemail
        """
        endpoint = f"{self._base_url}/phone/voice_mails/{voicemailId}"
        params = { 'voicemailId': voicemailId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_voicemail_read_status(self, voicemailId: Optional[Any] = None, read_status: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateVoicemailReadStatus
        method: PATCH
        path: /phone/voice_mails/{voicemailId}
        summary: Update Voicemail Read Status
        """
        endpoint = f"{self._base_url}/phone/voice_mails/{voicemailId}"
        params = { 'voicemailId': voicemailId, 'read_status': read_status }
        body = None
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zoom_rooms(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, site_id: Optional[Any] = None, calling_type: Optional[Any] = None, keyword: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listZoomRooms
        method: GET
        path: /phone/rooms
        summary: List Zoom Rooms under Zoom Phone license
        """
        endpoint = f"{self._base_url}/phone/rooms"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'site_id': site_id, 'calling_type': calling_type, 'keyword': keyword }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_zoom_room(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addZoomRoom
        method: POST
        path: /phone/rooms
        summary: Add a Zoom Room to a Zoom Phone
        """
        endpoint = f"{self._base_url}/phone/rooms"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_unassigned_zoom_rooms(self, keyword: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUnassignedZoomRooms
        method: GET
        path: /phone/rooms/unassigned
        summary: List Zoom Rooms without Zoom Phone assignment
        """
        endpoint = f"{self._base_url}/phone/rooms/unassigned"
        params = { 'keyword': keyword }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zoom_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZoomRoom
        method: GET
        path: /phone/rooms/{roomId}
        summary: Get a Zoom Room under Zoom Phone license
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}"
        params = { 'roomId': roomId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_zoom_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: RemoveZoomRoom
        method: DELETE
        path: /phone/rooms/{roomId}
        summary: Remove a Zoom Room from a ZP account
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}"
        params = { 'roomId': roomId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zoom_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZoomRoom
        method: PATCH
        path: /phone/rooms/{roomId}
        summary: Update a Zoom Room under Zoom Phone license
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_calling_plan_to_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignCallingPlanToRoom
        method: POST
        path: /phone/rooms/{roomId}/calling_plans
        summary: Assign calling plans to a Zoom Room
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}/calling_plans"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_calling_plan_from_room(self, roomId: Optional[Any] = None, type_: Optional[Any] = None, billing_account_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignCallingPlanFromRoom
        method: DELETE
        path: /phone/rooms/{roomId}/calling_plans/{type}
        summary: Remove a calling plan from a Zoom Room
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}/calling_plans/{type_}"
        params = { 'roomId': roomId, 'type': type_, 'billing_account_id': billing_account_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_phone_number_to_zoom_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assignPhoneNumberToZoomRoom
        method: POST
        path: /phone/rooms/{roomId}/phone_numbers
        summary: Assign phone numbers to a Zoom Room
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}/phone_numbers"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_phone_number_from_zoom_room(self, roomId: Optional[Any] = None, phoneNumberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UnassignPhoneNumberFromZoomRoom
        method: DELETE
        path: /phone/rooms/{roomId}/phone_numbers/{phoneNumberId}
        summary: Remove a phone number from a Zoom Room
        """
        endpoint = f"{self._base_url}/phone/rooms/{roomId}/phone_numbers/{phoneNumberId}"
        params = { 'roomId': roomId, 'phoneNumberId': phoneNumberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_meeting_participants_qos_summary(self, meetingId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardMeetingParticipantsQOSSummary
        method: GET
        path: /metrics/meetings/{meetingId}/participants/qos_summary
        summary: List meeting participants QoS Summary
        """
        endpoint = f"{self._base_url}/metrics/meetings/{meetingId}/participants/qos_summary"
        params = { 'meetingId': meetingId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def dashboard_webinar_participants_qos_summary(self, webinarId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: dashboardWebinarParticipantsQOSSummary
        method: GET
        path: /metrics/webinars/{webinarId}/participants/qos_summary
        summary: List webinar participants QoS Summary
        """
        endpoint = f"{self._base_url}/metrics/webinars/{webinarId}/participants/qos_summary"
        params = { 'webinarId': webinarId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def session_users_qos_summary(self, sessionId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: sessionUsersQOSSummary
        method: GET
        path: /videosdk/sessions/{sessionId}/users/qos_summary
        summary: List session users QoS Summary
        """
        endpoint = f"{self._base_url}/videosdk/sessions/{sessionId}/users/qos_summary"
        params = { 'sessionId': sessionId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def config_zoom_room_controller_apps(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ConfigZoomRoomControllerApps
        method: POST
        path: /rooms/controller/apps/config
        summary: Config Zoom Room Controller Apps
        """
        endpoint = f"{self._base_url}/rooms/controller/apps/config"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def invitation_list(self, invite_location_id: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, data_scope: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: invitationList
        method: GET
        path: /visitor/invitation
        summary: Get a list of visitors by location 
        """
        endpoint = f"{self._base_url}/visitor/invitation"
        params = { 'invite_location_id': invite_location_id, 'from': from_, 'to': to, 'data_scope': data_scope, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_invitation(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createInvitation
        method: POST
        path: /visitor/invitation
        summary: Send an invitation
        """
        endpoint = f"{self._base_url}/visitor/invitation"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_invitation(self, invitationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getInvitation
        method: GET
        path: /visitor/invitation/{invitationId}
        summary: Invitation details by invitationID
        """
        endpoint = f"{self._base_url}/visitor/invitation/{invitationId}"
        params = { 'invitationId': invitationId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_invitation(self, invitationId: Optional[Any] = None, cancel: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteInvitation
        method: DELETE
        path: /visitor/invitation/{invitationId}
        summary: Delete an Invitation 
        """
        endpoint = f"{self._base_url}/visitor/invitation/{invitationId}"
        params = { 'invitationId': invitationId, 'cancel': cancel }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_invitation(self, invitationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateInvitation
        method: PATCH
        path: /visitor/invitation/{invitationId}
        summary: Update an invitation
        """
        endpoint = f"{self._base_url}/visitor/invitation/{invitationId}"
        params = { 'invitationId': invitationId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def checkin_visitor(self, invitationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: checkinVisitor
        method: POST
        path: /visitor/invitation/{invitationId}/checkin
        summary: Check in a visitor
        """
        endpoint = f"{self._base_url}/visitor/invitation/{invitationId}/checkin"
        params = { 'invitationId': invitationId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_workspaces(self, location_id: Optional[Any] = None, workspace_name: Optional[Any] = None, workspace_type: Optional[Any] = None, reserve_user: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listWorkspaces
        method: GET
        path: /workspaces
        summary: List workspaces
        """
        endpoint = f"{self._base_url}/workspaces"
        params = { 'location_id': location_id, 'workspace_name': workspace_name, 'workspace_type': workspace_type, 'reserve_user': reserve_user, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_workspace(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createWorkspace
        method: POST
        path: /workspaces
        summary: Create a workspace
        """
        endpoint = f"{self._base_url}/workspaces"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getaworkspaceadditionalenhancements(self, from_: Optional[Any] = None, to: Optional[Any] = None, reserve_user: Optional[Any] = None, location_id: Optional[Any] = None, workspace_type: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getaworkspaceadditionalenhancements
        method: GET
        path: /workspaces/additional_informations
        summary: List workspace additional information with time range
        """
        endpoint = f"{self._base_url}/workspaces/additional_informations"
        params = { 'from': from_, 'to': to, 'reserve_user': reserve_user, 'location_id': location_id, 'workspace_type': workspace_type, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def getallworkspaceassets(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getallworkspaceassets
        method: GET
        path: /workspaces/assets
        summary: Get all workspace assets
        """
        endpoint = f"{self._base_url}/workspaces/assets"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def adda_workspaceasset(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddaWorkspaceasset
        method: POST
        path: /workspaces/assets
        summary: Create a workspace asset
        """
        endpoint = f"{self._base_url}/workspaces/assets"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_workspace_asset(self, assetId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_workspace_asset
        method: GET
        path: /workspaces/assets/{assetId}
        summary: Get a workspace asset
        """
        endpoint = f"{self._base_url}/workspaces/assets/{assetId}"
        params = { 'assetId': assetId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deletea_workspaceasset(self, assetId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteaWorkspaceasset
        method: DELETE
        path: /workspaces/assets/{assetId}
        summary: Delete a workspace asset
        """
        endpoint = f"{self._base_url}/workspaces/assets/{assetId}"
        params = { 'assetId': assetId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patch_workspaceasset(self, assetId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: PatchWorkspaceasset
        method: PATCH
        path: /workspaces/assets/{assetId}
        summary: Edit a workspace asset
        """
        endpoint = f"{self._base_url}/workspaces/assets/{assetId}"
        params = { 'assetId': assetId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def reservation_event(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reservationEvent
        method: POST
        path: /workspaces/events
        summary: Check in/out of a reservation
        """
        endpoint = f"{self._base_url}/workspaces/events"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_or_update_a_workspace_floor_map(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddOrUpdateAWorkspaceFloorMap
        method: POST
        path: /workspaces/floormap/files
        summary: Add or Update a Workspace floor map
        """
        endpoint = f"{self._base_url}/workspaces/floormap/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_workspace_settings(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateWorkspaceSettings
        method: PATCH
        path: /workspaces/settings
        summary: Update workspace settings
        """
        endpoint = f"{self._base_url}/workspaces/settings"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_hot_desk_usage(self, location_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getHotDeskUsage
        method: GET
        path: /workspaces/usage
        summary: Get a location's hot desk usage
        """
        endpoint = f"{self._base_url}/workspaces/usage"
        params = { 'location_id': location_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_workspace_calendar_free_busy_event(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetWorkspaceCalendarFree/BusyEvent
        method: GET
        path: /workspaces/users/{userId}/calendar/settings
        summary: Get  Workspace Calendar Free/Busy Event
        """
        endpoint = f"{self._base_url}/workspaces/users/{userId}/calendar/settings"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def set_calendar_free_busy_event(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: SetCalendarFree/BusyEvent
        method: POST
        path: /workspaces/users/{userId}/calendar/settings
        summary: Set Workspace Calendar Free/Busy Event
        """
        endpoint = f"{self._base_url}/workspaces/users/{userId}/calendar/settings"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_list_reservations(self, userId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userListReservations
        method: GET
        path: /workspaces/users/{userId}/reservations
        summary: Get a user's workspace's reservations
        """
        endpoint = f"{self._base_url}/workspaces/users/{userId}/reservations"
        params = { 'userId': userId, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_workspace_floor_map(self, locationId: Optional[Any] = None, remove_child: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteWorkspaceFloorMap
        method: DELETE
        path: /workspaces/{locationId}/background
        summary: Delete Workspace floor map
        """
        endpoint = f"{self._base_url}/workspaces/{locationId}/background"
        params = { 'locationId': locationId, 'remove_child': remove_child }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_workspace(self, workspaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getWorkspace
        method: GET
        path: /workspaces/{workspaceId}
        summary: Get a workspace
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}"
        params = { 'workspaceId': workspaceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_workspace(self, workspaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteWorkspace
        method: DELETE
        path: /workspaces/{workspaceId}
        summary: Delete a workspace
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}"
        params = { 'workspaceId': workspaceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_workspace(self, workspaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateWorkspace
        method: PATCH
        path: /workspaces/{workspaceId}
        summary: Update a workspace
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}"
        params = { 'workspaceId': workspaceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getadeskassignment(self, workspaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getadeskassignment
        method: GET
        path: /workspaces/{workspaceId}/assignment
        summary: Get a desk assignment
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/assignment"
        params = { 'workspaceId': workspaceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def set_a_desk_assignment(self, workspaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: setADeskAssignment
        method: PUT
        path: /workspaces/{workspaceId}/assignment
        summary: Set a desk assignment
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/assignment"
        params = { 'workspaceId': workspaceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteadeskassignment(self, workspaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteadeskassignment
        method: DELETE
        path: /workspaces/{workspaceId}/assignment
        summary: Delete a desk assignment
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/assignment"
        params = { 'workspaceId': workspaceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_workspace_qr_code(self, workspaceId: Optional[Any] = None, type_: Optional[Any] = None, ttl: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getWorkspaceQRCode
        method: GET
        path: /workspaces/{workspaceId}/qr_code
        summary: Get a workspace QR code
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/qr_code"
        params = { 'workspaceId': workspaceId, 'type': type_, 'ttl': ttl }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_reservations(self, workspaceId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listReservations
        method: GET
        path: /workspaces/{workspaceId}/reservations
        summary: Get a workspace's reservations
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/reservations"
        params = { 'workspaceId': workspaceId, 'from': from_, 'to': to, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_reservation(self, workspaceId: Optional[Any] = None, check_in: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createReservation
        method: POST
        path: /workspaces/{workspaceId}/reservations
        summary: Create a reservation
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/reservations"
        params = { 'workspaceId': workspaceId, 'check_in': check_in }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_getaworkspacereservationbyreservation_id(self, workspaceId: Optional[Any] = None, reservationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GETGetaworkspacereservationbyreservationID
        method: GET
        path: /workspaces/{workspaceId}/reservations/{reservationId}
        summary: Get a workspace reservation by reservationId
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/reservations/{reservationId}"
        params = { 'workspaceId': workspaceId, 'reservationId': reservationId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_reservation(self, workspaceId: Optional[Any] = None, reservationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteReservation
        method: DELETE
        path: /workspaces/{workspaceId}/reservations/{reservationId}
        summary: Delete a reservation
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/reservations/{reservationId}"
        params = { 'workspaceId': workspaceId, 'reservationId': reservationId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_reservation(self, workspaceId: Optional[Any] = None, reservationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateReservation
        method: PATCH
        path: /workspaces/{workspaceId}/reservations/{reservationId}
        summary: Update a reservation
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/reservations/{reservationId}"
        params = { 'workspaceId': workspaceId, 'reservationId': reservationId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def listworkspacereservationquestionnaires(self, workspaceId: Optional[Any] = None, reservationId: Optional[Any] = None, subject: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listworkspacereservationquestionnaires
        method: GET
        path: /workspaces/{workspaceId}/reservations/{reservationId}/questionnaires
        summary: List workspace reservation questionnaires
        """
        endpoint = f"{self._base_url}/workspaces/{workspaceId}/reservations/{reservationId}/questionnaires"
        params = { 'workspaceId': workspaceId, 'reservationId': reservationId, 'subject': subject }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zoom_rooms_1(self, status: Optional[Any] = None, tag_ids: Optional[Any] = None, type_: Optional[Any] = None, unassigned_rooms: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, location_id: Optional[Any] = None, query_name: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listZoomRooms
        method: GET
        path: /rooms
        summary: List Zoom Rooms
        """
        endpoint = f"{self._base_url}/rooms"
        params = { 'status': status, 'tag_ids': tag_ids, 'type': type_, 'unassigned_rooms': unassigned_rooms, 'page_size': page_size, 'next_page_token': next_page_token, 'location_id': location_id, 'query_name': query_name }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_a_room(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addARoom
        method: POST
        path: /rooms
        summary: Add a Zoom Room
        """
        endpoint = f"{self._base_url}/rooms"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_digital_signage_content(self, type_: Optional[Any] = None, folder_id: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listDigitalSignageContent
        method: GET
        path: /rooms/digital_signage
        summary: List digital signage contents
        """
        endpoint = f"{self._base_url}/rooms/digital_signage"
        params = { 'type': type_, 'folder_id': folder_id, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def manage_e911signage(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: manageE911signage
        method: PATCH
        path: /rooms/events
        summary: Update E911 digital signage
        """
        endpoint = f"{self._base_url}/rooms/events"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def zoom_rooms_controls(self, id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ZoomRoomsControls
        method: PATCH
        path: /rooms/{id}/events
        summary: Use Zoom Room controls
        """
        endpoint = f"{self._base_url}/rooms/{id}/events"
        params = { 'id': id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_settings(self, id: Optional[Any] = None, setting_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRSettings
        method: GET
        path: /rooms/{id}/settings
        summary: Get Zoom Room settings
        """
        endpoint = f"{self._base_url}/rooms/{id}/settings"
        params = { 'id': id, 'setting_type': setting_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zr_settings(self, id: Optional[Any] = None, setting_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZRSettings
        method: PATCH
        path: /rooms/{id}/settings
        summary: Update Zoom Room settings
        """
        endpoint = f"{self._base_url}/rooms/{id}/settings"
        params = { 'id': id, 'setting_type': setting_type }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_profile(self, roomId: Optional[Any] = None, regenerate_activation_code: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRProfile
        method: GET
        path: /rooms/{roomId}
        summary: Get Zoom Room profile
        """
        endpoint = f"{self._base_url}/rooms/{roomId}"
        params = { 'roomId': roomId, 'regenerate_activation_code': regenerate_activation_code }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_zoom_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAZoomRoom
        method: DELETE
        path: /rooms/{roomId}
        summary: Delete a Zoom Room
        """
        endpoint = f"{self._base_url}/rooms/{roomId}"
        params = { 'roomId': roomId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_room_profile(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateRoomProfile
        method: PATCH
        path: /rooms/{roomId}
        summary: Update a Zoom Room profile
        """
        endpoint = f"{self._base_url}/rooms/{roomId}"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_room_profiles(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getRoomProfiles
        method: GET
        path: /rooms/{roomId}/device_profiles
        summary: List device profiles
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/device_profiles"
        params = { 'roomId': roomId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_room_device_profile(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createRoomDeviceProfile
        method: POST
        path: /rooms/{roomId}/device_profiles
        summary: Create a device profile
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/device_profiles"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_room_devices(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getRoomDevices
        method: GET
        path: /rooms/{roomId}/device_profiles/devices
        summary: Get device information
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/device_profiles/devices"
        params = { 'roomId': roomId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_room_profile(self, roomId: Optional[Any] = None, deviceProfileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getRoomProfile
        method: GET
        path: /rooms/{roomId}/device_profiles/{deviceProfileId}
        summary: Get a device profile
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/device_profiles/{deviceProfileId}"
        params = { 'roomId': roomId, 'deviceProfileId': deviceProfileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_room_profile(self, roomId: Optional[Any] = None, deviceProfileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteRoomProfile
        method: DELETE
        path: /rooms/{roomId}/device_profiles/{deviceProfileId}
        summary: Delete a device profile
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/device_profiles/{deviceProfileId}"
        params = { 'roomId': roomId, 'deviceProfileId': deviceProfileId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_device_profile(self, roomId: Optional[Any] = None, deviceProfileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateDeviceProfile
        method: PATCH
        path: /rooms/{roomId}/device_profiles/{deviceProfileId}
        summary: Update a device profile
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/device_profiles/{deviceProfileId}"
        params = { 'roomId': roomId, 'deviceProfileId': deviceProfileId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zr_devices(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listZRDevices
        method: GET
        path: /rooms/{roomId}/devices
        summary: List Zoom Room devices
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/devices"
        params = { 'roomId': roomId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def change_zr_location(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: changeZRLocation
        method: PUT
        path: /rooms/{roomId}/location
        summary: Change a Zoom Room's location
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/location"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_sensor_data(self, roomId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, device_id: Optional[Any] = None, sensor_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRSensorData
        method: GET
        path: /rooms/{roomId}/sensor_data
        summary: Get Zoom Room sensor data
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/sensor_data"
        params = { 'roomId': roomId, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'device_id': device_id, 'sensor_type': sensor_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_webzrc_url(self, roomId: Optional[Any] = None, pre_authenticated_link: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getWebzrcUrl
        method: GET
        path: /rooms/{roomId}/virtual_controller
        summary: Get Zoom Rooms virtual controller URL
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/virtual_controller"
        params = { 'roomId': roomId, 'pre_authenticated_link': pre_authenticated_link }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_account_profile(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRAccountProfile
        method: GET
        path: /rooms/account_profile
        summary: Get Zoom Room account profile
        """
        endpoint = f"{self._base_url}/rooms/account_profile"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zr_acc_profile(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZRAccProfile
        method: PATCH
        path: /rooms/account_profile
        summary: Update Zoom Room account profile
        """
        endpoint = f"{self._base_url}/rooms/account_profile"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_account_settings(self, setting_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRAccountSettings
        method: GET
        path: /rooms/account_settings
        summary: Get Zoom Room account settings
        """
        endpoint = f"{self._base_url}/rooms/account_settings"
        params = { 'setting_type': setting_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zoom_room_acc_settings(self, setting_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZoomRoomAccSettings
        method: PATCH
        path: /rooms/account_settings
        summary: Update Zoom Room account settings
        """
        endpoint = f"{self._base_url}/rooms/account_settings"
        params = { 'setting_type': setting_type }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_calendar_services(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCalendarServices
        method: GET
        path: /rooms/calendar/services
        summary: List calendar services
        """
        endpoint = f"{self._base_url}/rooms/calendar/services"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_calendar_service(self, serviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteACalendarService
        method: DELETE
        path: /rooms/calendar/services/{serviceId}
        summary: Delete a calendar service
        """
        endpoint = f"{self._base_url}/rooms/calendar/services/{serviceId}"
        params = { 'serviceId': serviceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_calendar_resources_by_service_id(self, serviceId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCalendarResourcesByServiceId
        method: GET
        path: /rooms/calendar/services/{serviceId}/resources
        summary: List calendar resources by calendar service
        """
        endpoint = f"{self._base_url}/rooms/calendar/services/{serviceId}/resources"
        params = { 'serviceId': serviceId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_a_calendar_resource_to_calendar_service(self, serviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addACalendarResourceToCalendarService
        method: POST
        path: /rooms/calendar/services/{serviceId}/resources
        summary: Add a calendar resource to a calendar service
        """
        endpoint = f"{self._base_url}/rooms/calendar/services/{serviceId}/resources"
        params = { 'serviceId': serviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_calendar_resource_by_id(self, serviceId: Optional[Any] = None, resourceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCalendarResourceById
        method: GET
        path: /rooms/calendar/services/{serviceId}/resources/{resourceId}
        summary: Get a calendar resource by ID
        """
        endpoint = f"{self._base_url}/rooms/calendar/services/{serviceId}/resources/{resourceId}"
        params = { 'serviceId': serviceId, 'resourceId': resourceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_calendar_resource(self, serviceId: Optional[Any] = None, resourceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteACalendarResource
        method: DELETE
        path: /rooms/calendar/services/{serviceId}/resources/{resourceId}
        summary: Delete a calendar resource
        """
        endpoint = f"{self._base_url}/rooms/calendar/services/{serviceId}/resources/{resourceId}"
        params = { 'serviceId': serviceId, 'resourceId': resourceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def sync_a_calendar_service(self, serviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: syncACalendarService
        method: PUT
        path: /rooms/calendar/services/{serviceId}/sync
        summary: Start calendar service sync process
        """
        endpoint = f"{self._base_url}/rooms/calendar/services/{serviceId}/sync"
        params = { 'serviceId': serviceId }
        body = None
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zoom_roomsbackgroundimagelibrarycontents(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, folder_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListZoomRoomsbackgroundimagelibrarycontents
        method: GET
        path: /rooms/content/background/contents
        summary: List Zoom Rooms background image library contents
        """
        endpoint = f"{self._base_url}/rooms/content/background/contents"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'folder_id': folder_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zoom_rooms_background_image_library_content(self, contentId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetZoomRoomsBackgroundImageLibraryContent
        method: GET
        path: /rooms/content/background/contents/{contentId}
        summary: Get Zoom Rooms background image library content
        """
        endpoint = f"{self._base_url}/rooms/content/background/contents/{contentId}"
        params = { 'contentId': contentId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_zoom_rooms_background_image_library_content(self, contentId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteZoomRoomsBackgroundImageLibraryContent
        method: DELETE
        path: /rooms/content/background/contents/{contentId}
        summary: Delete Zoom Rooms Background Image Library Content
        """
        endpoint = f"{self._base_url}/rooms/content/background/contents/{contentId}"
        params = { 'contentId': contentId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_default_zoom_rooms_background_image_librarycontents(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListDefaultZoomRoomsBackgroundImageLibrarycontents
        method: GET
        path: /rooms/content/background/defaults
        summary: List default Zoom Rooms background image library contents
        """
        endpoint = f"{self._base_url}/rooms/content/background/defaults"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zoom_rooms_background_library_folders(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListZoomRoomsBackgroundLibraryFolders
        method: GET
        path: /rooms/content/background/folders
        summary: List Zoom Rooms Background Image Library Folders
        """
        endpoint = f"{self._base_url}/rooms/content/background/folders"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_zoom_rooms_background_library_folder(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddZoomRoomsBackgroundLibraryFolder
        method: POST
        path: /rooms/content/background/folders
        summary: Add Zoom Rooms Background Image Library Folder
        """
        endpoint = f"{self._base_url}/rooms/content/background/folders"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zoom_rooms_background_library_folder(self, folderId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetZoomRoomsBackgroundLibraryFolder
        method: GET
        path: /rooms/content/background/folders/{folderId}
        summary: Get Zoom Rooms Background Image Library Folder
        """
        endpoint = f"{self._base_url}/rooms/content/background/folders/{folderId}"
        params = { 'folderId': folderId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_zoom_rooms_background_library_folder(self, folderId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteZoomRoomsBackgroundLibraryFolder
        method: DELETE
        path: /rooms/content/background/folders/{folderId}
        summary: Delete Zoom Rooms Background Image Library Folder
        """
        endpoint = f"{self._base_url}/rooms/content/background/folders/{folderId}"
        params = { 'folderId': folderId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updatea_zoom_rooms_background_library_folder_name(self, folderId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateaZoomRoomsBackgroundLibraryFolderName
        method: PATCH
        path: /rooms/content/background/folders/{folderId}
        summary: Update Zoom Rooms Background Image Library Folder
        """
        endpoint = f"{self._base_url}/rooms/content/background/folders/{folderId}"
        params = { 'folderId': folderId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_listdigitalsignagecontentitems(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GETListdigitalsignagecontentitems
        method: GET
        path: /rooms/content/digital_signage/contents
        summary: List Digital Signage content items
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/contents"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def addadigitalsignage_url(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddadigitalsignageURL
        method: POST
        path: /rooms/content/digital_signage/contents
        summary: Add a digital signage URL
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/contents"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getdigitalsignagecontentitem(self, contentId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getdigitalsignagecontentitem
        method: GET
        path: /rooms/content/digital_signage/contents/{contentId}
        summary: Get Digital Signage content item
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/contents/{contentId}"
        params = { 'contentId': contentId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteadigitalsignagecontentitem(self, contentId: Optional[Any] = None, remove_from_library_only: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteadigitalsignagecontentitem
        method: DELETE
        path: /rooms/content/digital_signage/contents/{contentId}
        summary: Delete a Digital Signage content item
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/contents/{contentId}"
        params = { 'contentId': contentId, 'remove_from_library_only': remove_from_library_only }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updateadigitalsignagecontentitemattributes(self, contentId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Updateadigitalsignagecontentitemattributes
        method: PATCH
        path: /rooms/content/digital_signage/contents/{contentId}
        summary: Update a Digital Signage content item attributes
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/contents/{contentId}"
        params = { 'contentId': contentId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def addadigitalsignagecontentfolder(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Addadigitalsignagecontentfolder
        method: POST
        path: /rooms/content/digital_signage/folders
        summary: Add a digital signage content folder
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/folders"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getdigitalsignagecontentfolderdetails(self, folderId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getdigitalsignagecontentfolderdetails
        method: GET
        path: /rooms/content/digital_signage/folders/{folderId}
        summary: Get Digital Signage content folder
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/folders/{folderId}"
        params = { 'folderId': folderId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteadigitalsignagecontentfolder(self, folderId: Optional[Any] = None, remove_from_library_only: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteadigitalsignagecontentfolder
        method: DELETE
        path: /rooms/content/digital_signage/folders/{folderId}
        summary: Delete a Digital Signage content folder
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/folders/{folderId}"
        params = { 'folderId': folderId, 'remove_from_library_only': remove_from_library_only }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updateadigitalsignagecontentfolder(self, folderId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Updateadigitalsignagecontentfolder
        method: PATCH
        path: /rooms/content/digital_signage/folders/{folderId}
        summary: Update a digital signage content folder
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/folders/{folderId}"
        params = { 'folderId': folderId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_digital_signagelibraryplaylists(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListDigitalSignagelibraryplaylists
        method: GET
        path: /rooms/content/digital_signage/playlists
        summary: List Digital Signage library playlists
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def adda_digital_signagelibraryplaylist(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddaDigitalSignagelibraryplaylist
        method: POST
        path: /rooms/content/digital_signage/playlists
        summary: Add a Digital Signage library playlist
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_digital_signagelibraryplaylist(self, playlistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetDigitalSignagelibraryplaylist
        method: GET
        path: /rooms/content/digital_signage/playlists/{playlistId}
        summary: Get Digital Signage library playlist
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}"
        params = { 'playlistId': playlistId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_digital_signagelibraryplaylist(self, playlistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteDigitalSignagelibraryplaylist
        method: DELETE
        path: /rooms/content/digital_signage/playlists/{playlistId}
        summary: Delete Digital Signage library playlist
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}"
        params = { 'playlistId': playlistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updatea_digital_signagelibraryplaylist(self, playlistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateaDigitalSignagelibraryplaylist
        method: PATCH
        path: /rooms/content/digital_signage/playlists/{playlistId}
        summary: Update a Digital Signage library playlist
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}"
        params = { 'playlistId': playlistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_digital_signagelibraryplaylistcontentitems(self, playlistId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetDigitalSignagelibraryplaylistcontentitems
        method: GET
        path: /rooms/content/digital_signage/playlists/{playlistId}/contents
        summary: Get Digital Signage library playlist content items
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}/contents"
        params = { 'playlistId': playlistId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_digital_signagelibraryplaylistcontentitems(self, playlistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateDigitalSignagelibraryplaylistcontentitems
        method: PUT
        path: /rooms/content/digital_signage/playlists/{playlistId}/contents
        summary: Update Digital Signage library playlist content items
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}/contents"
        params = { 'playlistId': playlistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_digital_signagelibraryplaylistpublishedrooms(self, playlistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListDigitalSignagelibraryplaylistpublishedrooms
        method: GET
        path: /rooms/content/digital_signage/playlists/{playlistId}/rooms
        summary: List Digital Signage library playlist published rooms
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}/rooms"
        params = { 'playlistId': playlistId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_digital_signagelibraryplaylistpublishedrooms(self, playlistId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateDigitalSignagelibraryplaylistpublishedrooms
        method: PUT
        path: /rooms/content/digital_signage/playlists/{playlistId}/rooms
        summary: Update Digital Signage library playlist published rooms
        """
        endpoint = f"{self._base_url}/rooms/content/digital_signage/playlists/{playlistId}/rooms"
        params = { 'playlistId': playlistId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zoom_rooms_background_image_library_content(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateZoomRoomsBackgroundImageLibraryContent
        method: PUT
        path: /zrbackground/files
        summary: Update Zoom Rooms background image library content
        """
        endpoint = f"{self._base_url}/zrbackground/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_zoom_rooms_background_image_library_content(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddZoomRoomsBackgroundImageLibraryContent
        method: POST
        path: /zrbackground/files
        summary: Add Zoom Rooms background image library content
        """
        endpoint = f"{self._base_url}/zrbackground/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def updateadigitalsignageimageorvideofile(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Updateadigitalsignageimageorvideofile
        method: PUT
        path: /zrdigitalsignage/files
        summary: Update a Digital Signage image or video file
        """
        endpoint = f"{self._base_url}/zrdigitalsignage/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def adddigitalsignageimageorvideo(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Adddigitalsignageimageorvideo
        method: POST
        path: /zrdigitalsignage/files
        summary: Add a digital signage image or video 
        """
        endpoint = f"{self._base_url}/zrdigitalsignage/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_device_1(self, roomId: Optional[Any] = None, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteDevice
        method: DELETE
        path: /rooms/{roomId}/devices/{deviceId}
        summary: Delete a Zoom Room user device
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/devices/{deviceId}"
        params = { 'roomId': roomId, 'deviceId': deviceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def change_zoom_rooms_app_version(self, roomId: Optional[Any] = None, deviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: changeZoomRoomsAppVersion
        method: PUT
        path: /rooms/{roomId}/devices/{deviceId}/app_version
        summary: Change Zoom Rooms app version
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/devices/{deviceId}/app_version"
        params = { 'roomId': roomId, 'deviceId': deviceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zr_locations(self, parent_location_id: Optional[Any] = None, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listZRLocations
        method: GET
        path: /rooms/locations
        summary: List Zoom Room locations
        """
        endpoint = f"{self._base_url}/rooms/locations"
        params = { 'parent_location_id': parent_location_id, 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_azr_location(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addAZRLocation
        method: POST
        path: /rooms/locations
        summary: Add a location
        """
        endpoint = f"{self._base_url}/rooms/locations"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_location_structure(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRLocationStructure
        method: GET
        path: /rooms/locations/structure
        summary: Get Zoom Room location structure
        """
        endpoint = f"{self._base_url}/rooms/locations/structure"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zoom_rooms_location_structure(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZoomRoomsLocationStructure
        method: PUT
        path: /rooms/locations/structure
        summary: Update Zoom Rooms location structure
        """
        endpoint = f"{self._base_url}/rooms/locations/structure"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_location_profile(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRLocationProfile
        method: GET
        path: /rooms/locations/{locationId}
        summary: Get Zoom Room location profile
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}"
        params = { 'locationId': locationId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_azr_location(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAZRLocation
        method: DELETE
        path: /rooms/locations/{locationId}
        summary: Delete a location
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}"
        params = { 'locationId': locationId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zr_location_profile(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZRLocationProfile
        method: PATCH
        path: /rooms/locations/{locationId}
        summary: Update Zoom Room location profile
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}"
        params = { 'locationId': locationId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def change_parent_location(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: changeParentLocation
        method: PUT
        path: /rooms/locations/{locationId}/location
        summary: Change the assigned parent location
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}/location"
        params = { 'locationId': locationId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_zr_location_settings(self, locationId: Optional[Any] = None, setting_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getZRLocationSettings
        method: GET
        path: /rooms/locations/{locationId}/settings
        summary: Get location settings
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}/settings"
        params = { 'locationId': locationId, 'setting_type': setting_type }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_zr_location_settings(self, locationId: Optional[Any] = None, setting_type: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateZRLocationSettings
        method: PATCH
        path: /rooms/locations/{locationId}/settings
        summary: Update location settings
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}/settings"
        params = { 'locationId': locationId, 'setting_type': setting_type }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_tags_to_zoom_rooms_by_location_id(self, locationId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AssignTagsToZoomRoomsByLocationID
        method: PATCH
        path: /rooms/locations/{locationId}/tags
        summary: Assign Tags to Zoom Rooms By Location ID
        """
        endpoint = f"{self._base_url}/rooms/locations/{locationId}/tags"
        params = { 'locationId': locationId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_zoom_room_tags(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listZoomRoomTags
        method: GET
        path: /rooms/tags
        summary: List all Zoom Room Tags
        """
        endpoint = f"{self._base_url}/rooms/tags"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_zoom_room_tag(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createZoomRoomTag
        method: POST
        path: /rooms/tags
        summary: Create a new Zoom Rooms Tag
        """
        endpoint = f"{self._base_url}/rooms/tags"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_zoom_room_tag(self, tagId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteZoomRoomTag
        method: DELETE
        path: /rooms/tags/{tagId}
        summary: Delete Tag
        """
        endpoint = f"{self._base_url}/rooms/tags/{tagId}"
        params = { 'tagId': tagId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def edit_zoom_room_tag(self, tagId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: editZoomRoomTag
        method: PATCH
        path: /rooms/tags/{tagId}
        summary: Edit Tag
        """
        endpoint = f"{self._base_url}/rooms/tags/{tagId}"
        params = { 'tagId': tagId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def unassign_zoom_room_tag(self, roomId: Optional[Any] = None, tag_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: unassignZoomRoomTag
        method: DELETE
        path: /rooms/{roomId}/tags
        summary: Un-assign Tags from a Zoom Room
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/tags"
        params = { 'roomId': roomId, 'tag_ids': tag_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def assign_tags_to_a_zoom_room(self, roomId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AssignTagsToAZoomRoom
        method: PATCH
        path: /rooms/{roomId}/tags
        summary: Assign Tags to a Zoom Room
        """
        endpoint = f"{self._base_url}/rooms/{roomId}/tags"
        params = { 'roomId': roomId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getroutingresponse(self, formId: Optional[Any] = None, responseId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getroutingresponse
        method: GET
        path: /scheduler/routing/forms/{formId}/response/{responseId}
        summary: get routing response
        """
        endpoint = f"{self._base_url}/scheduler/routing/forms/{formId}/response/{responseId}"
        params = { 'formId': formId, 'responseId': responseId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_analytics(self, to: Optional[Any] = None, from_: Optional[Any] = None, time_zone: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: report_analytics
        method: GET
        path: /scheduler/analytics
        summary: Report analytics
        """
        endpoint = f"{self._base_url}/scheduler/analytics"
        params = { 'to': to, 'from': from_, 'time_zone': time_zone, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_availability(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_availability
        method: GET
        path: /scheduler/availability
        summary: List availability
        """
        endpoint = f"{self._base_url}/scheduler/availability"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def insert_availability(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: insert_availability
        method: POST
        path: /scheduler/availability
        summary: Insert availability
        """
        endpoint = f"{self._base_url}/scheduler/availability"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_availability(self, availabilityId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_availability
        method: GET
        path: /scheduler/availability/{availabilityId}
        summary: Get availability 
        """
        endpoint = f"{self._base_url}/scheduler/availability/{availabilityId}"
        params = { 'availabilityId': availabilityId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_availability(self, availabilityId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_availability
        method: DELETE
        path: /scheduler/availability/{availabilityId}
        summary: Delete availability
        """
        endpoint = f"{self._base_url}/scheduler/availability/{availabilityId}"
        params = { 'availabilityId': availabilityId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patch_availability(self, availabilityId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: patch_availability
        method: PATCH
        path: /scheduler/availability/{availabilityId}
        summary: Patch availability
        """
        endpoint = f"{self._base_url}/scheduler/availability/{availabilityId}"
        params = { 'availabilityId': availabilityId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_scheduled_events(self, to: Optional[Any] = None, from_: Optional[Any] = None, page_size: Optional[Any] = None, order_by: Optional[Any] = None, time_zone: Optional[Any] = None, next_page_token: Optional[Any] = None, show_deleted: Optional[Any] = None, event_type: Optional[Any] = None, user_id: Optional[Any] = None, search: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_scheduled_events
        method: GET
        path: /scheduler/events
        summary: List scheduled events
        """
        endpoint = f"{self._base_url}/scheduler/events"
        params = { 'to': to, 'from': from_, 'page_size': page_size, 'order_by': order_by, 'time_zone': time_zone, 'next_page_token': next_page_token, 'show_deleted': show_deleted, 'event_type': event_type, 'user_id': user_id, 'search': search }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_scheduled_events(self, eventId: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_scheduled_events
        method: GET
        path: /scheduler/events/{eventId}
        summary: Get scheduled events 
        """
        endpoint = f"{self._base_url}/scheduler/events/{eventId}"
        params = { 'eventId': eventId, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_scheduled_events(self, eventId: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_scheduled_events
        method: DELETE
        path: /scheduler/events/{eventId}
        summary: Delete scheduled events 
        """
        endpoint = f"{self._base_url}/scheduler/events/{eventId}"
        params = { 'eventId': eventId, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patch_scheduled_events(self, eventId: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: patch_scheduled_events
        method: PATCH
        path: /scheduler/events/{eventId}
        summary: Patch scheduled events
        """
        endpoint = f"{self._base_url}/scheduler/events/{eventId}"
        params = { 'eventId': eventId, 'user_id': user_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_scheduled_event_attendee(self, eventId: Optional[Any] = None, attendeeId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_scheduled_event_attendee
        method: GET
        path: /scheduler/events/{eventId}/attendees/{attendeeId}
        summary: Get scheduled event attendee
        """
        endpoint = f"{self._base_url}/scheduler/events/{eventId}/attendees/{attendeeId}"
        params = { 'eventId': eventId, 'attendeeId': attendeeId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_schedules(self, to: Optional[Any] = None, from_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, show_deleted: Optional[Any] = None, time_zone: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: list_schedules
        method: GET
        path: /scheduler/schedules
        summary: List schedules
        """
        endpoint = f"{self._base_url}/scheduler/schedules"
        params = { 'to': to, 'from': from_, 'page_size': page_size, 'next_page_token': next_page_token, 'show_deleted': show_deleted, 'time_zone': time_zone, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def insert_schedule(self, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: insert_schedule
        method: POST
        path: /scheduler/schedules
        summary: Insert schedules
        """
        endpoint = f"{self._base_url}/scheduler/schedules"
        params = { 'user_id': user_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_schedule(self, scheduleId: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_schedule
        method: GET
        path: /scheduler/schedules/{scheduleId}
        summary: Get schedules
        """
        endpoint = f"{self._base_url}/scheduler/schedules/{scheduleId}"
        params = { 'scheduleId': scheduleId, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_schedules(self, scheduleId: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delete_schedules
        method: DELETE
        path: /scheduler/schedules/{scheduleId}
        summary: Delete schedules
        """
        endpoint = f"{self._base_url}/scheduler/schedules/{scheduleId}"
        params = { 'scheduleId': scheduleId, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def patch_schedule(self, scheduleId: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: patch_schedule
        method: PATCH
        path: /scheduler/schedules/{scheduleId}
        summary: Patch schedules
        """
        endpoint = f"{self._base_url}/scheduler/schedules/{scheduleId}"
        params = { 'scheduleId': scheduleId, 'user_id': user_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def single_use_link(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: single_use_link
        method: POST
        path: /scheduler/schedules/single_use_link
        summary: Single use link
        """
        endpoint = f"{self._base_url}/scheduler/schedules/single_use_link"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_shares(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: create_shares
        method: POST
        path: /scheduler/shares
        summary: Create shares
        """
        endpoint = f"{self._base_url}/scheduler/shares"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: get_user
        method: GET
        path: /scheduler/users/{userId}
        summary: Get user
        """
        endpoint = f"{self._base_url}/scheduler/users/{userId}"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_scim2_list(self, startIndex: Optional[Any] = None, count: Optional[Any] = None, filter: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupSCIM2List
        method: GET
        path: /scim2/Groups
        summary: List groups
        """
        endpoint = f"{self._base_url}/scim2/Groups"
        params = { 'startIndex': startIndex, 'count': count, 'filter': filter }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_scim2_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupScim2Create
        method: POST
        path: /scim2/Groups
        summary: Create a group
        """
        endpoint = f"{self._base_url}/scim2/Groups"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_scim2_get(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupSCIM2Get
        method: GET
        path: /scim2/Groups/{groupId}
        summary: Get a group
        """
        endpoint = f"{self._base_url}/scim2/Groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_scim2_delete(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupSCIM2Delete
        method: DELETE
        path: /scim2/Groups/{groupId}
        summary: Delete a group
        """
        endpoint = f"{self._base_url}/scim2/Groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_scim2_update(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupSCIM2Update
        method: PATCH
        path: /scim2/Groups/{groupId}
        summary: Update a group
        """
        endpoint = f"{self._base_url}/scim2/Groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_scim2_list(self, startIndex: Optional[Any] = None, count: Optional[Any] = None, filter: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSCIM2List
        method: GET
        path: /scim2/Users
        summary: List users
        """
        endpoint = f"{self._base_url}/scim2/Users"
        params = { 'startIndex': startIndex, 'count': count, 'filter': filter }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_scim2_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userScim2Create
        method: POST
        path: /scim2/Users
        summary: Create a user
        """
        endpoint = f"{self._base_url}/scim2/Users"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_scim2_get(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSCIM2Get
        method: GET
        path: /scim2/Users/{userId}
        summary: Get a user
        """
        endpoint = f"{self._base_url}/scim2/Users/{userId}"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_scim2_update(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSCIM2Update
        method: PUT
        path: /scim2/Users/{userId}
        summary: Update a user
        """
        endpoint = f"{self._base_url}/scim2/Users/{userId}"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_scim2_delete(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSCIM2Delete
        method: DELETE
        path: /scim2/Users/{userId}
        summary: Delete a user
        """
        endpoint = f"{self._base_url}/scim2/Users/{userId}"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_adscim2_deactivate(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userADSCIM2Deactivate
        method: PATCH
        path: /scim2/Users/{userId}
        summary: Deactivate a user
        """
        endpoint = f"{self._base_url}/scim2/Users/{userId}"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_channel_mention_group(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChannelMentionGroup
        method: GET
        path: /chat/channels/{channelId}/mention_group
        summary: List channel mention groups
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_channel_mention_group(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createChannelMentionGroup
        method: POST
        path: /chat/channels/{channelId}/mention_group
        summary: Create a channel mention group
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group"
        params = { 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_channel_mention_group(self, channelId: Optional[Any] = None, mentionGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteAChannelMentionGroup
        method: DELETE
        path: /chat/channels/{channelId}/mention_group/{mentionGroupId}
        summary: Delete a channel mention group
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group/{mentionGroupId}"
        params = { 'channelId': channelId, 'mentionGroupId': mentionGroupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_channel_mention_group(self, channelId: Optional[Any] = None, mentionGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateChannelMentionGroup
        method: PATCH
        path: /chat/channels/{channelId}/mention_group/{mentionGroupId}
        summary: Update a channel mention group information
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group/{mentionGroupId}"
        params = { 'channelId': channelId, 'mentionGroupId': mentionGroupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_the_members_of_mention_group(self, channelId: Optional[Any] = None, mentionGroupId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listTheMembersOfMentionGroup
        method: GET
        path: /chat/channels/{channelId}/mention_group/{mentionGroupId}/members
        summary: List the members of a mention group
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group/{mentionGroupId}/members"
        params = { 'channelId': channelId, 'mentionGroupId': mentionGroupId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_a_channel_members_to_mention_group(self, channelId: Optional[Any] = None, mentionGroupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addAChannelMembersToMentionGroup
        method: POST
        path: /chat/channels/{channelId}/mention_group/{mentionGroupId}/members
        summary: Add channel members to a mention group
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group/{mentionGroupId}/members"
        params = { 'channelId': channelId, 'mentionGroupId': mentionGroupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_channel_mention_group_members(self, channelId: Optional[Any] = None, mentionGroupId: Optional[Any] = None, identifiers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeChannelMentionGroupMembers
        method: DELETE
        path: /chat/channels/{channelId}/mention_group/{mentionGroupId}/members
        summary: Remove channel mention group members
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/mention_group/{mentionGroupId}/members"
        params = { 'channelId': channelId, 'mentionGroupId': mentionGroupId, 'identifiers': identifiers }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_all_channel_activity_logs(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, activity_type: Optional[Any] = None, start_date: Optional[Any] = None, end_date: Optional[Any] = None, channel_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listAllChannelActivityLogs
        method: GET
        path: /chat/activities/channels
        summary: List channel activity logs
        """
        endpoint = f"{self._base_url}/chat/activities/channels"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'activity_type': activity_type, 'start_date': start_date, 'end_date': end_date, 'channel_id': channel_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def perform_operations_on_channels(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: PerformOperationsOnChannels
        method: PATCH
        path: /chat/channels/events
        summary: Perform operations on channels
        """
        endpoint = f"{self._base_url}/chat/channels/events"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_level_channel(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserLevelChannel
        method: GET
        path: /chat/channels/{channelId}
        summary: Get a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_user_level_channel(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteUserLevelChannel
        method: DELETE
        path: /chat/channels/{channelId}
        summary: Delete a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_user_level_channel(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateUserLevelChannel
        method: PATCH
        path: /chat/channels/{channelId}
        summary: Update a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}"
        params = { 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_user_level_channel_members(self, channelId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listUserLevelChannelMembers
        method: GET
        path: /chat/channels/{channelId}/members
        summary: List channel members
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members"
        params = { 'channelId': channelId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def invite_user_level_channel_members(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: InviteUserLevelChannelMembers
        method: POST
        path: /chat/channels/{channelId}/members
        summary: Invite channel members
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members"
        params = { 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_remove_channel_members(self, channelId: Optional[Any] = None, member_ids: Optional[Any] = None, user_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchRemoveChannelMembers
        method: DELETE
        path: /chat/channels/{channelId}/members
        summary: Batch remove members from a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members"
        params = { 'channelId': channelId, 'member_ids': member_ids, 'user_ids': user_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_channel_members_groups(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listChannelMembersGroups
        method: GET
        path: /chat/channels/{channelId}/members/groups
        summary: List channel members (Groups)
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members/groups"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def invite_channel_members_groups(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: inviteChannelMembersGroups
        method: POST
        path: /chat/channels/{channelId}/members/groups
        summary: Invite channel members (Groups)
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members/groups"
        params = { 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_a_member_group(self, channelId: Optional[Any] = None, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeAMemberGroup
        method: DELETE
        path: /chat/channels/{channelId}/members/groups/{groupId}
        summary: Remove a member (group)
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members/groups/{groupId}"
        params = { 'channelId': channelId, 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def join_channel(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: joinChannel
        method: POST
        path: /chat/channels/{channelId}/members/me
        summary: Join a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members/me"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def leave_channel(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: leaveChannel
        method: DELETE
        path: /chat/channels/{channelId}/members/me
        summary: Leave a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members/me"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_a_user_level_channel_member(self, channelId: Optional[Any] = None, identifier: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeAUserLevelChannelMember
        method: DELETE
        path: /chat/channels/{channelId}/members/{identifier}
        summary: Remove a member
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/members/{identifier}"
        params = { 'channelId': channelId, 'identifier': identifier }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_channels(self, userId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChannels
        method: GET
        path: /chat/users/{userId}/channels
        summary: List user's channels
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels"
        params = { 'userId': userId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_channel(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createChannel
        method: POST
        path: /chat/users/{userId}/channels
        summary: Create a channel
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_delete_channels_account_level(self, userId: Optional[Any] = None, channel_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchDeleteChannelsAccountLevel
        method: DELETE
        path: /chat/users/{userId}/channels
        summary: Batch delete channels
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels"
        params = { 'userId': userId, 'channel_ids': channel_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_account_channels(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getAccountChannels
        method: GET
        path: /chat/channels
        summary: List account's public channels
        """
        endpoint = f"{self._base_url}/chat/channels"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def search_channels(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: searchChannels
        method: POST
        path: /chat/channels/search
        summary: Search user's or account's channels
        """
        endpoint = f"{self._base_url}/chat/channels/search"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_channel_activity_logs(self, channelId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, activity_type: Optional[Any] = None, start_date: Optional[Any] = None, end_date: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listChannelActivityLogs
        method: GET
        path: /chat/channels/{channelId}/activities
        summary: List channel activity logs
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/activities"
        params = { 'channelId': channelId, 'page_size': page_size, 'next_page_token': next_page_token, 'activity_type': activity_type, 'start_date': start_date, 'end_date': end_date }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_channel_retention(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChannelRetention
        method: GET
        path: /chat/channels/{channelId}/retention
        summary: Get retention policy of a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/retention"
        params = { 'channelId': channelId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_channel_retention(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateChannelRetention
        method: PATCH
        path: /chat/channels/{channelId}/retention
        summary: Update retention policy of a channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/retention"
        params = { 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_channel(self, channelId: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChannel
        method: GET
        path: /chat/users/{userId}/channels/{channelId}
        summary: Get a channel
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}"
        params = { 'channelId': channelId, 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_channel(self, channelId: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteChannel
        method: DELETE
        path: /chat/users/{userId}/channels/{channelId}
        summary: Delete a channel
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}"
        params = { 'channelId': channelId, 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_channel(self, channelId: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateChannel
        method: PATCH
        path: /chat/users/{userId}/channels/{channelId}
        summary: Update a channel
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}"
        params = { 'channelId': channelId, 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_channel_administrators(self, userId: Optional[Any] = None, channelId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listChannelAdministrators
        method: GET
        path: /chat/users/{userId}/channels/{channelId}/admins
        summary: List channel administrators
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/admins"
        params = { 'userId': userId, 'channelId': channelId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def promote_channel_members_as_admin(self, userId: Optional[Any] = None, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: promoteChannelMembersAsAdmin
        method: POST
        path: /chat/users/{userId}/channels/{channelId}/admins
        summary: Promote channel members to administrators
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/admins"
        params = { 'userId': userId, 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_demote_channel_administrators(self, userId: Optional[Any] = None, channelId: Optional[Any] = None, admin_ids: Optional[Any] = None, user_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchDemoteChannelAdministrators
        method: DELETE
        path: /chat/users/{userId}/channels/{channelId}/admins
        summary: Batch demote channel administrators
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/admins"
        params = { 'userId': userId, 'channelId': channelId, 'admin_ids': admin_ids, 'user_ids': user_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_channel_members(self, channelId: Optional[Any] = None, userId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listChannelMembers
        method: GET
        path: /chat/users/{userId}/channels/{channelId}/members
        summary: List channel members
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/members"
        params = { 'channelId': channelId, 'userId': userId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def invite_channel_members(self, channelId: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: inviteChannelMembers
        method: POST
        path: /chat/users/{userId}/channels/{channelId}/members
        summary: Invite channel members
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/members"
        params = { 'channelId': channelId, 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def batch_remove_user_channel_members(self, channelId: Optional[Any] = None, userId: Optional[Any] = None, identifiers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: batchRemoveUserChannelMembers
        method: DELETE
        path: /chat/users/{userId}/channels/{channelId}/members
        summary: Batch remove members from a user's channel
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/members"
        params = { 'channelId': channelId, 'userId': userId, 'identifiers': identifiers }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def remove_a_channel_member(self, channelId: Optional[Any] = None, identifier: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: removeAChannelMember
        method: DELETE
        path: /chat/users/{userId}/channels/{channelId}/members/{identifier}
        summary: Remove a member
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/channels/{channelId}/members/{identifier}"
        params = { 'channelId': channelId, 'identifier': identifier, 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_custom_emojis(self, page_size: Optional[Any] = None, search_key: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCustomEmojis
        method: GET
        path: /chat/emoji
        summary: List custom emojis
        """
        endpoint = f"{self._base_url}/chat/emoji"
        params = { 'page_size': page_size, 'search_key': search_key, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_a_custom_emoji(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addACustomEmoji
        method: POST
        path: /chat/emoji/files
        summary: Add a custom emoji
        """
        endpoint = f"{self._base_url}/chat/emoji/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_custom_emoji(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteCustomEmoji
        method: DELETE
        path: /chat/emoji/{fileId}
        summary: Delete a custom emoji
        """
        endpoint = f"{self._base_url}/chat/emoji/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_file_info(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getFileInfo
        method: GET
        path: /chat/files/{fileId}
        summary: Get file info
        """
        endpoint = f"{self._base_url}/chat/files/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_chat_file(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteChatFile
        method: DELETE
        path: /chat/files/{fileId}
        summary: Delete a chat file
        """
        endpoint = f"{self._base_url}/chat/files/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_a_chat_file(self, userId: Optional[Any] = None, postToPersonalChat: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: uploadAChatFile
        method: POST
        path: /chat/users/{userId}/files
        summary: Upload a chat file
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/files"
        params = { 'userId': userId, 'postToPersonalChat': postToPersonalChat }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def send_chat_file(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: sendChatFile
        method: POST
        path: /chat/users/{userId}/messages/files
        summary: Send a chat file
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/files"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def perform_message_of_channel(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: PerformMessageOfChannel
        method: PATCH
        path: /chat/channel/message/events
        summary: Perform operations on the message of channel
        """
        endpoint = f"{self._base_url}/chat/channel/message/events"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_channel_pinned_messages(self, channelId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, include_history: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listChannelPinnedMessages
        method: GET
        path: /chat/channels/{channelId}/pinned
        summary: List pinned history messages of channel
        """
        endpoint = f"{self._base_url}/chat/channels/{channelId}/pinned"
        params = { 'channelId': channelId, 'page_size': page_size, 'next_page_token': next_page_token, 'include_history': include_history }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_forwarded_message(self, forwardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getForwardedMessage
        method: GET
        path: /chat/forwarded_message/{forwardId}
        summary: Get a forwarded message
        """
        endpoint = f"{self._base_url}/chat/forwarded_message/{forwardId}"
        params = { 'forwardId': forwardId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def fetch_bookmarks(self, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: fetchBookmarks
        method: GET
        path: /chat/messages/bookmarks
        summary: List bookmarks
        """
        endpoint = f"{self._base_url}/chat/messages/bookmarks"
        params = { 'to_contact': to_contact, 'to_channel': to_channel, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_or_remove_a_bookmark(self, message_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addOrRemoveABookmark
        method: PATCH
        path: /chat/messages/bookmarks
        summary: Add or remove a bookmark
        """
        endpoint = f"{self._base_url}/chat/messages/bookmarks"
        params = { 'message_id': message_id }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_scheduled_messages(self, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listScheduledMessages
        method: GET
        path: /chat/messages/schedule
        summary: List scheduled messages
        """
        endpoint = f"{self._base_url}/chat/messages/schedule"
        params = { 'to_contact': to_contact, 'to_channel': to_channel, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_schedule_message(self, draftId: Optional[Any] = None, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteScheduleMessage
        method: DELETE
        path: /chat/messages/schedule/{draftId}
        summary: Delete a scheduled message
        """
        endpoint = f"{self._base_url}/chat/messages/schedule/{draftId}"
        params = { 'draftId': draftId, 'to_contact': to_contact, 'to_channel': to_channel }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_chat_messages(self, userId: Optional[Any] = None, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, date: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, include_deleted_and_edited_message: Optional[Any] = None, search_type: Optional[Any] = None, search_key: Optional[Any] = None, exclude_child_message: Optional[Any] = None, download_file_formats: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChatMessages
        method: GET
        path: /chat/users/{userId}/messages
        summary: List user's chat messages
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages"
        params = { 'userId': userId, 'to_contact': to_contact, 'to_channel': to_channel, 'date': date, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'include_deleted_and_edited_message': include_deleted_and_edited_message, 'search_type': search_type, 'search_key': search_key, 'exclude_child_message': exclude_child_message, 'download_file_formats': download_file_formats }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def senda_chat_message(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: sendaChatMessage
        method: POST
        path: /chat/users/{userId}/messages
        summary: Send a chat message
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_chat_message(self, userId: Optional[Any] = None, messageId: Optional[Any] = None, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, download_file_formats: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChatMessage
        method: GET
        path: /chat/users/{userId}/messages/{messageId}
        summary: Get a message
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/{messageId}"
        params = { 'userId': userId, 'messageId': messageId, 'to_contact': to_contact, 'to_channel': to_channel, 'download_file_formats': download_file_formats }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def edit_message(self, userId: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: editMessage
        method: PUT
        path: /chat/users/{userId}/messages/{messageId}
        summary: Update a message
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/{messageId}"
        params = { 'userId': userId, 'messageId': messageId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_chat_message(self, userId: Optional[Any] = None, messageId: Optional[Any] = None, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteChatMessage
        method: DELETE
        path: /chat/users/{userId}/messages/{messageId}
        summary: Delete a message
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/{messageId}"
        params = { 'userId': userId, 'messageId': messageId, 'to_contact': to_contact, 'to_channel': to_channel }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def react_message(self, userId: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reactMessage
        method: PATCH
        path: /chat/users/{userId}/messages/{messageId}/emoji_reactions
        summary: React to a chat message
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/{messageId}/emoji_reactions"
        params = { 'userId': userId, 'messageId': messageId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def mark_message(self, userId: Optional[Any] = None, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: markMessage
        method: PATCH
        path: /chat/users/{userId}/messages/{messageId}/status
        summary: Mark message read or unread
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/{messageId}/status"
        params = { 'userId': userId, 'messageId': messageId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def retrieve_thread(self, userId: Optional[Any] = None, messageId: Optional[Any] = None, to_channel: Optional[Any] = None, to_contact: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, limit: Optional[Any] = None, sort: Optional[Any] = None, need_main_message: Optional[Any] = None, need_emoji: Optional[Any] = None, need_attachment: Optional[Any] = None, need_rich_text: Optional[Any] = None, need_at_items: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: retrieveThread
        method: GET
        path: /chat/users/{userId}/messages/{messageId}/thread
        summary: Retrieve a thread
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/messages/{messageId}/thread"
        params = { 'userId': userId, 'messageId': messageId, 'to_channel': to_channel, 'to_contact': to_contact, 'from': from_, 'to': to, 'limit': limit, 'sort': sort, 'need_main_message': need_main_message, 'need_emoji': need_emoji, 'need_attachment': need_attachment, 'need_rich_text': need_rich_text, 'need_at_items': need_at_items }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def migrate_channel_members(self, channelId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: MigrateChannelMembers
        method: POST
        path: /chat/migration/channels/{channelId}/members
        summary: Migrate channel members
        """
        endpoint = f"{self._base_url}/chat/migration/channels/{channelId}/members"
        params = { 'channelId': channelId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def migrate_chat_message_reactions(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: MigrateChatMessageReactions
        method: POST
        path: /chat/migration/emoji_reactions
        summary: Migrate chat message reactions
        """
        endpoint = f"{self._base_url}/chat/migration/emoji_reactions"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_migration_channels_mapping(self, origin_platform: Optional[Any] = None, origin_team_id: Optional[Any] = None, origin_channel_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getMigrationChannelsMapping
        method: GET
        path: /chat/migration/mappings/channels
        summary: Get migrated Zoom channel IDs
        """
        endpoint = f"{self._base_url}/chat/migration/mappings/channels"
        params = { 'origin_platform': origin_platform, 'origin_team_id': origin_team_id, 'origin_channel_ids': origin_channel_ids }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_migration_users_mapping(self, origin_platform: Optional[Any] = None, origin_team_id: Optional[Any] = None, origin_user_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getMigrationUsersMapping
        method: GET
        path: /chat/migration/mappings/users
        summary: Get migrated Zoom user IDs
        """
        endpoint = f"{self._base_url}/chat/migration/mappings/users"
        params = { 'origin_platform': origin_platform, 'origin_team_id': origin_team_id, 'origin_user_ids': origin_user_ids }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def migrate_chat_messages(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: MigrateChatMessages
        method: POST
        path: /chat/migration/messages
        summary: Migrate chat messages
        """
        endpoint = f"{self._base_url}/chat/migration/messages"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def migrate_a_chat_channel(self, identifier: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: MigrateAChatChannel
        method: POST
        path: /chat/migration/users/{identifier}/channels
        summary: Migrate a chat channel
        """
        endpoint = f"{self._base_url}/chat/migration/users/{identifier}/channels"
        params = { 'identifier': identifier }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def migrate1_1_conversation_or_channel_operations(self, identifier: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Migrate1:1ConversationOrChannelOperations
        method: POST
        path: /chat/migration/users/{identifier}/events
        summary: Migrate 1:1 conversation or channel operations
        """
        endpoint = f"{self._base_url}/chat/migration/users/{identifier}/events"
        params = { 'identifier': identifier }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_reminder_for_message(self, messageId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createReminderForMessage
        method: POST
        path: /chat/messages/{messageId}/reminder
        summary: Create a reminder message
        """
        endpoint = f"{self._base_url}/chat/messages/{messageId}/reminder"
        params = { 'messageId': messageId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_reminder_for_message(self, messageId: Optional[Any] = None, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteReminderForMessage
        method: DELETE
        path: /chat/messages/{messageId}/reminder
        summary: Delete a reminder for a message
        """
        endpoint = f"{self._base_url}/chat/messages/{messageId}/reminder"
        params = { 'messageId': messageId, 'to_contact': to_contact, 'to_channel': to_channel }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_reminders(self, to_contact: Optional[Any] = None, to_channel: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listReminders
        method: GET
        path: /chat/reminder
        summary: List reminders
        """
        endpoint = f"{self._base_url}/chat/reminder"
        params = { 'to_contact': to_contact, 'to_channel': to_channel, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def star_unstar_channel_contact(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: starUnstarChannelContact
        method: PATCH
        path: /chat/users/{userId}/events
        summary: Star or unstar a channel or contact user
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/events"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_chat_sessions(self, userId: Optional[Any] = None, type_: Optional[Any] = None, search_star: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getChatSessions
        method: GET
        path: /chat/users/{userId}/sessions
        summary: List a user's chat sessions
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/sessions"
        params = { 'userId': userId, 'type': type_, 'search_star': search_star, 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_contacts(self, type_: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserContacts
        method: GET
        path: /chat/users/me/contacts
        summary: List user's contacts
        """
        endpoint = f"{self._base_url}/chat/users/me/contacts"
        params = { 'type': type_, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_contact(self, identifier: Optional[Any] = None, query_presence_status: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserContact
        method: GET
        path: /chat/users/me/contacts/{identifier}
        summary: Get user's contact details
        """
        endpoint = f"{self._base_url}/chat/users/me/contacts/{identifier}"
        params = { 'identifier': identifier, 'query_presence_status': query_presence_status }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def search_company_contacts(self, search_key: Optional[Any] = None, query_presence_status: Optional[Any] = None, page_size: Optional[Any] = None, contact_types: Optional[Any] = None, user_status: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: searchCompanyContacts
        method: GET
        path: /contacts
        summary: Search company contacts
        """
        endpoint = f"{self._base_url}/contacts"
        params = { 'search_key': search_key, 'query_presence_status': query_presence_status, 'page_size': page_size, 'contact_types': contact_types, 'user_status': user_status, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def sendimmessages(self, chat_user: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: sendimmessages
        method: POST
        path: /im/users/me/chat/messages
        summary: Send IM messages
        """
        endpoint = f"{self._base_url}/im/users/me/chat/messages"
        params = { 'chat_user': chat_user }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_groups(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroups
        method: GET
        path: /im/groups
        summary: List IM directory groups
        """
        endpoint = f"{self._base_url}/im/groups"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroupCreate
        method: POST
        path: /im/groups
        summary: Create an IM directory group
        """
        endpoint = f"{self._base_url}/im/groups"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroup
        method: GET
        path: /im/groups/{groupId}
        summary: Retrieve an IM directory group
        """
        endpoint = f"{self._base_url}/im/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group_delete(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroupDelete
        method: DELETE
        path: /im/groups/{groupId}
        summary: Delete an IM directory group
        """
        endpoint = f"{self._base_url}/im/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group_update(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroupUpdate
        method: PATCH
        path: /im/groups/{groupId}
        summary: Update an IM directory group
        """
        endpoint = f"{self._base_url}/im/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group_members(self, groupId: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroupMembers
        method: GET
        path: /im/groups/{groupId}/members
        summary: List IM directory group members
        """
        endpoint = f"{self._base_url}/im/groups/{groupId}/members"
        params = { 'groupId': groupId, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group_members_create(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroupMembersCreate
        method: POST
        path: /im/groups/{groupId}/members
        summary: Add IM directory group members
        """
        endpoint = f"{self._base_url}/im/groups/{groupId}/members"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def im_group_members_delete(self, groupId: Optional[Any] = None, memberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: imGroupMembersDelete
        method: DELETE
        path: /im/groups/{groupId}/members/{memberId}
        summary: Delete IM directory group member
        """
        endpoint = f"{self._base_url}/im/groups/{groupId}/members/{memberId}"
        params = { 'groupId': groupId, 'memberId': memberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def send_new_contact_invitation(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: sendNewContactInvitation
        method: POST
        path: /chat/users/{userId}/invitations
        summary: Send new contact invitation
        """
        endpoint = f"{self._base_url}/chat/users/{userId}/invitations"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_legal_hold_matters(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listLegalHoldMatters
        method: GET
        path: /chat/legalhold/matters
        summary: List legal hold matters
        """
        endpoint = f"{self._base_url}/chat/legalhold/matters"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_legal_hold_matter(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addLegalHoldMatter
        method: POST
        path: /chat/legalhold/matters
        summary: Add a legal hold matter
        """
        endpoint = f"{self._base_url}/chat/legalhold/matters"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_legal_hold_matters(self, matterId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteLegalHoldMatters
        method: DELETE
        path: /chat/legalhold/matters/{matterId}
        summary: Delete legal hold matters
        """
        endpoint = f"{self._base_url}/chat/legalhold/matters/{matterId}"
        params = { 'matterId': matterId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_legal_hold_matter(self, matterId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateLegalHoldMatter
        method: PATCH
        path: /chat/legalhold/matters/{matterId}
        summary: Update legal hold matter
        """
        endpoint = f"{self._base_url}/chat/legalhold/matters/{matterId}"
        params = { 'matterId': matterId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_legal_hold_files(self, matterId: Optional[Any] = None, identifier: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listLegalHoldFiles
        method: GET
        path: /chat/legalhold/matters/{matterId}/files
        summary: List legal hold files by given matter
        """
        endpoint = f"{self._base_url}/chat/legalhold/matters/{matterId}/files"
        params = { 'matterId': matterId, 'identifier': identifier, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def download_legal_hold_files(self, matterId: Optional[Any] = None, file_key: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: downloadLegalHoldFiles
        method: GET
        path: /chat/legalhold/matters/{matterId}/files/download
        summary: Download legal hold files for given matter
        """
        endpoint = f"{self._base_url}/chat/legalhold/matters/{matterId}/files/download"
        params = { 'matterId': matterId, 'file_key': file_key }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_chat_sessions(self, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportChatSessions
        method: GET
        path: /report/chat/sessions
        summary: Get chat sessions reports
        """
        endpoint = f"{self._base_url}/report/chat/sessions"
        params = { 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def report_chat_messages(self, sessionId: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, include_fields: Optional[Any] = None, include_bot_message: Optional[Any] = None, include_reactions: Optional[Any] = None, query_all_modifications: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: reportChatMessages
        method: GET
        path: /report/chat/sessions/{sessionId}
        summary: Get chat message reports
        """
        endpoint = f"{self._base_url}/report/chat/sessions/{sessionId}"
        params = { 'sessionId': sessionId, 'from': from_, 'to': to, 'next_page_token': next_page_token, 'page_size': page_size, 'include_fields': include_fields, 'include_bot_message': include_bot_message, 'include_reactions': include_reactions, 'query_all_modifications': query_all_modifications }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_shared_spaces(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, user_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSharedSpaces
        method: GET
        path: /chat/spaces
        summary: List shared spaces
        """
        endpoint = f"{self._base_url}/chat/spaces"
        params = { 'next_page_token': next_page_token, 'page_size': page_size, 'user_id': user_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_space(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: createSpace
        method: POST
        path: /chat/spaces
        summary: Create a shared space
        """
        endpoint = f"{self._base_url}/chat/spaces"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_shared_space(self, spaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getASharedSpace
        method: GET
        path: /chat/spaces/{spaceId}
        summary: Get a shared space
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}"
        params = { 'spaceId': spaceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_space(self, spaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteSpace
        method: DELETE
        path: /chat/spaces/{spaceId}
        summary: Delete a shared space
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}"
        params = { 'spaceId': spaceId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_shared_space_settings(self, spaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSharedSpaceSettings
        method: PATCH
        path: /chat/spaces/{spaceId}
        summary: Update shared space settings
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}"
        params = { 'spaceId': spaceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def promote_space_members(self, spaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: promoteSpaceMembers
        method: POST
        path: /chat/spaces/{spaceId}/admins
        summary: Promote shared space members to administrators
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/admins"
        params = { 'spaceId': spaceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def demote_space_admins(self, spaceId: Optional[Any] = None, identifiers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: demoteSpaceAdmins
        method: DELETE
        path: /chat/spaces/{spaceId}/admins
        summary: Demote shared space administrators to members
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/admins"
        params = { 'spaceId': spaceId, 'identifiers': identifiers }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_shared_space_channels(self, spaceId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSharedSpaceChannels
        method: GET
        path: /chat/spaces/{spaceId}/channels
        summary: List shared space channels
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/channels"
        params = { 'spaceId': spaceId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_shared_space_channels(self, spaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateSharedSpaceChannels
        method: PATCH
        path: /chat/spaces/{spaceId}/channels
        summary: Move shared space channels
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/channels"
        params = { 'spaceId': spaceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_shared_space_members(self, spaceId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, role: Optional[Any] = None, status: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listSharedSpaceMembers
        method: GET
        path: /chat/spaces/{spaceId}/members
        summary: List shared space members
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/members"
        params = { 'spaceId': spaceId, 'page_size': page_size, 'next_page_token': next_page_token, 'role': role, 'status': status }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_space_members(self, spaceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: addSpaceMembers
        method: POST
        path: /chat/spaces/{spaceId}/members
        summary: Add members to a shared space
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/members"
        params = { 'spaceId': spaceId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_space_members(self, spaceId: Optional[Any] = None, identifiers: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: deleteSpaceMembers
        method: DELETE
        path: /chat/spaces/{spaceId}/members
        summary: Remove members from a shared space
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/members"
        params = { 'spaceId': spaceId, 'identifiers': identifiers }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def transfer_space_owner(self, spaceId: Optional[Any] = None, identifier: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: transferSpaceOwner
        method: PATCH
        path: /chat/spaces/{spaceId}/owner
        summary: Transfer shared space ownership
        """
        endpoint = f"{self._base_url}/chat/spaces/{spaceId}/owner"
        params = { 'spaceId': spaceId, 'identifier': identifier }
        body = None
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_groups(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroups
        method: GET
        path: /contacts/groups
        summary: List contact groups
        """
        endpoint = f"{self._base_url}/contacts/groups"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroupCreate
        method: POST
        path: /contacts/groups
        summary: Create a contact group
        """
        endpoint = f"{self._base_url}/contacts/groups"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroup
        method: GET
        path: /contacts/groups/{groupId}
        summary: Get a contact group
        """
        endpoint = f"{self._base_url}/contacts/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group_delete(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroupDelete
        method: DELETE
        path: /contacts/groups/{groupId}
        summary: Delete a contact group
        """
        endpoint = f"{self._base_url}/contacts/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group_update(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroupUpdate
        method: PATCH
        path: /contacts/groups/{groupId}
        summary: Update a contact group
        """
        endpoint = f"{self._base_url}/contacts/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group_members(self, groupId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroupMembers
        method: GET
        path: /contacts/groups/{groupId}/members
        summary: List contact group members
        """
        endpoint = f"{self._base_url}/contacts/groups/{groupId}/members"
        params = { 'groupId': groupId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group_member_add(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroupMemberAdd
        method: POST
        path: /contacts/groups/{groupId}/members
        summary: Add contact group members
        """
        endpoint = f"{self._base_url}/contacts/groups/{groupId}/members"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def contact_group_member_remove(self, groupId: Optional[Any] = None, member_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: contactGroupMemberRemove
        method: DELETE
        path: /contacts/groups/{groupId}/members
        summary: Remove members in a contact group
        """
        endpoint = f"{self._base_url}/contacts/groups/{groupId}/members"
        params = { 'groupId': groupId, 'member_ids': member_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_divisions(self, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listDivisions
        method: GET
        path: /divisions
        summary: List divisions
        """
        endpoint = f"{self._base_url}/divisions"
        params = { 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def createadivision(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Createadivision
        method: POST
        path: /divisions
        summary: Create a division
        """
        endpoint = f"{self._base_url}/divisions"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getdivision(self, divisionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getdivision
        method: GET
        path: /divisions/{divisionId}
        summary: Get a division
        """
        endpoint = f"{self._base_url}/divisions/{divisionId}"
        params = { 'divisionId': divisionId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deletedivision(self, divisionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deletedivision
        method: DELETE
        path: /divisions/{divisionId}
        summary: Delete a division
        """
        endpoint = f"{self._base_url}/divisions/{divisionId}"
        params = { 'divisionId': divisionId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updateadivision(self, divisionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Updateadivision
        method: PATCH
        path: /divisions/{divisionId}
        summary: Update a division
        """
        endpoint = f"{self._base_url}/divisions/{divisionId}"
        params = { 'divisionId': divisionId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_division_members(self, divisionId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listDivisionMembers
        method: GET
        path: /divisions/{divisionId}/users
        summary: List division members
        """
        endpoint = f"{self._base_url}/divisions/{divisionId}/users"
        params = { 'divisionId': divisionId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def assigndivision_member(self, divisionId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: assigndivisionMember
        method: POST
        path: /divisions/{divisionId}/users
        summary: Assign a division
        """
        endpoint = f"{self._base_url}/divisions/{divisionId}/users"
        params = { 'divisionId': divisionId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def groups(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groups
        method: GET
        path: /groups
        summary: List groups
        """
        endpoint = f"{self._base_url}/groups"
        params = { 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupCreate
        method: POST
        path: /groups
        summary: Create a group
        """
        endpoint = f"{self._base_url}/groups"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def group(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: group
        method: GET
        path: /groups/{groupId}
        summary: Get a group
        """
        endpoint = f"{self._base_url}/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_delete(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupDelete
        method: DELETE
        path: /groups/{groupId}
        summary: Delete a group
        """
        endpoint = f"{self._base_url}/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_update(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupUpdate
        method: PATCH
        path: /groups/{groupId}
        summary: Update a group
        """
        endpoint = f"{self._base_url}/groups/{groupId}"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_admins(self, groupId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupAdmins
        method: GET
        path: /groups/{groupId}/admins
        summary: List group admins
        """
        endpoint = f"{self._base_url}/groups/{groupId}/admins"
        params = { 'groupId': groupId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_admins_create(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupAdminsCreate
        method: POST
        path: /groups/{groupId}/admins
        summary: Add group admins
        """
        endpoint = f"{self._base_url}/groups/{groupId}/admins"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_admins_delete(self, groupId: Optional[Any] = None, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupAdminsDelete
        method: DELETE
        path: /groups/{groupId}/admins/{userId}
        summary: Delete a group admin
        """
        endpoint = f"{self._base_url}/groups/{groupId}/admins/{userId}"
        params = { 'groupId': groupId, 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_channels(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupChannels
        method: GET
        path: /groups/{groupId}/channels
        summary: List group channels
        """
        endpoint = f"{self._base_url}/groups/{groupId}/channels"
        params = { 'groupId': groupId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_group_lock_settings(self, groupId: Optional[Any] = None, option: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getGroupLockSettings
        method: GET
        path: /groups/{groupId}/lock_settings
        summary: Get locked settings
        """
        endpoint = f"{self._base_url}/groups/{groupId}/lock_settings"
        params = { 'groupId': groupId, 'option': option }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_locked_settings(self, groupId: Optional[Any] = None, option: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupLockedSettings
        method: PATCH
        path: /groups/{groupId}/lock_settings
        summary: Update locked settings
        """
        endpoint = f"{self._base_url}/groups/{groupId}/lock_settings"
        params = { 'groupId': groupId, 'option': option }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_members(self, groupId: Optional[Any] = None, page_size: Optional[Any] = None, page_number: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupMembers
        method: GET
        path: /groups/{groupId}/members
        summary: List group members 
        """
        endpoint = f"{self._base_url}/groups/{groupId}/members"
        params = { 'groupId': groupId, 'page_size': page_size, 'page_number': page_number, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_members_create(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupMembersCreate
        method: POST
        path: /groups/{groupId}/members
        summary: Add group members
        """
        endpoint = f"{self._base_url}/groups/{groupId}/members"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_members_delete(self, groupId: Optional[Any] = None, memberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupMembersDelete
        method: DELETE
        path: /groups/{groupId}/members/{memberId}
        summary: Delete a group member
        """
        endpoint = f"{self._base_url}/groups/{groupId}/members/{memberId}"
        params = { 'groupId': groupId, 'memberId': memberId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_group_member(self, groupId: Optional[Any] = None, memberId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateAGroupMember
        method: PATCH
        path: /groups/{groupId}/members/{memberId}
        summary: Update a group member
        """
        endpoint = f"{self._base_url}/groups/{groupId}/members/{memberId}"
        params = { 'groupId': groupId, 'memberId': memberId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_group_settings(self, groupId: Optional[Any] = None, option: Optional[Any] = None, custom_query_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getGroupSettings
        method: GET
        path: /groups/{groupId}/settings
        summary: Get a group's settings
        """
        endpoint = f"{self._base_url}/groups/{groupId}/settings"
        params = { 'groupId': groupId, 'option': option, 'custom_query_fields': custom_query_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_group_settings(self, groupId: Optional[Any] = None, option: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updateGroupSettings
        method: PATCH
        path: /groups/{groupId}/settings
        summary: Update a group's settings
        """
        endpoint = f"{self._base_url}/groups/{groupId}/settings"
        params = { 'groupId': groupId, 'option': option }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_settings_registration(self, groupId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupSettingsRegistration
        method: GET
        path: /groups/{groupId}/settings/registration
        summary: Get a group's webinar registration settings
        """
        endpoint = f"{self._base_url}/groups/{groupId}/settings/registration"
        params = { 'groupId': groupId, 'type': type_ }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def group_settings_registration_update(self, groupId: Optional[Any] = None, type_: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: groupSettingsRegistrationUpdate
        method: PATCH
        path: /groups/{groupId}/settings/registration
        summary: Update a group's webinar registration settings
        """
        endpoint = f"{self._base_url}/groups/{groupId}/settings/registration"
        params = { 'groupId': groupId, 'type': type_ }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_group_vb(self, groupId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: uploadGroupVB
        method: POST
        path: /groups/{groupId}/settings/virtual_backgrounds
        summary: Upload Virtual Background files
        """
        endpoint = f"{self._base_url}/groups/{groupId}/settings/virtual_backgrounds"
        params = { 'groupId': groupId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def del_group_vb(self, groupId: Optional[Any] = None, file_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delGroupVB
        method: DELETE
        path: /groups/{groupId}/settings/virtual_backgrounds
        summary: Delete Virtual Background files
        """
        endpoint = f"{self._base_url}/groups/{groupId}/settings/virtual_backgrounds"
        params = { 'groupId': groupId, 'file_ids': file_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def users(self, status: Optional[Any] = None, page_size: Optional[Any] = None, role_id: Optional[Any] = None, page_number: Optional[Any] = None, include_fields: Optional[Any] = None, next_page_token: Optional[Any] = None, license: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: users
        method: GET
        path: /users
        summary: List users
        """
        endpoint = f"{self._base_url}/users"
        params = { 'status': status, 'page_size': page_size, 'role_id': role_id, 'page_number': page_number, 'include_fields': include_fields, 'next_page_token': next_page_token, 'license': license }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userCreate
        method: POST
        path: /users
        summary: Create users
        """
        endpoint = f"{self._base_url}/users"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_email(self, email: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userEmail
        method: GET
        path: /users/email
        summary: Check a user email
        """
        endpoint = f"{self._base_url}/users/email"
        params = { 'email': email }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def bulk_update_feature(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: bulkUpdateFeature
        method: POST
        path: /users/features
        summary: Bulk update features for users
        """
        endpoint = f"{self._base_url}/users/features"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_zak(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userZak
        method: GET
        path: /users/me/zak
        summary: Get the user's ZAK
        """
        endpoint = f"{self._base_url}/users/me/zak"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_summary(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSummary
        method: GET
        path: /users/summary
        summary: Get user summary
        """
        endpoint = f"{self._base_url}/users/summary"
        params = {  }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_vanity_name(self, vanity_name: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userVanityName
        method: GET
        path: /users/vanity_name
        summary: Check a user's PM room
        """
        endpoint = f"{self._base_url}/users/vanity_name"
        params = { 'vanity_name': vanity_name }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user(self, userId: Optional[Any] = None, login_type: Optional[Any] = None, encrypted_email: Optional[Any] = None, search_by_unique_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: user
        method: GET
        path: /users/{userId}
        summary: Get a user
        """
        endpoint = f"{self._base_url}/users/{userId}"
        params = { 'userId': userId, 'login_type': login_type, 'encrypted_email': encrypted_email, 'search_by_unique_id': search_by_unique_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_delete(self, userId: Optional[Any] = None, encrypted_email: Optional[Any] = None, action: Optional[Any] = None, transfer_email: Optional[Any] = None, transfer_meeting: Optional[Any] = None, transfer_webinar: Optional[Any] = None, transfer_recording: Optional[Any] = None, transfer_whiteboard: Optional[Any] = None, transfer_clipfiles: Optional[Any] = None, transfer_notes: Optional[Any] = None, transfer_visitors: Optional[Any] = None, transfer_docs: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userDelete
        method: DELETE
        path: /users/{userId}
        summary: Delete a user
        """
        endpoint = f"{self._base_url}/users/{userId}"
        params = { 'userId': userId, 'encrypted_email': encrypted_email, 'action': action, 'transfer_email': transfer_email, 'transfer_meeting': transfer_meeting, 'transfer_webinar': transfer_webinar, 'transfer_recording': transfer_recording, 'transfer_whiteboard': transfer_whiteboard, 'transfer_clipfiles': transfer_clipfiles, 'transfer_notes': transfer_notes, 'transfer_visitors': transfer_visitors, 'transfer_docs': transfer_docs }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_update(self, userId: Optional[Any] = None, login_type: Optional[Any] = None, remove_tsp_credentials: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userUpdate
        method: PATCH
        path: /users/{userId}
        summary: Update a user
        """
        endpoint = f"{self._base_url}/users/{userId}"
        params = { 'userId': userId, 'login_type': login_type, 'remove_tsp_credentials': remove_tsp_credentials }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_assistants(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userAssistants
        method: GET
        path: /users/{userId}/assistants
        summary: List user assistants
        """
        endpoint = f"{self._base_url}/users/{userId}/assistants"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_assistant_create(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userAssistantCreate
        method: POST
        path: /users/{userId}/assistants
        summary: Add assistants
        """
        endpoint = f"{self._base_url}/users/{userId}/assistants"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_assistants_delete(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userAssistantsDelete
        method: DELETE
        path: /users/{userId}/assistants
        summary: Delete user assistants
        """
        endpoint = f"{self._base_url}/users/{userId}/assistants"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_assistant_delete(self, userId: Optional[Any] = None, assistantId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userAssistantDelete
        method: DELETE
        path: /users/{userId}/assistants/{assistantId}
        summary: Delete a user assistant
        """
        endpoint = f"{self._base_url}/users/{userId}/assistants/{assistantId}"
        params = { 'userId': userId, 'assistantId': assistantId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_collaboration_devices(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: listCollaborationDevices
        method: GET
        path: /users/{userId}/collaboration_devices
        summary: List a user's collaboration devices
        """
        endpoint = f"{self._base_url}/users/{userId}/collaboration_devices"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_collaboration_device(self, userId: Optional[Any] = None, collaborationDeviceId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getCollaborationDevice
        method: GET
        path: /users/{userId}/collaboration_devices/{collaborationDeviceId}
        summary: Get collaboration device detail
        """
        endpoint = f"{self._base_url}/users/{userId}/collaboration_devices/{collaborationDeviceId}"
        params = { 'userId': userId, 'collaborationDeviceId': collaborationDeviceId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_email_update(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userEmailUpdate
        method: PUT
        path: /users/{userId}/email
        summary: Update a user's email
        """
        endpoint = f"{self._base_url}/users/{userId}/email"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def getmeetingsummarytemplates(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getmeetingsummarytemplates
        method: GET
        path: /users/{userId}/meeting_summary_templates
        summary: Get meeting summary templates
        """
        endpoint = f"{self._base_url}/users/{userId}/meeting_summary_templates"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_meeting_templates(self, userId: Optional[Any] = None, meetingTemplateId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserMeetingTemplates
        method: GET
        path: /users/{userId}/meeting_templates/{meetingTemplateId}
        summary: Get meeting template detail
        """
        endpoint = f"{self._base_url}/users/{userId}/meeting_templates/{meetingTemplateId}"
        params = { 'userId': userId, 'meetingTemplateId': meetingTemplateId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_password(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userPassword
        method: PUT
        path: /users/{userId}/password
        summary: Update a user's password
        """
        endpoint = f"{self._base_url}/users/{userId}/password"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_permission(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userPermission
        method: GET
        path: /users/{userId}/permissions
        summary: Get user permissions
        """
        endpoint = f"{self._base_url}/users/{userId}/permissions"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_picture(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userPicture
        method: POST
        path: /users/{userId}/picture
        summary: Upload a user's profile picture
        """
        endpoint = f"{self._base_url}/users/{userId}/picture"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_picture_delete(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userPictureDelete
        method: DELETE
        path: /users/{userId}/picture
        summary: Delete a user's profile picture
        """
        endpoint = f"{self._base_url}/users/{userId}/picture"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_user_presence_status(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: getUserPresenceStatus
        method: GET
        path: /users/{userId}/presence_status
        summary: Get a user presence status
        """
        endpoint = f"{self._base_url}/users/{userId}/presence_status"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_presence_status(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: updatePresenceStatus
        method: PUT
        path: /users/{userId}/presence_status
        summary: Update a user's presence status
        """
        endpoint = f"{self._base_url}/users/{userId}/presence_status"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_schedulers(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSchedulers
        method: GET
        path: /users/{userId}/schedulers
        summary: List user schedulers
        """
        endpoint = f"{self._base_url}/users/{userId}/schedulers"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_schedulers_delete(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSchedulersDelete
        method: DELETE
        path: /users/{userId}/schedulers
        summary: Delete user schedulers
        """
        endpoint = f"{self._base_url}/users/{userId}/schedulers"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_scheduler_delete(self, userId: Optional[Any] = None, schedulerId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSchedulerDelete
        method: DELETE
        path: /users/{userId}/schedulers/{schedulerId}
        summary: Delete a scheduler
        """
        endpoint = f"{self._base_url}/users/{userId}/schedulers/{schedulerId}"
        params = { 'userId': userId, 'schedulerId': schedulerId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_settings(self, userId: Optional[Any] = None, login_type: Optional[Any] = None, option: Optional[Any] = None, custom_query_fields: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSettings
        method: GET
        path: /users/{userId}/settings
        summary: Get user settings
        """
        endpoint = f"{self._base_url}/users/{userId}/settings"
        params = { 'userId': userId, 'login_type': login_type, 'option': option, 'custom_query_fields': custom_query_fields }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_settings_update(self, userId: Optional[Any] = None, option: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSettingsUpdate
        method: PATCH
        path: /users/{userId}/settings
        summary: Update user settings
        """
        endpoint = f"{self._base_url}/users/{userId}/settings"
        params = { 'userId': userId, 'option': option }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def upload_v_buser(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: uploadVBuser
        method: POST
        path: /users/{userId}/settings/virtual_backgrounds
        summary: Upload Virtual Background files
        """
        endpoint = f"{self._base_url}/users/{userId}/settings/virtual_backgrounds"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def del_user_vb(self, userId: Optional[Any] = None, file_ids: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: delUserVB
        method: DELETE
        path: /users/{userId}/settings/virtual_backgrounds
        summary: Delete Virtual Background files
        """
        endpoint = f"{self._base_url}/users/{userId}/settings/virtual_backgrounds"
        params = { 'userId': userId, 'file_ids': file_ids }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_status(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userStatus
        method: PUT
        path: /users/{userId}/status
        summary: Update user status
        """
        endpoint = f"{self._base_url}/users/{userId}/status"
        params = { 'userId': userId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_token(self, userId: Optional[Any] = None, type_: Optional[Any] = None, ttl: Optional[Any] = None, meeting_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userToken
        method: GET
        path: /users/{userId}/token
        summary: Get a user's token
        """
        endpoint = f"{self._base_url}/users/{userId}/token"
        params = { 'userId': userId, 'type': type_, 'ttl': ttl, 'meeting_id': meeting_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def user_sso_token_delete(self, userId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: userSSOTokenDelete
        method: DELETE
        path: /users/{userId}/token
        summary: Revoke a user's SSO token
        """
        endpoint = f"{self._base_url}/users/{userId}/token"
        params = { 'userId': userId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def createwhiteboardsarchivefiles(self, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Createwhiteboardsarchivefiles
        method: GET
        path: /whiteboards/sessions
        summary: List whiteboards sessions
        """
        endpoint = f"{self._base_url}/whiteboards/sessions"
        params = { 'page_size': page_size, 'next_page_token': next_page_token, 'from': from_, 'to': to }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def downloadwhiteboardsactivityfile(self, path: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Downloadwhiteboardsactivityfile
        method: GET
        path: /whiteboards/sessions/activity/download/{path}
        summary: Download Whiteboards activity file
        """
        endpoint = f"{self._base_url}/whiteboards/sessions/activity/download/{path}"
        params = { 'path': path }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def listwhiteboardsessionsarchivedfiles(self, seesionId: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listwhiteboardsessionsarchivedfiles
        method: GET
        path: /whiteboards/sessions/{seesionId}
        summary: List whiteboard sessions activities
        """
        endpoint = f"{self._base_url}/whiteboards/sessions/{seesionId}"
        params = { 'seesionId': seesionId, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_whiteboard_collaborator(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAWhiteboardCollaborator
        method: GET
        path: /whiteboards/{whiteboardId}/collaborator
        summary: Get collaborators of a whiteboard
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}/collaborator"
        params = { 'whiteboardId': whiteboardId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def add_a_whiteboard_collaborator(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: AddAWhiteboardCollaborator
        method: POST
        path: /whiteboards/{whiteboardId}/collaborator
        summary: Share a whiteboard to new users or team chat channels.
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}/collaborator"
        params = { 'whiteboardId': whiteboardId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_whiteboard_collaborator(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAWhiteboardCollaborator
        method: PATCH
        path: /whiteboards/{whiteboardId}/collaborator
        summary: Update whiteboard collaborators
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}/collaborator"
        params = { 'whiteboardId': whiteboardId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_whiteboard_collaborator(self, whiteboardId: Optional[Any] = None, collaboratorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAWhiteboardCollaborator
        method: DELETE
        path: /whiteboards/{whiteboardId}/collaborator/{collaboratorId}
        summary: Remove the collaborator from a whiteboard
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}/collaborator/{collaboratorId}"
        params = { 'whiteboardId': whiteboardId, 'collaboratorId': collaboratorId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_whiteboards(self, search_key: Optional[Any] = None, user_id: Optional[Any] = None, date_filter_type: Optional[Any] = None, from_: Optional[Any] = None, to: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, project_id: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListWhiteboards
        method: GET
        path: /whiteboards
        summary: List all whiteboards
        """
        endpoint = f"{self._base_url}/whiteboards"
        params = { 'search_key': search_key, 'user_id': user_id, 'date_filter_type': date_filter_type, 'from': from_, 'to': to, 'page_size': page_size, 'next_page_token': next_page_token, 'project_id': project_id }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def new_whiteboard_create(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: newWhiteboardCreate
        method: POST
        path: /whiteboards
        summary: Create a new whiteboard
        """
        endpoint = f"{self._base_url}/whiteboards"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_a_whiteboard(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetAWhiteboard
        method: GET
        path: /whiteboards/{whiteboardId}
        summary: Get a whiteboard
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}"
        params = { 'whiteboardId': whiteboardId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_whiteboard_metadata(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAWhiteboardMetadata
        method: PUT
        path: /whiteboards/{whiteboardId}
        summary: Update whiteboard basic information
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}"
        params = { 'whiteboardId': whiteboardId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PUT", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_a_whiteboard(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteAWhiteboard
        method: DELETE
        path: /whiteboards/{whiteboardId}
        summary: Delete a whiteboard
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}"
        params = { 'whiteboardId': whiteboardId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def createwhiteboardsexport(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Createwhiteboardsexport
        method: POST
        path: /whiteboards/export
        summary: Create whiteboard export
        """
        endpoint = f"{self._base_url}/whiteboards/export"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def downloadwhiteboardexport(self, taskId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Downloadwhiteboardexport
        method: GET
        path: /whiteboards/export/task/{taskId}
        summary: Download whiteboard export
        """
        endpoint = f"{self._base_url}/whiteboards/export/task/{taskId}"
        params = { 'taskId': taskId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def getwhiteboardexportdatagenerationstatus(self, taskId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getwhiteboardexportdatagenerationstatus
        method: GET
        path: /whiteboards/export/task/{taskId}/status
        summary: Get whiteboard export generation status
        """
        endpoint = f"{self._base_url}/whiteboards/export/task/{taskId}/status"
        params = { 'taskId': taskId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def uploadfileforwhiteboardimport(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Uploadfileforwhiteboardimport
        method: POST
        path: /whiteboards/files
        summary: Upload file for whiteboard import
        """
        endpoint = f"{self._base_url}/whiteboards/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def downloadembeddedwhiteboardfile(self, whiteboardId: Optional[Any] = None, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Downloadembeddedwhiteboardfile
        method: GET
        path: /whiteboards/{whiteboardId}/files/{fileId}
        summary: Download Imported Whiteboard File
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}/files/{fileId}"
        params = { 'whiteboardId': whiteboardId, 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_whiteboard_import(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CreateWhiteboardImport
        method: POST
        path: /whiteboards/import
        summary: Create a new whiteboard by import
        """
        endpoint = f"{self._base_url}/whiteboards/import"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def get_whiteboardimportstatus(self, taskId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: GetWhiteboardimportstatus
        method: GET
        path: /whiteboards/import/{taskId}/status
        summary: Get whiteboard import status 
        """
        endpoint = f"{self._base_url}/whiteboards/import/{taskId}/status"
        params = { 'taskId': taskId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def listallprojects(self, search_key: Optional[Any] = None, user_id: Optional[Any] = None, page_size: Optional[Any] = None, next_page_token: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Listallprojects
        method: GET
        path: /whiteboards/projects
        summary: List all projects
        """
        endpoint = f"{self._base_url}/whiteboards/projects"
        params = { 'search_key': search_key, 'user_id': user_id, 'page_size': page_size, 'next_page_token': next_page_token }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def createproject(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Createproject
        method: POST
        path: /whiteboards/projects
        summary: Create a new project
        """
        endpoint = f"{self._base_url}/whiteboards/projects"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getaproject(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getaproject
        method: GET
        path: /whiteboards/projects/{projectId}
        summary: Get a project
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}"
        params = { 'projectId': projectId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def deleteproject(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Deleteproject
        method: DELETE
        path: /whiteboards/projects/{projectId}
        summary: Delete a project
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}"
        params = { 'projectId': projectId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def updateproject(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Updateproject
        method: PATCH
        path: /whiteboards/projects/{projectId}
        summary: Update project basic information
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}"
        params = { 'projectId': projectId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def getcollaboratorsofaproject(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getcollaboratorsofaproject
        method: GET
        path: /whiteboards/projects/{projectId}/collaborators
        summary: Get collaborators of a project
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}/collaborators"
        params = { 'projectId': projectId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def shareaprojecttonewusers(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Shareaprojecttonewusers
        method: POST
        path: /whiteboards/projects/{projectId}/collaborators
        summary: Share a project to new users
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}/collaborators"
        params = { 'projectId': projectId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def updateprojectcollaborators(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Updateprojectcollaborators
        method: PATCH
        path: /whiteboards/projects/{projectId}/collaborators
        summary: Update project collaborators
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}/collaborators"
        params = { 'projectId': projectId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def removethecollaboratorfromaproject(self, projectId: Optional[Any] = None, collaboratorId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Removethecollaboratorfromaproject
        method: DELETE
        path: /whiteboards/projects/{projectId}/collaborators/{collaboratorId}
        summary: Remove the collaborator from a project
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}/collaborators/{collaboratorId}"
        params = { 'projectId': projectId, 'collaboratorId': collaboratorId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def movewhiteboardstoproject(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Movewhiteboardstoproject
        method: POST
        path: /whiteboards/projects/{projectId}/whiteboards
        summary: Move whiteboards to a project
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}/whiteboards"
        params = { 'projectId': projectId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def removewhiteboardsfromaproject(self, projectId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Removewhiteboardsfromaproject
        method: DELETE
        path: /whiteboards/projects/{projectId}/whiteboards
        summary: Remove whiteboards from a project
        """
        endpoint = f"{self._base_url}/whiteboards/projects/{projectId}/whiteboards"
        params = { 'projectId': projectId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def update_a_whiteboard_share_setting(self, whiteboardId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: UpdateAWhiteboardShareSetting
        method: PATCH
        path: /whiteboards/{whiteboardId}/share_setting
        summary: Update whiteboard share setting
        """
        endpoint = f"{self._base_url}/whiteboards/{whiteboardId}/share_setting"
        params = { 'whiteboardId': whiteboardId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def create_doc(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: CreateDoc
        method: POST
        path: /docs/files
        summary: Create a new file
        """
        endpoint = f"{self._base_url}/docs/files"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def query_file_metadata(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: QueryFileMetadata
        method: GET
        path: /docs/files/{fileId}
        summary: Get metadata of a file
        """
        endpoint = f"{self._base_url}/docs/files/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def delete_file(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: DeleteFile
        method: DELETE
        path: /docs/files/{fileId}
        summary: Delete a file
        """
        endpoint = f"{self._base_url}/docs/files/{fileId}"
        params = { 'fileId': fileId }
        body = None
        body = None
        return await self._rest.request(
            "DELETE", endpoint, params=params, body=body, timeout=timeout
        )


    async def modify_metadata(self, fileId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ModifyMetadata
        method: PATCH
        path: /docs/files/{fileId}
        summary: Modify metadata of a file
        """
        endpoint = f"{self._base_url}/docs/files/{fileId}"
        params = { 'fileId': fileId }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "PATCH", endpoint, params=params, body=body, timeout=timeout
        )


    async def list_all_children(self, fileId: Optional[Any] = None, next_page_token: Optional[Any] = None, page_size: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: ListAllChildren
        method: GET
        path: /docs/files/{fileId}/children
        summary: List all children of a file
        """
        endpoint = f"{self._base_url}/docs/files/{fileId}/children"
        params = { 'fileId': fileId, 'next_page_token': next_page_token, 'page_size': page_size }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )


    async def uploadfilefordocsimportorattachments(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Uploadfilefordocsimportorattachments
        method: POST
        path: /docs/file_uploads
        summary: Create file upload for docs import or attachments
        """
        endpoint = f"{self._base_url}/docs/file_uploads"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def createanewfilebyimport(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Createanewfilebyimport
        method: POST
        path: /docs/imports
        summary: Create a new file by import
        """
        endpoint = f"{self._base_url}/docs/imports"
        params = {  }
        body = None
        # This endpoint accepts a request body.
        body = None
        return await self._rest.request(
            "POST", endpoint, params=params, body=body, timeout=timeout
        )


    async def getdocsfileimportstatus(self, importId: Optional[Any] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        original_operation_id: Getdocsfileimportstatus
        method: GET
        path: /docs/imports/{importId}/status
        summary: Get file import status
        """
        endpoint = f"{self._base_url}/docs/imports/{importId}/status"
        params = { 'importId': importId }
        body = None
        body = None
        return await self._rest.request(
            "GET", endpoint, params=params, body=body, timeout=timeout
        )

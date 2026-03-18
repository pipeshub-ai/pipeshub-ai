# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportArgumentType=false
"""
DocuSign Unified DataSource - Auto-generated API wrapper

Covers all DocuSign APIs:
- eSignature (SDK-based via docusign-esign)
- Admin, Rooms, Click, Monitor, WebForms (HTTP-based)

All eSign methods are synchronous (SDK). All HTTP methods are async.
"""

from __future__ import annotations

from typing import cast

import docusign_esign  # type: ignore[reportMissingImports]
from docusign_esign import ApiClient  # type: ignore[reportMissingImports]

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignResponse
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class DocuSignDataSource:
    """DocuSign Unified DataSource

    Provides wrapper methods for all DocuSign API operations:
    - eSignature: Envelopes, Templates, Users, Folders, Brands (SDK-based, sync)
    - Admin: Organizations, Users, Groups, Permissions (HTTP-based, async)
    - Rooms: Rooms, Documents, Templates, Roles (HTTP-based, async)
    - Click: Clickwraps, Agreements, Service Info (HTTP-based, async)
    - Monitor: Audit stream events (HTTP-based, async)
    - WebForms: Forms, Instances (HTTP-based, async)

    All methods return DocuSignResponse objects.
    """

    # Base URLs for non-eSign APIs
    ADMIN_BASE_URL = "https://api-d.docusign.net/management"
    ROOMS_BASE_URL = "https://demo.rooms.docusign.com/restapi"
    CLICK_BASE_URL = "https://demo.docusign.net/clickapi"
    MONITOR_BASE_URL = "https://lens-d.docusign.net"
    WEBFORMS_BASE_URL = "https://apps-d.docusign.com/api/webforms/v1.1"

    def __init__(self, client: DocuSignClient) -> None:
        """Initialize with DocuSignClient.

        Args:
            client: DocuSignClient instance with configured authentication
        """
        self._client = client
        # eSign SDK
        self._sdk: ApiClient = cast(ApiClient, client.get_client().get_sdk())
        self._account_id: str = client.get_client().get_account_id()
        # Lazy HTTP clients for each API
        self._admin_http: HTTPClient | None = None
        self._rooms_http: HTTPClient | None = None
        self._click_http: HTTPClient | None = None
        self._monitor_http: HTTPClient | None = None
        self._webforms_http: HTTPClient | None = None

        # Lazy SDK API instances
        self._envelopes_api: docusign_esign.EnvelopesApi | None = None
        self._templates_api: docusign_esign.TemplatesApi | None = None
        self._users_api: docusign_esign.UsersApi | None = None
        self._folders_api: docusign_esign.FoldersApi | None = None
        self._accounts_api: docusign_esign.AccountsApi | None = None

    # ---- lazy HTTP client accessors ----

    def _get_admin_http(self) -> HTTPClient:
        if self._admin_http is None:
            self._admin_http = self._client.get_client().get_http_client(self.ADMIN_BASE_URL)
        return self._admin_http

    def _get_rooms_http(self) -> HTTPClient:
        if self._rooms_http is None:
            self._rooms_http = self._client.get_client().get_http_client(self.ROOMS_BASE_URL)
        return self._rooms_http

    def _get_click_http(self) -> HTTPClient:
        if self._click_http is None:
            self._click_http = self._client.get_client().get_http_client(self.CLICK_BASE_URL)
        return self._click_http

    def _get_monitor_http(self) -> HTTPClient:
        if self._monitor_http is None:
            self._monitor_http = self._client.get_client().get_http_client(self.MONITOR_BASE_URL)
        return self._monitor_http

    def _get_webforms_http(self) -> HTTPClient:
        if self._webforms_http is None:
            self._webforms_http = self._client.get_client().get_http_client(self.WEBFORMS_BASE_URL)
        return self._webforms_http

    # ---- lazy SDK API accessors ----

    @property
    def envelopes_api(self) -> docusign_esign.EnvelopesApi:
        if self._envelopes_api is None:
            self._envelopes_api = docusign_esign.EnvelopesApi(self._sdk)
        return self._envelopes_api

    @property
    def templates_api(self) -> docusign_esign.TemplatesApi:
        if self._templates_api is None:
            self._templates_api = docusign_esign.TemplatesApi(self._sdk)
        return self._templates_api

    @property
    def users_api(self) -> docusign_esign.UsersApi:
        if self._users_api is None:
            self._users_api = docusign_esign.UsersApi(self._sdk)
        return self._users_api

    @property
    def folders_api(self) -> docusign_esign.FoldersApi:
        if self._folders_api is None:
            self._folders_api = docusign_esign.FoldersApi(self._sdk)
        return self._folders_api

    @property
    def accounts_api(self) -> docusign_esign.AccountsApi:
        if self._accounts_api is None:
            self._accounts_api = docusign_esign.AccountsApi(self._sdk)
        return self._accounts_api

    # ---- helpers ----

    def get_data_source(self) -> 'DocuSignDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> DocuSignClient:
        """Return the underlying DocuSignClient."""
        return self._client

    @staticmethod
    def _params(**kwargs: object) -> dict[str, object]:
        """Filter out Nones to avoid overriding SDK defaults."""
        out: dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out

    # ---- eSign SDK methods (synchronous) ----

    def list_envelopes(self, from_date: str, to_date: str | None = None, status: str | None = None, search_text: str | None = None, count: str | None = None, start_position: str | None = None, order: str | None = None, order_by: str | None = None, folder_ids: str | None = None) -> DocuSignResponse:
        """List envelopes for the account. from_date is required by the API.  [eSign]"""
        try:
            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position, order=order, order_by=order_by, folder_ids=folder_ids)
            result = self.envelopes_api.list_status_changes(account_id=self._account_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def get_envelope(self, envelope_id: str) -> DocuSignResponse:
        """Get details for a specific envelope.  [eSign]"""
        try:
            result = self.envelopes_api.get_envelope(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def create_envelope(self, envelope_definition: dict[str, object]) -> DocuSignResponse:
        """Create and optionally send a new envelope from an envelope definition dict.  [eSign]"""
        try:
            body = docusign_esign.EnvelopeDefinition(**envelope_definition)
            result = self.envelopes_api.create_envelope(account_id=self._account_id, envelope_definition=body)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def update_envelope(self, envelope_id: str, envelope: dict[str, object]) -> DocuSignResponse:
        """Update an existing envelope (e.g. change status to sent or voided).  [eSign]"""
        try:
            body = docusign_esign.Envelope(**envelope)
            result = self.envelopes_api.update(account_id=self._account_id, envelope_id=envelope_id, envelope=body)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_envelope_documents(self, envelope_id: str) -> DocuSignResponse:
        """List documents in an envelope.  [eSign]"""
        try:
            result = self.envelopes_api.list_documents(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def get_envelope_document(self, envelope_id: str, document_id: str) -> DocuSignResponse:
        """Download a specific document from an envelope.  [eSign]"""
        try:
            result = self.envelopes_api.get_document(account_id=self._account_id, envelope_id=envelope_id, document_id=document_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_envelope_recipients(self, envelope_id: str) -> DocuSignResponse:
        """List recipients for an envelope.  [eSign]"""
        try:
            result = self.envelopes_api.list_recipients(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def get_envelope_audit_events(self, envelope_id: str) -> DocuSignResponse:
        """Get audit trail events for an envelope.  [eSign]"""
        try:
            result = self.envelopes_api.list_audit_events(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_templates(self, count: str | None = None, start_position: str | None = None, search_text: str | None = None, folder: str | None = None, order: str | None = None, order_by: str | None = None) -> DocuSignResponse:
        """List templates for the account.  [eSign]"""
        try:
            params = self._params(count=count, start_position=start_position, search_text=search_text, folder=folder, order=order, order_by=order_by)
            result = self.templates_api.list_templates(account_id=self._account_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def get_template(self, template_id: str) -> DocuSignResponse:
        """Get details for a specific template.  [eSign]"""
        try:
            result = self.templates_api.get(account_id=self._account_id, template_id=template_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_users(self, count: str | None = None, start_position: str | None = None, status: str | None = None, email: str | None = None) -> DocuSignResponse:
        """List users in the account.  [eSign]"""
        try:
            params = self._params(count=count, start_position=start_position, status=status, email=email)
            result = self.users_api.list(account_id=self._account_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def get_user(self, user_id: str) -> DocuSignResponse:
        """Get details for a specific user.  [eSign]"""
        try:
            result = self.users_api.get_information(account_id=self._account_id, user_id=user_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_folders(self) -> DocuSignResponse:
        """List folders in the account.  [eSign]"""
        try:
            result = self.folders_api.list(account_id=self._account_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_folder_items(self, folder_id: str, from_date: str | None = None, to_date: str | None = None, status: str | None = None, search_text: str | None = None, count: str | None = None, start_position: str | None = None) -> DocuSignResponse:
        """List items (envelopes) in a specific folder.  [eSign]"""
        try:
            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position)
            result = self.folders_api.list_items(account_id=self._account_id, folder_id=folder_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_brands(self) -> DocuSignResponse:
        """List brands for the account.  [eSign]"""
        try:
            result = self.accounts_api.list_brands(account_id=self._account_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    def list_custom_fields(self) -> DocuSignResponse:
        """List custom fields for the account.  [eSign]"""
        try:
            result = self.accounts_api.list_custom_fields(account_id=self._account_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

    # ---- HTTP-based methods (async) ----

    async def admin_get_organizations(
        self
    ) -> DocuSignResponse:
        """Get all organizations  [Admin]

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ADMIN_BASE_URL
        url = base_url + "/v2/organizations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_admin_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed admin_get_organizations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute admin_get_organizations")

    async def admin_get_users(
        self,
        org_id: str,
        account_id: str | None = None,
        email: str | None = None,
        start: int | None = None,
        take: int | None = None
    ) -> DocuSignResponse:
        """Get users for an organization  [Admin]

        Args:
            org_id: Organization ID
            account_id: Filter by account ID
            email: Filter by email address
            start: Start index for pagination
            take: Number of results to return

        Returns:
            DocuSignResponse with operation result
        """
        query_params: dict[str, object] = {}
        if account_id is not None:
            query_params['account_id'] = account_id
        if email is not None:
            query_params['email'] = email
        if start is not None:
            query_params['start'] = start
        if take is not None:
            query_params['take'] = take

        base_url = self.ADMIN_BASE_URL
        url = base_url + f"/v2.1/organizations/{org_id}/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self._get_admin_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed admin_get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute admin_get_users")

    async def admin_get_user_profile(
        self,
        org_id: str,
        email: str | None = None
    ) -> DocuSignResponse:
        """Get user profile by email  [Admin]

        Args:
            org_id: Organization ID
            email: Email address to look up

        Returns:
            DocuSignResponse with operation result
        """
        query_params: dict[str, object] = {}
        if email is not None:
            query_params['email'] = email

        base_url = self.ADMIN_BASE_URL
        url = base_url + f"/v2.1/organizations/{org_id}/users/profile"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self._get_admin_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed admin_get_user_profile" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute admin_get_user_profile")

    async def admin_get_ds_groups(
        self,
        org_id: str,
        account_id: str
    ) -> DocuSignResponse:
        """Get DocuSign groups for an account  [Admin]

        Args:
            org_id: Organization ID
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ADMIN_BASE_URL
        url = base_url + f"/v2.1/organizations/{org_id}/accounts/{account_id}/dsGroups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_admin_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed admin_get_ds_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute admin_get_ds_groups")

    async def admin_get_permission_profiles(
        self,
        org_id: str,
        account_id: str
    ) -> DocuSignResponse:
        """Get permission profiles for an account  [Admin]

        Args:
            org_id: Organization ID
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ADMIN_BASE_URL
        url = base_url + f"/v2.1/organizations/{org_id}/accounts/{account_id}/products/permission_profiles"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_admin_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed admin_get_permission_profiles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute admin_get_permission_profiles")

    async def rooms_get_rooms(
        self,
        account_id: str,
        count: int | None = None,
        startPosition: int | None = None,
        roomStatus: str | None = None
    ) -> DocuSignResponse:
        """Get rooms for the account  [Rooms]

        Args:
            account_id: Account ID
            count: Number of results to return
            startPosition: Start position for pagination
            roomStatus: Filter by room status

        Returns:
            DocuSignResponse with operation result
        """
        query_params: dict[str, object] = {}
        if count is not None:
            query_params['count'] = count
        if startPosition is not None:
            query_params['startPosition'] = startPosition
        if roomStatus is not None:
            query_params['roomStatus'] = roomStatus

        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/rooms"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_rooms" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_rooms")

    async def rooms_get_room(
        self,
        account_id: str,
        room_id: str
    ) -> DocuSignResponse:
        """Get a specific room  [Rooms]

        Args:
            account_id: Account ID
            room_id: Room ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/rooms/{room_id}"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_room" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_room")

    async def rooms_create_room(
        self,
        account_id: str,
        name: str,
        roleId: int,
        transactionSideId: str | None = None
    ) -> DocuSignResponse:
        """Create a new room  [Rooms]

        Args:
            account_id: Account ID
            name: Room name
            roleId: Role ID for the room creator
            transactionSideId: Transaction side ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/rooms"

        body: dict[str, object] = {}
        body['name'] = name
        body['roleId'] = roleId
        if transactionSideId is not None:
            body['transactionSideId'] = transactionSideId

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_create_room" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_create_room")

    async def rooms_delete_room(
        self,
        account_id: str,
        room_id: str
    ) -> DocuSignResponse:
        """Delete a room  [Rooms]

        Args:
            account_id: Account ID
            room_id: Room ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/rooms/{room_id}"

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_delete_room" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_delete_room")

    async def rooms_get_room_documents(
        self,
        account_id: str,
        room_id: str
    ) -> DocuSignResponse:
        """Get documents in a room  [Rooms]

        Args:
            account_id: Account ID
            room_id: Room ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/rooms/{room_id}/documents"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_room_documents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_room_documents")

    async def rooms_get_room_templates(
        self,
        account_id: str,
        count: int | None = None,
        startPosition: int | None = None
    ) -> DocuSignResponse:
        """Get room templates  [Rooms]

        Args:
            account_id: Account ID
            count: Number of results to return
            startPosition: Start position for pagination

        Returns:
            DocuSignResponse with operation result
        """
        query_params: dict[str, object] = {}
        if count is not None:
            query_params['count'] = count
        if startPosition is not None:
            query_params['startPosition'] = startPosition

        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/room_templates"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_room_templates" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_room_templates")

    async def rooms_get_roles(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Get roles for the account  [Rooms]

        Args:
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/roles"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_roles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_roles")

    async def rooms_get_offices(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Get offices for the account  [Rooms]

        Args:
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/offices"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_offices" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_offices")

    async def rooms_get_regions(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Get regions for the account  [Rooms]

        Args:
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/regions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_regions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_regions")

    async def rooms_get_form_libraries(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Get form libraries for the account  [Rooms]

        Args:
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.ROOMS_BASE_URL
        url = base_url + f"/v2/accounts/{account_id}/form_libraries"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_rooms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed rooms_get_form_libraries" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute rooms_get_form_libraries")

    async def click_get_clickwraps(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Get all clickwraps for the account  [Click]

        Args:
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.CLICK_BASE_URL
        url = base_url + f"/v1/accounts/{account_id}/clickwraps"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_click_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed click_get_clickwraps" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute click_get_clickwraps")

    async def click_get_clickwrap(
        self,
        account_id: str,
        clickwrap_id: str
    ) -> DocuSignResponse:
        """Get a specific clickwrap  [Click]

        Args:
            account_id: Account ID
            clickwrap_id: Clickwrap ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.CLICK_BASE_URL
        url = base_url + f"/v1/accounts/{account_id}/clickwraps/{clickwrap_id}"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_click_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed click_get_clickwrap" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute click_get_clickwrap")

    async def click_create_clickwrap(
        self,
        account_id: str,
        clickwrapName: str,
        documents: list[dict[str, object]],
        requireReacceptance: bool | None = None
    ) -> DocuSignResponse:
        """Create a new clickwrap  [Click]

        Args:
            account_id: Account ID
            clickwrapName: Clickwrap name
            documents: Documents for the clickwrap
            requireReacceptance: Whether re-acceptance is required

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.CLICK_BASE_URL
        url = base_url + f"/v1/accounts/{account_id}/clickwraps"

        body: dict[str, object] = {}
        body['clickwrapName'] = clickwrapName
        body['documents'] = documents
        if requireReacceptance is not None:
            body['requireReacceptance'] = requireReacceptance

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self._get_click_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed click_create_clickwrap" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute click_create_clickwrap")

    async def click_delete_clickwrap(
        self,
        account_id: str,
        clickwrap_id: str
    ) -> DocuSignResponse:
        """Delete a clickwrap  [Click]

        Args:
            account_id: Account ID
            clickwrap_id: Clickwrap ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.CLICK_BASE_URL
        url = base_url + f"/v1/accounts/{account_id}/clickwraps/{clickwrap_id}"

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_click_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed click_delete_clickwrap" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute click_delete_clickwrap")

    async def click_get_clickwrap_agreements(
        self,
        account_id: str,
        clickwrap_id: str
    ) -> DocuSignResponse:
        """Get clickwrap agreements (user acceptances)  [Click]

        Args:
            account_id: Account ID
            clickwrap_id: Clickwrap ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.CLICK_BASE_URL
        url = base_url + f"/v1/accounts/{account_id}/clickwraps/{clickwrap_id}/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_click_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed click_get_clickwrap_agreements" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute click_get_clickwrap_agreements")

    async def click_get_service_info(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Get Click service information for the account  [Click]

        Args:
            account_id: Account ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.CLICK_BASE_URL
        url = base_url + f"/v1/accounts/{account_id}/service_information"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_click_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed click_get_service_info" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute click_get_service_info")

    async def monitor_get_stream(
        self,
        cursor: str | None = None,
        limit: int | None = None
    ) -> DocuSignResponse:
        """Get monitor audit stream events  [Monitor]

        Args:
            cursor: Cursor for pagination
            limit: Number of events to return

        Returns:
            DocuSignResponse with operation result
        """
        query_params: dict[str, object] = {}
        if cursor is not None:
            query_params['cursor'] = cursor
        if limit is not None:
            query_params['limit'] = limit

        base_url = self.MONITOR_BASE_URL
        url = base_url + "/api/v2.0/datasets/monitor/stream"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self._get_monitor_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed monitor_get_stream" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute monitor_get_stream")

    async def webforms_list_forms(
        self,
        account_id: str,
        search: str | None = None,
        state: str | None = None,
        status: str | None = None
    ) -> DocuSignResponse:
        """List web forms for the account  [Webforms]

        Args:
            account_id: Account ID
            search: Search filter
            state: Filter by form state
            status: Filter by form status

        Returns:
            DocuSignResponse with operation result
        """
        query_params: dict[str, object] = {}
        if search is not None:
            query_params['search'] = search
        if state is not None:
            query_params['state'] = state
        if status is not None:
            query_params['status'] = status

        base_url = self.WEBFORMS_BASE_URL
        url = base_url + f"/accounts/{account_id}/forms"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self._get_webforms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed webforms_list_forms" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute webforms_list_forms")

    async def webforms_get_form(
        self,
        account_id: str,
        form_id: str
    ) -> DocuSignResponse:
        """Get a specific web form  [Webforms]

        Args:
            account_id: Account ID
            form_id: Form ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.WEBFORMS_BASE_URL
        url = base_url + f"/accounts/{account_id}/forms/{form_id}"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_webforms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed webforms_get_form" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute webforms_get_form")

    async def webforms_list_instances(
        self,
        account_id: str,
        form_id: str
    ) -> DocuSignResponse:
        """List instances of a web form  [Webforms]

        Args:
            account_id: Account ID
            form_id: Form ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.WEBFORMS_BASE_URL
        url = base_url + f"/accounts/{account_id}/forms/{form_id}/instances"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_webforms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed webforms_list_instances" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute webforms_list_instances")

    async def webforms_get_instance(
        self,
        account_id: str,
        form_id: str,
        instance_id: str
    ) -> DocuSignResponse:
        """Get a specific web form instance  [Webforms]

        Args:
            account_id: Account ID
            form_id: Form ID
            instance_id: Instance ID

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.WEBFORMS_BASE_URL
        url = base_url + f"/accounts/{account_id}/forms/{form_id}/instances/{instance_id}"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self._get_webforms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed webforms_get_instance" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute webforms_get_instance")

    async def webforms_create_instance(
        self,
        account_id: str,
        form_id: str,
        clientUserId: str | None = None,
        tags: list[str] | None = None,
        returnUrl: str | None = None
    ) -> DocuSignResponse:
        """Create a new web form instance  [Webforms]

        Args:
            account_id: Account ID
            form_id: Form ID
            clientUserId: Client user ID
            tags: Tags for the instance
            returnUrl: Return URL after form completion

        Returns:
            DocuSignResponse with operation result
        """
        base_url = self.WEBFORMS_BASE_URL
        url = base_url + f"/accounts/{account_id}/forms/{form_id}/instances"

        body: dict[str, object] = {}
        if clientUserId is not None:
            body['clientUserId'] = clientUserId
        if tags is not None:
            body['tags'] = tags
        if returnUrl is not None:
            body['returnUrl'] = returnUrl

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self._get_webforms_http().execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DocuSignResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed webforms_create_instance" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="Failed to execute webforms_create_instance")

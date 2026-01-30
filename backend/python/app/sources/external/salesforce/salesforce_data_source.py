"""
AUTO-GENERATED SALESFORCE DATA SOURCE - DO NOT MODIFY MANUALLY

This module provides a comprehensive data source for Salesforce APIs including:
- Platform APIs (Core CRM, SOQL, SOSL, Composite, Bulk API v2)
- Commerce B2B/D2C APIs
- Commerce B2C APIs (SCAPI)
- Marketing Cloud APIs
- Data 360 APIs (formerly Data Cloud)
- CRM Analytics Connect API (Wave)
- Messaging for In-App and Web (MIAW) API

Generated from Salesforce Postman collections.
"""

from typing import Any, Dict, Optional

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.salesforce.salesforce import (
    SalesforceClient,
    SalesforceResponse,
)

HTTP_SUCCESS = 300
HTTP_NO_CONTENT = 204


class SalesforceDataSource:
    """
    Comprehensive Salesforce API Data Source.
    Provides access to multiple Salesforce API domains:
    - Core CRM (Accounts, Contacts, Leads, Opportunities, Cases)
    - Query & Search (SOQL, SOSL)
    - Composite API (Batch, Tree, Collections)
    - Bulk API v2 (Large data operations)
    - Commerce B2B/D2C (Webstores, Carts, Checkouts, Orders)
    - Commerce B2C/SCAPI (Shopper APIs)
    - Marketing Cloud (Campaigns, Journeys)
    - Data 360 (Customer profiles, Metadata)
    - CRM Analytics (Dashboards, Datasets, Dataflows)
    - Messaging (Conversations, Messages)
    """

    def __init__(self, client: SalesforceClient) -> None:
        """
        Initialize the Salesforce data source.
        Args:
            client: SalesforceClient instance for authentication and requests
        """
        self.client = client.get_client()
        self.base_url = client.get_base_url()

    def _build_url(self, path: str) -> str:
        """Build full URL from path."""
        # Remove leading slash if base_url already has trailing slash
        if self.base_url.endswith("/") and path.startswith("/"):
            path = path[1:]
        return f"{self.base_url}{path}"

    def _build_params(self, **kwargs) -> Dict[str, Any]:
        """Build query parameters, filtering out None values."""
        return {k: v for k, v in kwargs.items() if v is not None}

    async def _execute_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        content_type: str = "application/json"
    ) -> SalesforceResponse:
        """Execute an HTTP request and return SalesforceResponse."""
        url = self._build_url(path)

        headers = self.client.headers.copy()
        if content_type:
            headers["Content-Type"] = content_type

        # Ensure params are strings
        query_params = {}
        if params:
            query_params = {k: str(v) if not isinstance(v, str) else v for k, v in params.items()}

        # USE THE ALIAS "query" instead of "query_params"
        request = HTTPRequest(
            method=method,
            url=url,
            headers=headers,
            query=query_params,  # Changed from query_params to query (the alias!)
            body=body
        )

        try:
            response = await self.client.execute(request)

            # Call the methods since they're callable
            if response.status < HTTP_SUCCESS:
                response_text = response.text() if callable(response.text) else response.text
                if response_text and response.status != HTTP_NO_CONTENT:
                    data = response.json() if callable(response.json) else response.json
                else:
                    data = {}

                return SalesforceResponse(
                    success=True,
                    data=data
                )
            else:
                error_text = response.text() if callable(response.text) else response.text
                return SalesforceResponse(
                    success=False,
                    error=f"HTTP {response.status}",
                    message=error_text
                )
        except Exception as e:
            return SalesforceResponse(
                success=False,
                error=str(e)
            )

    # ========================================================================
    # ACTIONS ENDPOINTS
    # ========================================================================

    async def get_action_collection(self, entityid: str) -> SalesforceResponse:
        """Retrieve a collection of Salesforce actions available to a CRM Analytics user. For additional information, see the Actions Resource.

        HTTP GET: /wave/actions/{entityId}

        Args:
            entityid: Path parameter: entityId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/actions/{entityid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # ASSET ENDPOINTS
    # ========================================================================

    async def get_schedule(self, assetid: str) -> SalesforceResponse:
        """Retrieves the schedule for an asset. For additional information, see the Schedule Resource.

        HTTP GET: /wave/asset/{assetId}/schedule

        Args:
            assetid: Path parameter: assetId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/asset/{assetid}/schedule"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dependency(self, assetid: str) -> SalesforceResponse:
        """Get a Dependencies resource representation. For additional information, see the Dependencies Resource.

        HTTP GET: /wave/dependencies/{assetId}

        Args:
            assetid: Path parameter: assetId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dependencies/{assetid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # AUTH ENDPOINTS
    # ========================================================================

    async def jwt_bearer_token_flow(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """JWT Auth

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def username_password_flow(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Username Password Flow

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def user_agent_flow(
        self,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        display: str,
        login_hint: str,
        nonce: str
    ) -> SalesforceResponse:
        """User Agent Flow

        HTTP GET: /services/oauth2/authorize

        Args:
            response_type: Query parameter: response_type (required)
            client_id: Query parameter: client_id (required)
            redirect_uri: Query parameter: redirect_uri (required)
            display: Query parameter: display (required)
            login_hint: Query parameter: login_hint (required)
            nonce: Query parameter: nonce (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/authorize"
        params = self._build_params(**{"response_type": response_type, "client_id": client_id, "redirect_uri": redirect_uri, "display": display, "login_hint": login_hint, "nonce": nonce})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def web_server_flow_1(
        self,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        scope: str
    ) -> SalesforceResponse:
        """Web Server Flow 1

        HTTP GET: /services/oauth2/authorize

        Args:
            response_type: Query parameter: response_type (required)
            client_id: Query parameter: client_id (required)
            redirect_uri: Query parameter: redirect_uri (required)
            scope: Query parameter: scope (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/authorize"
        params = self._build_params(**{"response_type": response_type, "client_id": client_id, "redirect_uri": redirect_uri, "scope": scope})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def web_server_flow_2(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Web Server Flow 2

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def client_credentials_flow(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Client Credentials Flow

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def client_credentials_flow_basic_authorization_header(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Client Credentials Flow - basic authorization header

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def device_flow_1(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Device Flow 1

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def device_flow_2(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Device Flow 2

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def asset_token_flow(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Asset Token Flow

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def refresh_token(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Refresh Token

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def revoke_token(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Revoke Token

        HTTP POST: /services/oauth2/revoke

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/revoke"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def user_info(self) -> SalesforceResponse:
        """User Info

        HTTP GET: /services/oauth2/userinfo

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/userinfo"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def open_id_connect_discovery_endpoint(self) -> SalesforceResponse:
        """OpenID Connect Discovery Endpoint

        HTTP GET: /.well-known/openid-configuration

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/.well-known/openid-configuration"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def authentication_configuration_endpoint(self) -> SalesforceResponse:
        """Authentication Configuration Endpoint

        HTTP GET: /.well-known/auth-configuration

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/.well-known/auth-configuration"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def open_id_connect_token_introspection_endpoint(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """OpenID Connect Token Introspection Endpoint

        HTTP POST: /services/oauth2/introspect

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/introspect"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def open_id_connect_dynamic_client_registration_endpoint(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """OpenID Connect Dynamic Client Registration Endpoint

        HTTP POST: /services/oauth2/register

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/register"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def salesforce_keys(self) -> SalesforceResponse:
        """Salesforce Keys

        HTTP GET: /id/keys

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/id/keys"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def id_token(self, orgid: str, userid: str) -> SalesforceResponse:
        """ID Token

        HTTP GET: /id/{_orgId}/{_userId}

        Args:
            orgid: Path parameter: {_orgId
            userid: Path parameter: {_userId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/id/{orgid}/{userid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def generate_access_token_for_unauthenticated_user(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Generates an access token for an unverified “guest” user.

        HTTP POST: /iamessage/api/v2/authorization/unauthenticated/access-token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/iamessage/api/v2/authorization/unauthenticated/access-token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def generate_access_token_for_authenticated_user(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Generates an access token. Use the access token as a bearer token to make requests to the Messaging for In-App and Web APIs.

        HTTP POST: /iamessage/api/v2/authorization/authenticated/access-token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/iamessage/api/v2/authorization/authenticated/access-token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def revoke_token_1(self) -> SalesforceResponse:
        """Revokes an access token that was generated for an authenticated user.

        HTTP DELETE: /iamessage/api/v2/authorization/authenticated/access-token

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/iamessage/api/v2/authorization/authenticated/access-token"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def generate_continuation_token(self) -> SalesforceResponse:
        """Generates a JSON Web Token (JWT). This endpoint supports web users using multiple tabs, and it doesn't extend the JWT expiration time.

        HTTP GET: /iamessage/api/v2/authorization/continuation-access-token

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/iamessage/api/v2/authorization/continuation-access-token"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def complete_postman_authentication(self) -> SalesforceResponse:
        """**⚠️ Postman hack:** this request is actually **not** part of the Data Cloud API. It's a hack that lets us quickly retrieve the Data Cloud tenant access token thanks to post-request scripts.

        HTTP GET: /services/oauth2/userinfo

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/userinfo"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # AUTH EXTERNAL CLIENT APPS CREDENTIALS ENDPOINTS
    # ========================================================================

    async def collections(self, appid: str, version: str) -> SalesforceResponse:
        """Returns credentials for all consumers associated with an external client app. See OAuth Credentials by App ID.

        HTTP GET: /services/data/v{version}/apps/oauth/credentials/{_appId}

        Args:
            appid: Path parameter: {_appId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/credentials/{appid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def resources(
        self,
        consumerid: str,
        appid: str,
        version: str,
        part: str
    ) -> SalesforceResponse:
        """Returns credentials for a specific consumer. Use parameters to determine whether the request should return the key, the secret, or both key and secret. See OAuth Credentials by Consumer ID.

        HTTP GET: /services/data/v{version}/apps/oauth/credentials/{_appId}/{_consumerId}

        Args:
            consumerid: Path parameter: {_consumerId
            appid: Path parameter: _appId
            version: Path parameter: version
            part: Key, Secret, or KeyAndSecret (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/credentials/{appid}/{consumerid}"
        params = self._build_params(**{"part": part})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # AUTH EXTERNAL CLIENT APPS O AUTH USAGE ENDPOINTS
    # ========================================================================

    async def o_auth_usage(
        self,
        version: str,
        page: str,
        pagesize: str
    ) -> SalesforceResponse:
        """Lists all the external client apps for the current org. See OAuth Usage.

        HTTP GET: /services/data/v{version}/apps/oauth/usage

        Args:
            version: Path parameter: version
            page: Number of the page you want returned. Starts at 0. If you don’t specify a value or if you specify 0, the first page is returned. (required)
            pagesize: Number of usage entries per page. Defaults to 100. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/usage"
        params = self._build_params(**{"page": page, "pageSize": pagesize})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def o_auth_app_users(
        self,
        appid: str,
        version: str,
        page: str,
        pagesize: str
    ) -> SalesforceResponse:
        """Lists all users for the external client app indicated by the app ID. See OAuth Users by App ID.

        HTTP GET: /services/data/v{version}/apps/oauth/usage/{_appId}/users

        Args:
            appid: Path parameter: {_appId
            version: Path parameter: version
            page: Page number for the usage information. Defaults to 0. (required)
            pagesize: Number of usage entries per page. Defaults to 100. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/usage/{appid}/users"
        params = self._build_params(**{"page": page, "pageSize": pagesize})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def o_auth_app_users_tokens(
        self,
        userid: str,
        appid: str,
        version: str
    ) -> SalesforceResponse:
        """Lists all tokens for a user of the external client app. See Refresh Token by User and App.

        HTTP GET: /services/data/v{version}/apps/oauth/usage/{_appId}/{_userId}/tokens

        Args:
            userid: Path parameter: _userId
            appid: Path parameter: _appId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/usage/{appid}/{userid}/tokens"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def o_auth_user_tokens_revoke(
        self,
        userid: str,
        appid: str,
        version: str
    ) -> SalesforceResponse:
        """Revokes all tokens for a user of the external client app. See Refresh Token by User and App.

        HTTP DELETE: /services/data/v{version}/apps/oauth/usage/{_appId}/{_userId}/tokens

        Args:
            userid: Path parameter: _userId
            appid: Path parameter: _appId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/usage/{appid}/{userid}/tokens"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def o_auth_tokens_revoke(self, appid: str, version: str) -> SalesforceResponse:
        """Revokes all tokens available for the external client app. See Refresh Token by App ID.

        HTTP DELETE: /services/data/v{version}/apps/oauth/usage/{_appId}/tokens

        Args:
            appid: Path parameter: {_appId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/usage/{appid}/tokens"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def o_auth_token_revoke(self, version: str, tokenid: str) -> SalesforceResponse:
        """Revokes one token. See OAuth Refresh Token.

        HTTP DELETE: /services/data/v{version}/apps/oauth/usage/tokens/{_tokenId}

        Args:
            version: Path parameter: {version
            tokenid: Path parameter: _tokenId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/apps/oauth/usage/tokens/{tokenid}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # AUTH HEADLESS IDENTITY API DEMO ENDPOINTS
    # ========================================================================

    async def registration_initialize(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Submits the registration data to the init/registration endpoint and returns a request identifier. At the same time, an OTP is sent out to the end users via email or sms. Registration data is passed...

        HTTP POST: /services/auth/headless/init/registration

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/auth/headless/init/registration"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def registration_authorize(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """After you send your registration data to initialize, this request allows you to process that registration data, and as a part of this request you also verify the users email or sms number. The requ...

        HTTP POST: /services/oauth2/authorize

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/authorize"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def registration_token_exchange(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """This exchanges the auth code returned in the Authorize Request for an access token and refresh token. This follows the standard Auth Code flow/Webserver Code Flow pattern. The Code is the return fo...

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def username_password_login_authorize(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """This request allows named users to use their username and password to get an access token and refresh token from Salesforce in a headless manner. There are two headers that must be included, the fi...

        HTTP POST: /services/oauth2/authorize

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/authorize"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def username_password_login_token_exchange(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """This exchanges the auth code returned in the Authorize Request for an access token and refresh token. This follows the standard Auth Code flow/Webserver Code Flow pattern. The Code is the return fo...

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def forgot_password_initialize(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Forgot Password - Initialize

        HTTP POST: /services/auth/headless/forgot_password

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/auth/headless/forgot_password"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def forgot_password_change_password(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Forgot Password/Change Password is a two step process: In the first step you pass a username and recaptcha token into the endpoint. Salesforce then sends an OPT to the end user. In the second step ...

        HTTP POST: /services/auth/headless/forgot_password

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/auth/headless/forgot_password"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def passwordless_login_initialize(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Submits the passwordless login data to the init/passwordless/login endpoint and returns a request identifier. At the same time, an OTP is sent out to the end users via email or sms. Passwordless lo...

        HTTP POST: /services/auth/headless/init/passwordless/login

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/auth/headless/init/passwordless/login"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def passwordless_login_authorize(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """After you send your passwordless login data to initialize, this request allows you to process that login data, and as a part of this request you also verify the users email or sms number. The reque...

        HTTP POST: /services/oauth2/authorize

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/authorize"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def passwordless_login_token_exchange(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """This exchanges the auth code returned in the Authorize Request for an access token and refresh token. This follows the standard Auth Code flow/Webserver Code Flow pattern. The Code is the return fo...

        HTTP POST: /services/oauth2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def get_user_info(self) -> SalesforceResponse:
        """This requests uses an access token to get user information from Salesforce using the standard `/userinfo` endpoint.

        HTTP GET: /services/oauth2/userinfo

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/userinfo"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def revoke_token_2(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """This calls the standard revoke endpoint, passing in your access token. This endpoint should be called as part of your logout process to invalidate the access and refresh token.

        HTTP POST: /services/oauth2/revoke

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/revoke"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # BULK V1 ENDPOINTS
    # ========================================================================

    async def bulk_create_job(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Bulk Create Job

        HTTP POST: /services/async/{version}/job

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_spec(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Bulk Spec

        HTTP POST: /services/async/{version}/job/{_jobId}/spec

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job/{jobid}/spec"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_create_batch(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Bulk Create Batch

        HTTP POST: /services/async/{version}/job/{_jobId}/batch

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job/{jobid}/batch"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_close_job(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Bulk Close Job

        HTTP POST: /services/async/{version}/job/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_check_batch_status(self, jobid: str, version: str) -> SalesforceResponse:
        """Bulk Check Batch Status

        HTTP GET: /services/async/{version}/job/{_jobId}/batch

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job/{jobid}/batch"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_retrieve_batch_result(
        self,
        batchid: str,
        jobid: str,
        version: str
    ) -> SalesforceResponse:
        """Bulk Retrieve Batch Result

        HTTP GET: /services/async/{version}/job/{_jobId}/batch/{_batchId}/result

        Args:
            batchid: Path parameter: _batchId
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job/{jobid}/batch/{batchid}/result"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_retrieve_batch_result_data(
        self,
        batchid: str,
        jobid: str,
        batchresultid: str,
        version: str
    ) -> SalesforceResponse:
        """Bulk Retrieve Batch Result Data

        HTTP GET: /services/async/{version}/job/{_jobId}/batch/{_batchId}/result/{batchResultId}

        Args:
            batchid: Path parameter: _batchId
            jobid: Path parameter: _jobId
            batchresultid: Path parameter: batchResultId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/async/{version}/job/{jobid}/batch/{batchid}/result/{batchresultid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # BULK V1 QUERY ENDPOINTS
    # ========================================================================

    async def get_job_info_query(self, jobid: str, version: str) -> SalesforceResponse:
        """Get Job Info Query

        HTTP GET: /services/data/v{version}/jobs/query/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_query_result(
        self,
        jobid: str,
        version: str,
        locator: Optional[str] = None,
        maxrecords: Optional[str] = None
    ) -> SalesforceResponse:
        """Get Job Query Result

        HTTP GET: /services/data/v{version}/jobs/query/{_jobId}/results

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            locator: Query parameter: locator (optional)
            maxrecords: Query parameter: maxRecords (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}/results"
        params = self._build_params(**{"locator": locator, "maxRecords": maxrecords})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_query_jobs(
        self,
        version: str,
        ispkchunkingenabled: str,
        jobtype: str,
        concurrencymode: str,
        querylocator: str
    ) -> SalesforceResponse:
        """Get All Query Jobs

        HTTP GET: /services/data/v{version}/jobs/query

        Args:
            version: Path parameter: version
            ispkchunkingenabled: If set to true, the request only returns information about jobs where PK Chunking is enabled (required)
            jobtype: BigObjectIngest Classic V2Query (required)
            concurrencymode: serial parallel (required)
            querylocator: use the value from the nextRecordsUrl from the previous set (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query"
        params = self._build_params(**{"isPkChunkingEnabled": ispkchunkingenabled, "jobType": jobtype, "concurrencyMode": concurrencymode, "queryLocator": querylocator})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # BULK V2 ENDPOINTS
    # ========================================================================

    async def create_job(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create job

        HTTP POST: /services/data/v{version}/jobs/ingest

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upload_job_data(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Upload Job Data

        HTTP PUT: /services/data/v{version}/jobs/ingest/{_jobId}/batches

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}/batches"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def close_or_abort_a_job(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Close or Abort a Job

        HTTP PATCH: /services/data/v{version}/jobs/ingest/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_job(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete Job

        HTTP DELETE: /services/data/v{version}/jobs/ingest/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_info(self, jobid: str, version: str) -> SalesforceResponse:
        """Get Job Info

        HTTP GET: /services/data/v{version}/jobs/ingest/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_jobs(self, version: str) -> SalesforceResponse:
        """Get All Jobs

        HTTP GET: /services/data/v{version}/jobs/ingest

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_successful_record_results(self, jobid: str, version: str) -> SalesforceResponse:
        """Get Job Successful Record Results

        HTTP GET: /services/data/v{version}/jobs/ingest/{_jobId}/successfulResults

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}/successfulResults"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_failed_record_results(self, jobid: str, version: str) -> SalesforceResponse:
        """Get Job Failed Record Results

        HTTP GET: /services/data/v{version}/jobs/ingest/{_jobId}/failedResults

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}/failedResults"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_unprocessed_record_results(self, jobid: str, version: str) -> SalesforceResponse:
        """Get Job Unprocessed Record Results

        HTTP GET: /services/data/v{version}/jobs/ingest/{_jobId}/unprocessedrecords

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/ingest/{jobid}/unprocessedrecords"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # BULK V2 QUERY ENDPOINTS
    # ========================================================================

    async def successful_create_job_query(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Successful Create job Query

        HTTP POST: /services/data/v{version}/jobs/query

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def abort_a_job_query(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Abort a Job Query

        HTTP PATCH: /services/data/v{version}/jobs/query/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def successful_get_job_info_query(self, jobid: str, version: str) -> SalesforceResponse:
        """Successful Get Job Info Query

        HTTP GET: /services/data/v{version}/jobs/query/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_job_query(
        self,
        jobid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete Job Query

        HTTP DELETE: /services/data/v{version}/jobs/query/{_jobId}

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_query_result_1(
        self,
        jobid: str,
        version: str,
        locator: Optional[str] = None,
        maxrecords: Optional[str] = None
    ) -> SalesforceResponse:
        """Get Job Query Result

        HTTP GET: /services/data/v{version}/jobs/query/{_jobId}/results

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version
            locator: Query parameter: locator (optional)
            maxrecords: Query parameter: maxRecords (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}/results"
        params = self._build_params(**{"locator": locator, "maxRecords": maxrecords})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_parallel_results_for_a_query_job(self, jobid: str, version: str) -> SalesforceResponse:
        """Retrieves a set of up to 5 job results pages for parallel download.

        HTTP GET: /services/data/v{version}/jobs/query/{_jobId}/resultPages

        Args:
            jobid: Path parameter: _jobId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query/{jobid}/resultPages"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_query_jobs_1(
        self,
        version: str,
        ispkchunkingenabled: str,
        jobtype: str,
        concurrencymode: str,
        querylocator: str
    ) -> SalesforceResponse:
        """Get All Query Jobs

        HTTP GET: /services/data/v{version}/jobs/query

        Args:
            version: Path parameter: version
            ispkchunkingenabled: If set to true, the request only returns information about jobs where PK Chunking is enabled (required)
            jobtype: BigObjectIngest Classic V2Query (required)
            concurrencymode: serial parallel (required)
            querylocator: use the value from the nextRecordsUrl from the previous set (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/jobs/query"
        params = self._build_params(**{"isPkChunkingEnabled": ispkchunkingenabled, "jobType": jobtype, "concurrencyMode": concurrencymode, "queryLocator": querylocator})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CALCULATED INSIGHTS API ENDPOINTS
    # ========================================================================

    async def query_calculated_insights(
        self,
        ci_name: str,
        data: Optional[Dict[str, Any]] = None,
        batchsize: Optional[str] = None,
        dimensions: Optional[str] = None,
        measures: Optional[str] = None,
        filters: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns the list of data model objects, their fields, and category. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_profile_meta.htm

        HTTP GET: /api/v1/insight/calculated-insights/{ci_name}

        Args:
            ci_name: Path parameter: ci_name
            data: Request body data
            batchsize: Query parameter: batchSize (optional)
            dimensions: Query parameter: dimensions (optional)
            measures: Query parameter: measures (optional)
            filters: Query parameter: filters (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/insight/calculated-insights/{ci_name}"
        params = self._build_params(**{"batchSize": batchsize, "dimensions": dimensions, "measures": measures, "filters": filters})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_individual_calculated_insight(self, ci_name: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The metadata includes the dimension and measure that are part of the calculated insight. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_insights_meta_ci_name.htm

        HTTP GET: /api/v1/insight/metadata/{ci_name}

        Args:
            ci_name: Path parameter: ci_name
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/insight/metadata/{ci_name}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_all_calculated_insights(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Get Salesforce CDP Insight metadata, including Calculated Insight objects, their dimensions and measures. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resour...

        HTTP GET: /api/v1/insight/metadata

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/insight/metadata"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # COMPOSITE ENDPOINTS
    # ========================================================================

    async def composite(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Executes a series of REST API requests in a single call. You can use the output of one request as the input to a subsequent request. The response bodies and HTTP statuses of the requests are return...

        HTTP POST: /services/data/v{version}/composite

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def composite_graph(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Composite graphs provide an enhanced way to perform composite requests, which execute a series of REST API requests in a single call. Regular composite requests allow you to execute a series of RES...

        HTTP POST: /services/data/v{version}/composite/graph

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/graph"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def composite_batch(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Executes up to 25 subrequests in a single request. The response bodies and HTTP statuses of the subrequests in the batch are returned in a single response body. Each subrequest counts against rate ...

        HTTP POST: /services/data/v{version}/composite/batch

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/batch"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_tree(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Creates one or more sObject trees with root records of the specified type. An sObject tree is a collection of nested, parent-child records with a single root record. In the request data, you supply...

        HTTP POST: /services/data/v{version}/composite/tree/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/tree/{sobject_api_name}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_collections_create(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Executes actions on multiple records in one request. Use SObject Collections to reduce the number of round-trips between the client and server. This resource is available in API version 42.0 and la...

        HTTP POST: /services/data/v{version}/composite/sobjects

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/sobjects"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_collections_retrieve(
        self,
        sobject_api_name: str,
        version: str,
        ids: str,
        fields: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Executes actions on multiple records in one request. Use SObject Collections to reduce the number of round-trips between the client and server. This resource is available in API version 42.0 and la...

        HTTP GET: /services/data/v{version}/composite/sobjects/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data
            ids: Query parameter: ids (required)
            fields: Query parameter: fields (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/sobjects/{sobject_api_name}"
        params = self._build_params(**{"ids": ids, "fields": fields})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_collections_update(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Executes actions on multiple records in one request. Use SObject Collections to reduce the number of round-trips between the client and server. This resource is available in API version 42.0 and la...

        HTTP PATCH: /services/data/v{version}/composite/sobjects

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/sobjects"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_collections_upsert(
        self,
        sobject_api_name: str,
        field_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Use a PATCH request with sObject Collections to either create or update (upsert) up to 200 records based on an external ID field. This method returns a list of UpsertResult objects. You can choose ...

        HTTP PATCH: /services/data/v{version}/composite/sobjects/{SOBJECT_API_NAME}/{FIELD_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            field_name: Path parameter: FIELD_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/sobjects/{sobject_api_name}/{field_name}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_collections_delete(
        self,
        sobject_api_name: str,
        version: str,
        ids: str,
        allornone: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Executes actions on multiple records in one request. Use SObject Collections to reduce the number of round-trips between the client and server. This resource is available in API version 42.0 and la...

        HTTP DELETE: /services/data/v{version}/composite/sobjects

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data
            ids: Query parameter: ids (required)
            allornone: Query parameter: allOrNone (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite/sobjects"
        params = self._build_params(**{"ids": ids, "allOrNone": allornone})
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT CHATTER ENDPOINTS
    # ========================================================================

    async def delete_subscription(self, version: str, subscription_id: str) -> SalesforceResponse:
        """Information about the specified subscription. Also used to delete a subscription, for example, to unfollow a record or a topic. A subscription ID is returned as part of the response body for follow...

        HTTP DELETE: /services/data/v{version}/chatter/subscriptions/{SUBSCRIPTION_ID}

        Args:
            version: Path parameter: version
            subscription_id: Path parameter: SUBSCRIPTION_ID

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/subscriptions/{subscription_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT CHATTER COMMENTS ENDPOINTS
    # ========================================================================

    async def comment(
        self,
        version: str,
        comment_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get information about, edit, or delete a comment. To post a comment, use Feed Elements Capability, Comments Items. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/conne...

        HTTP GET: /services/data/v{version}/chatter/comments/{COMMENT_ID}

        Args:
            version: Path parameter: version
            comment_id: Path parameter: COMMENT_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/comments/{comment_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def comment_edit(
        self,
        version: str,
        comment_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get information about, edit, or delete a comment. To post a comment, use Feed Elements Capability, Comments Items. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/conne...

        HTTP PATCH: /services/data/v{version}/chatter/comments/{COMMENT_ID}

        Args:
            version: Path parameter: version
            comment_id: Path parameter: COMMENT_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/comments/{comment_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def comment_delete(self, version: str, comment_id: str) -> SalesforceResponse:
        """Get information about, edit, or delete a comment. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_comments_specific.htm#connect_resources_comments_spe...

        HTTP DELETE: /services/data/v{version}/chatter/comments/{COMMENT_ID}

        Args:
            version: Path parameter: version
            comment_id: Path parameter: COMMENT_ID

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/comments/{comment_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT CHATTER FEED ELEMENTS ENDPOINTS
    # ========================================================================

    async def feed_elements_post_and_search(
        self,
        version: str,
        feedelementtype: str,
        subjectid: str,
        text: str
    ) -> SalesforceResponse:
        """Feed item and comment bodies have a 10,000 character limit. Because this limit can change, we recommend that clients make a describeSObjects() call on the FeedItem or FeedComment object. To determi...

        HTTP POST: /services/data/v{version}/chatter/feed-elements

        Args:
            version: Path parameter: version
            feedelementtype: Feed elements are the top-level objects that a feed contains. The feed element type describes the characteristics of that feed element. One of these values: Bundle—A container of feed elements. A b... (required)
            subjectid: Query parameter: subjectId (required)
            text: Query parameter: text (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feed-elements"
        params = self._build_params(**{"feedElementType": feedelementtype, "subjectId": subjectid, "text": text})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def feed_elements_post_and_search_1(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_feed_element.htm https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_...

        HTTP POST: /services/data/v{version}/chatter/feed-elements

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feed-elements"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def feed_elements_batch_post(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Post a batch of up to 500 feed elements. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_feed_element_batch_post.htm

        HTTP POST: /services/data/v{version}/chatter/feed-elements/batch

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feed-elements/batch"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def feed_element_delete(self, feed_element_id: str, version: str) -> SalesforceResponse:
        """Access, edit, or delete a feed element. Feed items are the only type of feed element that can be edited. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resourc...

        HTTP DELETE: /services/data/v{version}/chatter/feed-elements/{FEED_ELEMENT_ID}

        Args:
            feed_element_id: Path parameter: FEED_ELEMENT_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feed-elements/{feed_element_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def feed_elements_capability_comments_items(
        self,
        feed_element_id: str,
        version: str,
        text: str
    ) -> SalesforceResponse:
        """Access comments for a feed element, or add a comment to a feed element. To upload a binary file to attach to a comment, you must send it in a multipart/form-data request. To send the text of the co...

        HTTP POST: /services/data/v{version}/chatter/feed-elements/{FEED_ELEMENT_ID}/capabilities/comments/items

        Args:
            feed_element_id: Path parameter: FEED_ELEMENT_ID
            version: Path parameter: version
            text: Query parameter: text (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feed-elements/{feed_element_id}/capabilities/comments/items"
        params = self._build_params(**{"text": text})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT CHATTER FEEDS ENDPOINTS
    # ========================================================================

    async def news_feed_elements(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """All feed elements from all groups the context user either owns or is a member of, as well as all files, records, and users the context user follows. Use this resource to get information about feed ...

        HTTP GET: /services/data/v{version}/chatter/feeds/news/me/feed-elements

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feeds/news/me/feed-elements"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def user_profile_feed_elements(self, user_id: str, version: str) -> SalesforceResponse:
        """Returns feed elements created when a user changes records that can be tracked in a feed, feed elements whose parent is the user, and feed elements that mention the user. This feed is different than...

        HTTP GET: /services/data/v{version}/chatter/feeds/user-profile/{USER_ID}/feed-elements

        Args:
            user_id: Path parameter: USER_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feeds/user-profile/{user_id}/feed-elements"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def record_feed_elements(
        self,
        record_group_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns the feed elements for all the records the context user is following, or all the feed elements of the specified recordId. Use this resource to search a feed or to get the feed elements for a...

        HTTP GET: /services/data/v{version}/chatter/feeds/record/{RECORD_GROUP_ID}/feed-elements

        Args:
            record_group_id: Path parameter: RECORD_GROUP_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/feeds/record/{record_group_id}/feed-elements"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT CHATTER GROUPS ENDPOINTS
    # ========================================================================

    async def list_of_groups(self, version: str) -> SalesforceResponse:
        """A list of all the groups in the organization. Get information about groups or create a group. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_groups_L...

        HTTP GET: /services/data/v{version}/chatter/groups

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/groups"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def group_members_private(
        self,
        group_id: str,
        version: str,
        status: Optional[str] = None
    ) -> SalesforceResponse:
        """Request membership in a private group or get the status of requests to a join a private group. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_groups_...

        HTTP GET: /services/data/v{version}/chatter/groups/{GROUP_ID}/members/requests

        Args:
            group_id: Path parameter: GROUP_ID
            version: Path parameter: version
            status: Optional. If you include this parameter, results are filtered to include those that match the specified status. Valid values: Accepted Declined Pending (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/groups/{group_id}/members/requests"
        params = self._build_params(**{"status": status})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_of_groups_post(
        self,
        version: str,
        name: str,
        visibility: str,
        description: str,
        information: str,
        isarchived: str,
        isautoarchivedisabled: str,
        isbroadcast: str,
        canhavechatterguests: str
    ) -> SalesforceResponse:
        """A list of all the groups in the organization. Get information about groups or create a group. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_groups_L...

        HTTP POST: /services/data/v{version}/chatter/groups

        Args:
            version: Path parameter: version
            name: Query parameter: name (required)
            visibility: PrivateAccess—Only members of the group can see posts to this group. PublicAccess—All users within the community can see posts to this group. Unlisted—Reserved for future use. (required)
            description: Query parameter: description (required)
            information: If the group is private, the “Information” section is visible only to members. (required)
            isarchived: Query parameter: isArchived (required)
            isautoarchivedisabled: true if automatic archiving is turned off for the group, false otherwise. Defaults to false. If true, if there are no posts or comments for 90 days the group is archived. (required)
            isbroadcast: true if only group owners and managers can create posts in the group, false otherwise. (required)
            canhavechatterguests: true if this group allows Chatter customers, false otherwise. After this property is set to true, it cannot be set to false. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/groups"
        params = self._build_params(**{"name": name, "visibility": visibility, "description": description, "information": information, "isArchived": isarchived, "isAutoArchiveDisabled": isautoarchivedisabled, "isBroadcast": isbroadcast, "canHaveChatterGuests	": canhavechatterguests})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def group_invites(
        self,
        group_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Invite internal and external users to join a group. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_groups_invites.htm#connect_resources_groups_invites

        HTTP POST: /services/data/v{version}/chatter/groups/group/{GROUP_ID}/invite

        Args:
            group_id: Path parameter: GROUP_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/groups/group/{group_id}/invite"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def group_members(
        self,
        group_id: str,
        version: str,
        userid: str
    ) -> SalesforceResponse:
        """Members of a specified group. Get the members and add a member to a group. To add a member, the context user must be the group owner or moderator. https://developer.salesforce.com/docs/atlas.en-us....

        HTTP POST: /services/data/v{version}/chatter/groups/{GROUP_ID}/members

        Args:
            group_id: Path parameter: GROUP_ID
            version: Path parameter: version
            userid: Query parameter: userId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/groups/{group_id}/members"
        params = self._build_params(**{"userId": userid})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def group_members_private_post(self, group_id: str, version: str) -> SalesforceResponse:
        """Request membership in a private group or get the status of requests to a join a private group. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_groups_...

        HTTP POST: /services/data/v{version}/chatter/groups/{GROUP_ID}/members/requests

        Args:
            group_id: Path parameter: GROUP_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/groups/{group_id}/members/requests"
        params = None
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def group_membership_requests_private(
        self,
        chatter_group_request_id: str,
        version: str,
        status: str,
        responsemessage: str
    ) -> SalesforceResponse:
        """Update the status of a request by a user to join a private group or get information about a request to join a private group. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatte...

        HTTP PATCH: /services/data/v{version}/chatter/group-membership-requests/{CHATTER_GROUP_REQUEST_ID}

        Args:
            chatter_group_request_id: Path parameter: CHATTER_GROUP_REQUEST_ID
            version: Path parameter: version
            status: Query parameter: status (required)
            responsemessage: Query parameter: responseMessage (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/group-membership-requests/{chatter_group_request_id}"
        params = self._build_params(**{"status": status, "responseMessage": responsemessage})
        body = None

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT CHATTER USER ENDPOINTS
    # ========================================================================

    async def user_photo(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Get, post, and crop a user photo. To use an image from the Files page as a user photo, pass the file ID in the fileId property of the request body or in the fileId request parameter. Images uploade...

        HTTP POST: /services/data/v{version}/connect/user-profiles/me/photo

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/user-profiles/me/photo"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def user_messages_general(
        self,
        version: str,
        text: str,
        recipients: str,
        inreplyto: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns all the messages for all the private conversations for the context user. Also used to search across all messages and post a message. To return all private conversations for the context user...

        HTTP POST: /services/data/v{version}/chatter/users/me/messages

        Args:
            version: Path parameter: version
            text: Query parameter: text (required)
            inreplyto: ID of an existing message that identifies which conversation this message is part of. Specify either recipients or inReplyTo. Specify one or the other, not both. (optional)
            recipients: Comma-separated list of User IDs (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/users/me/messages"
        params = self._build_params(**{"text": text, "recipients": recipients, "inReplyTo": inreplyto})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def following(
        self,
        user_id: str,
        version: str,
        filtertype: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns a list of people, groups, records, topics, and files that the specified user is following. Also used to follow records. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/cha...

        HTTP GET: /services/data/v{version}/chatter/users/{USER_ID}/following

        Args:
            user_id: Path parameter: USER_ID
            version: Path parameter: version
            filtertype: Specifies the key prefix to filter the type of objects returned. The key prefix is the three-character prefix code in the object ID. Object IDs are prefixed with three-character codes that specify ... (optional)
            page: Specifies the page number to return. The default value is 0, which returns the first page. (optional)
            pagesize: Specifies the number of items per page. Valid values are between 1 and 1000. If you don't specify a size, the default is 25. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/users/{user_id}/following"
        params = self._build_params(**{"filterType": filtertype, "page": page, "pageSize": pagesize})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def following_post(
        self,
        user_id: str,
        version: str,
        subjectid: str
    ) -> SalesforceResponse:
        """Returns a list of people, groups, records, topics, and files that the specified user is following. Also used to follow records. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/cha...

        HTTP POST: /services/data/v{version}/chatter/users/{USER_ID}/following

        Args:
            user_id: Path parameter: USER_ID
            version: Path parameter: version
            subjectid: Query parameter: subjectId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/chatter/users/{user_id}/following"
        params = self._build_params(**{"subjectId": subjectid})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT EINSTEIN ENDPOINTS
    # ========================================================================

    async def generate_response_based_on_prompt_template(
        self,
        prompt_template_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Generates a response based on the specified prompt template and input parameters (documentation).

        HTTP POST: /services/data/v{version}/einstein/prompt-templates/{PROMPT_TEMPLATE_API_NAME}/generations

        Args:
            prompt_template_api_name: Path parameter: PROMPT_TEMPLATE_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/einstein/prompt-templates/{prompt_template_api_name}/generations"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def run_agent_test(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Generates a response based on the specified prompt template and input parameters (documentation).

        HTTP POST: /services/data/v{version}/einstein/ai-evaluations/runs

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/einstein/ai-evaluations/runs"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_test_status(self, runid: str, version: str) -> SalesforceResponse:
        """Generates a response based on the specified prompt template and input parameters (documentation).

        HTTP GET: /services/data/v{version}/einstein/ai-evaluations/runs/{runId}

        Args:
            runid: Path parameter: runId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/einstein/ai-evaluations/runs/{runid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_test_results(
        self,
        runid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Generates a response based on the specified prompt template and input parameters (documentation).

        HTTP GET: /services/data/v{version}/einstein/ai-evaluations/runs/{runId}/results

        Args:
            runid: Path parameter: runId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/einstein/ai-evaluations/runs/{runid}/results"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT FILE ENDPOINTS
    # ========================================================================

    async def users_files_general(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Upload a file, including references to external files, to the Files home or get information about files a user owns. These files don’t include files shared with a user, files a user follows, or gen...

        HTTP POST: /services/data/v{version}/connect/files/users/me

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/files/users/me"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def file_information(self, file_id: str, version: str) -> SalesforceResponse:
        """Get information about a specified file, including references to external files. Upload a new version of an existing file, including references to external files. Rename a file, move a file to a dif...

        HTTP GET: /services/data/v{version}/connect/files/{FILE_ID}

        Args:
            file_id: Path parameter: FILE_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/files/{file_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def file_content(self, file_id: str, version: str) -> SalesforceResponse:
        """Returns the binary content of the file, including references to external files. The content is streamed as the body of the response. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.met...

        HTTP GET: /services/data/v{version}/connect/files/{FILE_ID}/content

        Args:
            file_id: Path parameter: FILE_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/files/{file_id}/content"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def file_information_delete(self, file_id: str, version: str) -> SalesforceResponse:
        """Get information about a specified file, including references to external files. Upload a new version of an existing file, including references to external files. Rename a file, move a file to a dif...

        HTTP DELETE: /services/data/v{version}/connect/files/{FILE_ID}

        Args:
            file_id: Path parameter: FILE_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/files/{file_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def file_shares(self, file_id: str, version: str) -> SalesforceResponse:
        """Returns information about the objects with which the specified file has been shared. Objects can be users, groups, or records. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chat...

        HTTP GET: /services/data/v{version}/connect/files/{FILE_ID}/file-shares

        Args:
            file_id: Path parameter: FILE_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/files/{file_id}/file-shares"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def files_shares_link(self, file_id: str, version: str) -> SalesforceResponse:
        """A description of a file shared as a link. Create, access, and delete a file’s share link. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_files_shares...

        HTTP PUT: /services/data/v{version}/connect/files/{FILE_ID}/file-shares/link

        Args:
            file_id: Path parameter: FILE_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/files/{file_id}/file-shares/link"
        params = None
        body = None

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE DECISION MATRICES ENDPOINTS
    # ========================================================================

    async def lookup_table(
        self,
        matrixuniquename: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Decision Matrix (Lookup Table) Performs a lookup on decision matrix rows based on the input values provided, and returns the row’s output.Resource ``` /connect/business-rules/decision-matrices/${...

        HTTP POST: /services/data/v{version}/connect/business-rules/decision-matrices/{matrixUniqueName}

        Args:
            matrixuniquename: Path parameter: matrixUniqueName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-matrices/{matrixuniquename}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE DECISION MODELS ENDPOINTS
    # ========================================================================

    async def decision_model_notation_export(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """# Decision Model Notation Export (POST) Export decision matrix data to a file in the DMN (Decision Model Notation) format, an easily readable format for business rules designed by Object Management...

        HTTP POST: /services/data/v{version}/connect/business-rules/decision-models/export

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-models/export"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE DECISION TABLE ENDPOINTS
    # ========================================================================

    async def create_table(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """# Decision Table Definitions (POST) Create a decision table definition. A decision table definition contains all the details required to create a decision table.Resource ``` /connect/business-rules...

        HTTP POST: /services/data/v{version}/connect/business-rules/decision-table/definitions

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-table/definitions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_table(
        self,
        decisiontableid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Decision Table Definitions ( GET) Get details of a decision table definition. Resource ``` /connect/business-rules/decision-table/definitions/${decisionTableId} ``` Resource Example ``` https://y...

        HTTP GET: /services/data/v{version}/connect/business-rules/decision-table/definitions/{decisionTableId}

        Args:
            decisiontableid: Path parameter: decisionTableId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-table/definitions/{decisiontableid}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_table(self, decisiontableid: str, version: str) -> SalesforceResponse:
        """# Decision Table Definitions ( DELETE) Delete a decision table definition associated with a decision table. Resource ``` /connect/business-rules/decision-table/definitions/${decisionTableId} ``` Re...

        HTTP DELETE: /services/data/v{version}/connect/business-rules/decision-table/definitions/{decisionTableId}

        Args:
            decisiontableid: Path parameter: decisionTableId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-table/definitions/{decisiontableid}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_table(
        self,
        decisiontableid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Decision Table Definitions ( DELETE) Delete a decision table definition associated with a decision table. Resource ``` /connect/business-rules/decision-table/definitions/${decisionTableId} ``` Re...

        HTTP PATCH: /services/data/v{version}/connect/business-rules/decision-table/definitions/{decisionTableId}

        Args:
            decisiontableid: Path parameter: decisionTableId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-table/definitions/{decisiontableid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def lookup_table_1(
        self,
        decisiontableid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Decision Table (Lookup Table) Performs a lookup on a decision table.Resource ``` /connect/business-rules/decision-table/${decisionTableId} ``` Resource Example ``` https://yourInstance.salesforce...

        HTTP POST: /services/data/v{version}/connect/business-rules/decision-table/{decisionTableId}

        Args:
            decisiontableid: Path parameter: decisionTableId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-table/{decisiontableid}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE DECISION TABLE LOOKUP ENDPOINTS
    # ========================================================================

    async def invoke(
        self,
        decisiontableid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Invoke Decision Tables Invoke a decision table by passing multiple input conditions within the same request.Resource ``` /connect/business-rules/decision-table/lookup/${decisionTableId} ``` Resou...

        HTTP POST: /services/data/v{version}/connect/business-rules/decision-table/lookup/{decisionTableId}

        Args:
            decisiontableid: Path parameter: decisionTableId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/decision-table/lookup/{decisiontableid}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE EXPRESSION SET ENDPOINTS
    # ========================================================================

    async def expression_set_creation(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.246.0.industries_reference.meta/industries_reference/connect_resources_bre_create_expression_set.htm # Expression Set Creation (POST) Creates an ex...

        HTTP POST: /services/data/v{version}/connect/business-rules/expression-set

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/expression-set"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def expression_set_retrieve(self, expressionsetid: str, version: str) -> SalesforceResponse:
        """# Expression Set Retrieve and Delete (DELETE, GET) Endpoints to read, and delete expression set.Resource ``` /connect/business-rules/expression-set/${expressionSetId} ``` Resource Example ``` https...

        HTTP GET: /services/data/v{version}/connect/business-rules/expression-set/{expressionSetId}

        Args:
            expressionsetid: Path parameter: expressionSetId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/expression-set/{expressionsetid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def expression_set_update(
        self,
        expressionsetid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Expression Set Update (PATCH) Endpoints to read, and update expression set.Resource ``` /connect/business-rules/expression-set/${expressionSetId} ``` Resource Example ``` https://yourInstance.sal...

        HTTP PATCH: /services/data/v{version}/connect/business-rules/expression-set/{expressionSetId}

        Args:
            expressionsetid: Path parameter: expressionSetId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/expression-set/{expressionsetid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def expression_set_delete(self, expressionsetid: str, version: str) -> SalesforceResponse:
        """# Expression Set Retrieve and Delete (DELETE, GET) Endpoints to read, and delete expression set.Resource ``` /connect/business-rules/expression-set/${expressionSetId} ``` Resource Example ``` https...

        HTTP DELETE: /services/data/v{version}/connect/business-rules/expression-set/{expressionSetId}

        Args:
            expressionsetid: Path parameter: expressionSetId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/expression-set/{expressionsetid}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE EXPRESSION SET VERSION ENDPOINTS
    # ========================================================================

    async def retrieve_expression_set_version_dependencies(self, expressionsetversionid: str, version: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.industries_reference.meta/industries_reference/connect_resources_expression_set_dependencies.htm # Expression Set Version Dependencies (GET) Retrie...

        HTTP GET: /services/data/v{version}/connect/business-rules/expression-set/version/{expressionSetVersionId}/dependencies

        Args:
            expressionsetversionid: Path parameter: expressionSetVersionId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/expression-set/version/{expressionsetversionid}/dependencies"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE EXPRESSIONSET ENDPOINTS
    # ========================================================================

    async def expression_set_invocation(
        self,
        expressionsetapiname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.246.0.industries_reference.meta/industries_reference/connect_resources_bre_expression_set.htm **WATCH OUT**: path **expressionSet** has no dash gje...

        HTTP POST: /services/data/v{version}/connect/business-rules/expressionset/{expressionSetAPIName}

        Args:
            expressionsetapiname: Path parameter: expressionSetAPIName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/expressionset/{expressionsetapiname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES BUSINESS RULES ENGINE LOOKUP TABLES ENDPOINTS
    # ========================================================================

    async def retrieve_lookup_tables(
        self,
        version: str,
        searchkey: str,
        usagetype: str,
        lookuptypes: str
    ) -> SalesforceResponse:
        """# Lookup Tables (GET) Retrieve lookup tables.Resource ``` /connect/business-rules/lookup-tables ``` Resource example ``` https://yourInstance.salesforce.com/services/data/v59.0/connect/business-rul...

        HTTP GET: /services/data/v{version}/connect/business-rules/lookup-tables

        Args:
            version: Path parameter: version
            searchkey: Query parameter: searchKey (required)
            usagetype: Query parameter: usageType (required)
            lookuptypes: Optional: Type of lookup table. Valid values are: DecisionMatrix, DecisionTable (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/business-rules/lookup-tables"
        params = self._build_params(**{"searchKey": searchkey, "usageType": usagetype, "lookupTypes": lookuptypes})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES COMMERCE WEBSTORE CART ITEMS ENDPOINTS
    # ========================================================================

    async def add_an_item_to_a_cart(
        self,
        webcartid: str,
        webstoreid: str,
        version: str,
        effectiveaccountid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Add an item to a cart

        HTTP POST: /services/data/v{version}/commerce/webstores/{webstoreId}/carts/{webCartId}/cart-items

        Args:
            webcartid: Path parameter: webCartId
            webstoreid: Path parameter: {webstoreId
            version: Path parameter: version
            data: Request body data
            effectiveaccountid: Query parameter: effectiveAccountId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/webstores/{webstoreid}/carts/{webcartid}/cart-items"
        params = self._build_params(**{"effectiveAccountId": effectiveaccountid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES FUNDRAISING COMMITMENTS ENDPOINTS
    # ========================================================================

    async def create_commitments(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create gift transactions with related new or matched donors, optional transaction designations, and payment instrument metadata. Supports custom fields for the donor account and gift transaction. #...

        HTTP POST: /services/data/v{version}/connect/fundraising/commitments

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/fundraising/commitments"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_pledge_commitments(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create pledge gift commitments with related new or matched donors, optional transaction designations, and payment instrument metadata. Supports custom fields for the donor account, gift commitment ...

        HTTP POST: /services/data/v{version}/connect/fundraising/commitments

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/fundraising/commitments"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_commitments(
        self,
        commitmentid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Modify the schedule or payment instrument metadata on an existing active gift commitment. ## Required Attributes: - {commitmentId} - amount - transactionPeriod (Monthly, Weekly, Yearly, etc.) - sta...

        HTTP PATCH: /services/data/v{version}/connect/fundraising/commitments/{commitmentId}

        Args:
            commitmentid: Path parameter: commitmentId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/fundraising/commitments/{commitmentid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_commitment_payments(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Update the future payment instrument metadata for active gift commitments. ## Required Attributes: - giftCommitmentId - paymentInstrument.type

        HTTP POST: /services/data/v{version}/connect/fundraising/commitments/payment-updates

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/fundraising/commitments/payment-updates"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT INDUSTRIES FUNDRAISING GIFTS ENDPOINTS
    # ========================================================================

    async def create_gifts(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create gift transactions with related new or matched donor, optional transaction designations, and payment instrument metadata. Supports custom fields for the donor account and gift transaction. ##...

        HTTP POST: /services/data/v{version}/connect/fundraising/gifts

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/fundraising/gifts"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_gift_transaction_payments(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Update the gateway and processor metadata for gift transactions. ## Required Attributes: - giftTransactionId - transactionStatus

        HTTP POST: /services/data/v{version}/connect/fundraising/transactions/payment-updates

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/fundraising/transactions/payment-updates"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT MESSAGING ENDPOINTS
    # ========================================================================

    async def get_conversation_entries(self, conversationidentifier: str, version: str) -> SalesforceResponse:
        """Gets in-app messaging conversation entries. https://developer.salesforce.com/docs/atlas.en-us.chatterapi.meta/chatterapi/connect_resources_conversation_entries.htm

        HTTP GET: /services/data/v{version}/connect/conversation/{conversationIdentifier}/entries

        Args:
            conversationidentifier: Path parameter: conversationIdentifier
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/conversation/{conversationidentifier}/entries"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONNECT NAMED CREDENTIAL ENDPOINTS
    # ========================================================================

    async def get_credential(
        self,
        version: str,
        externalcredential: str,
        principalname: str,
        principaltype: str
    ) -> SalesforceResponse:
        """Get a credential.

        HTTP GET: /services/data/v{version}/named-credentials/credential

        Args:
            version: Path parameter: version
            externalcredential: Fully qualified developer name of the external credential. (required)
            principalname: Name of the external credential named principal. (required)
            principaltype: Type of credential principal. Values are: * AwsStsPrincipal * NamedPrincipal * PerUserPrincipal (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/credential"
        params = self._build_params(**{"externalCredential": externalcredential, "principalName": principalname, "principalType": principaltype})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_external_credentials(self, version: str) -> SalesforceResponse:
        """Get external credentials that the user can authenticate to.

        HTTP GET: /services/data/v{version}/named-credentials/external-credentials

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/external-credentials"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_named_credentials(self, version: str) -> SalesforceResponse:
        """Get a list of named credentials in the org.

        HTTP GET: /services/data/v{version}/named-credentials/named-credential-setup

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/named-credential-setup"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_external_credentials_by_developer_name(self, developer_name: str, version: str) -> SalesforceResponse:
        """Get an external credential, including the named credentials and principals associated with it and the type and status of each principal.

        HTTP GET: /services/data/v{version}/named-credentials/external-credentials/{DEVELOPER_NAME}

        Args:
            developer_name: Path parameter: DEVELOPER_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/external-credentials/{developer_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_named_credential_by_developer_name(self, developer_name: str, version: str) -> SalesforceResponse:
        """Get a named credential.

        HTTP GET: /services/data/v{version}/named-credentials/named-credential-setup/{DEVELOPER_NAME}

        Args:
            developer_name: Path parameter: DEVELOPER_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/named-credential-setup/{developer_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_credential(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create a credential.

        HTTP POST: /services/data/v{version}/named-credentials/credential

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/credential"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_external_credential(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create an external credential.

        HTTP POST: /services/data/v{version}/named-credentials/external-credentials

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/external-credentials"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_named_credential(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create a named credential.

        HTTP POST: /services/data/v{version}/named-credentials/named-credential-setup

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/named-credential-setup"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_credential(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Update a credential.

        HTTP PUT: /services/data/v{version}/named-credentials/credential

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/credential"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_external_credential(
        self,
        developer_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update an external credential.

        HTTP PUT: /services/data/v{version}/named-credentials/external-credentials/{DEVELOPER_NAME}

        Args:
            developer_name: Path parameter: DEVELOPER_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/external-credentials/{developer_name}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_named_credential(
        self,
        developer_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update a named credential.

        HTTP PUT: /services/data/v{version}/named-credentials/named-credential-setup/{DEVELOPER_NAME}

        Args:
            developer_name: Path parameter: DEVELOPER_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/named-credential-setup/{developer_name}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_credential(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Delete a credential.

        HTTP DELETE: /services/data/v{version}/named-credentials/credential

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/credential"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_external_credential(self, developer_name: str, version: str) -> SalesforceResponse:
        """Delete an external credential.

        HTTP DELETE: /services/data/v{version}/named-credentials/external-credentials/{DEVELOPER_NAME}

        Args:
            developer_name: Path parameter: DEVELOPER_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/named-credentials/external-credentials/{developer_name}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CONVERSATION ENDPOINTS
    # ========================================================================

    async def create_conversation(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Creates a conversation if it doesn't exist. This API can also pass pre-chat data in the form of routing attributes.

        HTTP POST: /iamessage/api/v2/conversation

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/iamessage/api/v2/conversation"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def close_conversation(
        self,
        conversationid: str,
        esdevelopername: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Closes a conversation between an end user and agent. After a conversation is closed, end users and agents can no longer send messages in this conversation. Instead, they must start a new conversation.

        HTTP DELETE: /iamessage/api/v2/conversation/{conversationId}

        Args:
            conversationid: Path parameter: conversationId
            data: Request body data
            esdevelopername: Query parameter: esDeveloperName (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}"
        params = self._build_params(**{"esDeveloperName": esdevelopername})
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_conversations(
        self,
        inclclosedconvs: Optional[str] = None,
        limit: Optional[str] = None,
        conversationid: Optional[str] = None,
        starttimestamp: Optional[str] = None,
        endtimestamp: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns a list of all conversations for a specific end user. This API is useful to determine if an end user is part of a specific conversation. The list is always returned in chronological ascendin...

        HTTP GET: /iamessage/api/v2/conversation/list

        Args:
            inclclosedconvs: Query parameter: inclClosedConvs (optional)
            limit: Query parameter: limit (optional)
            conversationid: Query parameter: conversationId (optional)
            starttimestamp: Query parameter: startTimestamp (optional)
            endtimestamp: Query parameter: endTimestamp (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/iamessage/api/v2/conversation/list"
        params = self._build_params(**{"inclClosedConvs": inclclosedconvs, "limit": limit, "conversationId": conversationid, "startTimestamp": starttimestamp, "endTimestamp": endtimestamp})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def retrieve_conversation_routing_status(self, conversationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Retrieves the routing status of the specified conversation.

        HTTP GET: /iamessage/api/v2/conversation/{conversationId}

        Args:
            conversationid: Path parameter: conversationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_conversation_entries(self, conversationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Lists conversation entries for the specified conversation.

        HTTP GET: /iamessage/api/v2/conversation/{conversationId}/entries

        Args:
            conversationid: Path parameter: conversationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}/entries"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def send_message(self, conversationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Sends a message from the end user to the agent or bot. The message is sent one at a time and each message can be in a different form, depending on the message type and its payload.

        HTTP POST: /iamessage/api/v2/conversation/{conversationId}/message

        Args:
            conversationid: Path parameter: conversationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}/message"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def server_sent_events(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The Server-Sent Events endpoint is the interface through which clients establish connections to server-sent events to receive messages and events from the server. These messages encompass a variety...

        HTTP GET: /eventrouter/v1/sse

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/eventrouter/v1/sse"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def add_typing_indicator(self, conversationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Adds a typing indicator to notify participants when typing starts or stops. Add a “typing started” indicator to notify the agents or bots when the end user starts typing a message. Add a “typing st...

        HTTP POST: /iamessage/api/v2/conversation/{conversationId}/entry

        Args:
            conversationid: Path parameter: conversationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}/entry"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def send_reading_receipts(self, conversationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Sends a list of conversation entries with “delivery” acknowledgements and “read” receipts. Each conversation entry in the list indicates whether the entry from the agent or bot has been delivered t...

        HTTP POST: /iamessage/api/v2/conversation/{conversationId}/acknowledge-entries

        Args:
            conversationid: Path parameter: conversationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}/acknowledge-entries"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def retrieve_conversation_transcript(self, conversationid: str, esdevelopername: str) -> SalesforceResponse:
        """Generates a transcript of the last 200 conversation entries. The transcript is generated into binary format and saved as a PDF file. Before you continue, verify that the administrator has turned on...

        HTTP GET: /iamessage/api/v2/conversation/{conversationId}/transcript

        Args:
            conversationid: Path parameter: conversationId
            esdevelopername: Query parameter: esDeveloperName (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/iamessage/api/v2/conversation/{conversationid}/transcript"
        params = self._build_params(**{"esDeveloperName": esdevelopername})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CORE CRM BULK ENDPOINTS
    # ========================================================================

    async def bulk_v2_create_job(self, api_version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create a new Bulk API v2 ingest job for large data operations

        HTTP POST: /services/data/v{api_version}/jobs/ingest

        Args:
            api_version: API version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/jobs/ingest"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_v2_get_job(self, api_version: str, job_id: str) -> SalesforceResponse:
        """Get information about a Bulk API v2 job

        HTTP GET: /services/data/v{api_version}/jobs/ingest/{job_id}

        Args:
            api_version: API version
            job_id: Job ID

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/jobs/ingest/{job_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_v2_upload_data(
        self,
        api_version: str,
        job_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Upload CSV data to a Bulk API v2 job

        HTTP PUT: /services/data/v{api_version}/jobs/ingest/{job_id}/batches

        Args:
            api_version: API version
            job_id: Job ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/jobs/ingest/{job_id}/batches"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="text/csv"
        )

    async def bulk_v2_close_job(
        self,
        api_version: str,
        job_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Close a Bulk API v2 job to begin processing

        HTTP PATCH: /services/data/v{api_version}/jobs/ingest/{job_id}

        Args:
            api_version: API version
            job_id: Job ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/jobs/ingest/{job_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def bulk_v2_get_results(self, api_version: str, job_id: str) -> SalesforceResponse:
        """Get successful results from a Bulk API v2 job

        HTTP GET: /services/data/v{api_version}/jobs/ingest/{job_id}/successfulResults

        Args:
            api_version: API version
            job_id: Job ID

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/jobs/ingest/{job_id}/successfulResults"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CORE CRM COMPOSITE ENDPOINTS
    # ========================================================================

    async def composite_batch_request(self, api_version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Execute up to 25 subrequests in a single batch call

        HTTP POST: /services/data/v{api_version}/composite/batch

        Args:
            api_version: API version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/composite/batch"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def composite_tree_create(
        self,
        api_version: str,
        sobject: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Create a tree of related records in a single request

        HTTP POST: /services/data/v{api_version}/composite/tree/{sobject}

        Args:
            api_version: API version
            sobject: Root object API name
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/composite/tree/{sobject}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def collections_create(self, api_version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create up to 200 records in a single request

        HTTP POST: /services/data/v{api_version}/composite/sobjects

        Args:
            api_version: API version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/composite/sobjects"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def collections_update(self, api_version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Update up to 200 records in a single request

        HTTP PATCH: /services/data/v{api_version}/composite/sobjects

        Args:
            api_version: API version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/composite/sobjects"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def collections_delete(
        self,
        api_version: str,
        ids: str,
        allornone: Optional[str] = None
    ) -> SalesforceResponse:
        """Delete up to 200 records in a single request

        HTTP DELETE: /services/data/v{api_version}/composite/sobjects

        Args:
            api_version: API version
            ids: Comma-separated list of record IDs (required)
            allornone: Roll back on any failure (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/composite/sobjects"
        params = self._build_params(**{"ids": ids, "allOrNone": allornone})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CORE CRM CRUD ENDPOINTS
    # ========================================================================

    async def sobject_create(
        self,
        api_version: str,
        sobject: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Create a new record for the specified Salesforce object

        HTTP POST: /services/data/v{api_version}/sobjects/{sobject}

        Args:
            api_version: API version
            sobject: Object API name
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects/{sobject}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sobject_get(
        self,
        api_version: str,
        sobject: str,
        record_id: str,
        fields: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a Salesforce record by ID

        HTTP GET: /services/data/v{api_version}/sobjects/{sobject}/{record_id}

        Args:
            api_version: API version
            sobject: Object API name
            record_id: Record ID
            fields: Comma-separated list of fields to return (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects/{sobject}/{record_id}"
        params = self._build_params(**{"fields": fields})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sobject_update(
        self,
        api_version: str,
        sobject: str,
        record_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update a Salesforce record by ID

        HTTP PATCH: /services/data/v{api_version}/sobjects/{sobject}/{record_id}

        Args:
            api_version: API version
            sobject: Object API name
            record_id: Record ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects/{sobject}/{record_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sobject_delete(
        self,
        api_version: str,
        sobject: str,
        record_id: str
    ) -> SalesforceResponse:
        """Delete a Salesforce record by ID

        HTTP DELETE: /services/data/v{api_version}/sobjects/{sobject}/{record_id}

        Args:
            api_version: API version
            sobject: Object API name
            record_id: Record ID

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects/{sobject}/{record_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sobject_upsert(
        self,
        api_version: str,
        sobject: str,
        external_id_field: str,
        external_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Upsert a Salesforce record using an external ID field

        HTTP PATCH: /services/data/v{api_version}/sobjects/{sobject}/{external_id_field}/{external_id}

        Args:
            api_version: API version
            sobject: Object API name
            external_id_field: External ID field name
            external_id: External ID value
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects/{sobject}/{external_id_field}/{external_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CORE CRM METADATA ENDPOINTS
    # ========================================================================

    async def sobject_describe_global(self, api_version: str) -> SalesforceResponse:
        """Lists all available Salesforce objects and their metadata

        HTTP GET: /services/data/v{api_version}/sobjects

        Args:
            api_version: API version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sobject_describe(self, api_version: str, sobject: str) -> SalesforceResponse:
        """Describes the individual metadata for the specified Salesforce object

        HTTP GET: /services/data/v{api_version}/sobjects/{sobject}/describe

        Args:
            api_version: API version
            sobject: Object API name (e.g., Account)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/sobjects/{sobject}/describe"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CORE CRM QUERY ENDPOINTS
    # ========================================================================

    async def soql_query(self, api_version: str, q: str) -> SalesforceResponse:
        """Execute a SOQL query to retrieve records from Salesforce objects

        HTTP GET: /services/data/v{api_version}/query

        Args:
            api_version: API version (e.g., 59.0)
            q: The SOQL query string (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def soql_query_all(self, api_version: str, q: str) -> SalesforceResponse:
        """Execute a SOQL query including deleted and archived records

        HTTP GET: /services/data/v{api_version}/queryAll

        Args:
            api_version: API version (e.g., 59.0)
            q: The SOQL query string (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/queryAll"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sosl_search(self, api_version: str, q: str) -> SalesforceResponse:
        """Execute a SOSL search across multiple Salesforce objects

        HTTP GET: /services/data/v{api_version}/search

        Args:
            api_version: API version (e.g., 59.0)
            q: The SOSL search string (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/search"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # CORE CRM SYSTEM ENDPOINTS
    # ========================================================================

    async def org_limits(self, api_version: str) -> SalesforceResponse:
        """Get information about Salesforce organization limits

        HTTP GET: /services/data/v{api_version}/limits

        Args:
            api_version: API version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/limits"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def recent_items(self, api_version: str, limit: Optional[str] = None) -> SalesforceResponse:
        """Get recently viewed items for the current user

        HTTP GET: /services/data/v{api_version}/recent

        Args:
            api_version: API version
            limit: Maximum number of items to return (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{api_version}/recent"
        params = self._build_params(**{"limit": limit})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DASHBOARDS ENDPOINTS
    # ========================================================================

    async def get_dashboard_collection(
        self,
        folderid: Optional[str] = None,
        ids: Optional[str] = None,
        mobileonly: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        scope: Optional[str] = None,
        sort: Optional[str] = None,
        templateapiname: Optional[str] = None,
        type_param: Optional[str] = None
    ) -> SalesforceResponse:
        """Get all available dashboards.

        HTTP GET: /wave/dashboards

        Args:
            folderid: Filters the results to include only the contents of a specific folder. The id can be the requesting user’s ID for items in the user’s private folder. (optional)
            ids: Filters the results to include only the dashboards with the specified ids. (optional)
            mobileonly: Filters the results for mobile enabled dashboards only. (optional)
            page: A generated token that indicates the view of dashboard to be returned. (optional)
            pagesize: The number of items to be returned in a single page. Minimum is 1, maximum is 200, and default is 25. (optional)
            q: Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. (optional)
            scope: The type of scope to be applied to the returned collection. (optional)
            sort: The type of sort order to be applied to the returned collection. (optional)
            templateapiname: Filters the results to include only a collection of dashboards created from a specific application template. (optional)
            type_param: The asset type. The default type is Dashboard. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/dashboards"
        params = self._build_params(**{"folderId": folderid, "ids": ids, "mobileOnly": mobileonly, "page": page, "pageSize": pagesize, "q": q, "scope": scope, "sort": sort, "templateApiName": templateapiname, "type": type_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dashboard(self, dashboardidorapiname: str) -> SalesforceResponse:
        """Get a Dashboard resource representation.

        HTTP GET: /wave/dashboards/{dashboardIdOrApiName}

        Args:
            dashboardidorapiname: Path parameter: dashboardIdOrApiName

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dashboards/{dashboardidorapiname}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATA CONNECTORS ENDPOINTS
    # ========================================================================

    async def get_data_connector_status(self, connectoridorapiname: str) -> SalesforceResponse:
        """Retrieves the status for a specific CRM Analytics data connector by ID or developer name. For additional information, see the Data Connector Status Resource.

        HTTP GET: /wave/dataConnectors/{connectorIdOrApiName}/status

        Args:
            connectoridorapiname: Path parameter: connectorIdOrApiName

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dataConnectors/{connectoridorapiname}/status"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_data_connector(self, connectoridorapiname: str, userid: str) -> SalesforceResponse:
        """Retrieve a specific CRM Analytics data connector by ID or developer name. For additional information, see the Data Connector Resource.

        HTTP GET: /wave/dataConnectors/{connectorIdOrApiName}

        Args:
            connectoridorapiname: Path parameter: connectorIdOrApiName
            userid: (Required) The ID or developer name of the data connector. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dataConnectors/{connectoridorapiname}"
        params = self._build_params(**{"userId": userid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_data_connector_collection(
        self,
        category: Optional[str] = None,
        connectortype: Optional[str] = None,
        folderid: Optional[str] = None,
        scope: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a collection of CRM Analytics data connectors. For more information, see the Data Connectors List Resource.

        HTTP GET: /wave/dataConnectors

        Args:
            category: (Optional) Filters the collection to include only data connectors belonging to the specified category. (optional)
            connectortype: (Optional) Filters the collection to include only data connectors belonging to the specified connector type. (optional)
            folderid: (Optional) Filters the collection to only contain data connectors for the specified folder. The ID can be the requesting user's ID for data connectors in the user's private folder. (optional)
            scope: (Optional) Scope type to apply to the collection results. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/dataConnectors"
        params = self._build_params(**{"category": category, "connectorType": connectortype, "folderId": folderid, "scope": scope})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_data_connector_type_collection(self) -> SalesforceResponse:
        """Retrieve a collection of CRM Analytics data connector Types. For additional information, see the Data Connector Types Resource.

        HTTP GET: /wave/dataConnectorTypes

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/dataConnectorTypes"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATA GRAPH API ENDPOINTS
    # ========================================================================

    async def metadata_of_all_data_graphs(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP GET: /api/v1/dataGraph/metadata

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/dataGraph/metadata"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_of_a_data_graph(self, entityname: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP GET: /api/v1/dataGraph/metadata

        Args:
            data: Request body data
            entityname: API name of the data graph for which metadata is being requested. When a data graph is created, the API name is the value set in the DataGraphApiName field. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/dataGraph/metadata"
        params = self._build_params(**{"entityName": entityname})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def query_data_graph_data(
        self,
        datagraphname: str,
        datagraphrecordid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP GET: /api/v1/dataGraph/{dataGraphName}/{dataGraphRecordId}

        Args:
            datagraphname: Path parameter: dataGraphName
            datagraphrecordid: Path parameter: dataGraphRecordId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/dataGraph/{datagraphname}/{datagraphrecordid}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def query_data_graph_data_using_lookup_keys(
        self,
        datagraphname: str,
        lookupkeys: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP GET: /api/v1/dataGraph/{dataGraphName}

        Args:
            datagraphname: Path parameter: dataGraphName
            data: Request body data
            lookupkeys: The API name of the related object included in the data graph and a field name and field ID of that related object, limited to the individual ID field on a specific related data model object. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/dataGraph/{datagraphname}"
        params = self._build_params(**{"lookupKeys": lookupkeys})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATAFLOW JOBS ENDPOINTS
    # ========================================================================

    async def get_dataflow_job_node(self, nodeid: str, dataflowjobid: str) -> SalesforceResponse:
        """Retrieve a Dataflow Job Node resource representation. For more information, see the Dataflow Job Nodes Resource.

        HTTP GET: /wave/dataflowjobs/{dataflowjobId}/nodes/{nodeId}

        Args:
            nodeid: Path parameter: nodeId
            dataflowjobid: Path parameter: dataflowjobId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dataflowjobs/{dataflowjobid}/nodes/{nodeid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataflow_job_node_collection(self, dataflowjobid: str) -> SalesforceResponse:
        """Retrieve a Dataflow Job Node Collection resource representation. For more information, see the Dataflow Job Nodes List Resource.

        HTTP GET: /wave/dataflowjobs/{dataflowjobId}/nodes

        Args:
            dataflowjobid: Path parameter: dataflowjobId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dataflowjobs/{dataflowjobid}/nodes"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataflow_job_collection(
        self,
        dataflowid: Optional[str] = None,
        jobtypes: Optional[str] = None,
        licensetype: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        startedafter: Optional[str] = None,
        startedbefore: Optional[str] = None,
        status: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a collection of all dataflow jobs representation For additional information, see the Dataflow Jobs List Resource.

        HTTP GET: /wave/dataflowjobs

        Args:
            dataflowid: (Optional) Filters the collection to only contain dataflow jobs tied to this specific dataflow. The ID must start with '02K'. (optional)
            jobtypes: (Optional) Filters the collection to only contain dataflow jobs with a specific job type. (optional)
            licensetype: (Optional) The response includes CRM Analytics assets with this license type. The default is EinsteinAnalytics. (optional)
            page: (Optional) Generated token that indicates the view of dataflow jobs to be returned. (optional)
            pagesize: (Optional) Number of items to be returned in a single page. Minimum is 1, maximum is 200, and the default is 25. (optional)
            q: (Optional) Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. If the user’s search query contains quotation marks or... (optional)
            startedafter: (Optional) Filters the collection to only contain dataflow jobs started after the specified date and time. (optional)
            startedbefore: (Optional) Filters the collection to only contain dataflow jobs started before the specified date and time. (optional)
            status: (Optional) Filters the collection to only contain dataflow jobs with a specific runtime status. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/dataflowjobs"
        params = self._build_params(**{"dataflowId": dataflowid, "jobTypes": jobtypes, "licenseType": licensetype, "page": page, "pageSize": pagesize, "q": q, "startedAfter": startedafter, "startedBefore": startedbefore, "status": status})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataflow_job(self, dataflowjobid: str) -> SalesforceResponse:
        """Retrieve a Dataflow Job resource representation. For additional information, see the Dataflow Job Resource.

        HTTP GET: /wave/dataflowjobs/{dataflowjobId}

        Args:
            dataflowjobid: Path parameter: dataflowjobId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dataflowjobs/{dataflowjobid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATAFLOWS ENDPOINTS
    # ========================================================================

    async def get_dataflow_collection(self, q: Optional[str] = None) -> SalesforceResponse:
        """Retrieve a collection of dataflow resource representations. For additional information, see the Dataflows List Resource.

        HTTP GET: /wave/dataflows

        Args:
            q: (Optional) Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. If the user’s search query contains quotation marks or... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/dataflows"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataflow(self, dataflowid: str, historyid: Optional[str] = None) -> SalesforceResponse:
        """Retrieve a Dataflow resource representation. For additional information, see the Dataflow Resource.

        HTTP GET: /wave/dataflows/{dataflowId}

        Args:
            dataflowid: Path parameter: dataflowId
            historyid: (Optional) Request a specific dataflow version. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/dataflows/{dataflowid}"
        params = self._build_params(**{"historyId": historyid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATASETS ENDPOINTS
    # ========================================================================

    async def get_dataset_collection(
        self,
        createdafter: Optional[str] = None,
        createdbefore: Optional[str] = None,
        datasettypes: Optional[str] = None,
        folderid: Optional[str] = None,
        hascurrentonly: Optional[str] = None,
        ids: Optional[str] = None,
        includecurrentversion: Optional[str] = None,
        lastqueriedafter: Optional[str] = None,
        lastqueriedbefore: Optional[str] = None,
        licensetype: Optional[str] = None,
        order: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        scope: Optional[str] = None,
        sort: Optional[str] = None,
        supportsnewdates: Optional[str] = None,
        typeofdataflow: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a collection of dataset resource representations. For additional information, see the Datasets List Resource.

        HTTP GET: /wave/datasets

        Args:
            createdafter: (Optional) Filter the collection to include only datasets created after a specific timestamp. (optional)
            createdbefore: (Optional) Filter the collection to include only datasets created before a specific timestamp. (optional)
            datasettypes: (Optional) Filters the collection to include only datasets of one or more of the specified types. (optional)
            folderid: (Optional) Filters the collection to only contain datasets for the specified folder. The ID can be the requesting user's ID for datasets in the user's private folder. (optional)
            hascurrentonly: (Optional) Filters the collection of datasets to include only those datasets that have a current version. The default is false. (optional)
            ids: (Optional) Filter the collection to include only datasets with the specified IDs. (optional)
            includecurrentversion: (Optional) Includes the current dataset version metadata in the collection. The default is false. (optional)
            lastqueriedafter: (Optional) Filter the collection to include only datasets last queired after a specific timestamp. (optional)
            lastqueriedbefore: (Optional) Filter the collection to include only datasets last queried before a specific timestamp. (optional)
            licensetype: (Optional) The response includes datasets with this license type. The default is EinsteinAnalytics. (optional)
            order: (Optional) Ordering to apply to the collection results. (optional)
            page: (Optional) Generated token that indicates the view of datasets to be returned. (optional)
            pagesize: (Optional) Number of items to be returned in a single page. Minimum is 1, maximum is 200, and the default is 25. (optional)
            q: (Optional) Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. If the user’s search query contains quotation marks or... (optional)
            scope: (Optional) Scope type to apply to the collection results. (optional)
            sort: (Optional) Sort order to apply to the collection results. (optional)
            supportsnewdates: (Optional) Indicates whether to include only datasets that support new dates in the collection (true) or not (false). The default is false. (optional)
            typeofdataflow: (Optional) Filter the collection to include only datasets with the specified type of dataflow. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/datasets"
        params = self._build_params(**{"createdAfter": createdafter, "createdBefore": createdbefore, "datasetTypes": datasettypes, "folderId": folderid, "hasCurrentOnly": hascurrentonly, "ids": ids, "includeCurrentVersion": includecurrentversion, "lastQueriedAfter": lastqueriedafter, "lastQueriedBefore": lastqueriedbefore, "licenseType": licensetype, "order": order, "page": page, "pageSize": pagesize, "q": q, "scope": scope, "sort": sort, "supportsNewDates": supportsnewdates, "typeOfDataflow": typeofdataflow})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataset(self, datasetidorapiname: str) -> SalesforceResponse:
        """Get a dataset resource representation. For additional information, see the Dataset Resource.

        HTTP GET: /wave/datasets/{datasetIdOrApiName}

        Args:
            datasetidorapiname: Path parameter: datasetIdOrApiName

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/datasets/{datasetidorapiname}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataset_coverage(self, datasetidorapiname: str, versionid: str) -> SalesforceResponse:
        """Retrieve the security coverage for a particular dataset version. For additional information, see the Security Coverage Dataset Version Resource.

        HTTP GET: /wave/security/coverage/datasets/{datasetIdOrApiName}/versions/{versionId}

        Args:
            datasetidorapiname: Path parameter: datasetIdOrApiName
            versionid: Path parameter: versionId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/security/coverage/datasets/{datasetidorapiname}/versions/{versionid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATASETS VERSIONS ENDPOINTS
    # ========================================================================

    async def get_dataset_version(self, datasetidorapiname: str, versionid: str) -> SalesforceResponse:
        """Retrieve a dataset version resource representation. For additional information, see the Dataset Version Resource.

        HTTP GET: /wave/datasets/{datasetIdOrApiName}/versions/{versionId}

        Args:
            datasetidorapiname: Path parameter: datasetIdOrApiName
            versionid: Path parameter: versionId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/datasets/{datasetidorapiname}/versions/{versionid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_dataset_version_collection(self, datasetidorapiname: str) -> SalesforceResponse:
        """Get the dataset versions for a particular dataset. For additional information, see the Dataset Versions List Resource.

        HTTP GET: /wave/datasets/{datasetIdOrApiName}/versions

        Args:
            datasetidorapiname: Path parameter: datasetIdOrApiName

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/datasets/{datasetidorapiname}/versions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # DATASETS XMDS ENDPOINTS
    # ========================================================================

    async def get_xmd(
        self,
        datasetidorapiname: str,
        versionid: str,
        xmdtype: str
    ) -> SalesforceResponse:
        """Retrieve the extended metadata of the given type for the given dataset version. For additional information, see the Xmd Resource.

        HTTP GET: /wave/datasets/{datasetIdOrApiName}/versions/{versionId}/xmds/{xmdType}

        Args:
            datasetidorapiname: Path parameter: datasetIdOrApiName
            versionid: Path parameter: versionId
            xmdtype: Path parameter: xmdType

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/datasets/{datasetidorapiname}/versions/{versionid}/xmds/{xmdtype}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EINSTEIN PREDICTION SERVICE ENDPOINTS
    # ========================================================================

    async def predict(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Get available prediction definitions.

        HTTP POST: /services/data/v{version}/smartdatadiscovery/predict

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/smartdatadiscovery/predict"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def prediction_definitions(self, version: str) -> SalesforceResponse:
        """Get available prediction definitions.

        HTTP GET: /services/data/v{version}/smartdatadiscovery/predictionDefinitions

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/smartdatadiscovery/predictionDefinitions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def prediction_definition_metadata(self, prediction_definition_id: str, version: str) -> SalesforceResponse:
        """Get available prediction definitions.

        HTTP GET: /services/data/v{version}/smartdatadiscovery/predictionDefinitions/{PREDICTION_DEFINITION_ID}

        Args:
            prediction_definition_id: Path parameter: PREDICTION_DEFINITION_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/smartdatadiscovery/predictionDefinitions/{prediction_definition_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def prediction_models(self, prediction_definition_id: str, version: str) -> SalesforceResponse:
        """Get available prediction definitions.

        HTTP GET: /services/data/v{version}/smartdatadiscovery/predictionDefinitions/{PREDICTION_DEFINITION_ID}/models

        Args:
            prediction_definition_id: Path parameter: PREDICTION_DEFINITION_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/smartdatadiscovery/predictionDefinitions/{prediction_definition_id}/models"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # END2 END BUYER SHOPPER JOURNEY ENDPOINTS
    # ========================================================================

    async def login_buyer_shopper_soap(self, apiversion: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Authenticate the shopper through the variables _buyerUsername_ and _buyerPassword_. Generated session ID is saved in _accessToken for use in the other calls in the collection.

        HTTP POST: /services/Soap/u/{apiVersion}

        Args:
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/u/{apiversion}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def search_products_by_search_term(
        self,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Uses the variable _productSearchTerm_ to search for products in the webstore. Sets the first returned product in the variable _productId_.

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/search/product-search

        Args:
            webstoreid: Path parameter: webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/search/product-search"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_product(
        self,
        webstoreid: str,
        productid: str,
        apiversion: str,
        effectiveaccountid: Optional[str] = None,
        excludeattributesetinfo: Optional[str] = None,
        excludeentitlementdetails: Optional[str] = None,
        excludefields: Optional[str] = None,
        excludemedia: Optional[str] = None,
        excludeprimaryproductcategory: Optional[str] = None,
        excludeproductsellingmodels: Optional[str] = None,
        excludequantityrule: Optional[str] = None,
        excludevariationinfo: Optional[str] = None,
        fields: Optional[str] = None,
        mediagroups: Optional[str] = None
    ) -> SalesforceResponse:
        """Fetch standard field data, custom field data, and enrichment data for a single Product, identified by the variable _productId_ that was set by the search products request.

        HTTP GET: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/products/{productId}

        Args:
            webstoreid: Path parameter: {webstoreId
            productid: Path parameter: productId
            apiversion: Path parameter: {apiVersion
            effectiveaccountid: Query parameter: effectiveAccountId (optional)
            excludeattributesetinfo: Query parameter: excludeAttributeSetInfo (optional)
            excludeentitlementdetails: Query parameter: excludeEntitlementDetails (optional)
            excludefields: Query parameter: excludeFields (optional)
            excludemedia: Query parameter: excludeMedia (optional)
            excludeprimaryproductcategory: Query parameter: excludePrimaryProductCategory (optional)
            excludeproductsellingmodels: Query parameter: excludeProductSellingModels (optional)
            excludequantityrule: Query parameter: excludeQuantityRule (optional)
            excludevariationinfo: Query parameter: excludeVariationInfo (optional)
            fields: Query parameter: fields (optional)
            mediagroups: Query parameter: mediaGroups (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/products/{productid}"
        params = self._build_params(**{"effectiveAccountId": effectiveaccountid, "excludeAttributeSetInfo": excludeattributesetinfo, "excludeEntitlementDetails": excludeentitlementdetails, "excludeFields": excludefields, "excludeMedia": excludemedia, "excludePrimaryProductCategory": excludeprimaryproductcategory, "excludeProductSellingModels": excludeproductsellingmodels, "excludeQuantityRule": excludequantityrule, "excludeVariationInfo": excludevariationinfo, "fields": fields, "mediaGroups": mediagroups})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_cart_if_needed(
        self,
        webstoreid: str,
        cartid: str,
        apiversion: str
    ) -> SalesforceResponse:
        """Delete the Cart, as identified by the variable __cartId_. This variable is set in the _Create a cart_ request. (Note: This request is not needed the first time it is executed.)

        HTTP DELETE: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/carts/{cartId}

        Args:
            webstoreid: Path parameter: {webstoreId
            cartid: Path parameter: cartId
            apiversion: Path parameter: {apiVersion

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/carts/{cartid}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_a_cart(
        self,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Creates a Cart and sets the variable __cartId_.

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/carts

        Args:
            webstoreid: Path parameter: webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/carts"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def add_item_to_the_cart(
        self,
        webstoreid: str,
        cartid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None,
        productfields: Optional[str] = None
    ) -> SalesforceResponse:
        """Adds a quantity of 5 items (identified by the variable _productId)_ to the cart (identified by the variable __cartId)._

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/carts/{cartId}/cart-items

        Args:
            webstoreid: Path parameter: {webstoreId
            cartid: Path parameter: cartId
            apiversion: Path parameter: {apiVersion
            data: Request body data
            productfields: Query parameter: productFields (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/carts/{cartid}/cart-items"
        params = self._build_params(**{"productFields": productfields})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_start_checkout(
        self,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Starts a checkout for the cart identified by the variable __cartId_. Sets the checkout identifier in the variable __checkoutId_.

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/checkouts

        Args:
            webstoreid: Path parameter: webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/checkouts"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_shipping_address(
        self,
        webstoreid: str,
        buyeraccountid: str,
        apiversion: str,
        addresstype: str,
        sortorder: str,
        excludeunsupportedcountries: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns all of the shipping addresses associated with the buyer account identified through the _buyerAccountId_ variable. Sets the __addressId_ variable to the last created shipping address for tha...

        HTTP GET: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/accounts/{buyerAccountId}/addresses

        Args:
            webstoreid: Path parameter: {webstoreId
            buyeraccountid: Path parameter: buyerAccountId
            apiversion: Path parameter: {apiVersion
            data: Request body data
            addresstype: Query parameter: addressType (required)
            sortorder: Query parameter: sortOrder (required)
            excludeunsupportedcountries: Query parameter: excludeUnsupportedCountries (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/accounts/{buyeraccountid}/addresses"
        params = self._build_params(**{"addressType": addresstype, "sortOrder": sortorder, "excludeUnsupportedCountries": excludeunsupportedcountries})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_shipping_address_if_address_exists(
        self,
        webstoreid: str,
        buyeraccountid: str,
        apiversion: str,
        addressid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Adds a shipping address associated with the buyer account (variable _buyerAccountId_) and stores the address identifier into the variable __addressId_.

        HTTP PATCH: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/accounts/{buyerAccountId}/addresses/{addressId}

        Args:
            webstoreid: Path parameter: {webstoreId
            buyeraccountid: Path parameter: buyerAccountId
            apiversion: Path parameter: {apiVersion
            addressid: Path parameter: addressId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/accounts/{buyeraccountid}/addresses/{addressid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def add_shipping_address_if_address_does_not_exist(
        self,
        webstoreid: str,
        buyeraccountid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Adds a shipping address associated with the buyer account (variable _buyerAccountId_) and stores the address identifier into the variable __addressId_.

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/accounts/{buyerAccountId}/addresses

        Args:
            webstoreid: Path parameter: {webstoreId
            buyeraccountid: Path parameter: buyerAccountId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/accounts/{buyeraccountid}/addresses"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_checkout_with_shipping_address(
        self,
        checkoutid: str,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Sets the ship-to address associated with the checkout (variable _checkoutId) to the_ address found/created in a previous request (variable __addressId)_.

        HTTP PATCH: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/checkouts/{checkoutId}

        Args:
            checkoutid: Path parameter: checkoutId
            webstoreid: Path parameter: {webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/checkouts/{checkoutid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_checkout(
        self,
        checkoutid: str,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Retrieves the checkout (variable __checkoutId_).

        HTTP GET: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/checkouts/{checkoutId}

        Args:
            checkoutid: Path parameter: checkoutId
            webstoreid: Path parameter: {webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/checkouts/{checkoutid}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_a_payment(
        self,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Perform a server-side payment and store the created payment token in the variable __paymentToken_. Note: The sample payment gateway included in the store creation simply returns a dummy token.

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/payments/token

        Args:
            webstoreid: Path parameter: webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/payments/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_checkout_with_payment(
        self,
        checkoutid: str,
        webstoreid: str,
        apiversion: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Applies the payment token (variable __paymentToken_) to the checkout (variable __checkoutId_). Also sets the billing address. Note: No checking will be done for the validity of the billing address ...

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/checkouts/{checkoutId}/payments

        Args:
            checkoutid: Path parameter: checkoutId
            webstoreid: Path parameter: {webstoreId
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/checkouts/{checkoutid}/payments"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_order(
        self,
        webstoreid: str,
        apiversion: str,
        checkoutid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Checkout Payments Action

        HTTP POST: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/checkouts/{_checkoutId}/orders

        Args:
            webstoreid: Path parameter: {webstoreId
            apiversion: Path parameter: {apiVersion
            checkoutid: Path parameter: _checkoutId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/checkouts/{checkoutid}/orders"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_order_summaries(self, webstoreid: str, apiversion: str) -> SalesforceResponse:
        """Get all of the order summaries (i.e., placed orders) from the webstore.

        HTTP GET: /services/data/v{apiVersion}/commerce/webstores/{webstoreId}/order-summaries

        Args:
            webstoreid: Path parameter: webstoreId
            apiversion: Path parameter: {apiVersion

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/commerce/webstores/{webstoreid}/order-summaries"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM CUSTOM CHANNELS ENDPOINTS
    # ========================================================================

    async def list_event_channels(self, version: str, q: str) -> SalesforceResponse:
        """List event channels

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_event_channel(
        self,
        platform_event_channel_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get event channel

        HTTP GET: /services/data/v{version}/tooling/sobjects/PlatformEventChannel/{PLATFORM_EVENT_CHANNEL_ID}

        Args:
            platform_event_channel_id: Path parameter: PLATFORM_EVENT_CHANNEL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel/{platform_event_channel_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_event_channel(
        self,
        platform_event_channel_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete event channel

        HTTP DELETE: /services/data/v{version}/tooling/sobjects/PlatformEventChannel/{PLATFORM_EVENT_CHANNEL_ID}

        Args:
            platform_event_channel_id: Path parameter: PLATFORM_EVENT_CHANNEL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel/{platform_event_channel_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_event_channel(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Describe event channel

        HTTP GET: /services/data/v{version}/tooling/sobjects/PlatformEventChannel/describe

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel/describe"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_channel_members(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """List channel members

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_channel_member(
        self,
        platform_event_channel_member_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get channel member

        HTTP GET: /services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{PLATFORM_EVENT_CHANNEL_MEMBER_ID}

        Args:
            platform_event_channel_member_id: Path parameter: PLATFORM_EVENT_CHANNEL_MEMBER_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{platform_event_channel_member_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_channel_member(
        self,
        platform_event_channel_member_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete channel member

        HTTP DELETE: /services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{PLATFORM_EVENT_CHANNEL_MEMBER_ID}

        Args:
            platform_event_channel_member_id: Path parameter: PLATFORM_EVENT_CHANNEL_MEMBER_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{platform_event_channel_member_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM CUSTOM CHANNELS CHANGE DATA CAPTURE ENDPOINTS
    # ========================================================================

    async def create_channel(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create channel

        HTTP POST: /services/data/v{version}/tooling/sobjects/PlatformEventChannel

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_channel(
        self,
        platform_event_channel_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update channel

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/PlatformEventChannel/{PLATFORM_EVENT_CHANNEL_ID}

        Args:
            platform_event_channel_id: Path parameter: PLATFORM_EVENT_CHANNEL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel/{platform_event_channel_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def add_enriched_fields_to_channel_member(
        self,
        platform_event_channel_member_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Add enriched fields to channel member

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{PLATFORM_EVENT_CHANNEL_MEMBER_ID}

        Args:
            platform_event_channel_member_id: Path parameter: PLATFORM_EVENT_CHANNEL_MEMBER_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{platform_event_channel_member_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_channel_member(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create channel member

        HTTP POST: /services/data/v{version}/tooling/sobjects/PlatformEventChannelMember

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannelMember"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM CUSTOM CHANNELS PLATFORM EVENT ENDPOINTS
    # ========================================================================

    async def create_channel_1(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create channel

        HTTP POST: /services/data/v{version}/tooling/sobjects/PlatformEventChannel

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_channel_1(
        self,
        platform_event_channel_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update channel

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/PlatformEventChannel/{PLATFORM_EVENT_CHANNEL_ID}

        Args:
            platform_event_channel_id: Path parameter: PLATFORM_EVENT_CHANNEL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannel/{platform_event_channel_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_channel_member_1(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create channel member

        HTTP POST: /services/data/v{version}/tooling/sobjects/PlatformEventChannelMember

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannelMember"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def add_filter_expression_in_channel_member(
        self,
        platform_event_channel_member_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Add filter expression in channel member

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{PLATFORM_EVENT_CHANNEL_MEMBER_ID}

        Args:
            platform_event_channel_member_id: Path parameter: PLATFORM_EVENT_CHANNEL_MEMBER_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/PlatformEventChannelMember/{platform_event_channel_member_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM EVENT RELAY CONFIGURATION EVENT RELAY CONFIG ENDPOINTS
    # ========================================================================

    async def list_event_relays(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """List event relays

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_event_relay(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Describe event relay

        HTTP GET: /services/data/v{version}/tooling/sobjects/EventRelayConfig/describe

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/EventRelayConfig/describe"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_event_relay(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create event relay

        HTTP POST: /services/data/v{version}/tooling/sobjects/EventRelayConfig

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/EventRelayConfig"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_event_relay(
        self,
        event_relay_config_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete event relay

        HTTP DELETE: /services/data/v{version}/tooling/sobjects/EventRelayConfig/{EVENT_RELAY_CONFIG_ID}

        Args:
            event_relay_config_id: Path parameter: EVENT_RELAY_CONFIG_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/EventRelayConfig/{event_relay_config_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_event_relay(
        self,
        event_relay_config_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get event relay

        HTTP GET: /services/data/v{version}/tooling/sobjects/EventRelayConfig/{EVENT_RELAY_CONFIG_ID}

        Args:
            event_relay_config_id: Path parameter: EVENT_RELAY_CONFIG_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/EventRelayConfig/{event_relay_config_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def change_event_relay_state(
        self,
        event_relay_config_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Change event relay state

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/EventRelayConfig/{EVENT_RELAY_CONFIG_ID}

        Args:
            event_relay_config_id: Path parameter: EVENT_RELAY_CONFIG_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/EventRelayConfig/{event_relay_config_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_event_relay(
        self,
        event_relay_config_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update event relay

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/EventRelayConfig/{EVENT_RELAY_CONFIG_ID}

        Args:
            event_relay_config_id: Path parameter: EVENT_RELAY_CONFIG_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/EventRelayConfig/{event_relay_config_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM EVENT RELAY CONFIGURATION EVENT RELAY FEEDBACK ENDPOINTS
    # ========================================================================

    async def list_event_relay_feedback_items(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """List event relay feedback items

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_event_relay_feedback(
        self,
        event_relay_feedback_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get event relay feedback

        HTTP GET: /services/data/v{version}/sobjects/EventRelayFeedback/{EVENT_RELAY_FEEDBACK_ID}

        Args:
            event_relay_feedback_id: Path parameter: EVENT_RELAY_FEEDBACK_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/EventRelayFeedback/{event_relay_feedback_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_event_felay_feedback(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Describe event felay feedback

        HTTP GET: /services/data/v{version}/sobjects/EventRelayFeedback/describe

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/EventRelayFeedback/describe"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM EVENT RELAY CONFIGURATION NAMED CREDENTIAL ENDPOINTS
    # ========================================================================

    async def list_named_credentials_1(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """List named credentials

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_named_credential(
        self,
        named_credential_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get named credential

        HTTP GET: /services/data/v{version}/tooling/sobjects/NamedCredential/{NAMED_CREDENTIAL_ID}

        Args:
            named_credential_id: Path parameter: NAMED_CREDENTIAL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/NamedCredential/{named_credential_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_named_credential_1(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create named credential

        HTTP POST: /services/data/v{version}/tooling/sobjects/NamedCredential

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/NamedCredential"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_named_credential(
        self,
        named_credential_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete named credential

        HTTP DELETE: /services/data/v{version}/tooling/sobjects/NamedCredential/{NAMED_CREDENTIAL_ID}

        Args:
            named_credential_id: Path parameter: NAMED_CREDENTIAL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/NamedCredential/{named_credential_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_named_credential_1(
        self,
        named_credential_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update named credential

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/NamedCredential/{NAMED_CREDENTIAL_ID}

        Args:
            named_credential_id: Path parameter: NAMED_CREDENTIAL_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/NamedCredential/{named_credential_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM EVENT SCHEMA ENDPOINTS
    # ========================================================================

    async def platform_event_schema_by_schema_id(
        self,
        schema_id: str,
        version: str,
        payloadformat: Optional[str] = None
    ) -> SalesforceResponse:
        """Set, reset, or get information about a user password. This resource is available in REST API version 24.0 and later.

        HTTP GET: /services/data/v{version}/event/eventSchema/{SCHEMA_ID}

        Args:
            schema_id: Path parameter: SCHEMA_ID
            version: Path parameter: version
            payloadformat: (Optional query parameter. Available in API version 43.0 and later.) The format of the returned event schema. This parameter can take one of the following values. EXPANDED—The JSON representation o... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/event/eventSchema/{schema_id}"
        params = self._build_params(**{"payloadFormat": payloadformat})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def platform_event_schema_by_event_name(
        self,
        event_name: str,
        version: str,
        payloadformat: Optional[str] = None
    ) -> SalesforceResponse:
        """Set, reset, or get information about a user password. This resource is available in REST API version 24.0 and later.

        HTTP GET: /services/data/v{version}/sobjects/{EVENT_NAME}/eventSchema

        Args:
            event_name: Path parameter: EVENT_NAME
            version: Path parameter: version
            payloadformat: (Optional query parameter. Available in API version 43.0 and later.) The format of the returned event schema. This parameter can take one of the following values. EXPANDED—The JSON representation o... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{event_name}/eventSchema"
        params = self._build_params(**{"payloadFormat": payloadformat})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM MANAGED EVENT SUBSCRIPTIONS ENDPOINTS
    # ========================================================================

    async def create_managed_event_subscription(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create managed event subscription

        HTTP POST: /services/data/v{version}/tooling/sobjects/ManagedEventSubscription

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/ManagedEventSubscription"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_managed_event_subscription_by_id(
        self,
        managedeventsubscriptionid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get managed event subscription by ID

        HTTP GET: /services/data/v{version}/tooling/sobjects/ManagedEventSubscription/{managedEventSubscriptionId}

        Args:
            managedeventsubscriptionid: Path parameter: managedEventSubscriptionId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/ManagedEventSubscription/{managedeventsubscriptionid}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_managed_event_subscriptions(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get all managed event subscriptions

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_managed_event_subscription(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Describe managed event subscription

        HTTP GET: /services/data/v{version}/tooling/sobjects/ManagedEventSubscription/describe

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/ManagedEventSubscription/describe"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_managed_event_subscription(
        self,
        managedeventsubscriptionid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update managed event subscription

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/ManagedEventSubscription/{managedEventSubscriptionId}

        Args:
            managedeventsubscriptionid: Path parameter: managedEventSubscriptionId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/ManagedEventSubscription/{managedeventsubscriptionid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_managed_event_subscription(
        self,
        managedeventsubscriptionid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Delete managed event subscription

        HTTP DELETE: /services/data/v{version}/tooling/sobjects/ManagedEventSubscription/{managedEventSubscriptionId}

        Args:
            managedeventsubscriptionid: Path parameter: managedEventSubscriptionId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/ManagedEventSubscription/{managedeventsubscriptionid}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # EVENT PLATFORM PUBLISH PLATFORM EVENTS ENDPOINTS
    # ========================================================================

    async def publish_single_event(
        self,
        eventapiname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Publish single event

        HTTP POST: /services/data/v{version}/sobjects/{EventApiName}

        Args:
            eventapiname: Path parameter: EventApiName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{eventapiname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def publish_multiple_events(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Publish multiple events

        HTTP POST: /services/data/v{version}/composite

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def publish_multiple_events_with_soap_call(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Publish multiple events with SOAP call

        HTTP POST: /services/Soap/u/{version}

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/u/{version}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # FOLDERS ENDPOINTS
    # ========================================================================

    async def get_wave_folder_collection(
        self,
        ispinned: Optional[str] = None,
        mobileonlyfeaturedassets: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        scope: Optional[str] = None,
        sort: Optional[str] = None,
        templatefilters: Optional[str] = None,
        templatesourceid: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a collection of CRM Analytics folders. For additional information, see the Folders List Resource.

        HTTP GET: /wave/folders

        Args:
            ispinned: (Optional) Filters the collection to include only folders which are pinned (true) or not (false). The default is false. (optional)
            mobileonlyfeaturedassets: (Optional) Filters the collection to only contain folders which contain dashboards that are enabled for the CRM Analytics mobile app. The default is false. (optional)
            page: (Optional) Generated token that indicates the view of folders to be returned. (optional)
            pagesize: (Optional) Number of items to be returned in a single page. Minimum is 1, maximum is 200, and the default is 25. (optional)
            q: (Optional) Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. If the user’s search query contains quotation marks or... (optional)
            scope: (Optional) Scope type to apply to the collection results. (optional)
            sort: (Optional) Sort order to apply to the collection results. (optional)
            templatefilters: (Optional) Filters the collection to include only folders that match the filter value. (optional)
            templatesourceid: (Optional) Filters the collection to include only folders that are created from a specific template source. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/folders"
        params = self._build_params(**{"isPinned": ispinned, "mobileOnlyFeaturedAssets": mobileonlyfeaturedassets, "page": page, "pageSize": pagesize, "q": q, "scope": scope, "sort": sort, "templateFilters": templatefilters, "templateSourceId": templatesourceid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_wave_folder(
        self,
        folderid: str,
        disablemru: Optional[str] = None,
        mobileonlyfeaturedassets: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a CRM Analytics app folder resource representation. For additional information, see the Folder Resource.

        HTTP GET: /wave/folders/{folderId}

        Args:
            folderid: Path parameter: folderId
            disablemru: Indicates whether to display the last viewed date of the returned folder. (optional)
            mobileonlyfeaturedassets: Indicates whether to filter the collection of folders to show only dashboards that are enabled for the Analytics mobile app (true) or not (false). (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/folders/{folderid}"
        params = self._build_params(**{"disableMru": disablemru, "mobileOnlyFeaturedAssets": mobileonlyfeaturedassets})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # GENERAL ENDPOINTS
    # ========================================================================

    async def get_wave_analytics_limit_collection(self, licensetype: Optional[str] = None, types: Optional[str] = None) -> SalesforceResponse:
        """Retrieve limits in CRM Analytics. For additional information, see the Limits Resource.

        HTTP GET: /wave/limits

        Args:
            licensetype: (Optional) The response includes CRM Analytics assets with this license type. The default is EinsteinAnalytics. (optional)
            types: (Optional) The types of limits used in CRM Analytics. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/limits"
        params = self._build_params(**{"licenseType": licensetype, "types": types})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def post_query(
        self,
        data: Optional[Dict[str, Any]] = None,
        querylanguage: Optional[str] = None,
        querystring: Optional[str] = None,
        timezone: Optional[str] = None
    ) -> SalesforceResponse:
        """Execute a CRM Analytics query written in Salesforce Analytics Query Language (SAQL) or standard SQL. For additional information, see the Query Resource.

        HTTP POST: /wave/query

        Args:
            data: Request body data
            querylanguage: (Optional) The query language. The default is SAQL. (optional)
            querystring: (Optional) The query string to execute. It is recommended to use the SAQL Query Input Representation to pass the query. (optional)
            timezone: (Optional) The timezone for the query. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/query"
        params = self._build_params(**{"queryLanguage": querylanguage, "queryString": querystring, "timezone": timezone})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # GRAPH QL INTROSPECTION ENDPOINTS
    # ========================================================================

    async def introspection_query(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Introspection Query

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # GRAPH QL MUTATION ENDPOINTS
    # ========================================================================

    async def create_account(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create Account

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_account(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Delete Account

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_account(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Update Account

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # GRAPH QL UI ENDPOINTS
    # ========================================================================

    async def accounts(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Accounts

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def account_by_id(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Account by Id

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def accounts_with_filter(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Accounts with Filter

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def accounts_with_cursors_pagination(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Accounts with Cursors (Pagination)

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def contacts(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Contacts

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def contacts_ordered(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Contacts Ordered

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def contacts_with_account_name(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Contacts with Account Name

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def opportunities_closing_soon(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Opportunities Closing Soon

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def opportunities_closing_soon_explicit_and(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Opportunities Closing Soon (Explicit AND)

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def opportunities_early_stage(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Opportunities Early Stage

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def opportunities_not_closed(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Opportunities Not Closed

        HTTP POST: /services/data/v{version}/graphql

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/graphql"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES CPQ ENDPOINTS
    # ========================================================================

    async def generate_quote_document_api(self, loader: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Creates and saves a CPQ quote document. Available in: Salesforce CPQ Winter ’19 and later https://developer.salesforce.com/docs/atlas.en-us.cpq_dev_api.meta/cpq_dev_api/cpq_api_generate_proposal.htm

        HTTP POST: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES CPQ CONFIGURATION API ENDPOINTS
    # ========================================================================

    async def configuration_loader_api(
        self,
        loader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """The Configuration Loader API returns all the data for the product, including its product options and configuration model. When configuring a nested bundle, set the parentProductproperty to the pare...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader, "uid": uid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def configuration_load_rule_executor_api(
        self,
        loader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """The Configuration Load Rule Executor API invokes all the load event product rules for the specified product. When configuring a nested bundle, set the parentProduct property to the parent product t...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader, "uid": uid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def configuration_validator_api(
        self,
        loader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """The Configuration Validator API runs selection, validation, and alert product rules and configurator-scoped price rules against the input configuration model and returns an updated configuration mo...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader, "uid": uid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES CPQ CONTRACT API ENDPOINTS
    # ========================================================================

    async def contract_amender_api(
        self,
        loader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Receive a CPQ contract ID in a request, and return quote data for an amendment quote. Available in: Salesforce CPQ Summer ’16 and later Special Access Rules: All of these user permissions are requi...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader, "uid": uid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def contract_renewer_api(self, loader: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Receive a CPQ contract in a request, and return quote data for one or more renewal quotes. Available in: Salesforce CPQ Summer ’16 and later Special Access Rules: All of these user permissions are ...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES CPQ QUOTE API ENDPOINTS
    # ========================================================================

    async def save_quote_api(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The Save Quote API saves a CPQ quote. Available in: Salesforce CPQ Summer ’16 and later https://developer.salesforce.com/docs/atlas.en-us.cpq_dev_api.meta/cpq_dev_api/cpq_quote_api_save_final.htm

        HTTP POST: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def calculate_quote_api(self, loader: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The Calculate Quote API calculates the prices of a CPQ quote. https://developer.salesforce.com/docs/atlas.en-us.cpq_dev_api.meta/cpq_dev_api/cpq_quote_api_calculate_final.htm

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def read_quote_api(
        self,
        reader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """The Read Quote API reads a quote from a CPQ quote ID. Available in: Salesforce CPQ Summer ’16 and later https://developer.salesforce.com/docs/atlas.en-us.cpq_dev_api.meta/cpq_dev_api/cpq_api_read_q...

        HTTP GET: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            reader: Query parameter: reader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"reader": reader, "uid": uid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def validate_quote_api(self, loader: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Validate a CPQ quote and return any validation errors. Available in: Salesforce CPQ Winter ’19 and later https://developer.salesforce.com/docs/atlas.en-us.cpq_dev_api.meta/cpq_dev_api/cpq_api_valid...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def add_products_api(self, loader: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Receive a CPQ quote, product collection, and quote group key in a request, and return a Quote model with all provided products added as quote lines. Available in: Salesforce CPQ Summer ’16 and late...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def read_product_api(
        self,
        loader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """The Read Product API takes the request’s product ID, pricebook ID, and currency code and returns a Product model. The Product model loads the product from your catalog when the user requests it. Av...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader, "uid": uid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_and_save_quote_proposal_api(self, loader: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The Read Product API takes the request’s product ID, pricebook ID, and currency code and returns a Product model. The Product model loads the product from your catalog when the user requests it. Av...

        HTTP POST: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def quote_term_reader_api(
        self,
        loader: str,
        uid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """The Read Product API takes the request’s product ID, pricebook ID, and currency code and returns a Product model. The Product model loads the product from your catalog when the user requests it. Av...

        HTTP PATCH: /services/apexrest/SBQQ/ServiceRouter

        Args:
            data: Request body data
            loader: Query parameter: loader (required)
            uid: Query parameter: uid (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/apexrest/SBQQ/ServiceRouter"
        params = self._build_params(**{"loader": loader, "uid": uid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES LOYALTY MANAGEMENT BASIC RESOURCES ENDPOINTS
    # ========================================================================

    async def corporate_member_enrollments(
        self,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_enroll_corporate_member.htm

        HTTP POST: /services/data/v{version}/loyalty-programs/{loyaltyProgramName}/corporate-member-enrollments

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/loyalty-programs/{loyaltyprogramname}/corporate-member-enrollments"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def individual_member_enrollments(
        self,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_enroll_individual_member.htm

        HTTP POST: /services/data/v{version}/loyalty-programs/{loyaltyProgramName}/individual-member-enrollments

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/loyalty-programs/{loyaltyprogramname}/individual-member-enrollments"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def member_benefits(
        self,
        memberid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_member_benefits.htm

        HTTP GET: /services/data/v{version}/connect/loyalty/member/{memberId}/memberbenefits

        Args:
            memberid: Path parameter: memberId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/member/{memberid}/memberbenefits"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def member_profile(
        self,
        loyaltyprogramname: str,
        version: str,
        memberid: str,
        membershipnumber: str,
        programcurrencyname: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_member_profile.htm

        HTTP GET: /services/data/v{version}/loyalty-programs/{loyaltyProgramName}/members

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            memberid: The ID of the loyalty program member. (required)
            membershipnumber: The membership number of the loyalty program member. (required)
            programcurrencyname: The name of the loyalty program currency associated with the member. Use this parameter to get the details of the member’s points-related information for a specific currency. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/loyalty-programs/{loyaltyprogramname}/members"
        params = self._build_params(**{"memberId": memberid, "membershipNumber": membershipnumber, "programCurrencyName": programcurrencyname})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def redeem_voucher(
        self,
        loyaltyprogramname: str,
        membershipnumber: str,
        vouchercode: str,
        version: str,
        vouchernumber: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_redeem_voucher.htm

        HTTP POST: /services/data/v{version}/loyalty/programs/{loyaltyProgramName}/members/{membershipNumber}/vouchers/{voucherCode}/redeem

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            membershipnumber: Path parameter: membershipNumber
            vouchercode: Path parameter: voucherCode
            version: Path parameter: {version
            data: Request body data
            vouchernumber: Query parameter: voucherNumber (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/loyalty/programs/{loyaltyprogramname}/members/{membershipnumber}/vouchers/{vouchercode}/redeem"
        params = self._build_params(**{"voucherNumber": vouchernumber})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def transaction_history(
        self,
        loyaltyprogramname: str,
        version: str,
        page: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_transaction_history.htm

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/transaction-history

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            page: Query parameter: page (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/transaction-history"
        params = self._build_params(**{"page": page})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def transaction_ledger_summary(
        self,
        loyaltyprogramname: str,
        membershipnumber: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journalsubtypename: Optional[str] = None,
        additionaltransactionjournalattributes: Optional[str] = None,
        journaltypename: Optional[str] = None,
        pagenumber: Optional[str] = None,
        periodenddate: Optional[str] = None,
        periodstartdate: Optional[str] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_transaction_ledger.htm

        HTTP GET: /services/data/v{version}/loyalty/programs/{loyaltyProgramName}/members/{membershipNumber}/transaction-ledger-summary

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            membershipnumber: Path parameter: membershipNumber
            version: Path parameter: version
            data: Request body data
            journalsubtypename: Returns the transaction journals filtered by the specified journal subtype. (optional)
            additionaltransactionjournalattributes: Returns the list of transaction journal fields. (optional)
            journaltypename: Returns the transaction journals filtered by the specified journal type. (optional)
            pagenumber: Returns the transaction journals for the specified page number. (optional)
            periodenddate: Returns the transaction journals filtered by the specified end date. (optional)
            periodstartdate: Returns the transaction journals filtered by the specified start date. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/loyalty/programs/{loyaltyprogramname}/members/{membershipnumber}/transaction-ledger-summary"
        params = self._build_params(**{"journalSubTypeName": journalsubtypename, "additionalTransactionJournalAttributes": additionaltransactionjournalattributes, "journalTypeName": journaltypename, "pageNumber": pagenumber, "periodEndDate": periodenddate, "periodStartDate": periodstartdate})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def transaction_journals_execution(
        self,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_loyalty_program_realtime.htm

        HTTP POST: /services/data/v{version}/connect/realtime/loyalty/programs/{loyaltyProgramName}

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/realtime/loyalty/programs/{loyaltyprogramname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def transaction_journals_simulation(
        self,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_loyalty_program_process_simulation.htm

        HTTP POST: /services/data/v{version}/connect/realtime/loyalty/programs/{loyaltyProgramName}

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/realtime/loyalty/programs/{loyaltyprogramname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def member_vouchers(
        self,
        loyaltyprogramname: str,
        membershipnumber: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Member Vouchers NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP GET: /services/data/v{version}/loyalty/programs/{loyaltyProgramName}/members/{membershipNumber}/vouchers

        Args:
            loyaltyprogramname: Path parameter: loyaltyProgramName
            membershipnumber: Path parameter: membershipNumber
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/loyalty/programs/{loyaltyprogramname}/members/{membershipnumber}/vouchers"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES LOYALTY MANAGEMENT GAMIFICATION ENDPOINTS
    # ========================================================================

    async def games(
        self,
        participantid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        definitionid: Optional[str] = None,
        gameparticipantrewardid: Optional[str] = None
    ) -> SalesforceResponse:
        """Games NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP GET: /services/data/v{version}/game/participant/{participantId}/games

        Args:
            participantid: Path parameter: participantId
            version: Path parameter: version
            data: Request body data
            definitionid: ID of the game definition associated with the game whose details are to be fetched. (optional)
            gameparticipantrewardid: ID of the participant's game reward associated with the game whose details are to be fetched. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/game/participant/{participantid}/games"
        params = self._build_params(**{"definitionId": definitionid, "gameParticipantRewardId": gameparticipantrewardid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def game_reward(
        self,
        gameparticipantrewardid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Game Reward NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP GET: /services/data/v{version}/game/gameparticipantreward/{gameParticipantRewardId}/game-reward

        Args:
            gameparticipantrewardid: Path parameter: gameParticipantRewardId
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/game/gameparticipantreward/{gameparticipantrewardid}/game-reward"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES LOYALTY MANAGEMENT LOYALTY PROGRAM PROCESS ENDPOINTS
    # ========================================================================

    async def cancel_a_voucher(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_cancel_voucher.htm In the Pre-request. Script Tab, make changes in the variables val...

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def credit_points_to_members(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Standard Documentation: https://developer.salesforce.com/docs/atlas.en-us.loyalty.meta/loyalty/connect_resources_credit_points_to_members.htm In the Pre-request. Script Tab, make changes in the var...

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def debit_points_from_members(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Debit Points from Members NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def enroll_for_promotions(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Enroll for Promotions NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_member_promotions(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Get Member Promotions NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def issue_a_voucher(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Issue a Voucher NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def unenroll_a_member(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Unenroll a Member NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def opt_out_from_a_promotion(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Opt Out from a Promotion NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_member_details(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Update Member Details NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_member_tier(
        self,
        processname: str,
        loyaltyprogramname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Update Member Tier NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/connect/loyalty/programs/{loyaltyProgramName}/program-processes/{processName}

        Args:
            processname: Path parameter: processName
            loyaltyprogramname: Path parameter: loyaltyProgramName
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/connect/loyalty/programs/{loyaltyprogramname}/program-processes/{processname}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES LOYALTY MANAGEMENT PROMOTION ENDPOINTS
    # ========================================================================

    async def eligible_promotions(
        self,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Eligible Promotions NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/global-promotions-management/eligible-promotions

        Args:
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/global-promotions-management/eligible-promotions"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def promotions_creation(
        self,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Promotions Creation NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP POST: /services/data/v{version}/global-promotions-management/promotions

        Args:
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/global-promotions-management/promotions"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def promotion_details(
        self,
        promotionid: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        journaltypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Promotions Creation NOTE: In the Pre-request. Script Tab, make changes in the variables value accordingly with the real Process Name to invoke.

        HTTP GET: /services/data/v{version}/global-promotions-management/promotions/{promotionId}

        Args:
            promotionid: Path parameter: promotionId
            version: Path parameter: version
            data: Request body data
            journaltypeid: Query parameter: journalTypeId (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/global-promotions-management/promotions/{promotionid}"
        params = self._build_params(**{"journalTypeId": journaltypeid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INDUSTRIES OMNI STUDIO ENDPOINTS
    # ========================================================================

    async def integration_procedure_invocation_using_get(self, type_subtype: str, namespace: str) -> SalesforceResponse:
        """Integration Procedure Invocation Using GET

        HTTP GET: /services/apexrest/{NAMESPACE}/v1/integrationprocedure/{TYPE_SUBTYPE}

        Args:
            type_subtype: Path parameter: TYPE_SUBTYPE
            namespace: Path parameter: NAMESPACE

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/apexrest/{namespace}/v1/integrationprocedure/{type_subtype}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def integration_procedure_invocation_using_post(self, type_subtype: str, namespace: str) -> SalesforceResponse:
        """Integration Procedure Invocation Using POST

        HTTP POST: /services/apexrest/{NAMESPACE}/v1/integrationprocedure/{TYPE_SUBTYPE}

        Args:
            type_subtype: Path parameter: TYPE_SUBTYPE
            namespace: Path parameter: NAMESPACE

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/apexrest/{namespace}/v1/integrationprocedure/{type_subtype}"
        params = None
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INGESTION API BULK ENDPOINTS
    # ========================================================================

    async def create_job_1(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create Job

        HTTP POST: /api/v1/ingest/jobs

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/ingest/jobs"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upload_job(self, jobid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Upload Job

        HTTP PUT: /api/v1/ingest/jobs/{jobId}/batches

        Args:
            jobid: Path parameter: jobId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/ingest/jobs/{jobid}/batches"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="text/plain"
        )

    async def close_job(self, jobid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Close Job

        HTTP PATCH: /api/v1/ingest/jobs/{jobId}

        Args:
            jobid: Path parameter: jobId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/ingest/jobs/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def abort_job(self, jobid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Abort Job

        HTTP PATCH: /api/v1/ingest/jobs/{jobId}

        Args:
            jobid: Path parameter: jobId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/ingest/jobs/{jobid}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_job_1(self, jobid: str) -> SalesforceResponse:
        """Delete Job

        HTTP DELETE: /api/v1/ingest/jobs/{jobId}

        Args:
            jobid: Path parameter: jobId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/ingest/jobs/{jobid}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_job_info_1(self, jobid: str) -> SalesforceResponse:
        """Get Job Info

        HTTP GET: /api/v1/ingest/jobs/{jobId}

        Args:
            jobid: Path parameter: jobId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/ingest/jobs/{jobid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_jobs_1(
        self,
        limit: str,
        offset: str,
        orderby: str,
        state: str
    ) -> SalesforceResponse:
        """Get All Jobs

        HTTP GET: /api/v1/ingest/jobs

        Args:
            limit: Query parameter: limit (required)
            offset: Query parameter: offset (required)
            orderby: Query parameter: orderby (required)
            state: Query parameter: state (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/ingest/jobs"
        params = self._build_params(**{"limit": limit, "offset": offset, "orderby": orderby, "state": state})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # INGESTION API STREAMING ENDPOINTS
    # ========================================================================

    async def insert_records(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Insert Records

        HTTP POST: /api/v1/ingest/sources/Event_API/runner_profiles

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/ingest/sources/Event_API/runner_profiles"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_records(self, ids: str) -> SalesforceResponse:
        """Delete Records

        HTTP DELETE: /api/v1/ingest/sources/Event_API/runner_profiles

        Args:
            ids: Query parameter: ids (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/ingest/sources/Event_API/runner_profiles"
        params = self._build_params(**{"ids": ids})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sync_record_validation(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Sync Record Validation

        HTTP POST: /api/v1/ingest/sources/Event_API/runner_profiles/actions/test

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/ingest/sources/Event_API/runner_profiles/actions/test"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # LENSES ENDPOINTS
    # ========================================================================

    async def get_lens_collection(
        self,
        folderid: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        scope: Optional[str] = None,
        sort: Optional[str] = None
    ) -> SalesforceResponse:
        """Get a collection of lens resource representations.

        HTTP GET: /wave/lenses

        Args:
            folderid: Filters the results to include only the contents of a specific folder. (optional)
            page: A generated token that indicates the view of the lenses to be returned. (optional)
            pagesize: The number of items to be returned in a single page. Minimum is 1, maximum is 200, and default is 25 (optional)
            q: Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. (optional)
            scope: The type of scope to be applied to the returned collection. (optional)
            sort: The type of sort order to be applied to the returned collection. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/lenses"
        params = self._build_params(**{"folderId": folderid, "page": page, "pageSize": pagesize, "q": q, "scope": scope, "sort": sort})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_lens(self, lensidorapiname: str) -> SalesforceResponse:
        """Get a Lens resource representation.

        HTTP GET: /wave/lenses/lenses/{lensIdOrApiName}

        Args:
            lensidorapiname: Path parameter: lensIdOrApiName

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/lenses/lenses/{lensidorapiname}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST ADDRESS ENDPOINTS
    # ========================================================================

    async def validate_address(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/validateEmail.htm Validates an email by specifying the email address and validators to be used in the request body o...

        HTTP POST: /address/v1/validateEmail

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/address/v1/validateEmail"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST ASSETS CONTENT BUILDER CATEGORIES FOLDERS ENDPOINTS
    # ========================================================================

    async def get_categories_folder(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getCategories.htm Returns one or more Content Builder categories that are owned by or reside in your MID. To request categorie...

        HTTP GET: /asset/v1/content/categories

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/categories"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_categories_folder_shared(self, scope: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getCategories.htm Returns one or more Content Builder categories that are owned by or reside in your MID. To request categorie...

        HTTP GET: /asset/v1/content/categories

        Args:
            scope: Query parameter: scope (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/categories"
        params = self._build_params(**{"scope": scope})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_categories_folder_with_filter(
        self,
        page: str,
        pagesize: str,
        orderby: str,
        filter_param: str
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getCategories.htm Returns one or more Content Builder categories that are owned by or reside in your MID. To request categorie...

        HTTP GET: /asset/v1/content/categories

        Args:
            page: Query parameter: $page (required)
            pagesize: Query parameter: $pagesize (required)
            orderby: Query parameter: $orderBy (required)
            filter_param: Query parameter: $filter (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/categories"
        params = self._build_params(**{"$page": page, "$pagesize": pagesize, "$orderBy": orderby, "$filter": filter_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_categories_folder_by_id(self, categoryid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getCategory.htm Returns one Content Builder category by ID.

        HTTP GET: /asset/v1/content/categories/{categoryId}

        Args:
            categoryid: Path parameter: categoryId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/asset/v1/content/categories/{categoryid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_category_folder(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createCategory.htm Creates a category (folder) in Content Builder.

        HTTP POST: /asset/v1/content/categories

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/categories"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_category_folder_shared(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createCategory.htm Creates a category (folder) in Content Builder.

        HTTP POST: /asset/v1/content/categories

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/categories"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_category_folder_by_id(self, categoryid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/updateCategory.htm Updates one Content Builder category by ID.

        HTTP PUT: /asset/v1/content/categories/{categoryId}

        Args:
            categoryid: Path parameter: categoryId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/asset/v1/content/categories/{categoryid}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_category_folder_by_id(self, categoryid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/deleteCategory.htm Deletes one Content Builder category by ID.

        HTTP DELETE: /asset/v1/content/categories/{categoryId}

        Args:
            categoryid: Path parameter: categoryId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/asset/v1/content/categories/{categoryid}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST ASSETS CONTENT BUILDER CONTENT ENDPOINTS
    # ========================================================================

    async def create_asset_image(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP POST: /asset/v1/content/assets

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_asset_image(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP PATCH: /asset/v1/content/assets/<assetId>

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/<assetId>"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_asset_html_block(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP PATCH: /asset/v1/content/assets/<assetId>

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/<assetId>"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_asset_template(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP POST: /asset/v1/content/assets

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_asset_template(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP PATCH: /asset/v1/content/assets/<assetId>

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/<assetId>"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_asset_html_email(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP POST: /asset/v1/content/assets

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_asset_html_email(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/content-api.htm https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/base-asset-types.ht...

        HTTP PATCH: /asset/v1/content/assets/<assetId>

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/<assetId>"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_asset_by_id(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/routes.htm#detail_deleteAsset Deletes a category by ID.

        HTTP DELETE: /asset/v1/content/assets/<assetId>

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/<assetId>"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST ASSETS CONTENT BUILDER QUERY ENDPOINTS
    # ========================================================================

    async def simple_query_all_assets(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/assetSimpleQuery.htm Get all assets.

        HTTP GET: /asset/v1/content/assets

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def simple_query_by_id(self, assetid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/content-api.htm Gets an asset by ID.

        HTTP GET: /asset/v1/content/assets/{assetId}

        Args:
            assetid: Path parameter: {assetId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/asset/v1/content/assets/{assetid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def simple_query_with_filter(
        self,
        page: str,
        pagesize: str,
        orderby: str,
        filter_param: str,
        fields: str
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/assetSimpleQuery.htm Gets an asset collection by simple $filter parameters.

        HTTP GET: /asset/v1/content/assets

        Args:
            page: Query parameter: $page (required)
            pagesize: Query parameter: $pagesize (required)
            orderby: Query parameter: $orderBy (required)
            filter_param: Query parameter: $filter (required)
            fields: Query parameter: $fields (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets"
        params = self._build_params(**{"$page": page, "$pagesize": pagesize, "$orderBy": orderby, "$filter": filter_param, "$fields": fields})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def simple_query_binary_file(self, assetid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/content-api.htm Gets the binary file for an asset

        HTTP GET: /asset/v1/content/assets/{assetId}/file

        Args:
            assetid: Path parameter: {assetId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/asset/v1/content/assets/{assetid}/file"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def simple_query_salutations(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/content-api.htm Gets the default header and footer for an account.

        HTTP GET: /asset/v1/content/assets/salutations

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/salutations"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def simple_query_salutations_by_id(self, emailid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/content-api.htm Gets the header and footer for an email.

        HTTP GET: /asset/v1/content/assets/{emailId}/salutations

        Args:
            emailid: Path parameter: {emailId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/asset/v1/content/assets/{emailid}/salutations"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def advanced_query(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/assetAdvancedQuery.htm A complex query is comprised of two or more simple queries joined with an AND or OR logical comparison ...

        HTTP POST: /asset/v1/content/assets/query

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/asset/v1/content/assets/query"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST AUDIT ENDPOINTS
    # ========================================================================

    async def get_audit_events(self, orderby: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getAuditEvents.htm Retrieves logged Audit Trail audit events for the current account and its children. Logins are audited at t...

        HTTP GET: /data/v1/audit/auditEvents

        Args:
            orderby: Query parameter: $orderBy (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/data/v1/audit/auditEvents"
        params = self._build_params(**{"$orderBy": orderby})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_security_events(
        self,
        pagesize: str,
        page: str,
        orderby: str
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSecurityEvents.htm Retrieves logged Audit Trail security events for the authenticated user’s account and its children. Logi...

        HTTP GET: /data/v1/audit/securityEvents

        Args:
            pagesize: Query parameter: $pagesize (required)
            page: Query parameter: $page (required)
            orderby: Query parameter: $orderBy (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/data/v1/audit/securityEvents"
        params = self._build_params(**{"$pagesize": pagesize, "$page": page, "$orderBy": orderby})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST AUTH ENDPOINTS
    # ========================================================================

    async def request_sfmc_token(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-app-development.meta/mc-app-development/access-token-s2s.htm When the access token expires, your application must request a new access token usi...

        HTTP POST: /v2/token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/v2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_user_info_1(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getUserInfo.htm Gets information for the account and user that are associated with the access token.

        HTTP GET: /v2/userinfo

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/v2/userinfo"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_base_ur_ls(self, client_id: str, resource: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getBaseURLs.htm Gets tenant-specific REST and authentication base URIs for the Marketing Cloud tenant associated wit...

        HTTP GET: /v2/discovery

        Args:
            client_id: Query parameter: client_id (required)
            resource: Query parameter: resource (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/v2/discovery"
        params = self._build_params(**{"client_id": client_id, "resource": resource})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST CONTACTS ENDPOINTS
    # ========================================================================

    async def get_schemas_collection(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/schemasCollection.htm Retrieves the collection of all contact data schemas contained in the current account.

        HTTP GET: /contacts/v1/schema

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/schema"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_attribute_groups(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/routes.htm

        HTTP GET: /contacts/v1/attributeGroups

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/attributeGroups"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_contacts(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createContacts.htm Creates a new contact with the specified information in the specified attribute groups.

        HTTP POST: /contacts/v1/contacts

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/contacts"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_contacts(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/updateContacts.htm Updates contacts with the specified information in the specified attribute groups.

        HTTP PATCH: /contacts/v1/contacts

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/contacts"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_contact_key_for_email_address(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/retrieveContactKey.htm Retrieves the contact key for one or more email channel addresses.

        HTTP POST: /contacts/v1/addresses/email/search

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/addresses/email/search"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_contact_by_id(self, type_param: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/DeleteByContactIDs.htm Deletes contacts based on specified contact ID values. This operations runs asynchronously. U...

        HTTP POST: /contacts/v1/contacts/actions/delete

        Args:
            data: Request body data
            type_param: Query parameter: type (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/contacts/actions/delete"
        params = self._build_params(**{"type": type_param})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_contact_by_key(self, type_param: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/DeleteByContactKeys.htm Deletes contacts based on specified contact key values. This operation runs asynchronously. ...

        HTTP POST: /contacts/v1/contacts/actions/delete

        Args:
            data: Request body data
            type_param: Query parameter: type (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/contacts/actions/delete"
        params = self._build_params(**{"type": type_param})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_contact_delete_request_details(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/ContactsDeleteRequestsDetails.htm Retrieves details of contact delete requests for a date range.

        HTTP GET: /contacts/v1/contacts/analytics/deleterequests

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/contacts/analytics/deleterequests"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_contact_delete_request_summary(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/ContactsDeleteRequestsSummary.htm Retrieves a summary of contact delete requests for a date range.

        HTTP GET: /contacts/v1/contacts/analytics/deleterequests/summary

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/contacts/v1/contacts/analytics/deleterequests/summary"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST DATA ENDPOINTS
    # ========================================================================

    async def queue_de_one_time_file_import(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/marketing/marketing-cloud/references/mc-import_job_api?meta=Queue+and+start+a+one-time+data+import Queue and start a one-time import against a data extension w...

        HTTP POST: /data/v1/async/import

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/data/v1/async/import"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_file_import_status(self, id_param: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/marketing/marketing-cloud/references/mc-import_job_api?meta=Get+status+of+a+one-time+data+import Get the status of a queued data import by ID.

        HTTP GET: /data/v1/async/import/{id}/summary

        Args:
            id_param: Path parameter: id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/data/v1/async/import/{id_param}/summary"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_file_import_validation_summary(self, id_param: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/marketing/marketing-cloud/references/mc-import_job_api?meta=Get+validation+summary+of+a+one-time+data+import Get validation summary of a data import by ID.

        HTTP GET: /data/v1/async/import/{id}/validationsummary

        Args:
            id_param: Path parameter: id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/data/v1/async/import/{id_param}/validationsummary"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_file_import_validation_detail(self, id_param: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/marketing/marketing-cloud/references/mc-import_job_api?meta=Get+validation+details+of+a+one-time+data+import Get import row level validation details by ID.

        HTTP GET: /data/v1/async/import/{id}/validationresult

        Args:
            id_param: Path parameter: id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/data/v1/async/import/{id_param}/validationresult"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST DATA EVENTS ASYNCHRONOUS ENDPOINTS
    # ========================================================================

    async def upsert_row_de_key(self, de_external_key: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts a single data extension row Asynchronously using Data Extension's E...

        HTTP POST: /hub/v1/dataeventsasync/key:{DE_External_Key}/rowset

        Args:
            de_external_key: Path parameter: DE_External_Key
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/key:{de_external_key}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_de_id(self, de_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts a single data extension row Asynchronously using Data Extension's ID.

        HTTP POST: /hub/v1/dataeventsasync/{DE_ID}/rowset

        Args:
            de_id: Path parameter: DE_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/{de_id}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_set_de_key(self, de_external_key: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts a multiple data extension rows Asynchronously using Data Extension'...

        HTTP POST: /hub/v1/dataeventsasync/key:{DE_External_Key}/rowset

        Args:
            de_external_key: Path parameter: DE_External_Key
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/key:{de_external_key}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_set_de_id(self, de_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts a multiple data extension rows Asynchronously using Data Extension'...

        HTTP POST: /hub/v1/dataeventsasync/{DE_ID}/rowset

        Args:
            de_id: Path parameter: DE_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/{de_id}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_by_key_de_key(
        self,
        example: str,
        de_external_key: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putDataExtensionRowByKey.htm Upserts a data extension row by primary key using Data Extension's External Key.

        HTTP PUT: /hub/v1/dataeventsasync/key:{DE_External_Key}/rows/Email{example}@example.com

        Args:
            example: Path parameter: example
            de_external_key: Path parameter: DE_External_Key
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/key:{de_external_key}/rows/Email{example}@example.com"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_by_key_de_id(
        self,
        example: str,
        de_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putDataExtensionRowByKey.htm Upserts a data extension row by primary key using Data Extension ID.

        HTTP PUT: /hub/v1/dataeventsasync/{DE_ID}/rows/Email{example}@example.com

        Args:
            example: Path parameter: example
            de_id: Path parameter: DE_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/{de_id}/rows/Email{example}@example.com"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def increment_column_de_key(
        self,
        someone: str,
        de_external_key: str,
        step: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putIncrementColumnValueByKey.htm The API increments a numeric column in a single Asynchronous call using Data Extens...

        HTTP PUT: /hub/v1/dataeventsasync/key:{DE_External_Key}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment

        Args:
            someone: Path parameter: someone
            de_external_key: Path parameter: DE_External_Key
            data: Request body data
            step: Query parameter: step (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/key:{de_external_key}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment"
        params = self._build_params(**{"step": step})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def increment_column_de_id(
        self,
        someone: str,
        de_id: str,
        step: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putIncrementColumnValueByKey.htm The API increments a numeric column in a single Asynchronous call using Data Extens...

        HTTP PUT: /hub/v1/dataeventsasync/{DE_ID}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment

        Args:
            someone: Path parameter: someone
            de_id: Path parameter: DE_ID
            data: Request body data
            step: Query parameter: step (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataeventsasync/{de_id}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment"
        params = self._build_params(**{"step": step})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST DATA EVENTS SYNCHRONOUS ENDPOINTS
    # ========================================================================

    async def upsert_row_de_key_1(self, de_external_key: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts a single data extension row Synchronously using Data Extension's Ex...

        HTTP POST: /hub/v1/dataevents/key:{DE_External_Key}/rowset

        Args:
            de_external_key: Path parameter: DE_External_Key
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/key:{de_external_key}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_de_id_1(self, de_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts a single data extension row Synchronously using Data Extension's ID.

        HTTP POST: /hub/v1/dataevents/{DE_ID}/rowset

        Args:
            de_id: Path parameter: DE_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/{de_id}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_set_de_key_1(self, de_external_key: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts multiple data extension rows in a single synchronous call using Dat...

        HTTP POST: /hub/v1/dataevents/key:{DE_External_Key}/rowset

        Args:
            de_external_key: Path parameter: DE_External_Key
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/key:{de_external_key}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_set_de_id_1(self, de_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postDataExtensionRowsetByKey.htm The API upserts multiple data extension rows in a single synchronous call using Dat...

        HTTP POST: /hub/v1/dataevents/{DE_ID}/rowset

        Args:
            de_id: Path parameter: DE_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/{de_id}/rowset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_by_key_de_key_1(
        self,
        example: str,
        key: str,
        de_external_key: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putDataExtensionRowByKey.htm Upserts a data extension row by primary key using Data Extension's External Key.

        HTTP PUT: /hub/v1/dataevents/key{key}:{DE_External_Key}/rows/Email{example}@example.com

        Args:
            example: Path parameter: example
            key: Path parameter: key
            de_external_key: Path parameter: DE_External_Key
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/key{key}:{de_external_key}/rows/Email{example}@example.com"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def upsert_row_by_key_de_id_1(
        self,
        example: str,
        de_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putDataExtensionRowByKey.htm Upserts a data extension row by primary key using Data Extension ID.

        HTTP PUT: /hub/v1/dataevents/{DE_ID}/rows/Email{example}@example.com

        Args:
            example: Path parameter: example
            de_id: Path parameter: DE_ID
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/{de_id}/rows/Email{example}@example.com"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def increment_column_de_key_1(
        self,
        someone: str,
        de_external_key: str,
        step: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putIncrementColumnValueByKey.htm The API increments a numeric column in a single synchronous call using Data Extensi...

        HTTP PUT: /hub/v1/dataevents/key:{DE_External_Key}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment

        Args:
            someone: Path parameter: someone
            de_external_key: Path parameter: DE_External_Key
            data: Request body data
            step: Query parameter: step (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/key:{de_external_key}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment"
        params = self._build_params(**{"step": step})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def increment_column_de_id_1(
        self,
        someone: str,
        de_id: str,
        step: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/putIncrementColumnValueByKey.htm The API increments a numeric column in a single synchronous call using Data Extensi...

        HTTP PUT: /hub/v1/dataevents/{DE_ID}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment

        Args:
            someone: Path parameter: someone
            de_id: Path parameter: DE_ID
            data: Request body data
            step: Query parameter: step (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/hub/v1/dataevents/{de_id}/rows/Email{someone}@exacttarget.com/column/FollowerCount/increment"
        params = self._build_params(**{"step": step})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST EVENT NOTIFICATION CALLBACK ENDPOINTS
    # ========================================================================

    async def get_all_callbacks(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getAllCallbacks.htm Retrieves details about all registered callbacks.

        HTTP GET: /platform/v1/ens-callbacks

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/platform/v1/ens-callbacks"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_callback(self, callbackid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getCallback.htm Retrieves details about a registered callback.

        HTTP GET: /platform/v1/ens-callbacks/{callbackId}

        Args:
            callbackid: Path parameter: callbackId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/platform/v1/ens-callbacks/{callbackid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_callback(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createCallback.htm Registers a new callback to receive event notifications. Verify your callback before you can use it in a su...

        HTTP POST: /platform/v1/ens-callbacks

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/platform/v1/ens-callbacks"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def verify_callback(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/verifyCallback.htm Verifies a callback so that it can receive notifications.

        HTTP POST: /platform/v1/ens-verify

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/platform/v1/ens-verify"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_callback(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/updateCallback.htm Updates a registered callback. It can take up to two minutes for callback changes to become active.

        HTTP PUT: /platform/v1/ens-callbacks

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/platform/v1/ens-callbacks"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_callback(self, callbackid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/deleteCallback.htm Deletes a registered callback that isn't used by any subscriptions. Delete all subscriptions for the callba...

        HTTP DELETE: /platform/v1/ens-callbacks/{callbackId}

        Args:
            callbackid: Path parameter: callbackId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/platform/v1/ens-callbacks/{callbackid}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST EVENT NOTIFICATION SUBSCRIPTIONS ENDPOINTS
    # ========================================================================

    async def get_subscription(self, subscriptionid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSubscription.htm Retrieves details for a subscription.

        HTTP GET: /platform/v1/ens-subscriptions/{subscriptionId}

        Args:
            subscriptionid: Path parameter: subscriptionId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/platform/v1/ens-subscriptions/{subscriptionid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_subscriptions_for_a_callback(self, callbackid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getAllSubscriptionsForCallback.htm Retrieves details for all subscriptions associated with a callback.

        HTTP GET: /platform/v1/ens-subscriptions-by-cb/{callbackId}

        Args:
            callbackid: Path parameter: callbackId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/platform/v1/ens-subscriptions-by-cb/{callbackid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_subscription(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createSubscription.htm Creates a single subscription. A subscription indicates which event types to receive notifications for ...

        HTTP POST: /platform/v1/ens-subscriptions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/platform/v1/ens-subscriptions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_subscription(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/updateSubscription.htm Updates a single subscription. To pause an active subscription, set the status request parameter to pau...

        HTTP PUT: /platform/v1/ens-subscriptions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/platform/v1/ens-subscriptions"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_subscription_1(self, subscriptionid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/deleteSubscription.htm Deletes a subscription.

        HTTP DELETE: /platform/v1/ens-subscriptions/{subscriptionId}

        Args:
            subscriptionid: Path parameter: subscriptionId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/platform/v1/ens-subscriptions/{subscriptionid}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST INTERACTIONS JOURNEY BUILDER ENDPOINTS
    # ========================================================================

    async def get_discovery_document(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Retrieves the discovery document for the collection of journey resources.

        HTTP GET: /interaction/v1/rest

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/rest"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def fire_entry_event(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postEvent.htm Fires the entry event that initiates the journey.

        HTTP POST: /interaction/v1/events

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/events"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_interaction_journey_audit_log(self, journeyid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getInteractionAuditLog.htm Retrieves an audit log of a journey and its versions by ID or key. Pass in different acti...

        HTTP GET: /interaction/v1/interactions/{journeyId}/audit/all

        Args:
            journeyid: Path parameter: journeyId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/interactions/{journeyid}/audit/all"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def remove_contact_from_journey(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/contactExitRequest.htm Removes up to 50 contacts from a journey or from one or more versions of a journey.

        HTTP POST: /interaction/v1/interactions/contactexit

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions/contactexit"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_contact_journey_exit_status(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/contactExitStatus.htm Returns the status of a request to remove a contact from a journey or from one or more version...

        HTTP POST: /interaction/v1/interactions/contactexit/status

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions/contactexit/status"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_of_journeys_contact_is_in(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/contactMembershipRequest.htm Provides a list of journeys and journey versions for a list of contact keys.

        HTTP POST: /interaction/v1/interactions/contactMembership

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions/contactMembership"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def download_journey_history(self, columns: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/contactMembershipRequest.htm Provides a list of journeys and journey versions for a list of contact keys.

        HTTP POST: /interaction/v1/interactions/journeyhistory/download

        Args:
            data: Request body data
            columns: Query parameter: columns (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions/journeyhistory/download"
        params = self._build_params(**{"columns": columns})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST INTERACTIONS JOURNEY BUILDER EVENT DEFINITIONS ENDPOINTS
    # ========================================================================

    async def get_event_definitions(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Retrieves a collection of event definitions.

        HTTP GET: /interaction/v1/eventDefinitions

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/eventDefinitions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_event_definitions_by_key(self, eventdefinitionkey: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Retrieves an individual event definition by key.

        HTTP GET: /interaction/v1/eventDefinitions/key:{eventDefinitionKey}

        Args:
            eventdefinitionkey: Path parameter: {eventDefinitionKey

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/eventDefinitions/key:{eventdefinitionkey}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_event_definition(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/createEventDefinition.htm Creates an event definition (name and data schema for an event) and defines an event defin...

        HTTP POST: /interaction/v1/eventDefinitions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/eventDefinitions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_event_definition_by_key(self, eventdefinitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Updates an event definition by key. Once an event definition is created, only a few properties can be...

        HTTP PUT: /interaction/v1/eventDefinitions/key:{eventDefinitionKey}

        Args:
            eventdefinitionkey: Path parameter: {eventDefinitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/eventDefinitions/key:{eventdefinitionkey}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_event_definition_by_id(self, eventdefinitionid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Updates an event definition by ID. Once an event definition is created, only a few properties can be ...

        HTTP PUT: /interaction/v1/eventDefinitions/{eventDefinitionId}

        Args:
            eventdefinitionid: Path parameter: {eventDefinitionId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/eventDefinitions/{eventdefinitionid}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_event_definition_by_key(self, eventdefinitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Deletes an individual event definition by key.

        HTTP DELETE: /interaction/v1/eventDefinitions/key:{eventDefinitionKey}

        Args:
            eventdefinitionkey: Path parameter: {eventDefinitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/eventDefinitions/key:{eventdefinitionkey}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_event_definition_by_id(self, eventdefinitionid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Deletes an individual event definition by ID.

        HTTP DELETE: /interaction/v1/eventDefinitions/{eventDefinitionId}

        Args:
            eventdefinitionid: Path parameter: {eventDefinitionId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/eventDefinitions/{eventdefinitionid}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST INTERACTIONS JOURNEY BUILDER JOURNEYS ENDPOINTS
    # ========================================================================

    async def get_interactions_journeys(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Retrieves a collection of all journeys. The journey collection resources are embedded in the items pr...

        HTTP GET: /interaction/v1/interactions

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_interactions_journeys_by_id(self, journeyid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/jb-api-specification.htm Retrieves a single journey by ID.

        HTTP GET: /interaction/v1/interactions/{journeyId}

        Args:
            journeyid: Path parameter: journeyId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/interactions/{journeyid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_interaction_journey_basic_shell(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postCreateInteraction.htm Creates or saves a journey. To create a new journey provide the request body in the approp...

        HTTP POST: /interaction/v1/interactions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_interaction_journey_entry_event_only(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postCreateInteraction.htm Creates or saves a journey. To create a new journey provide the request body in the approp...

        HTTP POST: /interaction/v1/interactions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_interaction_journey_simple_journey(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postCreateInteraction.htm Creates or saves a journey. To create a new journey provide the request body in the approp...

        HTTP POST: /interaction/v1/interactions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/interaction/v1/interactions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def publish_interaction_journey(
        self,
        journeyid: str,
        versionnumber: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postPublishInteractionById.htm Publishes a journey version asynchronously.

        HTTP POST: /interaction/v1/interactions/publishAsync/{journeyId}

        Args:
            journeyid: Path parameter: journeyId
            data: Request body data
            versionnumber: Query parameter: versionNumber (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/interactions/publishAsync/{journeyid}"
        params = self._build_params(**{"versionNumber": versionnumber})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_interaction_journey_publish_status(self, statusid: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getPublishStatus.htm Checks the status of a publication.

        HTTP GET: /interaction/v1/interactions/publishStatus/{statusId}

        Args:
            statusid: Path parameter: {statusId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/interactions/publishStatus/{statusid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def stop_interaction_journey(
        self,
        journeyid: str,
        versionnumber: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postStopInteractionById.htm Stops a running journey.

        HTTP POST: /interaction/v1/interactions/stop/{journeyId}

        Args:
            journeyid: Path parameter: journeyId
            data: Request body data
            versionnumber: Query parameter: versionNumber (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/interactions/stop/{journeyid}"
        params = self._build_params(**{"versionNumber": versionnumber})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_interaction_journey_by_id(self, id_param: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/deleteInteractionById.htm Deletes a journey by ID. When deleting last version of a journey, check if the journey is ...

        HTTP DELETE: /interaction/v1/interactions/{id}

        Args:
            id_param: Path parameter: id
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/interaction/v1/interactions/{id_param}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST MESSAGING ENDPOINTS
    # ========================================================================

    async def send_email_triggered_send_definition(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/messageDefinitionSends.htm Sends transactional email using Marketing Cloud's triggered send functionality. In order ...

        HTTP POST: /messaging/v1/messageDefinitionSends/key:<tsd_external_key>/send

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/messageDefinitionSends/key:<tsd_external_key>/send"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_message_status(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/messageDefinitionSendsDeliveryRecords.htm Gets the delivery details of an email sent using the MessageDefinitionSends.

        HTTP GET: /messaging/v1/messageDefinitionSends/key:<tsd_external_key>/deliveryRecords/<recipientSendId>

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/messageDefinitionSends/key:<tsd_external_key>/deliveryRecords/<recipientSendId>"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST PUSH ENDPOINTS
    # ========================================================================

    async def broadcast_message(self, mobilepush_message_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postMessageAppSend.htm Broadcasts a message to all users of a push-enabled app.

        HTTP POST: /push/v1/messageApp/{mobilepush_message_id}/send

        Args:
            mobilepush_message_id: Path parameter: {mobilepush_message_id
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/push/v1/messageApp/{mobilepush_message_id}/send"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def send_message_to_list(self, mobilepush_message_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postMessageListSend.htm Sends a message to the specified mobile devices of a push-enabled app.

        HTTP POST: /push/v1/messageList/{mobilepush_message_id}/send

        Args:
            mobilepush_message_id: Path parameter: {mobilepush_message_id
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/push/v1/messageList/{mobilepush_message_id}/send"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_push_message(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/createPushMessage.htm Creates a push message template for sending to a list of subscribers or an audience inclusion ...

        HTTP POST: /push/v1/message

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/push/v1/message"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST PUSH LOCATION ENDPOINTS
    # ========================================================================

    async def retrieve_all_locations(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getLocations.htm Gets a list of all locations.

        HTTP GET: /push/v1/location

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/push/v1/location"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_specific_location(self, et_location_id: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getSpecificLocation.htm ets a specific location.

        HTTP GET: /push/v1/location/{et_location_id}

        Args:
            et_location_id: Path parameter: {et_location_id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/push/v1/location/{et_location_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_location(self, et_location_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/updateLocation.htm Updates an existing location by ID.

        HTTP PUT: /push/v1/location/{et_location_id}

        Args:
            et_location_id: Path parameter: {et_location_id
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/push/v1/location/{et_location_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_location(self, et_location_id: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/deleteLocation.htm Deletes an existing location.

        HTTP DELETE: /push/v1/location/{et_location_id}

        Args:
            et_location_id: Path parameter: {et_location_id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/push/v1/location/{et_location_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_location(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create Location

        HTTP POST: /push/v1/location

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/push/v1/location"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST SMS ENDPOINTS
    # ========================================================================

    async def create_keyword(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/createKeyword.htm Creates a keyword on an account.

        HTTP POST: /sms/v1/keyword

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/sms/v1/keyword"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def post_message_to_number(self, sms_message_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postMessageContactSend.htm Initiates a message to one or more mobile numbers.

        HTTP POST: /sms/v1/messageContact/{sms_message_id}/send

        Args:
            sms_message_id: Path parameter: sms_message_id
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/sms/v1/messageContact/{sms_message_id}/send"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def message_contact_history(
        self,
        et_sms_tokenid: str,
        mobile_number: str,
        et_smsmsg_id: str
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getMessageContactHistory.htm Retrieves the last message sent to a mobile number.

        HTTP GET: /sms/v1/messageContact/{et_smsmsg_id}/history/{et_sms_tokenId}/mobileNumber/{mobile_number}

        Args:
            et_sms_tokenid: Path parameter: et_sms_tokenId
            mobile_number: Path parameter: {mobile_number
            et_smsmsg_id: Path parameter: et_smsmsg_id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/sms/v1/messageContact/{et_smsmsg_id}/history/{et_sms_tokenid}/mobileNumber/{mobile_number}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_opt_in_message(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/messageOptIn.htm Creates an SMS opt-in message permitting contacts to subscribe to further SMS messages.

        HTTP POST: /sms/v1/message/optin

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/sms/v1/message/optin"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def queue_mo_message(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/postQueueMO.htm Queues an MO message for send.

        HTTP POST: /sms/v1/queueMO

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/sms/v1/queueMO"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_keyword(self, et_keyword_id: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/deleteKeywordViaKeywordId.htm Deletes a keyword on an account given a keyword Id.

        HTTP DELETE: /sms/v1/keyword/{et_keyword_id}

        Args:
            et_keyword_id: Path parameter: {et_keyword_id
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/sms/v1/keyword/{et_keyword_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_subscription_status(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/contactsSubscriptions.htm Returns subscription status for a mobile number or subscriber key.

        HTTP POST: /sms/v1/contacts/subscriptions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/sms/v1/contacts/subscriptions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_keyword_by_shortcode(
        self,
        et_sms_shortcode: str,
        et_keyword: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/deleteKeywordViaKeywordShortCodeCountryCode.htm Deletes a keyword on an account given a keyword, short code, and cou...

        HTTP DELETE: /sms/v1/keyword/{et_keyword}/{et_sms_shortcode}/US

        Args:
            et_sms_shortcode: Path parameter: {et_sms_shortcode
            et_keyword: Path parameter: {et_keyword
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/sms/v1/keyword/{et_keyword}/{et_sms_shortcode}/US"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_message_list_status(self, et_smsmsg_id: str, et_smstoken_id: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/getMessageList.htm Returns status for a message sent to a group of mobile numbers.

        HTTP GET: /sms/v1/messageList/{et_smsmsg_id}/deliveries/{et_smstoken_id}

        Args:
            et_smsmsg_id: Path parameter: et_smsmsg_id
            et_smstoken_id: Path parameter: et_smstoken_id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/sms/v1/messageList/{et_smsmsg_id}/deliveries/{et_smstoken_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST TRANSACTIONAL MESSAGING EMAIL ENDPOINTS
    # ========================================================================

    async def email_create_send_definiton(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createSendDefinition.htm Creates a send definition.

        HTTP POST: /messaging/v1/email/definitions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/email/definitions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_get_a_list_of_definitions(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getDefinitions.htm Gets a list of send definitions.

        HTTP GET: /messaging/v1/email/definitions

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/email/definitions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_get_queue_metrics_for_a_definition(self, definitionkey: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getQueueMetricsForDefinition.htm Gets metrics for the messages of a send definition. Applies to messages that are accepted but...

        HTTP GET: /messaging/v1/email/definitions/{definitionKey}/queue

        Args:
            definitionkey: Path parameter: definitionKey

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/email/definitions/{definitionkey}/queue"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_get_message_send_status_for_recipient(self, messagekey: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSendStatusForRecipient.htm Gets the send status for a message. Because this route is rate-limited, use it for infrequent ve...

        HTTP GET: /messaging/v1/email/messages/{messageKey}

        Args:
            messagekey: Path parameter: messageKey

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/email/messages/{messagekey}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_get_list_of_messages_not_sent_to_recipients(self, type_param: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getMessagesNotSent.htm Gets a paginated list of messages that were not sent, ordered from oldest to newest.

        HTTP GET: /messaging/v1/email/messages

        Args:
            type_param: Query parameter: type (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/email/messages"
        params = self._build_params(**{"type": type_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_get_send_definition_by_key(self, definitionkey: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSendDefinition.htm Gets send definition configuration details for a definition key.

        HTTP GET: /messaging/v1/email/definitions/{definitionKey}

        Args:
            definitionkey: Path parameter: definitionKey

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/email/definitions/{definitionkey}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_update_message_definition(self, definitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/updateMessageDefinition.htm Updates a specific message definition.

        HTTP PATCH: /messaging/v1/email/definitions/{definitionKey}

        Args:
            definitionkey: Path parameter: definitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/email/definitions/{definitionkey}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_delete_message_definition(self, definitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/deleteMessageDefinition.htm Deletes a message definition. You can't restore a deleted definition. The deleted definition is ar...

        HTTP DELETE: /messaging/v1/email/definitions/{definitionKey}

        Args:
            definitionkey: Path parameter: definitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/email/definitions/{definitionkey}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_send_a_message_to_a_single_recipient(self, guid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/sendMessageSingleRecipient.htm Sends a message to a single recipient via a send definition using a messageKey path parameter.

        HTTP POST: /messaging/v1/email/messages/{$guid}

        Args:
            guid: Path parameter: $guid
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/email/messages/{guid}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def email_send_a_message_to_multiple_recipients(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/sendMessageMultipleRecipients.htm Sends a message to multiple recipients using a send definition. You can provide a messageKey...

        HTTP POST: /messaging/v1/email/messages

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/email/messages"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS REST TRANSACTIONAL MESSAGING SMS ENDPOINTS
    # ========================================================================

    async def sms_create_send_definiton(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/createSMSSendDefinition.htm Creates a send definition.

        HTTP POST: /messaging/v1/sms/definitions

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/sms/definitions"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_send_a_message_to_a_single_recipient(self, guid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/sendSMSMessageSingleRecipient.htm Sends a message to a single recipient via a send definition using a messageKey path parameter.

        HTTP POST: /messaging/v1/sms/messages/{$guid}

        Args:
            guid: Path parameter: $guid
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/sms/messages/{guid}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_send_a_message_to_multiple_recipients(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/sendSMSMessageMultipleRecipients.htm Sends a message to multiple recipients using a send definition. You can provide a message...

        HTTP POST: /messaging/v1/sms/messages

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/sms/messages"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_update_message_definiton(self, definitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/updateSMSMessageDefinition.htm Updates a specific message definition.

        HTTP PATCH: /messaging/v1/sms/definitions/{definitionKey}

        Args:
            definitionkey: Path parameter: definitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/sms/definitions/{definitionkey}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_get_a_list_of_definitions(self) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSMSDefinitions.htm Gets a list of send definitions.

        HTTP GET: /messaging/v1/sms/definitions

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/sms/definitions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_get_message_send_status_for_recipient(self, messagekey: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSMSSendStatusForRecipient.htm Gets the send status for a message. Because this route is rate-limited, use it for infrequent...

        HTTP GET: /messaging/v1/sms/messages/{messageKey}

        Args:
            messagekey: Path parameter: messageKey

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/sms/messages/{messagekey}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_get_list_of_messages_not_sent_to_recipients(self, type_param: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSMSMessagesNotSent.htm Gets a paginated list of messages that were not sent, ordered from oldest to newest.

        HTTP GET: /messaging/v1/sms/messages

        Args:
            type_param: Query parameter: type (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/messaging/v1/sms/messages"
        params = self._build_params(**{"type": type_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_get_queue_metrics_for_a_definition(self, definitionkey: str) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/getSMSQueueMetricsForDefinition.htm Gets metrics for the messages of a send definition. Applies to messages that are accepted ...

        HTTP GET: /messaging/v1/sms/definitions/{definitionKey}/queue

        Args:
            definitionkey: Path parameter: definitionKey

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/sms/definitions/{definitionkey}/queue"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_delete_messages_queued_for_a_definition(self, definitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/deleteQueuedSMSMessagesForDefinition.htm

        HTTP DELETE: /messaging/v1/sms/definitions/{definitionKey}/queue

        Args:
            definitionkey: Path parameter: definitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/sms/definitions/{definitionkey}/queue"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sms_delete_message_definition(self, definitionkey: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/deleteSMSMessageDefinition.htm Deletes a message definition. You can't restore a deleted definition. The deleted definition is...

        HTTP DELETE: /messaging/v1/sms/definitions/{definitionKey}

        Args:
            definitionkey: Path parameter: definitionKey
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/messaging/v1/sms/definitions/{definitionkey}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # MESSAGING JOURNEYS SOAP ADMIN ENDPOINTS
    # ========================================================================

    async def retrieve_send_classifications(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Retrieve Send Classifications

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # MESSAGING JOURNEYS SOAP AUTOMATIONS ENDPOINTS
    # ========================================================================

    async def create_automation_query_activity(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/querydefinition.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def create_automation(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/automation.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def retrieve_automation(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/automation.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def perform_automation(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/automation.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def schedule_automation(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/interacting_with_automation_studio_via_the_web_service_soap_api.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # MESSAGING JOURNEYS SOAP DATA EXTENSIONS ENDPOINTS
    # ========================================================================

    async def create_data_extension(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/creating_a_data_extension_using_an_existing_template.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def retrieve_available_data_extension_templates(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/dataextensiontemplate.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def retrieve_data_extension_object(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/dataextension.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def create_data_extension_from_template(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/creating_a_data_extension_using_an_existing_template.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def update_data_extension_add_column(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/adding_new_column_to_an_existing_data_extension.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def perform_clear_data_extension(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/clearing_data_from_a_data_extension.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def delete_data_extension(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/dataextension.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # MESSAGING JOURNEYS SOAP SUBSCRIBER ENDPOINTS
    # ========================================================================

    async def create_subscriber(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/subscriber.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    async def retrieve_a_subscriber(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.noversion.mc-apis.meta/mc-apis/retrieve_a_subscriber_via_the_web_service_api.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # MESSAGING JOURNEYS SOAP USERS ENDPOINTS
    # ========================================================================

    async def retrieve_all_users(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """https://developer.salesforce.com/docs/atlas.en-us.mc-apis.meta/mc-apis/dataextension.htm

        HTTP POST: /Service.asmx

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/Service.asmx"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # METADATA ENDPOINTS
    # ========================================================================

    async def soap_describe_metadata(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SOAP DescribeMetadata

        HTTP POST: /services/Soap/m/{version}

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/m/{version}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def soap_describe_value_type(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SOAP DescribeValueType

        HTTP POST: /services/Soap/m/{version}

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/m/{version}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def soap_list_metadata(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """`type`: Replace the value of `type` with the metadata type you're interested in `folder`: If your type requires a folder add it here. Null values are ignored e.g. ```CustomObject ``` or ```Dashboar...

        HTTP POST: /services/Soap/m/{version}

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/m/{version}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # METADATA API ENDPOINTS
    # ========================================================================

    async def global_metadata(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The Metadata API response includes metadata about all entities, including Calculated Insights, Engagement, Profile, and other entities, as well as their relationships to other objects. https://deve...

        HTTP GET: /api/v1/metadata

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/metadata"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_by_type(self, entitytype: str) -> SalesforceResponse:
        """The response includes metadata about the data cloud object type supplied in the query parameters. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_metadata_api.htm

        HTTP GET: /api/v1/metadata

        Args:
            entitytype: Optional. The entity type for which metadata is requested. By default, this parameter includes all of the available types. Acceptable values: DataLakeObject, DataModelObject, and CalculatedInsight (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/metadata"
        params = self._build_params(**{"entityType": entitytype})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_by_entity(self, entityname: str) -> SalesforceResponse:
        """The Metadata API response includes metadata about all entities, including Calculated Insights, Engagement, Profile, and other entities, as well as their relationships to other objects. https://deve...

        HTTP GET: /api/v1/metadata

        Args:
            entityname: Optional. The name of the requested metadata entity. By default, this parameter includes all of the available categories and an exhaustive list of entities. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/metadata"
        params = self._build_params(**{"entityName": entityname})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_by_category(self, entitycategory: str) -> SalesforceResponse:
        """The response includes metadata about all entities of the specified category type supplied in the query parameter. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_ap...

        HTTP GET: /api/v1/metadata

        Args:
            entitycategory: Optional. The requested metadata entity category. By default, this parameter includes all of the available categories. It isn’t applicable for Calculated Insight entities. Acceptable values: Profil... (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/metadata"
        params = self._build_params(**{"entityCategory": entitycategory})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # PROFILE API ENDPOINTS
    # ========================================================================

    async def metadata(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """The metadata includes the dimension and measure that are part of the calculated insight. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_insights_meta_ci_name.htm

        HTTP GET: /api/v1/profile/metadata

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/profile/metadata"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def metadata_dmo(self, dmoapiname: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Returns the metadata for the data model object. Metadata includes the list of fields, data types and indexes available for lookup. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c...

        HTTP GET: /api/v1/profile/metadata/{dmoApiName}

        Args:
            dmoapiname: Path parameter: dmoApiName
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/metadata/{dmoapiname}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def dmo(
        self,
        dmoapiname: str,
        fields: str,
        limit: str,
        filters: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns data model objects based on search filters. Use this API call to retrieve the object records after applying the selected filter(s). https://developer.salesforce.com/docs/atlas.en-us.c360a_a...

        HTTP GET: /api/v1/profile/{dmoApiName}

        Args:
            dmoapiname: Path parameter: dmoApiName
            data: Request body data
            fields: Query parameter: fields (required)
            limit: Query parameter: limit (required)
            filters: Query parameter: filters (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/{dmoapiname}"
        params = self._build_params(**{"fields": fields, "limit": limit, "filters": filters})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def dmo_by_id(
        self,
        dmoapiname: str,
        dmorecordid: str,
        fields: str,
        limit: str,
        filters: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns data model objects based on search indexes and filters. Use this API call to retrieve the object record based on the primary key or secondary keys. Returns an empty list when records are no...

        HTTP GET: /api/v1/profile/{dmoApiName}/{dmoRecordId}

        Args:
            dmoapiname: Path parameter: dmoApiName
            dmorecordid: Path parameter: dmoRecordId
            data: Request body data
            fields: Query parameter: fields (required)
            limit: Query parameter: limit (required)
            filters: Query parameter: filters (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/{dmoapiname}/{dmorecordid}"
        params = self._build_params(**{"fields": fields, "limit": limit, "filters": filters})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def dmo_by_search_key(
        self,
        searchkey: str,
        dmoapiname: str,
        fields: str,
        limit: str,
        filters: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns data model objects based on search indexes and filters. Use this API call to retrieve the object record based on the primary key or secondary keys. Returns an empty list when records are no...

        HTTP GET: /api/v1/profile/{dmoApiName}/{searchKey}

        Args:
            searchkey: Path parameter: searchKey
            dmoapiname: Path parameter: dmoApiName
            data: Request body data
            searchkey: Query parameter: searchKey (required)
            fields: Query parameter: fields (required)
            limit: Query parameter: limit (required)
            filters: Query parameter: filters (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/{dmoapiname}/{searchkey}"
        params = self._build_params(**{"fields": fields, "limit": limit, "filters": filters})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def dmo_and_children_by_id(
        self,
        dmochildapiname: str,
        dmoparentapiname: str,
        dmoparentrecordid: str,
        fields: str,
        limit: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns data model objects and child objects based on indexes and search filters. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_profile_dmname_id_child_dmname...

        HTTP GET: /api/v1/profile/{dmoParentApiName}/{dmoParentRecordId}/{dmoChildApiName}

        Args:
            dmochildapiname: Path parameter: dmoChildApiName
            dmoparentapiname: Path parameter: dmoParentApiName
            dmoparentrecordid: Path parameter: dmoParentRecordId
            data: Request body data
            fields: Query parameter: fields (required)
            limit: Query parameter: limit (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/{dmoparentapiname}/{dmoparentrecordid}/{dmochildapiname}"
        params = self._build_params(**{"fields": fields, "limit": limit})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def dmo_and_children_by_search_key(
        self,
        dmochildapiname: str,
        dmoparentapiname: str,
        searchkey: str,
        fields: str,
        limit: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns data model objects and child objects based on indexes and search filters. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_profile_dmname_id_child_dmname...

        HTTP GET: /api/v1/profile/{dmoParentApiName}/{searchKey}/{dmoChildApiName}

        Args:
            dmochildapiname: Path parameter: dmoChildApiName
            dmoparentapiname: Path parameter: dmoParentApiName
            searchkey: Path parameter: searchKey
            data: Request body data
            searchkey: Query parameter: searchKey (required)
            fields: Query parameter: fields (required)
            limit: Query parameter: limit (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/{dmoparentapiname}/{searchkey}/{dmochildapiname}"
        params = self._build_params(**{"fields": fields, "limit": limit})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def dmo_and_calculated_insight_by_id(
        self,
        dmoapiname: str,
        dmorecordid: str,
        insightname: str,
        batchsize: str,
        dimensions: str,
        measures: str,
        filters: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns data model objects and a computed view based on indexes and search filters. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api_profile_dmname_id_ci_ci_name...

        HTTP GET: /api/v1/profile/{dmoApiName}/{dmoRecordId}/calculated-insights/{insightName}

        Args:
            dmoapiname: Path parameter: dmoApiName
            dmorecordid: Path parameter: dmoRecordId
            insightname: Path parameter: insightName
            data: Request body data
            batchsize: Query parameter: batchSize (required)
            dimensions: Query parameter: dimensions (required)
            measures: Query parameter: measures (required)
            filters: Query parameter: filters (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/profile/{dmoapiname}/{dmorecordid}/calculated-insights/{insightname}"
        params = self._build_params(**{"batchSize": batchsize, "dimensions": dimensions, "measures": measures, "filters": filters})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # QUERY API V1 ENDPOINTS
    # ========================================================================

    async def query(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP POST: /api/v1/query

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/query"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def query_with_limits(self, limit: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP POST: /api/v1/query

        Args:
            data: Request body data
            limit: Query parameter: limit (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v1/query"
        params = self._build_params(**{"limit": limit})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # QUERY API V2 ENDPOINTS
    # ========================================================================

    async def query_1(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP POST: /api/v2/query

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/api/v2/query"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def query_next_batch(self, nextbatchid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP GET: /api/v2/query/{nextBatchId}

        Args:
            nextbatchid: Path parameter: nextBatchId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v2/query/{nextbatchid}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # QUERY UNIFIED RECORD ID ENDPOINTS
    # ========================================================================

    async def query_2(
        self,
        datasourceid_c: str,
        sourcerecordid_c: str,
        datasourceobjectid_c: str,
        entityname: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Use this API to query the Customer 360 Audiences data lake across data model, lake, unified, and linked objects. https://developer.salesforce.com/docs/atlas.en-us.c360a_api.meta/c360a_api/c360a_api...

        HTTP GET: /api/v1/universalIdLookup/{entityName}/{dataSourceId__c}/{dataSourceObjectId__c}/{sourceRecordId__c}

        Args:
            datasourceid_c: Path parameter: dataSourceId__c
            sourcerecordid_c: Path parameter: sourceRecordId__c
            datasourceobjectid_c: Path parameter: dataSourceObjectId__c
            entityname: Path parameter: entityName
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/api/v1/universalIdLookup/{entityname}/{datasourceid_c}/{datasourceobjectid_c}/{sourcerecordid_c}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # RECIPES ENDPOINTS
    # ========================================================================

    async def get_recipe_notification(self, id_param: str) -> SalesforceResponse:
        """Retrieve notification condition on a recipe for the current user. For additional information, see the Recipe Notification Resource.

        HTTP GET: /wave/recipes/{id}/notification

        Args:
            id_param: Path parameter: id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/recipes/{id_param}/notification"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_recipe(self, id_param: str, format_param: str) -> SalesforceResponse:
        """Get a recipe by ID.

        HTTP GET: /wave/recipes/recipes/{id}

        Args:
            id_param: Path parameter: id
            format_param: (Required) Specifies the format of the returned recipe. (required)
            id_param: (Required) The ID of the recipe. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/recipes/recipes/{id_param}"
        params = self._build_params(**{"format": format_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_recipe_collection(
        self,
        folderid: Optional[str] = None,
        format_param: Optional[str] = None,
        lastmodifiedafter: Optional[str] = None,
        lastmodifiedbefore: Optional[str] = None,
        licensetype: Optional[str] = None,
        nextscheduledafter: Optional[str] = None,
        nextscheduledbefore: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        sort: Optional[str] = None,
        status: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a listing of all recipes. For additional information, see the Recipes List Resource.

        HTTP GET: /wave/recipes

        Args:
            folderid: (Optional) Filters the collection to include only recipes associated with the specified folder ID. (optional)
            format_param: (Optional) Filters the collection to include only recipes of the specified format. (optional)
            lastmodifiedafter: (Optional) Filters the collection to include only recipes with a last modified data after the given value. (optional)
            lastmodifiedbefore: (Optional) Filters the collection to include only recipes with a last modified data before the given value. (optional)
            licensetype: (Optional) The response includes assets with this license type. The default is EinsteinAnalytics. (optional)
            nextscheduledafter: (Optional) Filters the collection to include only recipes with a scheduled run after the given value. (optional)
            nextscheduledbefore: (Optional) Filters the collection to include only recipes with a scheduled run before the given value. (optional)
            page: (Optional) Generated token that indicates the view of recipes to be returned. (optional)
            pagesize: (Optional) Number of items to be returned in a single page. Minimum is 1, maximum is 200, and the default is 25. (optional)
            q: (Optional) Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. If the user’s search query contains quotation marks or... (optional)
            sort: (Optional) Sort order to apply to the collection results. (optional)
            status: (Optional) Filters the collection to include only recipes with the specified statuses. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/recipes"
        params = self._build_params(**{"folderId": folderid, "format": format_param, "lastModifiedAfter": lastmodifiedafter, "lastModifiedBefore": lastmodifiedbefore, "licenseType": licensetype, "nextScheduledAfter": nextscheduledafter, "nextScheduledBefore": nextscheduledbefore, "page": page, "pageSize": pagesize, "q": q, "sort": sort, "status": status})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REPLICATED DATASETS ENDPOINTS
    # ========================================================================

    async def get_replicated_field_collection(self, id_param: str) -> SalesforceResponse:
        """Retrieve a list of a Replicated Dataset's fields. For additional information, see the Replicated Dataset Fields Resource.

        HTTP GET: /wave/replicatedDatasets/{id}/fields

        Args:
            id_param: Path parameter: id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/replicatedDatasets/{id_param}/fields"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_replicated_dataset(self, id_param: str) -> SalesforceResponse:
        """Retrieve a single Replicated Dataset by ID. For additional information, see the Replicated Dataset Resource.

        HTTP GET: /wave/replicatedDatasets/{id}

        Args:
            id_param: Path parameter: id

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/replicatedDatasets/{id_param}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_replicated_dataset_collection(
        self,
        category: Optional[str] = None,
        connector: Optional[str] = None,
        includelive: Optional[str] = None,
        q: Optional[str] = None,
        sourceobject: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve a list of all Replicated Datasets. For additional information, see the Replicated Datasets List Resource.

        HTTP GET: /wave/replicatedDatasets

        Args:
            category: (Optional) Filters the collection to include only connected objects of the specified category. (optional)
            connector: (Optional) Filters the collection to include only connected objects belonging to the specified CRM Analytics connector. (optional)
            includelive: (Optional) Filters the collection to include live datasets. The default is false. (optional)
            q: (Optional) Search terms. Individual terms are separated by spaces. A wildcard is automatically appended to the last token in the query string. If the user’s search query contains quotation marks or... (optional)
            sourceobject: (Optional) Filters the collection to include only connected objects belonging to the specified source object. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/replicatedDatasets"
        params = self._build_params(**{"category": category, "connector": connector, "includeLive": includelive, "q": q, "sourceObject": sourceobject})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST ENDPOINTS
    # ========================================================================

    async def apex_rest(self, urlmapping: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Gets the list of icons and colors used by themes in the Salesforce application. Theme information is provided for objects in your organization that use icons and colors in the Salesforce UI. The If...

        HTTP GET: /services/apexrest/{urlMapping}

        Args:
            urlmapping: Path parameter: urlMapping
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/apexrest/{urlmapping}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def versions(self) -> SalesforceResponse:
        """Lists summary information about each Salesforce version currently available, including the version, label, and a link to each version's root.

        HTTP GET: /services/data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/data"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def resources_by_version(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Lists available resources for the specified API version, including resource name and URI.

        HTTP GET: /services/data/v{version}

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def limits(self, version: str) -> SalesforceResponse:
        """Lists information about limits in your org. For each limit, this resource returns the maximum allocation and the remaining allocation based on usage. This resource is available in REST API version ...

        HTTP GET: /services/data/v{version}/limits

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/limits"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def app_menu(self, version: str) -> SalesforceResponse:
        """Returns a list of items in either the Salesforce app drop-down menu or the Salesforce for Android, iOS, and mobile web navigation menu.

        HTTP GET: /services/data/v{version}/appMenu

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/appMenu"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def consent(
        self,
        version: str,
        actions: str,
        ids: str,
        aggregatedconsent: Optional[str] = None,
        datetime: Optional[str] = None,
        purpose: Optional[str] = None,
        verbose: Optional[str] = None
    ) -> SalesforceResponse:
        """Your users can store consent preferences in different locations and possibly inconsistently. You can locate your customers’ preferences for consent across multiple records when using API version 44...

        HTTP GET: /services/data/v{version}/consent/action/action

        Args:
            version: Path parameter: version
            aggregatedconsent: Optional: true or false. aggregatedConsent is the same as aggregatedConsent=true. If true, one result is returned indicating whether to proceed or not, rather than a result for each ID. If any ID i... (optional)
            datetime: Optional. The timestamp for which consent is determined. The value is converted to the UTC timezone and must be specified in ISO 8601 format. If not specified, defaults to the current date and time. (optional)
            purpose: Optional. The reason for contacting a customer. (optional)
            verbose: Optional: true or false. verbose is the same as verbose=true. Verbose responses are slower than non-verbose responses. See the examples for a verbose response. (optional)
            actions: Query parameter: actions (required)
            ids: Query parameter: ids (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/consent/action/action"
        params = self._build_params(**{"actions": actions, "ids": ids, "aggregatedConsent": aggregatedconsent, "datetime": datetime, "purpose": purpose, "verbose": verbose})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def product_schedules(self, opportunity_line_item_id: str, version: str) -> SalesforceResponse:
        """Work with revenue and quantity schedules for opportunity products. Establish or reestablish a product schedule with multiple installments for an opportunity product. Delete all installments in a sc...

        HTTP GET: /services/data/v{version}/sobjects/OpportunityLineItem/{OPPORTUNITY_LINE_ITEM_ID}/OpportunityLineItemSchedules

        Args:
            opportunity_line_item_id: Path parameter: OPPORTUNITY_LINE_ITEM_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/OpportunityLineItem/{opportunity_line_item_id}/OpportunityLineItemSchedules"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def query_3(self, version: str, q: str) -> SalesforceResponse:
        """Executes the specified SOQL query. If the query results are too large, the response contains the first batch of results and a query identifier in the nextRecordsUrl field of the response. The ident...

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            q: A SOQL query. Note that you will need to replace spaces with “+” characters in your query string to create a valid URI. An example query parameter string might look like: “SELECT+Name+FROM+MyObject... (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def query_all(
        self,
        version: str,
        q: str,
        explain: Optional[str] = None
    ) -> SalesforceResponse:
        """Executes the specified SOQL query. Unlike the Query resource, QueryAll will return records that have been deleted because of a merge or delete. QueryAll will also return information about archived ...

        HTTP GET: /services/data/v{version}/queryAll

        Args:
            version: Path parameter: version
            q: Query parameter: q (required)
            explain: A SOQL query to get performance feedback on. Use explain instead of q to get a response that details how Salesforce will process your query. You can use this feedback to further optimize your queri... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/queryAll"
        params = self._build_params(**{"q": q, "explain": explain})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def record_count(self, version: str, sobjects: str) -> SalesforceResponse:
        """Lists information about object record counts in your organization. This resource is available in REST API version 40.0 and later for API users with the “View Setup and Configuration” permission. Th...

        HTTP GET: /services/data/v{version}/limits/recordCount

        Args:
            version: Path parameter: version
            sobjects: A comma-delimited list of object names. If a listed object is not found in the org, it is ignored and not returned in the response. This parameter is optional. If this parameter is not provided, th... (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/limits/recordCount"
        params = self._build_params(**{"sObjects": sobjects})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def tabs(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """f a survey field can be translated or is already translated into a particular language, you can add or change the translated value of the survey field. https://developer.salesforce.com/docs/atlas.e...

        HTTP GET: /services/data/v{version}/tabs

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tabs"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def themes(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Gets the list of icons and colors used by themes in the Salesforce application. Theme information is provided for objects in your organization that use icons and colors in the Salesforce UI. The If...

        HTTP GET: /services/data/v{version}/theme

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/theme"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST ACTIONS ENDPOINTS
    # ========================================================================

    async def standard_invocable_actions(self, version: str) -> SalesforceResponse:
        """Returns the list of actions that can be statically invoked. You can also get basic information for each type of action. This resource is available in REST API version 32.0 and later.

        HTTP GET: /services/data/v{version}/actions/standard

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/standard"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def custom_invocable_actions(self, version: str) -> SalesforceResponse:
        """Returns the list of all custom actions. You can also get basic information for each type of action. This resource is available in REST API version 32.0 and later.

        HTTP GET: /services/data/v{version}/actions/custom

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/custom"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def launch_flow(
        self,
        flowapiname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Launches an Autolaunched Flow with the supplied input parameters.

        HTTP POST: /services/data/v{version}/actions/custom/flow/{flowApiName}

        Args:
            flowapiname: Path parameter: flowApiName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/custom/flow/{flowapiname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def quick_actions(self, version: str) -> SalesforceResponse:
        """Returns a list of global actions and object-specific actions. This resource is available in REST API version 28.0 and later. When working with actions, also refer to SObject Quick Actions. https://...

        HTTP GET: /services/data/v{version}/quickActions

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/quickActions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST ACTIONS CUSTOM BUSINESS RULES ENGINE INVOCABLE ACTIONS ENDPOINTS
    # ========================================================================

    async def run_expression_set(
        self,
        expressionsetapiname: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Expression Set Actions Invoke an active expression set. An expression set is a user-defined rule that accepts an input and returns the output based on the configured function. The configured func...

        HTTP POST: /services/data/v{version}/actions/custom/runExpressionSet/{expressionSetAPIName}

        Args:
            expressionsetapiname: Path parameter: expressionSetAPIName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/custom/runExpressionSet/{expressionsetapiname}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def run_decision_matrix(
        self,
        uniquename: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """# Decision Matrix Actions Invoke a decision matrix in a flow with the Decision Matrix Actions. A decision matrix is a user-defined table where you can look up an output based on the inputs you prov...

        HTTP POST: /services/data/v{version}/actions/custom/runDecisionMatrix/{UniqueName}

        Args:
            uniquename: Path parameter: UniqueName
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/custom/runDecisionMatrix/{uniquename}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST LIGHTNING METRICS ENDPOINTS
    # ========================================================================

    async def lightning_toggle_metrics(self, version: str) -> SalesforceResponse:
        """Return details about users who switched between Salesforce Classic and Lightning Experience. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_lightning_togglemetri...

        HTTP GET: /services/data/v{version}/sobjects/LightningToggleMetrics

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/LightningToggleMetrics"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def lightning_usage_by_page(self, version: str) -> SalesforceResponse:
        """Represents standard pages users viewed most frequently in Lightning Experience. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_lightning_usagebypagemetrics.htm

        HTTP GET: /services/data/v{version}/sobjects/LightningUsageByBrowserMetrics

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/LightningUsageByBrowserMetrics"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def lightning_usage_by_browser(self, version: str) -> SalesforceResponse:
        """Return Lightning Experience usage results grouped by browser instance. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_lightning_usagebybrowsermetrics.htm

        HTTP GET: /services/data/v{version}/sobjects/LightningUsageByBrowserMetrics

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/LightningUsageByBrowserMetrics"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def lightning_usage_by_app_type(self, version: str) -> SalesforceResponse:
        """Return the total number of Lightning Experience and Salesforce Mobile users. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_lightning_usagebyapptypemetrics.htm

        HTTP GET: /services/data/v{version}/sobjects/LightningUsageByAppTypeMetrics

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/LightningUsageByAppTypeMetrics"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def lightning_usage_by_flexi_page(self, version: str) -> SalesforceResponse:
        """Return details about the custom pages viewed most frequently in Lightning Experience. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_lightning_usagebyflexipageme...

        HTTP GET: /services/data/v{version}/sobjects/LightningUsageByFlexiPageMetrics

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/LightningUsageByFlexiPageMetrics"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def lightning_exit_by_page_metrics(self, version: str) -> SalesforceResponse:
        """Return frequency metrics about the standard pages within which users switched from Lightning Experience to Salesforce Classic. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_re...

        HTTP GET: /services/data/v{version}/sobjects/LightningExitByPageMetrics

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/LightningExitByPageMetrics"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST LIST VIEWS ENDPOINTS
    # ========================================================================

    async def list_views(
        self,
        sobject_api_name: str,
        version: str,
        limit: Optional[str] = None,
        offset: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns the list of list views for the specified sObject, including the ID and other basic information about each list view. You can also get basic information for a specific list view by ID.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/listviews

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            limit: The maximum number of records to return, between 1-2000. The default value is 25. (optional)
            offset: The first record to return. Use this parameter to paginate the results. The default value is 1. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/listviews"
        params = self._build_params(**{"limit": limit, "offset": offset})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_view_describe(
        self,
        sobject_api_name: str,
        query_locator: str,
        version: str
    ) -> SalesforceResponse:
        """Returns detailed information about a list view, including the ID, the columns, and the SOQL query.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/listviews/{QUERY_LOCATOR}/describe

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            query_locator: Path parameter: QUERY_LOCATOR
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/listviews/{query_locator}/describe"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def list_view_results(
        self,
        sobject_api_name: str,
        list_view_id: str,
        version: str,
        limit: Optional[str] = None,
        offset: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns detailed information about a list view, including the ID, the columns, and the SOQL query.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/listviews/{LIST_VIEW_ID}/results

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            list_view_id: Path parameter: LIST_VIEW_ID
            version: Path parameter: version
            limit: The maximum number of records to return, between 1-2000. The default value is 25. (optional)
            offset: The first record to return. Use this parameter to paginate the results. The default value is 1. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/listviews/{list_view_id}/results"
        params = self._build_params(**{"limit": limit, "offset": offset})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def recently_viewed_items(
        self,
        sobject_api_name: str,
        version: str,
        limit: str
    ) -> SalesforceResponse:
        """Gets the most recently accessed items that were viewed or referenced by the current user. Salesforce stores information about record views in the interface and uses it to generate a list of recentl...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/listviews/recent

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            limit: Query parameter: limit (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/listviews/recent"
        params = self._build_params(**{"limit": limit})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST LOGS ENDPOINTS
    # ========================================================================

    async def get_event_logs(self, version: str, q: str) -> SalesforceResponse:
        """Executes the specified SOQL query. If the query results are too large, the response contains the first batch of results and a query identifier in the nextRecordsUrl field of the response. The ident...

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            q: Gets the first 200 EventLogFile records ordered by most recently updated first. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_event_log_file(self, id_param: str, version: str) -> SalesforceResponse:
        """Get event log file

        HTTP GET: /services/data/v{version}/sobjects/EventLogFile/{id}/LogFile

        Args:
            id_param: Path parameter: id
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/EventLogFile/{id_param}/LogFile"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST OPEN API SCHEMA BETA ENDPOINTS
    # ========================================================================

    async def request_open_api_schema(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Requests an Open API document for the selected SObject REST API resources.

        HTTP POST: /services/data/v{version}/async/specifications/oas3

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/async/specifications/oas3"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def retrieve_open_api_schema(self, schemaid: str, version: str) -> SalesforceResponse:
        """Generate an OpenAPI 3.0 document for the sObjects REST API. This is a Beta feature that requires an activation step before it can be used.

        HTTP GET: /services/data/v{version}/async/specifications/oas3/{schemaId}

        Args:
            schemaid: Path parameter: schemaId
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/async/specifications/oas3/{schemaid}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST PROCESSES ENDPOINTS
    # ========================================================================

    async def process_approvals(self, version: str) -> SalesforceResponse:
        """Returns a list of all approval processes. Can also be used to submit a particular record if that entity supports an approval process and one has already been defined. Records can be approved and re...

        HTTP GET: /services/data/v{version}/process/approvals

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/process/approvals"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def process_approvals_submit(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Returns a list of all approval processes. Can also be used to submit a particular record if that entity supports an approval process and one has already been defined. Records can be approved and re...

        HTTP POST: /services/data/v{version}/process/approvals

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/process/approvals"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def process_rules(self, version: str) -> SalesforceResponse:
        """Returns a list of all active workflow rules. If a rule has actions, the actions will be listed under the rule. Can also be used to trigger all workflow rules that are associated with a specified re...

        HTTP GET: /services/data/v{version}/process/rules

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/process/rules"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST S OBJECT ENDPOINTS
    # ========================================================================

    async def describe_global(self, version: str) -> SalesforceResponse:
        """Lists the available objects and their metadata for your organization’s data. In addition, it provides the organization encoding, as well as the maximum batch size permitted in queries. You can use ...

        HTTP GET: /services/data/v{version}/sobjects

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def download_file(self) -> SalesforceResponse:
        """Lists the available objects and their metadata for your organization’s data. In addition, it provides the organization encoding, as well as the maximum batch size permitted in queries. You can use ...

        HTTP GET: /services/data/v54.0/sobjects/ContentVersion/0681k0000020wLFAAY/VersionData

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/data/v54.0/sobjects/ContentVersion/0681k0000020wLFAAY/VersionData"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_basic_information(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Describes the individual metadata for the specified object. Can also be used to create a new record for a given object. For example, this can be used to retrieve the metadata for the Account object...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_create(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SObject Create

        HTTP POST: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_describe(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Describes the individual metadata for the specified object. Can also be used to create a new record for a given object. For example, this can be used to retrieve the metadata for the Account object...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/describe

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/describe"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_get_deleted(
        self,
        sobject_api_name: str,
        version: str,
        start: str,
        end: str
    ) -> SalesforceResponse:
        """Retrieves the list of individual records that have been deleted within the given timespan for the specified object. SObject Get Deleted is available in API version 29.0 and later. This resource is ...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/deleted

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            start: Query parameter: start (required)
            end: Query parameter: end (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/deleted"
        params = self._build_params(**{"start": start, "end": end})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_get_updated(
        self,
        sobject_api_name: str,
        version: str,
        start: str,
        end: str
    ) -> SalesforceResponse:
        """Retrieves the list of individual records that have been updated (added or changed) within the given timespan for the specified object. SObject Get Updated is available in API version 29.0 and later...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/updated

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            start: Query parameter: start (required)
            end: Query parameter: end (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/updated"
        params = self._build_params(**{"start": start, "end": end})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_named_layouts(
        self,
        sobject_api_name: str,
        layout_name: str,
        version: str
    ) -> SalesforceResponse:
        """Retrieves information about alternate named layouts for a given object.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/describe/namedLayouts/{LAYOUT_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            layout_name: Path parameter: LAYOUT_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/describe/namedLayouts/{layout_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_rows(
        self,
        sobject_api_name: str,
        record_id: str,
        version: str
    ) -> SalesforceResponse:
        """Accesses records based on the specified object ID. Retrieves, updates, or deletes records. This resource can also be used to retrieve field values. Use the GET method to retrieve records or fields,...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{record_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_rows_update(
        self,
        sobject_api_name: str,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Accesses records based on the specified object ID. Retrieves, updates, or deletes records. This resource can also be used to retrieve field values. Use the GET method to retrieve records or fields,...

        HTTP PATCH: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{record_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_rows_delete(
        self,
        sobject_api_name: str,
        record_id: str,
        version: str
    ) -> SalesforceResponse:
        """Accesses records based on the specified object ID. Retrieves, updates, or deletes records. This resource can also be used to retrieve field values. Use the GET method to retrieve records or fields,...

        HTTP DELETE: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{record_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_rows_by_external_id(
        self,
        sobject_api_name: str,
        field_value: str,
        version: str,
        field_name: str
    ) -> SalesforceResponse:
        """Creates new records or updates existing records (upserts records) based on the value of a specified external ID field. If the specified value doesn't exist, a new record is created. If a record doe...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{FIELD_NAME}/{FIELD_VALUE}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            field_value: Path parameter: FIELD_VALUE
            version: Path parameter: version
            field_name: Path parameter: FIELD_NAME

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{field_name}/{field_value}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_blob_retrieve(
        self,
        record_id: str,
        sobject_api_name: str,
        version: str,
        blob_field: str
    ) -> SalesforceResponse:
        """Retrieves the specified blob field from an individual record.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}/{BLOB_FIELD}

        Args:
            record_id: Path parameter: RECORD_ID
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            blob_field: Path parameter: BLOB_FIELD

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{record_id}/{blob_field}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_approval_layouts(
        self,
        sobject_api_name: str,
        approval_process_name: str,
        version: str
    ) -> SalesforceResponse:
        """Returns a list of approval layouts for a specified object. Specify a particular approval process name to limit the return value to one specific approval layout. This resource is available in REST A...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/describe/approvalLayouts/{APPROVAL_PROCESS_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            approval_process_name: Path parameter: APPROVAL_PROCESS_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/describe/approvalLayouts/{approval_process_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_compact_layouts(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Returns a list of compact layouts for a specific object. This resource is available in REST API version 29.0 and later.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/describe/describe/compactLayouts

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/describe/describe/compactLayouts"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_global_layouts(self, version: str) -> SalesforceResponse:
        """Returns a list of layouts and descriptions. The list of fields and the layout name are returned.

        HTTP GET: /services/data/v{version}/sobjects/Global/describe/layouts

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/Global/describe/layouts"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_s_object_layouts(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Returns a list of layouts and descriptions. The list of fields and the layout name are returned.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/describe/layouts

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/describe/layouts"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def describe_s_object_layouts_per_record_type(
        self,
        record_type_id: str,
        sobject_api_name: str,
        version: str
    ) -> SalesforceResponse:
        """Returns a list of layouts and descriptions. The list of fields and the layout name are returned.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/describe/layouts/{RECORD_TYPE_ID}

        Args:
            record_type_id: Path parameter: RECORD_TYPE_ID
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/describe/layouts/{record_type_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_platform_action(self, version: str) -> SalesforceResponse:
        """PlatformAction is a virtual read-only object. It enables you to query for actions displayed in the UI, given a user, a context, device format, and a record ID. Examples include standard and custom ...

        HTTP GET: /services/data/v{version}/sobjects/PlatformAction

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/PlatformAction"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_quick_actions(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Returns a list of actions and their details. This resource is available in REST API version 28.0 and later. When working with actions, also refer to Quick Actions. To return a specific object’s act...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/quickActions

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/quickActions"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_rich_text_image_retrieve(
        self,
        record_id: str,
        content_reference_id: str,
        sobject_api_name: str,
        field_name: str,
        version: str
    ) -> SalesforceResponse:
        """Retrieves the specified image data from a specific rich text area field in a given record. contentReferenceId The reference ID that uniquely identifies an image within a rich text area field. You c...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}/richTextImageFields/{FIELD_NAME}/{CONTENT_REFERENCE_ID}

        Args:
            record_id: Path parameter: RECORD_ID
            content_reference_id: Path parameter: CONTENT_REFERENCE_ID
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            field_name: Path parameter: FIELD_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{record_id}/richTextImageFields/{field_name}/{content_reference_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_relationships(
        self,
        relationship_field_name: str,
        record_id: str,
        sobject_api_name: str,
        version: str
    ) -> SalesforceResponse:
        """Accesses records by traversing object relationships via friendly URLs. You can retrieve, update, or delete the record associated with the traversed relationship field. If there are multiple related...

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}/{RELATIONSHIP_FIELD_NAME}

        Args:
            relationship_field_name: Path parameter: RELATIONSHIP_FIELD_NAME
            record_id: Path parameter: RECORD_ID
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/{record_id}/{relationship_field_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_suggested_articles(
        self,
        sobject_api_name: str,
        version: str,
        description: str,
        language: str,
        subject: str
    ) -> SalesforceResponse:
        """Returns a list of suggested Salesforce Knowledge articles for a case, work order, or work order line item.

        HTTP GET: /services/data/v{version}/sobjects/{SOBJECT_API_NAME}/suggestedArticles

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            description: Query parameter: description (required)
            language: Query parameter: language (required)
            subject: Query parameter: subject (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/{sobject_api_name}/suggestedArticles"
        params = self._build_params(**{"description": description, "language": language, "subject": subject})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_user_password(self, user_id: str, version: str) -> SalesforceResponse:
        """Set, reset, or get information about a user password. This resource is available in REST API version 24.0 and later.

        HTTP GET: /services/data/v{version}/sobjects/User/{USER_ID}/password

        Args:
            user_id: Path parameter: USER_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/User/{user_id}/password"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def s_object_self_service_user_password(self, user_id: str, version: str) -> SalesforceResponse:
        """Set, reset, or get information about a user password. This resource is available in REST API version 24.0 and later.

        HTTP GET: /services/data/v{version}/sobjects/SelfServiceUser/{USER_ID}/password

        Args:
            user_id: Path parameter: USER_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/SelfServiceUser/{user_id}/password"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def compact_layouts(self, version: str, q: str) -> SalesforceResponse:
        """Returns a list of compact layouts for multiple objects. This resource is available in REST API version 31.0 and later.

        HTTP GET: /services/data/v{version}/compactLayouts

        Args:
            version: Path parameter: version
            q: object list (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/compactLayouts"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST SCHEDULING ENDPOINTS
    # ========================================================================

    async def scheduling(self, version: str) -> SalesforceResponse:
        """Return frequency metrics about the standard pages within which users switched from Lightning Experience to Salesforce Classic. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_re...

        HTTP GET: /services/data/v{version}/scheduling

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/scheduling"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_appointment_slots(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Returns a list of available appointment time slots for a resource based on given work type group and territories. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/requests_l...

        HTTP POST: /services/data/v{version}/scheduling/getAppointmentSlots

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/scheduling/getAppointmentSlots"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_appointment_candidates(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Returns a list of available service resources (appointment candidates) based on work type group and service territories. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/req...

        HTTP POST: /services/data/v{version}/scheduling/getAppointmentCandidates

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/scheduling/getAppointmentCandidates"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST SEARCH ENDPOINTS
    # ========================================================================

    async def search(self, version: str, q: str) -> SalesforceResponse:
        """Executes the specified SOSL search. The search string must be URL-encoded. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_search.htm

        HTTP GET: /services/data/v{version}/search

        Args:
            version: Path parameter: version
            q: A SOSL statement that is properly URL-encoded. (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/search"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def search_scope_and_order(self, version: str) -> SalesforceResponse:
        """Returns an ordered list of objects in the default global search scope of a logged-in user. Global search keeps track of which objects the user interacts with and how often and arranges the search r...

        HTTP GET: /services/data/v{version}/search/scopeOrder

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/search/scopeOrder"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def search_result_layouts(self, version: str, q: str) -> SalesforceResponse:
        """Returns search result layout information for the objects in the query string. For each object, this call returns the list of fields displayed on the search results page as columns, the number of ro...

        HTTP GET: /services/data/v{version}/search/layout

        Args:
            version: Path parameter: version
            q: Comma delimited object list (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/search/layout"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def search_for_records_suggested_by_autocomplete_and_instant_results(
        self,
        version: str,
        q: str,
        sobject: str,
        data: Optional[Dict[str, Any]] = None,
        type_param: Optional[str] = None,
        fields: Optional[str] = None,
        dynamicfields: Optional[str] = None,
        groupid: Optional[str] = None,
        ignoreunsupportedsobjects: Optional[str] = None,
        limit: Optional[str] = None,
        networkid: Optional[str] = None,
        topicid: Optional[str] = None,
        userid: Optional[str] = None,
        usesearchscope: Optional[str] = None,
        where: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns a list of suggested records whose names match the user’s search string. The suggestions resource provides autocomplete results and instant results for users to navigate directly to likely r...

        HTTP GET: /services/data/v{version}/search/suggestions

        Args:
            version: Path parameter: version
            data: Request body data
            type_param: Required when the sobject value is feedItem. Including this parameter for all other sobject values doesn’t affect the query. Specifies that the type of Feed is questions. Valid value: question. (optional)
            fields: Optional. Used for creating lookup queries. Specify multiple fields using a comma-separated list. Specifies which lookup fields to be returned in the response. (optional)
            dynamicfields: Optional. Available in API version 48.0 and later. Used to return additional dynamic fields. Specify multiple options using a comma-separated list. For example, if dynamicFields=secondaryField then... (optional)
            groupid: Optional. Specifies one or more unique identifiers of one or more groups that the question to return was posted to. Specify multiple groups using a comma-separated list. This parameter is only appl... (optional)
            ignoreunsupportedsobjects: Optional. Specifies what to do if unsupported objects are included in the request. If false and an unsupported object is included, an error is returned. If true and an unsupported object is include... (optional)
            limit: Optional. Specifies the maximum number of suggested records to return. If a limit isn’t specified, 5 records are returned by default. If there are more suggested records than the limit specified, t... (optional)
            networkid: Optional. Specifies one or more unique identifiers for the community(ies) that the question to return is associated to. Specify multiple communities using a comma-separated list. This parameter is ... (optional)
            topicid: Optional. Specifies the unique identifier of the single topic that the question to return was tagged as. This parameter is only applicable when the parameter type equals question. (optional)
            userid: Optional. Specifies one or more unique identifiers of one or more users who authored the question to return. Specify multiple users using a comma-separated list. This parameter is only applicable w... (optional)
            usesearchscope: Optional. Available in API version 40.0 and later. The default value is false. If false, the objects specified in the request are used to suggest records. If true, in addition to the objects specif... (optional)
            where: Optional. A filter that follows the same syntax as the SOQL WHERE clause. URLs encode the expression. Use the clause for an object, or globally for all compatible objects. An example of an object-s... (optional)
            q: Query parameter: q (required)
            sobject: Query parameter: sobject (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/search/suggestions"
        params = self._build_params(**{"q": q, "sobject": sobject, "type": type_param, "fields": fields, "dynamicFields": dynamicfields, "groupId": groupid, "ignoreUnsupportedSObjects": ignoreunsupportedsobjects, "limit": limit, "networkId": networkid, "topicId": topicid, "userId": userid, "useSearchScope": usesearchscope, "where": where})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def search_suggested_article_title_matches(
        self,
        version: str,
        q: str,
        publishstatus: str,
        language: str,
        data: Optional[Dict[str, Any]] = None,
        articletypes: Optional[str] = None,
        categories: Optional[str] = None,
        channel: Optional[str] = None,
        limit: Optional[str] = None,
        topics: Optional[str] = None,
        validationstatus: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns a list of Salesforce Knowledge article titles that match the user’s search query string. Provides a shortcut to navigate directly to likely relevant articles before the user performs a sear...

        HTTP GET: /services/data/v{version}/search/suggestions

        Args:
            version: Path parameter: version
            data: Request body data
            articletypes: Optional. Three-character ID prefixes indicating the desired article types. You can specify multiple values for this parameter in a single REST call, by repeating the parameter name for each value.... (optional)
            categories: Optional. The name of the data category group and name of the data category for desired articles, expressed as a JSON mapping. You can specify multiple data category group and data category pairs i... (optional)
            channel: Optional. The channel where the matching articles are visible. Valid values: AllChannels–Visible in all channels the user has access to App–Visible in the internal Salesforce Knowledge application ... (optional)
            limit: Optional. Specifies the maximum number of articles to return. If there are more suggested articles than the limit specified, the response body’s hasMoreResults property is true. (optional)
            topics: Optional. The topic of the returned articles. For example: topics=outlook&topics=email. (optional)
            validationstatus: Optional. The validation status of returned articles. (optional)
            q: Query parameter: q (required)
            publishstatus: Query parameter: publishStatus (required)
            language: Query parameter: language (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/search/suggestions"
        params = self._build_params(**{"q": q, "publishStatus": publishstatus, "language": language, "articleTypes": articletypes, "categories": categories, "channel": channel, "limit": limit, "topics": topics, "validationStatus": validationstatus})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def search_suggested_queries(
        self,
        version: str,
        q: str,
        language: str,
        data: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
        limit: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns a list of suggested searches based on the user’s query string text matching searches that other users have performed in Salesforce Knowledge. Provides a way to improve search effectiveness,...

        HTTP GET: /services/data/v{version}/search/suggestSearchQueries

        Args:
            version: Path parameter: version
            data: Request body data
            channel: Optional. Specifies the Salesforce Knowledge channel where the article can be viewed. Valid values: AllChannels–Visible in all channels the user has access to App–Visible in the internal Salesforce... (optional)
            limit: Optional. Specifies the maximum number of suggested searches to return. If there are more suggested queries than the limit specified, the response body’s hasMoreResults property is true. (optional)
            q: Query parameter: q (required)
            language: Query parameter: language (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/search/suggestSearchQueries"
        params = self._build_params(**{"q": q, "language": language, "channel": channel, "limit": limit})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def parameterized_search(self, version: str, q: str) -> SalesforceResponse:
        """Executes a simple RESTful search using parameters instead of a SOSL clause. Indicate parameters in a URL in the GET method. Or, use POST for more complex JSON searches.

        HTTP GET: /services/data/v{version}/parameterizedSearch

        Args:
            version: Path parameter: version
            q: A search string that is properly URL-encoded (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/parameterizedSearch"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def relevant_items(
        self,
        version: str,
        lastupdatedid: Optional[str] = None,
        sobjects: Optional[str] = None,
        sobject_lastupdatedid: Optional[str] = None
    ) -> SalesforceResponse:
        """Gets the current user’s most relevant items. Relevant items include records for objects in the user’s global search scope and also most recently used (MRU) objects. Relevant items include up to 50 ...

        HTTP GET: /services/data/v{version}/sobjects/relevantItems

        Args:
            version: Path parameter: version
            lastupdatedid: Optional. Compares the entire current list of relevant items to a previous version, if available. Specify the lastUpdatedId value returned in a previous response. (optional)
            sobjects: Optional. To scope the results to a particular object or set of objects, specify the name for one or more sObjects. (optional)
            sobject_lastupdatedid: Optional. Compares the current list of relevant items for this particular object to a previous version, if available. Specify the lastUpdatedId value returned in a previous response. Note You can o... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/sobjects/relevantItems"
        params = self._build_params(**{"lastUpdatedId": lastupdatedid, "sobjects": sobjects, "sobject.lastUpdatedId": sobject_lastupdatedid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # REST SUPPORT AND KNOWLEDGE ENDPOINTS
    # ========================================================================

    async def embedded_service_configuration_describe(self, embedded_service_config_developername: str, version: str) -> SalesforceResponse:
        """Retrieves the values for your Embedded Service deployment configuration, including the branding colors, font, and site URL. You must be logged in to the account that owns the EmbeddedServiceConfigD...

        HTTP GET: /services/data/v{version}/support/embeddedservice/configuration/{EMBEDDED_SERVICE_CONFIG_DEVELOPERNAME}

        Args:
            embedded_service_config_developername: Path parameter: EMBEDDED_SERVICE_CONFIG_DEVELOPERNAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/support/embeddedservice/configuration/{embedded_service_config_developername}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def data_category_groups(
        self,
        version: str,
        sobjectname: str,
        topcategoriesonly: Optional[str] = None
    ) -> SalesforceResponse:
        """Returns the list of list views for the specified sObject, including the ID and other basic information about each list view. You can also get basic information for a specific list view by ID.

        HTTP GET: /services/data/v{version}/support/dataCategoryGroups

        Args:
            version: Path parameter: version
            topcategoriesonly: Query parameter: topCategoriesOnly (optional)
            sobjectname: Query parameter: sObjectName (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/support/dataCategoryGroups"
        params = self._build_params(**{"sObjectName": sobjectname, "topCategoriesOnly": topcategoriesonly})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def data_category_detail(
        self,
        group: str,
        category: str,
        version: str,
        sobjectname: Optional[str] = None
    ) -> SalesforceResponse:
        """Get data category details and the child categories by a given category.

        HTTP GET: /services/data/v{version}/support/dataCategoryGroups/{GROUP}/dataCategories/{CATEGORY}

        Args:
            group: Path parameter: GROUP
            category: Path parameter: CATEGORY
            version: Path parameter: version
            sobjectname: Query parameter: sObjectName (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/support/dataCategoryGroups/{group}/dataCategories/{category}"
        params = self._build_params(**{"sObjectName": sobjectname})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def articles_list(
        self,
        version: str,
        q: Optional[str] = None,
        channel: Optional[str] = None,
        categories: Optional[str] = None,
        querymethod: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        pagesize: Optional[str] = None,
        pagenumber: Optional[str] = None
    ) -> SalesforceResponse:
        """Get a page of online articles for the given language and category through either search or query.

        HTTP GET: /services/data/v{version}/support/knowledgeArticles

        Args:
            version: Path parameter: version
            q: Optional, Performs an SOSL search. If the query string is null, empty, or not given, an SOQL query runs. (optional)
            channel: Optional, defaults to user’s context. For information on channel values, see Valid channel values. App: Visible in the internal Salesforce Knowledge application Pkb: Visible in the public knowledge... (optional)
            categories: Optional, defaults to None. Category group must be unique in each group:category pair, otherwise you get ARGUMENT_OBJECT_PARSE_ERROR (optional)
            querymethod: values are: AT, BELOW, ABOVE, ABOVE_OR_BELOW. Only valid when categories are specified, defaults to ABOVE_OR_BELOW (optional)
            sort: Optional, a sortable field name LastPublishedDate, CreatedDate, Title, ViewScore. Defaults to LastPublishedDate for query and relevance for search. (optional)
            order: Optional, either ASC or DESC, defaults to DESC. Valid only when sort is valid (optional)
            pagesize: Optional, defaults to 20. Valid range 1 to 100 (optional)
            pagenumber: Optional, defaults to 1 (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/support/knowledgeArticles"
        params = self._build_params(**{"q": q, "channel": channel, "categories": categories, "queryMethod": querymethod, "sort": sort, "order": order, "pageSize": pagesize, "pageNumber": pagenumber})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def articles_details(
        self,
        article_id: str,
        version: str,
        channel: Optional[str] = None,
        updateviewstat: Optional[str] = None,
        isurlname: Optional[str] = None
    ) -> SalesforceResponse:
        """Get all online article fields, accessible to the user.

        HTTP GET: /services/data/v{version}/support/knowledgeArticles/{ARTICLE_ID}

        Args:
            article_id: Path parameter: ARTICLE_ID
            version: Path parameter: version
            channel: Optional, defaults to user’s context. For information on channel values, see Valid channel Values. App: Visible in the internal Salesforce Knowledge application Pkb: Visible in the public knowledge... (optional)
            updateviewstat: Optional, defaults to true. If true, API updates the view count in the given channel as well as the total view count. (optional)
            isurlname: Optional, defaults to false. If true, indicates that the last portion of the endpoint is a URL name instead of an article ID. Available in API v44.0 and later (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/support/knowledgeArticles/{article_id}"
        params = self._build_params(**{"channel": channel, "updateViewStat": updateviewstat, "isUrlName": isurlname})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def retrieve_knowledge_language_settings(self, version: str) -> SalesforceResponse:
        """Returns the existing Knowledge language settings, including the default knowledge language and a list of supported Knowledge language information. https://developer.salesforce.com/docs/atlas.en-us....

        HTTP GET: /services/data/v{version}/knowledgeManagement/settings

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/knowledgeManagement/settings"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE AP IS END TO END USE CASES AND SAMPLES SCAPI END2 END SHOPPER JOURNEY ENDPOINTS
    # ========================================================================

    async def registered_customer_details(
        self,
        customerid: str,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Registered customer details

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customerId}

        Args:
            customerid: Path parameter: {customerId
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customerid}"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _1b_product_details(
        self,
        organizationid: str,
        ids: str,
        siteid: str
    ) -> SalesforceResponse:
        """1b. Product Details

        HTTP GET: /product/shopper-products/v1/organizations/{organizationId}/products

        Args:
            organizationid: Path parameter: {organizationId
            ids: Query parameter: ids (required)
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/product/shopper-products/v1/organizations/{organizationid}/products"
        params = self._build_params(**{"ids": ids, "siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _1c_product_search(
        self,
        organizationid: str,
        q: str,
        siteid: str
    ) -> SalesforceResponse:
        """1c. Product Search

        HTTP GET: /search/shopper-search/v1/organizations/{organizationId}/product-search

        Args:
            organizationid: Path parameter: {organizationId
            q: Query parameter: q (required)
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/search/shopper-search/v1/organizations/{organizationid}/product-search"
        params = self._build_params(**{"q": q, "siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _1c_product_search_suggestions(
        self,
        organizationid: str,
        q: str,
        siteid: str
    ) -> SalesforceResponse:
        """1c. Product Search Suggestions

        HTTP GET: /search/shopper-search/v1/organizations/{organizationId}/search-suggestions

        Args:
            organizationid: Path parameter: {organizationId
            q: Query parameter: q (required)
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/search/shopper-search/v1/organizations/{organizationid}/search-suggestions"
        params = self._build_params(**{"q": q, "siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _1d_promotion_details(
        self,
        organizationid: str,
        ids: str,
        siteid: str
    ) -> SalesforceResponse:
        """1d. Promotion Details

        HTTP GET: /pricing/shopper-promotions/v1/organizations/{organizationId}/promotions

        Args:
            organizationid: Path parameter: {organizationId
            ids: Query parameter: ids (required)
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/pricing/shopper-promotions/v1/organizations/{organizationid}/promotions"
        params = self._build_params(**{"ids": ids, "siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2c_create_basket(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2c. Create Basket

        HTTP POST: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2c_1_delete_baskets_if_needed(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2c-1. Delete Baskets (if needed)

        HTTP DELETE: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2c_2_get_basket(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2c-2. Get Basket

        HTTP GET: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2d_add_item_to_basket(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2d. Add Item To Basket

        HTTP POST: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}/items

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}/items"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2d_1_add_billing_address(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2d-1. Add Billing Address

        HTTP PUT: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}/billing-address

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}/billing-address"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2d_2_add_customer_info(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2d-2. Add Customer Info

        HTTP PUT: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}/customer

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}/customer"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2e_update_shipment_for_basket(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2e. Update Shipment for Basket

        HTTP PATCH: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}/shipments/me

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}/shipments/me"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _2f_add_payment(
        self,
        organizationid: str,
        basketid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """2f. Add Payment

        HTTP POST: /checkout/shopper-baskets/v1/organizations/{organizationId}/baskets/{basketId}/payment-instruments

        Args:
            organizationid: Path parameter: {organizationId
            basketid: Path parameter: basketId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/v1/organizations/{organizationid}/baskets/{basketid}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def _3a_create_order(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """3a. CREATE ORDER

        HTTP POST: /checkout/shopper-orders/v1/organizations/{organizationId}/orders

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_orders(
        self,
        organizationid: str,
        orderno: str,
        siteid: str
    ) -> SalesforceResponse:
        """Get Orders

        HTTP GET: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/{orderNo}

        Args:
            organizationid: Path parameter: {organizationId
            orderno: Path parameter: orderNo
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/{orderno}"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER BASKETS ENDPOINTS
    # ========================================================================

    async def scapi_create_basket(
        self,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets

        Args:
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get basket

        HTTP GET: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_merge_basket(
        self,
        organizationid: str,
        version: str,
        siteid: str,
        createdestinationbasket: str,
        productitemmergemode: str
    ) -> SalesforceResponse:
        """SCAPI - Merge basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/actions/merge

        Args:
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)
            createdestinationbasket: Query parameter: createDestinationBasket (required)
            productitemmergemode: Query parameter: productItemMergeMode (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/actions/merge"
        params = self._build_params(**{"siteId": siteid, "createDestinationBasket": createdestinationbasket, "productItemMergeMode": productitemmergemode})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update basket

        HTTP PATCH: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_delete_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Delete basket

        HTTP DELETE: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_billing_address_on_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update billing address on basket

        HTTP PUT: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/billing-address

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/billing-address"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_customer_on_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update customer on basket

        HTTP PUT: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/customer

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/customer"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_add_coupon_to_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Add coupon to basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/coupons

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/coupons"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_coupon_from_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Remove coupon from basket

        HTTP DELETE: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/coupons/test-couponItemId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/coupons/test-couponItemId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_add_gift_certificate_item_to_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Add gift certificate item to basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/gift-certificate-items

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/gift-certificate-items"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_gift_certificate_item_from_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Remove gift certificate item from basket

        HTTP DELETE: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/gift-certificate-items/test-giftCertificateItemId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/gift-certificate-items/test-giftCertificateItemId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_add_item_to_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Add item to basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/items

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/items"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_item_from_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Remove item from basket

        HTTP DELETE: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/items/test-itemId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/items/test-itemId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_item_in_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update item in basket

        HTTP PATCH: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/items/test-itemId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/items/test-itemId"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_add_credit_card_payment_instrument_to_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Add CREDIT_CARD payment instrument to basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/payment-instruments

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_add_gift_certificate_payment_instrument_to_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Add GIFT_CERTIFICATE payment instrument to basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/payment-instruments

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_add_other_payment_instrument_to_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Add other payment instrument to basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/payment-instruments

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_payment_instrument_from_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Remove payment instrument from basket

        HTTP DELETE: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/payment-instruments/test-paymentInstrumentId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/payment-instruments/test-paymentInstrumentId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_payment_instrument(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update payment instrument

        HTTP PATCH: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/payment-instruments/test-paymentInstrumentId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/payment-instruments/test-paymentInstrumentId"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_shipment_for_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create shipment for basket

        HTTP POST: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/shipments

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/shipments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_shipment_from_basket(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Remove shipment from basket

        HTTP DELETE: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/shipments/test-shpmentId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/shipments/test-shpmentId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_shipment(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update shipment

        HTTP PATCH: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/shipments/test-shpmentId

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/shipments/test-shpmentId"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_shipping_address_on_shipment(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update shipping address on shipment

        HTTP PUT: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/shipments/test-shpmentId/shipping-address

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/shipments/test-shpmentId/shipping-address"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_shipping_method_on_shipment(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update shipping method on shipment

        HTTP PUT: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/shipments/test-shpmentId/shipping-method

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/shipments/test-shpmentId/shipping-method"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_shipping_methods_for_shipment(
        self,
        basket_id: str,
        organizationid: str,
        version: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get shipping methods for shipment

        HTTP GET: /checkout/shopper-baskets/{version}/organizations/{organizationId}/baskets/{basket_id}/shipments/test-shpmentId/shipping-methods

        Args:
            basket_id: Path parameter: {basket_id
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-baskets/{version}/organizations/{organizationid}/baskets/{basket_id}/shipments/test-shpmentId/shipping-methods"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER CONTEXT ENDPOINTS
    # ========================================================================

    async def scapi_create_shopper_context(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create shopper context

        HTTP PUT: /shopper/shopper-context/v1/organizations/{organizationId}/shopper-context/test-shoppersUniqueIdentifier

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/shopper-context/v1/organizations/{organizationid}/shopper-context/test-shoppersUniqueIdentifier"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_shopper_context(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Get shopper context

        HTTP GET: /shopper/shopper-context/v1/organizations/{organizationId}/shopper-context/test-shoppersUniqueIdentifier

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/shopper-context/v1/organizations/{organizationid}/shopper-context/test-shoppersUniqueIdentifier"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_delete_shopper_context(self, organizationid: str) -> SalesforceResponse:
        """SCAPI - Delete shopper context

        HTTP DELETE: /shopper/shopper-context/v1/organizations/{organizationId}/shopper-context/test-shoppersUniqueIdentifier

        Args:
            organizationid: Path parameter: {organizationId

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/shopper-context/v1/organizations/{organizationid}/shopper-context/test-shoppersUniqueIdentifier"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_shopper_context(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SCAPI - Update shopper context

        HTTP PATCH: /shopper/shopper-context/v1/organizations/{organizationId}/shopper-context/test-shoppersUniqueIdentifier

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/shopper-context/v1/organizations/{organizationid}/shopper-context/test-shoppersUniqueIdentifier"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER CUSTOMERS ENDPOINTS
    # ========================================================================

    async def scapi_register_new_customer(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Register new customer

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_reset_password_token(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Get reset password token

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/password/actions/create-reset-token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/password/actions/create-reset-token"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_reset_customer_password(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Reset customer password

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/password/actions/reset

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/password/actions/reset"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_customer(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update customer

        HTTP PATCH: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_customer_address(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create customer address

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/addresses

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/addresses"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_address(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer address

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/addresses/test-addressId

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/addresses/test-addressId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_customer_address(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update customer address

        HTTP PATCH: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/addresses/test-addressId

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/addresses/test-addressId"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_customer_address(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Remove customer address

        HTTP DELETE: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/addresses/test-addressId

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/addresses/test-addressId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_baskets(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer baskets

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/baskets

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/baskets"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_orders(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer orders

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/orders

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/orders"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_customer_password(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update customer password

        HTTP PUT: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/password

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/password"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_credit_card_customer_payment_instrument(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create CREDIT_CARD customer payment instrument

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/payment-instruments

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_gift_certificate_customer_payment_instrument(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create GIFT_CERTIFICATE customer payment instrument

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/payment-instruments

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_other_customer_payment_instrument(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create other customer payment instrument

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/payment-instruments

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_payment_instrument(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer payment instrument

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/payment-instruments/test-paymentInstrumentId

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/payment-instruments/test-paymentInstrumentId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_delete_customer_payment_instrument(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Delete customer payment instrument

        HTTP DELETE: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/payment-instruments/test-paymentInstrumentId

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/payment-instruments/test-paymentInstrumentId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_product_lists(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer product lists

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_customer_product_list(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create customer product list

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_delete_customer_product_list(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Delete customer product list

        HTTP DELETE: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_product_list(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer product list

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_customer_product_list(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update customer product list

        HTTP PATCH: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_customer_product_list_item(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create customer product list item

        HTTP POST: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_delete_customer_product_list_item(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Delete customer product list item

        HTTP DELETE: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items/1d447daa4d25805fd682bd4ce1

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items/1d447daa4d25805fd682bd4ce1"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_customer_product_list_item(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get customer product list item

        HTTP GET: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items/1d447daa4d25805fd682bd4ce1

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items/1d447daa4d25805fd682bd4ce1"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_customer_product_list_item(
        self,
        organizationid: str,
        customer_id: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update customer product list item

        HTTP PATCH: /customer/shopper-customers/v1/organizations/{organizationId}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items/1d447daa4d25805fd682bd4ce1

        Args:
            organizationid: Path parameter: {organizationId
            customer_id: Path parameter: customer_id
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/customer/shopper-customers/v1/organizations/{organizationid}/customers/{customer_id}/product-lists/bcedkiWbxCM2MaaadkRhB2IBzM/items/1d447daa4d25805fd682bd4ce1"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER EXPERIENCE TURN OFF STOREFRONT PW PROTECTION ENDPOINTS
    # ========================================================================

    async def scapi_get_pages_plp(
        self,
        organizationid: str,
        version: str,
        siteid: str,
        aspecttypeid: str,
        categoryid: str,
        aspectattributes: str,
        locale: str,
        parameters: Optional[str] = None
    ) -> SalesforceResponse:
        """SCAPI - Get Pages (PLP)

        HTTP GET: /experience/shopper-experience/{version}/organizations/{organizationId}/pages

        Args:
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)
            aspecttypeid: Query parameter: aspectTypeId (required)
            categoryid: Query parameter: categoryId (required)
            aspectattributes: Query parameter: aspectAttributes (required)
            locale: Query parameter: locale (required)
            parameters: Query parameter: parameters (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/experience/shopper-experience/{version}/organizations/{organizationid}/pages"
        params = self._build_params(**{"siteId": siteid, "aspectTypeId": aspecttypeid, "categoryId": categoryid, "aspectAttributes": aspectattributes, "locale": locale, "parameters": parameters})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_pages_pdp(
        self,
        organizationid: str,
        version: str,
        siteid: str,
        aspecttypeid: str,
        categoryid: str,
        aspectattributes: str,
        locale: str,
        productid: Optional[str] = None,
        parameters: Optional[str] = None
    ) -> SalesforceResponse:
        """SCAPI - Get Pages (PDP)

        HTTP GET: /experience/shopper-experience/{version}/organizations/{organizationId}/pages

        Args:
            organizationid: Path parameter: {organizationId
            version: Path parameter: version
            siteid: Query parameter: siteId (required)
            aspecttypeid: Query parameter: aspectTypeId (required)
            categoryid: Query parameter: categoryId (required)
            productid: Query parameter: productId (optional)
            aspectattributes: Query parameter: aspectAttributes (required)
            locale: Query parameter: locale (required)
            parameters: Query parameter: parameters (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/experience/shopper-experience/{version}/organizations/{organizationid}/pages"
        params = self._build_params(**{"siteId": siteid, "aspectTypeId": aspecttypeid, "categoryId": categoryid, "aspectAttributes": aspectattributes, "locale": locale, "productId": productid, "parameters": parameters})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_page(
        self,
        page_id: str,
        organizationid: str,
        siteid: str,
        locale: str,
        parameters: Optional[str] = None,
        aspectattributes: Optional[str] = None
    ) -> SalesforceResponse:
        """SCAPI - Get Page

        HTTP GET: /experience/shopper-experience/v1/organizations/{organizationId}/pages/{page_id}

        Args:
            page_id: Path parameter: {page_id
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)
            locale: Query parameter: locale (required)
            parameters: Query parameter: parameters (optional)
            aspectattributes: Query parameter: aspectAttributes (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/experience/shopper-experience/v1/organizations/{organizationid}/pages/{page_id}"
        params = self._build_params(**{"siteId": siteid, "locale": locale, "parameters": parameters, "aspectAttributes": aspectattributes})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER ORDERS ENDPOINTS
    # ========================================================================

    async def scapi_create_order(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create order

        HTTP POST: /checkout/shopper-orders/v1/organizations/{organizationId}/orders

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_order(self, organizationid: str, siteid: str) -> SalesforceResponse:
        """SCAPI - Get order

        HTTP GET: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/test-orderId

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/test-orderId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_credit_card_payment_instrument_for_order(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create CREDIT_CARD payment instrument for order

        HTTP POST: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/test-orderId/payment-instruments

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/test-orderId/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_create_gift_certificate_payment_instrument_for_order(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Create GIFT_CERTIFICATE payment instrument for order

        HTTP POST: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/test-orderId/payment-instruments

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/test-orderId/payment-instruments"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_remove_payment_instrument_from_order(self, organizationid: str, siteid: str) -> SalesforceResponse:
        """SCAPI - Remove payment instrument from order

        HTTP DELETE: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/test-orderId/payment-instruments/test-paymentInstrumentId

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/test-orderId/payment-instruments/test-paymentInstrumentId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_update_payment_instrument_of_order(
        self,
        organizationid: str,
        siteid: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SCAPI - Update payment instrument of order

        HTTP PATCH: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/test-orderId/payment-instruments/test-paymentInstrumentId

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/test-orderId/payment-instruments/test-paymentInstrumentId"
        params = self._build_params(**{"siteId": siteid})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_payment_methods_for_order(self, organizationid: str, siteid: str) -> SalesforceResponse:
        """SCAPI - Get payment methods for order

        HTTP GET: /checkout/shopper-orders/v1/organizations/{organizationId}/orders/test-orderId/payment-methods

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/checkout/shopper-orders/v1/organizations/{organizationid}/orders/test-orderId/payment-methods"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER PRODUCTS ENDPOINTS
    # ========================================================================

    async def scapi_get_products(
        self,
        organizationid: str,
        ids: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get products

        HTTP GET: /product/shopper-products/v1/organizations/{organizationId}/products

        Args:
            organizationid: Path parameter: {organizationId
            ids: Query parameter: ids (required)
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/product/shopper-products/v1/organizations/{organizationid}/products"
        params = self._build_params(**{"ids": ids, "siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_product(self, organizationid: str, siteid: str) -> SalesforceResponse:
        """SCAPI - Get product

        HTTP GET: /product/shopper-products/v1/organizations/{organizationId}/products/test-productId

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/product/shopper-products/v1/organizations/{organizationid}/products/test-productId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_categories(
        self,
        organizationid: str,
        ids: str,
        siteid: str
    ) -> SalesforceResponse:
        """SCAPI - Get categories

        HTTP GET: /product/shopper-products/v1/organizations/{organizationId}/categories

        Args:
            organizationid: Path parameter: {organizationId
            ids: Query parameter: ids (required)
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/product/shopper-products/v1/organizations/{organizationid}/categories"
        params = self._build_params(**{"ids": ids, "siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_category(self, organizationid: str, siteid: str) -> SalesforceResponse:
        """SCAPI - Get category

        HTTP GET: /product/shopper-products/v1/organizations/{organizationId}/categories/test-categoryId

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/product/shopper-products/v1/organizations/{organizationid}/categories/test-categoryId"
        params = self._build_params(**{"siteId": siteid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SHOPPER SEARCH ENDPOINTS
    # ========================================================================

    async def scapi_perform_product_search(
        self,
        organizationid: str,
        siteid: str,
        q: str,
        refine: str,
        expand: str
    ) -> SalesforceResponse:
        """SCAPI - Perform product search

        HTTP GET: /search/shopper-search/v1/organizations/{organizationId}/product-search

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)
            q: Query parameter: q (required)
            refine: Query parameter: refine (required)
            refine: Query parameter: refine (required)
            expand: Query parameter: expand (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/search/shopper-search/v1/organizations/{organizationid}/product-search"
        params = self._build_params(**{"siteId": siteid, "q": q, "refine": refine, "expand": expand})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def scapi_get_search_suggestions(
        self,
        organizationid: str,
        siteid: str,
        q: str
    ) -> SalesforceResponse:
        """SCAPI - Get search suggestions

        HTTP GET: /search/shopper-search/v1/organizations/{organizationId}/search-suggestions

        Args:
            organizationid: Path parameter: {organizationId
            siteid: Query parameter: siteId (required)
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/search/shopper-search/v1/organizations/{organizationid}/search-suggestions"
        params = self._build_params(**{"siteId": siteid, "q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS ADMIN ENDPOINTS
    # ========================================================================

    async def o_auth_token_from_account_manager(self, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """OAuth token from account manager

        HTTP POST: /dwsso/oauth2/access_token

        Args:
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/dwsso/oauth2/access_token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_admin_create_update_tenant(
        self,
        version: str,
        tenant: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SLAS Admin - create/update tenant

        HTTP PUT: /shopper/auth-admin/{version}/tenants/{tenant}

        Args:
            version: Path parameter: {version
            tenant: Path parameter: {tenant
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_admin_retrieve_tenant(self, version: str, tenant: str) -> SalesforceResponse:
        """SLAS Admin - retrieve tenant

        HTTP GET: /shopper/auth-admin/{version}/tenants/{tenant}

        Args:
            version: Path parameter: {version
            tenant: Path parameter: {tenant

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_admin_create_update_public_client(
        self,
        public_client_id: str,
        tenant: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SLAS Admin - create/update public client

        HTTP PUT: /shopper/auth-admin/{version}/tenants/{tenant}/clients/{public_client_id}

        Args:
            public_client_id: Path parameter: {public_client_id
            tenant: Path parameter: {tenant
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}/clients/{public_client_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_admin_create_update_private_client(
        self,
        private_client_id: str,
        version: str,
        tenant: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SLAS Admin - create/update private client

        HTTP PUT: /shopper/auth-admin/{version}/tenants/{tenant}/clients/{private_client_id}

        Args:
            private_client_id: Path parameter: {private_client_id
            version: Path parameter: version
            tenant: Path parameter: {tenant
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}/clients/{private_client_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_admin_retrieve_client(
        self,
        public_client_id: str,
        tenant: str,
        version: str
    ) -> SalesforceResponse:
        """SLAS Admin - retrieve client

        HTTP GET: /shopper/auth-admin/{version}/tenants/{tenant}/clients/{public_client_id}

        Args:
            public_client_id: Path parameter: {public_client_id
            tenant: Path parameter: {tenant
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}/clients/{public_client_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_admin_retrieve_clients(self, version: str, tenant: str) -> SalesforceResponse:
        """SLAS Admin - retrieve clients

        HTTP GET: /shopper/auth-admin/{version}/tenants/{tenant}/clients

        Args:
            version: Path parameter: {version
            tenant: Path parameter: {tenant

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}/clients"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_admin_delete_client(
        self,
        public_client_id: str,
        tenant: str,
        version: str
    ) -> SalesforceResponse:
        """SLAS Admin - delete client

        HTTP DELETE: /shopper/auth-admin/{version}/tenants/{tenant}/clients/{public_client_id}

        Args:
            public_client_id: Path parameter: {public_client_id
            tenant: Path parameter: {tenant
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth-admin/{version}/tenants/{tenant}/clients/{public_client_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS ECOM USER PRIVATE CLIENT ENDPOINTS
    # ========================================================================

    async def slas_private_client_ecom_user_authorize(
        self,
        organizationid: str,
        client_id: str,
        channel_id: str,
        redirect_uri: str,
        code_challenge: str
    ) -> SalesforceResponse:
        """SLAS - Private Client - Ecom User - Authorize

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/login

        Args:
            organizationid: Path parameter: {organizationId
            client_id: Query parameter: client_id (required)
            channel_id: Query parameter: channel_id (required)
            redirect_uri: Query parameter: redirect_uri (required)
            code_challenge: Query parameter: code_challenge (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/login"
        params = self._build_params(**{"client_id": client_id, "channel_id": channel_id, "redirect_uri": redirect_uri, "code_challenge": code_challenge})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_private_client_ecom_user_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Private Client - Ecom User - Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_private_client_ecom_user_get_refresh_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Private Client - Ecom User - Get Refresh Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS ECOM USER PUBLIC CLIENT ENDPOINTS
    # ========================================================================

    async def slas_public_client_ecom_user_authorize(
        self,
        organizationid: str,
        client_id: str,
        channel_id: str,
        redirect_uri: str,
        code_challenge: str
    ) -> SalesforceResponse:
        """SLAS - Public Client - Ecom User - Authorize

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/login

        Args:
            organizationid: Path parameter: {organizationId
            client_id: Query parameter: client_id (required)
            channel_id: Query parameter: channel_id (required)
            redirect_uri: Query parameter: redirect_uri (required)
            code_challenge: Query parameter: code_challenge (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/login"
        params = self._build_params(**{"client_id": client_id, "channel_id": channel_id, "redirect_uri": redirect_uri, "code_challenge": code_challenge})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_public_client_ecom_user_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Public Client - Ecom User - Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_public_client_ecom_user_get_refresh_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Public Client - Ecom User - Get Refresh Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS GUEST USER PRIVATE CLIENT ENDPOINTS
    # ========================================================================

    async def slas_private_client_guest_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Private Client - Guest - Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_private_client_guest_get_refresh_guest_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Private Client - Guest - Get Refresh Guest Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS GUEST USER PUBLIC CLIENT ENDPOINTS
    # ========================================================================

    async def slas_public_client_guest_authorize(
        self,
        organizationid: str,
        redirect_uri: str,
        response_type: str,
        client_id: str,
        hint: str,
        code_challenge: str
    ) -> SalesforceResponse:
        """SLAS - Public Client - Guest - Authorize

        HTTP GET: /shopper/auth/v1/organizations/{organizationId}/oauth2/authorize

        Args:
            organizationid: Path parameter: {organizationId
            redirect_uri: Query parameter: redirect_uri (required)
            response_type: Query parameter: response_type (required)
            client_id: Query parameter: client_id (required)
            hint: Query parameter: hint (required)
            code_challenge: Query parameter: code_challenge (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/authorize"
        params = self._build_params(**{"redirect_uri": redirect_uri, "response_type": response_type, "client_id": client_id, "hint": hint, "code_challenge": code_challenge})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_public_client_guest_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Public Client - Guest - Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_public_client_guest_get_refresh_guest_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Public Client - Guest - Get Refresh Guest Access Token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS PASSWORDLESS LOGIN ENDPOINTS
    # ========================================================================

    async def slas_private_client_request_passwordless_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Private Client - Request passwordless token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/passwordless/login

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/passwordless/login"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_private_client_authenticate_with_passwordless_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Private Client - Authenticate with passwordless token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/passwordless/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/passwordless/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS RESET PASSWORD OF A SLAS SHOPPER ENDPOINTS
    # ========================================================================

    async def slas_get_password_reset_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Get password reset token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/password/reset

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/password/reset"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_reset_password(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Reset password

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/password/action

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/password/action"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS SESSION BRIDGE ECOM USER PRIVATE CLIENT ENDPOINTS
    # ========================================================================

    async def sfcc_add_to_cart_get_dwsid(
        self,
        locale: str,
        ocapi_site: str,
        format_param: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SFCC - Add to Cart, Get DWSID

        HTTP POST: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site
            data: Request body data
            format_param: Query parameter: format (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct"
        params = self._build_params(**{"format": format_param})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def sfcc_login_show_get_csrf(self, locale: str, ocapi_site: str) -> SalesforceResponse:
        """SFCC - Login-Show, get CSRF

        HTTP GET: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Login-Show

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Login-Show"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sfcc_account_login(
        self,
        locale: str,
        ocapi_site: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SFCC - Account-Login

        HTTP POST: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Account-Login

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Account-Login"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_session_bridge_get_jwt_for_dwsid_cookie(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Session bridge - Get JWT for DWSID cookie

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/session-bridge/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/session-bridge/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS SESSION BRIDGE ECOM USER PUBLIC CLIENT ENDPOINTS
    # ========================================================================

    async def sfcc_add_to_cart_get_dwsid_1(
        self,
        locale: str,
        ocapi_site: str,
        format_param: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SFCC - Add to Cart, Get DWSID

        HTTP POST: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site
            data: Request body data
            format_param: Query parameter: format (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct"
        params = self._build_params(**{"format": format_param})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def sfcc_login_show_get_csrf_1(self, locale: str, ocapi_site: str) -> SalesforceResponse:
        """SFCC - Login-Show, get CSRF

        HTTP GET: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Login-Show

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Login-Show"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def sfcc_account_login_1(
        self,
        locale: str,
        ocapi_site: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SFCC - Account-Login

        HTTP POST: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Account-Login

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Account-Login"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_public_client_ecom_user_authorize_1(
        self,
        version: str,
        tenant: str,
        client_id: str,
        channel_id: str,
        redirect_uri: str,
        code_challenge: str,
        usid: Optional[str] = None
    ) -> SalesforceResponse:
        """SLAS - Public Client - Ecom User - Authorize

        HTTP POST: /shopper/auth/{version}/organizations/{tenant}/oauth2/login

        Args:
            version: Path parameter: {version
            tenant: Path parameter: {tenant
            client_id: Query parameter: client_id (required)
            channel_id: Query parameter: channel_id (required)
            redirect_uri: Query parameter: redirect_uri (required)
            code_challenge: Query parameter: code_challenge (required)
            usid: Query parameter: usid (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/{version}/organizations/{tenant}/oauth2/login"
        params = self._build_params(**{"client_id": client_id, "channel_id": channel_id, "redirect_uri": redirect_uri, "code_challenge": code_challenge, "usid": usid})
        body = None

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_session_bridge_get_jwt_for_dwsid_cookie_1(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Session bridge - Get JWT for DWSID cookie

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/session-bridge/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/session-bridge/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS SESSION BRIDGE GUEST USER PRIVATE CLIENT ENDPOINTS
    # ========================================================================

    async def sfcc_add_to_cart_get_dwsid_2(
        self,
        locale: str,
        ocapi_site: str,
        format_param: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SFCC - Add to Cart, Get DWSID

        HTTP POST: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site
            data: Request body data
            format_param: Query parameter: format (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct"
        params = self._build_params(**{"format": format_param})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def slas_session_bridge_get_jwt_for_dwsid_cookie_2(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Session bridge - Get JWT for DWSID cookie

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/session-bridge/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/session-bridge/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS SESSION BRIDGE GUEST USER PUBLIC CLIENT ENDPOINTS
    # ========================================================================

    async def sfcc_add_to_cart_get_dwsid_3(
        self,
        locale: str,
        ocapi_site: str,
        format_param: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """SFCC - Add to Cart, Get DWSID

        HTTP POST: /on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct

        Args:
            locale: Path parameter: {locale
            ocapi_site: Path parameter: {ocapi_site
            data: Request body data
            format_param: Query parameter: format (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/on/demandware.store/Sites-{ocapi_site}-Site/{locale}/Cart-AddProduct"
        params = self._build_params(**{"format": format_param})
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="multipart/form-data"
        )

    async def slas_public_client_guest_authorize_1(
        self,
        organizationid: str,
        redirect_uri: str,
        response_type: str,
        client_id: str,
        hint: str,
        code_challenge: str
    ) -> SalesforceResponse:
        """SLAS - Public Client - Guest - Authorize

        HTTP GET: /shopper/auth/v1/organizations/{organizationId}/oauth2/authorize

        Args:
            organizationid: Path parameter: {organizationId
            redirect_uri: Query parameter: redirect_uri (required)
            response_type: Query parameter: response_type (required)
            client_id: Query parameter: client_id (required)
            hint: Query parameter: hint (required)
            code_challenge: Query parameter: code_challenge (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/authorize"
        params = self._build_params(**{"redirect_uri": redirect_uri, "response_type": response_type, "client_id": client_id, "hint": hint, "code_challenge": code_challenge})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_session_bridge_get_jwt_for_dwsid_cookie_3(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Session bridge - Get JWT for DWSID cookie

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/session-bridge/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/session-bridge/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS TRUSTED AGENT ON BEHALF TAOB ENDPOINTS
    # ========================================================================

    async def slas_public_client_get_trusted_agent_authorization_code(
        self,
        organizationid: str,
        client_id: str,
        channel_id: str,
        code_challenge: str,
        login_id: str,
        idp_origin: str,
        redirect_uri: str,
        response_type: str
    ) -> SalesforceResponse:
        """SLAS - Public Client - Get trusted agent authorization code

        HTTP GET: /shopper/auth/v1/organizations/{organizationId}/oauth2/trusted-agent/authorize

        Args:
            organizationid: Path parameter: {organizationId
            client_id: Query parameter: client_id (required)
            channel_id: Query parameter: channel_id (required)
            code_challenge: Query parameter: code_challenge (required)
            login_id: Query parameter: login_id (required)
            idp_origin: Query parameter: idp_origin (required)
            redirect_uri: Query parameter: redirect_uri (required)
            response_type: Query parameter: response_type (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/trusted-agent/authorize"
        params = self._build_params(**{"client_id": client_id, "channel_id": channel_id, "code_challenge": code_challenge, "login_id": login_id, "idp_origin": idp_origin, "redirect_uri": redirect_uri, "response_type": response_type})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def slas_public_client_request_trusted_agent_access_token(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Public Client - Request trusted agent access token

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/trusted-agent/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/trusted-agent/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SALESFORCE COMMERCE API FAMILIES SLAS TRUSTED SYSTEM ON BEHALF TSOB FOR EXTERNAL TRUSTED SYSTEM ENDPOINTS
    # ========================================================================

    async def slas_get_tsob_for_external_trusted_systems_for_registered_shoppers(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Get TSOB for external trusted systems for registered shoppers

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/trusted-system/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/trusted-system/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    async def slas_get_tsob_for_external_trusted_systems_for_guest_shoppers(self, organizationid: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """SLAS - Get TSOB for external trusted systems for guest shoppers

        HTTP POST: /shopper/auth/v1/organizations/{organizationId}/oauth2/trusted-system/token

        Args:
            organizationid: Path parameter: {organizationId
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/shopper/auth/v1/organizations/{organizationid}/oauth2/trusted-system/token"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/x-www-form-urlencoded"
        )

    # ========================================================================
    # SETUP QUERIES ENDPOINTS
    # ========================================================================

    async def login_org_admin_soap(self, apiversion: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Login org admin (SOAP)

        HTTP POST: /services/Soap/u/{apiVersion}

        Args:
            apiversion: Path parameter: {apiVersion
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/u/{apiversion}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_webstore_id(self, apiversion: str, q: str) -> SalesforceResponse:
        """Finds and sets the _webstoreId_ variable for the webstore with the name set in the _webStoreName_ variable.

        HTTP GET: /services/data/v{apiVersion}/query

        Args:
            apiversion: Path parameter: {apiVersion
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_buyer_account_id(self, apiversion: str, q: str) -> SalesforceResponse:
        """Finds and sets the _buyerAccountId_ variable for the buyer account with the name set in the _buyerUsername_ variable.

        HTTP GET: /services/data/v{apiVersion}/query

        Args:
            apiversion: Path parameter: {apiVersion
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{apiversion}/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SOAP ENDPOINTS
    # ========================================================================

    async def soap_undelete(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Undeletes records from the Recycle Bin.

        HTTP POST: /services/Soap/c/{version}

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/Soap/c/{version}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/xml"
        )

    # ========================================================================
    # SUBSCRIPTION MANAGEMENT LIFECYCLE MANAGEMENT ENDPOINTS
    # ========================================================================

    async def create_asset_from_order(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """CreateAssetFromOrder

        HTTP POST: /services/data/v{version}/actions/standard/createOrUpdateAssetFromOrder

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/standard/createOrUpdateAssetFromOrder"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def initiate_cancellation(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Initiate Cancellation

        HTTP POST: /services/data/v{version}/asset-management/assets/collection/actions/initiate-cancellation

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/asset-management/assets/collection/actions/initiate-cancellation"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def initiate_renewal(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Initiate Renewal

        HTTP POST: /services/data/v{version}/asset-management/assets/collection/actions/initiate-renewal

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/asset-management/assets/collection/actions/initiate-renewal"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def initiate_amend_quantity(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Initiate Amend Quantity

        HTTP POST: /services/data/v{version}/asset-management/assets/collection/actions/initiate-amend-quantity

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/asset-management/assets/collection/actions/initiate-amend-quantity"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SUBSCRIPTION MANAGEMENT ORDERS ENDPOINTS
    # ========================================================================

    async def create_order_one_time(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Creates a Subscription Management order with a single order item (label: Order Product). The order item has a one-time product selling model.

        HTTP POST: /services/data/v{version}/composite

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_order_with_bundle(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Creates a Subscription Management order for a product bundle.

        HTTP POST: /services/data/v{version}/composite

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_order_evergreen_termed(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Creates a Subscription Management order with two order items. One order item is sold on a monthly termed subscription, and the other order item is sold as an evergreen subscription.

        HTTP POST: /services/data/v{version}/composite

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/composite"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_order_from_quote(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create Order From Quote

        HTTP POST: /services/data/v{version}/actions/standard/createOrderFromQuote

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/actions/standard/createOrderFromQuote"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SUBSCRIPTION MANAGEMENT PRICING ENDPOINTS
    # ========================================================================

    async def calculate_price_new_sale(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Calculate the price of a new sale that contains two order items: a term-defined subscription order for a bronze-level SLA, and an evergreen subscription order for a virtual router. The payload for ...

        HTTP POST: /services/data/v{version}/commerce/pricing/salestransaction/actions/calculate-price

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/pricing/salestransaction/actions/calculate-price"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def calculate_price_new_sale_bundles(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Calculate the price of the Work Anywhere software bundle plus all it's bundle components for the pricing period of 1 month. Because this bundle is sold on an evergreen subscription rather than a te...

        HTTP POST: /services/data/v{version}/commerce/pricing/salestransaction/actions/calculate-price

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/pricing/salestransaction/actions/calculate-price"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def calculate_price_new_sale_with_discounts(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Calculate the price of a sales transaction, including a top-level and a line-level discount. The top-level discount specifies 20% off the entire sales transaction, while the line-level discount spe...

        HTTP POST: /services/data/v{version}/commerce/pricing/salestransaction/actions/calculate-price

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/pricing/salestransaction/actions/calculate-price"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SUBSCRIPTION MANAGEMENT PRODUCTS ENDPOINTS
    # ========================================================================

    async def query_eligible_products(self, version: str, q: str) -> SalesforceResponse:
        """This query returns all the products that have a related Product Selling Model Option. To price a product with Subscription Management, the product must have a related Product Selling Model Option. ...

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def request_product_information_no_bundles(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Request information for two products that aren't bundles. For the first product, request only the information about the specified product selling model. For the second product, request information ...

        HTTP POST: /services/data/v{version}/commerce/catalog-products/actions/get-products

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/catalog-products/actions/get-products"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def request_product_information_bundled_components(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Request information for a single bundle product and all it's bundle components. See Request Product Information.

        HTTP POST: /services/data/v{version}/commerce/catalog-products/actions/get-products

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/catalog-products/actions/get-products"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SUBSCRIPTION MANAGEMENT QUERY ORG POSTMAN RUNNER ENDPOINTS
    # ========================================================================

    async def user_info_1(self) -> SalesforceResponse:
        """User Info

        HTTP GET: /services/oauth2/userinfo

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/oauth2/userinfo"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_latest_release_version(self) -> SalesforceResponse:
        """Get Latest Release Version

        HTTP GET: /services/data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/services/data"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_default_account(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Default Account

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_default_billing_contact(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Default Billing Contact

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_standard_pricebook(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Standard Pricebook

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_default_tax_treatment(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Default Tax Treatment

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_products(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Products

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_product_selling_models(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """GetProductSellingModels

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_pbe_single_selling_model(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """GetPBE (Single Selling Model)

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_pb_es_virtual_router(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """GetPBEs(VirtualRouter)

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_product_relationship_type(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Product Relationship Type

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_payment_method(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create Payment Method

        HTTP POST: /services/data/v{version}/commerce/payments/payment-methods

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/payments/payment-methods"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_default_payment_method(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get Default Payment Method

        HTTP GET: /services/data/v{version}/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: Query parameter: q (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # SUBSCRIPTION MANAGEMENT QUOTES ENDPOINTS
    # ========================================================================

    async def create_or_update_quote(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """This example creates a quote for the following products. - 5 Virtual Routers sold on a 1-year termed subscription at $35/item - 1 Bronze Service Level Agreement sold on an evergreen subscription at...

        HTTP POST: /services/data/v{version}/commerce/quotes/actions/place

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/commerce/quotes/actions/place"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # TEMPLATES ENDPOINTS
    # ========================================================================

    async def get_wave_template_release_notes(self, templateidorapiname: str) -> SalesforceResponse:
        """Retrieve the release notes for the template. For additional information, see Template Release Notes Resource.

        HTTP GET: /wave/templates/{templateIdOrApiName}/releasenotes

        Args:
            templateidorapiname: Path parameter: templateIdOrApiName

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/templates/{templateidorapiname}/releasenotes"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_wave_template_config(
        self,
        templateidorapiname: str,
        disableapex: Optional[str] = None,
        options: Optional[str] = None
    ) -> SalesforceResponse:
        """Retrieve the configuration for a specific template. For additional information, see the Template Configuration Resource.

        HTTP GET: /wave/templates/{templateIdOrApiName}/configuration

        Args:
            templateidorapiname: Path parameter: templateIdOrApiName
            disableapex: (Optional) Indicates whether Apex integration hooks are disabled (true) or not (false). (optional)
            options: (Optional) Filters the configuration by template visibility options. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/templates/{templateidorapiname}/configuration"
        params = self._build_params(**{"disableApex": disableapex, "options": options})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_wave_template(self, templateidorapiname: str, options: Optional[str] = None) -> SalesforceResponse:
        """Retrieve the details for a specific CRM Analytics template. For additional information, see the Template Resource.

        HTTP GET: /wave/templates/{templateIdOrApiName}

        Args:
            templateidorapiname: Path parameter: templateIdOrApiName
            options: (Optional) Template visibility options to apply to the collection results. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/wave/templates/{templateidorapiname}"
        params = self._build_params(**{"options": options})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_wave_template_collection(self, options: Optional[str] = None, type_param: Optional[str] = None) -> SalesforceResponse:
        """Retrieve a collection of CRM Analytics templates within the current organization. For additional information, see the Templates List Resource.

        HTTP GET: /wave/templates

        Args:
            options: (Optional) Template visibility options to apply to the collection results. (optional)
            type_param: (Optional) Template type to apply to the collection results. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = "/wave/templates"
        params = self._build_params(**{"options": options, "type": type_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # TOOLING ENDPOINTS
    # ========================================================================

    async def tooling_completion(self, version: str, type_param: str) -> SalesforceResponse:
        """Retrieves available code completions of the referenced type for Apex system method symbols (type=apex). Available from API version 28.0 or later.

        HTTP GET: /services/data/v{version}/tooling/completions

        Args:
            version: Path parameter: version
            type_param: apex or visualforce (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/completions"
        params = self._build_params(**{"type": type_param})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def tooling_execute_anonymous(
        self,
        version: str,
        anonymousbody: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Executes Apex code anonymously. Available from API version 29.0 or later.

        HTTP GET: /services/data/v{version}/tooling/executeAnonymous

        Args:
            version: Path parameter: version
            data: Request body data
            anonymousbody: Query parameter: anonymousBody (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/executeAnonymous"
        params = self._build_params(**{"anonymousBody": anonymousbody})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def tooling_query(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Executes a query against an object and returns data that matches the specified criteria. Tooling API exposes objects like EntityDefinition and FieldDefinition that use the external object framework...

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: SOQL Query (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def tooling_run_tests_async(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Runs one or more methods within one or more Apex classes, using the asynchronous test execution mechanism. In the request body, you can specify test class names and IDs, suite names and IDs, the ma...

        HTTP POST: /services/data/v{version}/tooling/runTestsAsynchronous

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/runTestsAsynchronous"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def tooling_run_tests_sync(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Runs one or more methods within one or more Apex classes, using the asynchronous test execution mechanism. In the request body, you can specify test class names and IDs, suite names and IDs, the ma...

        HTTP POST: /services/data/v{version}/tooling/runTestsSynchronous

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/runTestsSynchronous"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def tooling_search(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Runs one or more methods within one or more Apex classes, using the asynchronous test execution mechanism. In the request body, you can specify test class names and IDs, suite names and IDs, the ma...

        HTTP GET: /services/data/v{version}/tooling/search

        Args:
            version: Path parameter: version
            data: Request body data
            q: SOSL search statement (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/search"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_tooling_describe(self, version: str) -> SalesforceResponse:
        """Lists the available Tooling API objects and their metadata.

        HTTP GET: /services/data/v{version}/tooling/sobjects

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_tooling_metadata_s_object(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Get Tooling Metadata SObject

        HTTP GET: /services/data/v{version}/tooling/sobjects/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/{sobject_api_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_tooling_describe_s_object(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Get Tooling Describe SObject

        HTTP GET: /services/data/v{version}/tooling/sobjects/{SOBJECT_API_NAME}/describe

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/{sobject_api_name}/describe"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def post_tooling_s_object(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Post Tooling SObject

        HTTP POST: /services/data/v{version}/tooling/sobjects/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/{sobject_api_name}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def access_records(
        self,
        sobject_api_name: str,
        record_id: str,
        version: str
    ) -> SalesforceResponse:
        """Access Records

        HTTP GET: /services/data/v{version}/tooling/sobjects/{SOBJECT_API_NAME}/{RECORD_ID}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/{sobject_api_name}/{record_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # TOOLING SANDBOX ENDPOINTS
    # ========================================================================

    async def list_sandboxes(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Lists all sandboxes.

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: SOQL Query (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_clone_sandbox(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Creates or clones a sandbox. If you wish to clone, fill the SourceId body field with the org Id of the source sandbox.

        HTTP POST: /services/data/v{version}/tooling/sobjects/SandboxInfo

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/SandboxInfo"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_sandbox(self, sandbox_info_id: str, version: str) -> SalesforceResponse:
        """Retrieves a sandbox record.

        HTTP GET: /services/data/v{version}/tooling/sobjects/SandboxInfo/{SANDBOX_INFO_ID}

        Args:
            sandbox_info_id: Path parameter: SANDBOX_INFO_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/SandboxInfo/{sandbox_info_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_sandbox_status(
        self,
        version: str,
        q: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Lists all sandboxes.

        HTTP GET: /services/data/v{version}/tooling/query

        Args:
            version: Path parameter: version
            data: Request body data
            q: SOQL Query (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/query"
        params = self._build_params(**{"q": q})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def refresh_sandbox(
        self,
        sandbox_info_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Refreshes a sandbox.

        HTTP PATCH: /services/data/v{version}/tooling/sobjects/SandboxInfo/{SANDBOX_INFO_ID}

        Args:
            sandbox_info_id: Path parameter: SANDBOX_INFO_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/SandboxInfo/{sandbox_info_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_sandbox(self, sandbox_info_id: str, version: str) -> SalesforceResponse:
        """Deletes a sandbox.

        HTTP DELETE: /services/data/v{version}/tooling/sobjects/SandboxInfo/{SANDBOX_INFO_ID}

        Args:
            sandbox_info_id: Path parameter: SANDBOX_INFO_ID
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/tooling/sobjects/SandboxInfo/{sandbox_info_id}"
        params = None
        body = None

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # UI ENDPOINTS
    # ========================================================================

    async def get_active_theme(self, version: str) -> SalesforceResponse:
        """Get a Salesforce org’s active theme. A theme uses colors, images, and banners to change the overall appearance of Salesforce. Administrators can define themes and switch themes to provide a differe...

        HTTP GET: /services/data/v{version}/ui-api/themes/active

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/themes/active"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # UI ACTIONS ENDPOINTS
    # ========================================================================

    async def get_global_actions(
        self,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        actiontypes: Optional[str] = None,
        apinames: Optional[str] = None,
        formfactor: Optional[str] = None,
        retrievalmode: Optional[str] = None,
        sections: Optional[str] = None
    ) -> SalesforceResponse:
        """Get the actions on record detail pages.

        HTTP GET: /services/data/v{version}/ui-api/actions/global

        Args:
            version: Path parameter: version
            data: Request body data
            actiontypes: The action type (`CustomButton`, `ProductivityAction`, `QuickAction` or `StandardButton`) (optional)
            apinames: The API names of one or more actions to be retrieved. Use this parameter only when passing `retrievalMode=All`. (optional)
            formfactor: The layout display size (`Large` (default), `Medium` or `Small`). (optional)
            retrievalmode: When the action context is Record, this parameter indicates which actions to retrieve from the record page. Either `All` or `PageLayout` (default). (optional)
            sections: The section of the user interface that the actions reside in (`ActivityComposer`, `CollaborateComposer`, `Page` or `SingleActionLinks`). (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/global"
        params = self._build_params(**{"actionTypes": actiontypes, "apiNames": apinames, "formFactor": formfactor, "retrievalMode": retrievalmode, "sections": sections})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_record_detail_page_actions(
        self,
        record_ids: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the actions on record detail pages.

        HTTP GET: /services/data/v{version}/ui-api/actions/record/{RECORD_IDS}

        Args:
            record_ids: Path parameter: RECORD_IDS
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/record/{record_ids}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_record_edit_page_actions(
        self,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the actions on record detail pages.

        HTTP GET: /services/data/v{version}/ui-api/actions/record/{RECORD_ID}/record-edit

        Args:
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/record/{record_id}/record-edit"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_related_list_actions(
        self,
        related_list_ids: str,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        actiontypes: Optional[str] = None,
        apinames: Optional[str] = None,
        formfactor: Optional[str] = None,
        retrievalmode: Optional[str] = None,
        sections: Optional[str] = None
    ) -> SalesforceResponse:
        """Get the actions on record detail pages.

        HTTP GET: /services/data/v{version}/ui-api/actions/record/{RECORD_ID}/related-list/{RELATED_LIST_IDS}

        Args:
            related_list_ids: Path parameter: RELATED_LIST_IDS
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data
            actiontypes: The action type (`CustomButton`, `ProductivityAction`, `QuickAction` or `StandardButton`) (optional)
            apinames: The API names of one or more actions to be retrieved. Use this parameter only when passing `retrievalMode=All`. (optional)
            formfactor: The layout display size (`Large` (default), `Medium` or `Small`). (optional)
            retrievalmode: When the action context is Record, this parameter indicates which actions to retrieve from the record page. Either `All` or `PageLayout` (default). (optional)
            sections: The section of the user interface that the actions reside in (`ActivityComposer`, `CollaborateComposer`, `Page` or `SingleActionLinks`). (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/record/{record_id}/related-list/{related_list_ids}"
        params = self._build_params(**{"actionTypes": actiontypes, "apiNames": apinames, "formFactor": formfactor, "retrievalMode": retrievalmode, "sections": sections})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_related_list_record_actions(
        self,
        related_list_record_ids: str,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the actions on records in related lists.

        HTTP GET: /services/data/v{version}/ui-api/actions/record/{RECORD_ID}/related-list-record/{RELATED_LIST_RECORD_IDS}

        Args:
            related_list_record_ids: Path parameter: RELATED_LIST_RECORD_IDS
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/record/{record_id}/related-list-record/{related_list_record_ids}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_header_actions(
        self,
        version: str,
        list_view_ids: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the actions on records in related lists.

        HTTP GET: /services/data/v{version}/ui-api/actions/list-view/{LIST_VIEW_IDS}

        Args:
            version: Path parameter: version
            list_view_ids: Path parameter: LIST_VIEW_IDS
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/list-view/{list_view_ids}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_record_actions(
        self,
        record_ids: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the record actions on list views.

        HTTP GET: /services/data/v{version}/ui-api/actions/list-view-record/{RECORD_IDS}

        Args:
            record_ids: Path parameter: RECORD_IDS
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/list-view-record/{record_ids}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_chart_actions(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the record actions on list views.

        HTTP GET: /services/data/v{version}/ui-api/actions/list-view-chart/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/list-view-chart/{sobject_api_name}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_lightning_page_actions(
        self,
        flexipage_names: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the actions on Lightning pages (FlexiPages).

        HTTP GET: /services/data/v{version}/ui-api/actions/flexipage/{FLEXIPAGE_NAMES}

        Args:
            flexipage_names: Path parameter: FLEXIPAGE_NAMES
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/flexipage/{flexipage_names}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_lookup_field_actions(
        self,
        sobject_api_names: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the actions on Lightning pages (FlexiPages).

        HTTP GET: /services/data/v{version}/ui-api/actions/lookup/{SOBJECT_API_NAMES}

        Args:
            sobject_api_names: Path parameter: SOBJECT_API_NAMES
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/lookup/{sobject_api_names}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_mru_list_view_actions(
        self,
        sobject_api_names: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the header actions on the most recently used (MRU) list view for objects.

        HTTP GET: /services/data/v{version}/ui-api/actions/mru-list/{SOBJECT_API_NAMES}

        Args:
            sobject_api_names: Path parameter: SOBJECT_API_NAMES
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/mru-list/{sobject_api_names}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_photo_actions(
        self,
        record_ids: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get the photo actions for pages. Currently, only group and user pages support photo actions.

        HTTP GET: /services/data/v{version}/ui-api/actions/photo/{RECORD_IDS}

        Args:
            record_ids: Path parameter: RECORD_IDS
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/actions/photo/{record_ids}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # UI APPS ENDPOINTS
    # ========================================================================

    async def get_apps(
        self,
        version: str,
        formfactor: str,
        data: Optional[Dict[str, Any]] = None,
        usercustomizations: Optional[str] = None
    ) -> SalesforceResponse:
        """Get metadata for all the apps a user has access to. Metadata for the selected app includes tabs on the app’s navigation bar. Metadata for other apps doesn’t include tabs on the navigation bar.

        HTTP GET: /services/data/v{version}/ui-api/apps

        Args:
            version: Path parameter: version
            data: Request body data
            formfactor: The form factor for each app that the user has access to (`Large`, `Medium` or `Small`) (required)
            usercustomizations: If true, gets custom and standard navigation tabs. If false, gets only standard navigation tabs. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/apps"
        params = self._build_params(**{"formFactor": formfactor, "userCustomizations": usercustomizations})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_an_app(
        self,
        app_id: str,
        version: str,
        formfactor: str,
        data: Optional[Dict[str, Any]] = None,
        usercustomizations: Optional[str] = None
    ) -> SalesforceResponse:
        """Get metadata about an app.

        HTTP GET: /services/data/v{version}/ui-api/apps/{APP_ID}

        Args:
            app_id: Path parameter: APP_ID
            version: Path parameter: version
            data: Request body data
            formfactor: The form factor for each app that the user has access to (`Large`, `Medium` or `Small`) (required)
            usercustomizations: If true, gets custom and standard navigation tabs. If false, gets only standard navigation tabs. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/apps/{app_id}"
        params = self._build_params(**{"formFactor": formfactor, "userCustomizations": usercustomizations})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_last_selected_app(
        self,
        app_id: str,
        version: str,
        formfactor: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Returns metadata for an app, and saves an app as the last selected for a user.

        HTTP PATCH: /services/data/v{version}/ui-api/apps/{APP_ID}

        Args:
            app_id: Path parameter: APP_ID
            version: Path parameter: version
            data: Request body data
            formfactor: The form factor for each app that the user has access to (`Large`, `Medium` or `Small`) (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/apps/{app_id}"
        params = self._build_params(**{"formFactor": formfactor})
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_last_selected_app(
        self,
        version: str,
        formfactor: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Retrieves the app the current user last selected or the app the user sees by default.

        HTTP GET: /services/data/v{version}/ui-api/apps/selected

        Args:
            version: Path parameter: version
            data: Request body data
            formfactor: The form factor for each app that the user has access to (`Large`, `Medium` or `Small`) (required)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/apps/selected"
        params = self._build_params(**{"formFactor": formfactor})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_personalized_navigation_items(
        self,
        app_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        formfactor: Optional[str] = None
    ) -> SalesforceResponse:
        """Get a user’s personalized navigation items (tabs).

        HTTP GET: /services/data/v{version}/ui-api/apps/{APP_ID}/user-nav-items

        Args:
            app_id: Path parameter: APP_ID
            version: Path parameter: version
            data: Request body data
            formfactor: The form factor for each app that the user has access to (`Large`, `Medium` or `Small`) (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/apps/{app_id}/user-nav-items"
        params = self._build_params(**{"formFactor": formfactor})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_all_navigation_items(
        self,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        formfactor: Optional[str] = None,
        navitemnames: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None
    ) -> SalesforceResponse:
        """Gets all navigation items (tabs) that the user has access to.

        HTTP GET: /services/data/v{version}/ui-api/nav-items

        Args:
            version: Path parameter: version
            data: Request body data
            formfactor: The form factor for each app that the user has access to (`Large`, `Medium` or `Small`) (optional)
            navitemnames: A comma delimited list of TabDefinition name values to include in the response. (optional)
            page: The page offset form which to begin returning nav items. The default value is 0, which returns nav items from the first page. For example, for `page=2` and `pageSize=10`, the first nav item returne... (optional)
            pagesize: The maximum number of nav items to return on a page. The default value is 25. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/nav-items"
        params = self._build_params(**{"formFactor": formfactor, "navItemNames": navitemnames, "page": page, "pageSize": pagesize})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_personalized_navigation_items(
        self,
        app_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Updates the order of a user’s personalized navigation items (tabs) and adds a navigation item to the list in the order specified.

        HTTP PUT: /services/data/v{version}/ui-api/apps/{APP_ID}/user-nav-items

        Args:
            app_id: Path parameter: APP_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/apps/{app_id}/user-nav-items"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # UI FAVORITES ENDPOINTS
    # ========================================================================

    async def create_a_favorite(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create a Favorite

        HTTP POST: /services/data/v{version}/ui-api/favorites

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_a_favorite(
        self,
        favorite_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get a favorite.

        HTTP GET: /services/data/v{version}/ui-api/favorites/{FAVORITE_ID}

        Args:
            favorite_id: Path parameter: FAVORITE_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites/{favorite_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_favorites(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Get all of a user’s favorites.

        HTTP GET: /services/data/v{version}/ui-api/favorites

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_a_favorite(
        self,
        favorite_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update a Favorite

        HTTP PATCH: /services/data/v{version}/ui-api/favorites/{FAVORITE_ID}

        Args:
            favorite_id: Path parameter: FAVORITE_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites/{favorite_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_a_batch_of_favorites(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Update all favorites at once. The sort order is updated to the given relative ordering. Any favorites missing from the request body are deleted.

        HTTP PUT: /services/data/v{version}/ui-api/favorites/batch

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites/batch"
        params = None
        body = data

        return await self._execute_request(
            method="PUT",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_a_favorite(
        self,
        favorite_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get a favorite.

        HTTP DELETE: /services/data/v{version}/ui-api/favorites/{FAVORITE_ID}

        Args:
            favorite_id: Path parameter: FAVORITE_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites/{favorite_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_usage_of_a_favorite(
        self,
        favorite_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update the usage of an individual favorite, for example, the last time and number of times the favorite was clicked.

        HTTP PATCH: /services/data/v{version}/ui-api/favorites/{FAVORITE_ID}/usage

        Args:
            favorite_id: Path parameter: FAVORITE_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/favorites/{favorite_id}/usage"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # UI LIST VIEWS ENDPOINTS
    # ========================================================================

    async def get_list_views_for_an_object(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        pagesize: Optional[str] = None,
        pagetoken: Optional[str] = None,
        q: Optional[str] = None,
        recentlistsonly: Optional[str] = None
    ) -> SalesforceResponse:
        """Get list views associated with an object.

        HTTP GET: /services/data/v{version}/ui-api/list-info/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data
            pagesize: The number of list records viewed at one time. The default value is 20. Value can be 1–2000. (optional)
            pagetoken: A token that represents the page offset. To indicate where the page starts, use this value with the pageSize parameter. The maximum offset is 2000 and the default is 0. (optional)
            q: Search term to filter the results. Wildcards are supported. (optional)
            recentlistsonly: Show only recently viewed lists. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/list-info/{sobject_api_name}"
        params = self._build_params(**{"pageSize": pagesize, "pageToken": pagetoken, "q": q, "recentListsOnly": recentlistsonly})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_records_by_id(
        self,
        list_view_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        fields: Optional[str] = None,
        optionalfields: Optional[str] = None,
        pagesize: Optional[str] = None,
        pagetoken: Optional[str] = None,
        searchterm: Optional[str] = None,
        sortby: Optional[str] = None,
        where: Optional[str] = None
    ) -> SalesforceResponse:
        """Get record data for a list view by list view ID using URL parameters.

        HTTP GET: /services/data/v{version}/ui-api/list-records/{LIST_VIEW_ID}

        Args:
            list_view_id: Path parameter: LIST_VIEW_ID
            version: Path parameter: version
            data: Request body data
            fields: Additional fields queried for the records returned. These fields don’t create visible columns. If the field isn't available to the user, an error occurs. (optional)
            optionalfields: Additional fields queried for the records returned. These fields don’t create visible columns. If the field isn't available to the user, no error occurs and the field isn’t included in the records. (optional)
            pagesize: The number of list records viewed at one time. The default value is 50. Value can be 1–2000. (optional)
            pagetoken: A token that represents the page offset. To indicate where the page starts, use this value with the `pageSize` parameter. The maximum offset is 2000 and the default is 0. (optional)
            searchterm: Search term to filter the results. Wildcards are supported. (optional)
            sortby: The API names of the fields the list view is sorted by. If the name is preceded with `-`, the sort order is descending. For example, `Name` sorts by name in ascending order. `-CreatedDate` sorts by... (optional)
            where: The filter applied to returned records, in GraphQL syntax. For example, { and: [ { StageName: { eq: \"Prospecting\" } }, { Account: { Name: { eq: \"Dickenson plc\" } } } ] } filters an Opportunity ... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/list-records/{list_view_id}"
        params = self._build_params(**{"fields": fields, "optionalFields": optionalfields, "pageSize": pagesize, "pageToken": pagetoken, "searchTerm": searchterm, "sortBy": sortby, "where": where})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_records_by_api_name(
        self,
        list_view_api_name: str,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        fields: Optional[str] = None,
        optionalfields: Optional[str] = None,
        pagesize: Optional[str] = None,
        pagetoken: Optional[str] = None,
        searchterm: Optional[str] = None,
        sortby: Optional[str] = None,
        where: Optional[str] = None
    ) -> SalesforceResponse:
        """Get record data for a list view by list view API name using URL parameters.

        HTTP GET: /services/data/v{version}/ui-api/list-records/{SOBJECT_API_NAME}/{LIST_VIEW_API_NAME}

        Args:
            list_view_api_name: Path parameter: LIST_VIEW_API_NAME
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data
            fields: Additional fields queried for the records returned. These fields don’t create visible columns. If the field isn't available to the user, an error occurs. (optional)
            optionalfields: Additional fields queried for the records returned. These fields don’t create visible columns. If the field isn't available to the user, no error occurs and the field isn’t included in the records. (optional)
            pagesize: The number of list records viewed at one time. The default value is 50. Value can be 1–2000. (optional)
            pagetoken: A token that represents the page offset. To indicate where the page starts, use this value with the `pageSize` parameter. The maximum offset is 2000 and the default is 0. (optional)
            searchterm: Search term to filter the results. Wildcards are supported. (optional)
            sortby: The API names of the fields the list view is sorted by. If the name is preceded with `-`, the sort order is descending. For example, `Name` sorts by name in ascending order. `-CreatedDate` sorts by... (optional)
            where: The filter applied to returned records, in GraphQL syntax. For example, { and: [ { StageName: { eq: \"Prospecting\" } }, { Account: { Name: { eq: \"Dickenson plc\" } } } ] } filters an Opportunity ... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/list-records/{sobject_api_name}/{list_view_api_name}"
        params = self._build_params(**{"fields": fields, "optionalFields": optionalfields, "pageSize": pagesize, "pageToken": pagetoken, "searchTerm": searchterm, "sortBy": sortby, "where": where})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_records(
        self,
        list_view_api_name: str,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get record data for a list view using a request body.

        HTTP POST: /services/data/v{version}/ui-api/list-records/{SOBJECT_API_NAME}/{LIST_VIEW_API_NAME}

        Args:
            list_view_api_name: Path parameter: LIST_VIEW_API_NAME
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/list-records/{sobject_api_name}/{list_view_api_name}"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_metadata_by_id(
        self,
        list_view_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get list view metadata by list view ID using URL parameters.

        HTTP GET: /services/data/v{version}/ui-api/list-info/{LIST_VIEW_ID}

        Args:
            list_view_id: Path parameter: LIST_VIEW_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/list-info/{list_view_id}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_list_view_metadata_by_api_name(
        self,
        list_view_api_name: str,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Get list view metadata by list view API name using URL parameters.

        HTTP GET: /services/data/v{version}/ui-api/list-info/{SOBJECT_API_NAME}/{LIST_VIEW_API_NAME}

        Args:
            list_view_api_name: Path parameter: LIST_VIEW_API_NAME
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/list-info/{sobject_api_name}/{list_view_api_name}"
        params = None
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    # ========================================================================
    # UI RECORDS ENDPOINTS
    # ========================================================================

    async def get_record_data_and_object_metadata(
        self,
        record_ids: str,
        version: str,
        childrelationships: Optional[str] = None,
        formfactor: Optional[str] = None,
        layouttypes: Optional[str] = None,
        modes: Optional[str] = None,
        optionalfields: Optional[str] = None,
        pagesize: Optional[str] = None,
        updatemru: Optional[str] = None
    ) -> SalesforceResponse:
        """Get layout information, metadata, and data to build UI for a single record or for a collection of records. The response contains layout information for whichever layout types are specified in the l...

        HTTP GET: /services/data/v{version}/ui-api/record-ui/{RECORD_IDS}

        Args:
            record_ids: Path parameter: RECORD_IDS
            version: Path parameter: version
            childrelationships: A collection of child relationship names. The records with those child relationship names are included in the response. Specify names in the format ObjectApiName.ChildRelationshipName or ObjectApiN... (optional)
            formfactor: The layout display size for the record. One of these values: Large—(Default) Use this value to get a layout for desktop display size. Medium—Use this value to get a layout for tablet display size. ... (optional)
            layouttypes: The layout type for the record. A collection of any of these values: Compact—Use this value to get a layout that contains a record’s key fields. Full—(Default) Use this value to get a full layout. (optional)
            modes: The access mode for the record. This value determines which fields to get from a layout. Layouts have different fields for create, edit, and view modes. For example, formula fields are rendered in ... (optional)
            optionalfields: A collection of optional field names. If a field is accessible to the context user, it’s included in the response. If a field isn’t accessible to the context user, it isn’t included in the response... (optional)
            pagesize: The maximum number of child records to return on a page. (optional)
            updatemru: To add to the most recently used (MRU) list view, set to true. The default value is false. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/record-ui/{record_ids}"
        params = self._build_params(**{"childRelationships": childrelationships, "formFactor": formfactor, "layoutTypes": layouttypes, "modes": modes, "optionalFields": optionalfields, "pageSize": pagesize, "updateMru": updatemru})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_record_layout_metadata(
        self,
        sobject_api_name: str,
        version: str,
        formfactor: Optional[str] = None,
        layouttypes: Optional[str] = None,
        modes: Optional[str] = None,
        recordtypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Get metadata about page layouts for the specified object type.

        HTTP GET: /services/data/v{version}/ui-api/layout/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            formfactor: The layout display size for the record. One of these values: Large—(Default) Use this value to get a layout for desktop display size. Medium—Use this value to get a layout for tablet display size. ... (optional)
            layouttypes: The layout type for the record. A collection of any of these values: Compact—Use this value to get a layout that contains a record’s key fields. Full—(Default) Use this value to get a full layout. (optional)
            modes: The access mode for the record. This value determines which fields to get from a layout. Layouts have different fields for create, edit, and view modes. For example, formula fields are rendered in ... (optional)
            recordtypeid: The ID of the record type (RecordType object) for the new record. If not provided, the default record type is used. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/layout/{sobject_api_name}"
        params = self._build_params(**{"formFactor": formfactor, "layoutTypes": layouttypes, "modes": modes, "recordTypeId": recordtypeid})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_a_directory_of_supported_objects(self, version: str) -> SalesforceResponse:
        """Get a Salesforce org’s active theme. A theme uses colors, images, and banners to change the overall appearance of Salesforce. Administrators can define themes and switch themes to provide a differe...

        HTTP GET: /services/data/v{version}/ui-api/object-info

        Args:
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/object-info"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_object_metadata(self, sobject_api_name: str, version: str) -> SalesforceResponse:
        """Get metadata about a specific object. The response includes metadata describing fields, child relationships, record type, and theme.

        HTTP GET: /services/data/v{version}/ui-api/object-info/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/object-info/{sobject_api_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_values_for_a_picklist_field(
        self,
        record_type_id: str,
        sobject_api_name: str,
        version: str,
        field_api_name: str
    ) -> SalesforceResponse:
        """Get metadata about a specific object. The response includes metadata describing fields, child relationships, record type, and theme.

        HTTP GET: /services/data/v{version}/ui-api/object-info/{SOBJECT_API_NAME}/picklist-values/{RECORD_TYPE_ID}/{FIELD_API_NAME}

        Args:
            record_type_id: Path parameter: RECORD_TYPE_ID
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            field_api_name: Path parameter: FIELD_API_NAME

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/object-info/{sobject_api_name}/picklist-values/{record_type_id}/{field_api_name}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_values_for_all_picklist_fields_of_a_record_type(
        self,
        record_type_id: str,
        sobject_api_name: str,
        version: str
    ) -> SalesforceResponse:
        """Get metadata about a specific object. The response includes metadata describing fields, child relationships, record type, and theme.

        HTTP GET: /services/data/v{version}/ui-api/object-info/{SOBJECT_API_NAME}/picklist-values/{RECORD_TYPE_ID}

        Args:
            record_type_id: Path parameter: RECORD_TYPE_ID
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/object-info/{sobject_api_name}/picklist-values/{record_type_id}"
        params = None
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_child_records(
        self,
        record_id: str,
        relationship_name: str,
        version: str,
        fields: Optional[str] = None,
        optionalfields: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        pagetoken: Optional[str] = None
    ) -> SalesforceResponse:
        """Get child records for a specified record and child relationship name. Relationships are connections between records. On a record detail page, each record in a related list has a child relationship ...

        HTTP GET: /services/data/v{version}/ui-api/records/{RECORD_ID}/child-relationships/{RELATIONSHIP_NAME}

        Args:
            record_id: Path parameter: RECORD_ID
            relationship_name: Path parameter: RELATIONSHIP_NAME
            version: Path parameter: version
            fields: Specifies the fields to return. If this property is specified, the response is a union of fields and optionalFields. If the context user doesn’t have access to a field, an error is returned. If you... (optional)
            optionalfields: A collection of optional field names. If a field is accessible to the context user, it’s included in the response. If a field isn’t accessible to the context user, it isn’t included in the response... (optional)
            page: The page offset from which to begin returning records. The default value is 0, which returns records from the first page. For example, for page=2 and pageSize=10, the first record returned is the 2... (optional)
            pagesize: The maximum number of child records to return on a page. The default value is 5. (optional)
            pagetoken: A token that represents the page offset. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/records/{record_id}/child-relationships/{relationship_name}"
        params = self._build_params(**{"fields": fields, "optionalFields": optionalfields, "page": page, "pageSize": pagesize, "pageToken": pagetoken})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_a_record(
        self,
        record_id: str,
        version: str,
        childrelationships: Optional[str] = None,
        fields: Optional[str] = None,
        layouttypes: Optional[str] = None,
        modes: Optional[str] = None,
        optionalfields: Optional[str] = None,
        pagesize: Optional[str] = None,
        updatemru: Optional[str] = None
    ) -> SalesforceResponse:
        """Get a record’s data.

        HTTP GET: /services/data/v{version}/ui-api/records/{RECORD_ID}

        Args:
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            childrelationships: A collection of child relationship names. The records with those child relationship names are included in the response. Specify names in the format ObjectApiName.ChildRelationshipName or ObjectApiN... (optional)
            fields: Specifies the fields to return. If this property is specified, the response is a union of fields and optionalFields. If the context user doesn’t have access to a field, an error is returned. If you... (optional)
            layouttypes: Specifies the fields to return. If this property is specified, the response is a union of layoutTypes, modes, and optionalFields. A collection containing any of these values: Compact—Use this value... (optional)
            modes: The access mode for the record. This value determines which fields to get from a layout. Layouts have different fields for create, edit, and view modes. For example, formula fields are rendered in ... (optional)
            optionalfields: A collection of optional field names. If a field is accessible to the context user, it’s included in the response. If a field isn’t accessible to the context user, it isn’t included in the response... (optional)
            pagesize: The maximum number of child records to return on a page. The default value is 5. (optional)
            updatemru: To add to the most recently used (MRU) list view, set to true. The default value is false. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/records/{record_id}"
        params = self._build_params(**{"childRelationships": childrelationships, "fields": fields, "layoutTypes": layouttypes, "modes": modes, "optionalFields": optionalfields, "pageSize": pagesize, "updateMru": updatemru})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_a_batch_of_records(
        self,
        record_ids: str,
        version: str,
        childrelationships: Optional[str] = None,
        fields: Optional[str] = None,
        layouttypes: Optional[str] = None,
        modes: Optional[str] = None,
        optionalfields: Optional[str] = None,
        pagesize: Optional[str] = None,
        updatemru: Optional[str] = None
    ) -> SalesforceResponse:
        """Get data for a batch of records.

        HTTP GET: /services/data/v{version}/ui-api/records/batch/{RECORD_IDS}

        Args:
            record_ids: Path parameter: RECORD_IDS
            version: Path parameter: version
            childrelationships: A collection of child relationship names. The records with those child relationship names are included in the response. Specify names in the format ObjectApiName.ChildRelationshipName or ObjectApiN... (optional)
            fields: Specifies the fields to return. If this property is specified, the response is a union of fields and optionalFields. If the context user doesn’t have access to a field, an error is returned. If you... (optional)
            layouttypes: Specifies the fields to return. If this property is specified, the response is a union of layoutTypes, modes, and optionalFields. A collection containing any of these values: Compact—Use this value... (optional)
            modes: The access mode for the record. This value determines which fields to get from a layout. Layouts have different fields for create, edit, and view modes. For example, formula fields are rendered in ... (optional)
            optionalfields: A collection of optional field names. If a field is accessible to the context user, it’s included in the response. If a field isn’t accessible to the context user, it isn’t included in the response... (optional)
            pagesize: The maximum number of child records to return on a page. The default value is 5. (optional)
            updatemru: To add to the most recently used (MRU) list view, set to true. The default value is false. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/records/batch/{record_ids}"
        params = self._build_params(**{"childRelationships": childrelationships, "fields": fields, "layoutTypes": layouttypes, "modes": modes, "optionalFields": optionalfields, "pageSize": pagesize, "updateMru": updatemru})
        body = None

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def create_a_record(self, version: str, data: Optional[Dict[str, Any]] = None) -> SalesforceResponse:
        """Create a record. First, make a request to the Clone Record Default or Create Record Default resources to get the default metadata and data for the record. As of API version 43.0, if you pass read-o...

        HTTP POST: /services/data/v{version}/ui-api/records

        Args:
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/records"
        params = None
        body = data

        return await self._execute_request(
            method="POST",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_default_values_to_clone_a_record(
        self,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        formfactor: Optional[str] = None,
        optionalfields: Optional[str] = None,
        recordtypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Get the default layout information, object information, and data for cloning a record. After getting the default values, make a request to POST /ui-api/records to create the record. The response co...

        HTTP GET: /services/data/v{version}/ui-api/record-defaults/clone/{RECORD_ID}

        Args:
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data
            formfactor: The layout display size for the record. One of these values: Large—(Default) Use this value to get a layout for desktop display size. Medium—Use this value to get a layout for tablet display size. ... (optional)
            optionalfields: A collection of optional field names. If a field is accessible to the context user, it’s included in the response. If a field isn’t accessible to the context user, it isn’t included in the response... (optional)
            recordtypeid: The ID of the record type (RecordType object) for the new record. If not provided, the default record type is used. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/record-defaults/clone/{record_id}"
        params = self._build_params(**{"formFactor": formfactor, "optionalFields": optionalfields, "recordTypeId": recordtypeid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_default_values_to_create_a_record(
        self,
        sobject_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        formfactor: Optional[str] = None,
        optionalfields: Optional[str] = None,
        recordtypeid: Optional[str] = None
    ) -> SalesforceResponse:
        """Get the default values for fields for a new record of a specified object and optional record type. After getting the default values, make a request to POST /ui-api/records to create the record. The...

        HTTP GET: /services/data/v{version}/ui-api/record-defaults/create/{SOBJECT_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            data: Request body data
            formfactor: The layout display size for the record. One of these values: Large—(Default) Use this value to get a layout for desktop display size. Medium—Use this value to get a layout for tablet display size. ... (optional)
            optionalfields: A collection of optional field names. If a field is accessible to the context user, it’s included in the response. If a field isn’t accessible to the context user, it isn’t included in the response... (optional)
            recordtypeid: The ID of the record type (RecordType object) for the new record. If not provided, the default record type is used. (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/record-defaults/create/{sobject_api_name}"
        params = self._build_params(**{"formFactor": formfactor, "optionalFields": optionalfields, "recordTypeId": recordtypeid})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def update_a_record(
        self,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update a record's data. User Interface API enforces Salesforce validation rules. If a validation rule fails, the response is an Error with Output. When you make a PATCH request to update a record, ...

        HTTP PATCH: /services/data/v{version}/ui-api/records/{RECORD_ID}

        Args:
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/records/{record_id}"
        params = None
        body = data

        return await self._execute_request(
            method="PATCH",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def delete_a_record(
        self,
        record_id: str,
        version: str,
        data: Optional[Dict[str, Any]] = None
    ) -> SalesforceResponse:
        """Update a record's data. User Interface API enforces Salesforce validation rules. If a validation rule fails, the response is an Error with Output. When you make a PATCH request to update a record, ...

        HTTP DELETE: /services/data/v{version}/ui-api/records/{RECORD_ID}

        Args:
            record_id: Path parameter: RECORD_ID
            version: Path parameter: version
            data: Request body data

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/records/{record_id}"
        params = None
        body = data

        return await self._execute_request(
            method="DELETE",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_lookup_field_suggestions(
        self,
        sobject_api_name: str,
        field_api_name: str,
        version: str,
        data: Optional[Dict[str, Any]] = None,
        dependentfieldbindings: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        searchtype: Optional[str] = None
    ) -> SalesforceResponse:
        """When a user edits a lookup field, use this resource to search for and display suggestions. You can search for most recently used matches, for matching names, or for any match in a searchable field....

        HTTP GET: /services/data/v{version}/ui-api/lookups/{SOBJECT_API_NAME}/{FIELD_API_NAME}

        Args:
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            field_api_name: Path parameter: FIELD_API_NAME
            version: Path parameter: version
            data: Request body data
            dependentfieldbindings: The dependent field bindings for dependent lookups. These field bindings represent the lookup filter that restricts the valid values for the field. Specify field bindings in a comma-separated list ... (optional)
            page: The page number. The default value is 1. (optional)
            pagesize: The number of items per page. The default value is 25. (optional)
            q: The term the user is searching for. When searchType=Search, specify at least 2 characters. A wildcard at the end of the search term is implied. For example, q=ca returns Cat and Cats. When searchTy... (optional)
            searchtype: The type of search to perform. One of these values: Recent—Return most recently used matches. Search—Search for records with searchable fields that match the query term. TypeAhead—Search for record... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/lookups/{sobject_api_name}/{field_api_name}"
        params = self._build_params(**{"dependentFieldBindings": dependentfieldbindings, "page": page, "pageSize": pagesize, "q": q, "searchType": searchtype})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

    async def get_lookup_field_suggestions_for_a_specified_object(
        self,
        target_api_name: str,
        sobject_api_name: str,
        version: str,
        field_api_name: str,
        data: Optional[Dict[str, Any]] = None,
        dependentfieldbindings: Optional[str] = None,
        page: Optional[str] = None,
        pagesize: Optional[str] = None,
        q: Optional[str] = None,
        searchtype: Optional[str] = None
    ) -> SalesforceResponse:
        """When a user edits a lookup field, use this resource to search for and display suggestions for a specified object. You can search for most recently used matches, for matching names, or for any match...

        HTTP GET: /services/data/v{version}/ui-api/lookups/{SOBJECT_API_NAME}/{FIELD_API_NAME}/{TARGET_API_NAME}

        Args:
            target_api_name: Path parameter: TARGET_API_NAME
            sobject_api_name: Path parameter: SOBJECT_API_NAME
            version: Path parameter: version
            field_api_name: Path parameter: FIELD_API_NAME
            data: Request body data
            dependentfieldbindings: The dependent field bindings for dependent lookups. These field bindings represent the lookup filter that restricts the valid values for the field. Specify field bindings in a comma-separated list ... (optional)
            page: The page number. The default value is 1. (optional)
            pagesize: The number of items per page. The default value is 25. (optional)
            q: The term the user is searching for. When searchType=Search, specify at least 2 characters. A wildcard at the end of the search term is implied. For example, q=ca returns Cat and Cats. When searchTy... (optional)
            searchtype: The type of search to perform. One of these values: Recent—Return most recently used matches. Search—Search for records with searchable fields that match the query term. TypeAhead—Search for record... (optional)

        Returns:
            SalesforceResponse with success status and data/error
        """
        path = f"/services/data/v{version}/ui-api/lookups/{sobject_api_name}/{field_api_name}/{target_api_name}"
        params = self._build_params(**{"dependentFieldBindings": dependentfieldbindings, "page": page, "pageSize": pagesize, "q": q, "searchType": searchtype})
        body = data

        return await self._execute_request(
            method="GET",
            path=path,
            params=params,
            body=body,
            content_type="application/json"
        )

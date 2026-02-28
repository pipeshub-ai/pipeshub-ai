import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config.configuration_service import ConfigurationService

try:
    from azure.identity import ClientSecretCredential as SyncClientSecretCredential  # type: ignore
    from azure.identity.aio import ClientSecretCredential  #type: ignore
    from kiota_authentication_azure.azure_identity_authentication_provider import (  #type: ignore
        AzureIdentityAuthenticationProvider,
    )
    from kiota_http.httpx_request_adapter import HttpxRequestAdapter  #type: ignore
    from msgraph import GraphServiceClient  #type: ignore
    from msgraph_core import GraphClientFactory  # type: ignore
except ImportError:
    raise ImportError("azure-identity is not installed. Please install it with `pip install azure-identity`")

from app.sources.client.iclient import IClient


class GraphMode(str, Enum):
    DELEGATED = "delegated"
    APP = "app"
@dataclass
class MSGraphResponse:
    """Standardized response wrapper for Microsoft Graph operations."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate response state."""
        if self.success and self.error:
            raise ValueError("Response cannot be successful and have an error")


class MSGraphClientViaUsernamePassword:
    """Microsoft Graph client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, username: str, password: str, client_id: str, tenant_id: str, mode: GraphMode = GraphMode.APP) -> None:
        self.mode = mode
        #TODO: Implement
        pass

    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self.client

    def get_mode(self) -> GraphMode:
        return self.mode

class MSGraphClientWithCertificatePath:
    def __init__(self, certificate_path: str, tenant_id: str, client_id: str, mode: GraphMode = GraphMode.APP) -> None:
        self.mode = mode
        #TODO: Implement
        pass

    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self.client

    def get_mode(self) -> GraphMode:
        return self.mode

class MSGraphClientWithClientIdSecret:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        scopes: List[str] = ["https://graph.microsoft.com/.default"],
        mode: GraphMode = GraphMode.APP
    ) -> None:
        self.mode = mode
        # Store credential as instance variable to prevent HTTP transport from being closed prematurely
        self.credential: Optional[Any] = None

        if mode == GraphMode.APP:
            # App-only (client credentials) auth for enterprise/service scenarios
            # Requires Application permissions + Admin consent.
            self.credential = ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
            auth_provider = AzureIdentityAuthenticationProvider(self.credential, scopes=scopes)
            adapter = HttpxRequestAdapter(auth_provider)
            self.client = GraphServiceClient(request_adapter=adapter)
        else:
            raise ValueError(f"Invalid mode: {mode}")

    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self.client

    def get_mode(self) -> GraphMode:
        return self.mode

    async def close(self) -> None:
        """Close the credential and release resources."""
        if self.credential and hasattr(self.credential, 'close'):
            await self.credential.close()
            self.credential = None

@dataclass
class MSGraphUsernamePasswordConfig:
    """Configuration for Microsoft Graph client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        client_id: The client id to use for authentication
        tenant_id: The tenant id to use for authentication
    """
    username: str
    password: str
    client_id: str
    tenant_id: str

    def create_client(self, mode: GraphMode = GraphMode.APP) -> MSGraphClientViaUsernamePassword:
        return MSGraphClientViaUsernamePassword(self.username, self.password, self.client_id, self.tenant_id, mode=mode)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class MSGraphClientWithClientIdSecretConfig:
    """Configuration for Microsoft Graph client via client id, client secret and tenant id
    Args:
        client_id: The client id to use for authentication
        client_secret: The client secret to use for authentication
        tenant_id: The tenant id to use for authentication
    """
    client_id: str
    client_secret: str
    tenant_id: str

    def create_client(self, mode: GraphMode = GraphMode.APP) -> MSGraphClientWithClientIdSecret:
        return MSGraphClientWithClientIdSecret(self.client_id, self.client_secret, self.tenant_id, mode=mode)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class MSGraphClientWithCertificatePathConfig:
    """Configuration for Microsoft Graph client via certificate path
    Args:
        certificate_path: The path to the certificate to use for authentication
        tenant_id: The tenant id to use for authentication
        client_id: The client id to use for authentication
    """
    certificate_path: str
    tenant_id: str
    client_id: str
    def create_client(self, mode: GraphMode = GraphMode.APP) -> MSGraphClientWithCertificatePath:
        return MSGraphClientWithCertificatePath(self.certificate_path, self.tenant_id, self.client_id, mode=mode)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class MSGraphClient(IClient):
    """Builder class for Microsoft Graph clients with different construction methods"""

    def __init__(
        self,
        client: MSGraphClientViaUsernamePassword | MSGraphClientWithClientIdSecret | MSGraphClientWithCertificatePath,
        mode: GraphMode = GraphMode.APP) -> None:
        """Initialize with a Microsoft Graph client object"""
        self.client = client
        self.mode = mode

    def get_client(self) -> MSGraphClientViaUsernamePassword | MSGraphClientWithClientIdSecret | MSGraphClientWithCertificatePath:
        """Return the Microsoft Graph client object"""
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: MSGraphUsernamePasswordConfig | MSGraphClientWithClientIdSecretConfig | MSGraphClientWithCertificatePathConfig, #type:ignore
        mode: GraphMode = GraphMode.APP) -> 'MSGraphClient':
        """
        Build MSGraphClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: MSGraphConfigBase instance
        Returns:
            MSGraphClient instance with placeholder implementation
        """
        return cls(config.create_client(mode))

    @classmethod
    async def build_from_services(
        cls,
        service_name: str,
        logger: logging.Logger,
        config_service: ConfigurationService,
        mode: GraphMode = GraphMode.APP,
        connector_instance_id: Optional[str] = None,
    ) -> 'MSGraphClient':
        """
        Build MSGraphClient using configuration service
        Args:
            service_name: Service name
            logger: Logger instance
            config_service: Configuration service instance
            mode: Graph mode (APP or DELEGATED)
        Returns:
            MSGraphClient instance
        """
        try:
            # Get Microsoft Graph configuration from the configuration service
            config = await cls._get_connector_config(service_name.replace(" ", "").lower(), logger, config_service, connector_instance_id)

            if not config:
                raise ValueError("Failed to get Microsoft Graph connector configuration")
            auth_config = config.get("auth",{}) or {}
            # Extract configuration values
            auth_type = auth_config.get("authType", "OAUTH_ADMIN_CONSENT")  # client_secret, username_password, certificate
            tenant_id = auth_config.get("tenantId", "")
            client_id = auth_config.get("clientId", "")

            if not tenant_id or not client_id:
                raise ValueError("Tenant ID and Client ID are required for Microsoft Graph authentication")

            # Create appropriate client based on auth type
            # to be implemented
            if auth_type == "USERNAME_PASSWORD":
                username = auth_config.get("username", "")
                password = auth_config.get("password", "")
                if not username or not password:
                    raise ValueError("Username and password required for username_password auth type")
                client = MSGraphClientViaUsernamePassword(username, password, client_id, tenant_id, mode)

            # to be implemented
            # elif auth_type == "OAUTH":
            #     access_token = config.get("credentials",{}).get("accessToken", "")
            #     certificate_path = config.get("credentials",{}).get("certificatePath", "")
            #     if not certificate_path:
            #         raise ValueError("Certificate path required for certificate auth type")
            #     client = MSGraphClientWithCertificatePath(certificate_path, tenant_id, client_id, mode)

            elif auth_type == "OAUTH_ADMIN_CONSENT":  # Default to client_secret auth
                client_secret = auth_config.get("clientSecret", "")
                if not client_secret:
                    raise ValueError("Client secret required for client_secret auth type")
                scopes = auth_config.get("scopes", ["https://graph.microsoft.com/.default"])
                client = MSGraphClientWithClientIdSecret(client_id, client_secret, tenant_id, scopes, mode)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client, mode)

        except Exception as e:
            logger.error(f"Failed to build Microsoft Graph client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(service_name: str, logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch connector config from etcd for Microsoft Graph."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get Microsoft Graph connector configuration for instance {service_name} {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get Microsoft Graph connector config: {e}")
            raise ValueError(f"Failed to get Microsoft Graph connector configuration for instance {service_name} {connector_instance_id}")

    # =========================================================================
    # TOOLSET-BASED CLIENT CREATION (New Architecture)
    # =========================================================================

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: Dict[str, Any],
        service_name: str,
        logger: logging.Logger,
    ) -> 'MSGraphClient':
        """
        Build MSGraphClient from toolset configuration stored in etcd.

        This is the new architecture for creating clients - toolset configs are
        stored per-user at /services/toolsets/{user_id}/{toolset_type}.

        The toolset uses OAuth (delegated) flow: the user authenticates via the
        OAuth consent screen and we receive an access_token + refresh_token.
        We use MSAL to manage token lifecycle and construct a Graph client
        whose /me/ endpoint resolves to the authenticated user.

        Args:
            toolset_config: Toolset configuration from etcd containing:
                - auth: { clientId, clientSecret, tenantId, type }
                - credentials: { access_token, refresh_token, expires_in, token_type, scope }
                - isAuthenticated: bool
            service_name: Name of Microsoft service (outlook, one_drive, etc.)
            logger: Logger instance

        Returns:
            MSGraphClient instance

        Raises:
            ValueError: If configuration is invalid or missing required fields
        """
        if not toolset_config:
            raise ValueError("Toolset configuration is required")

        auth_config = toolset_config.get("auth", {})
        credentials_config = toolset_config.get("credentials", {})
        is_authenticated = toolset_config.get("isAuthenticated", False)

        logger.debug(f"Microsoft {service_name} toolset auth keys: {list(auth_config.keys())}")
        logger.debug(f"Microsoft {service_name} toolset credential keys: {list(credentials_config.keys())}")

        if not is_authenticated:
            raise ValueError(
                f"Microsoft {service_name} toolset is not authenticated. "
                "Please complete the OAuth flow first."
            )

        if not credentials_config:
            raise ValueError(
                f"Microsoft {service_name} toolset has no credentials. "
                "Please re-authenticate."
            )

        # -----------------------------------------------------------------
        # Extract OAuth tokens from credentials
        # -----------------------------------------------------------------
        access_token = credentials_config.get("access_token")
        refresh_token = credentials_config.get("refresh_token")

        # Extract OAuth client credentials from auth config
        client_id = auth_config.get("clientId") or auth_config.get("client_id")
        client_secret = auth_config.get("clientSecret") or auth_config.get("client_secret")
        tenant_id = auth_config.get("tenantId") or auth_config.get("tenant_id", "common")

        if not access_token:
            raise ValueError(
                f"Access token not found in Microsoft {service_name} toolset credentials. "
                f"Available keys: {list(credentials_config.keys())}"
            )

        # Validate that access_token is not a placeholder or invalid
        access_token_str = str(access_token).strip()
        if not access_token_str or access_token_str.lower() in [
            "me-token-to-replace",
            "token-to-replace",
            "placeholder",
            "your-access-token",
            "access_token",
        ]:
            raise ValueError(
                f"Invalid access token in Microsoft {service_name} toolset credentials. "
                f"The stored token appears to be a placeholder value. "
                f"Please re-authenticate the toolset by going to Settings > Toolsets and completing the OAuth flow again."
            )

        # Basic JWT token validation (JWT tokens have 3 parts separated by dots)
        if "." not in access_token_str or len(access_token_str.split(".")) != 3:
            logger.warning(
                f"⚠️ Access token for Microsoft {service_name} does not appear to be a valid JWT token. "
                f"This may cause authentication failures."
            )

        if not client_id or not client_secret:
            raise ValueError(
                f"OAuth client credentials (clientId/clientSecret) not found for Microsoft {service_name}. "
                f"Available auth keys: {list(auth_config.keys())}"
            )

        if not refresh_token:
            logger.warning(
                f"⚠️ Refresh token missing for Microsoft {service_name} toolset. "
                "Token refresh will fail when access token expires."
            )

        # Determine tenant endpoint — fall back to "common" for multi-tenant/personal accounts.
        effective_tenant = (tenant_id or "").strip() or "common"

        # -----------------------------------------------------------------
        # Use MSAL ConfidentialClientApplication for token management
        # -----------------------------------------------------------------
        try:
            import msal  # type: ignore
            import time as _time

            # Scopes the token was originally granted for
            stored_scope = credentials_config.get("scope", "")
            if isinstance(stored_scope, str):
                scope_list = [s.strip() for s in stored_scope.split() if s.strip()]
            elif isinstance(stored_scope, list):
                scope_list = stored_scope
            else:
                scope_list = []

            # Fallback scopes if nothing was stored
            if not scope_list:
                scope_list = auth_config.get("scopes", [
                    "Mail.ReadWrite",
                    "Mail.Send",
                    "Calendars.ReadWrite",
                    "User.Read",
                    "offline_access",
                ])

            # MSAL scopes must NOT include "openid", "profile", "offline_access"
            msal_excluded = {"openid", "profile", "offline_access"}
            msal_scopes = [s for s in scope_list if s.lower() not in msal_excluded]

            authority = f"https://login.microsoftonline.com/{effective_tenant}"

            msal_app = msal.ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=authority,
            )
            logger.debug(
                f"MSAL ConfidentialClientApplication created "
                f"(authority={authority})"
            )

            # -----------------------------------------------------------------
            # Attempt to get a fresh token via refresh_token first.
            # -----------------------------------------------------------------
            final_access_token = access_token
            if refresh_token:
                try:
                    result = msal_app.acquire_token_by_refresh_token(
                        refresh_token=refresh_token,
                        scopes=msal_scopes,
                    )
                    if "access_token" in result:
                        refreshed_token = result["access_token"]
                        # Validate the refreshed token is not a placeholder
                        refreshed_token_str = str(refreshed_token).strip()
                        if refreshed_token_str and refreshed_token_str.lower() not in [
                            "me-token-to-replace",
                            "token-to-replace",
                            "placeholder",
                            "your-access-token",
                            "access_token",
                        ]:
                            final_access_token = refreshed_token
                            logger.info(
                                f"✅ MSAL token refresh succeeded for Microsoft {service_name}"
                            )
                        else:
                            logger.error(
                                f"❌ MSAL token refresh returned invalid placeholder token. "
                                f"Falling back to stored access token."
                            )
                    else:
                        error = result.get("error", "unknown")
                        error_desc = result.get("error_description", "")
                        logger.warning(
                            f"⚠️ MSAL token refresh failed ({error}): {error_desc[:200]}. "
                            f"Falling back to stored access token."
                        )
                except Exception as msal_err:
                    logger.warning(
                        f"⚠️ MSAL refresh call failed: {msal_err}. "
                        f"Falling back to stored access token."
                    )
            
            # Final validation of the token we'll actually use
            final_token_str = str(final_access_token).strip()
            if not final_token_str or final_token_str.lower() in [
                "me-token-to-replace",
                "token-to-replace",
                "placeholder",
                "your-access-token",
                "access_token",
            ]:
                logger.error(
                    f"❌ Invalid access token detected for Microsoft {service_name}. "
                    f"Token value (first 50 chars): '{final_token_str[:50]}'"
                )
                raise ValueError(
                    f"Invalid access token for Microsoft {service_name} toolset. "
                    f"The token (stored or refreshed) appears to be a placeholder value: '{final_token_str[:50]}...'. "
                    f"Please re-authenticate the toolset by going to Settings > Toolsets and completing the OAuth flow again."
                )
            
            # Log token info for debugging (first/last few chars only for security)
            token_preview = f"{final_token_str[:10]}...{final_token_str[-10:]}" if len(final_token_str) > 20 else "***"
            logger.debug(
                f"Using access token for Microsoft {service_name} (preview: {token_preview}, "
                f"length: {len(final_token_str)}, is_jwt: {len(final_token_str.split('.')) == 3})"
            )

            # -----------------------------------------------------------------
            # Build the Graph client with the (refreshed) access token.
            #
            # CRITICAL FIX: Do NOT pass base_url to HttpxRequestAdapter.
            #
            # The connector (admin-consent) path constructs the client as:
            #   auth_provider = AzureIdentityAuthenticationProvider(credential, scopes)
            #   adapter = HttpxRequestAdapter(auth_provider)        # NO base_url
            #   self.client = GraphServiceClient(request_adapter=adapter)
            #
            # GraphServiceClient's constructor (BaseGraphServiceClient) does:
            #   1. Sets adapter.base_url = "https://graph.microsoft.com/v1.0"
            #   2. Sets self.path_parameters["baseurl"] = adapter.base_url
            #
            # When we pre-set base_url on the adapter BEFORE passing it to
            # GraphServiceClient, the constructor's path_parameters setup
            # gets out of sync. The SDK's URL template engine uses
            # {+baseurl}/me/messages — and when path_parameters["baseurl"]
            # is wrong, /me resolves to the literal placeholder string
            # 'me-token-to-replace' instead of the /me endpoint.
            #
            # By omitting base_url (matching the connector path), we let
            # GraphServiceClient handle the entire base URL setup consistently.
            # -----------------------------------------------------------------
            from kiota_abstractions.authentication import (  # type: ignore
                AccessTokenProvider,
                AllowedHostsValidator,
                BaseBearerTokenAuthenticationProvider,
            )

            class _MsalTokenProvider(AccessTokenProvider):
                """Token provider backed by MSAL for transparent refresh."""

                _REFRESH_BUFFER_SECONDS = 300  # refresh 5 min before expiry

                def __init__(
                    self,
                    msal_application: msal.ConfidentialClientApplication,
                    current_access_token: str,
                    current_refresh_token: Optional[str],
                    token_scopes: List[str],
                    svc_logger: logging.Logger,
                    initial_expires_at: Optional[float] = None,
                ) -> None:
                    self._msal_app = msal_application
                    self._access_token = current_access_token
                    self._refresh_token = current_refresh_token
                    self._scopes = token_scopes
                    self._logger = svc_logger
                    self._expires_at = initial_expires_at
                    self._refresh_lock: Optional[Any] = None  # lazy asyncio.Lock

                def _is_token_expiring(self) -> bool:
                    if self._expires_at is None:
                        return False
                    return _time.time() >= (self._expires_at - self._REFRESH_BUFFER_SECONDS)

                async def _ensure_lock(self) -> Any:
                    import asyncio
                    if self._refresh_lock is None:
                        self._refresh_lock = asyncio.Lock()
                    return self._refresh_lock

                async def _refresh_access_token(self) -> None:
                    if not self._refresh_token:
                        self._logger.warning(
                            "Cannot refresh Microsoft token — no refresh_token stored."
                        )
                        return

                    try:
                        import asyncio
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(
                            None,
                            lambda: self._msal_app.acquire_token_by_refresh_token(
                                refresh_token=self._refresh_token,
                                scopes=self._scopes,
                            ),
                        )

                        if "access_token" in result:
                            self._access_token = result["access_token"]
                            if "refresh_token" in result:
                                self._refresh_token = result["refresh_token"]
                            expires_in = result.get("expires_in", 3600)
                            self._expires_at = _time.time() + float(expires_in)
                            self._logger.info(
                                "Microsoft access token refreshed successfully via MSAL"
                            )
                        else:
                            error = result.get("error", "unknown")
                            error_desc = result.get("error_description", "")
                            self._logger.error(
                                f"MSAL token refresh failed ({error}): {error_desc[:200]}"
                            )
                    except Exception as refresh_err:
                        self._logger.error(f"MSAL token refresh request failed: {refresh_err}")

                async def get_authorization_token(
                    self,
                    uri: str,
                    additional_authentication_context: Optional[Dict[str, Any]] = None,
                ) -> str:
                    if self._is_token_expiring():
                        lock = await self._ensure_lock()
                        async with lock:
                            if self._is_token_expiring():
                                await self._refresh_access_token()
                    return self._access_token

                def get_allowed_hosts_validator(self) -> AllowedHostsValidator:
                    return AllowedHostsValidator([
                        "graph.microsoft.com",
                        "graph.microsoft.us",
                        "dod-graph.microsoft.us",
                        "microsoftgraph.chinacloudapi.cn",
                        "canary.graph.microsoft.com",
                    ])

            # Compute initial expiry timestamp
            raw_expires = credentials_config.get("expires_at") or credentials_config.get("expiry_date")
            if raw_expires:
                try:
                    stored_expires_at: Optional[float] = float(raw_expires)
                    if stored_expires_at and stored_expires_at > 1e12:
                        stored_expires_at /= 1000.0
                except (TypeError, ValueError):
                    stored_expires_at = None
            else:
                expires_in = credentials_config.get("expires_in")
                stored_expires_at = (_time.time() + float(expires_in)) if expires_in else None

            token_provider = _MsalTokenProvider(
                msal_application=msal_app,
                current_access_token=final_access_token,
                current_refresh_token=refresh_token,
                token_scopes=msal_scopes,
                svc_logger=logger,
                initial_expires_at=stored_expires_at,
            )
            auth_provider = BaseBearerTokenAuthenticationProvider(token_provider)

            # ─── FIX: Match the connector (admin-consent) construction path ───
            # Do NOT pass base_url to HttpxRequestAdapter.
            # Let GraphServiceClient set both adapter.base_url AND its internal
            # path_parameters["baseurl"] via its own constructor defaults.
            #
            # Previously:
            #   adapter = HttpxRequestAdapter(auth_provider, base_url=GRAPH_BASE_URL)
            # This caused 'me-token-to-replace' because path_parameters were
            # out of sync with the adapter's base_url.
            adapter = HttpxRequestAdapter(
                authentication_provider=auth_provider,
            )

            # GraphServiceClient's constructor will:
            # 1. Set adapter.base_url = "https://graph.microsoft.com/v1.0"
            # 2. Set self.path_parameters["baseurl"] = adapter.base_url
            # This ensures {+baseurl}/me/messages resolves correctly.
            graph_client = GraphServiceClient(request_adapter=adapter)
            # ──────────────────────────────────────────────────────────────────

            # -----------------------------------------------------------------
            # FIX: Resolve 'me-token-to-replace' placeholder issue.
            #
            # The msgraph SDK's .me property creates child request builders
            # (e.g., .me.messages) that internally use URL templates like:
            #   {+baseurl}/users/{user%2Did}/messages
            #
            # When path_parameters["user%2Did"] is not set, the SDK falls
            # back to the placeholder 'me-token-to-replace', which causes:
            #   ErrorInvalidUser: "The requested user 'me-token-to-replace' is invalid."
            #
            # Fix: Extract the user's Object ID (oid) from the JWT access
            # token and set it as the user%2Did path parameter. This makes
            # all /me/* calls resolve to /users/{actual-oid}/* which works
            # correctly with delegated tokens.
            #
            # Fallback: if JWT decoding fails, use 'me' as the user ID
            # (the Graph API treats /users/me the same as /me for
            # delegated tokens).
            # -----------------------------------------------------------------
            import base64 as _b64

            user_id_for_graph = "me"  # fallback: /users/me ≡ /me
            try:
                jwt_parts = final_access_token.split(".")
                if len(jwt_parts) == 3:
                    payload_b64 = jwt_parts[1]
                    # Add base64 padding
                    payload_b64 += "=" * (4 - len(payload_b64) % 4)
                    claims = json.loads(_b64.urlsafe_b64decode(payload_b64))
                    oid = claims.get("oid")
                    if oid:
                        user_id_for_graph = oid
                        logger.info(
                            f"Extracted user OID from JWT for Graph API: {oid}"
                        )
            except Exception as jwt_err:
                logger.warning(
                    f"Could not decode JWT for user OID, falling back to 'me': {jwt_err}"
                )

            # Set the user ID in path_parameters so all /me/* URL templates
            # resolve to /users/{oid}/* instead of /users/me-token-to-replace/*
            if hasattr(graph_client, "path_parameters"):
                graph_client.path_parameters["user%2Did"] = user_id_for_graph
                logger.debug(
                    f"Set graph_client path_parameters['user%2Did'] = {user_id_for_graph}"
                )
            else:
                logger.warning(
                    "GraphServiceClient has no path_parameters attribute — "
                    "/me resolution may fail"
                )

            # -----------------------------------------------------------------
            # DEBUG: Log the actual base_url that the adapter will use
            # -----------------------------------------------------------------
            actual_base_url = getattr(adapter, 'base_url', 'UNKNOWN')
            logger.debug(
                f"HttpxRequestAdapter base_url = {actual_base_url}"
            )

            # -----------------------------------------------------------------
            # Wrap in a shim that implements the same interface as other clients.
            #
            # CRITICAL FIX: The msgraph SDK's .me property creates child
            # request builders with a hardcoded 'me-token-to-replace' placeholder
            # in their path_parameters. Setting path_parameters on the parent
            # GraphServiceClient does NOT cascade to .me.messages etc.
            #
            # Solution: Return a proxy around GraphServiceClient that redirects
            # .me access to .users.by_user_id(oid), which uses the correct
            # URL template: {+baseurl}/users/{user%2Did}/messages
            #
            # The Graph API treats /users/{oid} with a delegated token
            # identically to /me — same data, same permissions.
            # -----------------------------------------------------------------
            class _MeRedirectingGraphClient:
                """Proxy around GraphServiceClient that fixes /me resolution.
                
                The msgraph SDK's .me property internally uses a placeholder
                'me-token-to-replace' that doesn't get replaced in delegated
                auth scenarios. This proxy intercepts .me access and redirects
                it to .users.by_user_id(oid), which works correctly.
                """
                def __init__(self, real_client: GraphServiceClient, user_oid: str) -> None:
                    self._real_client = real_client
                    self._user_oid = user_oid

                @property
                def me(self):
                    """Redirect /me to /users/{oid} to avoid 'me-token-to-replace'."""
                    return self._real_client.users.by_user_id(self._user_oid)

                def __getattr__(self, name):
                    """Proxy all other attributes to the real GraphServiceClient."""
                    return getattr(self._real_client, name)

            class _DelegatedGraphClient:
                def __init__(self, graph_svc_client: GraphServiceClient, user_oid: str) -> None:
                    self._client = graph_svc_client
                    self._user_oid = user_oid

                def get_ms_graph_service_client(self) -> GraphServiceClient:
                    # Return the proxy that redirects .me → .users.by_user_id(oid)
                    return _MeRedirectingGraphClient(self._client, self._user_oid)

                def get_mode(self) -> GraphMode:
                    return GraphMode.DELEGATED

            shim = _DelegatedGraphClient(graph_client, user_id_for_graph)
            logger.info(
                f"✅ Created Microsoft {service_name} delegated Graph client from toolset config "
                f"(tenant={effective_tenant}, access_token=present, "
                f"refresh_token={'present' if refresh_token else 'missing'}, "
                f"expires_at={stored_expires_at})"
            )
            return cls(shim, GraphMode.DELEGATED)

        except ImportError as ie:
            logger.error(f"Missing dependency for Microsoft Graph toolset: {ie}")
            raise ValueError(
                f"Missing required package for Microsoft {service_name} client. "
                f"Please install msal: pip install msal"
            ) from ie
        except Exception as e:
            logger.error(f"Failed to build Microsoft Graph client from toolset: {e}")
            raise ValueError(f"Failed to create Microsoft {service_name} client: {str(e)}") from e
"""
Evernote Client using Apache Thrift Protocol
=============================================

Evernote API uses Apache Thrift RPC protocol (NOT REST/HTTP JSON).

Architecture:
- Transport Layer: THttpClient (HTTP is used as transport, but with Thrift on top)
- Protocol: TBinaryProtocol (Thrift binary serialization)
- Services:
  * UserStore: Authentication, user info, version checking
  * NoteStore: Notes, notebooks, tags, resources (user-specific sharded service)

Authentication:
- Token-based: Pass auth token as first parameter to each Thrift method
- Token format: S=s:U=userid:E=timestamp:C=signature...
- OAuth 1.0: Use EvernoteOAuthHandler for initial auth flow

Usage Example:
-------------
```python
from app.sources.client.evernote.evernote import EvernoteClient, EvernoteTokenConfig

# 1. Create client with token
config = EvernoteTokenConfig(
    token="S=s:U=...",
    note_store_url="https://www.evernote.com/shard/s1/notestore",
    sandbox=False
)
client = EvernoteClient.build_with_config(config)

# 2. Access UserStore (for user operations)
user_store = client.get_user_store()
user = user_store.getUser(client.get_token())
print(f"Username: {user.username}")

# 3. Access NoteStore (for notes, notebooks, tags)
note_store = client.get_note_store()
notebooks = note_store.listNotebooks(client.get_token())
for notebook in notebooks:
    print(f"Notebook: {notebook.name}")

# 4. Create a note (Thrift call)
from evernote.edam.type.ttypes import Note
note = Note()
note.title = "My Note"
note.content = '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note>Hello World</en-note>'
created_note = note_store.createNote(client.get_token(), note)
```

Requirements:
- pip install evernote3 (official Evernote SDK with Thrift bindings)
- pip install thrift (Apache Thrift runtime)

Reference:
- Evernote API Docs: https://dev.evernote.com/doc/
- Thrift IDL: https://github.com/evernote/evernote-thrift
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode

from pydantic import BaseModel, Field  # type: ignore
from thrift.protocol import TBinaryProtocol  # type: ignore
from thrift.transport import THttpClient  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

try:
    from evernote.edam.notestore import NoteStore  # type: ignore
    from evernote.edam.userstore import UserStore  # type: ignore
    EVERNOTE_SDK_AVAILABLE = True
except ImportError:
    EVERNOTE_SDK_AVAILABLE = False
    UserStore = None  # type: ignore
    NoteStore = None  # type: ignore


class EvernoteResponse(BaseModel):
    """Standardized Evernote API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class EvernoteThriftClient:
    """Evernote Thrift client for accessing NoteStore and UserStore APIs

    Evernote uses Apache Thrift protocol (not REST) for API communication.
    The token is passed as the first parameter to each Thrift method call.

    Args:
        token: The access token (starts with S=...)
        note_store_url: The user's NoteStore URL (required for NoteStore API calls)
        sandbox: Whether to use sandbox environment (default: False)
    """

    def __init__(
        self,
        token: str,
        note_store_url: Optional[str] = None,
        sandbox: bool = False
    ) -> None:
        if not EVERNOTE_SDK_AVAILABLE:
            raise ImportError(
                "Evernote SDK not available. Install it with: pip install evernote3"
            )

        if not token:
            raise ValueError("Evernote token cannot be empty")

        # Validate token format (should start with S=)
        if not token.startswith('S='):
            raise ValueError(f"Invalid Evernote token format. Token should start with 'S=', got: {token[:10]}...")

        self.token = token
        self.note_store_url = note_store_url
        self.sandbox = sandbox

        # Set service URLs based on environment
        if sandbox:
            self.user_store_uri = "https://sandbox.evernote.com/edam/user"
        else:
            self.user_store_uri = "https://www.evernote.com/edam/user"

        # Initialize Thrift clients (lazy loading)
        self._user_store_client: Optional[Any] = None
        self._note_store_client: Optional[Any] = None

    def get_user_store(self) -> object:
        """Get UserStore Thrift client

        Returns:
            UserStore.Client for authentication and user operations
        """
        if self._user_store_client is None:
            transport = THttpClient.THttpClient(self.user_store_uri)
            protocol = TBinaryProtocol.TBinaryProtocol(transport)
            self._user_store_client = UserStore.Client(protocol)
        return self._user_store_client

    def get_note_store(self) -> object:
        """Get NoteStore Thrift client

        Returns:
            NoteStore.Client for note, notebook, tag operations

        Raises:
            ValueError: If note_store_url is not set
        """
        if not self.note_store_url:
            raise ValueError(
                "note_store_url is required for NoteStore API calls. "
                "Obtain it from OAuth response (edam_noteStoreUrl) or UserStore.getNoteStoreUrl()"
            )

        if self._note_store_client is None:
            transport = THttpClient.THttpClient(self.note_store_url)
            protocol = TBinaryProtocol.TBinaryProtocol(transport)
            self._note_store_client = NoteStore.Client(protocol)
        return self._note_store_client

    def get_token(self) -> str:
        """Get the authentication token"""
        return self.token

    def get_note_store_url(self) -> Optional[str]:
        """Get the NoteStore URL"""
        return self.note_store_url

    def set_note_store_url(self, url: str) -> None:
        """Set the NoteStore URL and reset the NoteStore client"""
        self.note_store_url = url
        self._note_store_client = None


class EvernoteOAuthHandler:
    """Handles OAuth 1.0 flow for Evernote

    OAuth is used for initial authentication. After obtaining the access token,
    use EvernoteThriftClient for actual API calls via Thrift protocol.

    Args:
        consumer_key: The API key (consumer key)
        consumer_secret: The API secret (consumer secret)
        callback_url: The callback URL for OAuth flow
        sandbox: Whether to use sandbox environment (default: False)
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        callback_url: str,
        sandbox: bool = False
    ) -> None:
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.callback_url = callback_url
        self.sandbox = sandbox

        # Set URLs based on environment
        if sandbox:
            self.oauth_base_url = "https://sandbox.evernote.com"
            self.oauth_request_token_url = "https://sandbox.evernote.com/oauth"
            self.oauth_authorize_url = "https://sandbox.evernote.com/OAuth.action"
            self.oauth_access_token_url = "https://sandbox.evernote.com/oauth"
        else:
            self.oauth_base_url = "https://www.evernote.com"
            self.oauth_request_token_url = "https://www.evernote.com/oauth"
            self.oauth_authorize_url = "https://www.evernote.com/OAuth.action"
            self.oauth_access_token_url = "https://www.evernote.com/oauth"

    async def request_temporary_token(self) -> Optional[str]:
        """Step 1: Generate a temporary token
        Returns:
            Temporary OAuth token
        """
        raise NotImplementedError(
            "OAuth 1.0 signing not implemented. Use an OAuth 1.0 library like 'oauthlib' "
            "or 'requests-oauthlib' to properly sign the request."
        )

    def get_authorization_url(self, oauth_token: str) -> str:
        """Step 2: Generate authorization URL for user to approve access
        Args:
            oauth_token: The temporary token from request_temporary_token
        Returns:
            Authorization URL to redirect user to
        """
        params = {"oauth_token": oauth_token}
        return f"{self.oauth_authorize_url}?{urlencode(params)}"

    async def exchange_token_for_access(
        self,
        oauth_token: str,
        oauth_verifier: str
    ) -> Optional[Dict[str, str]]:
        """Step 3: Exchange temporary token and verifier for access token
        Args:
            oauth_token: The temporary OAuth token
            oauth_verifier: The verifier from the callback
        Returns:
            Dict with oauth_token and edam_noteStoreUrl
        """
        raise NotImplementedError(
            "OAuth 1.0 signing not implemented. Use an OAuth 1.0 library like 'oauthlib' "
            "or 'requests-oauthlib' to properly sign the request."
        )

    def _parse_oauth_response(self, response_text: str) -> Dict[str, str]:
        """Parse OAuth response in URL-encoded format
        Args:
            response_text: URL-encoded response string
        Returns:
            Dictionary of parsed parameters
        """
        # Use parse_qs for robust parsing of URL-encoded strings.
        # It handles edge cases and URL decoding automatically.
        parsed_qs = parse_qs(response_text)
        # parse_qs returns a dict where values are lists. Extract the first value for each key.
        return {k: v[0] for k, v in parsed_qs.items()}


class EvernoteTokenConfig(BaseModel):
    """Configuration for Evernote Thrift client via access token
    Args:
        token: The access token to use for authentication
        note_store_url: NoteStore URL for the user (required for NoteStore API calls)
        sandbox: Whether to use sandbox environment
    """
    token: str = Field(..., description="Evernote access token")
    note_store_url: Optional[str] = Field(None, description="User's NoteStore URL")
    sandbox: bool = Field(default=False, description="Use sandbox environment")

    def create_client(self) -> EvernoteThriftClient:
        """Create an Evernote Thrift client"""
        return EvernoteThriftClient(
            self.token,
            self.note_store_url,
            self.sandbox
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class EvernoteOAuthConfig(BaseModel):
    """Configuration for Evernote OAuth handler

    Args:
        consumer_key: The API key (consumer key)
        consumer_secret: The API secret (consumer secret)
        callback_url: The callback URL for OAuth flow
        sandbox: Whether to use sandbox environment
    """
    consumer_key: str = Field(..., description="Evernote consumer key (API key)")
    consumer_secret: str = Field(..., description="Evernote consumer secret")
    callback_url: str = Field(..., description="OAuth callback URL")
    sandbox: bool = Field(default=False, description="Use sandbox environment")

    def create_oauth_handler(self) -> EvernoteOAuthHandler:
        """Create an Evernote OAuth handler"""
        return EvernoteOAuthHandler(
            self.consumer_key,
            self.consumer_secret,
            self.callback_url,
            self.sandbox
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class EvernoteClient(IClient):
    """Builder class for Evernote Thrift clients

    Evernote uses Apache Thrift protocol for all API communication.
    This client provides access to both UserStore and NoteStore services.

    Authentication:
    - Token-based: Pass auth token directly to Thrift method calls
    - OAuth 1.0: Use EvernoteOAuthHandler for initial auth, then create client with token

    Essential components:
    - UserStore: User authentication, account info, version checking
    - NoteStore: Notes, notebooks, tags, resources operations
    - Token: Authentication token (format: S=...)
    - NoteStore URL: User-specific endpoint (sharded architecture)
    """

    def __init__(self, client: EvernoteThriftClient) -> None:
        """Initialize with an Evernote Thrift client"""
        self.client = client

    def get_client(self) -> EvernoteThriftClient:
        """Return the Evernote Thrift client"""
        return self.client

    def get_user_store(self) -> object:
        """Get UserStore Thrift client for authentication and user operations"""
        return self.client.get_user_store()

    def get_note_store(self) -> object:
        """Get NoteStore Thrift client for note, notebook, tag operations"""
        return self.client.get_note_store()

    def get_token(self) -> str:
        """Get the authentication token"""
        return self.client.get_token()

    def get_note_store_url(self) -> Optional[str]:
        """Get the user's NoteStore URL"""
        return self.client.get_note_store_url()

    def set_note_store_url(self, url: str) -> None:
        """Set the NoteStore URL"""
        self.client.set_note_store_url(url)

    @classmethod
    def build_with_config(
        cls,
        config: EvernoteTokenConfig
    ) -> "EvernoteClient":
        """Build EvernoteClient with token configuration
        Args:
            config: EvernoteTokenConfig instance
        Returns:
            EvernoteClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "EvernoteClient":
        """Build EvernoteClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            EvernoteClient instance
        """
        try:
            # Get Evernote configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Evernote connector configuration")

            auth_type = config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH
            auth_config = config.get("auth", {})
            if auth_type == "API_TOKEN":
                token = auth_config.get("apiToken", "")
                note_store_url = auth_config.get("noteStoreUrl")
                sandbox = auth_config.get("sandbox", False)
                if not token:
                    raise ValueError("Token required for token auth type")
                client = EvernoteTokenConfig(token=token, note_store_url=note_store_url, sandbox=sandbox).create_client()
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Evernote client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Evernote."""
        try:
            config = await config_service.get_config("/services/connectors/evernote/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Evernote connector config: {e}")
            raise

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from aiohttp import ClientSession

from app.config.constants.http_status_code import HttpStatusCode
from app.config.key_value_store import KeyValueStore


class GrantType(Enum):
    """OAuth 2.0 Grant Types"""
    AUTHORIZATION_CODE = "authorization_code"
    CLIENT_CREDENTIALS = "client_credentials"
    REFRESH_TOKEN = "refresh_token"
    IMPLICIT = "implicit"
    PASSWORD = "password"

class TokenType(Enum):
    """Token Types"""
    BEARER = "Bearer"
    MAC = "MAC"


@dataclass
class OAuthConfig:
    """OAuth Configuration"""
    client_id: str
    client_secret: str
    redirect_uri: str
    authorize_url: str
    token_url: str
    tenant_id: Optional[str] = None
    scope: Optional[str] = None
    state: Optional[str] = None
    response_type: str = "code"
    grant_type: GrantType = GrantType.AUTHORIZATION_CODE
    additional_params: Dict[str, Any] = field(default_factory=dict)
    token_access_type: Optional[str] = None

    def generate_state(self) -> str:
        """Generate random state for CSRF protection"""
        self.state = secrets.token_urlsafe(32)
        return self.state


@dataclass
class OAuthToken:
    """OAuth Token representation"""
    access_token: str
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    id_token: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    uid: Optional[str] = None   # used for dropbox
    account_id: Optional[str] = None
    team_id: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        """Check if token is expired"""
        if not self.expires_in:
            return False
        expiry_time = self.created_at + timedelta(seconds=self.expires_in)
        return datetime.now() >= expiry_time

    @property
    def expires_at_epoch(self) -> Optional[int]:
        """Get token expiration time"""
        if not self.expires_in:
            return None
        return int((self.created_at + timedelta(seconds=self.expires_in)).timestamp())

    def to_dict(self) -> Dict[str, Any]:
        """Convert token to dictionary"""
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
            "id_token": self.id_token,
            "created_at": self.created_at.isoformat(),
            "uid": self.uid,
            "account_id": self.account_id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OAuthToken':
        """Create token from dictionary"""
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        return cls(**data)


class OAuthProvider:
    """OAuth Provider for handling OAuth 2.0 flows"""

    def __init__(self, config: OAuthConfig, key_value_store: KeyValueStore, credentials_path: str, connector_name: Optional[str] = None) -> None:
        self.config = config
        self.key_value_store = key_value_store
        self._session: Optional[ClientSession] = None
        self.credentials_path = credentials_path
        self.token = None
        self.connector_name = connector_name

    @property
    async def session(self) -> ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "OAuthProvider":
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.close()

    def _get_authorization_url(self,state: str, **kwargs) -> str:
        """Generate authorization URL"""
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": self.config.response_type,
            "token_access_type": self.config.token_access_type,
            "state": state
        }

        if self.config.scope:
            params["scope"] = self.config.scope

        params.update(self.config.additional_params)
        params.update(kwargs)


        return f"{self.config.authorize_url}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str, state: Optional[str] = None, code_verifier: Optional[str] = None) -> OAuthToken:
        # Note: State validation is handled in handle_callback, not here
        # This method only exchanges the code for a token

        data = {
            "grant_type": GrantType.AUTHORIZATION_CODE.value,
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        session = await self.session
        async with session.post(self.config.token_url, data=data) as response:
            response.raise_for_status()
            token_data = await response.json()

        token = OAuthToken(**token_data)
        return token

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Refresh access token using refresh token"""
        data = {
            "grant_type": GrantType.REFRESH_TOKEN.value,
            "refresh_token": refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret
        }

        session = await self.session
        async with session.post(self.config.token_url, data=data) as response:
            if response.status == HttpStatusCode.FORBIDDEN.value:
                # Log additional details for 403 errors (common with expired/invalid refresh tokens)
                error_text = await response.text()
                raise Exception(f"Token refresh failed with 403 Forbidden. This usually means the refresh token has expired or is invalid. Response: {error_text}")
            response.raise_for_status()
            token_data = await response.json()

        # Create new token with current timestamp
        token = OAuthToken(**token_data)

        # Handle different OAuth providers:
        # - Google: doesn't return refresh_token on refresh, so preserve the old one
        # - Atlassian: returns a NEW refresh_token (rotating refresh tokens), so use the new one
        # - Other providers: use new refresh_token if provided, otherwise preserve old one

        # If no new refresh_token was returned, preserve the old one
        # This handles Google and other providers that don't return refresh_token on refresh
        if not token.refresh_token:
            token.refresh_token = refresh_token

        # Update the stored credentials with the new token
        config = await self.key_value_store.get_key(self.credentials_path)
        if not isinstance(config, dict):
            config = {}

        # Store the new token (which includes the new refresh_token if provided)
        config['credentials'] = token.to_dict()
        await self.key_value_store.create_key(self.credentials_path, config)

        return token

    async def ensure_valid_token(self) -> OAuthToken:
        """Ensure we have a valid (non-expired) token"""
        if not self.token:
            raise ValueError("No token found. Please authenticate first.")

        if self.token.is_expired and self.token.refresh_token:
            # Refresh the token
            self.token = await self.refresh_access_token(self.token.refresh_token)
        elif self.token.is_expired:
            raise ValueError("Token expired and no refresh token available. Please re-authenticate.")

        return self.token

    async def revoke_token(self) -> bool:
        """Revoke access token"""
        # Default implementation - override in specific providers
        config = await self.key_value_store.get_key(self.credentials_path)
        if not isinstance(config, dict):
            config = {}
        config['credentials'] = None
        await self.key_value_store.create_key(self.credentials_path, config)
        return True


    def _gen_code_verifier(self, n: int = 64) -> str:
        v = base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")
        return v

    def _gen_code_challenge(self, verifier: str) -> str:
        s256 = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(s256).decode().rstrip("=")

    async def start_authorization(self, *, return_to: Optional[str] = None, use_pkce: bool = True, **extra) -> str:
        state = self.config.generate_state()
        session_data: Dict[str, Any] = {
            "created_at": datetime.utcnow().isoformat(),
            "state": state
        }
        if use_pkce:
            code_verifier = self._gen_code_verifier()
            code_challenge = self._gen_code_challenge(code_verifier)
            session_data.update({
                "code_verifier": code_verifier,
                "pkce": True,
                "return_to": return_to
            })
            extra.update({
                "code_challenge": code_challenge,
                "code_challenge_method": "S256"
            })
        config = await self.key_value_store.get_key(self.credentials_path)
        if not isinstance(config, dict):
            config = {}
        config['oauth'] = session_data
        await self.key_value_store.create_key(self.credentials_path, config)
        return self._get_authorization_url(state=state, **extra)

    async def handle_callback(self, code: str, state: str) -> OAuthToken:
        config = await self.key_value_store.get_key(self.credentials_path)
        if not isinstance(config, dict):
            config = {}

        oauth_data = config.get('oauth', {}) or {}
        stored_state = oauth_data.get("state")

        # Validate state
        if not stored_state or stored_state != state:
            # Idempotent handling: if credentials already exist, treat as success
            existing_creds = config.get('credentials')
            if isinstance(existing_creds, dict) and existing_creds.get('access_token'):
                try:
                    token = OAuthToken.from_dict(existing_creds)
                except (TypeError, ValueError, KeyError):
                    # If stored creds are malformed, fall back to error
                    raise ValueError("Invalid or expired state")
                self.token = token
                return token
            # No existing credentials -> genuine invalid/expired state
            raise ValueError("Invalid or expired state")

        token = await self.exchange_code_for_token(code=code, state=state, code_verifier=oauth_data.get("code_verifier"))
        self.token = token

        # Clean up OAuth state and store credentials
        config['oauth'] = None  # remove transient state after successful exchange

        # Store the new token (use the refresh_token from the response if available)
        config['credentials'] = token.to_dict()
        await self.key_value_store.create_key(self.credentials_path, config)

        return token

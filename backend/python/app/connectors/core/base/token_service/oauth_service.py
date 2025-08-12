import base64
import hashlib
import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from aiohttp import ClientSession

from app.config.key_value_store import KeyValueStore
from app.connectors.services.base_arango_service import BaseArangoService


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
            "created_at": self.created_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OAuthToken':
        """Create token from dictionary"""
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        return cls(**data)


class OAuthProvider(ABC):
    """Abstract OAuth Provider interface"""

    def __init__(self, config: OAuthConfig, key_value_store: KeyValueStore, base_arango_service: BaseArangoService) -> None:
        self.config = config
        self.key_value_store = key_value_store
        self.base_arango_service = base_arango_service
        self._session: Optional[ClientSession] = None

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

    @abstractmethod
    def get_provider_name(self) -> str:
        """Get provider name"""
        pass

    def _get_authorization_url(self,state: str, **kwargs) -> str:
        """Generate authorization URL"""
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": self.config.response_type,
            "state": state
        }

        if self.config.scope:
            params["scope"] = self.config.scope

        params.update(self.config.additional_params)
        params.update(kwargs)

        return f"{self.config.authorize_url}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str, state: Optional[str] = None, code_verifier: Optional[str] = None) -> OAuthToken:
        if state and self.config.state and state != self.config.state:
            raise ValueError("Invalid state parameter - possible CSRF attack")

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
            response.raise_for_status()
            token_data = await response.json()

        token = OAuthToken(**token_data)

        # Store token
        storage_key = f"{self.get_provider_name()}/{self.config.client_id}"
        await self.key_value_store.create_key(storage_key, token)

        return token

    async def ensure_valid_token(self, token: Optional[OAuthToken] = None) -> OAuthToken:
        """Ensure we have a valid (non-expired) token"""
        if not token:
            raise ValueError("No token found. Please authenticate first.")

        if token.is_expired and token.refresh_token:
            # Refresh the token
            token = await self.refresh_access_token(token.refresh_token)
        elif token.is_expired:
            raise ValueError("Token expired and no refresh token available. Please re-authenticate.")

        return token

    async def revoke_token(self, key: str, token: Optional[str] = None) -> bool:
        """Revoke access token"""
        # Default implementation - override in specific providers
        await self.key_value_store.delete(key)
        return True


    def _gen_code_verifier(self, n: int = 64) -> str:
        v = base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")
        return v

    def _gen_code_challenge(self, verifier: str) -> str:
        s256 = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(s256).decode().rstrip("=")

    async def start_authorization(self, *, return_to: Optional[str] = None, use_pkce: bool = True, **extra) -> str:
        state = self.config.generate_state()
        session_data: Dict[str, Any] = {"created_at": datetime.utcnow().isoformat()}
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
        await self.key_value_store.create_key(f"oauth_state/{self.get_provider_name()}/{state}", session_data)
        return self._get_authorization_url(state=state, **extra)

    async def handle_callback(self, code: str, state: str, token_prefix: str="", save_token: bool = True) -> OAuthToken:
        data = await self.key_value_store.get_key(f"oauth_state/{self.get_provider_name()}/{state}")
        if not data:
            raise ValueError("Invalid or expired state")
        token = await self.exchange_code_for_token(code=code, state=state, code_verifier=data.get("code_verifier"))
        await self.key_value_store.delete_key(f"oauth_state/{self.get_provider_name()}/{state}")
        if save_token:
            await self.key_value_store.create_key(f"{self.get_provider_name()}/{token_prefix}", token)
        return token

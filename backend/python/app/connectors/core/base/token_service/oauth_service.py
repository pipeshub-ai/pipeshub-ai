import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode

from aiohttp import ClientSession

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode


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
    scope_parameter_name: str = "scope"  # Parameter name for scopes in authorization URL (e.g., "scope", "user_scope", "resource")
    token_response_path: Optional[str] = None  # Optional: path to extract token from nested response (e.g., "authed_user" for Slack)

    def generate_state(self) -> str:
        """Generate random state for CSRF protection"""
        self.state = secrets.token_urlsafe(32)
        return self.state

    def normalize_token_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize token response if token_response_path is configured.
        Args:
            response: Raw token response from OAuth provider

        Returns:
            Normalized token dictionary compatible with OAuthToken.from_dict()
        """
        if not self.token_response_path:
            return response

        # Extract from nested path (e.g., response["authed_user"])
        nested_data = response.get(self.token_response_path)
        if not isinstance(nested_data, dict):
            # Fallback to top-level if path doesn't exist (backward compatible)
            return response

        # Start with nested data (this is where the token should be)
        normalized = nested_data.copy()

        # Ensure access_token exists - check nested first, then top-level as fallback
        if "access_token" not in normalized:
            # Try top-level as fallback
            if "access_token" in response:
                normalized["access_token"] = response["access_token"]
            else:
                # If still not found, return original response to avoid breaking
                return response

        # Merge other useful fields from top-level if not in nested
        for field_name in ["scope", "token_type", "expires_in", "refresh_token", "refresh_token_expires_in"]:
            if field_name not in normalized and field_name in response:
                normalized[field_name] = response[field_name]

        # Extract team_id from team.id if present (Slack-specific)
        if "team" in response and isinstance(response.get("team"), dict):
            normalized["team_id"] = response["team"].get("id")

        return normalized


@dataclass
class OAuthToken:
    """OAuth Token representation"""
    access_token: str
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    refresh_token_expires_in: Optional[int] = None  # used by Microsoft/OneDrive
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
            "refresh_token_expires_in": self.refresh_token_expires_in,
            "scope": self.scope,
            "id_token": self.id_token,
            "created_at": self.created_at.isoformat(),
            "uid": self.uid,
            "account_id": self.account_id,
            "team_id": self.team_id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OAuthToken':
        """Create token from dictionary, filtering out unknown fields"""
        # Make a shallow copy to avoid mutating the caller's dict
        data = dict(data)
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        # Filter to only known fields to handle varying OAuth provider responses
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered_data)


class OAuthProvider:
    """OAuth Provider for handling OAuth 2.0 flows"""

    def __init__(self, config: OAuthConfig, configuration_service: ConfigurationService, credentials_path: str, connector_name: Optional[str] = None) -> None:
        self.config = config
        self.configuration_service = configuration_service
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

        # Use configurable scope parameter name (defaults to "scope")
        scope_param_name = getattr(self.config, 'scope_parameter_name', 'scope')
        if self.config.scope:
            params[scope_param_name] = self.config.scope

        params.update(self.config.additional_params)
        params.update(kwargs)

        return f"{self.config.authorize_url}?{urlencode(params)}"

    async def _make_token_request(self, data: dict) -> dict:
        """Helper to make a token request, handling different auth methods."""
        use_basic_auth = self.config.additional_params.get("use_basic_auth", False)
        use_json_body = self.config.additional_params.get("use_json_body", False)

        # Notion and some other providers use Basic Auth header instead of body params
        if not use_basic_auth:
            data["client_id"] = self.config.client_id
            data["client_secret"] = self.config.client_secret

        session = await self.session
        headers = {}

        # Prepare headers for providers requiring Basic Auth (e.g., Notion)
        if use_basic_auth:
            credentials = f"{self.config.client_id}:{self.config.client_secret}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded_credentials}"

            # Add Notion-specific version header if present
            if "notion_version" in self.config.additional_params:
                headers["Notion-Version"] = self.config.additional_params["notion_version"]

        # Prepare POST request kwargs (JSON or form-encoded)
        post_kwargs = {"headers": headers}
        if use_json_body:
            headers["Content-Type"] = "application/json"
            post_kwargs["json"] = data
        else:
            post_kwargs["data"] = data

        # Make token request
        async with session.post(self.config.token_url, **post_kwargs) as response:
            # Check for error status codes (4xx and 5xx)
            if response.status >= HttpStatusCode.BAD_REQUEST.value:
                # Get detailed error info for debugging
                error_text = await response.text()
                # Log detailed error but mask sensitive data
                FIRST_8_CHARS = 8
                masked_client_id = self.config.client_id[:FIRST_8_CHARS] + "..." if len(self.config.client_id) > FIRST_8_CHARS else "***"
                error_msg = (
                    f"OAuth token request failed with status {response.status}. "
                    f"Token URL: {self.config.token_url}, "
                    f"Redirect URI: {self.config.redirect_uri}, "
                    f"Client ID (masked): {masked_client_id}, "
                    f"Response: {error_text}"
                )
                raise Exception(error_msg)

            response.raise_for_status()
            # Handle both JSON and form-encoded responses
            content_type = response.headers.get('Content-Type', '').lower()
            if 'application/json' in content_type:
                token_data = await response.json()
                return token_data
            elif 'application/x-www-form-urlencoded' in content_type or 'text/plain' in content_type:
                text_response = await response.text()
                parsed_data = parse_qs(text_response, keep_blank_values=True)
                token_data = {key: values[0] if values else None for key, values in parsed_data.items()}
                # Convert string numbers to integers for expires_in if present
                if 'expires_in' in token_data and token_data['expires_in']:
                    try:
                        token_data['expires_in'] = int(token_data['expires_in'])
                    except (ValueError, TypeError):
                        pass
                return token_data
            else:
                return await response.json()

    async def exchange_code_for_token(self, code: str, state: Optional[str] = None, code_verifier: Optional[str] = None) -> OAuthToken:
        # Note: State validation is handled in handle_callback, not here
        # This method only exchanges the code for a token

        data = {
            "grant_type": GrantType.AUTHORIZATION_CODE.value,
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        }

        if code_verifier:
            data["code_verifier"] = code_verifier

        token_data = await self._make_token_request(data)
        # Normalize only if configured (backward compatible)
        normalized_data = self.config.normalize_token_response(token_data)

        # Ensure access_token exists after normalization
        if "access_token" not in normalized_data:
            raise ValueError(
                "OAuth token response missing required 'access_token' field. "
            )

        token = OAuthToken.from_dict(normalized_data)
        return token

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        """Refresh access token using refresh token"""
        data = {
            "grant_type": GrantType.REFRESH_TOKEN.value,
            "refresh_token": refresh_token,
        }

        try:
            token_data = await self._make_token_request(data)
        except Exception as e:
            # Enhance error message for 403 errors (common with expired/invalid refresh tokens)
            error_str = str(e)
            # Extract status code from error message using regex for more reliable matching
            status_match = re.search(r"status (\d+)", error_str)
            if status_match and int(status_match.group(1)) == HttpStatusCode.FORBIDDEN.value:
                raise Exception(f"Token refresh failed with 403 Forbidden. This usually means the refresh token has expired or is invalid. {error_str}")
            raise

        # Normalize only if configured (backward compatible)
        normalized_data = self.config.normalize_token_response(token_data)
        # Create new token with current timestamp
        token = OAuthToken.from_dict(normalized_data)

        # Handle different OAuth providers:
        # - Google: doesn't return refresh_token on refresh, so preserve the old one
        # - Atlassian: returns a NEW refresh_token (rotating refresh tokens), so use the new one
        # - Other providers: use new refresh_token if provided, otherwise preserve old one

        # If no new refresh_token was returned, preserve the old one
        # This handles Google and other providers that don't return refresh_token on refresh
        if not token.refresh_token:
            token.refresh_token = refresh_token

        # Update the stored credentials with the new token
        config = await self.configuration_service.get_config(self.credentials_path)
        if not isinstance(config, dict):
            config = {}

        # Store the new token (which includes the new refresh_token if provided)
        config['credentials'] = token.to_dict()
        await self.configuration_service.set_config(self.credentials_path, config)

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
        config = await self.configuration_service.get_config(self.credentials_path)
        if not isinstance(config, dict):
            config = {}
        config['credentials'] = None
        await self.configuration_service.set_config(self.credentials_path, config)
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
            "state": state,
            "used_codes": []  # Start fresh with empty used_codes for new auth flow
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
        config = await self.configuration_service.get_config(self.credentials_path)
        if not isinstance(config, dict):
            config = {}
        # Replace entire oauth session data - this clears any old state, codes, etc.
        # This is important for re-authentication to ensure fresh start
        config['oauth'] = session_data

        await self.configuration_service.set_config(self.credentials_path, config)
        return self._get_authorization_url(state=state, **extra)

    async def handle_callback(self, code: str, state: str) -> OAuthToken:
        config = await self.configuration_service.get_config(self.credentials_path)
        if not isinstance(config, dict):
            config = {}

        oauth_data = config.get('oauth', {}) or {}
        stored_state = oauth_data.get("state")

        # Validate state first (must match for security)
        if not stored_state or stored_state != state:
            # Check if this is a duplicate callback (code already used, credentials exist)
            # This handles browser refreshes or duplicate callback attempts
            existing_creds = config.get('credentials')
            used_codes = oauth_data.get("used_codes", [])

            # Only treat as success if:
            # 1. Credentials exist AND
            # 2. This specific code was already used (indicates duplicate callback)
            if isinstance(existing_creds, dict) and existing_creds.get('access_token') and code in used_codes:
                try:
                    token = OAuthToken.from_dict(existing_creds)
                    self.token = token
                    return token
                except (TypeError, ValueError, KeyError):
                    # If stored creds are malformed, fall back to error
                    raise ValueError("Invalid or expired state")

            # State mismatch and not a duplicate callback -> genuine error
            raise ValueError("Invalid or expired state")

        # Check if this specific code has already been used (prevent duplicate code usage)
        used_codes = oauth_data.get("used_codes", [])
        if code in used_codes:
            # This code was already used - check if we have valid credentials from it
            existing_creds = config.get('credentials')
            if isinstance(existing_creds, dict) and existing_creds.get('access_token'):
                # Return existing credentials from this code (duplicate callback protection)
                try:
                    token = OAuthToken.from_dict(existing_creds)
                    self.token = token
                    return token
                except (TypeError, ValueError, KeyError):
                    pass
            # Code was used but no valid credentials - treat as error
            raise ValueError("Authorization code has already been used")

        try:
            token = await self.exchange_code_for_token(code=code, state=state, code_verifier=oauth_data.get("code_verifier"))
            self.token = token

            # Mark this code as used
            used_codes.append(code)
            oauth_data["used_codes"] = used_codes

            # Store the new token FIRST before clearing OAuth state
            # This ensures credentials are updated even if something fails during cleanup
            config['credentials'] = token.to_dict()

            # Clean up OAuth transient state after successful exchange
            # Clear state and code_verifier, but keep used_codes temporarily
            # to prevent duplicate callback with same code
            config['oauth'] = {
                "used_codes": used_codes  # Keep used codes to prevent replay attacks
            }

            await self.configuration_service.set_config(self.credentials_path, config)

            return token
        except Exception:
            # If token exchange fails, still mark the code as used to prevent retry loops
            used_codes.append(code)
            oauth_data["used_codes"] = used_codes
            config['oauth'] = oauth_data
            await self.configuration_service.set_config(self.credentials_path, config)
            raise

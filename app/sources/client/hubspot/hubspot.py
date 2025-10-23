# ruff: noqa
"""
HubSpot Client Implementation

Provides HubSpotClient class that encapsulates the official HubSpot Python SDK
for secure API access with comprehensive authentication support.

This client supports:
- OAuth 2.0 authentication
- Private app access tokens  
- Rate limiting and retry logic
- Comprehensive error handling
- All HubSpot API endpoints
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from hubspot import HubSpot  # Official HubSpot Python SDK
    from hubspot.crm import contacts, companies, deals, tickets, products, line_items
    from hubspot.crm import quotes, orders, invoices, payments, subscriptions, carts
    from hubspot.crm import leads, listings, appointments, courses, services
    from hubspot.crm import calls, emails, meetings, notes, tasks, communications
    from hubspot.crm import properties, pipelines, schemas, associations, lists_
    from hubspot.crm import imports, exports, timeline, owners
    from hubspot.marketing import marketing_emails, single_send, campaigns, marketing_events, forms
    from hubspot.automation import actions, workflows
    from hubspot.cms import pages, blog_posts, hubdb, domains, url_redirects
    from hubspot.conversations import conversations, visitor_identification
    from hubspot.events import events, behavioral_events
    from hubspot.webhooks import webhooks  
    from hubspot.oauth import oauth
    from hubspot.files import files
    from hubspot.settings import users
    from hubspot.utils.oauth import get_auth_url
    from hubspot.utils.signature import Signature
    from urllib3.util.retry import Retry
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest


@dataclass
class HubSpotResponse:
    """Standardized response format for HubSpot API calls."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    rate_limit_remaining: Optional[int] = None
    rate_limit_reset: Optional[datetime] = None


class HubSpotClient:
    """
    HubSpot API client that encapsulates the official HubSpot Python SDK.
    
    This client provides comprehensive access to all HubSpot APIs including:
    - CRM APIs (Contacts, Companies, Deals, Tickets, etc.)
    - Marketing APIs (Emails, Campaigns, Forms, etc.) 
    - CMS APIs (Pages, Blog Posts, HubDB, etc.)
    - Automation APIs (Workflows, Actions, Sequences)
    - Conversations APIs
    - Events APIs
    - Settings APIs
    - OAuth & Authentication APIs
    
    Authentication methods supported:
    - OAuth 2.0 (for multi-account apps)
    - Private app access tokens (for single-account apps)
    """
    
    def __init__(
        self,
        access_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        base_url: str = "https://api.hubapi.com",
        retry_config: Optional[Retry] = None,
        timeout: int = 30,
        **kwargs
    ) -> None:
        """
        Initialize HubSpot client.
        
        Args:
            access_token: Access token for API authentication
            client_id: OAuth client ID (for OAuth flow)
            client_secret: OAuth client secret (for OAuth flow)
            refresh_token: OAuth refresh token (for token refresh)
            base_url: Base URL for HubSpot API
            retry_config: urllib3 Retry configuration for HTTP retries
            timeout: Request timeout in seconds
            **kwargs: Additional configuration options
        """
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)
        
        # Set up retry configuration if not provided
        if retry_config is None:
            retry_config = Retry(
                total=3,
                backoff_factor=0.3,
                status_forcelist=(429, 500, 502, 504),
            )
        
        # Initialize the official HubSpot SDK client if available
        if SDK_AVAILABLE and access_token:
            try:
                self.sdk_client = HubSpot(
                    access_token=access_token,
                    retry=retry_config
                )
                self.use_sdk = True
                self.logger.info("Initialized HubSpot client with official SDK")
            except Exception as e:
                self.logger.warning(f"Failed to initialize SDK client: {e}")
                self.use_sdk = False
                self.sdk_client = None
        else:
            self.use_sdk = False
            self.sdk_client = None
            self.logger.info("Using HTTP client implementation")
            
        # Initialize HTTP client as fallback
        self.http_client = HTTPClient(
            base_url=self.base_url,
            default_headers={
                "Authorization": f"Bearer {access_token}" if access_token else None,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=timeout
        )
    
    def get_client(self) -> Union[HubSpot, HTTPClient]:
        """Get the underlying client (SDK or HTTP)."""
        if self.use_sdk and self.sdk_client:
            return self.sdk_client
        return self.http_client
    
    def get_base_url(self) -> str:
        """Get the base URL for the API."""
        return self.base_url
        
    # OAuth and Authentication Methods
    # ================================
    
    def get_auth_url(
        self,
        scopes: List[str],
        redirect_uri: str,
        state: Optional[str] = None,
        optional_scopes: Optional[List[str]] = None
    ) -> str:
        """
        Generate OAuth authorization URL.
        
        Args:
            scopes: List of required OAuth scopes
            redirect_uri: Callback URL after authorization
            state: Optional state parameter for security
            optional_scopes: List of optional OAuth scopes
            
        Returns:
            Authorization URL string
        """
        if not self.client_id:
            raise ValueError("client_id is required for OAuth flow")
            
        if SDK_AVAILABLE:
            return get_auth_url(
                scope=tuple(scopes),
                client_id=self.client_id,
                redirect_uri=redirect_uri,
                state=state,
                optional_scope=tuple(optional_scopes) if optional_scopes else None
            )
        else:
            # Manual URL construction if SDK not available
            scope_str = " ".join(scopes)
            if optional_scopes:
                scope_str += " " + " ".join(optional_scopes)
            
            params = {
                "client_id": self.client_id,
                "scope": scope_str,
                "redirect_uri": redirect_uri,
                "response_type": "code"
            }
            
            if state:
                params["state"] = state
                
            from urllib.parse import urlencode
            query_string = urlencode(params)
            return f"https://app.hubspot.com/oauth/authorize?{query_string}"
    
    async def exchange_code_for_tokens(
        self,
        code: str,
        redirect_uri: str
    ) -> HubSpotResponse:
        """
        Exchange authorization code for access and refresh tokens.
        
        Args:
            code: Authorization code from OAuth callback
            redirect_uri: Redirect URI used in authorization
            
        Returns:
            HubSpotResponse containing token information
        """
        if not self.client_id or not self.client_secret:
            return HubSpotResponse(
                success=False,
                error="client_id and client_secret are required for token exchange"
            )
        
        if self.use_sdk and self.sdk_client:
            try:
                tokens = self.sdk_client.oauth.tokens_api.create(
                    grant_type="authorization_code",
                    redirect_uri=redirect_uri,
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    code=code
                )
                
                token_data = tokens.to_dict()
                
                # Update client with new access token
                self.access_token = token_data.get('access_token')
                self.refresh_token = token_data.get('refresh_token')
                
                if self.access_token:
                    self.sdk_client.access_token = self.access_token
                    
                return HubSpotResponse(success=True, data=token_data)
                
            except Exception as e:
                return HubSpotResponse(success=False, error=str(e))
        else:
            # HTTP client implementation
            request = HTTPRequest(
                method="POST",
                url=f"{self.base_url}/oauth/v1/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                    "code": code
                }
            )
            
            try:
                response = await self.http_client.execute(request)
                
                if response.get('access_token'):
                    self.access_token = response['access_token']
                    self.refresh_token = response.get('refresh_token')
                    
                    # Update HTTP client headers
                    self.http_client.default_headers["Authorization"] = f"Bearer {self.access_token}"
                    
                return HubSpotResponse(success=True, data=response)
                
            except Exception as e:
                return HubSpotResponse(success=False, error=str(e))
    
    async def refresh_access_token(self) -> HubSpotResponse:
        """
        Refresh the access token using refresh token.
        
        Returns:
            HubSpotResponse containing new token information
        """
        if not self.refresh_token:
            return HubSpotResponse(
                success=False,
                error="refresh_token is required for token refresh"
            )
            
        if not self.client_id or not self.client_secret:
            return HubSpotResponse(
                success=False,
                error="client_id and client_secret are required for token refresh"
            )
        
        if self.use_sdk and self.sdk_client:
            try:
                tokens = self.sdk_client.oauth.tokens_api.create(
                    grant_type="refresh_token",
                    refresh_token=self.refresh_token,
                    client_id=self.client_id,
                    client_secret=self.client_secret
                )
                
                token_data = tokens.to_dict()
                
                # Update client with new access token
                self.access_token = token_data.get('access_token')
                if token_data.get('refresh_token'):
                    self.refresh_token = token_data['refresh_token']
                
                if self.access_token:
                    self.sdk_client.access_token = self.access_token
                    
                return HubSpotResponse(success=True, data=token_data)
                
            except Exception as e:
                return HubSpotResponse(success=False, error=str(e))
        else:
            # HTTP client implementation
            request = HTTPRequest(
                method="POST",
                url=f"{self.base_url}/oauth/v1/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
            )
            
            try:
                response = await self.http_client.execute(request)
                
                if response.get('access_token'):
                    self.access_token = response['access_token']
                    if response.get('refresh_token'):
                        self.refresh_token = response['refresh_token']
                    
                    # Update HTTP client headers
                    self.http_client.default_headers["Authorization"] = f"Bearer {self.access_token}"
                    
                return HubSpotResponse(success=True, data=response)
                
            except Exception as e:
                return HubSpotResponse(success=False, error=str(e))
    
    async def get_token_info(self) -> HubSpotResponse:
        """
        Get information about the current access token.
        
        Returns:
            HubSpotResponse containing token information
        """
        if not self.access_token:
            return HubSpotResponse(
                success=False,
                error="access_token is required"
            )
        
        if self.use_sdk and self.sdk_client:
            try:
                token_info = self.sdk_client.oauth.access_tokens_api.get()
                return HubSpotResponse(success=True, data=token_info.to_dict())
            except Exception as e:
                return HubSpotResponse(success=False, error=str(e))
        else:
            request = HTTPRequest(
                method="GET",
                url=f"{self.base_url}/oauth/v1/access-tokens/{self.access_token}",
                headers={"Authorization": f"Bearer {self.access_token}"}
            )
            
            try:
                response = await self.http_client.execute(request)
                return HubSpotResponse(success=True, data=response)
            except Exception as e:
                return HubSpotResponse(success=False, error=str(e))
    
    # Webhook Signature Validation
    # ============================
    
    def validate_webhook_signature(
        self,
        signature: str,
        request_body: str,
        client_secret: str,
        http_uri: str,
        signature_version: str = "v1",
        timestamp: Optional[str] = None
    ) -> bool:
        """
        Validate HubSpot webhook signature for security.
        
        Args:
            signature: X-HubSpot-Signature header value
            request_body: Raw request body string
            client_secret: Your app's client secret
            http_uri: The full request URI
            signature_version: Signature version (v1, v2, v3)
            timestamp: X-HubSpot-Request-Timestamp header value
            
        Returns:
            True if signature is valid, False otherwise
        """
        if SDK_AVAILABLE:
            try:
                return Signature.is_valid(
                    signature=signature,
                    client_secret=client_secret,
                    request_body=request_body,
                    http_uri=http_uri,
                    signature_version=signature_version,
                    timestamp=timestamp
                )
            except Exception as e:
                self.logger.error(f"Signature validation failed: {e}")
                return False
        else:
            # Manual signature validation implementation
            import hmac
            import hashlib
            
            try:
                if signature_version == "v1":
                    # v1 signature: sha256(client_secret + http_method + http_uri + request_body)
                    source_string = client_secret + "GET" + http_uri + request_body
                    expected_signature = hashlib.sha256(source_string.encode()).hexdigest()
                    return hmac.compare_digest(signature, expected_signature)
                elif signature_version == "v2":
                    # v2 signature: sha256(client_secret + http_method + http_uri + request_body + timestamp)
                    if not timestamp:
                        return False
                    source_string = client_secret + "POST" + http_uri + request_body + timestamp
                    expected_signature = hashlib.sha256(source_string.encode()).hexdigest()
                    return hmac.compare_digest(signature, expected_signature)
                else:
                    return False
            except Exception as e:
                self.logger.error(f"Manual signature validation failed: {e}")
                return False
    
    # Utility Methods
    # ===============
    
    async def test_connection(self) -> HubSpotResponse:
        """
        Test the API connection and authentication.
        
        Returns:
            HubSpotResponse indicating connection status
        """
        if self.use_sdk and self.sdk_client:
            try:
                # Try to get account information as a connection test
                account_info = self.sdk_client.crm.owners.basic_api.get_page()
                return HubSpotResponse(
                    success=True,
                    data={"message": "Connection successful", "sdk_client": True}
                )
            except Exception as e:
                return HubSpotResponse(
                    success=False,
                    error=f"SDK connection failed: {str(e)}"
                )
        else:
            # HTTP client test
            request = HTTPRequest(
                method="GET",
                url=f"{self.base_url}/crm/v3/owners",
                headers={"Authorization": f"Bearer {self.access_token}"}
            )
            
            try:
                response = await self.http_client.execute(request)
                return HubSpotResponse(
                    success=True,
                    data={"message": "Connection successful", "http_client": True}
                )
            except Exception as e:
                return HubSpotResponse(
                    success=False,
                    error=f"HTTP connection failed: {str(e)}"
                )
    
    def get_client_info(self) -> Dict[str, Any]:
        """
        Get information about the client configuration.
        
        Returns:
            Dictionary containing client information
        """
        return {
            "sdk_available": SDK_AVAILABLE,
            "use_sdk": self.use_sdk,
            "has_access_token": bool(self.access_token),
            "has_oauth_credentials": bool(self.client_id and self.client_secret),
            "has_refresh_token": bool(self.refresh_token),
            "base_url": self.base_url,
            "timeout": self.timeout
        }
    
    # Error Handling and Rate Limiting
    # ================================
    
    def _handle_response(self, response: Any, context: str = "") -> HubSpotResponse:
        """
        Handle API response and extract rate limiting information.
        
        Args:
            response: Raw API response
            context: Context information for logging
            
        Returns:
            Standardized HubSpotResponse
        """
        try:
            if hasattr(response, 'to_dict'):
                data = response.to_dict()
            else:
                data = response
                
            # Extract rate limiting info if available
            rate_limit_remaining = None
            rate_limit_reset = None
            
            if hasattr(response, 'headers'):
                headers = response.headers
                if 'X-HubSpot-RateLimit-Remaining' in headers:
                    rate_limit_remaining = int(headers['X-HubSpot-RateLimit-Remaining'])
                if 'X-HubSpot-RateLimit-Reset' in headers:
                    reset_timestamp = int(headers['X-HubSpot-RateLimit-Reset'])
                    rate_limit_reset = datetime.fromtimestamp(reset_timestamp / 1000)
            
            return HubSpotResponse(
                success=True,
                data=data,
                rate_limit_remaining=rate_limit_remaining,
                rate_limit_reset=rate_limit_reset
            )
            
        except Exception as e:
            self.logger.error(f"Error handling response in {context}: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Response handling error: {str(e)}"
            )
    
    async def _execute_with_retry(self, func, *args, **kwargs) -> HubSpotResponse:
        """
        Execute API call with automatic retry and error handling.
        
        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function
            
        Returns:
            HubSpotResponse with results
        """
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return self._handle_response(result)
                
            except Exception as e:
                if attempt == max_retries - 1:
                    return HubSpotResponse(
                        success=False,
                        error=f"Max retries exceeded: {str(e)}"
                    )
                
                # Check if it's a rate limit error
                if "rate limit" in str(e).lower() or "429" in str(e):
                    await asyncio.sleep(retry_delay * (attempt + 1))
                else:
                    # For non-rate-limit errors, retry with shorter delay
                    await asyncio.sleep(0.1 * (attempt + 1))
                    
        return HubSpotResponse(
            success=False,
            error="Unexpected retry loop exit"
        )
    
    # Context Manager Support
    # ======================
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        # Clean up resources if needed
        pass
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        # Clean up resources if needed  
        pass
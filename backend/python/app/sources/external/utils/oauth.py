# ruff: noqa

import base64
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests # type: ignore

# --- 1. The Generic Client (Reusable Library Code) ---

class GenericOAuth2Client:
    """Generic OAuth2 client that can be used with any OAuth2 provider.

    Supports both "header" (Basic Auth) and "body" (POST body) authentication methods
    for token exchange, making it compatible with various OAuth providers like:
    - Bitbucket, Zoom, Google (header method)
    - Slack, Discord, Facebook (body method)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        auth_endpoint: str,
        token_endpoint: str,
        redirect_uri: str,
        scope_delimiter: str = " ",
        auth_method: str = "header",  # Options: 'header' (Basic Auth) or 'body'
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_endpoint = auth_endpoint
        self.token_endpoint = token_endpoint
        self.redirect_uri = redirect_uri
        self.scope_delimiter = scope_delimiter
        self.auth_method = auth_method

    def get_authorization_url(self, state: str, scopes: Optional[List[str]] = None) -> str:
        """Generates the URL to open in the browser.

        Args:
            state: Security state parameter for CSRF protection
            scopes: Optional list of OAuth scopes to request

        Returns:
            Complete authorization URL with all parameters
        """
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
        }

        if scopes:
            params["scope"] = self.scope_delimiter.join(scopes)

        return f"{self.auth_endpoint}?{urlencode(params)}"

    def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchanges the temporary authorization code for a permanent access token.

        Args:
            code: Authorization code received from OAuth callback

        Returns:
            Dictionary containing token response (access_token, refresh_token, etc.)

        Raises:
            requests.exceptions.RequestException: If token exchange fails
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        return self._make_token_request(data)

    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refreshes an expired access token using a refresh token.

        Args:
            refresh_token: Refresh token obtained from initial token exchange

        Returns:
            Dictionary containing new token response

        Raises:
            requests.exceptions.RequestException: If token refresh fails
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        return self._make_token_request(data)

    def _make_token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
        """Internal helper to handle Header vs Body authentication.

        Args:
            data: Token request data dictionary

        Returns:
            JSON response from token endpoint

        Raises:
            requests.exceptions.RequestException: If token request fails
        """
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        if self.auth_method == "header":
            # "Basic Auth" (Used by Bitbucket, Zoom, Google)
            creds = f"{self.client_id}:{self.client_secret}"
            b64_creds = base64.b64encode(creds.encode()).decode()
            headers["Authorization"] = f"Basic {b64_creds}"
        else:
            # "Post Body" (Used by Slack, Discord, Facebook)
            data["client_id"] = self.client_id
            data["client_secret"] = self.client_secret

        try:
            response = requests.post(self.token_endpoint, data=data, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # HTTPError means we got a response, so we can safely access response.text
            error_msg = f"Token Request Failed: {e}"
            if hasattr(e.response, 'text') and e.response.text:
                error_msg += f"\nResponse: {e.response.text}"
            raise requests.exceptions.HTTPError(error_msg) from e
        except requests.exceptions.RequestException as e:
            # Other RequestExceptions (ConnectionError, Timeout, etc.) don't have a response
            raise requests.exceptions.RequestException(
                f"Token Request Failed: {e}"
            ) from e


# --- 2. The Universal Callback Handler ---

class OAuthHTTPServer(HTTPServer):
    """Custom HTTPServer that encapsulates OAuth callback state.
    
    This class stores the authentication result state, making it thread-safe
    and allowing multiple concurrent OAuth flows without race conditions.
    """
    
    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        # Initialize state for this server instance
        self.auth_result = {"code": None, "state": None, "error": None}


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback redirects."""

    def do_GET(self):
        """Handle GET requests from OAuth provider redirect."""
        # Access state from the server instance
        auth_result = self.server.auth_result
        query = parse_qs(urlparse(self.path).query)

        if "code" in query:
            auth_result["code"] = query["code"][0]
            auth_result["state"] = query.get("state", [None])[0]
            self._send_response("Login Successful! You can close this tab.")
        elif "error" in query:
            auth_result["error"] = query["error"][0]
            self._send_response(f"Login Failed: {auth_result['error']}", color="red")
        else:
            self._send_response("Invalid response.", color="red")

    def _send_response(self, message: str, color: str = "green"):
        """Send HTML response to browser."""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(f"<h1 style='color:{color}'>{message}</h1>".encode())

    def log_message(self, format, *args):
        """Silence logging to reduce noise."""
        pass


# --- 3. Utilities ---

def start_server(port: int = 8080) -> OAuthHTTPServer:
    """Start a local HTTP server to handle OAuth callbacks.

    Args:
        port: Port number for the local server (default: 8080)

    Returns:
        OAuthHTTPServer instance with encapsulated auth state
    """
    server = OAuthHTTPServer(("localhost", port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()
    return server


def perform_oauth_flow(
    client_id: str,
    client_secret: str,
    auth_endpoint: str,
    token_endpoint: str,
    redirect_uri: str,
    scopes: Optional[List[str]] = None,
    scope_delimiter: str = " ",
    auth_method: str = "header",
    port: int = 8080,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Perform complete OAuth2 flow and return access token.

    This is a convenience function that handles the entire OAuth flow:
    1. Initialize client
    2. Generate authorization URL
    3. Start local server
    4. Open browser
    5. Wait for callback
    6. Exchange code for token

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        auth_endpoint: OAuth authorization endpoint URL
        token_endpoint: OAuth token endpoint URL
        redirect_uri: OAuth redirect URI (must match registered callback)
        scopes: Optional list of OAuth scopes
        scope_delimiter: Delimiter for scopes (default: " ")
        auth_method: Authentication method - "header" or "body" (default: "header")
        port: Local server port (default: 8080)
        timeout: Maximum seconds to wait for callback (default: 60)

    Returns:
        Dictionary containing token response from OAuth provider

    Raises:
        ValueError: If authorization fails or state mismatch occurs
        TimeoutError: If callback is not received within timeout period
        requests.exceptions.RequestException: If token exchange fails
    """
    # Initialize client
    client = GenericOAuth2Client(
        client_id=client_id,
        client_secret=client_secret,
        auth_endpoint=auth_endpoint,
        token_endpoint=token_endpoint,
        redirect_uri=redirect_uri,
        scope_delimiter=scope_delimiter,
        auth_method=auth_method,
    )

    # Security prep (state for CSRF protection)
    state = secrets.token_urlsafe(32)

    # Start server & open browser
    # Each server instance has its own isolated auth_result state
    server = start_server(port)
    auth_url = client.get_authorization_url(state=state, scopes=scopes)

    print(f"Opening Browser: {auth_url}")
    webbrowser.open(auth_url)

    # Wait for callback
    # Access state from the server instance (thread-safe)
    print("Waiting for callback...")
    for _ in range(timeout * 2):  # Check every 0.5 seconds
        if server.auth_result["code"] or server.auth_result["error"]:
            break
        time.sleep(0.5)

    # Verify & Exchange
    if server.auth_result["error"]:
        raise ValueError(f"Authorization Failed: {server.auth_result['error']}")
    elif not server.auth_result["code"]:
        raise TimeoutError("Timeout: No callback received within the timeout period.")
    elif server.auth_result["state"] != state:
        # CRITICAL SECURITY CHECK
        raise ValueError(
            f"SECURITY ALERT: State mismatch! Expected {state}, got {server.auth_result['state']}"
        )
    else:
        print("✅ Authorization Code Received. Exchanging for Token...")
        try:
            token_response = client.exchange_code_for_token(code=server.auth_result["code"])
            print("🎉 SUCCESS! Token obtained.")
            return token_response
        except requests.exceptions.RequestException as e:
            raise requests.exceptions.RequestException(
                f"Token Exchange Failed: {e}"
            ) from e


# --- 4. Main Execution (Configuration & Logic) ---

def main():
    """Standalone execution example for generic OAuth2 flow.

    Reads configuration from environment variables:
    - OAUTH_CLIENT_ID: OAuth client ID
    - OAUTH_CLIENT_SECRET: OAuth client secret
    - OAUTH_AUTH_URL: Authorization endpoint URL
    - OAUTH_TOKEN_URL: Token endpoint URL
    - OAUTH_REDIRECT_URI: Redirect URI (default: http://localhost:8080/callback)
    - OAUTH_SCOPES: Comma or space-separated list of scopes
    - OAUTH_SCOPE_DELIMITER: Delimiter for scopes (default: " ")
    - OAUTH_AUTH_METHOD: Authentication method - "header" or "body" (default: "header")
    """
    # --- CONFIGURATION ---
    client_id = os.getenv("OAUTH_CLIENT_ID")
    client_secret = os.getenv("OAUTH_CLIENT_SECRET")
    auth_url = os.getenv("OAUTH_AUTH_URL")
    token_url = os.getenv("OAUTH_TOKEN_URL")
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/callback")

    # Parse scopes from environment variable
    scopes_str = os.getenv("OAUTH_SCOPES", "")
    scope_delimiter = os.getenv("OAUTH_SCOPE_DELIMITER", " ")
    scopes = [s.strip() for s in scopes_str.split(scope_delimiter)] if scopes_str else None

    auth_method = os.getenv("OAUTH_AUTH_METHOD", "header")

    if not client_id or not client_secret:
        print("❌ Error: Missing Required Environment Variables")
        print("   Please set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET")
        return

    if not auth_url or not token_url:
        print("❌ Error: Missing OAuth Endpoints")
        print("   Please set OAUTH_AUTH_URL and OAUTH_TOKEN_URL")
        return

    try:
        token_response = perform_oauth_flow(
            client_id=client_id,
            client_secret=client_secret,
            auth_endpoint=auth_url,
            token_endpoint=token_url,
            redirect_uri=redirect_uri,
            scopes=scopes,
            scope_delimiter=scope_delimiter,
            auth_method=auth_method,
        )

        print("\n🎉 SUCCESS! Token Response:")
        print("\n💡 You can now use the 'access_token' from the response above.")
    except Exception as e:
        print(f"❌ OAuth Flow Failed: {e}")


if __name__ == "__main__":
    main()


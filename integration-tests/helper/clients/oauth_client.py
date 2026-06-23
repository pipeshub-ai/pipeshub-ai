"""OAuth Provider API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class OAuthProviderClient(APIClient):
    """Client for /api/v1/oauth2 and /.well-known/* endpoints.

    Provides methods for OAuth2/OIDC discovery and token operations.
    Note: This client uses an empty BASE since it serves multiple path prefixes.
    """

    BASE = ""

    def openid_configuration(self) -> requests.Response:
        """Get OpenID Connect discovery document (no auth required)."""
        return self.get("/.well-known/openid-configuration", auth=False)

    def oauth_authorization_server_metadata(self) -> requests.Response:
        """Get OAuth authorization server metadata (no auth required)."""
        return self.get("/.well-known/oauth-authorization-server", auth=False)

    def jwks(self) -> requests.Response:
        """Get JSON Web Key Set (no auth required)."""
        return self.get("/.well-known/jwks.json", auth=False)

    def oauth_protected_resource(self, resource: str = "mcp") -> requests.Response:
        """Get OAuth protected resource metadata (no auth required).

        Args:
            resource: Resource identifier (default: "mcp")
        """
        return self.get(f"/.well-known/oauth-protected-resource/{resource}", auth=False)

    def authorize(self, **params: Any) -> requests.Response:
        """OAuth2 authorize endpoint (GET).

        Args:
            **params: Query params (response_type, client_id, redirect_uri, etc.)
        """
        return self.get("/api/v1/oauth2/authorize", params=params, auth=False)

    def authorize_consent(self, **kwargs: Any) -> requests.Response:
        """OAuth2 authorize consent endpoint (POST)."""
        auth = kwargs.pop("auth", False)
        return self.post("/api/v1/oauth2/authorize", json=kwargs, auth=auth)

    def token(self, **kwargs: Any) -> requests.Response:
        """OAuth2 token endpoint."""
        return self.post("/api/v1/oauth2/token", json=kwargs, auth=False)

    def revoke(self, **kwargs: Any) -> requests.Response:
        """OAuth2 token revocation endpoint."""
        return self.post("/api/v1/oauth2/revoke", json=kwargs, auth=False)

    def introspect(self, **kwargs: Any) -> requests.Response:
        """OAuth2 token introspection endpoint."""
        return self.post("/api/v1/oauth2/introspect", json=kwargs, auth=False)

    def userinfo(self, access_token: str) -> requests.Response:
        """OAuth2 userinfo endpoint.

        Args:
            access_token: Valid access token
        """
        return self.get(
            "/api/v1/oauth2/userinfo",
            auth=False,
            headers={"Authorization": f"Bearer {access_token}"},
        )


class OAuthAppsClient(APIClient):
    """Client for /api/v1/oauth-clients endpoints.

    Provides methods for managing OAuth applications (OAuth Apps tag in OpenAPI).
    """

    BASE = "/api/v1/oauth-clients"

    def list_apps(self, **kwargs: Any) -> requests.Response:
        """List OAuth applications."""
        auth = kwargs.pop("auth", True)
        headers = kwargs.pop("headers", None)
        params = kwargs.pop("params", None) or kwargs or None
        request_kwargs: dict[str, Any] = {"auth": auth}
        if params is not None:
            request_kwargs["params"] = params
        if headers is not None:
            request_kwargs["headers"] = headers
        return self.get("/", **request_kwargs)

    def list_scopes(self, **kwargs: Any) -> requests.Response:
        """List OAuth scopes available to the caller."""
        return self.get("/scopes", **kwargs)

    def get_app(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Get OAuth application by ID."""
        return self.get(f"/{app_id}", **kwargs)

    def create_app(self, **kwargs: Any) -> requests.Response:
        """Create a new OAuth application."""
        auth = kwargs.pop("auth", True)
        return self.post("/", json=kwargs, auth=auth)

    def update_app(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Update an OAuth application."""
        auth = kwargs.pop("auth", True)
        return self.put(f"/{app_id}", json=kwargs, auth=auth)

    def delete_app(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Delete an OAuth application."""
        return self.delete(f"/{app_id}", **kwargs)

    def regenerate_secret(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Regenerate client secret for an OAuth application."""
        return self.post(f"/{app_id}/regenerate-secret", **kwargs)

    def suspend_app(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Suspend an OAuth application."""
        return self.post(f"/{app_id}/suspend", **kwargs)

    def activate_app(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Activate a suspended OAuth application."""
        return self.post(f"/{app_id}/activate", **kwargs)

    def list_tokens(self, app_id: str, **kwargs: Any) -> requests.Response:
        """List active tokens for an OAuth application."""
        return self.get(f"/{app_id}/tokens", **kwargs)

    def revoke_all_tokens(self, app_id: str, **kwargs: Any) -> requests.Response:
        """Revoke all tokens for an OAuth application."""
        return self.post(f"/{app_id}/revoke-all-tokens", **kwargs)

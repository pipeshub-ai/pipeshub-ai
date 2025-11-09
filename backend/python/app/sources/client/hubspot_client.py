# backend/python/app/sources/client/hubspot_client.py
"""
Minimal HubSpot client for PipesHub connectors.

Supports private app access tokens (Bearer) or OAuth bearer tokens.
"""

from typing import Optional, Dict, Any
import requests

DEFAULT_BASE = "https://api.hubapi.com"

class HubSpotClientError(Exception):
    pass

class HubSpotClient:
    def __init__(self, access_token: str, base_url: str = DEFAULT_BASE, session: Optional[requests.Session] = None, timeout: int = 10):
        """
        access_token: HubSpot private app token or OAuth access token (string).
        base_url: normally https://api.hubapi.com
        session: optional requests.Session for testing
        """
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token
        # ensure base_url is set to DEFAULT_BASE if None or empty
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            self.base_url = DEFAULT_BASE
        self.session = session or requests.Session()
        self.timeout = timeout
        # make sure session has headers set safely (don't overwrite if present)
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "pipeshub-hubspot-client/0.1"
        }
        # apply headers without losing existing ones
        self.session.headers.update(headers)

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, json: Optional[Any] = None) -> Dict[str, Any]:
        url = self._url(path)
        resp = self.session.request(method, url, params=params, json=json, timeout=self.timeout)
        if not resp.ok:
            # Try to extract error message
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise HubSpotClientError(f"HubSpot API error {resp.status_code}: {err}")
        try:
            return resp.json()
        except ValueError:
            return {"raw_text": resp.text}

    # Example methods
    def get_contacts(self, limit: int = 20, after: Optional[str] = None) -> Dict[str, Any]:
        """
        List contacts (paged).
        Docs: /crm/v3/objects/contacts
        """
        params = {"limit": limit}
        if after:
            params["after"] = after
        return self._request("GET", "/crm/v3/objects/contacts", params=params)

    def get_contact_by_id(self, contact_id: str, properties: Optional[str] = None) -> Dict[str, Any]:
        path = f"/crm/v3/objects/contacts/{contact_id}"
        params = {}
        if properties:
            params["properties"] = properties
        return self._request("GET", path, params=params)

    def search_contacts(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """
        Basic POST search against contacts. Use the search API for more powerful queries.
        """
        path = "/crm/v3/objects/contacts/search"
        body = {
            "limit": limit,
            "query": query
        }
        return self._request("POST", path, json=body)

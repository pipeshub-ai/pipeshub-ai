import logging
from typing import Any, AsyncIterator, Dict, Optional

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# Constants
DEFAULT_BASE_URL = "https://secure.aha.io/api/v1"
DEFAULT_PAGE_SIZE = 50


class AhaResponse(BaseModel):
    """Standardized wrapper for Aha API responses (raw JSON)."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def to_json(self) -> str:
        return self.model_dump_json()


class AhaRESTClient(HTTPClient):
    """
    Aha REST client.

    Supports two authentication methods:
      - Bearer token in Authorization header
      - API key as query param `api_key=<KEY>` (when using API-Key-style access)

    HTTPClient is expected to provide async methods:
      - async def get(self, url: str, params: dict | None = None) -> dict
      - async def post(self, url: str, json: dict | None = None, params: dict | None = None) -> dict
      - async def patch(self, url: str, json: dict | None = None, params: dict | None = None) -> dict
      - async def delete(self, url: str, params: dict | None = None) -> dict
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        access_token: Optional[str] = None,
        api_key: Optional[str] = None,
        auth_mode: str = "bearer",
    ) -> None:
        """
        :param base_url: base Aha API URL (default uses secure.aha.io)
        :param access_token: bearer token (if auth_mode == "bearer")
        :param api_key: api key (if auth_mode == "api_key")
        :param auth_mode: "bearer" or "api_key"
        """
        token_for_httpclient = access_token or api_key or ""
        # HTTPClient constructor signature in repo expects (token, auth_type) for header-based clients;
        # we still construct using token_for_httpclient but manage API key manually below.
        auth_type = "Bearer" if auth_mode == "bearer" else "ApiKey"
        super().__init__(token_for_httpclient, auth_type)

        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.access_token = access_token
        self.api_key = api_key
        self.auth_mode = auth_mode

        # default headers
        self.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def get_base_url(self) -> str:
        return self.base_url

    # ----------------------
    # Internal helpers
    # ----------------------
    def _auth_params(self) -> Dict[str, Any]:
        """Return query params to use for authentication if auth_mode == api_key."""
        if self.auth_mode == "api_key" and self.api_key:
            return {"api_key": self.api_key}
        return {}

    def _wrap_response(self, raw: object) -> AhaResponse:
        """Wrap raw JSON into AhaResponse (raw JSON mode as requested)."""
        return AhaResponse(success=True, data=raw)

    # async def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
    #     url = f"{self.base_url}{endpoint}"
    #     params = params or {}
    #     params.update(self._auth_params())
    #     # HTTPClient.get assumed to be async and return parsed JSON
    #     raw = await self._get(url, params=params)
    #     return self._wrap_response(raw)

    async def _get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        url = f"{self.base_url}{endpoint}"
        params = params or {}
        params.update(self._auth_params())
        client = await self._ensure_client()
        response = await client.get(url, params=params, headers=self.headers)
        raw = response.json()
        return self._wrap_response(raw)

    async def _post(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> AhaResponse:
        url = f"{self.base_url}{endpoint}"
        params = params or {}
        params.update(self._auth_params())
        raw = await self.post(url, json=json or {}, params=params)
        return self._wrap_response(raw)

    async def _patch(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> AhaResponse:
        url = f"{self.base_url}{endpoint}"
        params = params or {}
        params.update(self._auth_params())
        raw = await self.patch(url, json=json or {}, params=params)
        return self._wrap_response(raw)

    async def _delete(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        url = f"{self.base_url}{endpoint}"
        params = params or {}
        params.update(self._auth_params())
        raw = await self.delete(url, params=params)
        return self._wrap_response(raw)

    # ----------------------
    # Pagination helper
    # ----------------------
    async def iter_pages(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        per_page: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Iterate over paginated endpoints and yield each page's JSON.

        Aha pagination uses standard ?page=<n>&per_page=<m> params.
        """
        page = 1
        while True:
            p = {"page": page, "per_page": per_page}
            if params:
                p.update(params)
            resp = await self._get(endpoint, params=p)
            data = resp.data or {}
            # yield the raw page JSON
            yield data
            # Determine if there is a next page:
            # Aha typically returns fewer than per_page elements on the last page.
            # Best-effort detection:
            items = (
                data.get("products")
                or data.get("features")
                or data.get("releases")
                or data.get("ideas")
                or data.get("items")
                or []
            )
            if not isinstance(items, list) or len(items) < per_page:
                break
            page += 1

    # ----------------------
    # Core endpoints (async, return AhaResponse)
    # ----------------------
    # Products
    async def get_products(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/products", params=params)

    async def get_product(
        self, product_id: str, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get(f"/products/{product_id}", params=params)

    async def create_product(self, product_payload: Dict[str, Any]) -> AhaResponse:
        return await self._post("/products", json=product_payload)

    async def update_product(
        self, product_id: str, payload: Dict[str, Any]
    ) -> AhaResponse:
        return await self._patch(f"/products/{product_id}", json=payload)

    async def delete_product(self, product_id: str) -> AhaResponse:
        return await self._delete(f"/products/{product_id}")

    # Features
    async def get_features(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/features", params=params)

    async def get_feature(self, feature_id: str) -> AhaResponse:
        return await self._get(f"/features/{feature_id}")

    async def create_feature(self, payload: Dict[str, Any]) -> AhaResponse:
        return await self._post("/features", json=payload)

    async def update_feature(
        self, feature_id: str, payload: Dict[str, Any]
    ) -> AhaResponse:
        return await self._patch(f"/features/{feature_id}", json=payload)

    async def delete_feature(self, feature_id: str) -> AhaResponse:
        return await self._delete(f"/features/{feature_id}")

    # Epics
    async def get_epics(self, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
        return await self._get("/epics", params=params)

    async def get_epic(self, epic_id: str) -> AhaResponse:
        return await self._get(f"/epics/{epic_id}")

    # Requirements
    async def get_requirements(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/requirements", params=params)

    async def get_requirement(self, req_id: str) -> AhaResponse:
        return await self._get(f"/requirements/{req_id}")

    # Releases
    async def get_releases(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/releases", params=params)

    async def get_release(self, release_id: str) -> AhaResponse:
        return await self._get(f"/releases/{release_id}")

    # Ideas
    async def get_ideas(self, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
        return await self._get("/ideas", params=params)

    async def get_idea(self, idea_id: str) -> AhaResponse:
        return await self._get(f"/ideas/{idea_id}")

    # Users
    async def get_users(self, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
        return await self._get("/users", params=params)

    async def get_user(self, user_id: str) -> AhaResponse:
        return await self._get(f"/users/{user_id}")

    # Attachments & Comments (generic endpoints)
    async def get_comments(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/comments", params=params)

    async def get_attachments(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/attachments", params=params)

    # Product lines, tags, todos, audit logs, records, custom fields
    async def get_product_lines(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/product_lines", params=params)

    async def get_tags(self, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
        return await self._get("/tags", params=params)

    async def get_todos(self, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
        return await self._get("/todos", params=params)

    async def get_audit_logs(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/audit_logs", params=params)

    async def get_records(self, params: Optional[Dict[str, Any]] = None) -> AhaResponse:
        return await self._get("/records", params=params)

    async def get_custom_fields(
        self, params: Optional[Dict[str, Any]] = None
    ) -> AhaResponse:
        return await self._get("/custom_fields", params=params)


class AhaTokenConfig(BaseModel):
    """Configuration for Aha client using Bearer token."""

    base_url: Optional[str] = None
    access_token: str
    ssl: bool = True

    def create_client(self) -> AhaRESTClient:
        return AhaRESTClient(
            base_url=self.base_url, access_token=self.access_token, auth_mode="bearer"
        )

    def to_dict(self) -> dict:
        return self.model_dump()


class AhaApiKeyConfig(BaseModel):
    """Configuration for Aha client using API key (query param)."""

    base_url: Optional[str] = None
    api_key: str
    ssl: bool = True

    def create_client(self) -> AhaRESTClient:
        return AhaRESTClient(
            base_url=self.base_url, api_key=self.api_key, auth_mode="api_key"
        )

    def to_dict(self) -> dict:
        return self.model_dump()


class AhaClient(IClient):
    """Builder wrapper for AhaRESTClient."""

    def __init__(self, client: AhaRESTClient) -> None:
        self.client = client

    def get_client(self) -> AhaRESTClient:
        return self.client

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @classmethod
    def build_with_token(cls, token_config: AhaTokenConfig) -> "AhaClient":
        return cls(token_config.create_client())

    @classmethod
    def build_with_api_key(cls, api_key_config: AhaApiKeyConfig) -> "AhaClient":
        return cls(api_key_config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "AhaClient":
        """
        Load credentials from services if available.

        Implementers: fetch stored credentials from graph_db_service and/or
        config_service; return appropriate AhaClient instance.
        """
        # Placeholder for actual service lookups
        # Example:
        # cfg = await config_service.get("aha")
        # if cfg.get("access_token"):
        #     return cls.build_with_token(AhaTokenConfig(base_url=cfg.get("base_url"), access_token=cfg["access_token"]))
        return cls(client=None)  # type: ignore

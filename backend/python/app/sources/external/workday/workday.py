"""Workday Data Source implementation."""

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.workday.workday import WorkdayClient


class WorkdayDataSource:
    """Thin wrapper over :class:`WorkdayClient` providing higher-level calls."""

    def __init__(self, client: WorkdayClient) -> None:
        """Initialize the data source with a Workday client."""
        self._client = client.get_client()
        if self._client is None:
            msg = "HTTP client is not initialized"
            raise ValueError(msg)
        try:
            self.base_url = self._client.get_base_url().rstrip("/")  # type: ignore[attr-defined]
        except AttributeError as exc:
            msg = "HTTP client does not have get_base_url method"
            raise ValueError(msg) from exc

    def get_data_source(self) -> "WorkdayDataSource":
        """Return the data source instance."""
        return self

    async def get_workers(
        self,
        *,
        headers: dict[str, Any] | None = None,
        **query_params: Any, 
    ) -> HTTPResponse:
        """Fetch workers from Workday.

        Parameters
        ----------
        headers
            Optional extra request headers.
        query_params
            Arbitrary query parameters (e.g. "limit" or "updatedSince").

        """
        _headers: dict[str, str] = {**(headers or {})}
        _headers.setdefault("Accept", "application/json")
        rel_path = "/v1/workers"
        url = f"{self.base_url}{rel_path}"
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_headers,
            query_params={k: str(v) for k, v in query_params.items()},
        )
        return await self._client.execute(req)

    # Example of standard DataSource pattern compliance
    async def fetch_data(self, **kwargs: Any) -> HTTPResponse:  
        """Fetch data for pipelines expecting a `fetch_data` method."""
        return await self.get_workers(**kwargs)

    async def get_permissions(self) -> dict[str, Any]:
        """Retrieve permissions for the Workday tenant.

        Returns an empty mapping for now to satisfy interface requirements.
        """
        return {}

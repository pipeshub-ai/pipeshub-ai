"""Agents API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class AgentsClient(APIClient):
    """Client for /api/v1/agents endpoints.

    Provides both low-level HTTP methods and higher-level domain methods
    for agent management operations.
    """

    BASE = "/api/v1/agents"

    def list_agents(self, **kwargs: Any) -> requests.Response:
        """List all agents."""
        auth = kwargs.pop("auth", True)
        headers = kwargs.pop("headers", None)
        params = kwargs.pop("params", None) or kwargs or None
        request_kwargs: dict[str, Any] = {"auth": auth}
        if params is not None:
            request_kwargs["params"] = params
        if headers is not None:
            request_kwargs["headers"] = headers
        return self.get("/", **request_kwargs)

    def get_agent(self, agent_key: str, **kwargs: Any) -> requests.Response:
        """Get agent by key."""
        return self.get(f"/{agent_key}", **kwargs)

    def create_agent(self, **kwargs: Any) -> requests.Response:
        """Create a new agent."""
        auth = kwargs.pop("auth", True)
        return self.post("/create", json=kwargs, auth=auth)

    def update_agent(self, agent_key: str, **kwargs: Any) -> requests.Response:
        """Update agent by key."""
        auth = kwargs.pop("auth", True)
        return self.put(f"/{agent_key}", json=kwargs, auth=auth)

    def delete_agent(self, agent_key: str, **kwargs: Any) -> requests.Response:
        """Delete an agent."""
        return self.delete(f"/{agent_key}", **kwargs)

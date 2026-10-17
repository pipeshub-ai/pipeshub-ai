"""Can an ordinary member of the org find what a connector synced?

The connector suites check the graph and stream files as the admin. Neither
says whether the content reaches search for anyone else, which is the whole
point of a team connector -- or whether a personal one stays private.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import requests

from helper.graph_provider import GraphProviderProtocol
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from retrieval.ranking import virtual_id_of

SEARCH_LIMIT = 10


async def wait_until_searchable(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    record_name: str,
    *,
    timeout: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any]:
    """The synced record, once indexing has finished and it has a virtual id.

    Sync only puts the record in the graph; search can find it once it is
    indexed. Asking earlier would read as a permission failure.
    """
    deadline = time.monotonic() + timeout
    record: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        record = await graph_provider.get_record_by_name(connector_id, record_name)
        if record and record.get("indexingStatus") == "COMPLETED" and record.get("virtualRecordId"):
            return record
        await asyncio.sleep(poll_interval)
    status = (record or {}).get("indexingStatus", "not in the graph")
    raise AssertionError(
        f"{record_name!r} was not indexed within {timeout}s (status: {status}), "
        "so whether anyone can find it cannot be judged."
    )


def search_connector_as_admin(
    client: PipeshubClient, connector_id: str, query: str
) -> requests.Response:
    return requests.post(
        f"{client.base_url}/api/v1/search",
        headers=client._headers(),
        json={"query": query, "filters": {"apps": [connector_id]}, "limit": SEARCH_LIMIT},
        timeout=client.timeout_seconds,
    )


def search_connector_as(user: SecondUser, connector_id: str, query: str) -> requests.Response:
    return user.search_filtered(query, {"apps": [connector_id]}, SEARCH_LIMIT)


def found(resp: requests.Response, virtual_id: str) -> bool:
    if resp.status_code != 200:
        return False
    body = resp.json()
    hits = (body.get("searchResponse") or body).get("searchResults") or []
    return any(virtual_id_of(hit) == virtual_id for hit in hits)

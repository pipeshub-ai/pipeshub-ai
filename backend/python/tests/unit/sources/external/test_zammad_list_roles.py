"""``ZammadDataSource.list_roles`` asks for permission names, and the reads a sync gates on keep their status.

The connector tells a refused read (403: fails closed, names the missing permission)
from one that passes (5xx, 429: keeps what is stored) by ``status_code``, so
``list_roles``, ``list_groups`` and ``get_current_user`` must carry it.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.zammad.zammad import ZammadDataSource


def _data_source(status: int, body: object) -> tuple[ZammadDataSource, AsyncMock]:
    response = MagicMock(status=status)
    response.text.return_value = "body"
    response.json.return_value = body
    http = MagicMock()
    http.execute = AsyncMock(return_value=response)
    client = MagicMock()
    client.get_client.return_value = http
    client.get_base_url.return_value = "https://zammad.test/"
    return ZammadDataSource(client), http.execute


async def test_list_roles_sends_expand_for_the_permission_names() -> None:
    roles = [{"id": 2, "name": "Agent", "permissions": ["ticket.agent"], "group_ids": {"1": ["full"]}}]
    ds, execute = _data_source(200, roles)

    response = await ds.list_roles(page=2, per_page=100, expand=True)

    assert response.success and response.data == roles and response.status_code == 200
    request = execute.await_args.args[0]
    assert request.method == "GET"
    assert request.url == "https://zammad.test/api/v1/roles"
    assert request.query_params == {"page": "2", "per_page": "100", "expand": "true"}


@pytest.mark.parametrize(("expand", "params"), [
    (None, {"page": "1", "per_page": "1"}),
    (False, {"page": "1", "per_page": "1", "expand": "false"}),
])
async def test_list_roles_sends_expand_only_when_given(expand: bool | None, params: dict[str, str]) -> None:
    ds, execute = _data_source(200, [])

    await ds.list_roles(page=1, per_page=1, expand=expand)

    assert execute.await_args.args[0].query_params == params


@pytest.mark.parametrize("status", [403, 429, 503])
@pytest.mark.parametrize("method", ["list_roles", "list_groups", "get_current_user"])
async def test_a_failed_read_carries_its_status(method: str, status: int) -> None:
    ds, _ = _data_source(status, {"error": "not allowed"})

    response = await getattr(ds, method)()

    assert not response.success
    assert response.status_code == status


@pytest.mark.parametrize("method", ["list_roles", "list_groups", "get_current_user"])
async def test_a_read_that_gets_no_answer_has_no_status(method: str) -> None:
    ds, execute = _data_source(200, [])
    execute.side_effect = ConnectionError("connection reset by peer")

    response = await getattr(ds, method)()

    assert not response.success
    assert response.status_code is None
    assert "connection reset by peer" in (response.error or "")

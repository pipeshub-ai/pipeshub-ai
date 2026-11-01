"""Logos on custom MCP servers: who may set one, which servers take one, and who sees them.

Goes through the real resolver/service lookups against one in-memory store, as
`test_mcp_servers_personal.py` does.
"""
from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from PIL import Image

from app.agents.mcp.models import MCPServerLogoPayload
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    delete_instance,
    get_logos,
    remove_instance_logo,
    set_instance_logo,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG_PATH = "/services/mcp/instances/{}"
USER_PATH = "/services/mcp/user-instances/{}/{}/{}"
LOGO_PATH = "/services/mcp/logos/{}"


def _record(
    instance_id: str, *, owner: str = "admin-1", org_id: str = "org-1", scope: str | None = None, custom: bool = True,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "_id": instance_id, "orgId": org_id, "createdBy": owner, "name": instance_id,
        "typeId": None if custom else "github", "transport": "streamable_http", "authMode": "none",
        "url": "https://mcp.example.com", "isCustom": custom, "createdAt": 1, "updatedAt": 1,
    }
    if scope:
        record["scope"] = scope
    return record


def _stored_logo(tag: str) -> dict[str, str]:
    return {"dataUrl": f"data:image/png;base64,{tag}"}


def _store() -> FakeConfigService:
    return FakeConfigService({
        ORG_PATH.format("org-custom"): _record("org-custom"),
        ORG_PATH.format("org-catalog"): _record("org-catalog", custom=False),
        ORG_PATH.format("other-org"): _record("other-org", org_id="org-2"),
        USER_PATH.format("org-1", "u-alice", "alice-own"): _record("alice-own", owner="u-alice", scope="personal"),
        USER_PATH.format("org-1", "u-bob", "bob-own"): _record("bob-own", owner="u-bob", scope="personal"),
    })


def _png_upload(size: tuple[int, int] = (200, 100)) -> MCPServerLogoPayload:
    out = io.BytesIO()
    Image.new("RGBA", size, (10, 120, 200, 255)).save(out, format="PNG")
    return MCPServerLogoPayload(logo=f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode('ascii')}")


@pytest.fixture(autouse=True)
def _only_admin_1_is_admin() -> Iterator[None]:
    async def _is_admin(user_id: str, *_args: object, **_kwargs: object) -> bool:
        return user_id == "admin-1"

    with patch.object(mcp_servers, "_check_user_is_admin", new=_is_admin):
        yield


async def _status(coro: Any) -> int:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value.status_code


class TestSetLogo:
    async def test_an_admin_sets_the_logo_of_an_org_custom_server(self) -> None:
        store = _store()

        result = await set_instance_logo(route_request(store, user_id="admin-1"), "org-custom", _png_upload())

        assert result["logo"].startswith("data:image/png;base64,")
        assert store.data[LOGO_PATH.format("org-custom")] == {"dataUrl": result["logo"]}
        logo = Image.open(io.BytesIO(base64.b64decode(result["logo"].split(",", 1)[1])))
        assert logo.size == (96, 48)

    async def test_a_member_can_not_change_an_org_servers_logo(self) -> None:
        store = _store()

        assert await _status(set_instance_logo(route_request(store, user_id="u-alice"), "org-custom", _png_upload())) == 403
        assert LOGO_PATH.format("org-custom") not in store.data

    async def test_a_member_sets_the_logo_of_their_own_server(self) -> None:
        store = _store()

        await set_instance_logo(route_request(store, user_id="u-alice"), "alice-own", _png_upload())

        assert LOGO_PATH.format("alice-own") in store.data

    async def test_someone_elses_personal_server_is_not_found_even_by_an_admin(self) -> None:
        store = _store()

        assert await _status(set_instance_logo(route_request(store, user_id="u-alice"), "bob-own", _png_upload())) == 404
        assert await _status(set_instance_logo(route_request(store, user_id="admin-1"), "bob-own", _png_upload())) == 404
        assert LOGO_PATH.format("bob-own") not in store.data

    async def test_another_orgs_server_is_not_found(self) -> None:
        store = _store()

        assert await _status(set_instance_logo(route_request(store, user_id="admin-1"), "other-org", _png_upload())) == 404

    async def test_a_catalog_server_keeps_its_vendors_logo(self) -> None:
        store = _store()

        assert await _status(set_instance_logo(route_request(store, user_id="admin-1"), "org-catalog", _png_upload())) == 400
        assert LOGO_PATH.format("org-catalog") not in store.data

    async def test_an_svg_is_refused_with_the_reason(self) -> None:
        store = _store()
        svg = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode("ascii")

        with pytest.raises(HTTPException) as exc:
            await set_instance_logo(
                route_request(store, user_id="admin-1"), "org-custom",
                MCPServerLogoPayload(logo=f"data:image/svg+xml;base64,{svg}"),
            )

        assert exc.value.status_code == 400
        assert "PNG, JPEG, WebP or GIF" in exc.value.detail
        assert LOGO_PATH.format("org-custom") not in store.data

    async def test_a_failed_write_is_an_error(self) -> None:
        store = _store()

        async def _fail(*_args: object, **_kwargs: object) -> bool:
            return False

        store.set_config = _fail  # type: ignore[method-assign]

        assert await _status(set_instance_logo(route_request(store, user_id="admin-1"), "org-custom", _png_upload())) == 500

    async def test_an_upload_over_the_limit_is_refused_before_the_handler(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            MCPServerLogoPayload(logo="data:image/png;base64," + "A" * (2 * 1024 * 1024))


class TestRemoveLogo:
    async def test_the_owner_removes_their_logo(self) -> None:
        store = _store()
        store.data[LOGO_PATH.format("alice-own")] = _stored_logo("AA==")

        assert await remove_instance_logo(route_request(store, user_id="u-alice"), "alice-own") == {"success": True}
        assert LOGO_PATH.format("alice-own") not in store.data

    async def test_a_member_can_not_remove_an_org_servers_logo(self) -> None:
        store = _store()
        store.data[LOGO_PATH.format("org-custom")] = _stored_logo("AA==")

        assert await _status(remove_instance_logo(route_request(store, user_id="u-alice"), "org-custom")) == 403
        assert LOGO_PATH.format("org-custom") in store.data


class TestListLogos:
    async def test_a_member_sees_the_org_logos_and_their_own_only(self) -> None:
        store = _store()
        for instance_id in ("org-custom", "alice-own", "bob-own", "other-org"):
            store.data[LOGO_PATH.format(instance_id)] = _stored_logo(instance_id)

        result = await get_logos(route_request(store, user_id="u-alice"))

        assert result == {"logos": {
            "org-custom": "data:image/png;base64,org-custom",
            "alice-own": "data:image/png;base64,alice-own",
        }}

    async def test_a_catalog_server_gets_no_logo_even_with_one_stored(self) -> None:
        store = _store()
        store.data[LOGO_PATH.format("org-catalog")] = _stored_logo("x")

        assert await get_logos(route_request(store, user_id="admin-1")) == {"logos": {}}

    async def test_servers_without_a_logo_are_left_out(self) -> None:
        assert await get_logos(route_request(_store(), user_id="admin-1")) == {"logos": {}}


class TestDeleteServer:
    async def test_deleting_a_server_removes_its_logo(self) -> None:
        store = _store()
        store.data[LOGO_PATH.format("alice-own")] = _stored_logo("AA==")

        with patch.object(mcp_servers, "_assert_instance_not_in_use", return_value=None), patch(
            "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
            return_value=None,
        ):
            await delete_instance(route_request(store, user_id="u-alice"), "alice-own")

        assert LOGO_PATH.format("alice-own") not in store.data

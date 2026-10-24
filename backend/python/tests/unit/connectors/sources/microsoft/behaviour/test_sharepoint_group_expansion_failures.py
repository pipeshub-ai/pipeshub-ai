"""A SharePoint site-group claim expansion that fails keeps the stored members."""

from typing import Never

import pytest
from ms_graph_fakes import graph_error, page
from test_sharepoint_online_sync_behaviour import (  # noqa: F401
    GROUPS_DELTA,
    SITE_ID,
    SITE_REST,
    api,
    connector,
    groups_delta_url,
    rest_users,
    serve_site,
)

GUID = "0a1b2c3d-0000-4000-8000-000000000001"


def _serve(api, db, claim: dict) -> None:  # noqa: F811
    serve_site(api)
    api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
    api.on("GET", "/v1.0/groups", page([]))
    db.user_groups[f"{SITE_ID}-7"] = ["ana@contoso.com"]
    api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 7, "Title": "Designers"}))
    api.on("GET", f"{SITE_REST}/sitegroups/GetById(7)/users", rest_users(claim))


@pytest.mark.parametrize("claim", [
    {"LoginName": f"c:0o.c|federateddirectoryclaimprovider|{GUID}", "Title": "T", "PrincipalType": 4},
    {"LoginName": f"c:0t.c|tenant|{GUID}", "Title": "S", "PrincipalType": 4},
])
async def test_a_failed_claim_expansion_keeps_the_stored_site_group_members(connector, api, db, claim) -> None:  # noqa: F811
    _serve(api, db, claim)
    api.on("GET", f"/v1.0/groups/{GUID}/transitive_members", graph_error(503, "serviceNotAvailable"))
    api.on("GET", f"/v1.0/groups/{GUID}/transitiveMembers", graph_error(503, "serviceNotAvailable"))
    api.on("GET", f"/v1.0/directoryObjects/{GUID}", graph_error(404, "NotFound"))

    await connector._sync_user_groups()

    assert db.user_groups[f"{SITE_ID}-7"] == ["ana@contoso.com"]
    assert db.kept_user_groups == [f"{SITE_ID}-7"]


async def test_a_failed_directory_role_expansion_keeps_the_stored_site_group_members(connector, api, db) -> None:  # noqa: F811
    _serve(api, db, {"LoginName": f"c:0t.c|tenant|{GUID}", "Title": "R", "PrincipalType": 4})
    api.on("GET", f"/v1.0/directoryObjects/{GUID}",
           {"@odata.type": "#microsoft.graph.directoryRole", "id": GUID})
    api.on("GET", f"/v1.0/directoryRoles/{GUID}/members", graph_error(503, "serviceNotAvailable"))

    await connector._sync_user_groups()

    assert db.user_groups[f"{SITE_ID}-7"] == ["ana@contoso.com"]
    assert db.kept_user_groups == [f"{SITE_ID}-7"]


def _serve_empty_groups(api) -> None:  # noqa: F811
    api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
    api.on("GET", "/v1.0/groups", page([]))


async def test_a_failed_first_sync_member_read_keeps_the_stored_group(connector, api, db, checkpoints) -> None:  # noqa: F811
    from test_sharepoint_online_sync_behaviour import serve_groups_delta

    serve_groups_delta(api, {None: page([{"id": "g1"}], delta_link=groups_delta_url("D1"))})
    api.on("GET", "/v1.0/groups", page([{"id": "g1", "displayName": "Eng"}]))
    api.on("GET", "/v1.0/groups/g1/members", graph_error(503, "serviceNotAvailable"))
    db.user_groups["g1"] = ["ana@contoso.com"]

    await connector._sync_azure_ad_groups()

    assert db.user_groups == {"g1": ["ana@contoso.com"]}
    assert db.kept_user_groups == ["g1"]


async def test_a_failed_nested_group_read_keeps_the_parent_instead_of_truncating_it(connector, api, db, checkpoints) -> None:  # noqa: F811
    from test_sharepoint_online_sync_behaviour import (
        group_member,
        serve_groups_delta,
        user_member,
    )

    serve_groups_delta(api, {None: page([{"id": "g1"}], delta_link=groups_delta_url("D1"))})
    api.on("GET", "/v1.0/groups", page([{"id": "g1", "displayName": "Eng"}]))
    api.on("GET", "/v1.0/groups/g1/members", page([user_member("u1", "ana@contoso.com"), group_member("g-nested")]))
    api.on("GET", "/v1.0/groups/g-nested/members", graph_error(503, "serviceNotAvailable"))
    db.user_groups["g1"] = ["ana@contoso.com", "bo@contoso.com"]

    await connector._sync_azure_ad_groups()

    assert db.user_groups == {"g1": ["ana@contoso.com", "bo@contoso.com"]}
    assert db.kept_user_groups == ["g1"]


@pytest.mark.parametrize("failure", ["unauthorized", "no_web_url", "token_error"])
async def test_a_site_whose_group_list_cannot_be_read_keeps_its_stored_groups(connector, api, db, monkeypatch, failure) -> None:  # noqa: F811
    from sharepoint_behaviour_fakes import site_payload

    serve_site(api)
    _serve_empty_groups(api)
    db.user_groups[f"{SITE_ID}-7"] = ["ana@contoso.com"]
    db.user_groups["other-site-9"] = ["bo@contoso.com"]
    if failure == "unauthorized":
        api.on("GET", f"{SITE_REST}/sitegroups", graph_error(401, "Unauthorized"))
    elif failure == "no_web_url":
        api.on("GET", f"/v1.0/sites/{SITE_ID}", site_payload(web_url=""))
    else:
        async def boom(*_a, **_k) -> Never:
            raise RuntimeError("token endpoint down")

        monkeypatch.setattr(connector, "_get_sharepoint_access_token", boom)

    await connector._sync_user_groups()

    assert db.kept_user_groups == [f"{SITE_ID}-7"]
    assert db.user_groups[f"{SITE_ID}-7"] == ["ana@contoso.com"]


async def test_a_failed_stored_group_lookup_makes_the_full_sync_incomplete(connector, api, db, monkeypatch) -> None:  # noqa: F811
    serve_site(api)
    _serve_empty_groups(api)
    api.on("GET", f"{SITE_REST}/sitegroups", graph_error(401, "Unauthorized"))

    async def lookup_fails(*_a, **_k) -> Never:
        raise RuntimeError("graph store down")

    monkeypatch.setattr(db, "get_all_user_groups", lookup_fails)
    assert connector.full_sync_coverage().complete

    await connector._sync_user_groups()

    assert not connector.full_sync_coverage().complete


async def test_site_groups_read_in_full_leave_the_full_sync_complete(connector, api, db) -> None:  # noqa: F811
    _serve(api, db, {"LoginName": "i:0#.f|membership|ana@contoso.com", "Title": "Ana", "PrincipalType": 1, "Email": "ana@contoso.com"})

    await connector._sync_user_groups()

    assert connector.full_sync_coverage().complete


@pytest.mark.parametrize(("body", "reason"), [
    ({"error": {"code": "-2147024891, System.UnauthorizedAccessException",
                "message": {"lang": "en-US", "value": "Access denied."}}},
     "-2147024891, System.UnauthorizedAccessException Access denied."),
    ({"odata.error": {"code": "x", "message": {"value": "y" * 500}}}, ("x " + "y" * 500)[:300]),
    (None, "Service Unavailable"),
])
def test_a_failed_site_group_read_names_the_vendor_reason_bounded(body, reason) -> None:
    import httpx

    from app.connectors.sources.microsoft.sharepoint_online.connector import (
        rest_error_reason,
    )

    response = httpx.Response(503, json=body) if body is not None else httpx.Response(503, text="<html>down</html>")
    assert rest_error_reason(response) == reason

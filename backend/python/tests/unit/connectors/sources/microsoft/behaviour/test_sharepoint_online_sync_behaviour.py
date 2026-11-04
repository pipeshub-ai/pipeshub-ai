"""SharePoint Online connector, driven over a fake Microsoft 365 tenant.

The connector, the Graph SDK client with its retry middleware, and the Azure
client-secret credential are real. Every HTTP request (Graph, the token
endpoint and SharePoint's REST API) is answered by an in-memory stub, and our
databases are in-memory fakes.
"""

import asyncio
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from azure.identity.aio import ClientSecretCredential
from ms_graph_fakes import (
    GRAPH,
    FakeCheckpointStore,
    FakeRecordsDb,
    MicrosoftCloudStub,
    bearer,
    graph_error,
    graph_retry_skipped,
    json_response,
    page,
)
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from sharepoint_behaviour_fakes import (
    DRIVE_ID,
    LIBRARY_REST,
    LIST_ID,
    MODULE,
    ROOT_ITEM_ID,
    SITE_ID,
    SITE_PAGES_REST,
    SITE_URL,
    SP_HOST,
    deleted_item,
    delta_path,
    delta_url,
    drive_path,
    drive_payload,
    file_item,
    group_grant,
    guid_etag,
    link_grant,
    m365_grant,
    ready_connector,
    rest_body,
    rest_results,
    role_assignment,
    root_item,
    route_sharepoint_http,
    serve_item,
    serve_library_permissions,
    serve_site_pages_permissions,
    site_group_grant,
    site_payload,
    site_record_group,
    sp_group,
    sp_user,
    user_grant,
)

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
    SharePointRecordType,
)
from app.connectors.sources.microsoft.sharepoint_online.sync_health import (
    SharePointSyncIncomplete,
)
from app.models.entities import (
    FileRecord,
    RecordGroup,
    RecordGroupType,
    SharePointPageRecord,
)
from app.models.permission import EntityType, PermissionType

GROUPS_DELTA = "/v1.0/groups/delta"
PAGES = f"/v1.0/sites/{SITE_ID}/pages"


@pytest.fixture
def api(cloud: MicrosoftCloudStub, monkeypatch: pytest.MonkeyPatch) -> MicrosoftCloudStub:
    route_sharepoint_http(monkeypatch, cloud)
    cloud.on("GET", drive_path(), page([drive_payload()]))
    cloud.on("GET", f"/v1.0/drives/{DRIVE_ID}/root", root_item())
    cloud.on("GET", PAGES, page([]))
    # A library and a Site Pages list whose permissions can be read and where nothing is unique.
    serve_library_permissions(cloud)
    serve_site_pages_permissions(cloud)
    return cloud


@pytest.fixture
async def connector(api: MicrosoftCloudStub, db: FakeRecordsDb, checkpoints: FakeCheckpointStore) -> SharePointConnector:
    return await ready_connector(api, db, checkpoints)


def delta_pages(api, pages_by_token: dict) -> None:
    """Serve the drive delta by its ``token`` query value (None for the first call)."""

    def handler(request: httpx.Request) -> httpx.Response:
        answer = pages_by_token[api.query(request).get("token")]
        return answer if isinstance(answer, httpx.Response) else json_response(answer)

    api.on("GET", delta_path(), handler)


def stored_link(checkpoints) -> dict:
    return checkpoints.values_for(f"{SITE_ID}/{DRIVE_ID}") or {}


async def sync_site(connector) -> None:
    await connector._sync_site_content(site_record_group(connector))


def seed_file(db, connector, item_id: str, name: str, *, etag: str = "e1", xor: str = "h1", permissions=None) -> FileRecord:
    record = FileRecord(
        id=f"rec-{item_id}", record_name=name, record_type="FILE", record_group_type=RecordGroupType.DRIVE,
        external_record_id=item_id, external_revision_id=etag, version=0, origin="CONNECTOR",
        connector_name=connector.connector_name, connector_id=connector.connector_id,
        is_file=True, extension=name.rsplit(".", 1)[-1], quick_xor_hash=xor, size_in_bytes=1,
        # Where a stored file at the top of the library hangs: under the library's root folder record.
        parent_external_record_id=f"{DRIVE_ID}:root:{ROOT_ITEM_ID}",
    )
    db.seed_record(record, permissions)
    return record


class TestClientAndTokens:
    async def test_the_sdk_and_credential_are_real_and_every_request_carries_a_token(self, connector, api) -> None:
        assert type(connector.client) is GraphServiceClient
        assert type(connector.credential) is ClientSecretCredential
        assert not isinstance(sys.modules["msgraph"], MagicMock)
        assert not isinstance(sys.modules["kiota_http"], MagicMock)

        await connector._get_all_sites()

        graph = api.graph_calls()
        assert graph and all(bearer(r).startswith("Bearer fake-graph-token-") for r in graph)
        scopes = {form.get("scope") for form in api.token_requests()}
        assert "https://graph.microsoft.com/.default" in scopes
        assert f"https://{SP_HOST}/.default" in scopes, "SharePoint REST needs its own token"

    async def test_rejected_client_secret_stops_setup_with_a_clear_error(self, api, db, checkpoints) -> None:
        api.token_failure = json_response(
            {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."}, status=401
        )
        with pytest.raises(ConnectorInitError, match=r"AADSTS7000215.*client secret is wrong"):
            await ready_connector(api, db, checkpoints)
        assert api.graph_calls() == []


class TestSiteDiscovery:
    async def test_all_pages_of_sites_are_discovered_and_personal_sites_are_left_out(self, connector, api) -> None:
        other = f"{SP_HOST},{'3' * 32},{'4' * 32}"
        mysite = f"contoso-my.sharepoint.com,{'5' * 32},{'6' * 32}"
        api.on("GET", "/v1.0/sites", [
            page([site_payload(mysite, "https://contoso-my.sharepoint.com/personal/ana", "ana")],
                 next_link=f"{GRAPH}/sites?$skiptoken=2"),
            page([site_payload(other, f"https://{SP_HOST}/sites/ops", "ops")]),
        ])

        sites = await connector._get_all_sites()

        assert sorted(s.id for s in sites) == sorted([SITE_ID, other])
        assert connector.site_cache[other].site_url == f"https://{SP_HOST}/sites/ops"

    async def test_sharepoint_embedded_containers_are_left_out(self, connector, api) -> None:
        """Graph's site search lists them, and the app can't read them (403 on a live tenant)."""
        container = f"{SP_HOST},{'7' * 32},{'8' * 32}"
        api.on("GET", "/v1.0/sites", page([site_payload(container, f"https://{SP_HOST}/contentstorage/x8FNO-xtsk", "loop")]))
        api.on("GET", f"/v1.0/sites/{container}/sites", graph_error(403, "accessDenied"))
        api.on("GET", f"/v1.0/sites/{SITE_ID}/sites", page([]))

        sites = await connector._get_all_sites()

        assert [s.id for s in sites] == [SITE_ID]
        assert connector._health.complete, "a container is not a part of SharePoint the run failed to read"


class TestDriveDelta:
    async def test_first_sync_reads_every_delta_page_and_saves_the_delta_link(self, connector, api, db, checkpoints) -> None:
        delta_pages(api, {
            None: page([root_item(), file_item("i1", "plan.pdf")], next_link=delta_url("p2")),
            "p2": page([file_item("i2", "budget.pdf")], delta_link=delta_url("d1")),
        })
        serve_item(api, "i1", [])
        serve_item(api, "i2", [])

        await sync_site(connector)

        assert {r.record_name for r in db.records.values()} == {"root", "plan.pdf", "budget.pdf"}
        assert stored_link(checkpoints) == {**stored_link(checkpoints), "deltaLink": delta_url("d1"), "nextLink": None}
        plan = db.by_name("plan.pdf")
        assert plan.signed_url is None, "a download URL is a credential for an hour; it is read when the file is opened"
        assert plan.parent_external_record_id == f"{DRIVE_ID}:root:{ROOT_ITEM_ID}"
        assert db.records[f"{DRIVE_ID}:root:{ROOT_ITEM_ID}"].is_file is False

    async def test_next_sync_starts_from_the_saved_delta_link(self, connector, api, db, checkpoints) -> None:
        delta_pages(api, {
            None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1")),
            "d1": page([file_item("i2", "new.pdf")], delta_link=delta_url("d2")),
        })
        serve_item(api, "i1", [])
        serve_item(api, "i2", [])

        await sync_site(connector)
        await sync_site(connector)

        tokens = [api.query(r).get("token") for r in api.calls("GET", delta_path())]
        assert tokens == [None, "d1"]
        assert stored_link(checkpoints)["deltaLink"] == delta_url("d2")
        assert set(db.records) == {"i1", "i2"}

    async def test_deleted_items_are_removed(self, connector, api, db, checkpoints) -> None:
        seed_file(db, connector, "gone", "old.pdf")
        delta_pages(api, {None: page([deleted_item("gone")], delta_link=delta_url("d1"))})

        await sync_site(connector)

        assert db.deleted == ["rec-gone"]
        assert "gone" not in db.records

    async def test_an_edited_file_is_updated_in_place_with_fresh_permissions(self, connector, api, db) -> None:
        seed_file(db, connector, "i1", "plan.pdf", etag="e1", xor="h1", permissions=["copied-from-folder"])
        delta_pages(api, {None: page([file_item("i1", "plan-v2.pdf", etag="e2", xor="h2")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [user_grant("u1", "ana@contoso.com", "write")])

        await sync_site(connector)

        assert [r.record_name for r in db.metadata_updates] == ["plan-v2.pdf"]
        assert [r.external_record_id for r in db.content_updates] == ["i1"]
        assert db.records["i1"].id == "rec-i1", "the stored record keeps its id"
        (record, perms), = db.permission_updates
        # The file inherits: it reaches its folder's grants through inheritance and stores none of its own,
        # so a grant later removed from the folder is gone for the file too.
        assert record.inherit_permissions is True and perms == []

    async def test_a_file_seen_again_unchanged_is_not_reindexed(self, connector, api, db) -> None:
        seed_file(db, connector, "i1", "plan.pdf", etag="e1", xor="h1")
        delta_pages(api, {None: page([file_item("i1", "plan.pdf", etag="e1", xor="h1")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])

        await sync_site(connector)

        assert db.content_updates == []
        assert db.metadata_updates == []

    async def test_a_renamed_file_is_a_metadata_update_not_a_reindex(self, connector, api, db) -> None:
        seed_file(db, connector, "i1", "plan.pdf", etag="e1", xor="h1")
        delta_pages(api, {None: page([file_item("i1", "plan-final.pdf", etag="e2", xor="h1")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])

        await sync_site(connector)

        assert [r.record_name for r in db.metadata_updates] == ["plan-final.pdf"]
        assert db.content_updates == []

    async def test_a_file_whose_stored_hash_cannot_be_read_is_reindexed(self, connector, api, db) -> None:
        seed_file(db, connector, "i1", "plan.pdf", etag="e1", xor="h1")
        delta_pages(api, {None: page([file_item("i1", "plan.pdf", etag="e1", xor="h1")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])
        db.unreadable_file_records.add(db.records["i1"].id)

        await sync_site(connector)

        assert [r.external_record_id for r in db.content_updates] == ["i1"]

    async def test_a_file_whose_file_node_is_missing_is_reindexed(self, connector, api, db, monkeypatch) -> None:
        seed_file(db, connector, "i1", "plan.pdf", etag="e1", xor="h1")
        delta_pages(api, {None: page([file_item("i1", "plan.pdf", etag="e1", xor="h1")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])

        async def no_file_node(record_id: str) -> None:
            return None

        monkeypatch.setattr(db, "get_file_record_by_id", no_file_node)

        await sync_site(connector)

        assert [r.external_record_id for r in db.content_updates] == ["i1"]

    async def test_one_item_that_cannot_be_processed_does_not_stop_the_others(self, connector, api, db, monkeypatch) -> None:
        delta_pages(api, {None: page([file_item("bad", "broken.pdf"), file_item("ok", "fine.pdf")], delta_link=delta_url("d1"))})
        serve_item(api, "ok", [])
        real_lookup = db.get_record_by_external_id

        async def lookup(connector_id: str, external_id: str) -> object:
            if external_id == "bad":
                raise RuntimeError("database unavailable for this item")
            return await real_lookup(connector_id, external_id)

        monkeypatch.setattr(db, "get_record_by_external_id", lookup)

        await sync_site(connector)

        assert set(db.records) == {"ok"}

    @graph_retry_skipped
    async def test_throttled_delta_request_is_retried_after_the_retry_after_delay(self, connector, api, db, backoff_sleeps) -> None:
        delta_pages(api, {None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1"))})
        ok_handler = api._routes[0][2]
        api.on("GET", delta_path(), [graph_error(429, "TooManyRequests", headers={"Retry-After": "7"}), ok_handler])
        serve_item(api, "i1", [])

        await sync_site(connector)

        assert 7 in backoff_sleeps
        assert "i1" in db.records


class TestPermissions:
    async def test_item_sharing_is_mapped_to_users_and_groups_and_an_org_or_anonymous_link_alone_names_no_one(
        self, connector, api, db
    ) -> None:
        serve_item(api, "i1", [
            user_grant("u1", "ana@contoso.com", "owner"),
            group_grant("g-eng", "write"),
            link_grant("organization"),
            link_grant("anonymous"),
        ])

        # Graph's permissions are read for an item with its own permissions (sync adds the link grants).
        permissions = await connector._get_item_permissions(SITE_ID, DRIVE_ID, "i1")

        db.record_permissions["i1"] = permissions
        mapped = {(p.entity_type, p.external_id, p.type) for p in db.record_permissions["i1"]}
        assert mapped == {
            (EntityType.USER, "u1", PermissionType.OWNER),
            (EntityType.GROUP, "g-eng", PermissionType.WRITE),
        }
        assert next(p.email for p in db.record_permissions["i1"] if p.external_id == "u1") == "ana@contoso.com"

    async def test_a_failed_permission_read_keeps_the_stored_access(self, connector, api, db) -> None:
        serve_library_permissions(api, unique_items=((7, ADMIN_FOLDER_ID),))
        seed_file(db, connector, "i1", "plan.pdf", etag="e1")
        db.record_permissions["i1"] = ["kept-grant"]
        delta_pages(api, {None: page([file_item("i1", "plan.pdf", etag=guid_etag(ADMIN_FOLDER_ID, 2))],
                                     delta_link=delta_url("d1"))})
        serve_item(api, "i1", graph_error(403, "accessDenied"))

        await sync_site(connector)

        assert db.record_permissions["i1"] == ["kept-grant"]


class TestDeltaCheckpoints:
    async def test_an_empty_page_with_a_next_link_does_not_end_the_sync(self, connector, api, db, checkpoints) -> None:
        delta_pages(api, {
            None: page([], next_link=delta_url("p2")),
            "p2": page([file_item("i2", "late.pdf")], delta_link=delta_url("d1")),
        })
        serve_item(api, "i2", [])

        await sync_site(connector)

        assert "i2" in db.records
        assert stored_link(checkpoints).get("deltaLink") == delta_url("d1")

    async def test_a_failed_page_keeps_the_last_checkpoint(self, connector, api, db, checkpoints) -> None:
        delta_pages(api, {
            None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1")),
            "d1": page([file_item("i2", "b.pdf")], next_link=delta_url("p2")),
            "p2": graph_error(500, "generalException"),
        })
        serve_item(api, "i1", [])
        serve_item(api, "i2", [])
        await sync_site(connector)
        assert stored_link(checkpoints)["deltaLink"] == delta_url("d1")

        await sync_site(connector)

        link = stored_link(checkpoints)
        assert link.get("deltaLink") == delta_url("d1") or link.get("nextLink") == delta_url("p2")

    async def test_a_saved_link_for_another_host_is_discarded_and_the_library_is_read_from_the_start(
        self, connector, api, db, checkpoints
    ) -> None:
        key = connector.drive_delta_sync_point._get_full_sync_point_key(
            generate_record_sync_point_key(SharePointRecordType.DOCUMENT_LIBRARY.value, SITE_ID, DRIVE_ID)
        )
        checkpoints.sync_points[key] = {"deltaLink": "https://evil.example/delta?token=x"}
        delta_pages(api, {None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])

        await sync_site(connector)

        assert all(r.url.host != "evil.example" for r in api.requests)
        assert "i1" in db.records


class TestRetries:
    async def test_a_transient_server_error_listing_libraries_is_retried(self, connector, api, db) -> None:
        api.on("GET", drive_path(), [graph_error(500, "generalException"), page([drive_payload()])])
        delta_pages(api, {None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])

        await sync_site(connector)

        assert "i1" in db.records


def user_member(user_id: str, email: str) -> dict:
    return {"@odata.type": "#microsoft.graph.user", "id": user_id, "mail": email, "displayName": email.split("@")[0]}


def group_member(group_id: str) -> dict:
    return {"@odata.type": "#microsoft.graph.group", "id": group_id, "displayName": group_id}


def groups_delta_url(token: str) -> str:
    return f"{GRAPH}/groups/delta?$deltatoken={token}"


def serve_groups_delta(api, pages_by_token: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = pages_by_token[api.query(request).get("$deltatoken")]
        return answer if isinstance(answer, httpx.Response) else json_response(answer)

    api.on("GET", GROUPS_DELTA, handler)


def group_link(checkpoints) -> dict:
    return checkpoints.values_for("organization/org-1") or {}


def serve_group(api, group_id: str, members: list, *, containers: list | None = None) -> None:
    """A group's flattened members (transitiveMembers) and the groups it is in (transitiveMemberOf)."""
    api.on("GET", f"/v1.0/groups/{group_id}/transitiveMembers", page(members))
    api.on("GET", f"/v1.0/groups/{group_id}/transitiveMemberOf", page(containers or []))


class TestAzureAdGroups:
    async def test_first_sync_saves_every_group_with_all_member_pages_and_nested_members(
        self, connector, api, db, checkpoints
    ) -> None:
        serve_groups_delta(api, {None: page([{"id": "g1"}], delta_link=groups_delta_url("D1"))})
        api.on("GET", "/v1.0/groups", page([{"id": "g1", "displayName": "Eng"}]))

        def members(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                # transitiveMembers lists a nested group and, flattened, the people in it.
                return json_response(page([group_member("g-nested"), user_member("u2", "bo@contoso.com")]))
            return json_response(page([user_member("u1", "ana@contoso.com")],
                                      next_link=f"{GRAPH}/groups/g1/transitiveMembers?$skiptoken=2"))

        api.on("GET", "/v1.0/groups/g1/transitiveMembers", members)

        await connector._sync_azure_ad_groups()

        assert db.user_groups == {"g1": ["ana@contoso.com", "bo@contoso.com"]}
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D1")

    async def test_delta_removes_deleted_groups_and_rereads_changed_ones(self, connector, api, db, checkpoints) -> None:
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D1")),
            "D1": page([
                {"id": "g-old", "@removed": {"reason": "deleted"}},
                {"id": "g1", "displayName": "Eng", "members@delta": [
                    {"@odata.type": "#microsoft.graph.user", "id": "u2", "@removed": {"reason": "deleted"}}]},
            ], delta_link=groups_delta_url("D2")),
        })
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        db.user_groups.update({"g-old": ["x@contoso.com"], "g1": ["ana@contoso.com", "bo@contoso.com"]})
        serve_group(api, "g1", [user_member("u1", "ana@contoso.com")])

        await connector._sync_azure_ad_groups()

        assert db.deleted_groups == ["g-old"]
        assert db.user_groups == {"g1": ["ana@contoso.com"]}
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D2")

    async def test_a_change_in_a_nested_group_is_read_again_on_the_groups_that_contain_it(
        self, connector, api, db, checkpoints
    ) -> None:
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D1")),
            # Graph reports Bo leaving g-leads only; Eng holds g-leads, so Eng's flattened members change too.
            "D1": page([{"id": "g-leads", "displayName": "Leads"}], delta_link=groups_delta_url("D2")),
        })
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        db.user_groups.update({"g-eng": ["ana@contoso.com", "bo@contoso.com"], "g-leads": ["bo@contoso.com"]})
        serve_group(api, "g-leads", [], containers=[{"@odata.type": "#microsoft.graph.group", "id": "g-eng",
                                                     "displayName": "Eng"}])
        serve_group(api, "g-eng", [user_member("u1", "ana@contoso.com")])

        await connector._sync_azure_ad_groups()

        assert db.user_groups == {"g-eng": ["ana@contoso.com"], "g-leads": []}
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D2")

    async def test_a_group_write_the_database_refuses_is_read_again_on_the_next_run(self, connector, api, db, checkpoints) -> None:
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D1")),
            "D1": page([
                {"id": "g1", "displayName": "Eng", "members@delta": [
                    {"@odata.type": "#microsoft.graph.user", "id": "u2", "@removed": {"reason": "deleted"}}]},
            ], delta_link=groups_delta_url("D2")),
        })
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        db.user_groups["g1"] = ["ana@contoso.com", "bo@contoso.com"]
        serve_group(api, "g1", [user_member("u1", "ana@contoso.com")])
        db.fail_group_write.add("g1")

        with pytest.raises(RuntimeError, match="database unavailable"):
            await connector._sync_azure_ad_groups()

        assert db.user_groups == {"g1": ["ana@contoso.com", "bo@contoso.com"]}
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D1")

        db.fail_group_write.clear()
        await connector._sync_azure_ad_groups()

        assert db.user_groups == {"g1": ["ana@contoso.com"]}
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D2")

    async def test_a_refused_group_write_on_a_later_page_is_read_again_without_losing_a_deletion_after_it(
        self, connector, api, db, checkpoints
    ) -> None:
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D1")),
            "D1": page([], next_link=groups_delta_url("P2")),
            "P2": page([
                {"id": "g1", "displayName": "Eng", "members@delta": [
                    {"@odata.type": "#microsoft.graph.user", "id": "u2", "@removed": {"reason": "deleted"}}]},
            ], next_link=groups_delta_url("P3")),
            "P3": page([{"id": "g-old", "@removed": {"reason": "deleted"}}], delta_link=groups_delta_url("D2")),
        })
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        db.user_groups["g-old"] = ["x@contoso.com"]
        serve_group(api, "g1", [user_member("u1", "ana@contoso.com")])
        db.fail_group_write.add("g1")

        with pytest.raises(RuntimeError, match="database unavailable"):
            await connector._sync_azure_ad_groups()

        assert group_link(checkpoints)["nextLink"] == groups_delta_url("P2"), "stopped on the page with the change"

        db.fail_group_write.clear()
        await connector._sync_azure_ad_groups()

        assert db.user_groups == {"g1": ["ana@contoso.com"]}, "Bo is out, and the group deleted on the next page is gone"
        assert db.deleted_groups == ["g-old"]
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D2")

    async def test_a_failed_member_read_keeps_the_stored_members(self, connector, api, db, checkpoints) -> None:
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D1")),
            "D1": page([{"id": "g1", "displayName": "Eng"}], delta_link=groups_delta_url("D2")),
        })
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        db.user_groups["g1"] = ["ana@contoso.com"]
        api.on("GET", "/v1.0/groups/g1/transitiveMemberOf", page([]))
        api.on("GET", "/v1.0/groups/g1/transitiveMembers", graph_error(500, "generalException"))

        await connector._sync_azure_ad_groups()

        assert db.user_groups["g1"] == ["ana@contoso.com"]
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D1"), "the change is read again next run"

    async def test_an_interrupted_group_delta_resumes_from_the_saved_page(self, connector, api, db, checkpoints) -> None:
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D1")),
            "D1": page([], next_link=groups_delta_url("P2")),
            "P2": graph_error(500, "generalException"),
        })
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        with pytest.raises(ODataError):
            await connector._sync_azure_ad_groups()
        db.user_groups["g-old"] = ["x@contoso.com"]
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D9")),
            "P2": page([{"id": "g-old", "@removed": {"reason": "deleted"}}], delta_link=groups_delta_url("D2")),
        })

        await connector._sync_azure_ad_groups()

        assert db.deleted_groups == ["g-old"]

    async def test_a_full_refresh_still_applies_the_deletions_the_stored_link_reports(
        self, connector, api, db, checkpoints
    ) -> None:
        """The full read lists only groups that exist, and its new link starts after the deletion."""
        serve_groups_delta(api, {None: page([], delta_link=groups_delta_url("D1"))})
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        db.user_groups["g-old"] = ["x@contoso.com"]
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D3")),
            "D1": page([{"id": "g-old", "@removed": {"reason": "deleted"}}], delta_link=groups_delta_url("D2")),
        })
        connector._refresh_all_groups = True  # An account was disabled since the last run.

        await connector._sync_azure_ad_groups()

        assert db.deleted_groups == ["g-old"]
        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D3")

    async def test_a_full_refresh_keeps_the_stored_link_when_it_cannot_be_read(self, connector, api, db, checkpoints) -> None:
        serve_groups_delta(api, {None: page([], delta_link=groups_delta_url("D1"))})
        api.on("GET", "/v1.0/groups", page([]))
        await connector._sync_azure_ad_groups()
        serve_groups_delta(api, {
            None: page([], delta_link=groups_delta_url("D3")),
            "D1": graph_error(500, "generalException"),
        })
        connector._refresh_all_groups = True

        await connector._sync_azure_ad_groups()

        assert group_link(checkpoints)["deltaLink"] == groups_delta_url("D1"), "its deletions are read next run"


def rest_users(*entries: dict) -> dict:
    return {"d": {"results": list(entries)}}


SITE_REST = "/sites/eng/_api/web"


def serve_site(api) -> None:
    api.on("GET", "/v1.0/sites", page([]))
    api.on("GET", f"/v1.0/sites/{SITE_ID}", site_payload())
    api.on("GET", f"/v1.0/sites/{SITE_ID}/sites", page([]))


class TestSiteAccess:
    async def test_every_role_assignment_of_the_site_becomes_site_access(self, connector, api) -> None:
        connector._site_groups_read.add(SITE_ID)  # A run lists the site's groups before it builds any grant.
        serve_site(api)
        await connector._get_all_sites()
        m365_id = "0a1b2c3d-0000-4000-8000-000000000001"
        owners_claim = f"c:0o.c|federateddirectoryclaimprovider|{m365_id}_o"
        api.on("GET", f"/v1.0/groups/{m365_id}/owners", page([user_member("u-owner", "olu@contoso.com")]))
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users(
            role_assignment(sp_group(3, "Eng Owners"), "Full Control"),
            role_assignment(sp_group(4, "Eng Members"), "Edit"),
            role_assignment(sp_group(5, "Eng Visitors"), "Read"),
            role_assignment({"LoginName": owners_claim, "Title": "Eng Owners", "PrincipalType": 4}, "Full Control"),
            # People and claims granted on the site directly, not through a SharePoint group.
            role_assignment(sp_user(11, "ana@contoso.com"), "Edit"),
            role_assignment({"LoginName": EEEU_CLAIM, "Title": "Everyone except external users", "PrincipalType": 4}, "Read"),
            role_assignment(sp_group(20, "Limited Access System Group"), "Limited Access"),
        ))

        permissions = await connector._get_site_permissions(SITE_ID)

        # The owners claim reaches the group's owners; the tenant-wide claim is held by its own group.
        assert mapped(permissions) == {
            (EntityType.GROUP, f"{SITE_ID}-3", PermissionType.OWNER),
            (EntityType.GROUP, f"{SITE_ID}-4", PermissionType.WRITE),
            (EntityType.GROUP, f"{SITE_ID}-5", PermissionType.READ),
            (EntityType.USER, "u-owner", PermissionType.OWNER),
            (EntityType.USER, "11", PermissionType.WRITE),
            (EntityType.GROUP, "sharepoint-everyone-except-external", PermissionType.READ),
        }
        rest_calls = [r for r in api.requests if r.url.host == SP_HOST]
        assert rest_calls and all(bearer(r).startswith("Bearer fake-graph-token-") for r in rest_calls)

    async def test_a_failed_site_access_read_keeps_the_stored_access(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", "/v1.0/users", page([]))
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users())
        db.record_group_permissions[SITE_ID] = ["stored-grant"]
        api.on("GET", f"{SITE_REST}/roleassignments", graph_error(503, "serviceNotAvailable"))

        with pytest.raises(SharePointSyncIncomplete, match="permissions of site"):
            await connector.run_sync()

        assert db.record_group_permissions[SITE_ID] == ["stored-grant"]


class TestSiteGroups:
    async def test_site_group_members_are_read_from_sharepoint(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 7, "Title": "Designers", "Description": "Design"}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(7)/users", rest_users(
            {"Id": 11, "Title": "Ana", "Email": "ana@contoso.com", "PrincipalType": 1, "LoginName": "i:0#.f|membership|ana"}))

        await connector._sync_user_groups()

        assert db.user_groups[f"{SITE_ID}-7"] == ["ana@contoso.com"]

    async def test_a_sharing_links_group_is_saved_with_the_people_who_redeemed_the_link(self, connector, api, db) -> None:
        """A link's group is hidden in the SharePoint UI but is a site group; its members are the link's redeemers."""
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 31, "Title": "SharingLinks.0f6e.OrganizationEdit.9a1b"}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(31)/users", rest_users(
            {"Id": 12, "Title": "Raj", "Email": "raj@contoso.com", "PrincipalType": 1, "LoginName": "i:0#.f|membership|raj"}))

        await connector._sync_user_groups()

        assert db.user_groups[f"{SITE_ID}-31"] == ["raj@contoso.com"]

    async def test_a_deleted_m365_group_left_in_a_site_group_grants_nobody(self, connector, api, db) -> None:
        """SharePoint keeps the claim of a Microsoft 365 group after the group is deleted; Graph answers 404 for it."""
        gone = "c227ac08-bd29-4e04-a43d-b406d8cc916d"
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 14, "Title": "IT editors"}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(14)/users", rest_users(
            {"Id": 9, "Title": "Devs Members", "PrincipalType": 4,
             "LoginName": f"c:0o.c|federateddirectoryclaimprovider|{gone}"},
            {"Id": 11, "Title": "Ana", "Email": "ana@contoso.com", "PrincipalType": 1, "LoginName": "i:0#.f|membership|ana"}))
        api.on("GET", f"/v1.0/groups/{gone}/transitiveMembers", graph_error(404, "Request_ResourceNotFound"))

        await connector._sync_user_groups()

        assert db.user_groups[f"{SITE_ID}-14"] == ["ana@contoso.com"]
        assert connector._health.complete
        assert connector._site_group_permissions(SITE_ID, "14", PermissionType.WRITE)

    async def test_a_failed_site_group_member_read_keeps_the_stored_members(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        db.user_groups[f"{SITE_ID}-7"] = ["ana@contoso.com"]
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 7, "Title": "Designers"}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(7)/users", graph_error(503, "serviceNotAvailable"))

        await connector._sync_user_groups()

        assert db.user_groups[f"{SITE_ID}-7"] == ["ana@contoso.com"]

    async def test_a_site_whose_groups_cannot_be_listed_keeps_its_stored_group_grants(self, connector, api) -> None:
        """Which of its groups hold "Everyone" is unknown, so what they grant is unknown, not nothing."""
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", graph_error(403, "accessDenied"))
        await connector._sync_user_groups()
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users(role_assignment(sp_group(5, "Eng Visitors"), "Read")))

        assert await connector._get_site_permissions(SITE_ID) is None

    async def test_a_group_grant_read_before_the_sites_groups_is_unknown(self, connector, api) -> None:
        """A reindex reads grants without listing site groups first."""
        serve_site(api)
        await connector._get_all_sites()
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users(role_assignment(sp_group(5, "Eng Visitors"), "Read")))

        assert await connector._get_site_permissions(SITE_ID) is None


CREATED_TS = "2024-02-01T00:00:00Z"


def site_page(page_id: str, title: str, etag: str = "pe1") -> dict:
    return {"@odata.type": "#microsoft.graph.sitePage", "id": page_id, "name": f"{title}.aspx", "title": title,
            "eTag": etag, "webUrl": f"{SITE_URL}/SitePages/{title}.aspx",
            "createdDateTime": CREATED_TS, "lastModifiedDateTime": CREATED_TS}


def page_checkpoint(checkpoints) -> dict:
    return checkpoints.values_for(f"{RecordGroupType.SHAREPOINT_SITE.value}/{SITE_ID}") or {}


class TestSitePages:
    async def test_pages_are_saved_and_every_run_lists_every_page(self, connector, api, db) -> None:
        serve_site(api)
        await connector._get_all_sites()
        api.on("GET", PAGES, page([site_page("pg1", "Home")]))
        delta_pages(api, {None: page([], delta_link=delta_url("d1"))})

        await sync_site(connector)
        await sync_site(connector)

        assert db.by_name("Home - Eng").external_record_id == "pg1"
        # No filter on lastModifiedDateTime: that compared the source's clock with this server's, and
        # could not tell a deleted page from an unchanged one.
        filters = [api.query(r).get("$filter") for r in api.calls("GET", PAGES)]
        assert filters == [None, None]

    async def test_every_page_of_site_pages_is_read(self, connector, api, db) -> None:
        serve_site(api)
        await connector._get_all_sites()

        def pages(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return json_response(page([site_page("pg2", "News")]))
            return json_response(page([site_page("pg1", "Home")], next_link=f"{GRAPH}/sites/{SITE_ID}/pages?$skiptoken=2"))

        api.on("GET", PAGES, pages)
        delta_pages(api, {None: page([], delta_link=delta_url("d1"))})

        await sync_site(connector)

        assert {"pg1", "pg2"} <= set(db.records)

    async def test_a_deleted_page_is_removed_and_a_failed_listing_removes_nothing(self, connector, api, db) -> None:
        serve_site(api)
        await connector._get_all_sites()
        api.on("GET", PAGES, page([site_page("pg1", "Home"), site_page("pg2", "News")]))
        delta_pages(api, {None: page([], delta_link=delta_url("d1"))})
        await sync_site(connector)
        api.on("GET", PAGES, graph_error(503, "serviceNotAvailable"))

        await sync_site(connector)

        assert {"pg1", "pg2"} <= set(db.records), "a listing that failed says nothing about what is gone"
        api.on("GET", PAGES, page([site_page("pg1", "Home")]))

        await sync_site(connector)

        assert "pg1" in db.records and "pg2" not in db.records


class TestFullRun:
    async def test_run_sync_saves_users_the_site_its_library_and_files(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", "/v1.0/users", page([{"id": "u1", "displayName": "Ana", "mail": "ana@contoso.com", "accountEnabled": True}]))
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users())
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users(role_assignment(sp_user(11, "ana@contoso.com"), "Edit")))
        delta_pages(api, {None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [user_grant("u1", "ana@contoso.com")])

        await connector.run_sync()

        assert [u.email for u in db.app_users] == ["ana@contoso.com"]
        assert [(p.email, p.type) for p in db.record_group_permissions[SITE_ID]] == [("ana@contoso.com", PermissionType.WRITE)]
        assert db.record_groups[DRIVE_ID].group_type == RecordGroupType.DRIVE
        assert db.by_name("plan.pdf").external_record_group_id == DRIVE_ID

    async def test_incremental_sync_picks_up_new_files(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", "/v1.0/users", page([]))
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users())
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users())
        delta_pages(api, {None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])

        await connector.run_incremental_sync()

        assert "i1" in db.records


ADMIN_FOLDER_ID = "9617aacb-e380-454f-ae8e-7fd2526ed289"
SHARED_PDF_ID = "9d3f757b-2a74-4695-8ea4-60dcc5c5276c"
INHERITING_ID = "3e5a6276-a93f-4219-976e-f6fe93e9b84d"
SECRET_PAGE_ID = "156c4769-bb86-4d8f-9ae1-6b99bb1b7b44"
HOME_PAGE_ID = "be12b84f-63cd-474b-94a7-e0cfe77c2ad0"
M365_GROUP_ID = "5f0e9a1c-0000-4000-8000-0000000000aa"
ADMIN_ROLE_ID = "9908e57b-4444-4a0e-af96-e8ca83c0a0e5"
SECURITY_GROUP_ID = "32537252-0676-4c47-a372-2d9356345611"
EEEU_CLAIM = "c:0-.f|rolemanager|spo-grid-all-users/tenant-1"


def mapped(permissions) -> set:
    return {(p.entity_type, p.external_id, p.type) for p in permissions}


def serve_empty_delta(api) -> None:
    delta_pages(api, {None: page([], delta_link=delta_url("d1"))})


def unreadable_library(api) -> None:
    """A library with unique content whose permissions SharePoint REST refuses to give."""
    serve_library_permissions(api, unique=True, unique_items=((7, ADMIN_FOLDER_ID),))
    api.on("GET", LIBRARY_REST, graph_error(500, "serverError"))


async def unreadable_site_pages(connector, api) -> None:
    serve_site(api)
    await connector._get_all_sites()
    api.on("GET", SITE_PAGES_REST, graph_error(500, "serverError"))


def seed_page(db, connector, page_id: str, title: str, *, etag: str, permissions=None) -> SharePointPageRecord:
    record = SharePointPageRecord(
        id=f"rec-{page_id}", record_name=f"{title} - Eng", record_type="SHAREPOINT_PAGE",
        record_group_type=RecordGroupType.SHAREPOINT_SITE, external_record_group_id=SITE_ID,
        external_record_id=page_id, external_revision_id=etag, version=0, origin="CONNECTOR",
        connector_name=connector.connector_name, connector_id=connector.connector_id,
    )
    db.seed_record(record, permissions)
    return record


class TestUniquePermissions:
    """Graph marks no driveItem permission as inherited; SharePoint REST says which items are unique."""

    async def test_a_unique_item_stops_inheriting_and_keeps_its_full_acl_with_site_groups(self, connector, api, db) -> None:
        connector._site_groups_read.add(SITE_ID)  # A run lists the site's groups before it builds any grant.
        serve_library_permissions(api, unique_items=((7, ADMIN_FOLDER_ID), (9, SHARED_PDF_ID)))
        api.on("GET", f"{LIBRARY_REST}/items(7)/roleassignments", rest_results(
            role_assignment(sp_group(3, "IT Team Site Owners"), "Full Control"),
            role_assignment({"LoginName": f"c:0o.c|federateddirectoryclaimprovider|{M365_GROUP_ID}",
                             "Title": "Hello World Members", "PrincipalType": 4}, "Edit"),
        ))
        api.on("GET", f"{LIBRARY_REST}/items(9)/roleassignments", rest_results())
        delta_pages(api, {None: page([
            root_item(),
            file_item("i-admin", "privatekey.key", etag=guid_etag(ADMIN_FOLDER_ID)),
            file_item("i-shared", "letter.pdf", etag="e9", list_item_unique_id=SHARED_PDF_ID),
        ], delta_link=delta_url("d1"))})
        serve_item(api, "i-admin", [
            site_group_grant("3", "IT Team Site Owners", "owner"),
            m365_grant("hello-world", "Hello World Members", "write"),
        ])
        serve_item(api, "i-shared", [link_grant("organization", "edit")])

        await sync_site(connector)

        admin = db.records["i-admin"]
        assert admin.inherit_permissions is False
        assert mapped(db.record_permissions["i-admin"]) == {
            (EntityType.GROUP, f"{SITE_ID}-3", PermissionType.OWNER),
            (EntityType.GROUP, M365_GROUP_ID, PermissionType.WRITE),
        }
        assert db.records["i-shared"].inherit_permissions is False, "sharepointIds.listItemUniqueId is read too"
        assert db.records[f"{DRIVE_ID}:root:{ROOT_ITEM_ID}"].inherit_permissions is True

    async def test_an_organization_links_redeemers_come_from_its_group_in_the_role_assignments(
        self, connector, api, db
    ) -> None:
        """Graph lists no grantee on an organization link; SharePoint holds its redeemers in the link's group."""
        connector._site_groups_read.add(SITE_ID)  # A run lists the site's groups before it builds any grant.
        link_group = "SharingLinks.0f6e.OrganizationEdit.9a1b"
        serve_library_permissions(api, unique_items=((9, SHARED_PDF_ID),))
        api.on("GET", f"{LIBRARY_REST}/items(9)/roleassignments", rest_results(
            role_assignment(sp_group(3, "IT Team Site Owners"), "Full Control"),
            role_assignment(sp_group(31, link_group), "Contribute"),
        ))
        delta_pages(api, {None: page([
            file_item("i-shared", "letter.pdf", etag="e9", list_item_unique_id=SHARED_PDF_ID),
        ], delta_link=delta_url("d1"))})
        serve_item(api, "i-shared", [link_grant("organization", "edit")])

        await sync_site(connector)

        assert mapped(db.record_permissions["i-shared"]) == {
            (EntityType.GROUP, f"{SITE_ID}-3", PermissionType.OWNER),
            (EntityType.GROUP, f"{SITE_ID}-31", PermissionType.WRITE),
        }, "the link's group carries its role; the link adds no organization-wide grant"

    async def test_an_item_that_inherits_stores_no_grants_of_its_own(self, connector, api, db) -> None:
        serve_library_permissions(api, unique_items=((7, ADMIN_FOLDER_ID),))
        delta_pages(api, {None: page([file_item("i1", "plan.pdf", etag=guid_etag(INHERITING_ID))], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [site_group_grant("4", "IT Team Site Visitors", "read")])

        await sync_site(connector)

        assert db.records["i1"].inherit_permissions is True
        # Graph lists the inherited Visitors grant on the file; a copy of it would outlive its removal
        # from the folder or site, which Graph does not report on the file.
        assert db.record_permissions["i1"] == []
        assert api.calls("GET", f"/v1.0/drives/{DRIVE_ID}/items/i1/permissions") == []

    async def test_a_unique_library_gets_its_own_role_assignments_and_no_limited_access(self, connector, api, db) -> None:
        connector._site_groups_read.add(SITE_ID)  # A run lists the site's groups before it builds any grant.
        serve_library_permissions(api, unique=True, role_assignments=(
            role_assignment(sp_group(3, "IT Team Site Owners"), "Full Control"),
            role_assignment(sp_group(14, "IT editors"), "Edit", "Limited Access"),
            role_assignment(sp_user(21, "ana@contoso.com"), "Read"),
            role_assignment(sp_group(20, "Limited Access System Group For List"), "Limited Access"),
        ))
        serve_empty_delta(api)

        await sync_site(connector)

        library = db.record_groups[DRIVE_ID]
        assert library.inherit_permissions is False
        assert mapped(db.record_group_permissions[DRIVE_ID]) == {
            (EntityType.GROUP, f"{SITE_ID}-3", PermissionType.OWNER),
            (EntityType.GROUP, f"{SITE_ID}-14", PermissionType.WRITE),
            (EntityType.USER, "21", PermissionType.READ),
        }

    async def test_a_library_that_inherits_from_its_site_is_saved_as_before(self, connector, api, db) -> None:
        serve_library_permissions(api, unique=False)
        serve_empty_delta(api)

        await sync_site(connector)

        assert db.record_groups[DRIVE_ID].inherit_permissions is True
        assert db.record_group_permissions[DRIVE_ID] == []
        assert api.calls("GET", f"{LIBRARY_REST}/roleassignments") == []

    async def test_a_unique_page_gets_its_own_role_assignments(self, connector, api, db) -> None:
        connector._site_groups_read.add(SITE_ID)  # A run lists the site's groups before it builds any grant.
        serve_site(api)
        await connector._get_all_sites()
        api.on("GET", PAGES, page([site_page(SECRET_PAGE_ID, "Secret"), site_page(HOME_PAGE_ID, "Home")]))
        serve_site_pages_permissions(api, unique_items=((4, SECRET_PAGE_ID),), role_assignments_by_item={4: (
            role_assignment(sp_group(3, "Owners"), "Full Control"),
            role_assignment(sp_user(30, "bo@contoso.com"), "Read"),
        )})
        serve_empty_delta(api)

        await sync_site(connector)

        assert db.records[SECRET_PAGE_ID].inherit_permissions is False
        assert mapped(db.record_permissions[SECRET_PAGE_ID]) == {
            (EntityType.GROUP, f"{SITE_ID}-3", PermissionType.OWNER),
            (EntityType.USER, "30", PermissionType.READ),
        }
        assert db.records[HOME_PAGE_ID].inherit_permissions is True
        assert db.record_permissions[HOME_PAGE_ID] == []

    async def test_a_new_item_waits_when_the_library_permissions_cannot_be_read(
        self, connector, api, db, checkpoints
    ) -> None:
        unreadable_library(api)
        delta_pages(api, {None: page([file_item("i-new", "plan.pdf", etag=guid_etag(INHERITING_ID))],
                                     delta_link=delta_url("d1"))})
        serve_item(api, "i-new", [user_grant("u1", "ana@contoso.com", "read")])

        await sync_site(connector)

        assert "i-new" not in db.records, "an item whose access is unknown is not stored with a guess"
        assert stored_link(checkpoints).get("deltaLink") is None, "the next run reads it again"
        assert not connector._health.complete

    async def test_a_stored_item_keeps_its_access_when_the_library_permissions_cannot_be_read(
        self, connector, api, db
    ) -> None:
        unreadable_library(api)
        seed_file(db, connector, "i1", "salaries.pdf", etag="e1", xor="h1", permissions=["hr-only"])
        delta_pages(api, {None: page([file_item("i1", "salaries.pdf", etag="e1", xor="h1")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [user_grant("u2", "bo@contoso.com", "read")])

        await sync_site(connector)

        assert db.permission_updates == [], "rewriting the permissions would also rewrite the inheritance"
        assert db.record_batches == [] and db.metadata_updates == [] and db.content_updates == []
        assert db.record_permissions["i1"] == ["hr-only"]

    @pytest.mark.parametrize("stored_inherits", [False, True])
    async def test_an_edited_stored_item_keeps_its_inheritance_when_the_library_permissions_cannot_be_read(
        self, connector, api, db, checkpoints, stored_inherits
    ) -> None:
        unreadable_library(api)
        seed_file(db, connector, "i1", "salaries.pdf", etag="e1", xor="h1", permissions=["hr-only"])
        if stored_inherits:
            checkpoints.inheriting.add("records/rec-i1")
        delta_pages(api, {None: page([file_item("i1", "salaries.pdf", etag="e2", xor="h2")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [user_grant("u2", "bo@contoso.com", "read")])

        await sync_site(connector)

        assert [r.external_record_id for r in db.content_updates] == ["i1"], "the edit itself is still synced"
        assert db.records["i1"].inherit_permissions is stored_inherits
        assert db.permission_updates == []
        assert db.record_permissions["i1"] == ["hr-only"]
        assert stored_link(checkpoints).get("deltaLink") is None, "its permissions are read again next run"

    @pytest.mark.parametrize("stored_inherits", [False, True])
    async def test_a_stored_library_keeps_its_access_when_its_permissions_cannot_be_read(
        self, connector, api, db, checkpoints, stored_inherits
    ) -> None:
        unreadable_library(api)
        serve_empty_delta(api)
        db.record_groups[DRIVE_ID] = RecordGroup(
            id="lib-1", name="Documents", external_group_id=DRIVE_ID, connector_name=connector.connector_name,
            connector_id=connector.connector_id, group_type=RecordGroupType.DRIVE,
        )
        db.record_group_permissions[DRIVE_ID] = ["hr-only"]
        if stored_inherits:
            checkpoints.inheriting.add("recordGroups/lib-1")

        await sync_site(connector)

        (library, grants), = db.record_group_writes
        assert grants is None, "an empty list would remove the stored grants"
        assert library.inherit_permissions is stored_inherits
        assert db.record_group_permissions[DRIVE_ID] == ["hr-only"]

    async def test_a_new_library_does_not_inherit_when_its_permissions_cannot_be_read(self, connector, api, db) -> None:
        unreadable_library(api)
        serve_empty_delta(api)

        await sync_site(connector)

        (library, grants), = db.record_group_writes
        assert grants is None
        assert library.inherit_permissions is False

    async def test_a_unique_library_whose_role_assignments_cannot_be_read_keeps_its_stored_grants(
        self, connector, api, db
    ) -> None:
        serve_library_permissions(api, unique=True)
        api.on("GET", f"{LIBRARY_REST}/roleassignments", graph_error(500, "serverError"))
        serve_empty_delta(api)
        db.record_group_permissions[DRIVE_ID] = ["hr-only"]

        await sync_site(connector)

        (library, grants), = db.record_group_writes
        assert grants is None
        assert library.inherit_permissions is False
        assert db.record_group_permissions[DRIVE_ID] == ["hr-only"]

    async def test_an_unreadable_library_is_reported_once_per_run_not_once_per_item(
        self, connector, api, db, caplog
    ) -> None:
        unreadable_library(api)
        delta_pages(api, {None: page([file_item(f"i{n}", f"{n}.pdf") for n in range(3)], delta_link=delta_url("d1"))})
        for n in range(3):
            serve_item(api, f"i{n}", [])

        with caplog.at_level(logging.WARNING, logger="test.sharepoint"):
            await sync_site(connector)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and LIST_ID in r.getMessage()]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert len(api.calls("GET", LIBRARY_REST)) == 1

    async def test_a_new_page_waits_when_the_page_permissions_cannot_be_read(self, connector, api, db) -> None:
        await unreadable_site_pages(connector, api)
        api.on("GET", PAGES, page([site_page(SECRET_PAGE_ID, "Secret")]))
        serve_empty_delta(api)

        await sync_site(connector)

        assert SECRET_PAGE_ID not in db.records
        assert not connector._health.complete

    async def test_a_unique_page_whose_role_assignments_cannot_be_read_waits(self, connector, api, db) -> None:
        serve_site(api)
        await connector._get_all_sites()
        api.on("GET", PAGES, page([site_page(SECRET_PAGE_ID, "Secret")]))
        serve_site_pages_permissions(api, unique_items=((4, SECRET_PAGE_ID),))
        api.on("GET", f"{SITE_PAGES_REST}/items(4)/roleassignments", graph_error(500, "serverError"))
        serve_empty_delta(api)

        await sync_site(connector)

        assert SECRET_PAGE_ID not in db.records

    async def test_a_stored_page_keeps_its_access_when_the_page_permissions_cannot_be_read(
        self, connector, api, db
    ) -> None:
        await unreadable_site_pages(connector, api)
        seed_page(db, connector, SECRET_PAGE_ID, "Secret", etag="pe1", permissions=["hr-only"])
        api.on("GET", PAGES, page([site_page(SECRET_PAGE_ID, "Secret", etag="pe1")]))
        serve_empty_delta(api)

        await sync_site(connector)

        assert db.record_batches == [], "writing the page again would also write its inheritance"
        assert db.permission_updates == [] and db.content_updates == []
        assert db.record_permissions[SECRET_PAGE_ID] == ["hr-only"]

    @pytest.mark.parametrize("stored_inherits", [False, True])
    async def test_an_edited_stored_page_keeps_its_inheritance_when_the_page_permissions_cannot_be_read(
        self, connector, api, db, checkpoints, stored_inherits
    ) -> None:
        await unreadable_site_pages(connector, api)
        seed_page(db, connector, SECRET_PAGE_ID, "Secret", etag="pe1", permissions=["hr-only"])
        if stored_inherits:
            checkpoints.inheriting.add(f"records/rec-{SECRET_PAGE_ID}")
        api.on("GET", PAGES, page([site_page(SECRET_PAGE_ID, "Secret", etag="pe2")]))
        serve_empty_delta(api)

        await sync_site(connector)

        assert [r.external_record_id for r in db.content_updates] == [SECRET_PAGE_ID]
        assert db.records[SECRET_PAGE_ID].inherit_permissions is stored_inherits
        assert db.permission_updates == [] and db.record_batches == []
        assert db.record_permissions[SECRET_PAGE_ID] == ["hr-only"]
        assert db.record_permissions[SECRET_PAGE_ID] == ["hr-only"]

    @pytest.mark.parametrize("stored_inherits", [False, True])
    async def test_a_reindexed_page_keeps_its_inheritance_when_the_page_permissions_cannot_be_read(
        self, connector, api, db, checkpoints, stored_inherits
    ) -> None:
        await unreadable_site_pages(connector, api)
        stored = seed_page(db, connector, SECRET_PAGE_ID, "Secret", etag="pe1")
        if stored_inherits:
            checkpoints.inheriting.add(f"records/rec-{SECRET_PAGE_ID}")
        api.on("GET", f"{PAGES}/{SECRET_PAGE_ID}", site_page(SECRET_PAGE_ID, "Secret", etag="pe2"))

        page_record, _ = await connector._check_and_fetch_updated_page_record(stored)

        assert page_record.inherit_permissions is stored_inherits

    async def test_unique_permissions_are_read_once_per_library_per_run(self, connector, api, db) -> None:
        serve_library_permissions(api, unique_items=((7, ADMIN_FOLDER_ID),))
        delta_pages(api, {None: page([
            file_item("i1", "a.pdf", etag=guid_etag(ADMIN_FOLDER_ID)),
            file_item("i2", "b.pdf", etag=guid_etag(INHERITING_ID)),
        ], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])
        serve_item(api, "i2", [])

        await sync_site(connector)

        assert len(api.calls("GET", f"{LIBRARY_REST}/items")) == 1

    async def test_unique_items_are_read_across_pages_and_a_throttled_page_is_retried(
        self, connector, api, db, monkeypatch
    ) -> None:
        slept: list[float] = []

        async def no_sleep(delay: float, *_: object) -> None:
            slept.append(delay)

        monkeypatch.setattr(f"{MODULE}.asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": no_sleep}))
        serve_library_permissions(api)
        second_page = [
            json_response({"error": "throttled"}, status=429, headers={"Retry-After": "3"}),
            json_response(rest_results({"Id": 9, "UniqueId": SHARED_PDF_ID, "HasUniqueRoleAssignments": True})),
        ]

        def items(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken"):
                return api._render(second_page, request)
            return json_response(rest_results(
                {"Id": 7, "UniqueId": ADMIN_FOLDER_ID, "HasUniqueRoleAssignments": True},
                next_url=f"https://{SP_HOST}{LIBRARY_REST}/items?$skiptoken=Paged%3dTRUE%26p_ID%3d7",
            ))

        api.on("GET", f"{LIBRARY_REST}/items", items)
        api.on("GET", f"{LIBRARY_REST}/items(7)/roleassignments", rest_results())
        api.on("GET", f"{LIBRARY_REST}/items(9)/roleassignments", rest_results())
        delta_pages(api, {None: page([
            file_item("i1", "a.pdf", etag=guid_etag(ADMIN_FOLDER_ID)),
            file_item("i2", "b.pdf", etag=guid_etag(SHARED_PDF_ID)),
        ], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])
        serve_item(api, "i2", [])

        await sync_site(connector)

        assert db.records["i1"].inherit_permissions is False
        assert db.records["i2"].inherit_permissions is False, "the second page was read"
        assert 3 in slept


class TestSharingChanges:
    async def test_a_grant_revoked_on_an_unchanged_item_is_removed(self, connector, api, db) -> None:
        # The fake's on_new_records replaces grants, but the real one only adds them, so the
        # record must go through the replacing path for the revoked grant to go.
        seed_file(db, connector, "i1", "plan.pdf", etag="e1", xor="h1",
                  permissions=["ana-grant-revoked-at-source"])
        delta_pages(api, {None: page([file_item("i1", "plan.pdf", etag="e1", xor="h1")], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [user_grant("u2", "bo@contoso.com", "read")])

        await sync_site(connector)

        assert db.record_batches == [], "an unchanged stored item must not go through on_new_records"
        (record, permissions), = db.permission_updates
        assert record.external_record_id == "i1"
        # The file inherits, so the grant revoked above it goes with the copy it used to hold.
        assert permissions == [] and record.inherit_permissions is True
        assert db.content_updates == [] and db.metadata_updates == []

    async def test_the_delta_asks_graph_for_sharing_only_changes(self, connector, api, db) -> None:
        serve_empty_delta(api)

        await sync_site(connector)

        (request,) = api.calls("GET", delta_path())
        assert request.headers["Prefer"] == "deltashowsharingchanges"


class TestPrincipalMapping:
    async def test_an_m365_owners_grant_reaches_only_the_group_owners(self, connector, api, db) -> None:
        api.on("GET", f"/v1.0/groups/{M365_GROUP_ID}/owners", page([user_member("u-owner", "olu@contoso.com")]))
        serve_library_permissions(api, unique_items=((7, ADMIN_FOLDER_ID), (9, SHARED_PDF_ID)))
        owners = {"LoginName": f"c:0o.c|federateddirectoryclaimprovider|{M365_GROUP_ID}_o", "Title": "IT Team Site Owners",
                  "PrincipalType": 4}
        members = {"LoginName": f"c:0o.c|federateddirectoryclaimprovider|{M365_GROUP_ID}", "Title": "IT Team Site Members",
                   "PrincipalType": 4}
        api.on("GET", f"{LIBRARY_REST}/items(7)/roleassignments", rest_results(
            role_assignment(owners, "Full Control"), role_assignment(members, "Edit")))
        api.on("GET", f"{LIBRARY_REST}/items(9)/roleassignments", rest_results(role_assignment(owners, "Full Control")))
        delta_pages(api, {None: page([file_item("i1", "a.pdf", etag=guid_etag(ADMIN_FOLDER_ID)),
                                      file_item("i2", "b.pdf", etag=guid_etag(SHARED_PDF_ID))], delta_link=delta_url("d1"))})
        serve_item(api, "i1", [])
        serve_item(api, "i2", [])

        await sync_site(connector)

        assert mapped(db.record_permissions["i1"]) == {
            (EntityType.USER, "u-owner", PermissionType.OWNER),
            (EntityType.GROUP, M365_GROUP_ID, PermissionType.WRITE),
        }
        assert mapped(db.record_permissions["i2"]) == {(EntityType.USER, "u-owner", PermissionType.OWNER)}
        assert len(api.calls("GET", f"/v1.0/groups/{M365_GROUP_ID}/owners")) == 1, "owners are read once per run"

    async def test_an_owners_named_group_link_identity_keeps_all_its_members(self, connector, api, db) -> None:
        """A security group named "... Owners" in a link's grantees is the group, not an M365 group's owners."""
        api.on("GET", "/v1.0/groups/sec-1", {"id": "sec-1", "groupTypes": []})
        serve_item(api, "i1", [{"id": "p-link", "roles": ["read"], "link": {"scope": "users", "type": "view"},
                                "grantedToIdentitiesV2": [{"group": {"id": "sec-1", "displayName": "Data Owners"}}]}])

        permissions = await connector._get_item_permissions(SITE_ID, DRIVE_ID, "i1")

        assert mapped(permissions) == {(EntityType.GROUP, "sec-1", PermissionType.READ)}
        assert not api.calls("GET", "/v1.0/groups/sec-1/owners")

    async def test_a_site_group_holding_everyone_also_grants_the_claims_group(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 4, "Title": "Visitors"}, {"Id": 7, "Title": "Designers"}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(4)/users", rest_users(
            {"Id": 2, "Title": "Everyone except external users", "LoginName": EEEU_CLAIM, "PrincipalType": 4}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(7)/users", rest_users(
            {"Id": 11, "Title": "Ana", "Email": "ana@contoso.com", "PrincipalType": 1, "LoginName": "i:0#.f|membership|ana"}))
        await connector._sync_user_groups()
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users(
            role_assignment(sp_group(4, "Visitors"), "Read"), role_assignment(sp_group(7, "Designers"), "Edit")))

        permissions = await connector._get_site_permissions(SITE_ID)

        assert db.user_groups[f"{SITE_ID}-4"] == []
        # The claim's group, not the PipesHub organization: a guest or an outsider in the org is not "everyone
        # except external users" of the Microsoft tenant.
        assert mapped(permissions) == {
            (EntityType.GROUP, f"{SITE_ID}-4", PermissionType.READ),
            (EntityType.GROUP, "sharepoint-everyone-except-external", PermissionType.READ),
            (EntityType.GROUP, f"{SITE_ID}-7", PermissionType.WRITE),
        }

    async def test_a_directory_role_tenant_claim_expands_to_its_members(self, connector, api, db) -> None:
        serve_site(api)
        await connector._get_all_sites()
        api.on("GET", f"/v1.0/directoryObjects/{ADMIN_ROLE_ID}",
               {"@odata.type": "#microsoft.graph.directoryRole", "id": ADMIN_ROLE_ID, "displayName": "Global Administrator"})
        api.on("GET", f"/v1.0/directoryRoles/{ADMIN_ROLE_ID}/members", page([user_member("u-admin", "root@contoso.com")]))
        api.on("GET", f"/v1.0/directoryObjects/{SECURITY_GROUP_ID}",
               {"@odata.type": "#microsoft.graph.group", "id": SECURITY_GROUP_ID, "displayName": "Finance"})
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users(
            role_assignment({"LoginName": f"c:0t.c|tenant|{ADMIN_ROLE_ID}", "Title": "Global Administrator",
                             "PrincipalType": 4}, "Edit"),
            role_assignment({"LoginName": f"c:0t.c|tenant|{SECURITY_GROUP_ID}", "Title": "Finance", "PrincipalType": 4},
                            "Edit"),
        ))

        permissions = await connector._get_site_permissions(SITE_ID)

        assert mapped(permissions) == {
            (EntityType.USER, "u-admin", PermissionType.WRITE),
            (EntityType.GROUP, SECURITY_GROUP_ID, PermissionType.WRITE),
        }

    async def test_a_directory_role_in_a_site_group_is_saved_as_its_members(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"/v1.0/directoryObjects/{ADMIN_ROLE_ID}",
               {"@odata.type": "#microsoft.graph.directoryRole", "id": ADMIN_ROLE_ID})
        api.on("GET", f"/v1.0/directoryRoles/{ADMIN_ROLE_ID}/members", page([user_member("u-admin", "root@contoso.com")]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users({"Id": 3, "Title": "Site Owners"}))
        api.on("GET", f"{SITE_REST}/sitegroups/GetById(3)/users", rest_users(
            {"LoginName": f"c:0t.c|tenant|{ADMIN_ROLE_ID}", "Title": "Global Administrator", "PrincipalType": 4}))

        await connector._sync_user_groups()

        assert db.user_groups[f"{SITE_ID}-3"] == ["root@contoso.com"]


class TestGuests:
    async def test_users_carry_whether_the_directory_lists_them_as_guests(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users", page([
            {"id": "u1", "displayName": "Ana", "mail": "ana@contoso.com", "accountEnabled": True, "userType": "Member"},
            {"id": "u2", "displayName": "Bob", "mail": "bob@partner.com", "accountEnabled": True, "userType": "Guest"},
        ]))

        await connector._sync_users()

        assert {u.email: u.is_guest for u in db.app_users} == {"ana@contoso.com": False, "bob@partner.com": True}
        (request,) = api.calls("GET", "/v1.0/users")
        assert "userType" in api.query(request)["$select"].split(",")


class TestRunSafety:
    async def test_a_batch_the_database_refuses_keeps_the_delta_token(self, connector, api, db, checkpoints, monkeypatch) -> None:
        delta_pages(api, {None: page([file_item("i1", "plan.pdf")], delta_link=delta_url("d1"))})

        async def refuse(_records: list, **_: object) -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "on_new_records", refuse)

        with pytest.raises(RuntimeError, match="database unavailable"):
            await sync_site(connector)

        assert stored_link(checkpoints).get("deltaLink") is None, "the token never moves past what was not stored"

    async def test_a_library_missing_from_the_listing_is_removed_only_after_graph_says_it_is_gone(
        self, connector, api, db, checkpoints
    ) -> None:
        other = "b!drive-2"
        api.on("GET", drive_path(), page([drive_payload(), drive_payload(other, "Archive")]))
        serve_library_permissions(api, drive_id=other)
        delta_pages(api, {None: page([], delta_link=delta_url("d1"))})
        api.on("GET", f"{delta_path(other)}", page([], delta_link=delta_url("d2", other)))
        await sync_site(connector)
        api.on("GET", drive_path(), page([drive_payload()]))
        api.on("GET", f"/v1.0/drives/{other}", {"id": other})

        await sync_site(connector)

        assert db.deleted_groups == [] and other in db.record_groups, "still there at the source: kept"
        api.on("GET", f"/v1.0/drives/{other}", graph_error(404, "itemNotFound"))

        await sync_site(connector)

        assert other not in db.record_groups

    async def test_a_deleted_folder_takes_what_is_under_it(self, connector, api, db) -> None:
        folder = seed_file(db, connector, "f1", "Reports", etag="e1")
        folder.is_file = False
        child = seed_file(db, connector, "c1", "q1.pdf")
        child.parent_external_record_id = "f1"
        delta_pages(api, {None: page([deleted_item("f1")], delta_link=delta_url("d1"))})

        await sync_site(connector)

        assert "f1" not in db.records and "c1" not in db.records

    async def test_a_run_that_could_not_read_a_site_fails_and_tells_the_admin(self, connector, api, db) -> None:
        serve_site(api)
        api.on("GET", "/v1.0/users", page([]))
        api.on("GET", GROUPS_DELTA, page([], delta_link=groups_delta_url("D1")))
        api.on("GET", "/v1.0/groups", page([]))
        api.on("GET", f"{SITE_REST}/sitegroups", rest_users())
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users())
        api.on("GET", drive_path(), graph_error(403, "accessDenied"))

        with pytest.raises(SharePointSyncIncomplete, match="libraries of site"):
            await connector.run_sync()
        await asyncio.gather(*connector._background_tasks)

        assert [n["title"] for n in connector._notification_service.sent] == ["SharePoint sync could not read everything"]
        assert connector.full_sync_coverage().complete is False

    async def test_a_disabled_account_is_left_out_of_group_members_and_grants(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users", page([
            {"id": "u1", "displayName": "Ana", "mail": "ana@contoso.com", "accountEnabled": True, "userType": "Member"},
            {"id": "u2", "displayName": "Bo", "mail": "bo@contoso.com", "accountEnabled": False, "userType": "Member"},
        ]))
        await connector._sync_users()
        serve_groups_delta(api, {None: page([{"id": "g1"}], delta_link=groups_delta_url("D1"))})
        api.on("GET", "/v1.0/groups", page([{"id": "g1", "displayName": "Eng"}]))
        api.on("GET", "/v1.0/groups/g1/transitiveMembers", page([user_member("u1", "ana@contoso.com"),
                                                                 user_member("u2", "bo@contoso.com")]))

        await connector._sync_azure_ad_groups()

        assert db.user_groups["g1"] == ["ana@contoso.com"]
        assert db.user_groups["sharepoint-everyone-except-external"] == ["ana@contoso.com"]
        grant = await connector._sharepoint_principal_permissions(sp_user(9, "bo@contoso.com"), PermissionType.READ, SITE_ID)
        assert grant == []


class TestPageContent:
    async def test_images_and_list_web_parts_are_labelled_not_fetched(self, connector, api) -> None:
        serve_site(api)
        webpart = '{"properties": {"selectedListId": "list-9"}, "serverProcessedContent": {"searchablePlainTexts": {"listTitle": "Salaries <b>2026</b>"}}}'
        canvas = (f"<div><p>Hello</p><img src=\"/sites/eng/Shared Documents/secret.png\"/>"
                  f"<div data-sp-webpartdata='{webpart}'></div></div>")
        api.on("GET", f"{SITE_REST}/GetFileById('pg1')/ListItemAllFields", rest_body({"CanvasContent1": canvas, "Title": "Home"}))

        html = await connector._get_page_content(SITE_ID, "pg1")

        assert "Hello" in html and "[image: secret.png]" in html
        assert "[embedded list: Salaries &lt;b&gt;2026&lt;/b&gt;]" in html
        fetched = [r.url.path for r in api.requests if r.url.host == SP_HOST]
        assert not any("GetFileByServerRelativeUrl" in p or "lists(guid" in p for p in fetched)


class TestConnectionCheck:
    async def test_the_check_fails_when_the_app_cannot_read_sites(self, connector, api) -> None:
        api.on("GET", "/v1.0/sites", graph_error(403, "accessDenied"))

        assert await connector.test_connection_and_access() is False

    async def test_the_check_passes_when_graph_and_sharepoint_answer(self, connector, api) -> None:
        api.on("GET", "/v1.0/sites", page([]))
        api.on("GET", "/_api/web/roleassignments", rest_users())
        api.on("GET", f"{SITE_REST}/roleassignments", rest_users())

        assert await connector.test_connection_and_access() is True

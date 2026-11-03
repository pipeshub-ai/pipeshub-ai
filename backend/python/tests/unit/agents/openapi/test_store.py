from app.agents.openapi.models import (
    ApiToolsetCredentials,
    ApiToolsetInstance,
    AuthScheme,
    AuthType,
    CompiledOperation,
    OAuthClient,
)
from app.agents.openapi.store import ApiToolsetStore
from tests.support.in_memory_config import InMemoryConfigService


def _instance(instance_id: str, org_id: str) -> ApiToolsetInstance:
    return ApiToolsetInstance(
        id=instance_id, org_id=org_id, created_by="a", name=instance_id, spec_title="T", openapi_version="3.1.0",
        spec_hash="h", base_url="https://api.acme.test", timeout_seconds=10, created_at=1, updated_at=1,
        auth_scheme=AuthScheme(name="k", type=AuthType.API_KEY, location="header", param_name="K"),
        operations=[CompiledOperation(tool_name="t", method="GET", path="/t", input_schema={})],
    )


async def _store_with_two_orgs() -> tuple[ApiToolsetStore, InMemoryConfigService]:
    config = InMemoryConfigService()
    store = ApiToolsetStore(config)
    for instance_id, org in (("i1", "org-1"), ("i2", "org-1"), ("i3", "org-2")):
        await store.save_instance(_instance(instance_id, org))
        for owner in ("u1", "u2"):
            await store.save_credentials(instance_id, owner, ApiToolsetCredentials(api_key=f"{instance_id}-{owner}"))
        await store.save_oauth_client(instance_id, OAuthClient(client_id="app"))
    return store, config


async def test_a_deleted_org_takes_its_toolsets_and_credentials_with_it() -> None:
    store, config = await _store_with_two_orgs()
    assert await store.remove_org("org-1") == 2
    assert [i.id for i in await store.list_instances("org-1")] == []
    assert [i.id for i in await store.list_instances("org-2")] == ["i3"]
    assert not [k for k in config.values if "/i1" in k or "/i2" in k]
    assert await store.get_credentials("i3", "u1") is not None


async def test_a_deleted_user_loses_only_their_own_credentials() -> None:
    store, _config = await _store_with_two_orgs()
    await store.remove_owner("org-1", "u1")
    assert await store.get_credentials("i1", "u1") is None and await store.get_credentials("i2", "u1") is None
    assert await store.get_credentials("i1", "u2") is not None
    # Another org's toolset with the same user id is not touched.
    assert await store.get_credentials("i3", "u1") is not None
    assert [i.id for i in await store.list_instances("org-1")] == ["i1", "i2"]


async def test_an_unreadable_record_is_skipped_when_listing() -> None:
    store, config = await _store_with_two_orgs()
    config.values["/services/api-toolsets/instances/broken"] = {"orgId": "org-1", "name": "half a record"}
    assert [i.id for i in await store.list_instances("org-1")] == ["i1", "i2"]

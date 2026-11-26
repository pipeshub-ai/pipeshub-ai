import pytest

from app.agents.constants.api_toolset_constants import (
    get_credentials_path,
    get_credentials_prefix,
    get_instance_path,
    get_oauth_client_path,
)
from app.agents.mcp.models import OAuthTokens
from app.agents.openapi.models import (
    ApiToolsetCredentials,
    ApiToolsetInstance,
    AuthScheme,
    AuthType,
    CompiledOperation,
    OAuthClient,
)
from app.agents.openapi.store import ApiToolsetStore
from app.config.configuration_service import ConfigKeyNotDeletedError
from app.utils import error_class
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


async def test_a_saved_sign_in_with_a_lifetime_past_the_mcp_bound_still_loads() -> None:
    # MCP bounds expires_in when it parses a token (oauth_client.parse_tokens); the shared model does not, so an
    # OpenAPI credential that main loads still loads, and its toolset stays listed (F26e review M1).
    config = InMemoryConfigService()
    store = ApiToolsetStore(config)
    await store.save_credentials("i1", "u1", ApiToolsetCredentials(oauth=OAuthTokens(access_token="a")))
    path = get_credentials_path("i1", "u1")
    record = await config.get_config(path)
    record["oauth"]["expiresIn"] = 10**20
    await config.set_config(path, record)

    loaded = await store.get_credentials("i1", "u1")

    assert loaded is not None and loaded.oauth is not None and loaded.oauth.expires_in == 10**20


async def test_a_saved_sign_in_with_a_lifetime_under_a_second_still_expires() -> None:
    # The shared model drops a fraction toward zero but never to 0, which would read as "no expiry".
    config = InMemoryConfigService()
    store = ApiToolsetStore(config)
    await store.save_credentials("i1", "u1", ApiToolsetCredentials(oauth=OAuthTokens(access_token="a")))
    record = await config.get_config(get_credentials_path("i1", "u1"))
    record["oauth"]["expiresIn"] = 0.5
    await config.set_config(get_credentials_path("i1", "u1"), record)

    loaded = await store.get_credentials("i1", "u1")

    assert loaded is not None and loaded.oauth is not None
    assert loaded.oauth.expires_in == 1 and loaded.oauth.expires_at_epoch is not None


class KeepsSome(InMemoryConfigService):
    """Keeps the keys in ``kept`` (answers their delete False); raises ``ValueError`` for the keys in ``broken``."""

    def __init__(self) -> None:
        super().__init__()
        self.kept: set[str] = set()
        self.broken: set[str] = set()

    async def delete_config(self, key: str) -> bool:
        if key in self.broken:
            raise ValueError("a fault of ours")
        return False if key in self.kept else await super().delete_config(key)


async def _kept_store() -> tuple[ApiToolsetStore, KeepsSome]:
    config = KeepsSome()
    store = ApiToolsetStore(config)
    for instance_id in ("i1", "i2"):
        await store.save_instance(_instance(instance_id, "org-1"))
        await store.save_credentials(instance_id, "u1", ApiToolsetCredentials(api_key=f"{instance_id}-u1"))
        await store.save_oauth_client(instance_id, OAuthClient(client_id="app"))
    return store, config


async def test_each_delete_raises_when_the_store_kept_the_key() -> None:
    store, config = await _kept_store()
    config.kept = {get_credentials_path("i1", "u1"), get_oauth_client_path("i2")}
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.delete_credentials("i1", "u1")
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.delete_instance_credentials("i1")
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.delete_instance("i2")
    assert get_credentials_path("i1", "u1") in config.values


async def test_one_kept_credential_does_not_stop_the_others_going() -> None:
    store, config = await _kept_store()
    await store.save_credentials("i1", "u2", ApiToolsetCredentials(api_key="i1-u2"))
    first, *rest = await config.list_keys_in_directory(get_credentials_prefix("i1"))
    config.kept = {first}
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.delete_instance_credentials("i1")
    assert first in config.values and rest and not any(key in config.values for key in rest)


async def test_the_not_deleted_error_names_no_key_or_user() -> None:
    store, config = await _kept_store()
    config.kept = {get_credentials_path("i1", "u1")}
    with pytest.raises(ConfigKeyNotDeletedError) as raised:
        await store.delete_credentials("i1", "u1")
    assert "u1" not in str(raised.value) and "i1" not in str(raised.value)


async def test_deleting_what_is_not_there_succeeds() -> None:
    store, _ = await _kept_store()
    await store.delete_credentials("i1", "nobody")
    await store.delete_instance("missing")


async def test_a_failed_credential_delete_leaves_the_toolset_listed() -> None:
    store, config = await _kept_store()
    config.kept = {get_credentials_path("i1", "u1")}
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.delete_instance("i1")
    assert get_instance_path("i1") in config.values and get_oauth_client_path("i1") in config.values


async def test_a_failed_oauth_app_delete_leaves_the_toolset_listed() -> None:
    store, config = await _kept_store()
    config.kept = {get_oauth_client_path("i1")}
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.delete_instance("i1")
    assert get_instance_path("i1") in config.values


async def test_removing_a_user_tries_every_toolset_then_raises() -> None:
    store, config = await _kept_store()
    config.kept = {get_credentials_path("i1", "u1")}
    with pytest.raises(ConfigKeyNotDeletedError) as raised:
        await store.remove_owner("org-1", "u1")
    assert await store.get_credentials("i2", "u1") is None
    assert raised.value.__notes__ == ["1 of 2 toolsets failed"]


async def test_removing_an_org_tries_every_toolset_then_raises() -> None:
    store, config = await _kept_store()
    config.kept = {get_instance_path("i1")}
    with pytest.raises(ConfigKeyNotDeletedError):
        await store.remove_org("org-1")
    assert get_instance_path("i2") not in config.values


async def test_a_fault_of_ours_beside_a_kept_key_is_not_reported_as_an_outage() -> None:
    store, config = await _kept_store()
    config.kept = {get_credentials_path("i1", "u1")}
    config.broken = {get_credentials_path("i2", "u1")}
    with pytest.raises(ValueError, match="a fault of ours") as raised:
        await store.remove_owner("org-1", "u1")
    assert error_class.classify_error(raised.value) == "internal"

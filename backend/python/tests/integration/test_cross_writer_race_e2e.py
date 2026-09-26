import asyncio
import json
import os
import uuid
import pytest
import pytest_asyncio
from unittest.mock import MagicMock

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore
from app.config.key_value_store_factory import StoreConfig
from app.connectors.core.base.token_service.token_refresh_service import TokenRefreshService, OAuthToken
from app.connectors.core.base.token_service.oauth_service import OAuthProvider, OAuthConfig
from app.config.configuration_service import ConcurrentModificationError

@pytest_asyncio.fixture(params=["in_memory", "redis", "etcd"])
async def config_service(request):
    import logging
    logger = logging.getLogger("test_cas")
    store_type = request.param
    if store_type == "in_memory":
        store = InMemoryKeyValueStore(logger=logger)
    elif store_type == "redis":
        store = RedisDistributedKeyValueStore(serializer=lambda x: json.dumps(x).encode(), deserializer=lambda x: json.loads(x.decode()), host="localhost", port=6379, db=0, key_prefix="test_cas:")
    elif store_type == "etcd":
        store = Etcd3DistributedKeyValueStore(serializer=lambda x: json.dumps(x).encode(), deserializer=lambda x: json.loads(x.decode()), host="localhost", port=2379)
        
    cs = ConfigurationService(logger=logger, key_value_store=store)
    yield cs
    if store_type == "redis":
        keys = await store.client.keys("test_cas:*")
        if keys:
            await store.client.delete(*keys)
        await store.close()
    elif store_type == "etcd":
        await store.close()

@pytest.mark.asyncio
async def test_token_refresh_race(config_service, caplog):
    """
    Test scenario 1: Two concurrent token refreshes (TokenRefresh vs TokenRefresh).
    Both attempt to persist refreshed credentials at the same time.
    """
    connector_id = f"conn_{uuid.uuid4().hex}"
    config_key = f"/services/connectors/{connector_id}/config"
    
    # Initialize config
    await config_service.set_config(config_key, {"credentials": {"access_token": "old", "refresh_token": "old"}})
    
    service = TokenRefreshService(
        configuration_service=config_service,
        graph_provider=MagicMock(),
    )
    
    token1 = OAuthToken(access_token="new1", refresh_token="new_refresh1")
    token2 = OAuthToken(access_token="new2", refresh_token="new_refresh2")
    
    # Run two persists concurrently
    await asyncio.gather(
        service._persist_refreshed_credentials(connector_id, config_key, {}, token1),
        service._persist_refreshed_credentials(connector_id, config_key, {}, token2),
    )
    
    # One of them should have won, and the other should have retried and succeeded or recognized it as done
    # The final state should have one of the new tokens
    final_config, _ = await config_service.get_config_with_version(config_key)
    assert final_config["credentials"]["access_token"] in ["new1", "new2"]


@pytest.mark.asyncio
async def test_oauth_start_authorization_race(config_service):
    """
    Test scenario 2: Two concurrent start_authorization calls.
    (start_authorization vs start_authorization or token refresh).
    """
    connector_id = f"conn_{uuid.uuid4().hex}"
    config_key = f"/services/connectors/{connector_id}/config"
    
    await config_service.set_config(config_key, {"credentials": {"access_token": "old"}})
    
    oauth_config = OAuthConfig(
        client_id="test",
        client_secret="test",
        redirect_uri="http://redirect",
        authorize_url="http://auth",
        token_url="http://token"
    )
    
    service = OAuthProvider(
        configuration_service=config_service,
        config=oauth_config,
        connector_name=connector_id,
        credentials_path=config_key
    )
    
    # We patch generate_state so we know the state
    service.config.generate_state = lambda: uuid.uuid4().hex
    
    urls = await asyncio.gather(
        service.start_authorization(use_pkce=True),
        service.start_authorization(use_pkce=True)
    )
    
    assert len(urls) == 2
    final_config, _ = await config_service.get_config_with_version(config_key)
    assert "oauth" in final_config
    assert "state" in final_config["oauth"]


@pytest.mark.asyncio
async def test_save_connector_instance_filters_race(config_service):
    """
    Test scenario 3: Concurrent save_connector_instance_filters updates.
    Simulating the router method using the config_service directly.
    """
    connector_id = f"conn_{uuid.uuid4().hex}"
    config_key = f"/services/connectors/{connector_id}/config"
    
    await config_service.set_config(config_key, {"filters": {"values": ["old"]}})
    
    async def simulate_filter_save(new_values):
        import random
        for attempt in range(1, 6):
            config, version = await config_service.get_config_with_version(config_key)
            if not config:
                config = {}
            if "filters" not in config:
                config["filters"] = {}
                
            config["filters"]["values"] = new_values
            
            success, _ = await config_service.compare_and_set(config_key, version, config)
            if success:
                return True
            await asyncio.sleep(0.01)
        raise ConcurrentModificationError("conflict")

    # Run 5 concurrent filter saves
    results = await asyncio.gather(
        simulate_filter_save(["A"]),
        simulate_filter_save(["B"]),
        simulate_filter_save(["C"]),
        simulate_filter_save(["D"]),
        simulate_filter_save(["E"])
    )
    
    assert all(results)
    
    final_config, _ = await config_service.get_config_with_version(config_key)
    assert final_config["filters"]["values"] in [["A"], ["B"], ["C"], ["D"], ["E"]]

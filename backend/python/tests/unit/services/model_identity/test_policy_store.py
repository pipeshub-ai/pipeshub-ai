from app.config.constants.service import config_node_constants
from app.services.model_identity.policy_store import IdentityPolicyStore
from app.services.model_identity.settings import DEFAULT_GOVERNANCE


class _Config:
    def __init__(self, values) -> None:
        self.values = values
        self.reads = []
        self.fail = False

    async def get_config(self, key, default=None, use_cache=False, raise_on_error=False, **_) -> object:
        self.reads.append(key)
        self.raise_on_error = raise_on_error
        if self.fail:
            # As the real service: a store failure is a miss unless the caller asks to see it.
            if raise_on_error:
                raise RuntimeError("kv down")
            return default
        return self.values.get(key, default)


POLICY_KEY = config_node_constants.AI_GATEWAY_IDENTITY_POLICY.value
SECRETS_KEY = config_node_constants.SECRET_KEYS.value


async def test_reads_and_caches_the_policy() -> None:
    cfg = _Config({POLICY_KEY: {"includeEmail": True}})
    store = IdentityPolicyStore(cfg)
    assert (await store.get("o")).include_email is True
    await store.get("o")
    assert cfg.reads.count(POLICY_KEY) == 1


async def test_missing_policy_is_the_default() -> None:
    assert await IdentityPolicyStore(_Config({})).get() == DEFAULT_GOVERNANCE


async def test_ttl_expiry_and_invalidate_reload() -> None:
    cfg = _Config({POLICY_KEY: {}})
    store = IdentityPolicyStore(cfg, ttl_seconds=0.0)
    await store.get()
    await store.get()
    assert cfg.reads.count(POLICY_KEY) == 2
    long_lived = IdentityPolicyStore(cfg)
    await long_lived.get()
    long_lived.invalidate()
    await long_lived.get()
    assert cfg.reads.count(POLICY_KEY) == 4


async def test_outage_keeps_the_last_known_policy_and_never_raises() -> None:
    cfg = _Config({POLICY_KEY: {"includeEmail": True, "pseudonymize": True}})
    store = IdentityPolicyStore(cfg, ttl_seconds=0.0)
    first = await store.get()
    cfg.fail = True
    assert await store.get() == first


async def test_the_read_asks_to_see_a_store_failure() -> None:
    cfg = _Config({POLICY_KEY: {}})
    await IdentityPolicyStore(cfg).get()
    assert cfg.raise_on_error is True


async def test_a_failed_read_with_no_policy_cached_sends_nothing_that_identifies_a_person() -> None:
    cfg = _Config({POLICY_KEY: {"pseudonymize": True, "groups": {"enabled": False}}})
    cfg.fail = True
    policy = await IdentityPolicyStore(cfg).get()
    assert policy.pseudonymize is True
    assert policy.groups_enabled is False
    assert policy != DEFAULT_GOVERNANCE


async def test_an_outage_does_not_make_every_call_wait_on_the_store() -> None:
    cfg = _Config({POLICY_KEY: {}})
    cfg.fail = True
    store = IdentityPolicyStore(cfg)
    await store.get()
    await store.get()
    await store.get()
    assert cfg.reads.count(POLICY_KEY) == 1


async def test_pseudonym_key_prefers_the_dedicated_secret() -> None:
    cfg = _Config({SECRETS_KEY: {"identityPseudonymKey": "dedicated", "scopedJwtSecret": "scoped"}})
    assert await IdentityPolicyStore(cfg).pseudonym_key() == b"dedicated"
    cfg = _Config({SECRETS_KEY: {"scopedJwtSecret": "scoped"}})
    assert await IdentityPolicyStore(cfg).pseudonym_key() == b"scoped"
    assert await IdentityPolicyStore(_Config({})).pseudonym_key() is None
    cfg.fail = True
    assert await IdentityPolicyStore(cfg).pseudonym_key() is None


def test_a_call_without_a_config_service_keeps_the_configured_runtime() -> None:
    from app.services.model_identity import runtime as runtime_module

    runtime_module.set_identity_runtime(None)
    try:
        configured = runtime_module.get_identity_runtime(object())
        assert runtime_module.get_identity_runtime() is configured
        assert runtime_module.get_identity_runtime(object()) is not configured
    finally:
        runtime_module.set_identity_runtime(None)


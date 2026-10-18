from app.config.constants.service import config_node_constants
from app.services.model_identity.policy_store import IdentityPolicyStore
from app.services.model_identity.settings import DEFAULT_GOVERNANCE


class _Config:
    def __init__(self, values):
        self.values = values
        self.reads = []
        self.fail = False

    async def get_config(self, key, default=None, use_cache=False, **_):
        self.reads.append(key)
        if self.fail:
            raise RuntimeError("kv down")
        return self.values.get(key, default)


POLICY_KEY = config_node_constants.AI_GATEWAY_IDENTITY_POLICY.value
SECRETS_KEY = config_node_constants.SECRET_KEYS.value


async def test_reads_and_caches_the_policy():
    cfg = _Config({POLICY_KEY: {"includeEmail": True}})
    store = IdentityPolicyStore(cfg)
    assert (await store.get("o")).include_email is True
    await store.get("o")
    assert cfg.reads.count(POLICY_KEY) == 1


async def test_missing_policy_is_the_default():
    assert await IdentityPolicyStore(_Config({})).get() == DEFAULT_GOVERNANCE


async def test_ttl_expiry_and_invalidate_reload():
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


async def test_outage_keeps_the_last_known_policy_and_never_raises():
    cfg = _Config({POLICY_KEY: {"includeEmail": True}})
    store = IdentityPolicyStore(cfg, ttl_seconds=0.0)
    first = await store.get()
    cfg.fail = True
    assert await store.get() == first
    assert await IdentityPolicyStore(cfg).get() == DEFAULT_GOVERNANCE


async def test_pseudonym_key_prefers_the_dedicated_secret():
    cfg = _Config({SECRETS_KEY: {"identityPseudonymKey": "dedicated", "scopedJwtSecret": "scoped"}})
    assert await IdentityPolicyStore(cfg).pseudonym_key() == b"dedicated"
    cfg = _Config({SECRETS_KEY: {"scopedJwtSecret": "scoped"}})
    assert await IdentityPolicyStore(cfg).pseudonym_key() == b"scoped"
    assert await IdentityPolicyStore(_Config({})).pseudonym_key() is None
    cfg.fail = True
    assert await IdentityPolicyStore(cfg).pseudonym_key() is None

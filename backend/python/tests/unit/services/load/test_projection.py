"""``explain.project``: the governor's state as a load signal body, and nothing else."""
from __future__ import annotations

import json
import random
from pathlib import Path

import jsonschema
import pytest

from app.services.resource_governor import explain
from app.services.resource_governor.explain import BrakeReason
from app.services.resource_governor.probe import PressureSample

SCHEMA = json.loads((Path(explain.__file__).parent / "load_signal_v1.schema.json").read_text())


def _pool(limit: int = 4, in_use: int = 1, rule: str = "grow", **extra: object) -> dict:
    return {"ceiling": 8, "limit": limit, "inUse": in_use, "waiting": 2, "rule": rule, "ruleAgeSec": 12.5, **extra}


def _stats(*, admission: bool = True) -> dict:
    stats = {
        "worker_count": 2, "cpu_quota": 4.0, "cpu_utilisation": 0.4, "cpu_throttled_ratio": 0.0,
        "mem_pressure_raw": 0.55, "mem_limit_bytes": 8 * 1024**3,
        "downstream_feedback": {"throttles": {"qdrant": 2}, "timeouts": {}, "pool_exhaustions": {}, "unavailable": {}},
    }
    if admission:
        stats["admission"] = {"pools": {"heavy_parse": _pool(rule="mem_soft"), "light_parse": _pool(4, 4),
                                        "index_light": _pool(rule="at_ceiling")}}
    return stats


PRESSURE = PressureSample(cpu_psi_some_avg60=0.3, mem_psi_full_avg60=0.01, oom_kills_total=2, cpu_saturated=True, mem_saturated=False)


def test_projection_matches_the_schema() -> None:
    body = explain.project(_stats(), identity={"service": "indexing", "pid": 7, "roles": ["indexing"], "draining": False}, pressure=PRESSURE)
    jsonschema.validate(body, SCHEMA)
    assert body["v"] == 1 and set(body["pools"]) == {"heavy_parse", "light_parse", "index_light"}
    assert body["cpu"]["saturated"] is True and body["mem"]["oomKillsTotal"] == 2
    heavy = body["pools"]["heavy_parse"]
    assert (heavy["reason"], heavy["state"], heavy["reasonForS"], heavy["waiters"]) == ("memory_soft", "braked", 12.5, 2)
    assert body["pools"]["light_parse"]["state"] == "full"
    assert body["pools"]["index_light"]["reason"] == "none" and body["pools"]["index_light"]["state"] == "open"


def test_without_the_admission_block_pools_are_absent_not_zero() -> None:
    body = explain.project(_stats(admission=False))
    jsonschema.validate(body, SCHEMA)
    assert body["pools"] == {}
    assert body["waiting"]["oldestNotStartedS"] is None
    assert all(v is None for v in body["waiting"]["oldestNotStartedPublishMs"].values())
    assert body["cpu"]["psiSomeAvg60"] is None and body["mem"]["oomKillsTotal"] is None and body["cpu"]["saturated"] is None


def test_a_waiting_block_is_carried_when_the_governor_supplies_it() -> None:
    stats = _stats() | {"waiting": {"oldest_not_started_s": 3.0, "oldest_not_started_publish_ms": {"light": 5, "heavy_small": 6, "heavy": None}}}
    body = explain.project(stats)
    # contracts section 14 has {light, heavy}: a heavy_small input is not a field of the signal
    assert body["waiting"] == {"oldestNotStartedS": 3.0, "oldestNotStartedPublishMs": {"light": 5, "heavy": None}}


@pytest.mark.parametrize(
    ("rule", "cause", "expected"),
    [
        ("mem_hard", None, BrakeReason.MEMORY_HARD), ("mem_incident", None, BrakeReason.MEMORY_HARD),
        ("mem_soft", None, BrakeReason.MEMORY_SOFT), ("cpu", None, BrakeReason.CPU),
        ("downstream", None, BrakeReason.DOWNSTREAM), ("downstream_holdoff", None, BrakeReason.DOWNSTREAM),
        ("latency_hold", None, BrakeReason.DOWNSTREAM), ("mem_target", None, BrakeReason.MEMORY_TARGET),
        ("pressure_unknown", None, BrakeReason.FROZEN), ("at_ceiling", None, BrakeReason.NONE),
        ("cooldown", "mem_soft", BrakeReason.MEMORY_SOFT), ("cooldown", "cpu", BrakeReason.CPU),
        ("cooldown", None, BrakeReason.RAMPING), ("grow", None, BrakeReason.RAMPING),
        ("a_rule_added_later", None, BrakeReason.RAMPING),
    ],
)
def test_brake_reason_from_the_governors_rule(rule: str, cause: object, expected: BrakeReason) -> None:
    assert explain.brake_reason(rule, cause) is expected


@pytest.mark.parametrize(
    ("reason", "in_use", "limit", "state"),
    [
        (BrakeReason.MEMORY_SOFT, 0, 4, "braked"), (BrakeReason.CPU, 4, 4, "braked"), (BrakeReason.DOWNSTREAM, 0, 4, "braked"),
        (BrakeReason.MEMORY_TARGET, 4, 4, "full"), (BrakeReason.RAMPING, 1, 4, "open"),
        (BrakeReason.FROZEN, 4, 4, "full"), (BrakeReason.NONE, 0, 0, "open"),
    ],
)
def test_pool_state_table(reason: BrakeReason, in_use: int, limit: int, state: str) -> None:
    assert explain.pool_state(reason, in_use, limit) == state


def test_schema_rejects_an_unknown_key_and_a_wrong_type() -> None:
    body = explain.project(_stats())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**body, "orgId": "o-1"}, SCHEMA)
    body["pools"]["heavy_parse"]["limit"] = "many"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, SCHEMA)


def _keys(value: object, prefix: str = "") -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(f"{prefix}{k}")
            if k in ("pools", "downstream") and isinstance(v, dict):
                for row in v.values():
                    out |= _keys(row, f"{prefix}{k}.*.")
            else:
                out |= _keys(v, f"{prefix}{k}.")
    return out


def test_projection_allow_list() -> None:
    body = explain.project(_stats(), identity={"memberId": "m", "service": "indexing", "pid": 1}, pressure=PRESSURE)
    allowed = (
        {"v", "workerCount", "cpu", "mem", "pools", "waiting", "downstream", "memberId", "service", "pid"}
        | {f"cpu.{k}" for k in explain.CPU_KEYS} | {f"mem.{k}" for k in explain.MEM_KEYS}
        | {f"pools.*.{k}" for k in explain.POOL_KEYS} | {f"waiting.{k}" for k in explain.WAITING_KEYS}
        | {f"waiting.oldestNotStartedPublishMs.{k}" for k in explain.PUBLISH_MS_KEYS}
        | {f"downstream.*.{k}" for k in explain.DOWNSTREAM_KEYS}
    )
    assert _keys(body) <= allowed


ORG = "org-5f3c9a1e77"


def test_fuzzed_stats_and_identity_never_leak() -> None:
    stats = _stats()
    stats["demand"] = {ORG: {"connector": "conn-secret-1"}}
    stats["record_memory"] = {ORG: 1}
    stats["admission"]["pools"][ORG] = {"limit": 1}
    stats["admission"]["pools"]["heavy_parse"]["rule"] = ORG
    stats["admission"]["pools"]["heavy_parse"]["services"] = [ORG]
    stats["downstream_feedback"] = {
        "throttles": {ORG: 3, "Embedding Svc": 1, "ORG-ABC": 1, "qdrant": 2},
        "timeouts": {f"https://{ORG}.example": 1},
    }
    stats["waiting"] = {"oldest_not_started_s": float("nan"), "oldest_not_started_publish_ms": {"light": ORG}}
    identity = {
        "memberId": "m1", "orgId": ORG, "connector": "conn-secret-1", "recordName": "secret.pdf",
        "roles": ["indexing", 5], "restartsByRole": {"indexing": 1, ORG: "x"},
    }
    body = explain.project(stats, identity)
    text = json.dumps(body)
    for needle in (ORG, "conn-secret-1", "secret.pdf", "Embedding Svc", "ORG-ABC"):
        assert needle not in text, needle
    assert body["pools"]["heavy_parse"]["reason"] == "ramping"
    assert body["waiting"]["oldestNotStartedS"] is None
    assert set(body["downstream"]) == {"qdrant", "other"}
    assert body["downstream"]["other"]["throttles"] == 5  # the org-shaped names are only counted
    jsonschema.validate(body, SCHEMA)


# ---------------------------------------------------------------------------
# A wrong shape loses its own field only
# ---------------------------------------------------------------------------

SEED = 0x51A8_1115
ORGISH = ["org-6f1c2a", "6650f0c2a1b2c3d4e5f60718", "rec-0001", "Acme Corp", "conn/slack:42", "user@example.com"]
STAT_KEYS = ["admission", "waiting", "downstream_feedback", "worker_count", "cpu_quota", "cpu_utilisation",
             "mem_pressure_raw", "mem_limit_bytes", "cpu_throttled_ratio", "org_id", "records"]


def _walk_strings(obj: object):  # noqa: ANN202
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def _rand_value(rng: random.Random, depth: int = 0) -> object:
    kinds = ["int", "float", "str", "none", "bool", "list", "dict", "nan"]
    if depth > 2:
        kinds = kinds[:5]
    kind = rng.choice(kinds)
    if kind == "int":
        return rng.randrange(-5, 10**6)
    if kind == "float":
        return rng.uniform(-1, 1e6)
    if kind == "str":
        return rng.choice(ORGISH)
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "nan":
        return float("nan")
    if kind == "list":
        return [_rand_value(rng, depth + 1) for _ in range(rng.randrange(3))]
    keys = [*ORGISH, "pools", "heavy_parse", "throttles", "light"]
    return {rng.choice(keys): _rand_value(rng, depth + 1) for _ in range(rng.randrange(4))}


def test_hostile_stats_shapes_never_raise_and_never_leak() -> None:
    """2000 seeded shapes (a bool where a dict is expected, a list of lists, org-looking strings): no raise, no leak."""
    rng = random.Random(SEED)
    validator = jsonschema.validators.validator_for(SCHEMA)(SCHEMA)  # built once: validate() rebuilds it per call (15 ms)
    for i in range(2000):
        stats = {k: _rand_value(rng) for k in rng.sample(STAT_KEYS, rng.randrange(len(STAT_KEYS)))}
        if rng.random() < 0.5:
            stats["admission"] = {"sampleSeq": i, "pools": rng.choice([_rand_value(rng), {"heavy_parse": _rand_value(rng)}, []])}
        if rng.random() < 0.3:
            stats["downstream_feedback"] = rng.choice([[], "x", {"throttles": ["a"]}, {"throttles": {ORGISH[0]: 3}}])
        if rng.random() < 0.3:
            stats["waiting"] = {"oldest_not_started_publish_ms": rng.choice([5, "x", [1], {"light": "org-1"}])}
        body = explain.project(stats)  # must not raise
        leaked = [s for s in _walk_strings(body) if any(o in s for o in ORGISH)]
        assert not leaked, (leaked, stats)
        validator.validate(body)


@pytest.mark.parametrize(
    "broken",
    [
        {"admission": {"pools": True}},
        {"admission": {"pools": {"heavy_parse": [1]}}},
        {"downstream_feedback": False},
        {"downstream_feedback": {"throttles": 5}},
        {"waiting": {"oldest_not_started_publish_ms": 5}},
        {"waiting": 3},
    ],
)
def test_a_wrong_shape_drops_only_its_own_field(broken: dict) -> None:
    stats = {"cpu_utilisation": 0.4, "mem_pressure_raw": 0.5, "worker_count": 2, **broken}
    body = explain.project(stats)
    assert body["cpu"]["util"] == 0.4 and body["mem"]["pressureRaw"] == 0.5 and body["workerCount"] == 2


def test_identity_values_are_closed() -> None:
    body = explain.project(
        {}, {"restartsByRole": {"org-6f1c2a": 1, "indexing": 2}, "roles": ["Acme Corp"], "service": "Acme Corp", "pod": "p"}
    )
    assert "restartsByRole" not in body and "roles" not in body and "service" not in body
    ok = explain.project({}, {"restartsByRole": {"indexing": 2, "parse-small": 1}, "roles": ["indexing", "query"], "service": "indexing"})
    assert ok["restartsByRole"] == {"indexing": 2, "parse-small": 1} and ok["roles"] == ["indexing", "query"] and ok["service"] == "indexing"
    assert not [s for s in _walk_strings(body) if any(o in s for o in ORGISH)]


# ---------------------------------------------------------------------------
# The schema is the publisher's view of contracts section 14
# ---------------------------------------------------------------------------


def test_the_schema_accepts_the_v1_fields_that_have_no_producer_yet() -> None:
    body = explain.project(_stats())
    body["parse"] = {"gates": {"heavy_parse": {"capacity": 4, "running": {"small": 1}, "waiting": {"large": 2}}},
                     "engines": {"docling": {"limit": 2, "inUse": 1, "waiters": 0}}}
    body["counters"] = {"restarts": 1}
    body["budget"] = {"C": 8, "used": 3, "reserveHeld": 1, "reserveUsed": 0, "criticalWaiters": 0, "bigWaiters": 1,
                      "oldestCriticalWaitS": None, "saturated": False, "floorsOverCapacity": False,
                      "families": {"parse": {"used": 2, "waiting": 0}}}
    body["downstream"]["qdrant"].update({"calls": 40, "local": False})
    jsonschema.validate(body, SCHEMA)


def test_the_schema_has_no_heavy_small_and_still_refuses_unknown_keys() -> None:
    body = explain.project(_stats())
    body["waiting"]["oldestNotStartedPublishMs"]["heavySmall"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, SCHEMA)
    body = explain.project(_stats())
    body["budget"] = {"unknownField": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, SCHEMA)
    assert "heavySmall" not in json.dumps(explain.project(_stats()))


# ---------------------------------------------------------------------------
# The names real clients report
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("DoclingService", "docling"), ("ParsingService", "parsing"), ("ExtractionService", "extraction"),
        ("EmbeddingServer", "embedding"), ("openAI", "embedding"), ("azureOpenAI", "embedding"),
        ("default", "embedding"), ("llm_gateway", "llm_gateway"), ("qdrant", "qdrant"), ("redis", "redis"),
        ("Embedding Svc", "other"), ("org-5f3c9a1e77", "other"),
    ],
)
def test_the_names_real_clients_report_keep_their_dependency(reported: str, expected: str) -> None:
    assert explain.normalise_dependency(reported) == expected
    body = explain.project({"downstream_feedback": {"timeouts": {reported: 2}}})
    assert set(body["downstream"]) == {expected}


def test_every_embedding_provider_maps_to_embedding() -> None:
    from app.utils.aimodels import EmbeddingProvider

    assert {v.value.lower() for v in EmbeddingProvider} == explain.EMBEDDING_PROVIDER_NAMES
    for provider in EmbeddingProvider:
        assert explain.normalise_dependency(provider.value) == "embedding", provider


@pytest.mark.parametrize(
    ("source", "literal"),
    [
        ("app/services/docling/client.py", 'service_name="DoclingService"'),
        ("app/services/parsing/client.py", 'service_name="ParsingService"'),
        ("app/services/extraction/client.py", 'service_name="ExtractionService"'),
        ("app/utils/embedding_server_client.py", '_EMBEDDING_SERVER_SERVICE_NAME = "EmbeddingServer"'),
    ],
)
def test_the_alias_table_follows_the_names_the_clients_use(source: str, literal: str) -> None:
    """If a client is renamed, its dependency would silently become ``other``: this fails first."""
    text = (REPO / source).read_text()
    assert literal in text
    name = literal.split('"')[1]
    assert explain.normalise_dependency(name) != "other"


def test_downstream_comes_from_the_window_of_the_sample_the_pools_came_from() -> None:
    """stats() copies downstream_feedback and builds the admission block under separate lock holds, so a sample
    can land between them: the admission block's own window is the one that belongs with its pools."""
    stats = {
        "cpu_utilisation": 0.4, "mem_pressure_raw": 0.5, "worker_count": 2,
        "downstream_feedback": {"throttles": {"qdrant": 7}},  # the previous window
        "admission": {"sampleSeq": 9, "pools": {}, "feedback": {"window": {"seq": 9, "throttles": {"qdrant": 2}}, "totals": {}}},
    }
    body = explain.project(stats)
    assert body["downstream"]["qdrant"]["throttles"] == 2
    del stats["admission"]["feedback"]
    assert explain.project(stats)["downstream"]["qdrant"]["throttles"] == 7  # no window in the block: the old source


def test_each_identity_scalar_is_checked_against_its_own_schema_type() -> None:
    """A value of the wrong type for its field is dropped alone; the valid fields next to it stay."""
    body = explain.project({}, {"pid": "seven", "seq": True, "draining": "false", "loopLagMs": "slow", "pod": 5,
                                "memberId": "m-1", "containerStartMs": 12.5, "restartsRecent": 2})
    for dropped in ("pid", "seq", "draining", "loopLagMs", "pod"):
        assert dropped not in body, dropped
    assert body["memberId"] == "m-1" and body["containerStartMs"] == 12.5 and body["restartsRecent"] == 2
    ok = explain.project({}, {"pid": 7, "seq": 3, "draining": True, "loopLagMs": 1.5, "pod": "p"})
    assert (ok["pid"], ok["seq"], ok["draining"], ok["loopLagMs"], ok["pod"]) == (7, 3, True, 1.5, "p")
    SCHEMA_VALIDATOR = jsonschema.validators.validator_for(SCHEMA)(SCHEMA)
    SCHEMA_VALIDATOR.validate(ok)


def test_a_pool_in_cooldown_reports_the_brake_the_governor_tracked_through_it() -> None:
    row = {"rule": "cooldown", "cooldownCause": "memory", "limit": 2, "ceiling": 4, "inUse": 1, "waiting": 0}
    pools = {"heavy_parse": row}
    old = explain.project({"admission": {"pools": pools}})
    assert old["pools"]["heavy_parse"]["reason"] == "ramping"  # no tracked reason: the cause alone cannot say which
    tracked = explain.project({"admission": {"pools": {"heavy_parse": {**row, "brakeReason": "memory_soft"}}}})
    assert tracked["pools"]["heavy_parse"]["reason"] == "memory_soft"
    junk = explain.project({"admission": {"pools": {"heavy_parse": {**row, "brakeReason": "org-6f1c2a"}}}})
    assert junk["pools"]["heavy_parse"]["reason"] == "ramping"  # an unknown value is ignored
    for malformed in ([], {}, ["memory_soft"], 5, None):  # a wrong type loses its own field, never the signal
        body = explain.project({"admission": {"pools": {"heavy_parse": {**row, "brakeReason": malformed}}}})
        assert body["pools"]["heavy_parse"]["reason"] == "ramping", malformed

"""Every capacity variable, fed anything an operator might type.

Whatever the environment says, parsing never raises, the ceiling is a whole
number within [1, slot files], the floor is within [1, ceiling], and the
per-org cap is within [1, limit] or none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.sandbox.capacity import (
    ENV_CAPACITY_DIVISOR,
    ENV_CPU_FACTOR,
    ENV_HOST_CPU_MILLIS,
    ENV_HOST_MEMORY_BYTES,
    ENV_HOST_PID_LIMIT,
    ENV_MAX_PER_ORG,
    ENV_MAX_TOTAL,
    ENV_MEMORY_SHARE,
    ENV_MIN_TOTAL,
    MAX_SLOT_FILES,
    MIN_SLOT_FILES,
    CapacitySettings,
    MachineSize,
    RunSize,
    SandboxCapacity,
    compute_plan,
    slot_file_count,
)
from app.services.resource_governor.shared_slots import sandbox_state_directory

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, scope="module")
def _forget_warnings() -> Iterator[None]:
    """The warn-once sets are process-wide: leave them as they were, so other
    tests that look for a first warning still see it."""
    from app.agent_loop_lib.sandbox import capacity
    from app.services.resource_governor import shared_slots

    saved = set(capacity._warned), set(shared_slots._warned_state_ids)
    yield
    capacity._warned.clear()
    capacity._warned.update(saved[0])
    shared_slots._warned_state_ids.clear()
    shared_slots._warned_state_ids.update(saved[1])


_KEYS = [
    ENV_MAX_TOTAL, ENV_MAX_PER_ORG, ENV_MIN_TOTAL, ENV_MEMORY_SHARE, ENV_CPU_FACTOR,
    ENV_HOST_MEMORY_BYTES, ENV_HOST_CPU_MILLIS, ENV_HOST_PID_LIMIT, ENV_CAPACITY_DIVISOR,
]

_numbers = st.one_of(
    st.integers(min_value=-(10**30), max_value=10**30).map(str),
    st.integers(min_value=10**300, max_value=10**400).map(str),
    st.floats(allow_nan=True, allow_infinity=True).map(repr),
    st.sampled_from([
        "", " ", "\t\n", " 4 ", "+4", "-0", "0", "1e3", "0x10", "1_000", "nan", "inf",
        "-inf", "four", "٤", "４", "4 ", "\u0000", "9" * 5000, "0.5", "١٢",
    ]),
)
_values = st.one_of(_numbers, st.text(max_size=12))
_env = st.dictionaries(st.sampled_from(_KEYS), _values, max_size=len(_KEYS))

_MACHINES = [
    MachineSize(None, None, None, "unknown"),
    MachineSize(2**30, 1.0, 4096, "helm_env"),
    MachineSize(1024 * 2**30, 256.0, 4_194_304, "helm_env"),
    MachineSize(0, 0.0, 0, "helm_env"),
]
_RUN = RunSize(2**30, 1.0, 512)


def _assert_sane(plan, slot_files: int) -> None:
    assert isinstance(plan.ceiling, int) and isinstance(plan.floor, int)
    assert 1 <= plan.ceiling <= slot_files
    assert 1 <= plan.floor <= plan.ceiling


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(env=_env)
def test_any_environment_gives_a_sane_plan(env: dict[str, str]) -> None:
    parsed = CapacitySettings.from_env(env)
    files = slot_file_count(env)
    assert MIN_SLOT_FILES <= files <= MAX_SLOT_FILES
    assert 1 <= parsed.divisor <= 64
    assert 0.05 <= parsed.memory_share <= 0.95
    assert 0.1 <= parsed.cpu_factor <= 2.0
    assert parsed.min_total >= 1
    assert parsed.max_total is None or parsed.max_total >= 1
    assert parsed.max_per_org is None or parsed.max_per_org >= 0
    for machine in _MACHINES:
        for mode in ("compose", "helm_dind", "local", "e2b"):
            _assert_sane(compute_plan(mode, machine, _RUN, parsed, files), files)
    # A machine as large as the environment says (the helm path) is sized too.
    if parsed.host_memory_bytes is not None and parsed.host_cpu_millis is not None:
        machine = MachineSize(
            parsed.host_memory_bytes, parsed.host_cpu_millis / 1000, parsed.host_pid_limit, "helm_env",
        )
        _assert_sane(compute_plan("compose", machine, _RUN, parsed, files), files)


@settings(max_examples=200, deadline=None)
@given(env=_env, limit=st.integers(min_value=1, max_value=MAX_SLOT_FILES))
def test_the_per_org_cap_stays_within_the_limit(env: dict[str, str], limit: int) -> None:
    capacity = SandboxCapacity("e2b", CapacitySettings.from_env(env), slot_files=slot_file_count(env))
    for share in (None, 0.0, 0.01, 0.5, 1.0):
        capacity._share = share
        cap = capacity.per_org_limit(limit)
        assert cap is None or 1 <= cap <= limit


@pytest.mark.parametrize("raw", ["9" * 40, "9" * 400])
@pytest.mark.parametrize("key", [ENV_HOST_MEMORY_BYTES, ENV_HOST_CPU_MILLIS, ENV_HOST_PID_LIMIT])
def test_an_absurdly_large_host_value_is_ignored_not_an_overflow(
    key: str, raw: str, caplog, monkeypatch,
) -> None:
    from app.agent_loop_lib.sandbox import capacity

    monkeypatch.setattr(capacity, "_warned", set())
    env = {ENV_HOST_MEMORY_BYTES: str(64 * 2**30), ENV_HOST_CPU_MILLIS: "16000", key: raw}
    parsed = CapacitySettings.from_env(env)
    assert getattr(parsed, {
        ENV_HOST_MEMORY_BYTES: "host_memory_bytes", ENV_HOST_CPU_MILLIS: "host_cpu_millis",
        ENV_HOST_PID_LIMIT: "host_pid_limit",
    }[key]) is None
    assert key in caplog.text


@settings(max_examples=300, deadline=None)
@given(raw=st.text(max_size=80))
def test_any_state_id_gives_one_directory_level_in_tmp(raw: str) -> None:
    directory = sandbox_state_directory({"SANDBOX_STATE_ID": raw})
    assert directory is not None
    assert directory.name.startswith("pipeshub-sandbox")
    assert "/" not in directory.name and "\x00" not in directory.name
    assert directory.name not in (".", "..")

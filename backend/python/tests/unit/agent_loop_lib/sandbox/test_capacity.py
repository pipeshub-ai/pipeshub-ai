"""Sizing the code sandbox ceiling from the machine (pure parts)."""

from __future__ import annotations

import logging

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    CapacityPlan,
    CapacitySettings,
    MachineSize,
    RunSize,
    SandboxCapacity,
    compute_plan,
    run_size,
    slot_file_count,
)

GiB = 2**30
DOCKER_RUN = RunSize(512 * 2**20, 0.5, 256)
LOCAL_RUN = RunSize(1536 * 2**20, 1.0, None)
PID_MAX = 4_194_304


def _plan(
    machine: MachineSize, run: RunSize | None = DOCKER_RUN, mode: str = "compose", **settings: object,
) -> CapacityPlan:
    return compute_plan(mode, machine, run, CapacitySettings(**settings), 256)


def _capacity(plan: CapacityPlan, **settings: object) -> SandboxCapacity:
    capacity = SandboxCapacity("compose", CapacitySettings(**settings), slot_files=256)
    capacity.plan = plan
    return capacity


# (machine, run, settings, terms, ceiling, binding)
WORKED_EXAMPLES = [
    (MachineSize(16 * GiB, 4, PID_MAX, "docker_info"), DOCKER_RUN, {}, (8, 6, 4096), 6, "cpu"),
    (MachineSize(int(15.6 * GiB), 4, PID_MAX, "docker_info"), DOCKER_RUN, {}, (7, 6, 4096), 6, "cpu"),
    (MachineSize(64 * GiB, 16, PID_MAX, "docker_info"), DOCKER_RUN, {}, (32, 24, 4096), 24, "cpu"),
    (MachineSize(192 * GiB, 48, PID_MAX, "docker_info"), DOCKER_RUN, {}, (96, 72, 4096), 72, "cpu"),
    (MachineSize(192 * GiB, 48, 32_768, "docker_info"), DOCKER_RUN, {}, (96, 72, 32), 32, "pids"),
    (MachineSize(4 * GiB, 2, PID_MAX, "helm_env"), DOCKER_RUN, {"memory_share": 0.9}, (7, 3, 4096), 3, "cpu"),
    (MachineSize(4 * GiB, 4, PID_MAX, "helm_env"), DOCKER_RUN, {"memory_share": 0.9}, (7, 6, 4096), 6, "cpu"),
    (MachineSize(10 * GiB, 8, None, "app_cgroup"), LOCAL_RUN, {}, (1, 6, None), 2, "memory"),
]


class TestWorkedExamples:
    @pytest.mark.parametrize(("machine", "run", "settings", "terms", "ceiling", "binding"), WORKED_EXAMPLES)
    def test_row(self, machine, run, settings, terms, ceiling, binding) -> None:
        plan = _plan(machine, run, **settings)
        assert (plan.terms["memory"], plan.terms["cpu"], plan.terms["pids"]) == terms
        assert (plan.ceiling, plan.binding, plan.floor, plan.explicit) == (ceiling, binding, 2, False)

    @pytest.mark.parametrize(("machine", "run", "settings", "terms", "ceiling", "binding"), WORKED_EXAMPLES)
    def test_the_ceiling_is_the_clamped_smaller_of_the_two_terms(
        self, machine, run, settings, terms, ceiling, binding,
    ) -> None:
        cap = _capacity(_plan(machine, run, **settings), **settings)
        mem = cap.mem_term()
        expected = min(mem if mem is not None else 10**9, cap.other_term())
        assert cap.ceiling() == min(max(expected, cap.floor()), cap.slot_files)

    def test_e2b_is_fixed_at_100(self) -> None:
        plan = _plan(MachineSize(None, None, None, "fixed"), None, mode="e2b")
        assert (plan.ceiling, plan.binding, plan.explicit) == (100, "fixed", False)

    def test_e2b_takes_an_explicit_value(self) -> None:
        plan = _plan(MachineSize(None, None, None, "fixed"), None, mode="e2b", max_total=20)
        assert (plan.ceiling, plan.binding, plan.explicit) == (20, "explicit", True)


class TestExplicitAndBounds:
    @pytest.mark.parametrize("total", [3, 40])
    def test_explicit_replaces_the_terms(self, total: int) -> None:
        plan = _plan(MachineSize(16 * GiB, 4, PID_MAX, "docker_info"), max_total=total)
        cap = _capacity(plan, max_total=total)
        assert (plan.ceiling, plan.binding) == (total, "explicit")
        assert cap.mem_term() is None
        assert cap.other_term() == cap.ceiling() == total

    def test_explicit_above_a_term_is_honoured_with_a_warning(self, caplog) -> None:
        cap = SandboxCapacity("compose", CapacitySettings(max_total=40), slot_files=256)
        with caplog.at_level(logging.WARNING):
            cap._apply(MachineSize(16 * GiB, 4, PID_MAX, "docker_info"), None)
        assert cap.ceiling() == 40
        assert "SANDBOX_MAX_TOTAL=40 exceeds what this machine supports by cpu (6)" in caplog.text

    def test_a_machine_below_the_minimum_uses_the_minimum_with_a_warning(self, caplog) -> None:
        cap = SandboxCapacity("local", CapacitySettings(), slot_files=256)
        with caplog.at_level(logging.WARNING):
            cap._apply(MachineSize(10 * GiB, 8, None, "app_cgroup"), None)
        assert cap.ceiling() == 2
        assert "fewer than the minimum of 2" in caplog.text

    def test_unknown_terms_give_the_minimum(self) -> None:
        plan = _plan(MachineSize(None, None, None, "unknown"))
        cap = _capacity(plan)
        assert (plan.ceiling, plan.binding) == (2, "unknown")
        assert cap.mem_term() is None and cap.other_term() == 2

    def test_a_minimum_above_the_terms_is_held_to_them(self) -> None:
        # The CPU term is 6: a minimum of 8 would run more than the machine holds.
        plan = _plan(MachineSize(16 * GiB, 4, PID_MAX, "docker_info"), min_total=8)
        assert (plan.ceiling, plan.floor) == (6, 6)

    def test_the_floor_never_exceeds_an_explicit_ceiling(self) -> None:
        plan = _plan(MachineSize(16 * GiB, 4, PID_MAX, "docker_info"), max_total=1)
        assert (plan.ceiling, plan.floor) == (1, 1)

    def test_the_ceiling_never_exceeds_the_slot_files(self) -> None:
        plan = compute_plan(
            "compose", MachineSize(4096 * GiB, 1024, PID_MAX, "docker_info"), DOCKER_RUN,
            CapacitySettings(), 256,
        )
        assert plan.ceiling == 256


class TestDivisor:
    def test_pods_sharing_a_daemon_each_get_their_share(self) -> None:
        plan = _plan(MachineSize(64 * GiB, 16, PID_MAX, "docker_info"), divisor=3)
        assert (plan.ceiling, plan.binding) == (8, "cpu")

    def test_a_share_below_the_minimum_is_the_minimum(self) -> None:
        plan = _plan(MachineSize(64 * GiB, 16, PID_MAX, "docker_info"), divisor=64)
        assert plan.ceiling == 2

    @pytest.mark.parametrize("raw", ["abc", "0", "65", "-2"])
    def test_an_invalid_divisor_is_ignored_with_a_warning(self, raw: str, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            settings = CapacitySettings.from_env({"SANDBOX_CAPACITY_DIVISOR": raw})
        assert settings.divisor == 1
        assert "SANDBOX_CAPACITY_DIVISOR" in caplog.text
        plan = compute_plan(
            "compose", MachineSize(64 * GiB, 16, PID_MAX, "docker_info"), DOCKER_RUN, settings, 256,
        )
        assert plan.ceiling == 24


class TestSettingsFromEnv:
    def test_defaults(self) -> None:
        s = CapacitySettings.from_env({})
        assert (s.max_total, s.max_per_org, s.min_total, s.memory_share, s.cpu_factor) == (
            None, None, 2, 0.25, 0.75,
        )

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_zero_or_less_is_the_largest_cap(self, raw: str, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            s = CapacitySettings.from_env({"SANDBOX_MAX_TOTAL": raw})
        assert s.max_total == 1024
        assert "unlimited is no longer supported" in caplog.text
        assert slot_file_count({"SANDBOX_MAX_TOTAL": raw}) == 1024

    @pytest.mark.parametrize(("key", "raw", "expected"), [
        ("SANDBOX_MEMORY_SHARE", "2", 0.95),
        ("SANDBOX_MEMORY_SHARE", "abc", 0.25),
        ("SANDBOX_CPU_FACTOR", "9", 2.0),
        ("SANDBOX_CPU_FACTOR", "nan", 0.75),
    ])
    def test_out_of_range_floats_are_clamped_with_a_warning(self, key, raw, expected, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            s = CapacitySettings.from_env({key: raw})
        value = s.memory_share if key == "SANDBOX_MEMORY_SHARE" else s.cpu_factor
        assert value == expected
        assert key in caplog.text

    def test_empty_values_are_unset(self) -> None:
        env = dict.fromkeys((
            "SANDBOX_MAX_TOTAL", "SANDBOX_MAX_PER_ORG", "SANDBOX_MIN_TOTAL", "SANDBOX_MEMORY_SHARE",
            "SANDBOX_CPU_FACTOR", "SANDBOX_HOST_MEMORY_BYTES", "SANDBOX_HOST_CPU_MILLIS",
        ), "")
        assert CapacitySettings.from_env(env) == CapacitySettings()

    def test_one_host_variable_alone_is_ignored_with_a_warning(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            s = CapacitySettings.from_env({"SANDBOX_HOST_MEMORY_BYTES": str(4 * GiB)})
        assert s.host_memory_bytes is None and s.host_cpu_millis is None
        assert "set both or neither" in caplog.text


class TestSlotFileCount:
    @pytest.mark.parametrize(("raw", "files"), [
        (None, 256), ("", 256), ("300", 300), ("5000", 1024), ("0", 1024), ("-3", 1024), ("40", 256),
    ])
    def test_cases(self, raw: str | None, files: int) -> None:
        assert slot_file_count({} if raw is None else {"SANDBOX_MAX_TOTAL": raw}) == files


class TestPerOrgLimit:
    @pytest.mark.parametrize(("max_per_org", "limit", "expected"), [
        (4, 3, 3), (4, 10, 4), (0, 10, None),
    ])
    def test_an_explicit_cap(self, max_per_org: int, limit: int, expected: int | None) -> None:
        cap = SandboxCapacity("compose", CapacitySettings(max_per_org=max_per_org), slot_files=256)
        assert cap.per_org_limit(limit) == expected

    def test_without_a_share_one_org_may_use_every_slot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.edition.sandbox_org_share", lambda: None)
        cap = SandboxCapacity("compose", CapacitySettings(), slot_files=256)
        assert cap.per_org_limit(6) is None

    @pytest.mark.parametrize(("limit", "expected"), [(1, 1), (2, 2), (3, 2), (6, 3), (72, 36)])
    def test_a_share_of_the_current_limit(
        self, monkeypatch: pytest.MonkeyPatch, limit: int, expected: int,
    ) -> None:
        monkeypatch.setattr("app.edition.sandbox_org_share", lambda: 0.5)
        cap = SandboxCapacity("compose", CapacitySettings(), slot_files=256)
        assert cap.per_org_limit(limit) == expected

    @pytest.mark.parametrize(("max_per_org", "expected"), [(4, 3), (0, None)])
    def test_an_explicit_cap_wins_over_the_share(
        self, monkeypatch: pytest.MonkeyPatch, max_per_org: int, expected: int | None,
    ) -> None:
        monkeypatch.setattr("app.edition.sandbox_org_share", lambda: 0.5)
        cap = SandboxCapacity("compose", CapacitySettings(max_per_org=max_per_org), slot_files=256)
        assert cap.per_org_limit(3) == expected


class TestRunSize:
    def test_docker_runs_use_the_factory_limits(self) -> None:
        assert run_size("compose") == DOCKER_RUN

    def test_local_runs_use_the_factory_limits(self) -> None:
        assert run_size("local") == LOCAL_RUN

    def test_e2b_has_none(self) -> None:
        assert run_size("e2b") is None

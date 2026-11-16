"""Facts: cgroup readers on fixture trees, the exported value, and what is ignored."""

from __future__ import annotations

import json
import os

import pytest

from app.capacity import cgroup
from app.capacity.facts import (
    FACTS_ENV,
    MAX_FACTS_BYTES,
    CapacityFacts,
    facts_from_json,
)
from app.capacity.sources import Cgroup, Env, Static, current_member_id, resolve_facts
from tests.support.cgroup_tree import build_tree, point_readers_at

GIB = 2**30


@pytest.fixture(autouse=True)
def _affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(8)))
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.delenv("NVIDIA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(os.path, "exists", _exists_without_gpu(os.path.exists))


def _exists_without_gpu(real) -> object:
    return lambda p: False if str(p) == "/dev/nvidia0" else real(p)


TREES = {
    "v2_limits": ({"version": "v2", "cpu_max": "200000 100000", "memory_max": str(12 * GIB), "swap_max": "0"}, 2.0, 12 * GIB, "v2", 0),
    "v2_fraction": ({"version": "v2", "cpu_max": "50000 100000", "memory_max": str(3 * GIB)}, 0.5, 3 * GIB, "v2", None),
    "v2_unlimited": ({"version": "v2", "cpu_max": "max 100000", "memory_max": "max"}, None, None, "none", None),
    "v2_swap_max": ({"version": "v2", "cpu_max": "400000 100000", "memory_max": str(4 * GIB), "swap_max": "max"}, 4.0, 4 * GIB, "v2", None),
    "v1_limits": ({"version": "v1", "cpu_v1": ("600000", "100000"), "memory_max": str(24 * GIB), "memsw": str(30 * GIB)}, 6.0, 24 * GIB, "v1", 6 * GIB),
    "v1_sentinel": ({"version": "v1", "cpu_v1": ("-1", "100000"), "memory_max": str(2**63 - 4096)}, None, None, "none", None),
    "v1_no_swap": ({"version": "v1", "cpu_v1": ("100000", "100000"), "memory_max": str(2 * GIB)}, 1.0, 2 * GIB, "v1", None),
    "bad_values": ({"version": "v2", "cpu_max": "zero 100000", "memory_max": "lots"}, None, None, "none", None),
}


@pytest.mark.parametrize("name", TREES)
def test_cgroup_tree(name: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree, cpus, mem, kind, swap = TREES[name]
    build_tree(tmp_path, **tree)
    point_readers_at(monkeypatch, tmp_path)
    facts = Cgroup({}).read()
    assert facts.cpu_quota == cpus
    assert facts.mem_limit == mem
    assert facts.swap_limit == swap
    assert facts.cgroup == kind
    assert facts.mem_host == 8 * GIB, "MemTotal of the meminfo file"
    assert facts.cpu_affinity == 8


def test_meminfo_missing_falls_back_to_sysconf(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path, meminfo_kb=None)
    point_readers_at(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "sysconf", lambda name: 4096 if name == "SC_PAGE_SIZE" else 1024)
    assert cgroup.host_memory() == 4096 * 1024


def test_numa_limited_to_the_affinity_mask(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path, numa={0: "0-3", 1: "4-11"})
    point_readers_at(monkeypatch, tmp_path)
    assert cgroup.numa_nodes() == ((0, (0, 1, 2, 3)), (1, (4, 5, 6, 7)))


def test_numa_absent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path)
    point_readers_at(monkeypatch, tmp_path)
    assert Cgroup({}).read().numa == ()


def test_requests_are_read_from_the_downward_api_values(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path)
    point_readers_at(monkeypatch, tmp_path)
    facts = Cgroup({"PIPESHUB_CPU_REQUEST_MILLI": "6000", "PIPESHUB_MEMORY_REQUEST": "24Gi"}).read()
    assert facts.cpu_request == 6.0
    assert facts.mem_request == 24 * GIB


@pytest.mark.parametrize("cpu,mem", [("", ""), ("0", "0"), ("many", "lots"), ("-5", "-1Gi")])
def test_unusable_requests_are_ignored(cpu: str, mem: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path)
    point_readers_at(monkeypatch, tmp_path)
    facts = Cgroup({"PIPESHUB_CPU_REQUEST_MILLI": cpu, "PIPESHUB_MEMORY_REQUEST": mem}).read()
    assert facts.cpu_request is None and facts.mem_request is None


def test_no_gpu_without_a_device(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path)
    point_readers_at(monkeypatch, tmp_path)
    assert Cgroup({}).read().gpus == ()


def test_gpu_rows_and_a_failing_nvidia_smi(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from app.capacity import sources

    build_tree(tmp_path)
    point_readers_at(monkeypatch, tmp_path)
    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "all")
    monkeypatch.setattr(sources.shutil, "which", lambda _n: "/usr/bin/nvidia-smi")

    class Done:
        stdout = "0, 16384, 15000\n1, 16384, 10\nbad line\n"

    monkeypatch.setattr(sources.subprocess, "run", lambda *a, **k: Done())
    assert Cgroup({}).read().gpus == ((0, 16384 * 2**20, 15000 * 2**20), (1, 16384 * 2**20, 10 * 2**20))

    def boom(*_a, **_k) -> None:
        raise subprocess.TimeoutExpired("nvidia-smi", 2)

    monkeypatch.setattr(sources.subprocess, "run", boom)
    assert Cgroup({}).read().gpus == ()


def _facts(**kw) -> CapacityFacts:
    base = {"cpu_quota": 4.0, "cpu_affinity": 8, "cpu_host": 8, "mem_limit": 8 * GIB, "mem_host": 16 * GIB, "cgroup": "v2",
                "member_id": "m-1", "numa": ((0, (0, 1)),), "gpus": ((0, 1, 2),)}
    base.update(kw)
    return CapacityFacts(**base)


def test_inherit_roundtrip() -> None:
    facts = _facts(cpu_request=2.5, mem_request=GIB, swap_limit=0, local_cpu_embedding=True)
    assert facts_from_json(facts.to_json()) == facts


def test_resolve_prefers_the_exported_facts_of_this_container(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path, cpu_max="800000 100000", memory_max=str(32 * GIB))
    point_readers_at(monkeypatch, tmp_path)
    here = current_member_id()
    exported = _facts(cpu_quota=2.0, member_id=here)
    got = resolve_facts({FACTS_ENV: exported.to_json()})
    assert got.cpu_quota == 2.0, "the exported value is used, not a re-read"
    assert resolve_facts({}).cpu_quota == 8.0


def test_resolve_marks_local_embedding_on_exported_facts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path)
    point_readers_at(monkeypatch, tmp_path)
    exported = _facts(member_id=current_member_id())
    assert resolve_facts({FACTS_ENV: exported.to_json()}, local_cpu_embedding=True).local_cpu_embedding


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        json.dumps({"v": 2}),
        json.dumps({**json.loads(_facts().to_json()), "v": "1"}),
        json.dumps({**json.loads(_facts().to_json()), "cpu_quota": "4"}),
        json.dumps({**json.loads(_facts().to_json()), "cpu_quota": float("inf")}),
        json.dumps({**json.loads(_facts().to_json()), "cpu_quota": -1}),
        json.dumps({**json.loads(_facts().to_json()), "cgroup": "v3"}),
        json.dumps({**json.loads(_facts().to_json()), "numa": [[0, "x"]]}),
        json.dumps({**json.loads(_facts().to_json()), "gpus": [[0, 1]] * 100}),
        json.dumps({**json.loads(_facts().to_json()), "member_id": "x" * 1000}),
        json.dumps({**json.loads(_facts().to_json()), "padding": "x" * MAX_FACTS_BYTES}),
        "[" * 100000,
        json.dumps({**json.loads(_facts().to_json()), "cpu_quota": 10**400}),
        json.dumps({**json.loads(_facts().to_json()), "cpu_affinity": 10**400}),
        json.dumps({**json.loads(_facts().to_json()), "mem_host": 10**400}),
        json.dumps({**json.loads(_facts().to_json()), "numa": [[0, [1e309]]]}),
        json.dumps({**json.loads(_facts().to_json()), "gpus": [[0, 1, 1e309]]}),
    ],
    ids=["text", "list", "wrong_v", "v_string", "str_cpu", "inf", "negative", "bad_cgroup", "bad_numa", "many_gpus",
         "long_member", "oversized", "deep", "huge_quota", "huge_affinity", "huge_mem", "inf_numa", "inf_gpu"],
)
def test_env_facts_untrusted(payload: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path, cpu_max="300000 100000", memory_max=str(6 * GIB))
    point_readers_at(monkeypatch, tmp_path)
    assert Env({FACTS_ENV: payload}).read() is None
    assert resolve_facts({FACTS_ENV: payload}).cpu_quota == 3.0, "a bad payload falls back to the live read"


def test_env_facts_ignored_for_a_foreign_member(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path, cpu_max="300000 100000", memory_max=str(6 * GIB))
    point_readers_at(monkeypatch, tmp_path)
    foreign = _facts(cpu_quota=64.0, member_id="someone-else")
    assert Env({FACTS_ENV: foreign.to_json()}).read() is None
    assert resolve_facts({FACTS_ENV: foreign.to_json()}).cpu_quota == 3.0
    assert Env({FACTS_ENV: _facts(member_id="").to_json()}).read() is None


def test_static_source() -> None:
    facts = _facts()
    assert Static(facts).read() is facts


def test_member_id_without_a_cgroup_is_still_stable(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_tree(tmp_path)  # no memory.max: no cgroup directory to name
    point_readers_at(monkeypatch, tmp_path)
    first = current_member_id()
    assert first and first == current_member_id()


def test_member_id_matches_the_governor_probe(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.resource_governor import probe

    build_tree(tmp_path, memory_max=str(4 * GIB))
    point_readers_at(monkeypatch, tmp_path)
    monkeypatch.setattr(probe, "_CGROUP_ROOT", tmp_path / "cgroup")
    monkeypatch.setattr(probe, "_PROC_SELF_CGROUP", tmp_path / "no_self_cgroup")
    monkeypatch.setattr(probe, "_BOOT_ID", tmp_path / "boot_id")
    probe.memory_domain_id.cache_clear()
    try:
        assert probe.memory_domain_id() == cgroup.memory_domain_id()
    finally:
        probe.memory_domain_id.cache_clear()


@pytest.mark.parametrize("name", ["v2_limits", "v2_fraction", "v2_unlimited", "v1_limits", "v1_sentinel", "bad_values"])
def test_readers_agree_with_the_governor_probe(name: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two readers of the same files give the same answer, so they cannot drift apart."""
    from app.services.resource_governor import probe

    tree = TREES[name][0]
    build_tree(tmp_path, **tree)
    point_readers_at(monkeypatch, tmp_path)
    monkeypatch.setattr(probe, "_CGROUP_ROOT", tmp_path / "cgroup")
    monkeypatch.setattr(probe, "_PROC_SELF_CGROUP", tmp_path / "no_self_cgroup")
    monkeypatch.setattr(probe, "_PROC_MEMINFO", tmp_path / "meminfo")
    quota = probe._cgroup_v2_cpu_quota() or probe._cgroup_v1_cpu_quota()
    assert cgroup.cpu_quota()[0] == quota
    limit = probe._cgroup_v2_memory()[0] or probe._cgroup_v1_memory()[0]
    assert cgroup.memory_limit()[0] == limit


def test_a_payload_for_this_member_is_still_refused_when_oversized(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The size cap is its own check: the member id and every field are right, only the length is not."""
    build_tree(tmp_path, cpu_max="300000 100000", memory_max=str(6 * GIB))
    point_readers_at(monkeypatch, tmp_path)
    mine = json.loads(_facts(member_id=current_member_id()).to_json())
    assert Env({FACTS_ENV: json.dumps(mine)}).read() is not None, "the payload must be good without the padding"
    padded = json.dumps({**mine, "padding": "x" * MAX_FACTS_BYTES})
    assert Env({FACTS_ENV: padded}).read() is None
    with pytest.raises(ValueError, match="too large"):
        facts_from_json(padded)

"""One memory brake per memory domain: the domain id, the stamp, and the cap it grants."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.services.resource_governor import memory_domain, probe
from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import Pool, ResourceSnapshot
from app.utils import request_context as rc


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    probe.memory_domain_id.cache_clear()
    token = rc.set_admitted_in(None)
    yield
    rc.reset_admitted_in(token)
    probe.memory_domain_id.cache_clear()


def _fake_cgroup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boot_id: str = "boot-1") -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir(exist_ok=True)
    (cgroup / "memory.max").write_text("max")
    (tmp_path / "boot_id").write_text(boot_id + "\n")
    monkeypatch.setattr(probe, "_resolve_cgroup_path", lambda *_args: cgroup / "memory.max")
    monkeypatch.setattr(probe, "_BOOT_ID", tmp_path / "boot_id")


class _Governor:
    def ceiling(self, pool: Pool) -> int:
        return 8


def _governor(*, ceiling: int, limit: int) -> ResourceGovernor:
    snapshot = ResourceSnapshot(
        cpu_quota=4.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=8 * 1024**3, mem_working_set_bytes=1024**3, source="test",
    )

    class _FixedProbe:
        def snapshot(self) -> ResourceSnapshot:
            return snapshot

    governor = ResourceGovernor(logger=logging.getLogger("test"), env_parse=ceiling, probe=_FixedProbe())
    governor._registry.set(Pool.HEAVY_PARSE, limit)
    return governor


def test_processes_under_one_memory_cgroup_share_a_domain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_cgroup(monkeypatch, tmp_path)
    first = probe.memory_domain_id()
    probe.memory_domain_id.cache_clear()
    assert first is not None and len(first) == 32
    assert probe.memory_domain_id() == first


def test_another_host_is_another_domain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_cgroup(monkeypatch, tmp_path, "boot-1")
    first = probe.memory_domain_id()
    probe.memory_domain_id.cache_clear()
    _fake_cgroup(monkeypatch, tmp_path, "boot-2")
    assert probe.memory_domain_id() != first


def test_no_readable_cgroup_means_no_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "_resolve_cgroup_path", lambda *_args: None)
    assert probe.memory_domain_id() is None


def test_a_parse_braked_in_this_domain_is_capped_at_the_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_cgroup(monkeypatch, tmp_path)
    rc.set_admitted_in(probe.memory_domain_id())
    assert memory_domain.parse_admission_cap(_Governor(), Pool.HEAVY_PARSE) == 8


def test_a_parse_braked_elsewhere_is_braked_here(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_cgroup(monkeypatch, tmp_path)
    rc.set_admitted_in("another-domain")
    assert memory_domain.parse_admission_cap(_Governor(), Pool.HEAVY_PARSE) is None
    rc.set_admitted_in(None)
    assert memory_domain.parse_admission_cap(_Governor(), Pool.HEAVY_PARSE) is None


def test_without_a_domain_every_process_keeps_its_brake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "_resolve_cgroup_path", lambda *_args: None)
    rc.set_admitted_in("anything")
    assert memory_domain.parse_admission_cap(_Governor(), Pool.HEAVY_PARSE) is None
    memory_domain.stamp_admitted_here()
    assert rc.get_admitted_in() == "anything"


def test_braking_here_stamps_this_domain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_cgroup(monkeypatch, tmp_path)
    memory_domain.stamp_admitted_here()
    assert rc.get_admitted_in() == probe.memory_domain_id()


@pytest.mark.asyncio
async def test_docling_admits_a_parse_indexing_already_braked(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.docling.docling_service as docling_service

    governor = _governor(ceiling=4, limit=1)
    monkeypatch.setattr(docling_service, "_resource_governor", governor)
    monkeypatch.setattr(docling_service, "DOCLING_QUEUE_WAIT_WARN_SECONDS", 0.02)
    monkeypatch.setattr(docling_service, "DOCLING_GATE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(memory_domain, "memory_domain_id", lambda: "dom-1")
    assert await governor.gate(Pool.HEAVY_PARSE).acquire(cost=1)

    # Braked by no one this process shares memory with: Docling's own (full) brake applies.
    rc.set_admitted_in("elsewhere")
    assert (await docling_service._acquire_docling_gate(b"%PDF-1.4", "m1"))[0] is False

    rc.set_admitted_in("dom-1")
    admitted, cost = await docling_service._acquire_docling_gate(b"%PDF-1.4", "m2")
    assert admitted and cost >= 1
    docling_service._release_docling_gate(cost)

"""Where facts come from: the exported value, the cgroup, or a fixed value in tests."""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from app.capacity import cgroup
from app.capacity.facts import FACTS_ENV, CapacityFacts, facts_from_json
from app.capacity.knobs import parse_bytes

if TYPE_CHECKING:
    from collections.abc import Mapping

_GPU_TIMEOUT_S = 2.0
_MEBIBYTE = 1024 * 1024


class FactsSource(Protocol):
    def read(self) -> CapacityFacts | None: ...


class Static:
    """A fixed value (tests, ``python -m app.capacity plan --cpus ... --mem ...``)."""

    def __init__(self, facts: CapacityFacts) -> None:
        self._facts = facts

    def read(self) -> CapacityFacts:
        return self._facts


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    raw = (env.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _memory_request(env: Mapping[str, str]) -> int | None:
    """``PIPESHUB_MEMORY_REQUEST`` in bytes: plain bytes, or the Kubernetes Ki/Mi/Gi/Ti suffixes."""
    return parse_bytes(env.get("PIPESHUB_MEMORY_REQUEST"))


def _gpus() -> tuple[tuple[int, int, int], ...]:
    """``(index, total, used)`` per GPU. Never imports torch; empty on any failure or timeout."""
    visible = os.environ.get("NVIDIA_VISIBLE_DEVICES", "")
    if visible in ("", "void", "none") and not os.path.exists("/dev/nvidia0"):
        return ()
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return ()
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_GPU_TIMEOUT_S,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    rows: list[tuple[int, int, int]] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]) * _MEBIBYTE, int(parts[2]) * _MEBIBYTE))
        except ValueError:
            continue
    return tuple(rows)


def current_member_id() -> str:
    """This container's identity: the memory cgroup's, else a hash of the host name and boot id."""
    member = cgroup.memory_domain_id()
    if member:
        return member
    boot = cgroup.read_text(cgroup.BOOT_ID) or ""
    return hashlib.sha256(f"{socket.gethostname()}:{boot}".encode()).hexdigest()[:32]


class Cgroup:
    """Facts read from the live cgroup, ``/proc`` and sysfs."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        local_cpu_embedding: bool = False,
        probe_gpu: bool = True,
    ) -> None:
        self._env = os.environ if env is None else env
        self._local_embedding = local_cpu_embedding
        self._probe_gpu = probe_gpu

    def read(self) -> CapacityFacts:
        quota, cpu_kind = cgroup.cpu_quota()
        limit, mem_kind = cgroup.memory_limit()
        request_milli = _env_int(self._env, "PIPESHUB_CPU_REQUEST_MILLI")
        host_cpus = os.cpu_count() or 1
        affinity = cgroup.cpu_affinity() or host_cpus
        kind = cpu_kind if cpu_kind != "none" else mem_kind
        member = current_member_id()
        return CapacityFacts(
            cpu_quota=quota,
            cpu_affinity=affinity,
            cpu_host=host_cpus,
            cpu_request=None if request_milli is None else request_milli / 1000.0,
            mem_limit=limit,
            mem_host=cgroup.host_memory() or 0,
            mem_request=_memory_request(self._env),
            swap_limit=cgroup.swap_limit(limit),
            cgroup=kind,
            numa=cgroup.numa_nodes(),
            gpus=_gpus() if self._probe_gpu else (),
            member_id=member,
            local_cpu_embedding=self._local_embedding,
        )


class Env:
    """Facts exported once per container start in ``PIPESHUB_CAPACITY_FACTS``.

    The payload is size-capped and schema-checked. One that names a different container
    (``member_id``) than the one this process runs in is ignored, so a value inherited into
    another container or copied into a ``.env`` file never sizes it.
    """

    def __init__(self, env: Mapping[str, str] | None = None, *, member_id: str | None = None) -> None:
        self._env = os.environ if env is None else env
        self._member_id = member_id

    def read(self) -> CapacityFacts | None:
        raw = self._env.get(FACTS_ENV)
        if not raw:
            return None
        try:
            facts = facts_from_json(raw)
        except ValueError:
            return None
        expected = self._member_id if self._member_id is not None else current_member_id()
        if not facts.member_id or facts.member_id != expected:
            return None
        return facts


def resolve_facts(
    env: Mapping[str, str] | None = None, *, local_cpu_embedding: bool = False, probe_gpu: bool = True
) -> CapacityFacts:
    """The exported facts when valid for this container, else a live read."""
    source = os.environ if env is None else env
    live = Cgroup(source, local_cpu_embedding=local_cpu_embedding, probe_gpu=probe_gpu)
    exported = source.get(FACTS_ENV)
    if exported:
        from_env = Env(source, member_id=current_member_id()).read()
        if from_env is not None:
            if local_cpu_embedding and not from_env.local_cpu_embedding:
                return replace(from_env, local_cpu_embedding=True)
            return from_env
    return live.read()

"""``SandboxBroker`` on a Docker daemon: hardened runc, or gVisor (``runsc``) when the daemon has it.

Each sandbox is one long-lived container with a read-only root, no
capabilities, a non-root user, pid/memory/CPU limits and no network unless
the spec's egress says otherwise. Writable directories are broker-owned
volumes: a size-capped tmpfs volume per scratch mount, and for persistent
mounts a subpath of the broker's data volume (Engine >= 26) or, on older
engines, a scratch volume the broker copies the directory into and back out
of. Nothing is published and nothing joins a shared bridge: ``connect`` runs
a relay through ``docker exec``.

All blocking docker-py calls run on the shared ``DockerClientProvider``
executor; exec streams use the asyncio transport in ``docker_exec``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import posixpath
import signal as signals
import tarfile
import tempfile
import time
from dataclasses import dataclass, field, replace
from typing import IO, TYPE_CHECKING, Any, cast

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    build_firewall_rules_script,
    new_firewall_token,
)
from app.core.org_context import org_of, spawn_detached
from app.harness.sandbox.docker_egress import EgressLabels, hosts_for
from app.harness.sandbox.docker_exec import DockerEndpoint, demux, start_exec
from app.harness.sandbox.docker_policy import (
    FIREWALL_HELPER_ARGV0,
    FIREWALL_HELPER_CAP,
    LABEL_NAMESPACE,
    SANDBOX_USER,
    VOLUME_PREFIX,
    HardeningPolicy,
    normalize_image,
    owns_volume,
    runsc_oci_seccomp,
    unsafe_runsc_args,
)
from app.harness.sandbox.image_binaries import TMPFS, DockerImageBinaryReader
from harness_core.engines.binary import ImageBinaryVerifier, vouched_binaries
from harness_core.failpoints import afailpoint
from harness_core.logs import HarnessLog
from harness_core.sandbox.capacity import SandboxCap
from harness_core.sandbox.egress.admission import admit_egress
from harness_core.sandbox.errors import (
    BrokerUnavailable,
    ImageNotAllowed,
    InvalidSandboxRequest,
    InvalidSandboxSpec,
    IsolationUnavailable,
    PolicyUnenforceable,
    SandboxConnectError,
    SandboxError,
    SandboxNotFound,
    SandboxPathNotFound,
    SandboxRefusal,
    SandboxReleaseIncomplete,
)
from harness_core.sandbox.ledger import AcquireLedger, LedgerEntry, key_digest
from harness_core.sandbox.metering import NOOP_BROKER_METRICS, SandboxMeter
from harness_core.sandbox.pool import (
    CapacityProbe,
    WarmPool,
    WarmPoolConfig,
    WarmSandbox,
    WarmShape,
)
from harness_core.sandbox.streams import ChunkChannel, spool
from harness_core.sandbox.types import (
    ENGINE_IMAGES,
    ENGINE_SANDBOX,
    ENGINE_SANDBOX_MISSING,
    ENGINE_SANDBOX_READY,
    ENGINES_UNAVAILABLE,
    ENGINES_UNTRUSTED,
    FAREWELL_LABEL,
    GVISOR_SYSCALL_FILTER,
    LIFETIME_LABEL,
    PERSISTENT_MOUNTS,
    BrokerHealth,
    EgressDefault,
    LeaseRef,
    MountSpec,
    OrgSandboxErasure,
    Resources,
    RuntimeMode,
    SandboxFarewell,
    SandboxSpec,
    engine_images_detail,
    is_safe_segment,
)
from harness_core.supply_chain.images import ImageFacts, ImageTrust, registration_diff
from harness_core.supply_chain.trust import ArtefactUntrusted, Attestation
from harness_core.workspace.sync.archive import Owner
from harness_engines.release import CANDIDATE_LABEL

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Iterator,
        Mapping,
        Sequence,
    )

    from app.harness.sandbox.docker_client import DockerClientProvider
    from app.harness.sandbox.docker_egress import DockerEgressWiring
    from app.harness.sandbox.engine_images import EngineImageAvailability
    from harness_core.clock import Clock
    from harness_core.engines.binary import ImageBinaryReader, PinnedBinary
    from harness_core.ids import IdSource
    from harness_core.sandbox.metering import BrokerMetrics

# Every sandbox process runs as the engine images' USER; a handed-over repository is that user's too (WP-110).
_SANDBOX_UID = Owner().uid
# Containers idle on this; work arrives through exec.
KEEPALIVE = ("/bin/sh", "-c", "while :; do sleep 3600; done")
SUBPATH_MIN_ENGINE = 26
MAX_ARCHIVE_BYTES = 4 * 1024**3
# A removal the daemon is still running when the client's request times out (60 s) is waited for this long more
# before it is handed to the sweep: a busy shared daemon has taken over a minute (BUGS-A, rx5).
REMOVE_WAIT_S = 120
_ARCHIVE_SPOOL_BYTES = 16 * 1024**2
_ARCHIVE_CHUNK = 64 * 1024
_PIDFILE_DIR = TMPFS
_SIGNAL_RETRIES = 20
_EXIT_POLL_S = 0.05
_HELPER_MOUNT = "/data"
# Where a sandbox's seeder mounts its read-only scratch volumes, each at /seed/<mount index> (PF-01).
_SEED_MOUNT = "/seed"
# A helper or firewall container lives for one copy or one install; one this old at start is a crashed broker's.
_TRANSIENT_GRACE_S = 10 * 60
# Farewell outcomes that never reached the vendor: the person has to sign out there themselves (SIGNIN SP-5).
_UNREVOKED = frozenset({"gone", "failed", "timeout"})
# TEST-NET-1 (RFC 5737): no host answers it, and an internal network has no route to it.
NO_UPSTREAM_DNS = "192.0.2.53"
_TMP_OPTIONS = "size={mb}m,nr_inodes={inodes},mode=1777,noexec,nosuid,nodev"
# A tmpfs's default inode count follows host RAM, not its size: an inode fill would grow kernel memory until the
# cgroup's OOM killer ends it, instead of failing with ENOSPC like a byte fill does.
TMPFS_INODES_PER_MB = 1024
# Under gVisor the host's pids cgroup counts the Sentry's own threads and a stub process per guest process (systrap:
# about 30 at rest, about 2 per guest process), not the guest's tasks: at the spec's pids the Sentry could not clone
# and the whole sandbox died instead of a fork failing (R31-GVISO). There the spec's limit is RLIMIT_NPROC, which the
# Sentry counts in its own kernel, one per sandbox (under runc the count is host-wide per uid, shared by every
# sandbox, so runc keeps the cgroup); the host cgroup stays a looser bound above it.
GVISOR_HOST_PIDS_BASE = 128
GVISOR_HOST_PIDS_PER_TASK = 3
# A persistent workspace is a subpath of the shared data volume on the host's disk, which no kernel quota bounds
# (Docker's local driver has none for a subpath): the broker measures it, as the kubelet does ephemeral storage, and
# the kernel bounds how fast a sandbox writes (``WriteBound``), so the most it can write past its quota before the next
# measure is known: rate × interval, SEC-A-03.
WORKSPACE_CHECK_S = 10.0
# Disk inodes are the host's, shared with everything on it: a tighter ratio than a tmpfs's.
WORKSPACE_INODES_PER_MB = 256
WORKSPACE_QUOTA_EXCEEDED = "workspace_quota_exceeded"
_MEASURE_TIMEOUT_S = 60.0
# du and find as the sandbox user: a folder it made unreadable fails the measure, which fails closed (over quota).
# Inodes are counted with find, not ``du --inodes``: the BusyBox du of the Alpine engine images has no --inodes, and
# every measure there failed closed, ending each opencode/Codex/Gemini sandbox at the guard's first round.
_UNREADABLE = "unreadable"
_MEASURE = (
    f'b=$(du -sxk -- "$1") || {{ echo {_UNREADABLE}; exit 1; }}; '
    f'i=$({{ find "$1" -xdev 2>/dev/null || echo "!{_UNREADABLE}"; }} '
    f"| awk '$0 == \"!{_UNREADABLE}\" {{ u = 1 }} END {{ print u ? \"{_UNREADABLE}\" : NR }}'); "
    f'[ "$i" != {_UNREADABLE} ] || {{ echo {_UNREADABLE}; exit 1; }}; '
    'echo "${b%%[[:space:]]*} $i"'
)
# A measure that could not run at all (the sandbox at its pids limit, a slow daemon) is tried again next round; only
# this many in a row fail closed, so a build briefly at its limit is not mistaken for a hidden workspace.
_MAX_UNMEASURED = 3
_UNMEASURED = "unmeasured"
# The sandbox user may have made folders unreadable to itself: make them its own again before removing them.
_ERASE = 'test -e "$1" || exit 3; chmod -R u+rwX -- "$1" 2>/dev/null; rm -rf -- "$1"'
# Open egress (the legacy SANDBOX_ALLOW_NETWORK path) without main #3780's address filter is refused.
EGRESS_FIREWALL_UNAVAILABLE = "egress_firewall_unavailable"
CANDIDATE_IMAGE = "candidate_image"
# C2.20: a sandbox pinned to an image digest was created from another image (the tag was moved after the pin).
IMAGE_DIGEST_MISMATCH = "image_digest_mismatch"
# ADR-SEC-01: an image no trusted key vouches for (its cause: ``sandbox.image_untrusted``).
IMAGE_UNTRUSTED = "image_untrusted"
_FIREWALL_TIMEOUT_S = 60
_ACK = b"\x01"
_GONE = "gone"
# The relay an image brings (engine images have no python3, P5.1-f), else the Python one below.
# Both: connect to 127.0.0.1:<port>, write one ack byte, then relay both ways with half-close.
RELAY_BINARY = "pxh-relay"
_CONNECT = (
    f'if command -v {RELAY_BINARY} >/dev/null 2>&1; then exec {RELAY_BINARY} "$1"; fi; '
    'exec python3 -c "$2" "$1"'
)
_RELAY = r"""
import os, socket, sys, threading
s = socket.socket()
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(111)
os.write(1, b"\x01")
def up():
    while True:
        d = os.read(0, 65536)
        if not d:
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            return
        s.sendall(d)
threading.Thread(target=up, daemon=True).start()
while True:
    d = s.recv(65536)
    if not d:
        break
    while d:
        d = d[os.write(1, d):]
"""


@dataclass(frozen=True, slots=True)
class DockerCapabilities:
    reachable: bool
    engine_version: str = ""
    runtimes: frozenset[str] = frozenset()
    exec_transport: str = ""
    # The daemon's memory (`docker info` MemTotal): what the warm pool sizes from.
    memory_mb: int = 0
    # Arguments the daemon's runsc entry carries that weaken gVisor (``unsafe_runsc_args``): gVisor is then not offered.
    unsafe_runsc: tuple[str, ...] = ()
    # Runtimes the daemon has that the operator withheld from the broker (``SANDBOX_WITHHOLD_RUNTIMES``).
    withheld: frozenset[str] = frozenset()
    # Whether the daemon's runsc entry carries ``--oci-seccomp`` (``runsc_oci_seccomp``).
    runsc_oci_seccomp: bool = False

    @property
    def engine_major(self) -> int:
        head = self.engine_version.split(".", 1)[0]
        return int(head) if head.isdigit() else 0

    @property
    def subpath(self) -> bool:
        return self.engine_major >= SUBPATH_MIN_ENGINE

    def modes(self) -> frozenset[RuntimeMode]:
        if not self.reachable or not self.exec_transport:
            return frozenset()
        modes = {RuntimeMode.DOCKER_HARDENED}
        if "runsc" in self.runtimes and not self.unsafe_runsc:
            modes.add(RuntimeMode.GVISOR)
        return frozenset(modes)

    @property
    def gvisor_without_syscall_filter(self) -> bool:
        """gVisor is offered and its guest has no syscall filter of its own (no ``--oci-seccomp``)."""
        return RuntimeMode.GVISOR in self.modes() and not self.runsc_oci_seccomp

    def detail(self) -> dict[str, str]:
        gvisor = RuntimeMode.GVISOR in self.modes()
        return {
            "engineVersion": self.engine_version,
            "runtimes": ",".join(sorted(self.runtimes)),
            "subpath": str(self.subpath).lower(),
            "execTransport": self.exec_transport,
            **({"runscRefused": ",".join(self.unsafe_runsc)} if self.unsafe_runsc else {}),
            **({"withheld": ",".join(sorted(self.withheld))} if self.withheld else {}),
            **({GVISOR_SYSCALL_FILTER: "on" if self.runsc_oci_seccomp else "off"} if gvisor else {}),
        }


@dataclass(frozen=True, slots=True)
class WriteBound:
    """SEC-A-03: the kernel's cap on how fast one sandbox writes to the host's disk (cgroup ``io.max``, Docker's
    ``BlkioDeviceWriteBps``), on ``device``, the block device that holds the daemon's volumes (a whole disk, which
    the kernel requires; the installer finds it). ``mbps``: MiB per second per sandbox."""

    device: str
    mbps: int

    def __post_init__(self) -> None:
        if not self.device.startswith("/dev/") or self.mbps <= 0:
            raise ValueError("a write bound names a /dev block device and a positive MiB/s")

    @property
    def overshoot_mb(self) -> int:
        """How far past its quota a sandbox can write between two measures of the guard."""
        return int(self.mbps * WORKSPACE_CHECK_S)


@dataclass(frozen=True, slots=True)
class _Labels:
    namespace: str

    @property
    def managed(self) -> str:
        return f"{self.namespace}.managed"

    @property
    def sandbox_id(self) -> str:
        return f"{self.namespace}.id"

    @property
    def key(self) -> str:
        return f"{self.namespace}.key"

    @property
    def created(self) -> str:
        return f"{self.namespace}.created"

    @property
    def mode(self) -> str:
        return f"{self.namespace}.mode"

    @property
    def holder(self) -> str:
        return f"{self.namespace}.holder"

    @property
    def fence(self) -> str:
        return f"{self.namespace}.fence"

    @property
    def role(self) -> str:
        return f"{self.namespace}.role"

    @property
    def shape(self) -> str:
        return f"{self.namespace}.shape"

    @property
    def install(self) -> str:
        return f"{self.namespace}.install"

    @property
    def memory(self) -> str:
        """The memory limit a sandbox's container was made with, in MiB, so every broker process counts what the
        namespace holds (R23FU-FU-1) without inspecting each container."""
        return f"{self.namespace}.memoryMb"

    @property
    def seeds(self) -> str:
        """On a seeder: the sandbox it seeds. Never the id label, which would make it one of the sandbox's own
        containers to adopt, count as live or remove with its volumes."""
        return f"{self.namespace}.seeds"


@dataclass
class _Sandbox:
    sandbox_id: str
    container_id: str
    spec: SandboxSpec
    # Persistent mounts copied in because the engine lacks volume subpaths: (source, target).
    copied: tuple[tuple[str, str], ...] = ()
    # What the broker vouches for (EH2): path -> digest of files read from this container's image id.
    binaries: dict[str, str] = field(default_factory=dict[str, str])
    over_quota: bool = False
    # Consecutive quota-guard rounds whose measure could not run.
    unmeasured: int = 0



def _network_disabled(spec: SandboxSpec, network: str) -> bool:
    """A sandbox with no egress has no network stack beyond loopback. On runc ``NetworkDisabled`` keeps loopback; on
    gVisor it leaves the sandbox's netstack with no interface at all, so nothing in it can listen on 127.0.0.1 (R36-COVER:
    every STDIO MCP runner's bridge failed to start at the production floor, ``Cannot assign requested address``).
    ``NetworkMode=none`` alone gives gVisor's sandbox its loopback and still no route out."""
    return network == "none" and spec.mode is not RuntimeMode.GVISOR


def _open_egress(spec: SandboxSpec, *, proxied: bool) -> bool:
    return spec.egress.default is EgressDefault.ALLOW and not proxied

class DockerSandboxBroker:
    """Build with :meth:`create`, which probes the daemon for modes."""

    def __init__(
        self,
        *,
        provider: DockerClientProvider,
        clock: Clock,
        ids: IdSource,
        capabilities: DockerCapabilities,
        allowed_images: frozenset[str] | None = None,
        egress_network: str | None = None,
        data_volume: str | None = None,
        label_namespace: str = LABEL_NAMESPACE,
        volume_prefix: str = VOLUME_PREFIX,
        copy_persistent_mounts: bool | None = None,
        egress: DockerEgressWiring | None = None,
        install_id: str | None = None,
        helper_image: str | None = None,
        warm_pool: WarmPoolConfig | None = None,
        memory_mb: int | None = None,
        engine_images: EngineImageAvailability | None = None,
        egress_allow_cidrs: tuple[str, ...] = (),
        admit_candidate_images: bool = False,
        binary_pins: Mapping[str, Sequence[PinnedBinary]] | None = None,
        binary_reader: ImageBinaryReader | None = None,
        image_trust: ImageTrust | None = None,
        farewells: Mapping[str, SandboxFarewell] | None = None,
        workspace_quota_mb: int | None = None,
        workspace_check_s: float = WORKSPACE_CHECK_S,
        max_sandboxes: int | None = None,
        metrics: BrokerMetrics = NOOP_BROKER_METRICS,
        monotonic: Callable[[], float] = time.monotonic,
        withheld_runtimes: frozenset[str] = frozenset(),
        write_bound: WriteBound | None = None,
    ) -> None:
        if install_id is not None and not is_safe_segment(install_id):
            raise ValueError("an install id is one safe path segment")
        if workspace_quota_mb is not None and workspace_quota_mb <= 0:
            raise ValueError("a workspace quota is a positive number of MB")
        self._quota_mb = workspace_quota_mb
        self._check_s = workspace_check_s
        self._write_bound = write_bound
        self._guard: asyncio.Future[None] | None = None
        # Persistent sources over quota whose folder could not be erased yet: refused until it is.
        self._over_quota: set[str] = set()
        self._erasing: set[asyncio.Future[bool]] = set()
        self._provider = provider
        # Persistent folders live under <install>/ on the data volume: a daemon may serve several installs.
        self._install = install_id
        self._helper_image = helper_image
        self._clock = clock
        self._ids = ids
        self._caps = capabilities
        self._withheld = withheld_runtimes
        self._egress_network = egress_network
        self._egress_allow_cidrs = egress_allow_cidrs
        self._data_volume = data_volume
        self._labels = _Labels(label_namespace)
        # On everything the broker creates: the install label is what its start-up reconcile may remove.
        self._owned = {self._labels.managed: "1", **({self._labels.install: install_id} if install_id else {})}
        self._volume_prefix = volume_prefix
        self._copy_override = copy_persistent_mounts
        self._egress = egress
        if egress is not None:
            egress.bind_labels(
                EgressLabels(
                    managed=self._labels.managed,
                    sandbox_id=self._labels.sandbox_id,
                    created=self._labels.created,
                    role=self._labels.role,
                )
            )
        self._policy = HardeningPolicy(
            user=SANDBOX_USER,
            allowed_networks=frozenset({egress_network} if egress_network else ()),
            volume_allowed=self._owns_volume,
            allowed_images=allowed_images,
            network_allowed=egress.owns_network if egress is not None else None,
            firewall_image=helper_image,
        )
        self.ledger = AcquireLedger(clock)
        self._sandboxes: dict[str, _Sandbox] = {}
        # Released sandboxes whose removal the daemon did not finish: sandbox id -> container id, retried by the sweep.
        self._removals: dict[str, str] = {}
        # What runs in a sandbox the broker removes on its own, by its FAREWELL_LABEL (GAPS-2), and when this process
        # first saw each such sandbox it neither holds nor keeps (an orphan from before a restart).
        self._farewells = dict(farewells or {})
        self._orphans: dict[str, int] = {}
        self._log = HarnessLog.named("app.harness.sandbox.broker", broker="docker")
        self._endpoint: DockerEndpoint | None = None
        # Host memory to size the pool from; None reads the daemon's.
        self._memory_mb = memory_mb
        self._engine_images = engine_images
        # Only the test lanes that gate a candidate build set this (CC1, EH1).
        self._admit_candidates = admit_candidate_images
        self._binaries = (
            ImageBinaryVerifier(
                binary_pins,
                binary_reader or DockerImageBinaryReader(provider, user=SANDBOX_USER, labels=self._helper_labels),
                log=self._log,
                normalize=normalize_image,
            )
            if binary_pins
            else None
        )
        # ADR-SEC-01 D3: images are started only on a signature over their own digest.
        self._image_trust = image_trust
        self._trusted_images: dict[str, Attestation] = {}
        self.pool = WarmPool(self, warm_pool or WarmPoolConfig(ceiling=0), clock=clock, log=self._log, metrics=metrics)
        # BU-04: each sandbox's seconds from its bind to a run until the run (or the broker) lets it go.
        self.meter = SandboxMeter(metrics, monotonic=monotonic, log=self._log)
        self._cap = SandboxCap(max_sandboxes, self._live_sandboxes)

    @classmethod
    async def create(
        cls,
        *,
        provider: DockerClientProvider,
        clock: Clock,
        ids: IdSource,
        allowed_images: frozenset[str] | None = None,
        egress_network: str | None = None,
        data_volume: str | None = None,
        label_namespace: str = LABEL_NAMESPACE,
        volume_prefix: str = VOLUME_PREFIX,
        copy_persistent_mounts: bool | None = None,
        egress: DockerEgressWiring | None = None,
        install_id: str | None = None,
        helper_image: str | None = None,
        warm_pool: WarmPoolConfig | None = None,
        memory_mb: int | None = None,
        engine_images: EngineImageAvailability | None = None,
        egress_allow_cidrs: tuple[str, ...] = (),
        admit_candidate_images: bool = False,
        binary_pins: Mapping[str, Sequence[PinnedBinary]] | None = None,
        binary_reader: ImageBinaryReader | None = None,
        image_trust: ImageTrust | None = None,
        farewells: Mapping[str, SandboxFarewell] | None = None,
        workspace_quota_mb: int | None = None,
        workspace_check_s: float = WORKSPACE_CHECK_S,
        max_sandboxes: int | None = None,
        metrics: BrokerMetrics = NOOP_BROKER_METRICS,
        monotonic: Callable[[], float] = time.monotonic,
        withheld_runtimes: frozenset[str] = frozenset(),
        write_bound: WriteBound | None = None,
    ) -> DockerSandboxBroker:
        broker = cls(
            provider=provider,
            clock=clock,
            ids=ids,
            capabilities=await probe(provider, withheld=withheld_runtimes),
            withheld_runtimes=withheld_runtimes,
            allowed_images=allowed_images,
            egress_network=egress_network,
            data_volume=data_volume,
            label_namespace=label_namespace,
            volume_prefix=volume_prefix,
            copy_persistent_mounts=copy_persistent_mounts,
            egress=egress,
            install_id=install_id,
            helper_image=helper_image,
            warm_pool=warm_pool,
            memory_mb=memory_mb,
            engine_images=engine_images,
            egress_allow_cidrs=egress_allow_cidrs,
            admit_candidate_images=admit_candidate_images,
            binary_pins=binary_pins,
            binary_reader=binary_reader,
            image_trust=image_trust,
            farewells=farewells,
            workspace_quota_mb=workspace_quota_mb,
            workspace_check_s=workspace_check_s,
            max_sandboxes=max_sandboxes,
            metrics=metrics,
            monotonic=monotonic,
            write_bound=write_bound,
        )
        if egress is not None and broker._caps.reachable:
            await egress.load()
        if broker._caps.reachable:
            _ = await broker.pool.load()
        broker._log.info(
            "broker.capabilities",
            **broker._caps.detail(),
            modes=sorted(broker.modes()),
            egressProxy=egress.proxy_container if egress is not None else "",
        )
        broker._warn_unfiltered_gvisor(broker._caps)
        return broker

    def _warn_unfiltered_gvisor(self, caps: DockerCapabilities) -> None:
        if caps.gvisor_without_syscall_filter:
            self._log.warning(
                "broker.gvisor_syscall_filter_off",
                hint="register runsc with --oci-seccomp (deployment/docker-compose/GVISOR.md)",
            )

    @property
    def egress(self) -> DockerEgressWiring | None:
        return self._egress

    @property
    def write_mbps(self) -> int:
        """SEC-A-03: the kernel's cap on each sandbox's disk writes, MiB/s; 0 when none is configured."""
        return self._write_bound.mbps if self._write_bound is not None else 0

    async def resync_egress(self) -> bool:
        """After a proxy restart, give it every held binding back (it refuses unknown sources until then)."""
        return await self._egress.resync() if self._egress is not None else False

    @property
    def capabilities(self) -> DockerCapabilities:
        return self._caps

    def modes(self) -> frozenset[RuntimeMode]:
        return self._caps.modes()

    @property
    def _copy_mounts(self) -> bool:
        return self._copy_override if self._copy_override is not None else not self._caps.subpath

    def _owns_volume(self, name: str) -> bool:
        return owns_volume(name, data_volume=self._data_volume, volume_prefix=self._volume_prefix)

    def _client(self) -> Any:  # noqa: ANN401 - docker-py is untyped
        return self._provider.client

    async def _blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return await self._provider.run_blocking(fn, *args, **kwargs)

    def endpoint(self) -> DockerEndpoint:
        if self._endpoint is None:
            api = self._client().api
            self._endpoint = DockerEndpoint.resolve(api.api_version, authorization=api.headers.get("Authorization"))
        return self._endpoint

    async def health(self) -> BrokerHealth:
        caps = await probe(self._provider, withheld=self._withheld)
        if caps != self._caps:
            self._log.info("broker.capabilities_changed", **caps.detail(), modes=sorted(caps.modes()))
            if caps.gvisor_without_syscall_filter != self._caps.gvisor_without_syscall_filter:
                self._warn_unfiltered_gvisor(caps)
            self._caps = caps
        detail = {
            **caps.detail(),
            PERSISTENT_MOUNTS: str(bool(self._data_volume)).lower(),
            ENGINE_SANDBOX: ENGINE_SANDBOX_READY if self._egress is not None else ENGINE_SANDBOX_MISSING,
        }
        if self.pool.enabled and caps.reachable:
            detail.update(self.pool.detail(await self.pool.capacity()))
        if self._engine_images is not None and caps.reachable:
            detail[ENGINES_UNAVAILABLE] = ",".join(await self._engine_images.unavailable())
            detail[ENGINES_UNTRUSTED] = ",".join(self._engine_images.untrusted)
            detail[ENGINE_IMAGES] = engine_images_detail(await self._engine_images.digests())
        return BrokerHealth(
            ok=caps.reachable and bool(caps.modes()), modes=caps.modes(), active=self.ledger.active(), detail=detail
        )

    def _refuse(self, error: SandboxRefusal, spec: SandboxSpec) -> SandboxRefusal:
        self._log.warning("sandbox.refused", **_log_fields(spec.labels, reason=error.reason, mode=spec.mode.value))
        return error

    def _validate(self, spec: SandboxSpec) -> bool:
        if spec.mode not in self.modes():
            raise self._refuse(
                IsolationUnavailable(f"mode {spec.mode} is not available on this host", reason="mode_unavailable"),
                spec,
            )
        errors = spec.validation_errors()
        errors += [f"label {k!r} is reserved" for k in spec.labels if k.startswith(self._labels.namespace + ".")]
        if errors:
            raise self._refuse(InvalidSandboxSpec("; ".join(errors), reason="invalid_spec"), spec)
        if not self._policy.image_allowed(spec.image_digest):
            raise self._refuse(ImageNotAllowed(f"image {spec.image_digest!r} is not allowed", reason="image_not_allowed"), spec)
        try:
            proxied = admit_egress(spec, proxy_configured=self._egress is not None)
        except SandboxRefusal as refused:
            raise self._refuse(refused, spec) from None
        if spec.egress.default is EgressDefault.ALLOW and not self._egress_network:
            raise self._refuse(PolicyUnenforceable("no egress network is configured", reason="egress_network_missing"), spec)
        if _open_egress(spec, proxied=proxied) and spec.mode is not RuntimeMode.DOCKER_HARDENED:
            raise self._refuse(
                PolicyUnenforceable(
                    "open egress is address-filtered only under runc: gVisor's netstack sends past the namespace's rules",
                    reason=EGRESS_FIREWALL_UNAVAILABLE,
                ),
                spec,
            )
        if any(m.source is not None for m in spec.mounts) and not self._data_volume:
            raise self._refuse(PolicyUnenforceable("no data volume is configured", reason="data_volume_missing"), spec)
        return proxied

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> DockerSandboxHandle:
        proxied = self._validate(spec)
        await self._clear_over_quota(spec)
        key = key_digest(idempotency_key)
        started = time.monotonic()
        try:
            async with self.pool.acquiring(), self.ledger.lock(key):
                entry = self.ledger.by_key(key)
                adopted = False
                if entry is None:
                    entry = await self._adopt(key, spec)
                    adopted = entry is not None
                if entry is not None:
                    self.ledger.admit(entry, lease)
                    sandbox = self._sandboxes[entry.sandbox_id]
                    await self._rebind(sandbox, spec, proxied=proxied)
                    self.meter.bound(sandbox.sandbox_id, sandbox.spec)
                    self._log.info(
                        "sandbox.acquired",
                        **_log_fields(
                            spec.labels,
                            sandboxId=sandbox.sandbox_id,
                            keyDigest=key,
                            reused=True,
                            adopted=adopted,
                            fence=lease.fence,
                        ),
                    )
                    self._watch()
                    return DockerSandboxHandle(self, sandbox)
                async with self._admitted(spec):
                    sandbox = await self._from_pool(spec, key, lease, proxied=proxied)
                    warm = sandbox is not None
                    if sandbox is None:
                        self.pool.learn(spec)
                        _ = await self.pool.make_room(spec)
                        sandbox = await self._create(spec, key, lease, proxied=proxied)
                    self.ledger.record(key, sandbox.sandbox_id, lease)
                    self._sandboxes[sandbox.sandbox_id] = sandbox
                    self.meter.bound(sandbox.sandbox_id, spec)
        finally:
            self.pool.kick()
        self._log.info(
            "sandbox.acquired",
            **_log_fields(
                spec.labels,
                sandboxId=sandbox.sandbox_id,
                keyDigest=key,
                reused=False,
                warm=warm,
                mode=spec.mode.value,
                fence=lease.fence,
                ms=int((time.monotonic() - started) * 1000),
            ),
        )
        self._watch()
        return DockerSandboxHandle(self, sandbox)

    async def _rebind(self, sandbox: _Sandbox, spec: SandboxSpec, *, proxied: bool) -> None:
        """A re-acquire for a later turn (SIGNIN-5's kept sandbox) brings that turn's egress: its tool upstream and
        placeholders. Only the binding can change: the container's named hosts were fixed when it was made."""
        if spec.egress == sandbox.spec.egress and spec.labels == sandbox.spec.labels:
            return
        if {d.host for d in spec.egress.destinations} != {d.host for d in sandbox.spec.egress.destinations}:
            raise InvalidSandboxSpec("a re-acquire cannot change where the sandbox may reach", reason="invalid_spec")
        if proxied and self._egress is not None:
            _ = await self._egress.bind(sandbox.sandbox_id, spec)
        sandbox.spec = replace(sandbox.spec, egress=spec.egress, labels=spec.labels)

    async def _from_pool(self, spec: SandboxSpec, key: str, lease: LeaseRef, *, proxied: bool) -> _Sandbox | None:
        warm = await self.pool.take(spec)
        if warm is None:
            return None
        try:
            return await self._bind_warm(warm, spec, key, lease, proxied=proxied)
        except BaseException as exc:
            # Never handed out half-bound: destroyed, and this acquire goes cold.
            await asyncio.shield(self.discard_warm(warm, reason="bind_failed"))
            if not isinstance(exc, Exception) or isinstance(exc, SandboxRefusal):
                raise
            self._log.warning("sandbox.warm_bind_failed", sandboxId=warm.sandbox_id, errorType=type(exc).__name__)
            return None

    async def _bind_warm(
        self, warm: WarmSandbox, spec: SandboxSpec, key: str, lease: LeaseRef, *, proxied: bool
    ) -> _Sandbox:
        """Claimed already: the run's labels on a binding marker, the proxy's binding, then the network."""
        sandbox_id = warm.sandbox_id
        container_id = warm.ref or cast(
            "str", (await self._blocking(self._client().api.inspect_container, self._claimed_name(sandbox_id)))["Id"]
        )
        binaries = await self._vouch(spec, container_id, sandboxId=sandbox_id)
        await afailpoint("broker.warm.after_claim")
        marker = {
            **dict(spec.labels),
            **self._owned,
            self._labels.sandbox_id: sandbox_id,
            self._labels.key: key,
            self._labels.created: str(int(self._clock.now().timestamp())),
            self._labels.mode: spec.mode.value,
            self._labels.holder: lease.holder,
            self._labels.fence: str(lease.fence),
            self._labels.role: "binding",
        }
        await self._blocking(
            self._client().api.create_volume, name=self._marker_name(sandbox_id), driver="local", labels=marker
        )
        if proxied and self._egress is not None:
            subnet = await self._egress.bind(sandbox_id, spec)
            await self._blocking(self._client().api.disconnect_container_from_network, container_id, "none")
            await self._blocking(
                self._client().api.connect_container_to_network,
                container_id,
                self._egress.network_name(sandbox_id),
                ipv4_address=subnet.sandbox_address,
            )
        return _Sandbox(sandbox_id, container_id, spec, binaries=binaries)

    def _warm_name(self, sandbox_id: str) -> str:
        return f"{self._volume_prefix}-warm-{sandbox_id}"

    def _claimed_name(self, sandbox_id: str) -> str:
        return f"{self._volume_prefix}-sbx-{sandbox_id}"

    def _marker_name(self, sandbox_id: str) -> str:
        return f"{self._volume_prefix}-{sandbox_id}-bind"

    def _unclaimed(self, info: Mapping[str, Any]) -> bool:
        labels = cast("dict[str, str]", info.get("Labels") or {})
        name = f"/{self._warm_name(labels.get(self._labels.sandbox_id, ''))}"
        return name in cast("list[str]", info.get("Names") or [])

    async def create_warm(self, shape: WarmShape, labels: Mapping[str, str]) -> WarmSandbox:
        """A generic sandbox of ``shape``: no run's labels, ``NetworkMode=none``, its network prepared beside it."""
        sandbox_id = self._ids.new_id("sbx")
        created_at = int(self._clock.now().timestamp())
        own = {
            **dict(labels),
            **self._owned,
            self._labels.sandbox_id: sandbox_id,
            self._labels.created: str(created_at),
            self._labels.mode: shape.mode.value,
            self._labels.memory: str(shape.resources.memory_mb),
        }
        spec = shape.template(labels)
        container_id: str | None = None
        try:
            await self._admit_image(spec)
            extra_hosts: dict[str, str] | None = None
            if shape.proxied_hosts is not None:
                if self._egress is None:
                    raise PolicyUnenforceable("no egress proxy is configured", reason="egress_proxy_missing")
                subnet = await self._egress.prepare(sandbox_id, own)
                extra_hosts = hosts_for(spec, subnet)
            mounts, _ = await self._mounts(spec, sandbox_id, own)
            host_config = self._host_config(spec, mounts, network="none", extra_hosts=extra_hosts)
            self._policy.check(image=shape.image_digest, user=SANDBOX_USER, host_config=host_config)
            # Not `network_disabled`: a container created without a network sandbox can never join one.
            created, _ = await _both(
                self._blocking(
                    self._client().api.create_container,
                    shape.image_digest,
                    command=list(KEEPALIVE),
                    user=SANDBOX_USER,
                    working_dir=_workdir(spec),
                    labels={**own, self._labels.role: "warm", self._labels.shape: shape.to_wire()},
                    host_config=host_config,
                    hostname="sandbox",
                    name=self._warm_name(sandbox_id),
                ),
                self._make_seeder(spec, sandbox_id),
            )
            container_id = cast("str", created["Id"])
            _ = await self._vouch(spec, container_id, sandboxId=sandbox_id)
            await self._blocking(self._client().api.start, container_id)
        except BaseException as exc:
            if isinstance(exc, Exception) and not isinstance(exc, SandboxError):
                self._log.error("sandbox.warm_create_failed", exc=exc, sandboxId=sandbox_id, errorType=type(exc).__name__)
            await asyncio.shield(self._remove(sandbox_id, container_id))
            raise
        return WarmSandbox(sandbox_id, shape, float(created_at), ref=container_id)

    async def claim_warm(self, sandbox: WarmSandbox) -> bool:
        """Rename out of the pool's name: the daemon lets exactly one caller do it, across processes."""
        try:
            await self._blocking(
                self._client().api.rename, self._warm_name(sandbox.sandbox_id), self._claimed_name(sandbox.sandbox_id)
            )
        except Exception as exc:
            if _is_not_found(exc) or _is_conflict(exc) or _status(exc) == 400:
                return False
            raise
        return True

    async def discard_warm(self, sandbox: WarmSandbox, *, reason: str) -> None:
        _ = self._sandboxes.pop(sandbox.sandbox_id, None)
        await self._remove(sandbox.sandbox_id, sandbox.ref or None)
        self._log.info("sandbox.released", sandboxId=sandbox.sandbox_id, reason=f"warm_{reason}")

    async def list_warm(self) -> list[WarmSandbox]:
        listed = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=warm"]},
        )
        found: list[WarmSandbox] = []
        for info in cast("list[dict[str, Any]]", listed):
            if not self._unclaimed(info):
                continue
            labels = cast("dict[str, str]", info.get("Labels") or {})
            try:
                shape = WarmShape.from_wire(labels.get(self._labels.shape, ""))
            except (ValueError, KeyError, TypeError):
                continue
            found.append(
                WarmSandbox(
                    labels.get(self._labels.sandbox_id, ""),
                    shape,
                    float(int(labels.get(self._labels.created, "0") or 0)),
                    ref=cast("str", info["Id"]),
                    running=info.get("State") == "running",
                )
            )
        return found

    @contextlib.asynccontextmanager
    async def _admitted(self, spec: SandboxSpec) -> AsyncGenerator[None]:
        try:
            async with self._cap.admit():
                yield
        except BrokerUnavailable:
            if self._cap.limit is not None:
                self._log.warning(
                    "sandbox.capacity_reached", **_log_fields(spec.labels, reasonCode="sandbox_capacity", limit=self._cap.limit)
                )
            raise

    async def _live_sandboxes(self) -> int:
        return (await self.probe_capacity()).held

    async def probe_capacity(self) -> CapacityProbe:
        """Every live sandbox of the namespace counts, warm ones included, whichever broker process made it."""
        listed = await self._blocking(self._client().api.containers, filters={"label": [f"{self._labels.managed}=1"]})
        held = warm = held_mb = warm_mb = 0
        for info in cast("list[dict[str, Any]]", listed):
            labels = cast("dict[str, str]", info.get("Labels") or {})
            role = labels.get(self._labels.role)
            if role == "warm" and self._unclaimed(info):
                warm += 1
                warm_mb += self._memory_of(labels)
            elif role in ("sandbox", "warm"):
                held += 1
                held_mb += self._memory_of(labels)
        memory = self._memory_mb if self._memory_mb is not None else self._caps.memory_mb
        return CapacityProbe(memory_mb=memory, held=held, warm=warm, held_mb=held_mb, warm_mb=warm_mb)

    def _memory_of(self, labels: Mapping[str, str]) -> int:
        """A container's memory limit from its label; one made before the label counts as the default size."""
        raw = labels.get(self._labels.memory, "")
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        with contextlib.suppress(ValueError, KeyError, TypeError):
            return WarmShape.from_wire(labels[self._labels.shape]).resources.memory_mb
        return Resources().memory_mb

    def holds(self, sandbox_id: str) -> bool:
        return sandbox_id in self._sandboxes

    async def fill_warm(self) -> int:
        return await self.pool.refill()

    async def trim_warm(self) -> int:
        return await self.pool.trim()

    def warm_ids(self) -> frozenset[str]:
        return self.pool.warm_ids()

    async def aclose(self) -> None:
        if self._guard is not None:
            _ = self._guard.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._guard
            self._guard = None
        if self._erasing:
            _ = await asyncio.gather(*self._erasing, return_exceptions=True)
        await self.pool.aclose()

    def _ulimits(self, spec: SandboxSpec) -> list[dict[str, object]]:
        # No core dumps: the kernel hands a crashing process's dump to the host's core_pattern helper (apport), a
        # host program a sandbox would otherwise reach (WP-14).
        limits: list[dict[str, object]] = [{"name": "core", "soft": 0, "hard": 0}]
        if self._quota_mb is not None:
            limit = self._quota_mb * 1024 * 1024
            limits.append({"name": "fsize", "soft": limit, "hard": limit})
        if spec.mode is RuntimeMode.GVISOR:
            limits.append({"name": "nproc", "soft": spec.resources.pids, "hard": spec.resources.pids})
        return limits

    def _quota_refusal(self) -> SandboxRefusal:
        return SandboxRefusal(
            f"The workspace went over its {self._quota_mb} MB limit", reason=WORKSPACE_QUOTA_EXCEEDED
        )

    def _watched(self, sandbox: _Sandbox) -> list[MountSpec]:
        """The writable persistent mounts the quota guard measures; a copied one is a size-capped tmpfs already."""
        if self._quota_mb is None or self._copy_mounts or sandbox.over_quota:
            return []
        return [m for m in sandbox.spec.mounts if m.source is not None and not m.read_only]

    def _watch(self) -> None:
        if self._guard is not None and not self._guard.done():
            return
        if any(self._watched(s) for s in self._sandboxes.values()):
            self._guard = spawn_detached(self._guard_loop())

    async def _guard_loop(self) -> None:
        """Ends once no held sandbox has a workspace to measure; the next acquire of one starts it again."""
        while True:
            await asyncio.sleep(self._check_s)
            try:
                watched = await self.check_workspaces()
            except Exception as exc:
                self._log.warning("sandbox.workspace_check_failed", errorType=type(exc).__name__)
                continue
            if watched == 0:
                self._guard = None
                return

    async def check_workspaces(self) -> int:
        """One round of the quota guard; how many sandboxes it measured."""
        watched = [(s, mounts) for s in list(self._sandboxes.values()) if (mounts := self._watched(s))]
        for sandbox, mounts in watched:
            for mount in mounts:
                if sandbox.over_quota:
                    break
                usage = await self._measure(sandbox, mount.target)
                if usage == _UNMEASURED:
                    sandbox.unmeasured += 1
                    if sandbox.unmeasured < _MAX_UNMEASURED:
                        break
                    usage = None
                if isinstance(usage, str):
                    break
                if usage is None or self._over(usage):
                    await self._exceeded(sandbox, mount, usage)
                    break
                sandbox.unmeasured = 0
        return len(watched)

    def _over(self, usage: tuple[int, int]) -> bool:
        limit = cast("int", self._quota_mb)
        return usage[0] > limit * 1024 or usage[1] > limit * WORKSPACE_INODES_PER_MB

    async def _measure(self, sandbox: _Sandbox, target: str) -> tuple[int, int] | str | None:
        """(KiB, inodes) of the mount; ``None`` when part of it is unreadable, ``_UNMEASURED`` when the measure did not
        run or answered nothing usable, ``_GONE`` when the sandbox is not running."""
        api = self._client().api

        def measure() -> tuple[int, bytes]:
            created = api.exec_create(sandbox.container_id, ["sh", "-c", _MEASURE, "measure", target], user=SANDBOX_USER)
            output = cast("bytes", api.exec_start(created["Id"]))
            code = cast("dict[str, Any]", api.exec_inspect(created["Id"])).get("ExitCode")
            return (code if isinstance(code, int) else -1), output

        try:
            code, output = await asyncio.wait_for(self._blocking(measure), _MEASURE_TIMEOUT_S)
        except TimeoutError:
            return _UNMEASURED
        except Exception as exc:
            if _is_not_found(exc) or _is_conflict(exc):
                return _GONE
            raise
        fields = output.decode("ascii", "replace").split()
        if code == 1 and _UNREADABLE in fields:
            return None
        if code != 0 or len(fields) != 2 or not all(f.isdigit() for f in fields):
            return _UNMEASURED
        return int(fields[0]), int(fields[1])

    async def _exceeded(self, sandbox: _Sandbox, mount: MountSpec, usage: tuple[int, int] | None) -> None:
        """Every sandbox writing the folder is ended and refused from now on; the folder, a cache of the workspace
        revision store that the next materialize rewrites, is erased so the disk is given back at once."""
        source = cast("str", mount.source)
        sharing = [
            s for s in list(self._sandboxes.values())
            if any(m.source == source and not m.read_only for m in s.spec.mounts)
        ]
        for held in sharing:
            held.over_quota = True
        # Until the folder is erased, an acquire of it erases it first (or is refused).
        self._over_quota.add(source)
        self._log.warning(
            "sandbox.workspace_quota_exceeded",
            **_log_fields(
                sandbox.spec.labels,
                sandboxId=sandbox.sandbox_id,
                limitMb=cast("int", self._quota_mb),
                measured=usage is not None,
                usedKb=usage[0] if usage is not None else -1,
                usedInodes=usage[1] if usage is not None else -1,
                sharing=len(sharing),
            ),
        )
        for held in sharing:
            try:
                await self._blocking(self._client().api.kill, held.container_id)
            except Exception as exc:
                if not (_is_not_found(exc) or _is_conflict(exc)):
                    self._log.warning("sandbox.quota_kill_failed", sandboxId=held.sandbox_id, errorType=type(exc).__name__)
        # A broker closing meanwhile waits for the eraser rather than leave its helper holding the data volume.
        erasing = spawn_detached(self._erase_cache(source))
        self._erasing.add(erasing)
        erasing.add_done_callback(self._erasing.discard)
        if await asyncio.shield(erasing):
            self._over_quota.discard(source)

    async def _erase_cache(self, source: str) -> bool:
        image = self._erase_image()
        if image is None or not self._data_volume:
            return False
        try:
            exit_code = await self._run_helper(image, ["sh", "-c", _ERASE, "erase", f"{_HELPER_MOUNT}/{self._volume_key(source)}"])
        except Exception as exc:
            self._log.warning("sandbox.quota_erase_failed", errorType=type(exc).__name__)
            return False
        return exit_code in (0, 3)

    async def _clear_over_quota(self, spec: SandboxSpec) -> None:
        for source in [m.source for m in spec.mounts if m.source is not None and m.source in self._over_quota]:
            if not await self._erase_cache(source):
                raise self._refuse(self._quota_refusal(), spec)
            self._over_quota.discard(source)

    async def _adopt(self, key: str, spec: SandboxSpec) -> LedgerEntry | None:
        """A container for ``key`` left by an earlier broker process, re-registered if still running."""
        found = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.key}={key}"]},
        )
        candidates = [
            (info, cast("dict[str, str]", info.get("Labels") or {})) for info in cast("list[dict[str, Any]]", found)
        ]
        if not candidates:
            candidates = await self._bound_warm(key)
        for info, labels in candidates:
            sandbox_id = labels.get(self._labels.sandbox_id, "")
            if info.get("State") != "running" or not sandbox_id:
                await self._remove(sandbox_id, info["Id"])
                continue
            try:
                binaries = await self._vouch(spec, cast("str", info["Id"]), sandboxId=sandbox_id)
            except SandboxRefusal:
                await self._remove(sandbox_id, info["Id"])
                raise
            fence = int(labels.get(self._labels.fence, "0") or 0)
            holder = labels.get(self._labels.holder, "")
            entry = self.ledger.record(key, sandbox_id, LeaseRef(holder, fence))
            copied = (
                tuple((m.source, m.target) for m in spec.mounts if m.source is not None) if self._copy_mounts else ()
            )
            self._sandboxes[sandbox_id] = _Sandbox(sandbox_id, info["Id"], spec, copied, binaries)
            if self._egress is not None and spec.egress.needs_proxy:
                await self._egress.reattach(sandbox_id, spec)
            self._log.info("sandbox.adopted", sandboxId=sandbox_id, keyDigest=key)
            return entry
        return None

    async def _bound_warm(self, key: str) -> list[tuple[dict[str, Any], dict[str, str]]]:
        """Warm-bound sandboxes of ``key``: their container, with the run's labels from the binding marker."""
        found: list[tuple[dict[str, Any], dict[str, str]]] = []
        for marker in await self._markers([f"{self._labels.key}={key}"]):
            labels = cast("dict[str, str]", marker.get("Labels") or {})
            listed = await self._blocking(
                self._client().api.containers,
                all=True,
                filters={"label": [f"{self._labels.managed}=1", f"{self._labels.sandbox_id}={labels.get(self._labels.sandbox_id, '')}"]},
            )
            found.extend((info, labels) for info in cast("list[dict[str, Any]]", listed))
        return found

    async def _markers(self, label_filters: list[str]) -> list[dict[str, Any]]:
        listed = await self._blocking(
            self._client().api.volumes,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=binding", *label_filters]},
        )
        return _volumes_of(listed)

    async def _create(self, spec: SandboxSpec, key: str, lease: LeaseRef, *, proxied: bool) -> _Sandbox:
        sandbox_id = self._ids.new_id("sbx")
        labels = {
            **dict(spec.labels),
            **self._owned,
            self._labels.sandbox_id: sandbox_id,
            self._labels.key: key,
            self._labels.created: str(int(self._clock.now().timestamp())),
            self._labels.mode: spec.mode.value,
            self._labels.holder: lease.holder,
            self._labels.fence: str(lease.fence),
            self._labels.memory: str(spec.resources.memory_mb),
        }
        container_id: str | None = None
        try:
            await self._admit_image(spec)
            network = await self._network(spec)
            open_egress = _open_egress(spec, proxied=proxied)
            egress_cidrs = await self._open_egress_cidrs(spec) if open_egress else []
            networking_config: object = None
            extra_hosts: dict[str, str] | None = None
            if proxied and self._egress is not None:
                attachment = await self._egress.attach(sandbox_id, spec, labels)
                network, extra_hosts = attachment.network, dict(attachment.extra_hosts)
                networking_config = self._client().api.create_networking_config(
                    {network: self._client().api.create_endpoint_config(ipv4_address=attachment.subnet.sandbox_address)}
                )
            mounts, copied = await self._mounts(spec, sandbox_id, labels)
            host_config = self._host_config(spec, mounts, network=network, extra_hosts=extra_hosts)
            self._policy.check(image=spec.image_digest, user=SANDBOX_USER, host_config=host_config)
            created, _ = await _both(
                self._blocking(
                    self._client().api.create_container,
                    spec.image_digest,
                    command=list(KEEPALIVE),
                    user=SANDBOX_USER,
                    working_dir=_workdir(spec),
                    labels={**labels, self._labels.role: "sandbox"},
                    host_config=host_config,
                    hostname="sandbox",
                    name=self._claimed_name(sandbox_id),
                    network_disabled=_network_disabled(spec, network),
                    networking_config=networking_config,
                ),
                self._make_seeder(spec, sandbox_id),
            )
            container_id = cast("str", created["Id"])
            await afailpoint("broker.provision.after_create_before_ref")
            binaries = await self._vouch(spec, container_id, **_log_fields(spec.labels, sandboxId=sandbox_id))
            await self._blocking(self._client().api.start, container_id)
            if open_egress:
                await self._install_egress_firewall(spec, sandbox_id, container_id, egress_cidrs)
            sandbox = _Sandbox(sandbox_id, container_id, spec, copied, binaries)
            for source, target in copied:
                await self._copy_in(sandbox, source, target)
            return sandbox
        except (SandboxRefusal, asyncio.CancelledError):
            await asyncio.shield(self._remove(sandbox_id, container_id))
            raise
        except Exception as exc:
            self._log.error("sandbox.create_failed", exc=exc, sandboxId=sandbox_id, errorType=type(exc).__name__)
            try:
                await self._remove(sandbox_id, container_id)
            except Exception as cleanup:
                # A slow daemon finishes a create after the client gave up and the late container holds the volume
                # (409 "in use"): the create's own failure is what the run must see, and the label sweep removes the
                # rest. A cleanup error raised here would turn a broker outage into a crash of the run (R14-BRK-1).
                self._log.warning(
                    "sandbox.create_cleanup_deferred", sandboxId=sandbox_id, errorType=type(cleanup).__name__
                )
            if isinstance(exc, SandboxError):
                raise
            if type(exc).__name__ == "ImageNotFound":
                # Not capacity: only an admin can make the image exist, so the run is refused, never waited for.
                raise SandboxRefusal(
                    f"sandbox image {spec.image_digest!r} is not on this host", reason="engine_image_missing"
                ) from exc
            raise BrokerUnavailable(f"could not create sandbox: {type(exc).__name__}") from exc

    def _host_config(
        self, spec: SandboxSpec, mounts: list[dict[str, object]], *, network: str, extra_hosts: dict[str, str] | None
    ) -> Any:  # noqa: ANN401 - docker-py's HostConfig
        return self._client().api.create_host_config(
            mounts=mounts,
            tmpfs={_PIDFILE_DIR: _tmp_options(spec.resources.scratch_mb)},
            ulimits=self._ulimits(spec),
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=host_pids_limit(spec),
            mem_limit=spec.resources.memory_mb * 1024 * 1024,
            memswap_limit=spec.resources.memory_mb * 1024 * 1024,
            nano_cpus=int(spec.resources.cpus * 1e9),
            network_mode=network,
            extra_hosts=extra_hosts,
            # A proxied sandbox names its peers in its hosts file; Docker's own resolver on its network must not
            # forward to the host's (engines before 26 did so even on an internal network: a DNS exfiltration path).
            dns=[NO_UPSTREAM_DNS] if extra_hosts is not None else None,
            init=True,
            runtime="runsc" if spec.mode is RuntimeMode.GVISOR else None,
            device_write_bps=(
                [{"Path": self._write_bound.device, "Rate": self._write_bound.mbps * 1024 * 1024}]
                if self._write_bound is not None
                else None
            ),
        )

    async def prepull_engine_images(self) -> tuple[str, ...]:
        """The pinned engine images this daemon lacks, pulled now from their registry (C5.01); the images pulled."""
        if self._engine_images is None or not self.capabilities.reachable:
            return ()
        return await self._engine_images.prepull()

    async def _admit_image(self, spec: SandboxSpec) -> None:
        """Present (pulled if not), and not a candidate build: a candidate retagged by hand under an allowlisted
        tag passes the allowlist by name, so its label is read from the image itself."""
        image = spec.image_digest
        if not await self._provider.ensure_image(image):
            await self._provider.pull_image(image)
        if not self._admit_candidates and CANDIDATE_LABEL in await self._provider.image_labels(image):
            raise self._refuse(
                ImageNotAllowed(f"image {image!r} is a candidate engine build", reason=CANDIDATE_IMAGE), spec
            )
        await self._verify_signature(spec)

    async def _verify_signature(self, spec: SandboxSpec) -> None:
        """An image's attestation, checked against the digests and layers the daemon reports for
        the image now (never a tag or a label): ``image_unsigned`` and the other ``ImageTrust`` reasons refuse it
        before anything is created from it."""
        trust, image = self._image_trust, spec.image_digest
        kind = trust.kind_of(image) if trust is not None else None
        if trust is None or kind is None:
            return
        digests = await self._provider.image_digests(image)
        layers = await self._provider.image_layers(image)
        # The bases' layers are read only for a check that compares an image against them.
        bases = (
            {base: await self._provider.image_layers(base) for base in sorted(trust.base_images) if base != image}
            if trust.needs_bases(kind)
            else {}
        )
        try:
            verified = trust.verify(ImageFacts(image, digests, layers), bases)
        except ArtefactUntrusted as refused:
            # One code for the run's ending (a person reads "ask your admin"); the cause is the operator's, logged.
            self._log.warning("sandbox.image_untrusted", **_log_fields(spec.labels, image=image, reasonCode=refused.reason))
            raise self._refuse(ImageNotAllowed(str(refused), reason=IMAGE_UNTRUSTED), spec) from None
        if verified is None:
            return
        attestation = verified.attestation
        previous = self._trusted_images.get(image)
        if previous is not None and previous.digest != attestation.digest:
            # D4: a pinned image that changed is logged as what moved, for the operator.
            self._log.warning("sandbox.image_changed", image=image, changes=registration_diff(previous, attestation))
        self._trusted_images[image] = attestation
        self._log.info("sandbox.image_verified", image=image, keyId=verified.key_id)

    async def _vouch(self, spec: SandboxSpec, container_id: str, **fields: str | int | bool) -> dict[str, str]:
        """The pinned binaries of the image this container runs (its id, never the tag that named it), checked
        once per image id; ``binary_mismatch`` refuses every sandbox of an image that does not match its pins.
        A spec pinned to a digest (C2.20) is first held to it: ``image_digest_mismatch`` refuses a container made
        from any other image, whatever the tag names now (a rebuilt image, or a warm sandbox of the old one)."""
        pinned_binaries = self._binaries is not None and self._binaries.pinned(spec.image_digest)
        if spec.image_pin is None and not pinned_binaries:
            return {}
        inspected = await self._blocking(self._client().api.inspect_container, container_id)
        image_id = cast("str", cast("dict[str, Any]", inspected)["Image"])
        if spec.image_pin is not None and spec.image_pin != image_id:
            answers = await self._provider.image_digests(image_id)
            if spec.image_pin not in answers:
                self._log.warning(
                    "sandbox.image_digest_mismatch", **_log_fields(spec.labels, **fields), pinned=spec.image_pin
                )
                raise self._refuse(
                    ImageNotAllowed(
                        f"image {spec.image_digest!r} is not the pinned {spec.image_pin}", reason=IMAGE_DIGEST_MISMATCH
                    ),
                    spec,
                )
        if self._binaries is None or not pinned_binaries:
            return {}
        try:
            observed = await self._binaries.verify(spec.image_digest, image_id, **fields)
        except SandboxRefusal as refused:
            raise self._refuse(refused, spec) from None
        return vouched_binaries(observed, spec, writable=(_PIDFILE_DIR,))

    def _helper_labels(self) -> dict[str, str]:
        return {
            **self._owned,
            self._labels.role: "helper",
            self._labels.created: str(int(self._clock.now().timestamp())),
        }

    async def _open_egress_cidrs(self, spec: SandboxSpec) -> list[str]:
        # The bridge's own subnets are blocked too (gateway, sibling sandboxes): unread, nothing is safe to join.
        cidrs = await self._provider.egress_network_cidrs(self._egress_network or "")
        if not cidrs:
            raise self._refuse(
                PolicyUnenforceable("the egress network's subnet could not be read", reason=EGRESS_FIREWALL_UNAVAILABLE),
                spec,
            )
        return cidrs

    async def _install_egress_firewall(
        self, spec: SandboxSpec, sandbox_id: str, container_id: str, egress_cidrs: list[str]
    ) -> None:
        """Main #3780's rules in the sandbox's network namespace, installed by a one-shot root helper with
        ``NET_ADMIN`` alone; the sandbox never holds a capability, so its code can neither change nor bypass them."""
        # The deployment's own image, never the run's: a sandbox image of a foothold's making would give its own
        # `sh` root and NET_ADMIN in the namespace (R44STOP-FU-3).
        image = self._helper_image
        if image is None:
            raise self._refuse(
                PolicyUnenforceable("no egress firewall image is configured", reason=EGRESS_FIREWALL_UNAVAILABLE), spec
            )
        if not await self._provider.ensure_image(image):
            await self._provider.pull_image(image)
        token = new_firewall_token()
        script = build_firewall_rules_script(egress_cidrs, self._egress_allow_cidrs, token=token)
        host_config = self._client().api.create_host_config(
            cap_drop=["ALL"],
            cap_add=[FIREWALL_HELPER_CAP],
            read_only=True,
            tmpfs={"/run": "size=1m,mode=755,noexec,nosuid,nodev"},
            security_opt=["no-new-privileges:true"],
            pids_limit=16,
            mem_limit=64 * 1024 * 1024,
            memswap_limit=64 * 1024 * 1024,
            nano_cpus=int(0.25 * 1e9),
            network_mode=f"container:{container_id}",
        )
        command = ["sh", "-c", script, FIREWALL_HELPER_ARGV0]
        self._policy.check_firewall_helper(
            image=image,
            user="0",
            host_config=host_config,
            sandbox_container=container_id,
            command=command,
            entrypoint=[],
        )
        created = await self._blocking(
            self._client().api.create_container,
            image,
            command=command,
            entrypoint=[],
            user="0",
            labels={
                **self._owned,
                self._labels.role: "firewall",
                self._labels.sandbox_id: sandbox_id,
                self._labels.created: str(int(self._clock.now().timestamp())),
            },
            host_config=host_config,
        )
        helper = cast("str", created["Id"])
        try:
            await self._blocking(self._client().api.start, helper)
            result = await self._blocking(self._client().api.wait, helper, timeout=_FIREWALL_TIMEOUT_S)
            exit_code = int(cast("dict[str, Any]", result).get("StatusCode", -1))
        finally:
            await self._blocking(self._client().api.remove_container, helper, force=True)
        if exit_code != 0:
            raise self._refuse(
                PolicyUnenforceable(
                    f"the egress firewall could not be installed (exit {exit_code}); its image ({image}) needs iptables",
                    reason=EGRESS_FIREWALL_UNAVAILABLE,
                ),
                spec,
            )

    async def _network(self, spec: SandboxSpec) -> str:
        if spec.egress.default is EgressDefault.ALLOW and self._egress_network:
            return await self._provider.ensure_egress_network(self._egress_network)
        return "none"

    async def _mounts(
        self, spec: SandboxSpec, sandbox_id: str, labels: Mapping[str, str]
    ) -> tuple[list[dict[str, object]], tuple[tuple[str, str], ...]]:
        # NoCopy: an empty volume would otherwise take the image's ownership of the
        # target path (root, for a working directory Docker creates), locking the sandbox user out.
        mounts: list[dict[str, object]] = []
        copied: list[tuple[str, str]] = []
        sources = [m.source for m in spec.mounts if m.source is not None]
        if sources and not self._copy_mounts:
            await self._ensure_source_dirs(spec.image_digest, sources)
        for index, mount in enumerate(spec.mounts):
            if mount.source is not None and not self._copy_mounts:
                mounts.append(
                    {
                        "Type": "volume",
                        "Source": self._data_volume,
                        "Target": mount.target,
                        "ReadOnly": mount.read_only,
                        "VolumeOptions": {"Subpath": self._volume_key(mount.source), "NoCopy": True},
                    }
                )
                continue
            name = f"{self._volume_prefix}-{sandbox_id}-{index}"
            mb = mount.size_mb or spec.resources.scratch_mb
            # Root's, with the sticky bit: the sandbox user adds and removes its own entries, never the ones the
            # controller placed (an engine's configuration, SEC1), nor renames a folder of root's away. A read-only
            # mount has no writer inside, so its root is plain 0755: an agent that trusts a config only in folders
            # nobody else can write (Gemini CLI's system settings, WP-59) trusts one seeded there.
            mode = "0755" if mount.read_only else "1777"
            await self._blocking(
                self._client().api.create_volume,
                name=name,
                driver="local",
                driver_opts={
                    "type": "tmpfs",
                    "device": "tmpfs",
                    "o": f"size={mb}m,nr_inodes={mb * TMPFS_INODES_PER_MB},uid=0,gid=0,mode={mode}",
                },
                labels={**labels, self._labels.role: "volume"},
            )
            mounts.append(
                {
                    "Type": "volume",
                    "Source": name,
                    "Target": mount.target,
                    "ReadOnly": mount.read_only,
                    "VolumeOptions": {"NoCopy": True},
                }
            )
            if mount.source is not None:
                copied.append((mount.source, mount.target))
        return mounts, tuple(copied)

    def _seeder_name(self, sandbox_id: str) -> str:
        return f"{self._volume_prefix}-seed-{sandbox_id}"

    async def _make_seeder(self, spec: SandboxSpec, sandbox_id: str) -> None:
        """The sandbox's seeder, when it has read-only scratch mounts: never started, those volumes read-write at
        ``/seed/<index>``. A seed is then one archive write into it, not a helper made and removed per seed (PF-01:
        1.9 s on a loaded host). One already there (a concurrent seed made it) is the one used. It carries none of
        the run's labels: whatever finds a run's containers by them finds its sandbox alone."""
        seeded = [index for index, mount in enumerate(spec.mounts) if mount.read_only and mount.source is None]
        if not seeded:
            return
        host_config = self._client().api.create_host_config(
            mounts=[
                {
                    "Type": "volume",
                    "Source": f"{self._volume_prefix}-{sandbox_id}-{index}",
                    "Target": f"{_SEED_MOUNT}/{index}",
                    "VolumeOptions": {"NoCopy": True},
                }
                for index in seeded
            ],
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=8,
            mem_limit=64 * 1024 * 1024,
            memswap_limit=64 * 1024 * 1024,
            nano_cpus=int(0.1 * 1e9),
            network_mode="none",
        )
        self._policy.check(image=spec.image_digest, user=SANDBOX_USER, host_config=host_config)
        try:
            await self._blocking(
                self._client().api.create_container,
                spec.image_digest,
                command=["true"],
                user=SANDBOX_USER,
                labels={
                    **self._owned,
                    self._labels.created: str(int(self._clock.now().timestamp())),
                    self._labels.role: "seeder",
                    self._labels.seeds: sandbox_id,
                },
                host_config=host_config,
                network_disabled=True,
                name=self._seeder_name(sandbox_id),
            )
        except Exception as exc:
            if not _is_conflict(exc):
                raise

    async def _remove_seeder(self, sandbox_id: str) -> None:
        listed = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.seeds}={sandbox_id}"]},
        )
        for info in cast("list[dict[str, Any]]", listed):
            try:
                await self._remove_container(cast("str", info["Id"]))
            except Exception as exc:
                if not _is_not_found(exc):
                    raise

    async def _reap_orphan_seeders(self, filters: list[str], now: int, older_than_s: int, live: set[str]) -> int:
        """Seeders whose sandbox is gone, before the volumes they hold: the daemon keeps a volume a container mounts."""
        listed = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=seeder", *filters]},
        )
        removed = 0
        for info in cast("list[dict[str, Any]]", listed):
            labels = cast("dict[str, str]", info.get("Labels") or {})
            if labels.get(self._labels.seeds, "") in live:
                continue
            if now - int(labels.get(self._labels.created, "0") or 0) < older_than_s:
                continue
            try:
                await self._remove_container(cast("str", info["Id"]))
                removed += 1
            except Exception as exc:
                if not _is_not_found(exc):
                    raise
        return removed

    async def _with_helper(self, image: str, work: Any) -> Any:  # noqa: ANN401
        """Run ``work(container_id)`` against a stopped container that mounts the data volume.

        The daemon's archive API writes into volumes of a container that never
        starts, so the broker reaches its volumes without a bind mount
        and without running anything.
        """
        host_config = self._client().api.create_host_config(
            mounts=[
                {
                    "Type": "volume",
                    "Source": self._data_volume,
                    "Target": _HELPER_MOUNT,
                    "VolumeOptions": {"NoCopy": True},
                }
            ],
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=8,
            mem_limit=64 * 1024 * 1024,
            memswap_limit=64 * 1024 * 1024,
            nano_cpus=int(0.1 * 1e9),
            network_mode="none",
        )
        self._policy.check(image=image, user=SANDBOX_USER, host_config=host_config)
        created = await self._blocking(
            self._client().api.create_container,
            image,
            command=["true"],
            user=SANDBOX_USER,
            labels={**self._owned, self._labels.role: "helper",
                    self._labels.created: str(int(self._clock.now().timestamp()))},
            host_config=host_config,
            network_disabled=True,
        )
        helper = cast("str", created["Id"])
        try:
            return await work(helper)
        finally:
            await self._blocking(self._client().api.remove_container, helper, force=True)

    async def _ensure_source_dirs(self, image: str, sources: Sequence[str]) -> None:
        directories: set[str] = set()
        for source in sources:
            parts = self._volume_key(source).split("/")
            directories.update("/".join(parts[: i + 1]) for i in range(len(parts)))
        archive = _dirs_tar(sorted(directories, key=lambda d: (d.count("/"), d)))

        async def put(helper: str) -> None:
            await self._blocking(self._client().api.put_archive, helper, _HELPER_MOUNT, archive)

        await self._with_helper(image, put)

    async def _copy_in(self, sandbox: _Sandbox, source: str, target: str) -> None:
        await self._ensure_source_dirs(sandbox.spec.image_digest, [source])

        async def pull(helper: str) -> None:
            stream, _ = await self._blocking(self._client().api.get_archive, helper, f"{_HELPER_MOUNT}/{self._volume_key(source)}")
            with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as data:
                await self._blocking(_strip_top, stream, data)
                await self._blocking(self._client().api.put_archive, sandbox.container_id, target, data)

        await self._with_helper(sandbox.spec.image_digest, pull)

    async def _copy_out(self, sandbox: _Sandbox, source: str, target: str) -> None:
        stream, _ = await self._blocking(self._client().api.get_archive, sandbox.container_id, target)
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as data:
            await self._blocking(_strip_top, stream, data)

            async def push(helper: str) -> None:
                ok = await self._blocking(
                    self._client().api.put_archive, helper, f"{_HELPER_MOUNT}/{self._volume_key(source)}", data
                )
                if not ok:
                    raise SandboxError(f"could not copy {target} back to its persistent folder")

            await self._with_helper(sandbox.spec.image_digest, push)

    def _volume_key(self, source: str) -> str:
        return f"{self._install}/{source}" if self._install is not None else source

    async def erase_session(self, *, org_id: str, session_id: str) -> int:
        """Remove this install's persistent folders of one session (``<install>/<org>/<session>``); 1 when
        there was one. Ids are single safe path segments: nothing outside the session's folder is reached."""
        if not (is_safe_segment(org_id) and is_safe_segment(session_id)):
            raise InvalidSandboxRequest("org and session ids must be single safe path segments")
        if not self._data_volume:
            return 0
        folder = f"{_HELPER_MOUNT}/{self._volume_key(f'{org_id}/{session_id}')}"
        image = self._erase_image()
        if image is None:
            raise PolicyUnenforceable("no image to run the eraser with", reason="image_missing")
        exit_code = await self._run_helper(image, ["sh", "-c", _ERASE, "erase", folder])
        if exit_code not in (0, 3):
            raise SandboxError(f"erasing a session folder exited {exit_code}")
        self._log.info("broker.session_erased", orgId=org_id, sessionId=session_id, found=exit_code == 0)
        return 1 if exit_code == 0 else 0

    async def erase_org(self, *, org_id: str) -> OrgSandboxErasure:
        """End this install's sandboxes of an erased org (through the reap, so a kept sign-in signs out first) and
        remove its folder tree ``<install>/<org>`` (CLEANUP-G1)."""
        if not is_safe_segment(org_id):
            raise InvalidSandboxRequest("the org id must be a single safe path segment")
        install = self._owned.get(self._labels.install)
        sandboxes = await self.reap(
            labels={"orgId": org_id, **({self._labels.install: install} if install else {})}, older_than_s=0,
        )
        folders = 0
        if self._data_volume:
            image = self._erase_image()
            if image is None:
                raise PolicyUnenforceable("no image to run the eraser with", reason="image_missing")
            folder = f"{_HELPER_MOUNT}/{self._volume_key(org_id)}"
            exit_code = await self._run_helper(image, ["sh", "-c", _ERASE, "erase", folder])
            if exit_code not in (0, 3):
                raise SandboxError(f"erasing an org's folders exited {exit_code}")
            folders = 1 if exit_code == 0 else 0
        self._log.info("broker.org_erased", orgId=org_id, sandboxes=sandboxes, folders=folders)
        return OrgSandboxErasure(sandboxes, folders)

    def _erase_image(self) -> str | None:
        allowed = self._policy.allowed_images
        return self._helper_image or (min(allowed) if allowed else None)

    async def _run_helper(self, image: str, command: list[str]) -> int:
        """Run ``command`` once in a helper with the data volume at ``/data`` (no network, sandbox user)."""
        host_config = self._client().api.create_host_config(
            mounts=[{"Type": "volume", "Source": self._data_volume, "Target": _HELPER_MOUNT, "VolumeOptions": {"NoCopy": True}}],
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=16,
            mem_limit=64 * 1024 * 1024,
            memswap_limit=64 * 1024 * 1024,
            nano_cpus=int(0.25 * 1e9),
            network_mode="none",
        )
        self._policy.check(image=image, user=SANDBOX_USER, host_config=host_config)
        created = await self._blocking(
            self._client().api.create_container,
            image,
            command=command,
            entrypoint=[],
            user=SANDBOX_USER,
            labels={**self._owned, self._labels.role: "helper",
                    self._labels.created: str(int(self._clock.now().timestamp()))},
            host_config=host_config,
            network_disabled=True,
        )
        helper = cast("str", created["Id"])
        try:
            await self._blocking(self._client().api.start, helper)
            result = await self._blocking(self._client().api.wait, helper, timeout=120)
            return int(cast("dict[str, Any]", result).get("StatusCode", -1))
        finally:
            await self._blocking(self._client().api.remove_container, helper, force=True)

    async def release(self, handle: object, *, keep_warm_s: int = 0) -> None:
        sandbox_id = getattr(handle, "id", None)
        if not isinstance(sandbox_id, str) or self.ledger.by_id(sandbox_id) is None:
            return
        if keep_warm_s > 0:
            self.ledger.keep_warm(sandbox_id, keep_warm_s)
            self.meter.kept(sandbox_id)
            self._log.info("sandbox.released", sandboxId=sandbox_id, keepWarmS=keep_warm_s)
            return
        await self._destroy(sandbox_id, reason="released")

    async def sweep_warm(self) -> int:
        await self._retry_removals()
        swept = 0
        for entry in self.ledger.expired_warm():
            # One sandbox whose changes cannot be copied out yet is kept for the next sweep, not the rest.
            with contextlib.suppress(SandboxReleaseIncomplete):
                # Under the key's lock: a turn taking the sandbox back meanwhile is never handed one being ended.
                async with self.ledger.lock(entry.key):
                    if self.ledger.by_id(entry.sandbox_id) is not entry or entry not in self.ledger.expired_warm():
                        continue
                    await self._destroy(entry.sandbox_id, reason="warm_expired")
                swept += 1
        return swept + await self._end_orphans()

    async def _end_orphans(self) -> int:
        """Sandboxes with a farewell that this process neither holds nor keeps warm (left from before a broker
        restart, whose keep-warm time it forgot): ended, farewell first, once they stayed so for the farewell's
        ``orphan_after_s``. A turn that takes one back before then adopts it as always."""
        if not any(f.orphan_after_s is not None for f in self._farewells.values()):
            return 0
        found = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=sandbox", FAREWELL_LABEL]},
        )
        now = int(self._clock.now().timestamp())
        seen: dict[str, int] = {}
        ended = 0
        for info in cast("list[dict[str, Any]]", found):
            labels = cast("dict[str, str]", info.get("Labels") or {})
            sandbox_id = labels.get(self._labels.sandbox_id, "")
            farewell = self._farewells.get(labels.get(FAREWELL_LABEL, ""))
            if not sandbox_id or sandbox_id in self._sandboxes or farewell is None or farewell.orphan_after_s is None:
                continue
            first = seen[sandbox_id] = self._orphans.get(sandbox_id, now)
            if now - first < farewell.orphan_after_s:
                continue
            async with self.ledger.lock(labels.get(self._labels.key, sandbox_id)):
                if sandbox_id in self._sandboxes:
                    continue
                await self._farewell(sandbox_id, cast("str", info["Id"]), labels, reason="orphaned")
                await self._forget_and_remove(sandbox_id, cast("str", info["Id"]))
            del seen[sandbox_id]
            ended += 1
        self._orphans = seen
        return ended

    async def _farewell(self, sandbox_id: str, container_id: str, labels: Mapping[str, str], *, reason: str) -> None:
        """The farewell ``labels`` name, if any, before the broker removes the sandbox on its own; never raises. The
        audit lines hold ids and the outcome only, never what the command printed."""
        farewell = self._farewells.get(labels.get(FAREWELL_LABEL, ""))
        if farewell is None:
            return
        outcome = await run_farewell(self._provider, container_id, farewell)
        ids = {
            "farewell": labels.get(FAREWELL_LABEL, ""),
            "sandboxId": sandbox_id,
            "orgId": org_of(labels) or "",
            "sessionId": labels.get("sessionId", ""),
        }
        self._log.info("engine_account.signed_out", by="broker", reason=reason, outcome=outcome, **ids)
        if outcome in _UNREVOKED:
            # The login state goes with the sandbox, but the vendor may still hold the session: a stopped container
            # (a host reboot) cannot run the logout.
            self._log.warning("engine_account.vendor_sign_out_needed", reason=reason, outcome=outcome, **ids)

    async def _destroy(self, sandbox_id: str, *, reason: str) -> None:
        _ = self.meter.ended(sandbox_id)
        sandbox = self._sandboxes.pop(sandbox_id, None)
        entry = self.ledger.forget(sandbox_id)
        if sandbox is None:
            return
        if reason != "released":
            # Its holder released it and ended what it had to; one that expired had nobody left to (GAPS-2).
            await self._farewell(sandbox_id, sandbox.container_id, sandbox.spec.labels, reason=reason)
        try:
            await self._copy_back(sandbox)
        except Exception as exc:
            # The changes exist only in the container now: keep it, its volumes and its ledger entry, and hand it
            # to the warm sweep, which copies out again before removing anything.
            self._sandboxes[sandbox_id] = sandbox
            if entry is not None:
                _ = self.ledger.record(entry.key, sandbox_id, LeaseRef(entry.holder, entry.fence))
                self.ledger.keep_warm(sandbox_id, 0)
            self._log.error(
                "sandbox.copy_out_failed", exc=exc, sandboxId=sandbox_id, errorType=type(exc).__name__, kept=True
            )
            raise SandboxReleaseIncomplete(
                f"sandbox {sandbox_id} is kept: its persistent changes could not be copied out yet"
            ) from exc
        try:
            await self._remove(sandbox_id, sandbox.container_id)
        except Exception as exc:
            # The release is done for its caller; a daemon slow to remove is the broker's to retry (QA31F).
            self._removals[sandbox_id] = sandbox.container_id
            self._log.warning("sandbox.remove_deferred", sandboxId=sandbox_id, errorType=type(exc).__name__)
            return
        self._log.info("sandbox.released", sandboxId=sandbox_id, reason=reason)

    async def _retry_removals(self) -> None:
        for sandbox_id, container_id in list(self._removals.items()):
            try:
                await self._remove(sandbox_id, container_id)
            except Exception as exc:
                self._log.warning("sandbox.remove_deferred", sandboxId=sandbox_id, errorType=type(exc).__name__)
                continue
            del self._removals[sandbox_id]
            self._log.info("sandbox.released", sandboxId=sandbox_id, reason="removal_retried")

    async def _copy_back(self, sandbox: _Sandbox) -> None:
        for source, target in sandbox.copied:
            if not any(m.target == target and m.read_only for m in sandbox.spec.mounts):
                await self._copy_out(sandbox, source, target)

    async def _remove(self, sandbox_id: str, container_id: str | None) -> None:
        # A create that timed out client-side may still have made the container: find it by label.
        container_ids = [container_id] if container_id is not None else await self._containers_of(sandbox_id)
        for found in container_ids:
            try:
                await self._remove_container(found)
            except Exception as exc:
                if not _is_not_found(exc):
                    self._log.error("sandbox.remove_failed", exc=exc, sandboxId=sandbox_id, errorType=type(exc).__name__)
                    raise
        if sandbox_id and self._egress is not None:
            await self._egress.detach(sandbox_id)
        if sandbox_id:
            await self._remove_seeder(sandbox_id)
            await self._remove_volumes([f"{self._labels.sandbox_id}={sandbox_id}"])

    async def _remove_container(self, container_id: str) -> None:
        try:
            await self._blocking(self._client().api.remove_container, container_id, force=True)
        except Exception as exc:
            if not _is_removal_running(exc):
                raise
            # The daemon goes on removing after the client gives up, and refuses a second request meanwhile.
            await self._blocking(self._client().api.wait, container_id, timeout=REMOVE_WAIT_S, condition="removed")

    async def _containers_of(self, sandbox_id: str) -> list[str]:
        if not sandbox_id:
            return []
        listed = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.sandbox_id}={sandbox_id}"]},
        )
        return [cast("str", info["Id"]) for info in cast("list[dict[str, Any]]", listed)]

    async def _remove_volumes(self, label_filters: list[str]) -> int:
        listed = await self._blocking(
            self._client().api.volumes, filters={"label": [f"{self._labels.managed}=1", *label_filters]}
        )
        removed = 0
        for volume in _volumes_of(listed):
            try:
                await self._blocking(self._client().api.remove_volume, volume["Name"], force=True)
                removed += 1
            except Exception as exc:
                if not _is_not_found(exc):
                    raise
        return removed

    async def reap(self, *, labels: Mapping[str, str], older_than_s: int) -> int:
        """Cold sandboxes by their labels, warm-bound ones by their binding marker (age from the bind), and
        unbound or dead-claimed warm ones by their own labels (pool labels only, never a run's)."""
        filters = [f"{k}={v}" for k, v in labels.items()]
        now = int(self._clock.now().timestamp())

        def old(item_labels: Mapping[str, str]) -> bool:
            return now - int(item_labels.get(self._labels.created, "0") or 0) >= older_than_s

        found = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=sandbox", *filters]},
        )
        reaped = 0
        for info in cast("list[dict[str, Any]]", found):
            container_labels = cast("dict[str, str]", info.get("Labels") or {})
            if not old(container_labels):
                continue
            sandbox_id = container_labels.get(self._labels.sandbox_id, "")
            # A lifetime past or a crash loop's reap: whoever held it is gone, so the broker says its farewell (GAPS-2).
            await self._farewell(sandbox_id, cast("str", info["Id"]), container_labels, reason="reaped")
            await self._forget_and_remove(sandbox_id, cast("str", info["Id"]))
            reaped += 1
        for marker in await self._markers(filters):
            marker_labels = cast("dict[str, str]", marker.get("Labels") or {})
            if old(marker_labels):
                await self._forget_and_remove(marker_labels.get(self._labels.sandbox_id, ""), None)
                reaped += 1
        reaped += await self._reap_warm(filters, old)
        live = await self._live_sandbox_ids()
        _ = await self._reap_orphan_seeders([], now, older_than_s, live)
        reaped_volumes = await self._reap_orphan_volumes(filters, now, older_than_s, live)
        reaped_networks = (
            await self._egress.reap_orphans(filters, now, older_than_s, live) if self._egress is not None else 0
        )
        self._log.info(
            "sandbox.reaped",
            **_log_fields(
                labels,
                count=reaped,
                orphanVolumes=reaped_volumes,
                orphanNetworks=reaped_networks,
                olderThanS=older_than_s,
            ),
        )
        return reaped

    async def reap_expired(self) -> int:
        """Sandboxes older than the lifetime their own ``LIFETIME_LABEL`` gives, whoever holds them: the broker's
        backstop for a holder that died before releasing (its sweep runs this; idempotent)."""
        found = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=sandbox", LIFETIME_LABEL]},
        )
        now = int(self._clock.now().timestamp())
        due: set[int] = set()
        for info in cast("list[dict[str, Any]]", found):
            labels = cast("dict[str, str]", info.get("Labels") or {})
            lifetime = labels.get(LIFETIME_LABEL, "")
            if lifetime.isdigit() and now - int(labels.get(self._labels.created, "0") or 0) >= int(lifetime):
                due.add(int(lifetime))
        reaped = 0
        for lifetime in sorted(due):
            reaped += await self.reap(labels={LIFETIME_LABEL: str(lifetime)}, older_than_s=lifetime)
        return reaped

    async def reconcile(self) -> int:
        """Once at start: this install's containers that can never run again (stopped sandboxes and warm ones, after
        a host reboot or a crash), helper and firewall containers a crashed broker left, and the scratch volumes,
        binding markers and networks no container is left for. A running sandbox stays: a turn adopts it by key, and
        the run sweeper, its lifetime or the orphan farewell end it. Only resources labelled with this broker's
        install id are touched; returns the containers removed."""
        install = self._owned.get(self._labels.install)
        if not install:
            return 0
        own = [f"{self._labels.install}={install}"]
        listed = await self._blocking(
            self._client().api.containers, all=True, filters={"label": [f"{self._labels.managed}=1", *own]}
        )
        now = int(self._clock.now().timestamp())
        removed = 0
        for info in cast("list[dict[str, Any]]", listed):
            labels = cast("dict[str, str]", info.get("Labels") or {})
            role = labels.get(self._labels.role, "")
            sandbox_id = labels.get(self._labels.sandbox_id, "")
            container_id = cast("str", info["Id"])
            if role in ("helper", "firewall"):
                if now - int(labels.get(self._labels.created, "0") or 0) < _TRANSIENT_GRACE_S:
                    continue
                await self._remove_container(container_id)
            elif role in ("sandbox", "warm") and info.get("State") != "running" and sandbox_id not in self._sandboxes:
                async with self.ledger.lock(labels.get(self._labels.key, sandbox_id)):
                    await self._farewell(sandbox_id, container_id, labels, reason="reconciled")
                    await self._forget_and_remove(sandbox_id, container_id)
            else:
                continue
            removed += 1
        live = await self._live_sandbox_ids()
        removed += await self._reap_orphan_seeders(own, now, 0, live)
        volumes = await self._reap_orphan_volumes(own, now, 0, live)
        markers = 0
        for marker in await self._markers(own):
            if cast("dict[str, str]", marker.get("Labels") or {}).get(self._labels.sandbox_id, "") in live:
                continue
            await self._blocking(self._client().api.remove_volume, marker["Name"], force=True)
            markers += 1
        networks = await self._egress.reap_orphans(own, now, 0, live) if self._egress is not None else 0
        self._log.info(
            "broker.reconciled",
            containers=removed,
            orphanVolumes=volumes,
            orphanMarkers=markers,
            orphanNetworks=networks,
        )
        return removed

    async def _forget_and_remove(self, sandbox_id: str, container_id: str | None) -> None:
        _ = self.meter.ended(sandbox_id)
        _ = self._sandboxes.pop(sandbox_id, None)
        _ = self.ledger.forget(sandbox_id)
        if sandbox_id:
            self.pool.forget(sandbox_id)
        await self._remove(sandbox_id, container_id)

    async def _reap_warm(self, filters: list[str], old: Callable[[Mapping[str, str]], bool]) -> int:
        found = await self._blocking(
            self._client().api.containers,
            all=True,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=warm", *filters]},
        )
        reaped = 0
        for info in cast("list[dict[str, Any]]", found):
            container_labels = cast("dict[str, str]", info.get("Labels") or {})
            sandbox_id = container_labels.get(self._labels.sandbox_id, "")
            if not sandbox_id or not old(container_labels):
                continue
            if self._unclaimed(info):
                shape = WarmShape.from_wire(container_labels[self._labels.shape])
                if not await self.claim_warm(WarmSandbox(sandbox_id, shape, 0.0)):
                    continue
            elif await self._markers([f"{self._labels.sandbox_id}={sandbox_id}"]):
                # Bound to a run: its marker decides (above), by the bind's age and the run's labels.
                continue
            await self._forget_and_remove(sandbox_id, cast("str", info["Id"]))
            reaped += 1
        return reaped

    async def _live_sandbox_ids(self) -> set[str]:
        listed = await self._blocking(
            self._client().api.containers, all=True, filters={"label": [f"{self._labels.managed}=1"]}
        )
        ids = {
            cast("dict[str, str]", info.get("Labels") or {}).get(self._labels.sandbox_id, "")
            for info in cast("list[dict[str, Any]]", listed)
        }
        return (ids | set(self._sandboxes)) - {""}

    async def _reap_orphan_volumes(self, filters: list[str], now: int, older_than_s: int, live: set[str]) -> int:
        listed = await self._blocking(
            self._client().api.volumes,
            filters={"label": [f"{self._labels.managed}=1", f"{self._labels.role}=volume", *filters]},
        )
        removed = 0
        for volume in _volumes_of(listed):
            volume_labels = cast("dict[str, str]", volume.get("Labels") or {})
            sandbox_id = volume_labels.get(self._labels.sandbox_id, "")
            if sandbox_id in live:
                continue
            if now - int(volume_labels.get(self._labels.created, "0") or 0) < older_than_s:
                continue
            try:
                await self._blocking(self._client().api.remove_volume, volume["Name"], force=True)
                removed += 1
            except Exception as exc:
                if not _is_not_found(exc):
                    raise
        return removed

    def live(self, sandbox_id: str) -> _Sandbox:
        self.ledger.held(sandbox_id)
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxNotFound(f"sandbox {sandbox_id} is not held")
        if sandbox.over_quota:
            raise self._quota_refusal()
        return sandbox

    async def exec_in(
        self, sandbox: _Sandbox, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool
    ) -> DockerProcess:
        if not argv:
            raise InvalidSandboxRequest("argv is empty")
        pidfile = f"{_PIDFILE_DIR}/.sbx-{self._ids.new_id('exec')}.pid"
        wrapped = ["/bin/sh", "-c", 'echo $$ > "$0" && exec "$@"', pidfile, *argv]
        try:
            created = await self._blocking(
                self._client().api.exec_create,
                sandbox.container_id,
                wrapped,
                stdin=stdin,
                stdout=True,
                stderr=True,
                environment=dict(env),
                user=SANDBOX_USER,
            )
        except Exception as exc:
            if _is_not_found(exc) or _is_conflict(exc):
                raise SandboxNotFound(f"sandbox {sandbox.sandbox_id} is gone") from exc
            raise
        exec_id = cast("str", created["Id"])
        reader, writer = await start_exec(self.endpoint(), exec_id)
        return DockerProcess(self, sandbox, exec_id, pidfile, reader, writer, stdin_open=stdin)

    async def exit_code(self, exec_id: str) -> int:
        while True:
            info = await self._blocking(self._client().api.exec_inspect, exec_id)
            if not info.get("Running"):
                code = info.get("ExitCode")
                return code if isinstance(code, int) else -1
            await asyncio.sleep(_EXIT_POLL_S)

    async def send_signal(self, sandbox: _Sandbox, pidfile: str, sig: int) -> None:
        argv = ["/bin/sh", "-c", 'test -s "$1" && kill -"$0" "$(cat "$1")"', str(int(sig)), pidfile]
        for _ in range(_SIGNAL_RETRIES):
            try:
                result = await self._blocking(
                    self._client().api.exec_create, sandbox.container_id, argv, user=SANDBOX_USER
                )
            except Exception as exc:
                # A killed container answers 409 (not running) or 404: the sandbox is gone, as exec_in says.
                if _is_not_found(exc) or _is_conflict(exc):
                    raise SandboxNotFound(f"sandbox {sandbox.sandbox_id} is gone") from exc
                raise
            await self._blocking(self._client().api.exec_start, result["Id"])
            info = await self._blocking(self._client().api.exec_inspect, result["Id"])
            if info.get("ExitCode") == 0:
                return
            # The pidfile is written as the process starts; give a just-started exec a moment.
            await asyncio.sleep(0.05)

    async def put_archive(self, sandbox: _Sandbox, dest: str, stream: AsyncIterator[bytes]) -> None:
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as spooled:
            await _spool_archive(stream, spooled)
            try:
                ok = await self._blocking(self._client().api.put_archive, sandbox.container_id, dest, spooled)
            except Exception as exc:
                raise _archive_error(exc, dest) from exc
        if not ok:
            raise SandboxError(f"could not write the archive to {dest}")

    async def seed_archive(self, sandbox: _Sandbox, dest: str, stream: AsyncIterator[bytes]) -> None:
        """``seed_tar``: the mount's scratch volume is read-write in the sandbox's seeder, read-only in the sandbox.
        A sandbox a broker made before seeders existed gets its seeder at its first seed."""
        mount = sandbox.spec.mount_for(dest)
        if mount is None or not sandbox.spec.seedable(dest):
            raise InvalidSandboxRequest(f"{dest} is not in a read-only scratch mount")
        index = list(sandbox.spec.mounts).index(mount)
        inside = f"{_SEED_MOUNT}/{index}" + posixpath.normpath(dest)[len(mount.target) :]
        seeder = self._seeder_name(sandbox.sandbox_id)
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as spooled:
            await _spool_archive(stream, spooled)
            try:
                ok = await self._blocking(self._client().api.put_archive, seeder, inside, spooled)
            except Exception as exc:
                if not _is_not_found(exc):
                    raise _archive_error(exc, dest) from exc
                await self._make_seeder(sandbox.spec, sandbox.sandbox_id)
                _ = spooled.seek(0)
                try:
                    ok = await self._blocking(self._client().api.put_archive, seeder, inside, spooled)
                except Exception as retried:
                    raise _archive_error(retried, dest) from retried
        if not ok:
            raise SandboxError(f"could not seed the archive into {dest}")

    async def get_archive(self, sandbox: _Sandbox, src: str) -> AsyncIterator[bytes]:
        await self._running(sandbox)
        try:
            stream, _ = await self._blocking(
                self._client().api.get_archive, sandbox.container_id, src, chunk_size=_ARCHIVE_CHUNK
            )
        except Exception as exc:
            raise _archive_error(exc, src) from exc
        return self._drain_running(sandbox, cast("Iterator[bytes]", stream))

    async def hold_flow(self, sandbox: _Sandbox, *, run_id: str, hold: str) -> bool | None:
        """SEC-A-02 through the broker (R38EGRESS-FU-1): ``None`` without an egress proxy behind it."""
        if self._egress is None:
            return None
        return await self._egress.hold_flow(sandbox.sandbox_id, run_id=run_id, hold=hold)

    async def memory_exhausted(self, sandbox: _Sandbox) -> bool:
        """The container's ``State.OOMKilled``: set by the kernel's OOM kill of a process in its cgroup, which under
        gVisor is the Sentry, so the whole sandbox; it stays set, so a later end of the same sandbox reads it too.
        A container that is already gone cannot tell: ``False``."""
        try:
            info = await self._blocking(self._client().api.inspect_container, sandbox.container_id)
        except Exception:
            return False
        return cast("dict[str, Any]", info.get("State") or {}).get("OOMKilled") is True

    async def _running(self, sandbox: _Sandbox) -> None:
        """A stopped container's scratch mounts are gone and Docker still archives it (as empty folders): read as a
        sandbox that is gone, never as a workspace whose files were all deleted."""
        try:
            info = await self._blocking(self._client().api.inspect_container, sandbox.container_id)
        except Exception as exc:
            if _is_not_found(exc):
                raise SandboxNotFound(f"sandbox {sandbox.sandbox_id} is gone") from exc
            raise BrokerUnavailable(f"sandbox {sandbox.sandbox_id} could not be inspected") from exc
        if not cast("dict[str, Any]", info.get("State") or {}).get("Running"):
            raise SandboxNotFound(f"sandbox {sandbox.sandbox_id} is not running")

    async def _drain_running(self, sandbox: _Sandbox, stream: Iterator[bytes]) -> AsyncIterator[bytes]:
        async for chunk in self._drain(stream):
            yield chunk
        # Killed while the archive was read: what was read may be the emptied mount.
        await self._running(sandbox)

    async def _drain(self, stream: Iterator[bytes]) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._blocking(next, stream, None)
            if chunk is None:
                return
            yield chunk


class DockerProcess:
    def __init__(
        self,
        broker: DockerSandboxBroker,
        sandbox: _Sandbox,
        exec_id: str,
        pidfile: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        stdin_open: bool,
    ) -> None:
        self._broker = broker
        self._sandbox = sandbox
        self._exec_id = exec_id
        self._pidfile = pidfile
        self._writer = writer
        self._stdin_open = stdin_open
        self._stdout = ChunkChannel()
        self._stderr = ChunkChannel()
        self._exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._pump = spawn_detached(self._read(reader))

    async def _read(self, reader: asyncio.StreamReader) -> None:
        # Every way out settles the exit, or wait() and signal() would treat a dead exec as live forever.
        try:
            await self._forward(reader)
            code = await self._broker.exit_code(self._exec_id)
        except asyncio.CancelledError:
            _ = self._exit.cancel()
            raise
        except Exception as exc:
            self._exit.set_exception(exc)
        else:
            self._exit.set_result(code)

    async def _forward(self, reader: asyncio.StreamReader) -> None:
        try:
            async for stream, payload in demux(reader):
                await (self._stderr if stream == 2 else self._stdout).put(payload)
        finally:
            self._stdout.close()
            self._stderr.close()
            self._writer.close()

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return aiter(self._stdout)

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return aiter(self._stderr)

    async def write(self, data: bytes) -> None:
        if not self._stdin_open:
            raise InvalidSandboxRequest("stdin is not open")
        self._writer.write(data)
        await self._writer.drain()

    async def close_stdin(self) -> None:
        if self._stdin_open and self._writer.can_write_eof():
            self._writer.write_eof()
        self._stdin_open = False

    async def signal(self, sig: int) -> None:
        if not self._exit.done():
            await self._broker.send_signal(self._sandbox, self._pidfile, sig)

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await asyncio.wait_for(asyncio.shield(self._exit), timeout_s)


class ExecByteStream:
    """A TCP connection inside the sandbox, carried by a relay process's stdin and stdout."""

    def __init__(self, process: DockerProcess, stdout: AsyncIterator[bytes], first: bytes) -> None:
        self._process = process
        self._stdout = stdout
        self._buffer = first

    async def read(self, max_bytes: int = 65536) -> bytes:
        if not self._buffer:
            try:
                self._buffer = await anext(self._stdout)
            except StopAsyncIteration:
                return b""
        chunk, self._buffer = self._buffer[:max_bytes], self._buffer[max_bytes:]
        return chunk

    async def write(self, data: bytes) -> None:
        await self._process.write(data)

    async def write_eof(self) -> None:
        await self._process.close_stdin()

    async def aclose(self) -> None:
        await self._process.signal(signals.SIGKILL)


class DockerSandboxHandle:
    def __init__(self, broker: DockerSandboxBroker, sandbox: _Sandbox) -> None:
        self._broker = broker
        self._sandbox = sandbox

    @property
    def id(self) -> str:
        return self._sandbox.sandbox_id

    @property
    def mode(self) -> RuntimeMode:
        return self._sandbox.spec.mode

    @property
    def container_id(self) -> str:
        return self._sandbox.container_id

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        return self._sandbox.binaries

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> DockerProcess:
        return await self._broker.exec_in(self._broker.live(self.id), argv, env=env, stdin=stdin)

    async def connect(self, port: int) -> ExecByteStream:
        if not 0 < port < 65536:
            raise InvalidSandboxRequest(f"port {port} is out of range")
        relay = await self.exec(["/bin/sh", "-c", _CONNECT, "connect", str(port), _RELAY], env={}, stdin=True)
        stdout = relay.stdout
        try:
            first = await anext(stdout)
        except StopAsyncIteration:
            first = b""
        if not first.startswith(_ACK):
            await relay.wait(timeout_s=10)
            raise SandboxConnectError(f"nothing listens on port {port} in sandbox {self.id}")
        return ExecByteStream(relay, stdout, first[len(_ACK) :])

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        await self._broker.put_archive(self._broker.live(self.id), dest, stream)

    async def memory_exhausted(self) -> bool:
        return await self._broker.memory_exhausted(self._sandbox)

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None:
        return await self._broker.hold_flow(self._sandbox, run_id=run_id, hold=hold)

    async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        await self._broker.seed_archive(self._broker.live(self.id), dest, stream)

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        return await self._broker.get_archive(self._broker.live(self.id), src)


def host_pids_limit(spec: SandboxSpec) -> int:
    """The host cgroup's pids limit for ``spec``: its own under runc, above the Sentry's need under gVisor."""
    pids = spec.resources.pids
    return GVISOR_HOST_PIDS_BASE + GVISOR_HOST_PIDS_PER_TASK * pids if spec.mode is RuntimeMode.GVISOR else pids


def _runtime_args(entry: object) -> object:
    return cast("dict[str, object]", entry).get("runtimeArgs") if isinstance(entry, dict) else None


async def probe(provider: DockerClientProvider, *, withheld: frozenset[str] = frozenset()) -> DockerCapabilities:
    """What this daemon offers, less the runtimes the operator ``withheld``; unreachable is a result, not an error."""
    try:
        client = provider.client
        version = await provider.run_blocking(client.version)
        info = await provider.run_blocking(client.info)
        endpoint = DockerEndpoint.resolve(client.api.api_version)
    except Exception:
        return DockerCapabilities(reachable=False)
    runtimes = cast("dict[str, object]", info.get("Runtimes") or {})
    memory = info.get("MemTotal")
    return DockerCapabilities(
        reachable=True,
        engine_version=str(version.get("Version", "")),
        runtimes=frozenset(runtimes) - withheld,
        exec_transport="unix" if endpoint.unix_path else "tcp",
        withheld=frozenset(runtimes) & withheld,
        unsafe_runsc=unsafe_runsc_args(_runtime_args(runtimes.get("runsc"))),
        runsc_oci_seccomp=runsc_oci_seccomp(_runtime_args(runtimes.get("runsc"))),
        memory_mb=memory // (1024 * 1024) if isinstance(memory, int) else 0,
    )


async def run_farewell(provider: DockerClientProvider, container_id: str, farewell: SandboxFarewell) -> str:
    """Runs ``farewell`` in the container as the sandbox user, bounded by its timeout: ``done``, ``nothing_to_do``,
    ``failed``, ``timeout`` or ``gone``. What it prints is never read (a logout may name the account)."""
    api = provider.client.api
    try:
        # Attached and discarded: a command whose output goes nowhere can fail on its first write (`claude` does).
        created = await provider.run_blocking(
            api.exec_create, container_id, list(farewell.argv), stdout=True, stderr=True,
            environment=dict(farewell.env), user=SANDBOX_USER,
        )
        exec_id = cast("str", created["Id"])
        _ = await asyncio.wait_for(provider.run_blocking(api.exec_start, exec_id), farewell.timeout_s)
        info = cast("dict[str, Any]", await provider.run_blocking(api.exec_inspect, exec_id))
    except TimeoutError:
        # The container is removed next, which ends the command.
        return "timeout"
    except Exception as exc:
        return "gone" if _is_not_found(exc) or _is_conflict(exc) else "failed"
    code = info.get("ExitCode")
    if code == 0:
        return "done"
    return "nothing_to_do" if code == SandboxFarewell.NOTHING_TO_DO else "failed"


def _log_fields(labels: Mapping[str, str], **fields: str | int | bool) -> dict[str, str | int | bool]:
    """Spec labels (the caller's correlation ids) plus the broker's own fields, which win on a clash."""
    merged: dict[str, str | int | bool] = {k: v for k, v in labels.items() if k not in ("event", "exc")}
    merged.update(fields)
    return merged


def _tmp_options(scratch_mb: int) -> str:
    return _TMP_OPTIONS.format(mb=scratch_mb, inodes=scratch_mb * TMPFS_INODES_PER_MB)


def _workdir(spec: SandboxSpec) -> str:
    writable = [m.target for m in spec.mounts if not m.read_only]
    return writable[0] if writable else _PIDFILE_DIR


def _dirs_tar(directories: Sequence[str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for directory in directories:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o750
            info.uid = info.gid = _SANDBOX_UID
            archive.addfile(info)
    return buffer.getvalue()


def _strip_top(chunks: Iterator[bytes], out: IO[bytes], *, max_bytes: int = MAX_ARCHIVE_BYTES) -> None:
    """A ``get_archive`` stream re-rooted at its top directory into ``out`` (rewound), for a ``put_archive`` into
    another path; spooled both ways, so a persistent folder of any size is never held in memory whole.

    The folder is the sandbox's to fill, so the stream is bounded as every archive the broker takes is, and read
    as plain tar: the daemon never compresses one, so a compressed stream is refused rather than inflated."""
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as source:
        for chunk in chunks:
            size += len(chunk)
            if size > max_bytes:
                raise InvalidSandboxRequest(f"archive exceeds {max_bytes} bytes")
            _ = source.write(chunk)
        _ = source.seek(0)
        with tarfile.open(fileobj=source, mode="r|") as reader, tarfile.open(fileobj=out, mode="w") as writer:
            links: set[str] = set()
            for member in reader:
                _, _, rest = member.name.partition("/")
                if not rest or not _kept_member(member, rest.rstrip("/"), links):
                    continue
                member.name = rest
                extracted = reader.extractfile(member) if member.isfile() else None
                writer.addfile(member, extracted)
    _ = out.seek(0)


def _plain_relative(name: str) -> bool:
    return bool(name) and "\x00" not in name and not name.startswith("/") and all(
        part not in ("", ".", "..") for part in name.split("/")
    )


def _kept_member(member: tarfile.TarInfo, rest: str, links: set[str]) -> bool:
    """Whether a member of a sandbox's folder is copied on (SEC-FS-02): the sandbox wrote these names, so a name
    that climbs out, a member written through a link the same archive made, a hard link to anything but a plain
    member of the archive, and anything but a file, folder or link are dropped here, not left to the daemon's own
    checks on a volume that holds every org's folders. A link keeps its target: it is resolved inside a sandbox."""
    if not _plain_relative(rest):
        return False
    parts = rest.split("/")
    if any("/".join(parts[:i]) in links for i in range(1, len(parts))):
        return False
    if member.islnk():
        _, _, target = member.linkname.partition("/")
        if not _plain_relative(target) or target in links:
            return False
        member.linkname = target
    elif member.issym():
        links.add(rest)
    elif not (member.isfile() or member.isdir()):
        return False
    return True


async def _spool_archive(stream: AsyncIterator[bytes], file: IO[bytes]) -> None:
    _ = await spool(stream, file, in_memory_bytes=_ARCHIVE_SPOOL_BYTES, max_bytes=MAX_ARCHIVE_BYTES)


def _volumes_of(listed: object) -> list[dict[str, Any]]:
    volumes = cast("dict[str, object]", listed).get("Volumes") if isinstance(listed, dict) else None
    return cast("list[dict[str, Any]]", volumes) if isinstance(volumes, list) else []


def _is_not_found(exc: Exception) -> bool:
    return type(exc).__name__ in ("NotFound", "ImageNotFound") or getattr(
        getattr(exc, "response", None), "status_code", None
    ) == 404


def _status(exc: Exception) -> object:
    return getattr(getattr(exc, "response", None), "status_code", None)


def _is_conflict(exc: Exception) -> bool:
    return _status(exc) == 409


def _is_removal_running(exc: Exception) -> bool:
    """A container removal the daemon has not finished: the request timed out, or an earlier one is in progress."""
    import requests

    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        return True
    return _is_conflict(exc) and "already in progress" in str(exc)


async def _both[A, B](first: Awaitable[A], second: Awaitable[B]) -> tuple[A, B]:
    """Both at once; neither is left running when the other fails, so a cleanup after finds whatever both made."""
    results = await asyncio.gather(first, second, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return cast("A", results[0]), cast("B", results[1])


def _archive_error(exc: Exception, path: str) -> SandboxError:
    if _is_not_found(exc):
        return SandboxPathNotFound(f"{path} does not exist")
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (400, 403, 409, 500):
        return SandboxError(f"archive refused at {path}: HTTP {status}")
    return BrokerUnavailable(f"archive failed at {path}: {type(exc).__name__}")


__all__ = [
    "CANDIDATE_IMAGE",
    "GVISOR_HOST_PIDS_BASE",
    "GVISOR_HOST_PIDS_PER_TASK",
    "IMAGE_DIGEST_MISMATCH",
    "KEEPALIVE",
    "LABEL_NAMESPACE",
    "REMOVE_WAIT_S",
    "SANDBOX_USER",
    "DockerCapabilities",
    "DockerProcess",
    "DockerSandboxBroker",
    "DockerSandboxHandle",
    "host_pids_limit",
    "probe",
]

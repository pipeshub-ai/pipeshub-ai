"""The hardening every container the Docker broker creates must have, checked before the daemon sees it.

The broker builds its own create requests, so a failure here is a bug, not
user input; checking anyway keeps a future change from quietly weakening
isolation. The rules follow the Docker socket proxy's allow-policy (PR #3782),
which the broker replaces as the only holder of the socket, plus the floor both
hold every sandbox to (``app.sandbox.sandbox_floor``, SEC-A-04): a read-only
root, all capabilities dropped, a non-root user, limits on pids, memory and
CPU; and what the broker adds: volumes only the broker owns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from app.sandbox.sandbox_floor import (
    NO_NEW_PRIVILEGES,
    firewall_rules_script_ok,
    floor_violations,
    root_user,
)
from harness_core.sandbox.errors import SandboxPolicyViolation
from harness_core.workspace.sync.archive import Owner

if TYPE_CHECKING:
    from collections.abc import Callable

# What the broker names and labels everything it creates by, and the Docker API policy proxy in front of its socket
# admits by (R38IDENT-FU-1): both read them from here.
SANDBOX_USER = f"{Owner().uid}:{Owner().gid}"
LABEL_NAMESPACE = "io.pipeshub.sandbox-broker"
VOLUME_PREFIX = "pipeshub-sbx"
DATA_VOLUME = "pipeshub_sandbox_data"
DEFAULT_EGRESS_NETWORK = "pipeshub_sandbox_egress"
# The per-sandbox egress networks (P5.4 §1.1): internal, no host address on the bridge, no masquerade.
EGRESS_NETWORK_OPTIONS = {
    "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
    "com.docker.network.bridge.enable_ip_masquerade": "false",
    "com.docker.network.bridge.enable_icc": "true",
}


def owns_volume(name: str, *, data_volume: str | None, volume_prefix: str) -> bool:
    """A volume the broker may mount: its data volume, or one it named under its prefix."""
    return name == data_volume or name.startswith(volume_prefix + "-")


ALLOWED_RUNTIMES = frozenset({"", "runc", "runsc"})
_MUST_BE_EMPTY = (
    "Binds",
    "VolumesFrom",
    "Devices",
    "DeviceRequests",
    "DeviceCgroupRules",
    "Links",
    "Sysctls",
    "PortBindings",
    "VolumeDriver",
    "CapAdd",
    "CgroupParent",
    "PidMode",
    "UTSMode",
    "UsernsMode",
    # OCI annotations reach the runtime: runsc reads ``dev.gvisor.*`` ones as its own flags and mount hints.
    "Annotations",
)
# runsc arguments a daemon's runtime entry may carry that open the boundary the broker relies on: annotations that
# override flags (any spec could then turn the next one off), the host's network stack or raw sockets past the
# proxy, host sockets and FIFOs, and the test-only switches.
_UNSAFE_RUNSC_ARGS = ("--allow-flag-override", "--net-raw", "--host-uds", "--host-fifo", "--testonly")
_UNSAFE_RUNSC_VALUES = {"--network": frozenset({"host"})}
# Go's boolean flag spellings: a bare ``--oci-seccomp`` is true, and the last occurrence wins.
_GO_TRUE = frozenset({"", "1", "t", "true"})
_GO_FALSE = frozenset({"0", "f", "false"})


def normalize_image(ref: str) -> str:
    """Canonical ``repo:tag`` or ``repo@digest`` so equivalent spellings compare equal."""
    ref = ref.strip()
    if ref.startswith("sha256:"):
        return ref
    if "@" in ref:
        name, digest = ref.split("@", 1)
        suffix = "@" + digest
    else:
        name, suffix = ref, ":latest"
        last = name.rsplit("/", 1)[-1]
        if ":" in last:
            name, tag = name.rsplit(":", 1)
            suffix = ":" + tag
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.removeprefix("library/") + suffix


@dataclass(frozen=True, slots=True)
class HardeningPolicy:
    user: str
    allowed_networks: frozenset[str]
    volume_allowed: Callable[[str], bool]
    allowed_images: frozenset[str] | None = None
    # Networks the broker creates per sandbox (P5.4), recognised by name.
    network_allowed: Callable[[str], bool] | None = None
    # The one image the egress firewall's root helper may run: the deployment's own sandbox image, digest-pinned by
    # install.sh (PIPESHUB_SANDBOX_PINNED). Unset: no helper runs (R44STOP-FU-3).
    firewall_image: str | None = None

    def network_ok(self, network: object) -> bool:
        if network == "none" or network in self.allowed_networks:
            return True
        return isinstance(network, str) and self.network_allowed is not None and self.network_allowed(network)

    def image_allowed(self, image: str) -> bool:
        if self.allowed_images is None:
            return True
        return normalize_image(image) in {normalize_image(i) for i in self.allowed_images}

    def violations(self, *, image: str, user: str, host_config: Mapping[str, object]) -> list[str]:
        found: list[str] = []
        if not self.image_allowed(image):
            found.append(f"image {image!r} is not allowed")
        if user != self.user or root_user(user):
            found.append(f"user {user!r} is not the sandbox user")
        found.extend(_host_violations(host_config, self))
        return found

    def check(self, *, image: str, user: str, host_config: Mapping[str, object]) -> None:
        found = self.violations(image=image, user=user, host_config=host_config)
        if found:
            raise SandboxPolicyViolation("; ".join(found), reason="hardening_policy")

    def check_firewall_helper(
        self,
        *,
        image: str,
        user: str,
        host_config: Mapping[str, object],
        sandbox_container: str,
        command: object,
        entrypoint: object,
        env: object = None,
    ) -> None:
        """The one container that is not the sandbox shape: root with ``NET_ADMIN`` alone, in one sandbox's
        network namespace, running only the broker's rules script (main #3780's firewall, installed from
        outside because the sandbox has no capabilities). Everything else the sandbox policy demands still holds.

        The script is checked against the grammar ``build_firewall_rules_script`` writes (R43SOCK-FU-4), with no
        entrypoint of the image's ahead of it and no environment that could steer it, from ``firewall_image`` alone
        (R44STOP-FU-3)."""
        found: list[str] = []
        if entrypoint != []:
            found.append("the image's entrypoint is not cleared")
        if env:
            found.append("the helper is given an environment")
        argv = command if isinstance(command, list) else []
        if not (
            len(argv) == 4
            and argv[:2] == ["sh", "-c"]
            and argv[3] == FIREWALL_HELPER_ARGV0
            and isinstance(argv[2], str)
            and firewall_rules_script_ok(argv[2])
        ):
            found.append("the command is not the egress firewall's rules script")
        # The helper is root with NET_ADMIN in a sandbox's namespace: from an image a foothold made, its own `sh` and
        # `iptables` would run there. Only the image the deployment ships runs it (R44STOP-FU-3).
        if self.firewall_image is None or normalize_image(image) != normalize_image(self.firewall_image):
            found.append(f"image {image!r} is not the egress firewall's pinned image")
        if not self.image_allowed(image):
            found.append(f"image {image!r} is not allowed")
        if user != "0":
            found.append(f"user {user!r} is not root")
        if _cap_add(host_config) != [FIREWALL_HELPER_CAP]:
            found.append(f"CapAdd is not exactly [{FIREWALL_HELPER_CAP}]")
        if host_config.get("NetworkMode") != f"container:{sandbox_container}":
            found.append(f"network {host_config.get('NetworkMode')!r} is not the sandbox's namespace")
        rest = {k: v for k, v in host_config.items() if k not in ("CapAdd", "NetworkMode")}
        found.extend(_host_violations({**rest, "NetworkMode": "none"}, self))
        if found:
            raise SandboxPolicyViolation("; ".join(found), reason="hardening_policy")


FIREWALL_HELPER_CAP = "NET_ADMIN"
# The helper's $0, which build_firewall_rules_script's fail() line never reads.
FIREWALL_HELPER_ARGV0 = "egress-firewall"


def unsafe_runsc_args(args: object) -> tuple[str, ...]:
    """The daemon's ``runsc`` runtime arguments that weaken the sandbox (empty: safe to offer gVisor)."""
    if not isinstance(args, list):
        return ()
    found: list[str] = []
    items = [str(a) for a in cast("list[object]", args)]
    for index, raw in enumerate(items):
        flag = "--" + raw.lstrip("-").lower()
        name, _, value = flag.partition("=")
        if not value and name in _UNSAFE_RUNSC_VALUES and index + 1 < len(items):
            value = items[index + 1].lower()
        if name.startswith(_UNSAFE_RUNSC_ARGS) and value not in ("false", "none"):
            found.append(raw)
        elif value in _UNSAFE_RUNSC_VALUES.get(name, frozenset()):
            found.append(f"{name}={value}")
    return tuple(found)


def runsc_oci_seccomp(args: object) -> bool:
    """Whether the daemon's ``runsc`` runtime arguments turn on ``--oci-seccomp``: the container's seccomp profile
    (Docker's default) applied to the guest inside the Sentry, so a syscall the profile denies gets its errno there
    as under runc. Read from ``runtimeArgs`` only; a flag set elsewhere (a runsc config file) is not seen."""
    if not isinstance(args, list):
        return False
    on = False
    for raw in (str(a) for a in cast("list[object]", args)):
        name, _, value = ("--" + raw.lstrip("-")).partition("=")
        if name.lower() != "--oci-seccomp":
            continue
        if value.lower() in _GO_TRUE:
            on = True
        elif value.lower() in _GO_FALSE:
            on = False
    return on


def _cap_add(host_config: Mapping[str, object]) -> list[object]:
    cap_add = host_config.get("CapAdd")
    return list(cast("list[object]", cap_add)) if isinstance(cap_add, list) else []


def _host_violations(hc: Mapping[str, object], policy: HardeningPolicy) -> list[str]:
    found: list[str] = []
    if hc.get("Privileged"):
        found.append("privileged")
    found.extend(f"{key} is set" for key in _MUST_BE_EMPTY if hc.get(key))
    if hc.get("PublishAllPorts"):
        found.append("PublishAllPorts is set")
    # SEC-A-04: the floor the legacy socket proxy holds every sandbox to as well.
    found.extend(floor_violations(hc))
    if hc.get("IpcMode") not in (None, "", "private", "none"):
        found.append("IpcMode is shared")
    if hc.get("CgroupnsMode") not in (None, "", "private"):
        found.append("CgroupnsMode is not private")
    if hc.get("Runtime", "") not in ALLOWED_RUNTIMES:
        found.append(f"runtime {hc.get('Runtime')!r} is not allowed")
    restart = hc.get("RestartPolicy")
    if isinstance(restart, Mapping) and cast("Mapping[str, object]", restart).get("Name") not in (None, "", "no"):
        found.append("a restart policy is set")
    network = hc.get("NetworkMode")
    if not policy.network_ok(network):
        found.append(f"network {network!r} is not allowed")
    mounts: object = hc.get("Mounts") or []
    for raw in cast("list[object]", mounts) if isinstance(mounts, list) else [mounts]:
        found.extend(_mount_violations(raw, policy))
    return found


def _mount_violations(raw: object, policy: HardeningPolicy) -> list[str]:
    if not isinstance(raw, Mapping):
        return ["a mount is malformed"]
    mount = cast("Mapping[str, object]", raw)
    kind, source = mount.get("Type"), mount.get("Source")
    if kind == "tmpfs":
        return []
    if kind == "volume" and isinstance(source, str) and policy.volume_allowed(source):
        return []
    return [f"mount {kind!r} from {source!r} is not allowed"]


__all__ = [
    "ALLOWED_RUNTIMES",
    "DATA_VOLUME",
    "DEFAULT_EGRESS_NETWORK",
    "EGRESS_NETWORK_OPTIONS",
    "FIREWALL_HELPER_ARGV0",
    "FIREWALL_HELPER_CAP",
    "LABEL_NAMESPACE",
    "NO_NEW_PRIVILEGES",
    "SANDBOX_USER",
    "VOLUME_PREFIX",
    "HardeningPolicy",
    "owns_volume",
    "normalize_image",
    "runsc_oci_seccomp",
    "unsafe_runsc_args",
]

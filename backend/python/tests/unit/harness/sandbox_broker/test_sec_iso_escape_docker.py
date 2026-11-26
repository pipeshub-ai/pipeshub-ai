"""SEC-ISO-01 and SEC-ISO-07 (C6.05): escape probes from inside a real broker sandbox, on runc and on gVisor.

One probe script runs as the sandbox's user, with no inner sandbox of its own (an engine runs that way: its own
sandbox is off, SEC-ISO-07), and reports what each attempt got; every attempt must end where the broker's hardening
says it does (no capabilities, no new privileges, a syscall filter, read-only root, masked /proc, private
namespaces). The assertions name the security property, not one runtime's errno: gVisor answers some attempts from
its own kernel with another error (``EACCES`` before ``EROFS``, ``EINVAL`` for ``setuid``), hides what the host has
(``/proc/kcore``, the cgroup file) and filters syscalls in its Sentry rather than with the container's seccomp
profile. Without ``--oci-seccomp`` gVisor also lets a process create user and network namespaces of its own, inside
its kernel; a child that does so must gain nothing outside them. With it (R32-SECCOMP), Docker's profile filters the
guest inside the Sentry too: the probes then expect what runc gives, the profile's ``EPERM`` for a syscall it denies
and no new namespace. Which of the two a daemon runs is read from the broker's own report, so the probe asserts the
state it finds and a daemon that changes it is noticed.
"""

from __future__ import annotations

import json
import re
import socket
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.sandbox.types import LeaseRef, RuntimeMode
from harness_core.testing.sandbox.contract import run

if TYPE_CHECKING:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker

    from .conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

EPERM, EACCES, EROFS, ENOENT, EINVAL, ENOSYS = 1, 13, 30, 2, 22, 38

_PROBE = r"""
import ctypes, errno, json, os, socket
libc = ctypes.CDLL(None, use_errno=True)
out = {}

def status(field):
    for line in open("/proc/self/status"):
        if line.startswith(field + ":"):
            return line.split(":", 1)[1].strip()

def call(fn, *args):
    ctypes.set_errno(0)
    rc = fn(*args)
    return [rc, ctypes.get_errno()]

def write(path):
    try:
        with open(path, "w") as f:
            f.write("1")
        return "written"
    except OSError as exc:
        return exc.errno

def read(path):
    try:
        with open(path, "rb") as f:
            return "read:%d" % len(f.read(4096))
    except OSError as exc:
        return exc.errno

for field in ("CapEff", "CapPrm", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp"):
    out[field] = status(field)
out["uid"] = os.getuid()
out["euid"] = os.geteuid()
out["root_write"] = write("/escape-probe")
out["etc_write"] = write("/etc/escape-probe")
out["proc_sys"] = write("/proc/sys/kernel/hostname")
out["sysrq"] = write("/proc/sysrq-trigger")
out["sys_write"] = write("/sys/kernel/uevent_helper")
out["kcore"] = read("/proc/kcore")
out["kmsg"] = read("/proc/kmsg")
out["host_mem"] = read("/dev/mem")
out["mount"] = call(libc.mount, b"tmpfs", b"/tmp", b"tmpfs", 0, None)
out["umount"] = call(libc.umount2, b"/proc", 2)
CLONE_NEWNS, CLONE_NEWUSER, CLONE_NEWNET = 0x00020000, 0x10000000, 0x40000000

# unshare(flags) in a child, so the other probes still run in the sandbox's own namespaces; reports what the child
# could do from inside whatever it got.
def in_child(flags):
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_end)
        got = {"unshare": call(libc.unshare, flags)}
        if got["unshare"][0] == 0:
            got["etc_write"] = write("/etc/escape-probe")
            got["root_write"] = write("/escape-probe")
            got["host_mem"] = read("/dev/mem")
            try:
                os.setuid(0)
                got["setuid0"] = "root"
            except OSError as exc:
                got["setuid0"] = exc.errno
            got["links"] = sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else None
            try:
                socket.create_connection(("1.1.1.1", 443), timeout=3).close()
                got["outside"] = "connected"
            except OSError as exc:
                got["outside"] = type(exc).__name__
        os.write(write_end, json.dumps(got).encode())
        os._exit(0)
    os.close(write_end)
    data = b""
    while chunk := os.read(read_end, 65536):
        data += chunk
    os.waitpid(pid, 0)
    return json.loads(data)

out["unshare_user"] = in_child(CLONE_NEWUSER | CLONE_NEWNS)
out["unshare_net"] = in_child(CLONE_NEWNET)
out["unshare_user_net"] = in_child(CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWNET)
PTRACE_ATTACH = 16
out["ptrace_pid1"] = call(libc.ptrace, PTRACE_ATTACH, 1, None, None)
SYS_init_module, SYS_finit_module, SYS_pivot_root, SYS_keyctl = 175, 313, 155, 250
out["init_module"] = call(libc.syscall, SYS_init_module, None, 0, b"")
out["finit_module"] = call(libc.syscall, SYS_finit_module, -1, b"", 0)
out["pivot_root"] = call(libc.syscall, SYS_pivot_root, b"/tmp", b"/tmp")
# Implemented by gVisor's kernel (unfiltered: ENOSYS for this command), denied by Docker's profile (EPERM).
out["keyctl"] = call(libc.syscall, SYS_keyctl, 0, 0, 0, 0, 0)
out["chroot"] = call(libc.chroot, b"/tmp")
try:
    os.mknod("/tmp/escape-mem", 0o600 | 0o020000, os.makedev(1, 1))
    out["mknod"] = "made"
except OSError as exc:
    out["mknod"] = exc.errno
try:
    socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    out["raw_socket"] = "opened"
except OSError as exc:
    out["raw_socket"] = exc.errno
try:
    os.setuid(0)
    out["setuid0"] = "root"
except OSError as exc:
    out["setuid0"] = exc.errno
out["docker_sock"] = [os.path.exists(p) for p in ("/var/run/docker.sock", "/run/docker.sock", "/run/containerd/containerd.sock")]
out["cgroup"] = open("/proc/self/cgroup").read().strip()
out["kernel"] = os.uname().release
out["hostname"] = socket.gethostname()
out["pid1"] = open("/proc/1/cmdline", "rb").read().split(b"\0")[0].decode()
print(json.dumps(out))
"""


@pytest.fixture
def broker(docker_broker: DockerSandboxBroker) -> DockerSandboxBroker:
    return docker_broker


async def test_sec_iso_01_07_escape_probes_end_where_the_hardening_says(
    broker: DockerSandboxBroker, sandbox_env: DockerContractEnv, runtime_mode: RuntimeMode
) -> None:
    handle = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("w", 1), idempotency_key="sec-iso-01")
    try:
        out, err, code = await run(await handle.exec(["python3", "-c", _PROBE], env={}), timeout_s=60)
        assert code == 0, err
        found = json.loads(out)
    finally:
        await broker.release(handle)
    gvisor = runtime_mode is RuntimeMode.GVISOR
    # Docker's seccomp profile reaches the guest under runc always, under gVisor only with runsc's --oci-seccomp.
    filtered = not gvisor or broker.capabilities.runsc_oci_seccomp

    assert found["kernel"].endswith("gvisor") is gvisor, found["kernel"]
    for field in ("CapEff", "CapPrm", "CapBnd", "CapAmb"):
        assert int(found[field], 16) == 0, (field, found[field])
    assert found["NoNewPrivs"] == "1"
    # runc filters with Docker's seccomp profile; under gVisor no syscall reaches the host kernel, the Sentry
    # answers it (and filters itself on the host), so the profile is applied only with runsc's --oci-seccomp.
    assert found["Seccomp"] == ("2" if filtered else "0"), (found["Seccomp"], filtered)
    rc, error = found["keyctl"]
    assert rc == -1 and (error == EPERM if filtered else error in (EPERM, ENOSYS, EINVAL)), (found["keyctl"], filtered)
    assert (found["uid"], found["euid"]) == (1000, 1000)
    for path in ("root_write", "etc_write", "proc_sys", "sysrq", "sys_write"):
        assert found[path] in (EROFS, EACCES, EPERM, ENOENT), (path, found[path])
    # Masked by the runtime: an empty file or a refusal, never the host's memory or kernel log.
    for path in ("kcore", "kmsg", "host_mem"):
        assert found[path] in ("read:0", EACCES, EPERM, ENOENT), (path, found[path])
    for attempt in ("mount", "umount", "ptrace_pid1", "init_module", "finit_module", "pivot_root", "chroot"):
        rc, error = found[attempt]
        assert rc == -1 and error in (EPERM, EACCES, ENOSYS, EINVAL), (attempt, found[attempt])
    for attempt in ("mknod", "raw_socket"):
        assert found[attempt] in (EPERM, EACCES), (attempt, found[attempt])
    assert found["setuid0"] in (EPERM, EACCES, EINVAL), found["setuid0"]
    for attempt in ("unshare_user", "unshare_net", "unshare_user_net"):
        _namespace_gives_nothing(found[attempt], allowed=not filtered, name=attempt)
        if filtered:
            assert found[attempt]["unshare"] == [-1, EPERM], (attempt, found[attempt])
    assert found["docker_sock"] == [False, False, False]
    # A private cgroup namespace (runc), or gVisor's own hierarchy, where a container is one segment named by its
    # id: never the host's tree (``system.slice/docker-<id>.scope``).
    if gvisor:
        assert all(re.fullmatch(r"\d+:[a-z,]+:/[0-9a-f]{64}", line) for line in found["cgroup"].splitlines()), found
    else:
        assert found["cgroup"] == "0::/", found["cgroup"]
    assert found["hostname"] == "sandbox" and found["hostname"] != socket.gethostname()
    assert found["pid1"].endswith("docker-init")


def _namespace_gives_nothing(got: dict[str, object], *, allowed: bool, name: str) -> None:
    """runc refuses a new namespace (Docker's seccomp profile, no capabilities). gVisor may make one in its own
    kernel: the capabilities that come with it reach nothing the sandbox's user could not, and a new network
    namespace has no way out."""
    rc, error = cast("list[int]", got["unshare"])
    if rc == -1:
        assert error in (EPERM, EACCES, ENOSYS, EINVAL), (name, got)
        return
    assert allowed, (name, "a sandbox under Docker's seccomp profile made a namespace", got)
    assert got["etc_write"] in (EROFS, EACCES, EPERM), (name, got)
    assert got["root_write"] in (EROFS, EACCES, EPERM), (name, got)
    assert got["host_mem"] in ("read:0", EACCES, EPERM, ENOENT), (name, got)
    assert got["setuid0"] in (EPERM, EINVAL), (name, got)
    assert got["outside"] != "connected", (name, got)

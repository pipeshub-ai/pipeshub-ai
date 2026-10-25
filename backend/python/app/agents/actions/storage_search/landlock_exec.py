"""Run one command confined by Landlock, then exec it.

    python -I -S landlock_exec.py <readable-dir> -- <argv...>

The command, and everything it starts, may read <readable-dir> and the system's
programs and libraries, write only /dev/null, and use no network (on kernels
with Landlock ABI 4 or later). Each stage also gets at most 30 s of CPU and
2 GiB of memory. The storage
tools run every pipeline stage this way, so a command that slips past their
validator still cannot read another tenant's files, the app's configuration or
/proc, or write anything. Exits 126 without running the command when Landlock
cannot be applied.

Standard library only, and kept to what -I -S provides: it runs once per stage.
"""

import ctypes
import os
import resource
import sys
import warnings  # noqa: F401 - os.execvp imports it lazily, after the interpreter's own files may be unreadable

_CREATE_RULESET, _ADD_RULE, _RESTRICT_SELF = 444, 445, 446  # same on every arch
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_REFER, _TRUNCATE, _IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
_NET_BIND_TCP, _NET_CONNECT_TCP = 1 << 0, 1 << 1

# The storage tools kill a command at 30 s of wall time; a stage gets no more CPU,
# and a pathological pattern cannot take the memory the service shares with it.
CPU_SECONDS = 30
MEMORY_BYTES = 2 << 30

SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
_FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | _TRUNCATE | _IOCTL_DEV


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def abi_version() -> int:
    """The kernel's Landlock ABI version, or 0 when Landlock is unavailable."""
    try:
        version = _libc().syscall(_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except OSError:
        return 0
    return max(int(version), 0)


def confine(readable_dir: str) -> None:
    """Restrict this process (and what it execs) as described above; raises OSError."""
    libc = _libc()
    abi = abi_version()
    if abi < 1:
        raise OSError("Landlock is not available on this kernel")
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= _REFER
    if abi >= 3:
        handled |= _TRUNCATE
    if abi >= 5:
        handled |= _IOCTL_DEV
    attr = _RulesetAttr(handled, (_NET_BIND_TCP | _NET_CONNECT_TCP) if abi >= 4 else 0)
    size = ctypes.sizeof(_RulesetAttr) if abi >= 4 else ctypes.sizeof(ctypes.c_uint64)
    ruleset = libc.syscall(_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    try:
        rules = [(readable_dir, READ_FILE | READ_DIR)]
        rules += [(d, READ_FILE | READ_DIR | EXECUTE) for d in SYSTEM_DIRS]
        rules.append(("/dev/null", READ_FILE | WRITE_FILE))
        for path, wanted in rules:
            try:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except FileNotFoundError:
                continue
            try:
                allowed = wanted if os.path.isdir(path) else wanted & _FILE_RIGHTS
                rule = _PathBeneathAttr(allowed & handled, fd)
                if libc.syscall(_ADD_RULE, ctypes.c_int(ruleset), ctypes.c_int(_RULE_PATH_BENEATH),
                                ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
                    raise OSError(ctypes.get_errno(), f"landlock_add_rule failed for {path}")
            finally:
                os.close(fd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS) failed")
        if libc.syscall(_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)) < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(ruleset)


def limit_resources() -> None:
    """Cap this process's (and what it execs') CPU time and address space."""
    for which, wanted in ((resource.RLIMIT_CPU, CPU_SECONDS), (resource.RLIMIT_AS, MEMORY_BYTES)):
        _soft, hard = resource.getrlimit(which)
        cap = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
        resource.setrlimit(which, (cap, cap))


def main(argv: list[str]) -> None:
    if len(argv) < 4 or argv[2] != "--":
        sys.stderr.write("usage: landlock_exec.py <readable-dir> -- <argv...>\n")
        sys.exit(2)
    try:
        limit_resources()
        confine(argv[1])
    except (OSError, ValueError) as e:
        sys.stderr.write(f"landlock: refusing to run unconfined: {e}\n")
        sys.exit(126)
    os.execvp(argv[3], argv[3:])


if __name__ == "__main__":
    main(sys.argv)

# pyright: basic
# (Runs in the sandbox on its own interpreter; everything it handles is untyped JSON off the wire.)
"""C-13's view of a sandbox, for scanning outside it: standard library only.

Prints one JSON object: every regular file on a writable mount (the root
filesystem is read-only, so these are all a sandbox can have changed), every
process's environment and command line, and what could not be read. Nothing
secret is handed in: the caller scans the output for its own secret values.
"""

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

SKIP_TYPES = {"proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue"}
MAX_BYTES = 1 << 20


def writable_mounts() -> "Iterator[str]":
    with open("/proc/self/mounts") as mounts:
        for line in mounts:
            _, target, kind, options = line.split()[:4]
            if kind not in SKIP_TYPES and "rw" in options.split(",") and not target.startswith(("/proc", "/sys")):
                yield target.replace("\\040", " ")


def read(path: str, out: "dict[str, str]", unreadable: "dict[str, str]") -> None:
    try:
        with open(path, "rb") as handle:
            out[path] = handle.read(MAX_BYTES).decode("utf-8", "replace")
    except OSError as error:
        unreadable[path] = type(error).__name__


def main() -> None:
    files, processes, unreadable = {}, {}, {}
    mounts = sorted(set(writable_mounts()))
    for top in mounts:
        if os.path.isfile(top):
            read(top, files, unreadable)
            continue
        for root, _, names in os.walk(top):
            for name in names:
                path = os.path.join(root, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    read(path, files, unreadable)
    me = str(os.getpid())
    for pid in os.listdir("/proc"):
        if pid.isdigit() and pid != me:
            for part in ("environ", "cmdline"):
                read(f"/proc/{pid}/{part}", processes, unreadable)
    print(json.dumps({"mounts": mounts, "files": files, "processes": processes, "unreadable": unreadable}))


main()

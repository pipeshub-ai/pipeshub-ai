"""Gives one sandbox run its own process budget, then becomes the run (Linux).

RLIMIT_NPROC is checked against the tasks of the real UID in the caller's user namespace and, at every
ancestor namespace, against the limit its creator had. So in a user namespace of the run's own, created
while the inherited limit is still the account's, the budget counts only the run's processes and threads:
nothing the account starts elsewhere (other runs, services, test workers) spends it, and nothing the run
starts spends theirs. A budget added to a count of the account's tasks, the fallback where no namespace
can be made (container seccomp, sysctl), is spent by any task the account starts after the count.

Run as ``python -I -S process_budget.py BUDGET PARENT_NS -- CMD...``: it imports nothing from the service,
so it works inside bubblewrap, where it is the first program of the run.
"""

from __future__ import annotations

import os
import resource
import sys
from collections.abc import Callable, Sequence

__all__ = ["budget_command", "choose_process_limit", "namespace_id", "user_task_count"]

_NS_LINK = "/proc/self/ns/user"


def namespace_id() -> int | None:
    # The link's text ("user:[INODE]"), not stat(): following it is refused inside a user namespace.
    try:
        return int(os.readlink(_NS_LINK).rsplit("[", 1)[1].rstrip("]"))
    except (OSError, IndexError, ValueError):
        return None


def budget_command(cmd: Sequence[str], budget: int) -> list[str]:
    """``cmd`` started through this module with ``budget``; the parent's namespace tells the run whether a
    confinement layer (bubblewrap) already gave it one of its own."""
    return [sys.executable, "-I", "-S", os.path.abspath(__file__), str(budget), str(namespace_id() or 0), "--", *cmd]


def user_task_count(uid: int, proc_root: str = "/proc") -> int | None:
    """Processes and threads the real UID runs now, as RLIMIT_NPROC counts them; None where `/proc` cannot
    tell (hidepid)."""
    try:
        pids = [entry for entry in os.listdir(proc_root) if entry.isdigit()]
    except OSError:
        return None
    total = 0
    for pid in pids:
        try:
            if os.stat(os.path.join(proc_root, pid)).st_uid != uid:
                continue
            with open(os.path.join(proc_root, pid, "status"), encoding="ascii", errors="replace") as status:
                threads = next((line for line in status if line.startswith("Threads:")), "Threads:\t1")
        except OSError:
            continue  # the process exited between the listing and the read
        total += int(threads.split(":", 1)[1].strip() or 1)
    return total or None


def enter_own_user_namespace() -> bool:
    """Moves this process into a new user namespace, keeping its user and group ids where the kernel allows.

    The ids are mapped by a helper left outside: AppArmor's userns restriction (Ubuntu 23.10+) takes every
    capability from a process that unshares, so it cannot map itself, while its owner outside still can.
    Unmapped, the run would see itself as nobody and could not nest a namespace of its own.
    """
    uid, gid, target = os.getuid(), os.getgid(), os.getpid()
    ready_read, ready_write = os.pipe()
    helper = os.fork()
    if helper == 0:
        os.close(ready_write)
        os.read(ready_read, 1)
        try:
            for name, line in (("uid_map", f"{uid} {uid} 1"), ("setgroups", "deny"), ("gid_map", f"{gid} {gid} 1")):
                with open(f"/proc/{target}/{name}", "w", encoding="ascii") as mapping:
                    mapping.write(line)
        finally:
            os._exit(0)
    os.close(ready_read)
    try:
        os.unshare(os.CLONE_NEWUSER)
        return True
    except (OSError, AttributeError):
        return False
    finally:
        os.close(ready_write)
        os.waitpid(helper, 0)


def choose_process_limit(
    budget: int,
    parent_namespace: int,
    *,
    own_namespace: Callable[[], int | None],
    enter_namespace: Callable[[], bool],
    count_tasks: Callable[[], int | None],
    hard_limit: int,
) -> int:
    """RLIMIT_NPROC for the run: the budget alone in a namespace of its own (one bubblewrap made, or one
    entered here), else the account's tasks plus the budget; within the hard limit it may not raise."""
    current = own_namespace()
    isolated = (current is not None and current != parent_namespace) or enter_namespace()
    in_use = None if isolated else count_tasks()
    wanted = budget if in_use is None else in_use + budget
    return wanted if hard_limit == resource.RLIM_INFINITY else min(wanted, hard_limit)


def main(argv: Sequence[str]) -> None:
    budget, parent_namespace, separator, *cmd = argv[1:]
    if separator != "--" or not cmd:
        sys.exit("usage: process_budget.py BUDGET PARENT_NS -- CMD...")
    _soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
    limit = choose_process_limit(
        int(budget),
        int(parent_namespace),
        own_namespace=namespace_id,
        enter_namespace=enter_own_user_namespace,
        count_tasks=lambda: user_task_count(os.getuid()),
        hard_limit=hard,
    )
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (limit, limit))
    except (ValueError, OSError):
        pass  # best-effort, as the executor's other limits
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main(sys.argv)

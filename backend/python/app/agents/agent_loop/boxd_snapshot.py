"""Build the boxd snapshot `SANDBOX_MODE=boxd` starts its sandboxes from.

`run_code` tells the model which packages the sandbox already has
(`IMAGE_PYTHON_PACKAGES`, `IMAGE_NODE_PACKAGES`). The PipesHub sandbox image
has them; a new boxd machine does not. This makes a machine, installs them
where `BoxdCodingSandbox` looks (a virtualenv and `node_modules` in its
working directory, plus `tsx` and `typescript`), saves it as a snapshot and
deletes the machine. Then set `BOXD_SNAPSHOT` to the snapshot's name.

    BOXD_API_KEY=... python -m app.agents.agent_loop.boxd_snapshot pipeshub-sandbox

Run it again after the package lists change: saving under the same name
makes a new version, and new sandboxes start from the newest one.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import sys
import time
import uuid
from typing import TYPE_CHECKING

from app.agents.agent_loop.sandbox_bridge import (
    IMAGE_NODE_PACKAGES,
    IMAGE_PYTHON_PACKAGES,
)

if TYPE_CHECKING:
    from boxd import Boxd

logger = logging.getLogger(__name__)

__all__ = ["build_snapshot", "setup_commands"]

_WORKING_DIR = "/home/boxd/sandbox"
_INSTALL_TIMEOUT_S = 1200.0
_SNAPSHOT_READY_TIMEOUT_S = 600.0


def setup_commands(
    python_packages: tuple[str, ...] = IMAGE_PYTHON_PACKAGES,
    node_packages: tuple[str, ...] = IMAGE_NODE_PACKAGES,
) -> list[str]:
    """The commands that prepare the working directory, in order."""
    py = " ".join(shlex.quote(p) for p in python_packages)
    node = " ".join(shlex.quote(p) for p in ("tsx", "typescript", *node_packages))
    return [
        f"mkdir -p {_WORKING_DIR}/output {_WORKING_DIR}/workspace",
        f"cd {_WORKING_DIR} && python3 -m venv .venv",
        f"cd {_WORKING_DIR} && .venv/bin/pip install --no-cache-dir --only-binary=:all: {py}",
        f"cd {_WORKING_DIR} && (test -f package.json || npm init -y >/dev/null)",
        f"cd {_WORKING_DIR} && npm install --ignore-scripts --no-audit --no-fund {node}",
    ]


def build_snapshot(
    client: Boxd,
    name: str,
    *,
    org: str | None = None,
    region: str | None = None,
    labels: dict[str, str] | None = None,
    poll_s: float = 2.0,
) -> str:
    """Make the snapshot `name` and return its id. The build machine is
    deleted whether or not the build succeeds."""
    machine = client.machines.create(
        f"{name}-build-{uuid.uuid4().hex[:8]}",
        org=org,
        region=region,
        isolated=True,
        labels={**(labels or {}), "app": "pipeshub", "pipeshub-sandbox": "snapshot-build"},
        auto_suspend_timeout=0,
        # Past every step's timeout, so the build machine goes even if this process dies.
        delete_after=int(_INSTALL_TIMEOUT_S * (len(setup_commands()) + 1)),
    )
    logger.info("boxd_snapshot.build_machine", extra={"machine_id": machine.id})
    try:
        client.machines.wait_until_ready(machine.id, region=region)
        for command in setup_commands():
            result = client.machines.exec(machine.id, command, timeout=_INSTALL_TIMEOUT_S, region=region)
            if result.exit_code != 0:
                tail = (result.stderr or result.stdout or "")[-2000:]
                raise RuntimeError(f"snapshot build step failed (exit {result.exit_code}): {command}\n{tail}")
        snapshot = client.snapshots.create(machine.id, name, region=region)
        deadline = time.monotonic() + _SNAPSHOT_READY_TIMEOUT_S
        while getattr(snapshot, "status", "ready") != "ready":
            if time.monotonic() >= deadline:
                raise RuntimeError(f"snapshot {name!r} was not ready after {_SNAPSHOT_READY_TIMEOUT_S:.0f}s")
            time.sleep(poll_s)
            snapshot = client.snapshots.get(name, region=region)
        return snapshot.id
    finally:
        try:
            client.machines.delete(machine.id, region=region)
            logger.info("boxd_snapshot.build_machine_deleted", extra={"machine_id": machine.id})
        except Exception:
            # Its `delete_after` timer removes it; a build error raised above is the one to see.
            logger.warning("boxd_snapshot.build_machine_delete_failed", extra={"machine_id": machine.id})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the boxd snapshot SANDBOX_MODE=boxd starts its sandboxes from.")
    parser.add_argument("name", help="snapshot name, then BOXD_SNAPSHOT=<name>")
    parser.add_argument("--org", default=os.environ.get("BOXD_ORG") or None)
    parser.add_argument("--region", default=os.environ.get("BOXD_REGION") or None)
    args = parser.parse_args(argv)
    try:
        from boxd import Boxd
    except ImportError:
        sys.stderr.write("The boxd SDK is not installed; install it with `pip install boxd`.\n")
        return 1
    client = Boxd(api_key=os.environ.get("BOXD_API_KEY"), region=args.region)
    snapshot_id = build_snapshot(client, args.name, org=args.org, region=args.region)
    sys.stdout.write(f"snapshot {args.name} ({snapshot_id}) is ready; set BOXD_SNAPSHOT={args.name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

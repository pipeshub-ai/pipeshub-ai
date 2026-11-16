"""Live check of ``--chaos kill-app-at`` on a throwaway container, no PipesHub stack needed.

Docker applies no restart policy to ``docker kill``, so the chaos run kills the indexing process inside the app
container and the container's process monitor restarts it. This starts a 64 MB container whose PID 1 is a small
monitor loop like ``deployment/process_monitor.sh`` (it restarts ``app.indexing_main`` when it dies), then:

1. shows that ``docker kill`` leaves it stopped with ``RestartCount`` 0 under ``--restart on-failure`` (the old way),
2. runs the bench's own ``ChaosKill`` against a fresh one and checks the kill fired, PID 1 survived, and a new
   indexing process came back.

Usage: python chaos_probe.py [image]   (default redis:7.4-bookworm: any Debian image with a shell, tr and grep)
Exit 0 only when both hold. The container (``bench-chaos-*``) is removed with ``-v`` on the way out.
"""
from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__file__))
import bench_costclass as bc

IMAGE = sys.argv[1] if len(sys.argv) > 1 else "redis:7.4-bookworm"
MONITOR = ("while true; do sh -c 'sleep 600; :' app.indexing_main & wait $!; sleep 1; done")


def sh(*cmd: str) -> str:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=60, check=False).stdout.strip()


def start(name: str) -> None:
    sh("docker", "run", "-d", "--name", name, "--restart", "on-failure", "--memory", "64m", "--cpus", "0.5",
       "--entrypoint", "sh", IMAGE, "-c", MONITOR)
    time.sleep(2)


def main() -> int:
    stamp = str(int(time.time()))
    names = [f"bench-chaos-{stamp}-a", f"bench-chaos-{stamp}-b"]
    ok = True
    try:
        start(names[0])
        sh("docker", "kill", names[0])
        time.sleep(4)
        state = sh("docker", "inspect", "-f", "{{.State.Status}} restarts={{.RestartCount}}", names[0])
        print(f"docker kill: {state}  (the old way: a manual stop, no restart)")
        ok &= state.startswith("exited") and "restarts=0" in state

        start(names[1])
        chaos = bc.ChaosKill(names[1], 0.1)
        chaos.start()
        time.sleep(1.0)
        recovered = chaos.recovered(wait_s=30, poll_s=1.0)
        status = sh("docker", "inspect", "-f", "{{.State.Status}}", names[1])
        print(f"in-container kill: fired={chaos.fired} before={sorted(chaos.before)} recovered={recovered} container={status}")
        ok &= chaos.fired and recovered and status == "running"
    finally:
        for n in names:
            sh("docker", "rm", "-f", "-v", n)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

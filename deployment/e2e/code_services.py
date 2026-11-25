"""The services of an e2e stack that run this tree's code from a bind mount, one per line (run.sh rebuild).

    docker compose ... config --format json | python3 code_services.py <E2E_SRC>

`compose up` keeps a container whose configuration did not change, so such a service would go on running the code
it started with: the scripted model served the old script (CTL-6), and the sandbox broker kept the engine allowlist
it loaded before the run images changed, refusing every Codex and Claude Code run (HTIMG).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def code_services(config: dict[str, Any], src: str) -> list[str]:
    root = os.path.realpath(src)
    return sorted({
        name
        for name, service in config["services"].items()
        for volume in service.get("volumes") or []
        if volume.get("type") == "bind" and os.path.realpath(volume.get("source", "")).startswith(root + os.sep)
    })


if __name__ == "__main__":
    print("\n".join(code_services(json.load(sys.stdin), sys.argv[1])))

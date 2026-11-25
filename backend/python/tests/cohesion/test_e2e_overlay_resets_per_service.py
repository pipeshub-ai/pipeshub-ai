"""The e2e overlay resets each service's `depends_on` (and `build`) on the service itself, not through a merge key.

run.sh creates the stack with `--no-deps` and starts the app with `compose start`, which waits on its dependencies. The
stores it depends on in the base file (mongodb, redis) are the host's shared containers, never project services. A
`!reset` that only arrives by `<<: *anchor` is dropped by some Compose releases: CI's es-pr lane then failed with
"pipeshub-ai is missing dependency redis" (and "... mongodb" before), while a local Compose resolved the same files fine.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
OVERLAY = REPO / "deployment" / "e2e" / "docker-compose.e2e.yml"
BASE = REPO / "deployment" / "docker-compose" / "docker-compose.yml"


def _own_keys(service_block: str) -> set[str]:
    """The keys a service sets on its own lines (4-space indent), with the tag of each, ignoring what `<<:` merges."""
    return {f"{m.group(1)} {m.group(2) or ''}".strip() for m in re.finditer(r"^    ([\w-]+):\s*(![a-z]+)?", service_block, re.M)}


def _service_blocks(text: str) -> dict[str, str]:
    body = text.split("\nservices:\n", 1)[1]
    blocks = re.split(r"^  ([\w-]+):[^\n]*\n", body, flags=re.M)
    return {blocks[i]: blocks[i + 1] for i in range(1, len(blocks) - 1, 2)}


def _base_services() -> dict:
    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    return yaml.load(BASE.read_text(encoding="utf-8"), _Loader)["services"]


def test_every_merged_e2e_service_resets_its_dependencies_itself() -> None:
    base = _base_services()
    missing = {}
    for name, block in _service_blocks(OVERLAY.read_text(encoding="utf-8")).items():
        if "<<: *e2e-python" not in block or name not in base:
            continue
        own = _own_keys(block)
        need = []
        if base[name].get("depends_on") and "depends_on !reset" not in own:
            need.append("depends_on: !reset {}")
        if base[name].get("build") and "build !reset" not in own:
            need.append("build: !reset null")
        if need:
            missing[name] = need
    assert missing == {}, f"set these on the service itself, not only in the x-e2e-python anchor: {missing}"

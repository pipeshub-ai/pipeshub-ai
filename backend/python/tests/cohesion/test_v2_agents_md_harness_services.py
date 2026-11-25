"""C15.02 (OP-09): AGENTS.md lists the agent harness's processes as Compose runs them.

Each bullet under "**Agent harness**" in AGENTS.md "What lives where" names a
process, its ports, the module that starts it, its Compose service and any
network aliases. The service must exist in the Compose file named (the base file
unless another is given), every alias must be on that service, and each port must
be declared by the module or by a module it imports, so the table cannot drift
from the code. Every harness entry point under ``backend/python/app`` is listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.cohesion.compose_files import BASE, COMPOSE_DIR, compose_services
from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

AGENTS_MD = REPO_ROOT / "AGENTS.md"
NODE_APPS = REPO_ROOT / "backend" / "nodejs" / "apps"
HARNESS_ENTRY_POINTS = {
    "app.harness_main",
    "app.model_gateway_main",
    "app.sandbox_broker_main",
    "app.egress_proxy_main",
    "app.docker_proxy_main",
}
BULLET = re.compile(
    r"^- \*\*(?P<name>[^*]+)\*\*(?P<ports>[^(]*)\((?P<meta>[^)]*)\) — ", re.M
)


def _harness_block() -> str:
    text = AGENTS_MD.read_text(encoding="utf-8")
    start = text.index("**Agent harness**")
    end = text.index("\n## ", start)
    return text[start:end]


@dataclass(frozen=True)
class Entry:
    name: str
    ports: tuple[int, ...]
    module: str
    service: str
    file: Path
    aliases: tuple[str, ...]


def _entries() -> list[Entry]:
    entries: list[Entry] = []
    for match in BULLET.finditer(_harness_block()):
        meta = match.group("meta")
        compose = re.search(r"Compose `([^`]+)`(?: in `([^`]+)`)?", meta)
        module = re.match(r"\s*`([^`]+)`", meta)
        assert compose, f"{match.group('name')}: no Compose service named"
        assert module, f"{match.group('name')}: no module named"
        entries.append(
            Entry(
                name=match.group("name"),
                ports=tuple(
                    int(p) for p in re.findall(r"`:(\d+)`", match.group("ports"))
                ),
                module=module.group(1),
                service=compose.group(1),
                file=COMPOSE_DIR / (compose.group(2) or BASE.name),
                aliases=tuple(re.findall(r"alias `([^`]+)`", meta)),
            )
        )
    return entries


def _module_source(module: str) -> Path:
    if module.endswith(".ts"):
        return NODE_APPS / module
    return PYTHON_ROOT.joinpath(*module.split(".")).with_suffix(".py")


def _declares_port(path: Path, port: int, depth: int = 1) -> bool:
    source = path.read_text(encoding="utf-8")
    if re.search(rf"\b{port}\b", source):
        return True
    if depth == 0 or path.suffix != ".py":
        return False
    for module in re.findall(r"^from\s+(app[\w.]*)\s+import", source, re.M):
        candidate = PYTHON_ROOT.joinpath(*module.split(".")).with_suffix(".py")
        if candidate.is_file() and _declares_port(candidate, port, depth - 1):
            return True
    return False


def _aliases(service: dict[str, Any]) -> set[str]:
    networks: object = service.get("networks") or {}
    if not isinstance(networks, dict):
        return set()
    found: set[str] = set()
    for net in cast("dict[str, Any]", networks).values():
        if isinstance(net, dict):
            found.update(
                cast("list[str]", cast("dict[str, Any]", net).get("aliases") or [])
            )
    return found


def test_every_harness_entry_point_is_listed() -> None:
    listed = {entry.module for entry in _entries()}
    assert HARNESS_ENTRY_POINTS <= listed, (
        f"AGENTS.md misses {sorted(HARNESS_ENTRY_POINTS - listed)}"
    )


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e.name)
def test_the_listed_service_exists_with_its_aliases_and_ports(
    entry: Entry,
) -> None:
    source = _module_source(entry.module)
    assert source.is_file(), f"{entry.name}: {source} does not exist"
    services = compose_services(entry.file)
    assert entry.service in services, (
        f"{entry.name}: no service {entry.service} in {entry.file.name}"
    )
    missing = set(entry.aliases) - _aliases(services[entry.service])
    assert not missing, f"{entry.name}: {entry.service} has no alias {sorted(missing)}"
    for port in entry.ports:
        assert _declares_port(source, port), (
            f"{entry.name}: {entry.module} does not declare port {port}"
        )


def test_no_harness_port_is_published() -> None:
    for entry in _entries():
        published = cast(
            "list[object]",
            compose_services(entry.file)[entry.service].get("ports") or [],
        )
        for mapping in published:
            container_port = int(str(mapping).rsplit(":", 1)[-1].split("/")[0])
            assert container_port not in entry.ports, (
                f"{entry.name}: port {container_port} is published"
            )

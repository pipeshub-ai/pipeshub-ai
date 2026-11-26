"""docker-compose.postgres-only.yml removes the other stores; it must remove every service that only exists for them too.

A service left behind that depends on a removed store makes the whole project invalid ("depends on undefined service"),
and one that only serves a removed store (in its profiles alone) starts with every profile on. Both happened when the
harness's store helpers (kafka-init, etcd-tls) met main's overlay, which was written against main's service list.
Static: read from the files, no Docker needed.
"""

from __future__ import annotations

import pathlib

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parents[5] / "deployment" / "docker-compose"
RESET = object()


class _ComposeLoader(yaml.SafeLoader):
    """Reads Compose's `!reset` / `!override` tags: `!reset null` removes, `!override` replaces."""


def _reset(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    return RESET if isinstance(node, yaml.ScalarNode) else _plain(loader, node)


def _plain(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!reset", _reset)
_ComposeLoader.add_constructor("!override", _plain)


def _services(name: str) -> dict:
    return yaml.load((COMPOSE / name).read_text(encoding="utf-8"), _ComposeLoader)["services"]


def _depends(service: dict) -> set[str]:
    deps = service.get("depends_on") or {}
    return set(deps) if isinstance(deps, (dict, list)) else set()


def _closure() -> tuple[dict, dict, set[str]]:
    base, overlay = _services("docker-compose.yml"), _services("docker-compose.postgres-only.yml")
    removed = {name for name, svc in overlay.items() if svc is RESET}
    return base, overlay, removed


def test_no_kept_service_depends_on_a_removed_one() -> None:
    base, overlay, removed = _closure()
    broken = {}
    for name, svc in base.items():
        if name in removed:
            continue
        own = overlay.get(name) or {}
        reset = {dep for dep, value in (own.get("depends_on") or {}).items() if value is RESET} if isinstance(own, dict) else set()
        left = (_depends(svc) & removed) - reset
        if left:
            broken[name] = sorted(left)
    assert broken == {}, f"remove these with their store in docker-compose.postgres-only.yml, or reset the dependency: {broken}"


def test_no_kept_service_serves_only_a_removed_store() -> None:
    """A helper whose profiles are all a removed store's would start with every profile on (COMPOSE_PROFILES='*')."""
    base, _, removed = _closure()
    store_profiles = {p for name in removed if name in base for p in base[name].get("profiles") or []}
    left = sorted(
        name for name, svc in base.items()
        if name not in removed and svc.get("profiles") and set(svc["profiles"]) <= store_profiles
    )
    assert left == [], f"these run only for a store the overlay removes; remove them too: {left}"

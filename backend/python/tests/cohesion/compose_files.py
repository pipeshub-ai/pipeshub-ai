"""The shipped Compose files as data, for the cohesion cases that read them without Docker."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

COMPOSE_DIR = REPO_ROOT / "deployment" / "docker-compose"
BASE = COMPOSE_DIR / "docker-compose.yml"


class ComposeLoader(yaml.SafeLoader):
    """Compose's merge tags: ``!reset`` removes the key, ``!override`` replaces it with the value given."""


def _override(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> Any:  # noqa: ANN401 - parsed YAML
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_object(node)


ComposeLoader.add_multi_constructor("!reset", lambda *_: None)  # pyright: ignore[reportUnknownLambdaType]
ComposeLoader.add_multi_constructor("!override", _override)


def render_defaults(value: str) -> str:
    """What ``${NAME:-d}`` / ``${NAME-d}`` render to with nothing set, innermost first so a default may itself hold
    one (``${PIPESHUB_APP_PINNED:-${PIPESHUB_IMAGE:-repo}:${IMAGE_TAG:-latest}}``)."""
    while True:
        rendered = re.sub(r"\$\{[A-Z0-9_]+:?-([^${}]*)\}", r"\1", value)
        if rendered == value:
            return rendered
        value = rendered


def load_compose(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader) or {}


def compose_services(path: Path) -> dict[str, dict[str, Any]]:
    return {name: service or {} for name, service in (load_compose(path).get("services") or {}).items()}


def is_fragment(service: dict[str, Any]) -> bool:
    """A service that only runs merged over one of the same name: an overlay file's entry."""
    return not any(key in service for key in ("image", "build", "extends"))


def effective_service(path: Path, name: str) -> dict[str, Any]:
    """The service as Compose runs it: over what it ``extends`` and, in an overlay file, over the base file's.

    A key the overlay resets is ``None`` here, as absent as Compose makes it.
    """
    services = compose_services(path)
    service = {key: value for key, value in services[name].items() if key != "extends"}
    extended = services[name].get("extends")
    if isinstance(extended, dict):
        parent = (path.parent / extended["file"]).resolve() if "file" in extended else path
        return {**effective_service(parent, extended["service"]), **service}
    overlay = path != BASE and any(is_fragment(other) for other in services.values())
    if overlay and name in compose_services(BASE):
        return {**effective_service(BASE, name), **service}
    return service

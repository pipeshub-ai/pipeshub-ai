"""SEC-C-09: Node's worker-only listener (3002) is never published by any shipped Compose file.

It listens on every interface of the app container because two neighbours need it (the egress proxy on
sandbox_plane, Prometheus on pipeshub); the host never does. The route-side defence (a guard before any body is
read) is Node's internal-listener tests.
"""

from __future__ import annotations

import pytest

from tests.cohesion.compose_files import (
    BASE,
    COMPOSE_DIR,
    compose_services,
    render_defaults,
)

INTERNAL_PORT = "3002"


def _published(port_spec: object) -> str:
    """The container port a ``ports`` entry publishes, in short or long syntax."""
    if isinstance(port_spec, dict):
        return str(port_spec.get("target", ""))
    rendered = render_defaults(str(port_spec)).split("/")[0]
    return rendered.rsplit(":", 1)[-1]


@pytest.mark.parametrize("path", sorted(COMPOSE_DIR.glob("docker-compose*.yml")), ids=lambda p: p.name)
def test_no_compose_file_publishes_the_internal_listener(path: object) -> None:
    for name, service in compose_services(path).items():  # type: ignore[arg-type]
        published = [_published(spec) for spec in service.get("ports") or []]
        assert INTERNAL_PORT not in published, f"{name} publishes Node's worker-only port {INTERNAL_PORT}"


def test_the_app_names_its_internal_listener_explicitly() -> None:
    environment = compose_services(BASE)["pipeshub-ai"].get("environment") or []
    settings = dict(str(item).split("=", 1) for item in environment if "=" in str(item))
    # The port stays the default (RS-13 holds that it is not set); the bind is stated, and the URL names the port.
    assert settings.get("AGENT_HARNESS_INTERNAL_HOST") == "0.0.0.0"
    assert settings.get("AGENT_HARNESS_STATE_URL") == f"http://localhost:{INTERNAL_PORT}"

"""Every code-graph setting the code reads is passed through by each deployment (O-12).

CODE_EDGE_BUILD_RECONCILE_INTERVAL_SECONDS was documented in env.template and
had no effect in Docker Compose: the service lists its variables one by one.
"""
import pathlib

import pytest

from app.utils.env_config import env_int

ROOT = pathlib.Path(__file__).resolve().parents[6]
KNOBS = [
    "CODE_EDGE_BUILD_RECONCILE_INTERVAL_SECONDS", "CODE_EDGE_FULL_REBUILD_INTERVAL_SECONDS",
    "CODE_EDGE_BUILD_MAX_CONCURRENT", "CODE_EDGE_BUILD_MAX_PER_ORG", "CODE_EDGE_BUILD_MAX_BLOCKS",
    "CODE_EDGE_BUILD_MAX_FAILURES", "PARSE_MAX_ORG_SHARE", "CODE_GRAPH_ENABLED",
    "CODE_GRAPH_BACKFILL",
]
FILES = [
    "deployment/docker-compose/docker-compose.yml",
    "deployment/helm/pipeshub-ai/templates/deployment.yaml",
    "deployment/docker-compose/env.template",
]


@pytest.mark.parametrize("path", FILES)
def test_each_deployment_passes_every_knob(path: str) -> None:
    text = (ROOT / path).read_text()
    assert [k for k in KNOBS if k not in text] == []


def test_an_empty_value_from_compose_means_the_default(monkeypatch) -> None:
    """Compose passes `${X:-}` as an empty string, which int() would reject at import."""
    monkeypatch.setenv("CODE_EDGE_BUILD_MAX_BLOCKS", "")
    assert env_int("CODE_EDGE_BUILD_MAX_BLOCKS", 300000) == 300000

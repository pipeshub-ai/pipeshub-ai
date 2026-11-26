"""PIPESHUB_OFFLINE reaches every container that runs the app image, and is documented."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
COMPOSE_DIR = REPO / "deployment" / "docker-compose"
HELM = REPO / "deployment" / "helm" / "pipeshub-ai"

PASSTHROUGH = "- PIPESHUB_OFFLINE=${PIPESHUB_OFFLINE:-}"

# Compose files that define an app container (or a parsing container running the app image).
APP_COMPOSE = [
    "docker-compose.yml",
    "docker-compose.build.neo4j.yml",
    "docker-compose.integration.arango.yml",
    "docker-compose.integration.neo4j.yml",
    "docker-compose.prod-legacy.yml",
    "docker-compose.parsing.yml",
    "hot-reload/docker-compose.dev.arango.yml",
    "hot-reload/docker-compose.dev.neo4j.yml",
]


def test_every_app_compose_file_passes_the_switch_with_an_empty_default() -> None:
    for name in APP_COMPOSE:
        text = (COMPOSE_DIR / name).read_text()
        assert text.count(PASSTHROUGH) == 1, name


def test_no_compose_file_turns_it_on_by_itself() -> None:
    for path in COMPOSE_DIR.rglob("*.yml"):
        text = path.read_text()
        assert not re.search(r"PIPESHUB_OFFLINE=(1|true)\b", text), path
        assert not re.search(r"PIPESHUB_OFFLINE=\$\{PIPESHUB_OFFLINE:-[^}]", text), path


def test_helm_value_defaults_off_and_reaches_the_app_env_helper() -> None:
    values = (HELM / "values.yaml").read_text()
    assert re.search(r"^offline:\n  enabled: false\n", values, re.M)
    helper = (HELM / "templates" / "_helpers.tpl").read_text()
    # "false" as a string must be off, so the value is compared as text, and the entry is the exact env name.
    assert 'if eq (toString .Values.offline.enabled) "true"' in helper
    assert re.search(r'^\s+- name: PIPESHUB_OFFLINE\n\s+value: "1"$', helper, re.M)
    # Both pods that run the app image build their env from this helper.
    for template in ("deployment.yaml", "parsing.yaml"):
        assert 'include "pipeshub-ai.appEnv"' in (HELM / "templates" / template).read_text()


def test_the_env_template_and_guide_document_it() -> None:
    assert re.search(r"^PIPESHUB_OFFLINE=$", (COMPOSE_DIR / "env.template").read_text(), re.M)
    guide = (COMPOSE_DIR / "ADVANCED_DEPLOYMENT.md").read_text()
    assert "## Air-gapped installs" in guide
    assert "pipeshub_hf_cache" in guide.split("## Air-gapped installs", 1)[1]
    assert "slim image has no" in guide.split("## Air-gapped installs", 1)[1] or "slim image has none" in guide


@pytest.mark.parametrize(
    "compose",
    ["docker-compose.yml", "hot-reload/docker-compose.dev.arango.yml", "hot-reload/docker-compose.dev.neo4j.yml"],
)
def test_the_compose_file_passes_all_seven_switches(compose) -> None:
    """An explicit value in .env (HF_HUB_OFFLINE=0) must reach the container, or the offline default replaces it."""
    text = (COMPOSE_DIR / compose).read_text()
    for name in (
        "TELEMETRY_COLLECTION_FLAG",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "LITELLM_LOCAL_MODEL_COST_MAP",
        "DO_NOT_TRACK",
    ):
        assert f"      - {name}=${{{name}:-}}\n" in text, (compose, name)

"""Every capacity knob reaches the operator in compose, the env template and the Helm chart.

The compose entrypoint unsets a blank knob that an older image would read with ``int()``; its
list must be exactly the registry's ``entrypoint_unset`` set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.capacity.knobs import (
    COMPOSE,
    ENTRYPOINT_UNSET,
    HELM,
    KNOBS,
    REGISTRY,
    entrypoint_unset_names,
)

ROOT = Path(__file__).resolve().parents[5]
COMPOSE_FILE = ROOT / "deployment/docker-compose/docker-compose.yml"
ENV_TEMPLATE = ROOT / "deployment/docker-compose/env.template"
HELM_DIR = ROOT / "deployment/helm/pipeshub-ai"
DEPLOYMENT = HELM_DIR / "templates/deployment.yaml"
VALUES = HELM_DIR / "values.yaml"


def _loop_names(compose: str) -> set[str]:
    match = re.search(r"for k in ([^;]+?); do", compose, re.S)
    assert match, "the entrypoint unset loop is not in docker-compose.yml"
    return set(match.group(1).split())


def test_entrypoint_list_equals_the_registry() -> None:
    assert _loop_names(COMPOSE_FILE.read_text()) == entrypoint_unset_names()


@pytest.mark.parametrize("knob", [k for k in KNOBS if COMPOSE in k.passthrough], ids=lambda k: k.name)
def test_compose_passes_the_knob_through(knob) -> None:
    line = f"- {knob.name}=${{{knob.name}:-{knob.compose_default}}}"
    assert line in COMPOSE_FILE.read_text(), f"docker-compose.yml lacks {line}"


@pytest.mark.parametrize("knob", [k for k in KNOBS if COMPOSE in k.passthrough], ids=lambda k: k.name)
def test_env_template_names_the_knob(knob) -> None:
    assert re.search(rf"^#?\s*{knob.name}=", ENV_TEMPLATE.read_text(), re.M), f"env.template lacks {knob.name}"


def _chart_text() -> str:
    return "\n".join(p.read_text() for p in sorted((HELM_DIR / "templates").glob("*.yaml")))


@pytest.mark.parametrize("knob", [k for k in KNOBS if HELM in k.passthrough], ids=lambda k: k.name)
def test_helm_passes_the_knob_only_when_set(knob) -> None:
    text = DEPLOYMENT.read_text()
    match = re.search(
        rf"\{{\{{- if ne \(toString \.Values\.config\.(\w+)\) \"\" \}}\}}\n\s*- name: {knob.name}\n"
        rf"\s*value: \{{\{{ \.Values\.config\.\1 \| quote \}}\}}\n\s*\{{\{{- end \}}\}}",
        text,
    )
    assert match, f"the chart does not pass {knob.name} behind an if-set guard"
    assert re.search(rf"^  {match.group(1)}: \"\"", VALUES.read_text(), re.M), (
        f"values.yaml has no empty default for config.{match.group(1)}"
    )


def test_helm_never_renders_an_empty_registry_knob() -> None:
    """No registry knob is written unconditionally: a blank value would reach an old image's ``int()``."""
    text = DEPLOYMENT.read_text()
    for knob in KNOBS:
        for match in re.finditer(rf"^(\s*)- name: {knob.name}\n", text, re.M):
            before = text[: match.start()].rstrip().splitlines()[-1]
            assert before.strip().startswith("{{- if ne (toString"), f"{knob.name} is rendered unconditionally"


def test_helm_passes_the_pod_requests() -> None:
    text = DEPLOYMENT.read_text()
    assert re.search(r"PIPESHUB_CPU_REQUEST_MILLI\n\s+valueFrom:\n\s+resourceFieldRef:\n\s+resource: requests\.cpu\n\s+divisor: 1m", text)
    assert re.search(r"PIPESHUB_MEMORY_REQUEST\n\s+valueFrom:\n\s+resourceFieldRef:\n\s+resource: requests\.memory", text)


def test_the_registry_has_no_duplicates_and_known_classes() -> None:
    assert len(REGISTRY) == len(KNOBS)
    assert all(ENTRYPOINT_UNSET not in k.passthrough or COMPOSE in k.passthrough for k in KNOBS)

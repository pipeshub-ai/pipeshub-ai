"""R25-OPS (C15.04, C20.48): the air-gapped install smoke reads the shipped files the way an operator uses them.

``deployment/docker-compose/tests/airgap_install_smoke.sh`` runs ``install.sh --no-pull`` with a mirror's images,
layered with ``docker-compose.airgap-install.yml`` through ``PIPESHUB_COMPOSE_OVERRIDES``, then a turn, an engine job,
Docling with no route out and the DR drill. The lane run proves it end to end; these keep its wiring honest: the
overlay leaves no network of the install routed but the ingress's, and the smoke takes the documented path, not a
shortcut around the installer. ``app.modules.parsers.pdf.docling_models`` fills the cache from the parser's own
converter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

import pytest

from tests.cohesion.support import REPO_ROOT

COMPOSE_DIR = REPO_ROOT / "deployment" / "docker-compose"
TESTS = COMPOSE_DIR / "tests"
OVERLAY = TESTS / "docker-compose.airgap-install.yml"
SMOKE = TESTS / "airgap_install_smoke.sh"
GUIDE = REPO_ROOT / "docs" / "agent-harness-v2" / "guides" / "air-gapped-install.md"
DRILL = REPO_ROOT / "docs" / "agent-harness-v2" / "DR-DRILL.md"
TOOLS = ("pxh-ingress", "pxh-fake-llm", "pxh-fake-saas")

needs_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose renders the files")


def _render() -> dict[str, Any]:
    env = ("COMPOSE_PROFILES=graph-neo4j,sandbox", "PIPESHUB_IMAGE=mirror.internal/pipeshub-ai", "IMAGE_TAG=t1",
           "APP_PORT=4000", "AIRGAP_E2E_DIR=/e2e", "AIRGAP_LLM_PORT=4001", "AIRGAP_SAAS_PORT=4002",
           "AIRGAP_LLM_KEY=k", "AIRGAP_CONTROL_TOKEN=c",
           # Compose refuses to render without them (SEC-D-06).
           "REDIS_PASSWORD=ci", "QDRANT_API_KEY=ci", "MONGO_PASSWORD=ci", "KAFKA_PASSWORD=ci")
    with tempfile.TemporaryDirectory(prefix="pxh-r25-") as scratch:
        env_file = Path(scratch) / "render.env"
        env_file.write_text("\n".join(env) + "\n", encoding="utf-8")
        out = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_DIR / "docker-compose.yml"), "-f", str(OVERLAY), "-p", "pxh-r25",
             "--env-file", str(env_file), "config", "--format", "json"],
            env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
            capture_output=True, text=True, check=True,
        ).stdout
    return json.loads(out)


@needs_compose
def test_r25_ag_1_only_the_ingress_is_on_a_routed_network_and_only_it_publishes() -> None:
    rendered = _render()
    networks = rendered["networks"]
    routed = {name for name, net in networks.items() if not net.get("internal")}
    assert routed == {"ingress"}, routed
    for name, service in rendered["services"].items():
        on = set(service.get("networks") or {})
        if name == "pxh-ingress":
            assert on == {"ingress", "pipeshub"}
            assert {p["published"] for p in service["ports"]} == {"4000", "4001", "4002"}
            assert {p["host_ip"] for p in service["ports"]} == {"127.0.0.1"}
        else:
            assert "ingress" not in on, name
            assert not service.get("ports"), f"{name} publishes a port past the ingress"


@needs_compose
def test_r25_ag_2_the_test_services_run_from_the_installed_image_so_nothing_else_is_needed() -> None:
    services = _render()["services"]
    for name in TOOLS:
        assert services[name]["image"] == "mirror.internal/pipeshub-ai:t1", name
    assert services["pipeshub-ai"]["image"] == "mirror.internal/pipeshub-ai:t1"
    assert services["pipeshub-ai"]["environment"]["HARNESS_ENGINE_RUNS"] == "false", "engines stay off unless .env says"


def test_r25_ag_3_the_smoke_takes_the_documented_install_path() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    assert "bash ./install.sh" in text and "--no-pull" in text
    for setting in ("PIPESHUB_IMAGE=", "PIPESHUB_SANDBOX_IMAGE=", "PIPESHUB_COMPOSE_OVERRIDES=", "PIPESHUB_ENV_EXTRA="):
        assert setting in text, setting
    for line in ("TELEMETRY_COLLECTION_FLAG=false", "HF_HUB_OFFLINE=1", "HARNESS_ENGINE_RUNS=true"):
        assert line in text, line
    assert "docker pull" not in text and "--pull" not in text.replace("--no-pull", "")
    assert "config --images" in text, "every image the install names is checked on the host before it starts"
    assert "airgap_check.py" in text
    assert "python -m app.modules.parsers.pdf.docling_models" in text


def test_r25_ag_4_the_drill_follows_dr_drill_md() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    drill = DRILL.read_text(encoding="utf-8")
    for step in ("mongodump", "--archive --gzip --oplog", "mongorestore --quiet --archive --gzip --oplogReplay"):
        assert step in text, step
    assert "mongorestore --archive --gzip --oplogReplay" in drill
    assert "--drop --oplogReplay" not in text + drill, "a restore that replaces its own user loses its oplog replay"
    assert text.index("mongodump") < text.index("stop pipeshub-ai") < text.index("after-backup") < text.index("mongorestore")
    assert re.search(r"docker compose -p \S+ create", drill), "the blob volume is the project's before it is filled"
    assert "compose_cmd create" in text


def test_r25_ag_5_the_guide_names_the_settings_the_installer_reads() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    installer = (COMPOSE_DIR / "install.sh").read_text(encoding="utf-8")
    assert "PIPESHUB_SANDBOX_IMAGE" in guide and 'SANDBOX_DOCKER_IMAGE="${PIPESHUB_SANDBOX_IMAGE:-}"' in installer
    assert "python -m app.modules.parsers.pdf.docling_models" in guide
    assert "airgap_install_smoke.sh" in guide


def _fake_docling(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    base_models = types.ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = types.SimpleNamespace(PDF="pdf")  # type: ignore[attr-defined]
    for name in ("docling", "docling.datamodel"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_models)
    converter = types.SimpleNamespace(initialize_pipeline=calls.append)
    processor = types.ModuleType("app.modules.parsers.pdf.docling_processor")
    processor._get_converter = lambda: converter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.modules.parsers.pdf.docling_processor", processor)


def test_r25_dm_1_fetch_builds_the_parsers_own_pdf_pipeline_and_lists_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from app.modules.parsers.pdf import docling_models

    calls: list[Any] = []
    _fake_docling(monkeypatch, calls)
    for repo in ("models--docling-project--docling-models", "models--docling-project--docling-layout-heron",
                 "models--BAAI--bge-large-en-v1.5"):
        (tmp_path / repo).mkdir()
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", str(tmp_path))
    assert docling_models.fetch() == ["docling-project/docling-layout-heron", "docling-project/docling-models"]
    assert calls == ["pdf"]


def test_r25_dm_2_it_refuses_to_run_offline(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from app.modules.parsers.pdf import docling_models

    calls: list[Any] = []
    _fake_docling(monkeypatch, calls)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert docling_models.main() == 1
    assert calls == [] and "HF_HUB_OFFLINE" in capsys.readouterr().err


def test_r25_dm_3_a_pipeline_that_cannot_be_built_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from app.modules.parsers.pdf import docling_models

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    def refuse() -> list[str]:
        raise OSError("no route to huggingface.co")

    monkeypatch.setattr(docling_models, "fetch", refuse)
    assert docling_models.main() == 1
    assert "no route to huggingface.co" in capsys.readouterr().err

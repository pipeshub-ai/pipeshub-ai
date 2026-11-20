"""Docker, Compose and Helm agree on where the baked Docling models live.

The models sit in /opt/docling-models (set once by the image, as PIPESHUB_DOCLING_MODELS_DIR); no manifest may
cover that path, set Docling's own DOCLING_ARTIFACTS_PATH (once set, Docling stops downloading and fails on any
file the directory lacks), or force the Hub offline (the HF cache volume still serves runtime downloads).
"""

import re
from pathlib import Path, PurePosixPath

import yaml

from app.config.constants.docling_models import (
    DOCLING_ARTIFACTS_DEFAULT,
    DOCLING_ARTIFACTS_ENV,
)

REPO = Path(__file__).resolve().parents[5]
DOCKERFILES = [REPO / "Dockerfile", REPO / "deployment" / "docker-compose" / "hot-reload" / "Dockerfile.dev"]
COMPOSE = sorted((REPO / "deployment" / "docker-compose").rglob("docker-compose*.y*ml"))
HELM = sorted(
    p for p in (REPO / "deployment" / "helm" / "pipeshub-ai").rglob("*") if p.suffix in {".yaml", ".tpl", ".txt"}
)
MODELS = PurePosixPath(DOCLING_ARTIFACTS_DEFAULT)
DOCLING_OWN_VARIABLE = re.compile(r"(?<![A-Z_])DOCLING_ARTIFACTS_PATH")
# Every spelling that turns the Hub offline: `HF_HUB_OFFLINE=1`, `HF_HUB_OFFLINE: "1"`, a default such as
# `${HF_HUB_OFFLINE:-1}`, and Helm's `name: HF_HUB_OFFLINE` followed by `value: "1"`. Unset and empty stay valid.
HUB_FORCED_OFFLINE = re.compile(r"HF_HUB_OFFLINE[\"']?\W+(?:value[\"']?\W+)?[\"']?(?:1|true|yes|on)\b", re.I)


def _covers(target: str) -> bool:
    """A mount at ``target`` hides or replaces the models: it is the directory, a parent of it, or inside it."""
    path = PurePosixPath(target.strip().strip("\"'"))
    return path.is_absolute() and (path == MODELS or path in MODELS.parents or MODELS in path.parents)


class _Loader(yaml.SafeLoader):
    """Compose merge tags such as ``!override`` and ``!reset`` carry no meaning for this check."""


_Loader.add_multi_constructor("!", lambda loader, _suffix, node: loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode) else loader.construct_scalar(node))


def _compose_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for service in (yaml.load(path.read_text(), Loader=_Loader) or {}).get("services", {}).values():
        for volume in service.get("volumes", []) or []:
            if isinstance(volume, dict):
                targets.append(str(volume.get("target", "")))
            else:
                parts = str(volume).split(":")
                targets.append(parts[1] if len(parts) > 1 else parts[0])
        tmpfs = service.get("tmpfs", [])
        targets += [t.split(":")[0] for t in ([tmpfs] if isinstance(tmpfs, str) else tmpfs)]
    return targets


def test_the_manifest_sets_are_not_empty() -> None:
    assert len(COMPOSE) >= 10 and len(HELM) >= 10


def test_each_image_bakes_the_models_into_the_directory_the_code_reads() -> None:
    for dockerfile in DOCKERFILES:
        text = dockerfile.read_text()
        assert f"ENV {DOCLING_ARTIFACTS_ENV}={DOCLING_ARTIFACTS_DEFAULT}\n" in text, dockerfile
        assert not DOCLING_OWN_VARIABLE.search(text), dockerfile
        run = re.search(r"^RUN python /tmp/bake/bake-docling-models\.py \"\$\{%s\}\"(?:\\\n|[^\n])*" % DOCLING_ARTIFACTS_ENV, text, re.M)
        assert run, f"{dockerfile} does not run the bake script on the models directory"
        assert "chmod -R a+rX" in run.group(0) and ".agent_harnesses.json" in run.group(0) and "huggingface/xet" in run.group(0), dockerfile
        assert "COPY deployment/bake-docling-models.py backend/python/app/config/constants/docling_models.py /tmp/bake/" in text, dockerfile
        assert "BUNDLE_TABLEFORMER_FAST" in text, dockerfile
        assert "hf_hub_download('docling-project" not in text, f"{dockerfile} still bakes into the HF cache"


def test_no_compose_file_covers_or_redirects_the_models() -> None:
    for path in COMPOSE:
        text = path.read_text()
        assert not DOCLING_OWN_VARIABLE.search(text) and DOCLING_ARTIFACTS_ENV not in text, f"{path}: an older image has no such directory"
        assert not HUB_FORCED_OFFLINE.search(text), path
        for target in _compose_targets(path):
            assert not _covers(target), (path, target)
        for mount in re.findall(r"pipeshub_hf_cache:(\S+)", text):
            assert mount == "/root/.cache/huggingface", (path, mount)


def test_the_helm_chart_needs_no_volume_or_env_for_the_models() -> None:
    for path in HELM:
        text = path.read_text()
        assert not DOCLING_OWN_VARIABLE.search(text) and DOCLING_ARTIFACTS_ENV not in text, path
        assert not HUB_FORCED_OFFLINE.search(text), path
        for target in re.findall(r"mountPath:\s*(\S+)", text):
            assert not _covers(target), (path, target)


def test_the_guard_recognises_every_way_of_covering_the_models() -> None:
    covering = ["/opt", "/opt/", '"/opt"', "'/opt/docling-models'", "/opt/docling-models/x", "/"]
    assert all(_covers(t) for t in covering)
    assert not any(_covers(t) for t in ["/optional", "/opt/zammad/storage", "/root/.cache/huggingface", "relative/opt"])


def test_the_offline_guard_recognises_every_way_of_forcing_the_hub_offline() -> None:
    forcing = [
        "- HF_HUB_OFFLINE=1",
        'HF_HUB_OFFLINE: "1"',
        "HF_HUB_OFFLINE: 1",
        "- HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}",
        "- name: HF_HUB_OFFLINE\n  value: \"1\"",
        "- name: HF_HUB_OFFLINE\n  value: 'true'",
        '- name: "HF_HUB_OFFLINE"\n  value: 1',
    ]
    assert all(HUB_FORCED_OFFLINE.search(t) for t in forcing), [t for t in forcing if not HUB_FORCED_OFFLINE.search(t)]
    allowed = [
        "- HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-}",
        "HF_HUB_OFFLINE: ${HF_HUB_OFFLINE:-}",
        "- name: HF_HUB_OFFLINE\n  value: \"\"",
        "- name: HF_HUB_OFFLINE\n  value: \"0\"",
        "HF_HUB_OFFLINE: 0",
        "# HF_HUB_OFFLINE is unset",
    ]
    assert not any(HUB_FORCED_OFFLINE.search(t) for t in allowed), [t for t in allowed if HUB_FORCED_OFFLINE.search(t)]


def test_the_deployment_guide_names_the_directory() -> None:
    guide = (REPO / "deployment" / "docker-compose" / "ADVANCED_DEPLOYMENT.md").read_text()
    assert DOCLING_ARTIFACTS_DEFAULT in guide and "BUNDLE_TABLEFORMER_FAST" in guide
    assert "downloads its 145 MB" not in guide and DOCLING_ARTIFACTS_ENV in guide

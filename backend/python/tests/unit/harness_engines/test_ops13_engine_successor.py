"""OPS-13 (ADR-OPS-01 D6, UP-02): a release declares which retired versions of an engine its version continues.

An allowlist entry's ``resumes`` lists earlier versions of its own kind; a run pinned to one of them that the
release no longer lists resumes on the successor's image. Node reads the same field as ``RELEASE_ENGINE_SUCCESSORS``
(``contracts/harness/codegen/generate.py``); the two reads must agree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.binary import PinnedBinary
from harness_core.engines.types import EngineVersion
from harness_engines.release import (
    BINARY_PATHS,
    ENGINE_IMAGE_REGISTRY_ENV,
    InvalidEngineSuccessor,
    release_allowlist,
    release_binary_pins,
    release_images,
    release_registry,
    release_successors,
    run_image,
)

if TYPE_CHECKING:
    from types import ModuleType

    from pydantic import JsonValue

REPO = Path(__file__).resolve().parents[5]
GENERATOR = REPO / "contracts" / "harness" / "codegen" / "generate.py"
NEW_DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "e" * 64
OPENCODE_IMAGE = "pxh-opencode-tools:1.19.0-ph5"
CODEX_IMAGE = "pxh-codex-tools:0.170.0-ph5"


def _entry(kind: str, version: str, image: str, **extra: JsonValue) -> dict[str, JsonValue]:
    return {"kind": kind, "version": version, "binaryDigest": NEW_DIGEST, "image": image, **extra}


def _successor_release() -> str:
    """opencode 1.19.0 continues 1.18.34 and 1.18.30; codex 0.170.0 continues 0.160.0; neither old one is listed."""
    return json.dumps(
        {
            "engines": [
                _entry("opencode", "1.19.0", OPENCODE_IMAGE, cpus=2, imageDigest="sha256:" + "d" * 64,
                       resumes=["1.18.34", "1.18.30"]),
                _entry("codex", "0.170.0", CODEX_IMAGE, resumes=["0.160.0"]),
                _entry("codex", "0.165.0", "pxh-codex-tools:0.165.0-ph5", resumes=["0.160.0"]),
            ]
        }
    )


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contracts_codegen_generate_ops13", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ops13_an_entry_parses_the_versions_it_resumes() -> None:
    """OPS-13: ``resumes`` is read into ``EngineRelease.resumes`` in order; an entry without it resumes nothing."""
    opencode, codex, older_codex = release_allowlist(_successor_release())
    assert opencode.resumes == ("1.18.34", "1.18.30")
    assert codex.resumes == ("0.160.0",)
    assert older_codex.resumes == ("0.160.0",)
    assert all(r.resumes == () for r in release_allowlist())


@pytest.mark.parametrize(
    ("resumes", "message"),
    [
        ("1.18.34", "must be a list"),
        ([1], "not a version"),
        ([""], "not a version"),
        (["1.19.0"], "its own version"),
        (["1.18.34", "1.18.34"], "1.18.34 twice"),
    ],
)
def test_ops13_a_malformed_resumes_is_refused(resumes: JsonValue, message: str) -> None:
    """OPS-13: only a list of distinct version strings other than the entry's own is a declaration."""
    raw = json.dumps({"engines": [_entry("opencode", "1.19.0", OPENCODE_IMAGE, resumes=resumes)]})
    with pytest.raises(InvalidEngineSuccessor, match=message):
        _ = release_allowlist(raw)


def test_ops13_a_version_the_release_still_lists_is_never_resumed_by_another() -> None:
    """OPS-13: a listed version always runs itself; declaring a successor for it is refused, naming the fix."""
    raw = json.dumps(
        {
            "engines": [
                _entry("opencode", "1.19.0", OPENCODE_IMAGE, resumes=["1.18.34"]),
                {**_entry("opencode", "1.18.34", "pxh-opencode-tools:1.18.34-ph5"), "binaryDigest": OLD_DIGEST},
            ]
        }
    )
    with pytest.raises(InvalidEngineSuccessor, match="still lists; remove it from the allowlist first") as refused:
        _ = release_allowlist(raw)
    assert refused.value.reason_code == "invalid_engine_successor"


def test_ops13_each_resumed_version_maps_to_its_first_declaring_successor() -> None:
    """OPS-13: ``kind@old`` -> the kind's first entry declaring it, as Node's table reads it."""
    releases = release_allowlist(_successor_release())
    successors = {old: r.version.ref for old, r in release_successors(releases).items()}
    assert successors == {
        "opencode@1.18.34": "opencode@1.19.0",
        "opencode@1.18.30": "opencode@1.19.0",
        "codex@0.160.0": "codex@0.170.0",
    }


@pytest.mark.parametrize("registry", [None, "mirror.example:5000/pxh"])
def test_ops13_a_run_pinned_to_a_resumed_version_gets_the_successors_image_pin_and_cpus(
    monkeypatch: pytest.MonkeyPatch, registry: str | None
) -> None:
    """OPS-13: the worker's controller config resolves ``kind@old`` (whatever old digest its run pinned) to the
    successor's run image (under the configured registry, D9), its release image digest and its CPUs."""
    from app.harness.engines.worker import controller_config

    if registry is None:
        monkeypatch.delenv(ENGINE_IMAGE_REGISTRY_ENV, raising=False)
    else:
        monkeypatch.setenv(ENGINE_IMAGE_REGISTRY_ENV, registry)
    releases = release_allowlist(_successor_release())
    config = controller_config(releases)
    new = releases[0]
    old = EngineVersion("opencode", "1.18.34", OLD_DIGEST)
    image = config.images[old]
    assert image == run_image(OPENCODE_IMAGE)
    assert image == (OPENCODE_IMAGE if registry is None else f"{registry}/{OPENCODE_IMAGE}")
    assert config.image_pins.get(old) == new.image_digest
    assert config.resources_for(image).cpus == 2
    assert config.images.get(EngineVersion("codex", "0.160.0", OLD_DIGEST)) == run_image(CODEX_IMAGE)
    # No release pin for codex 0.170.0's image: the resumed run gets none either (the broker reports one).
    assert config.image_pins.get(EngineVersion("codex", "0.160.0", OLD_DIGEST)) is None
    # A version no entry resumes still has no image: the controller refuses it as before.
    assert config.images.get(EngineVersion("opencode", "1.17.0", OLD_DIGEST)) is None
    assert set(config.images) == {r.version for r in releases}


def test_ops13_the_broker_allows_and_vouches_for_the_successor_image() -> None:
    """OPS-13: the image a resumed run asks for is the successor's own run image: the broker allows it and its
    binary pin (keyed by image name, EH2) is the successor's binary, so ``_vouch`` passes."""
    releases = release_allowlist(_successor_release())
    new = releases[0]
    assert run_image(new.image) in release_images(releases)
    assert release_binary_pins(releases)[run_image(new.image)] == (PinnedBinary(new.version, BINARY_PATHS["opencode"]),)


def test_ops13_python_and_node_read_the_same_successors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OPS-13 cohesion: ``_release_engine_successors()`` (Node's ``RELEASE_ENGINE_SUCCESSORS``) and
    ``release_successors`` agree, for a declaring allowlist and for the shipped one."""
    generator = _generator()
    allowlist = tmp_path / "allowlist.json"
    _ = allowlist.write_text(_successor_release(), encoding="utf-8")
    monkeypatch.setattr(generator, "ALLOWLIST", allowlist)
    python = {old: r.version.ref for old, r in release_successors(release_allowlist(_successor_release())).items()}
    assert generator._release_engine_successors() == python  # pyright: ignore[reportPrivateUsage]
    monkeypatch.undo()
    shipped = {old: r.version.ref for old, r in release_successors().items()}
    assert _generator()._release_engine_successors() == shipped  # pyright: ignore[reportPrivateUsage]


def test_ops13_the_registry_admits_a_run_pinned_to_a_resumed_version() -> None:
    """OPS-13 (WP-99): a resumed run pinned to ``opencode@1.18.34`` gets the successor's adapter, the registry names
    the successor it resumes on, and the successor itself, once the kernel moved the profile to it, is admitted
    exactly (its own binary digest). A listed version and an undeclared one are never moved."""
    releases = release_allowlist(_successor_release())
    registry = release_registry(releases=releases)
    old = EngineVersion("opencode", "1.18.34", OLD_DIGEST)
    adapter = registry.adapter_for(old)
    assert adapter.kind == "opencode"
    assert registry.resumed_on(old) == releases[0].version
    assert registry.adapter_for(releases[0].version) is adapter
    assert registry.resumed_on(releases[0].version) is None
    assert registry.resumed_on(EngineVersion("opencode", "1.17.0", OLD_DIGEST)) is None
    assert registry.resumed_on(EngineVersion("codex", "0.160.0", OLD_DIGEST)) == releases[1].version

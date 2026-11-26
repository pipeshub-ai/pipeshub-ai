"""RG-1, RG-2 (E0), EP-14 (E4a): the registry and the default profile from the pinned release allowlist."""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.errors import EngineVersionNotAllowed
from harness_core.engines.isolation import OVERRIDE_ENV
from harness_core.engines.types import (
    EngineMode,
    EngineVersion,
    ProfileApprovals,
    RunLimits,
    ToolCeiling,
)
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing.engines.contract import (
    EngineRegistryContract,
    RegistryHarness,
)
from harness_engines.release import (
    DuplicateEngineVersion,
    default_versions,
    release_allowlist,
    release_binary_pins,
    release_default_profile,
    release_default_wire,
    release_images,
    release_profiles,
    release_registry,
)

ACCESS = AccessRouteRef("route-1", "d1")


def _opencode() -> EngineVersion:
    return next(r.version for r in release_allowlist() if r.version.kind == "opencode")


def test_rg1_the_packaged_allowlist_pins_opencode_by_binary_digest() -> None:
    version = _opencode()
    assert version.version == "1.18.34"
    assert version.binary_digest == "sha256:4fa437184846977f1849ffb65fc19d32686dec85a455df3d37f69a2993f22182"
    assert release_images() == frozenset(
        {"pxh-opencode-tools:1.18.34-ph7", "pxh-codex-tools:0.160.0-ph7", "pxh-claude-code-tools:2.1.319-ph7", "pxh-gemini-tools:0.63.0-ph7", "pxh-goose-tools:1.57.0-ph7", "pxh-qwen-tools:0.26.5-ph7", "pxh-dsh-tools:0.2.2-rc.1-ph7"}
    )


def _codex() -> EngineVersion:
    return next(r.version for r in release_allowlist() if r.version.kind == "codex")


def test_pn1_the_packaged_allowlist_pins_codex_by_asset_and_binary_digest() -> None:
    version = _codex()
    assert version.version == "0.160.0"
    assert version.binary_digest == "sha256:d9244ef26a5193aec701e61c2f9280d44040efbedb9c16b4c0c6f41e354bcdd3"
    entry = next(
        e
        for e in json.loads((Path(__file__).resolve().parents[3] / "harness_engines" / "allowlist.json").read_text())["engines"]
        if e["kind"] == "codex"
    )
    assert (entry["image"], entry["variants"]["engine"]) == ("pxh-codex-tools:0.160.0-ph7", "pxh-codex:0.160.0")
    assert entry["asset"] == {
        "name": "codex-app-server-x86_64-unknown-linux-musl.tar.gz",
        "url": "https://github.com/openai/codex/releases/download/rust-v0.160.0/codex-app-server-x86_64-unknown-linux-musl.tar.gz",
        "sha256": "d0a05909836d4a73634641b89c7a62fd99c0978cdfe7ae9b8b631e9ee6d9da46",
        "member": "codex-app-server-x86_64-unknown-linux-musl",
    }


def test_pn1_the_release_registry_gives_codex_its_adapter_and_refuses_another_digest() -> None:
    registry = release_registry()
    assert registry.adapter_for(_codex()).kind == "codex"
    with pytest.raises(EngineVersionNotAllowed) as refused:
        registry.adapter_for(dataclasses.replace(_codex(), binary_digest="sha256:" + "a" * 64))
    assert refused.value.reason_code == "digest_mismatch"


def test_pn2_opencode_stays_the_default_engine_and_codex_follows() -> None:
    # An ACP engine comes last: it is opt-in per org (ACP-driver), never the default.
    assert [p.engine.kind for p in release_profiles(ACCESS)] == ["opencode", "codex", "claude_code", "acp-opencode", "acp-gemini", "acp-goose", "acp-qwen", "acp-dsh"]


def _claude_code() -> EngineVersion:
    return next(r.version for r in release_allowlist() if r.version.kind == "claude_code")


def test_pn1cc_claude_code_is_pinned_by_binary_digest_and_supplied_by_the_admin() -> None:
    """D29: no release asset to download; the admin's binary must hash to the tested version's digest."""
    version = _claude_code()
    assert version.version == "2.1.319"
    assert version.binary_digest == "sha256:dabafec82ca1319fa43d8be36a10fc61b1d11821cb98ad5df04ad95e199b8eed"
    release = next(r for r in release_allowlist() if r.version == version)
    # CCPY: runs use the tools variant, so the Python Claude Code writes runs in its own shell.
    assert (release.image, release.supply, dict(release.variants)) == (
        "pxh-claude-code-tools:2.1.319-ph7",
        "admin",
        {"engine": "pxh-claude-code:2.1.319", "tools": "pxh-claude-code-tools:2.1.319-ph7"},
    )
    entry = next(
        e
        for e in json.loads((Path(__file__).resolve().parents[3] / "harness_engines" / "allowlist.json").read_text())["engines"]
        if e["kind"] == "claude_code"
    )
    assert "asset" not in entry
    assert {r.supply for r in release_allowlist() if r.version.kind != "claude_code"} == {"release"}


def test_pn1cc_the_release_registry_gives_claude_code_its_adapter_and_its_model_api() -> None:
    from harness_core.access.types import ModelApi
    from harness_engines.release import release_model_apis

    registry = release_registry()
    assert registry.adapter_for(_claude_code()).kind == "claude_code"
    with pytest.raises(EngineVersionNotAllowed) as refused:
        registry.adapter_for(dataclasses.replace(_claude_code(), binary_digest="sha256:" + "a" * 64))
    assert refused.value.reason_code == "digest_mismatch"
    assert release_model_apis()["claude_code"] == frozenset({ModelApi.ANTHROPIC_MESSAGES})


def test_pn1cc_an_unknown_supply_is_refused() -> None:
    raw = json.dumps(
        {"engines": [{"kind": "opencode", "version": "1", "binaryDigest": "sha256:" + "b" * 64, "image": "x", "supply": "vendor"}]}
    )
    with pytest.raises(ValueError, match="unknown supply"):
        release_allowlist(raw)


def test_rg1_the_release_registry_gives_the_pinned_version_its_adapter_and_refuses_another_digest() -> None:
    registry = release_registry()
    assert registry.adapter_for(_opencode()).kind == "opencode"
    with pytest.raises(EngineVersionNotAllowed) as refused:
        registry.adapter_for(dataclasses.replace(_opencode(), binary_digest="sha256:" + "a" * 64))
    assert refused.value.reason_code == "digest_mismatch"


def test_rg1_a_deployment_pin_replaces_the_packaged_one() -> None:
    raw = json.dumps({"engines": [{"kind": "opencode", "version": "9.9.9", "binaryDigest": "sha256:" + "b" * 64, "image": "x:9"}]})
    releases = release_allowlist(raw)
    assert release_images(releases) == frozenset({"x:9"})
    with pytest.raises(EngineVersionNotAllowed):
        release_registry(releases=releases).adapter_for(_opencode())


@dataclass
class _ReleaseHarness:
    registry: object
    allowed: EngineVersion
    unregistered_kind: EngineVersion


class TestReleaseRegistryContract(EngineRegistryContract):
    @pytest.fixture
    def registry_harness(self) -> RegistryHarness:
        jcode = EngineVersion("jcode", "0.1.0", "sha256:" + "c" * 64)
        opencode = _opencode()
        raw = json.dumps(
            {
                "engines": [
                    {"kind": v.kind, "version": v.version, "binaryDigest": v.binary_digest, "image": f"img-{v.kind}"}
                    for v in (opencode, jcode, EngineVersion("codex", "0.160.0", "sha256:" + "d" * 64))
                ]
            }
        )
        registry = release_registry(releases=release_allowlist(raw))
        return _ReleaseHarness(registry, opencode, jcode)  # type: ignore[return-value]


def test_rg2_ep14_the_release_default_profile_comes_from_release_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    profile = release_default_profile(_opencode(), ACCESS)
    assert profile.mode is EngineMode.BUILD
    assert not profile.sub_agents.enabled
    assert profile.limits == RunLimits(max_steps=1000, max_wall_s=30 * 60)
    assert profile.isolation_min is RuntimeMode.GVISOR
    assert profile.egress == "none"
    assert profile.tools == ToolCeiling()
    assert profile.approvals == ProfileApprovals(read="allow", workspace="allow", write="ask", execute="ask")
    assert profile.skills is None
    assert profile.reasoning_effort is None


def test_ep14_every_pinned_engine_gets_the_default_in_allowlist_order() -> None:
    jcode = {"kind": "jcode", "version": "0.1.0", "binaryDigest": "sha256:" + "c" * 64, "image": "j"}
    raw = json.dumps({"engines": [jcode, {**jcode, "kind": "opencode", "image": "o"}]})
    profiles = release_profiles(ACCESS, raw=raw)
    assert [p.engine.kind for p in profiles] == ["jcode", "opencode"]
    assert {p.limits for p in profiles} == {RunLimits(max_steps=1000, max_wall_s=30 * 60)}


def test_ep14_a_deployment_pin_may_carry_its_own_defaults() -> None:
    raw = json.loads(
        (Path(__file__).resolve().parents[3] / "harness_engines" / "allowlist.json").read_text(encoding="utf-8")
    )
    raw["defaultProfile"] = release_default_wire()
    raw["defaultProfile"]["limits"]["maxSteps"] = 20
    profiles = release_profiles(ACCESS, raw=json.dumps(raw))
    assert {p.limits.max_steps for p in profiles} == {20}


def test_sel1_every_opencode_run_uses_the_tools_image_and_the_engine_only_image_is_not_a_run_image() -> None:
    """E0b SEL-1: the allowlist's ``image`` is the one selection point; both variants carry the same pin."""
    release = next(r for r in release_allowlist() if r.version.kind == "opencode")
    assert release.image == "pxh-opencode-tools:1.18.34-ph7"
    assert dict(release.variants) == {"engine": "pxh-opencode:1.18.34", "tools": "pxh-opencode-tools:1.18.34-ph7"}
    assert release.variants["engine"] not in release_images()


def test_pf01_opencode_sandboxes_get_two_cpus_and_the_other_engines_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """WP-17: opencode's boot is CPU-bound (~0.4 s faster on 2 CPUs), so its release asks for
    2; the worker's turns and the broker's warm shapes read the same per-engine setting, so warm sandboxes match."""
    from app.harness.engines.worker import controller_config
    from app.sandbox_broker_main import warm_pool_config
    from harness_core.engines.controller import engine_sandbox_spec, engine_warm_shapes
    from harness_core.sandbox.pool import shape_of
    from harness_core.workspace.sync.strategies import TAR

    releases = release_allowlist()
    config = controller_config(releases)
    monkeypatch.setenv("SANDBOX_WARM_POOL_MAX", "2")
    # Every engine installed, so each one's warm shape is configured (the broker warms installed engines only).
    monkeypatch.setattr("app.sandbox_broker_main.uninstalled_engines", lambda: ())
    broker = warm_pool_config(frozenset({RuntimeMode.DOCKER_HARDENED}))
    assert broker is not None
    cpus: dict[str, float] = {}
    for release in releases:
        spec = engine_sandbox_spec(
            config, image=release.image, mode=RuntimeMode.DOCKER_HARDENED, workspace=TAR.mount(config.workspace_root),
            labels={}, placeholders=("pxh-model-x", "pxh-tools-x"), tools_upstream="http://t/x",
        )
        cpus[release.version.kind] = spec.resources.cpus
        (shape,) = engine_warm_shapes(config, [release.image], RuntimeMode.DOCKER_HARDENED)
        assert shape == shape_of(spec)
        assert shape in broker.shapes, f"the broker's warm {release.version.kind} sandboxes would never be taken"
    # opencode over ACP runs the same image (the same binary's boot): it gets opencode's CPUs.
    opencode = next(r.image for r in releases if r.version.kind == "opencode")
    on_opencode = {r.version.kind for r in releases if r.image == opencode}
    assert {kind: c for kind, c in cpus.items() if kind in on_opencode} == dict.fromkeys(on_opencode, 2.0)
    assert {kind: c for kind, c in cpus.items() if kind not in on_opencode} == {
        kind: 1.0 for kind in cpus if kind not in on_opencode
    }
    assert {"codex", "claude_code"} <= cpus.keys() - on_opencode
    entry = {"kind": "opencode", "version": "9.9.9", "binaryDigest": "sha256:" + "b" * 64, "image": "x:1", "cpus": 0}
    with pytest.raises(ValueError, match="cpus"):
        release_allowlist(json.dumps({"engines": [entry]}))


def test_it4_an_image_that_names_no_variant_is_refused() -> None:
    entry = {"kind": "opencode", "version": "9.9.9", "binaryDigest": "sha256:" + "b" * 64, "image": "x:other",
             "variants": {"engine": "x:engine", "tools": "x:tools"}}
    with pytest.raises(ValueError, match="none of its variants"):
        release_allowlist(json.dumps({"engines": [entry]}))
    entry["image"] = "x:engine"
    assert release_allowlist(json.dumps({"engines": [entry]}))[0].image == "x:engine"


def test_sel3_only_the_release_module_reads_the_allowlist_and_its_variants() -> None:
    """E0b SEL-3: no second switch picks an image (profiles pick engines, E4a); the broker and the
    controller get images from ``release_images`` / ``EngineRelease.image`` only."""
    root = Path(__file__).resolve().parents[3]
    pattern = re.compile(r"[\"']allowlist\.json[\"']|ALLOWLIST_RESOURCE|\.variants\b")
    readers = sorted(
        str(path.relative_to(root))
        for package in ("harness_core", "harness_engines", "app")
        for path in (root / package).rglob("*.py")
        if path.name != "release.py" and pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    )
    assert readers == [], readers


@pytest.mark.parametrize(
    ("ceiling", "image"),
    [
        (None, "pxh-opencode-tools:1.18.34-ph7"),
        ({"engines": ["opencode"]}, "pxh-opencode-tools:1.18.34-ph7"),
        ({"engines": ["opencode@1.18.34"]}, "pxh-opencode-tools:1.18.34-ph7"),
        ({"engines": ["codex"]}, "pxh-codex-tools:0.160.0-ph7"),
    ],
)
async def test_sel4_the_engine_e4a_resolves_runs_in_its_release_image(ceiling: dict[str, object] | None, image: str) -> None:
    """E0b SEL-4: profiles pick an engine version (release default, or an org ceiling by ``kind`` or
    ``kind@version``); the image is the release's for that version, as stage 1's host maps it."""
    from harness_core.engines.memory import InMemoryEngineCeilings
    from harness_core.engines.profiles import EngineProfileResolver

    releases = release_allowlist()
    ceilings = InMemoryEngineCeilings()
    if ceiling is not None:
        ceilings.store("org-1", ceiling)  # type: ignore[arg-type]
    resolver = EngineProfileResolver(lambda access: release_profiles(access, releases), ceilings)
    profile = await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
    images = {r.version: r.image for r in releases}
    assert images[profile.engine] == image


def _side_by_side() -> str:
    """The packaged opencode entry, then an older opencode version with its own image (C14.01)."""
    newer = _opencode()
    older = EngineVersion("opencode", "1.17.0", "sha256:" + "e" * 64)
    return json.dumps(
        {
            "engines": [
                {"kind": v.kind, "version": v.version, "binaryDigest": v.binary_digest, "image": f"pxh-opencode-tools:{v.version}-ph7"}
                for v in (newer, older)
            ]
        }
    )


def test_up01_two_versions_of_an_engine_run_side_by_side() -> None:
    """UP-01 / C14.01: the allowlist holds N versions of one engine; the registry admits each by its own digest,
    the broker allows each image and pins each image's binary, and the first entry is the default version."""
    releases = release_allowlist(_side_by_side())
    newer, older = (r.version for r in releases)
    registry = release_registry(releases=releases)
    assert registry.adapter_for(newer).kind == "opencode"
    assert registry.adapter_for(older).kind == "opencode"
    with pytest.raises(EngineVersionNotAllowed) as refused:
        registry.adapter_for(dataclasses.replace(older, binary_digest=newer.binary_digest))
    assert refused.value.reason_code == "digest_mismatch"
    assert release_images(releases) == frozenset({"pxh-opencode-tools:1.18.34-ph7", "pxh-opencode-tools:1.17.0-ph7"})
    pins = release_binary_pins(releases)
    assert [p.engine for p in pins["pxh-opencode-tools:1.17.0-ph7"]] == [older]
    assert default_versions(releases) == {"opencode": newer}
    assert [p.engine for p in release_profiles(ACCESS, releases)] == [newer, older]


def test_up01_one_engine_version_pinned_twice_is_refused() -> None:
    entry = {"kind": "opencode", "version": "1", "binaryDigest": "sha256:" + "b" * 64, "image": "x:1"}
    with pytest.raises(DuplicateEngineVersion, match="opencode@1 is pinned twice"):
        release_allowlist(json.dumps({"engines": [entry, {**entry, "binaryDigest": "sha256:" + "c" * 64}]}))


@pytest.mark.parametrize(
    ("ceiling", "version"),
    [
        (None, "1.18.34"),
        ({"engines": ["opencode"]}, "1.18.34"),
        ({"engines": ["opencode@1.17.0"]}, "1.17.0"),
        ({"engines": ["opencode@1.17.0", "opencode@1.18.34"]}, "1.18.34"),
    ],
)
async def test_ad01_an_org_runs_the_versions_its_ceiling_turns_on(ceiling: dict[str, object] | None, version: str) -> None:
    """AD-01 / C11.01 on the worker's resolver: an org ceiling naming ``kind@version`` turns versions on one by
    one; the default version is the first the ceiling allows, as Node's ``resolveEngineProfile`` decides."""
    from harness_core.engines.memory import InMemoryEngineCeilings
    from harness_core.engines.profiles import EngineProfileResolver

    releases = release_allowlist(_side_by_side())
    ceilings = InMemoryEngineCeilings()
    if ceiling is not None:
        ceilings.store("org-1", ceiling)  # type: ignore[arg-type]
    resolver = EngineProfileResolver(lambda access: release_profiles(access, releases), ceilings)
    profile = await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
    assert profile.engine.version == version


def test_r23perf_fu2_qwen_sandboxes_get_at_least_3_gib_and_the_other_engines_the_default() -> None:
    """R23PERF-FU-2: Qwen Code's tree-sitter-bash tier-up peaks near 1.9 GB in a session whose live set is ~300 MB,
    so its release sets a memory floor; a larger default still wins and an org's cap still lowers it."""
    from app.harness.engines.worker import controller_config
    from harness_core.engines.controller import engine_sandbox_spec
    from harness_core.sandbox.types import ResourceCaps, Resources
    from harness_core.workspace.sync.strategies import TAR

    releases = release_allowlist()
    config = controller_config(releases)

    def memory(image: str, resources: Resources | None = None, caps: ResourceCaps | None = None) -> int:
        chosen = config if resources is None else dataclasses.replace(config, resources=resources)
        return engine_sandbox_spec(
            chosen, image=image, mode=RuntimeMode.DOCKER_HARDENED, workspace=TAR.mount(chosen.workspace_root),
            labels={}, placeholders=("pxh-model-x", "pxh-tools-x"), tools_upstream="http://t/x", resource_caps=caps,
        ).resources.memory_mb

    qwen = next(r.image for r in releases if r.version.kind == "acp-qwen")
    assert {r.version.kind: memory(r.image) for r in releases if r.image != qwen} == {
        r.version.kind: Resources().memory_mb for r in releases if r.image != qwen
    }
    assert memory(qwen) == 3072
    assert memory(qwen, Resources(memory_mb=4096)) == 4096
    assert memory(qwen, caps=ResourceCaps(memory_mb=2048)) == 2048
    for bad in (0, -1, True, 1.5, "3072"):
        entry = {"kind": "opencode", "version": "9.9.9", "binaryDigest": "sha256:" + "b" * 64, "image": "x:1",
                 "minMemoryMb": bad}
        with pytest.raises(ValueError, match="minMemoryMb"):
            release_allowlist(json.dumps({"engines": [entry]}))

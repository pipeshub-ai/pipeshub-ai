"""R32-TRUST (ADR-SEC-01 D7): the ACP profiles are trusted by a signature, the release key's or a test stack's own
test root, never because engines run below gVisor; a test root is refused on a release build; an untrusted profile is
named as such, never "not installed"."""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.harness.sandbox.engine_images import EngineImageAvailability
from harness_core.engines.isolation import OVERRIDE_ENV
from harness_core.sandbox.types import RuntimeMode
from harness_core.supply_chain.signatures import verify_detached
from harness_core.testing.clock import ManualClock
from harness_engines import release
from harness_engines.acp import registry
from harness_engines.acp.registry import AGENTS_RESOURCE, AGENTS_SIGNATURE_RESOURCE
from harness_engines.profile_trust import (
    ALLOW_TEST_ROOT_ENV,
    ROOT_FILE,
    SIGNATURE_FILE,
    TEST_ROOT_ENV,
    UNSIGNED_ENV,
    ProfileTestRootRefused,
    read_test_root,
)
from harness_engines.vendor_rules import RELEASE_KEYS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

REPO = Path(__file__).resolve().parents[5]
SIGNER = REPO / "scripts" / "engines" / "sign_vendor_rules.py"
SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
ACP = ["acp-dsh", "acp-gemini", "acp-goose", "acp-opencode", "acp-qwen"]


def _signer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sign_vendor_rules", SIGNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    for cached in (registry.packaged_acp_agents, read_test_root):
        cached.cache_clear()
    yield
    for cached in (registry.packaged_acp_agents, read_test_root):
        cached.cache_clear()


@pytest.fixture
def stack_root(tmp_path: Path) -> Path:
    """What run.sh writes for a signed stack: the tree's own signing tool, a fresh key, the private half forgotten."""
    folder = tmp_path / "test-trust"
    done = subprocess.run(
        [sys.executable, str(SIGNER), "--test-root", str(folder)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().startswith("test-")
    return folder


def _kinds(env: dict[str, str]) -> list[str]:
    return sorted(p.engine.kind for p in release.acp_profiles(env))


class TestTheVerifier:
    def test_profiles_signed_by_the_stacks_test_root_are_trusted(self, stack_root: Path) -> None:
        assert _kinds({TEST_ROOT_ENV: str(stack_root)}) == ACP

    def test_a_tampered_signature_installs_nothing(self, stack_root: Path) -> None:
        signature = json.loads((stack_root / SIGNATURE_FILE).read_text())
        other = Ed25519PrivateKey.generate().sign(SHIPPED)
        signature["sig"] = base64.b64encode(other).decode()
        (stack_root / SIGNATURE_FILE).write_text(json.dumps(signature))
        assert _kinds({TEST_ROOT_ENV: str(stack_root)}) == []

    def test_profiles_changed_after_signing_install_nothing(self, stack_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tampered = SHIPPED.replace(b'"opencode (ACP)"', b'"opencode (evil)"')
        assert tampered != SHIPPED
        packaged = registry._packaged  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(
            registry, "_packaged", lambda name: tampered if name == AGENTS_RESOURCE else packaged(name)
        )
        assert _kinds({TEST_ROOT_ENV: str(stack_root)}) == []

    def test_the_tree_holds_no_signature_and_git_ignores_one(self) -> None:
        assert resources.files("harness_engines").joinpath(AGENTS_SIGNATURE_RESOURCE).is_file() is False
        assert registry.release_signed() is False
        ignored = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", f"backend/python/harness_engines/{AGENTS_SIGNATURE_RESOURCE}"],
            check=False,
        )
        assert ignored.returncode == 0


class TestTheTestRoot:
    def test_a_release_build_refuses_a_test_root_unless_the_dev_flag_allows_it(
        self, stack_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(release, "release_signed", lambda: True)
        with pytest.raises(ProfileTestRootRefused, match="release build"):
            release.acp_profiles({TEST_ROOT_ENV: str(stack_root)})
        with pytest.raises(ProfileTestRootRefused):
            release.acp_profiles({TEST_ROOT_ENV: str(stack_root), ALLOW_TEST_ROOT_ENV: "yes"})
        assert _kinds({TEST_ROOT_ENV: str(stack_root), ALLOW_TEST_ROOT_ENV: "true"}) == ACP

    @pytest.mark.parametrize("key_id", ["pipeshub-release-test", *RELEASE_KEYS])
    def test_a_test_key_carries_a_test_id_and_never_a_release_keys(self, stack_root: Path, key_id: str) -> None:
        root = json.loads((stack_root / ROOT_FILE).read_text())
        (stack_root / ROOT_FILE).write_text(json.dumps({**root, "keyId": key_id}))
        with pytest.raises(ProfileTestRootRefused, match="test-"):
            release.acp_profiles({TEST_ROOT_ENV: str(stack_root)})

    @pytest.mark.parametrize("broken", ["missing", "not_json", "short_key", "wrong_schema"])
    def test_an_unusable_root_refuses_to_start(self, stack_root: Path, broken: str) -> None:
        path = stack_root / ROOT_FILE
        root = json.loads(path.read_text())
        if broken == "missing":
            path.unlink()
        elif broken == "not_json":
            path.write_text("{")
        elif broken == "short_key":
            path.write_text(json.dumps({**root, "publicKey": base64.b64encode(b"x" * 8).decode()}))
        else:
            path.write_text(json.dumps({**root, "schema": "other/1"}))
        with pytest.raises(ProfileTestRootRefused):
            release.acp_profiles({TEST_ROOT_ENV: str(stack_root)})

    def test_the_test_key_vouches_for_the_acp_profiles_only(self, stack_root: Path) -> None:
        """Never the vendor rules, their hosts or an image: those keep the release root alone (D2)."""
        from harness_engines.trust_roots import release_trust_roots

        key_id = json.loads((stack_root / ROOT_FILE).read_text())["keyId"]
        assert all(key_id not in keys for keys in release_trust_roots().keys.values())
        signature = (stack_root / SIGNATURE_FILE).read_bytes()
        rules = resources.files("harness_engines").joinpath("vendor_rules.json").read_bytes()
        root = read_test_root(str(stack_root), frozenset(RELEASE_KEYS))
        assert verify_detached(SHIPPED, signature, {key_id: root.public_key}) == key_id
        with pytest.raises(ValueError, match="does not verify"):
            verify_detached(rules, signature, {key_id: root.public_key})

    def test_the_folder_holds_no_private_key(self, stack_root: Path) -> None:
        assert sorted(p.name for p in stack_root.iterdir()) == [SIGNATURE_FILE, ROOT_FILE]
        assert all(b"PRIVATE" not in p.read_bytes() for p in stack_root.iterdir())


class TestTheTwoSwitches:
    def test_the_gvisor_override_alone_reads_no_unsigned_profile(self) -> None:
        assert _kinds({OVERRIDE_ENV: "true"}) == []

    def test_the_unsigned_setting_alone_reads_them_and_keeps_the_gvisor_floor(self) -> None:
        env = {UNSIGNED_ENV: "true"}
        assert _kinds(env) == ACP
        assert release.release_default_wire(env=env)["isolationMin"] == RuntimeMode.GVISOR.value
        assert release.engines_without_gvisor(env) is False

    def test_both_together_are_todays_dev_stack(self) -> None:
        env = {UNSIGNED_ENV: "true", OVERRIDE_ENV: "true"}
        assert _kinds(env) == ACP
        assert release.release_default_wire(env=env)["isolationMin"] != RuntimeMode.GVISOR.value

    @pytest.mark.parametrize("value", ["1", "yes", "on", " TRUE "])
    def test_only_exactly_true_reads_unsigned_profiles(self, value: str) -> None:
        assert _kinds({UNSIGNED_ENV: value}) == (ACP if value.strip().lower() == "true" else [])


class TestWhatTheBrokerSays:
    def test_an_untrusted_profile_is_named_untrusted_and_not_only_uninstalled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(UNSIGNED_ENV, raising=False)
        monkeypatch.delenv(TEST_ROOT_ENV, raising=False)
        untrusted = release.untrusted_engines()
        assert sorted(ref.split("@")[0] for ref in untrusted) == ACP
        assert set(untrusted) <= set(release.uninstalled_engines())

    def test_a_trusted_profile_is_neither(self, stack_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TEST_ROOT_ENV, str(stack_root))
        assert release.untrusted_engines() == ()
        assert release.uninstalled_engines() == ()

    def test_the_image_availability_carries_them_to_the_health_answer(self) -> None:
        availability = EngineImageAvailability(
            object(),  # type: ignore[arg-type]
            {"pxh-here:1": ("opencode@1", "acp-opencode@1")},
            ManualClock(),
            uninstalled=("acp-opencode@1",),
            untrusted=("acp-opencode@1",),
        )
        assert availability.untrusted == ("acp-opencode@1",)


class TestTheReleaseBuildStep:
    def test_a_release_build_signs_the_profiles_with_a_listed_key_and_nothing_else(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tested with a throwaway key: listed in a copy of the key table the tool reads, never the shipped one."""
        signer = _signer()
        key = Ed25519PrivateKey.generate()
        pem = tmp_path / "throwaway.pem"
        pem.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        keys = tmp_path / "vendor_rules.py"
        keys.write_text(f'    "pipeshub-release-throwaway": base64.b64decode("{public}"),\n')
        monkeypatch.setattr(signer, "PYTHON_KEYS", keys)
        rules_before = signer.SIGNATURE.read_bytes()
        out = tmp_path / AGENTS_SIGNATURE_RESOURCE
        signer.sign_acp(pem, "pipeshub-release-throwaway", out)
        assert verify_detached(SHIPPED, out.read_bytes(), {"pipeshub-release-throwaway": base64.b64decode(public)})
        assert signer.SIGNATURE.read_bytes() == rules_before, "the release build step signs the profiles only"
        with pytest.raises(SystemExit, match="RELEASE_KEYS"):
            signer.sign_acp(pem, "pipeshub-release-unlisted", tmp_path / "other.sig")

    def test_the_ceremony_no_longer_signs_the_profiles_into_the_tree(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")
        ceremony = source[source.index("def sign(") : source.index("def sign_acp(")]
        assert "ACP_AGENTS" not in ceremony

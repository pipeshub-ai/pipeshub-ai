"""IM-1 (E0), IM-1c (E8), IT-1..IT-3 (E0b): an engine image build refuses an asset or a binary that is not
the pinned one, for every engine and image variant, and builds a variant as its Dockerfile target and
allowlist tag; an engine without variants builds as before.

Each engine's ``build.sh`` (a wrapper of ``engine-build.sh``) runs with a stub ``docker`` on PATH that records
its argv and the build context: a refused build must stop before ``docker build``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import cast

import pytest

from harness_engines.release import CANDIDATE_LABEL

REPO = Path(__file__).resolve().parents[5]
IMAGES = REPO / "deployment" / "sandbox-images"
ALLOWLIST = REPO / "backend" / "python" / "harness_engines" / "allowlist.json"
# R3: every build context carries ph and the static /pipeshub files from here.
PIPESHUB = REPO / "backend" / "python" / "harness_engines" / "pipeshub"
ENGINES = ("opencode", "codex")

pytestmark = pytest.mark.skipif(shutil.which("sha256sum") is None, reason="needs coreutils sha256sum")


def _entry(kind: str) -> dict[str, object]:
    return dict(next(e for e in json.loads(ALLOWLIST.read_text())["engines"] if e["kind"] == kind))


def _asset(path: Path, member: str, binary: bytes) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(binary)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(binary))
    path.write_bytes(buffer.getvalue())
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _run(
    tmp_path: Path,
    kind: str,
    asset: Path,
    allowlist: dict[str, object] | None = None,
    *,
    variant: str | None = None,
    tag: str | None = "pxh-e8-im1:never",
    version: str | None = None,
) -> tuple[int, str, bool]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    marker = tmp_path / "docker-called"
    # The context is the last argument; its listing shows what the build can COPY.
    (stub / "docker").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {marker}\nfor last; do :; done\nls "$last" > {marker}.context\n'
    )
    (stub / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}
    script = IMAGES / kind / "build.sh"
    if allowlist is not None:
        # A copy of the image tree whose allowlist pins the test asset.
        root = tmp_path / "repo"
        (root / "backend" / "python" / "harness_engines").mkdir(parents=True)
        (root / "backend" / "python" / "harness_engines" / "allowlist.json").write_text(json.dumps(allowlist))
        shutil.copytree(PIPESHUB, root / "backend" / "python" / "harness_engines" / "pipeshub")
        shutil.copytree(IMAGES, root / "deployment" / "sandbox-images")
        script = root / "deployment" / "sandbox-images" / kind / "build.sh"
    argv = ["sh", str(script), "--asset", str(asset)]
    argv += ["--tag", tag] if tag is not None else []
    argv += ["--variant", variant] if variant is not None else []
    argv += ["--version", version] if version is not None else []
    done = subprocess.run(
        argv,
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )
    return done.returncode, done.stdout + done.stderr, marker.exists()


def _pinned(kind: str, asset_sha: str, binary_sha: str) -> dict[str, object]:
    entry = _entry(kind)
    entry["binaryDigest"] = f"sha256:{binary_sha}"
    entry["asset"] = {**entry["asset"], "sha256": asset_sha}  # type: ignore[dict-item]
    return {"engines": [entry]}


def _member(kind: str) -> str:
    return str(_entry(kind)["asset"]["member"])  # type: ignore[index]


BUILDS = [("opencode", None), ("opencode", "engine"), ("opencode", "tools"), ("codex", None), ("codex", "engine")]


@pytest.mark.parametrize(("kind", "variant"), BUILDS)
def test_im1_an_asset_that_is_not_the_pinned_one_stops_the_build(tmp_path: Path, kind: str, variant: str | None) -> None:
    asset = tmp_path / "asset.tar.gz"
    _asset(asset, _member(kind), b"not the engine")
    code, output, docker_called = _run(tmp_path, kind, asset, variant=variant)
    assert code != 0
    assert "FAILED" in output
    assert not docker_called


@pytest.mark.parametrize(("kind", "variant"), BUILDS)
def test_im1_a_binary_that_is_not_the_pinned_one_stops_the_build(tmp_path: Path, kind: str, variant: str | None) -> None:
    asset = tmp_path / "asset.tar.gz"
    asset_sha = _asset(asset, _member(kind), b"a different binary")
    code, output, docker_called = _run(tmp_path, kind, asset, _pinned(kind, asset_sha, "0" * 64), variant=variant)
    assert code != 0
    assert "FAILED" in output
    assert not docker_called


@pytest.mark.parametrize("kind", ENGINES)
def test_im1_the_pinned_asset_and_binary_reach_docker_build(tmp_path: Path, kind: str) -> None:
    asset = tmp_path / "asset.tar.gz"
    binary = b"the pinned binary"
    asset_sha = _asset(asset, _member(kind), binary)
    code, output, docker_called = _run(tmp_path, kind, asset, _pinned(kind, asset_sha, hashlib.sha256(binary).hexdigest()))
    assert code == 0, output
    assert docker_called


def _built(tmp_path: Path, kind: str, variant: str | None) -> tuple[list[str], set[str]]:
    asset = tmp_path / "asset.tar.gz"
    binary = b"the pinned binary"
    asset_sha = _asset(asset, _member(kind), binary)
    pinned = _pinned(kind, asset_sha, hashlib.sha256(binary).hexdigest())
    code, output, docker_called = _run(tmp_path, kind, asset, pinned, variant=variant, tag=None)
    assert code == 0, output
    assert docker_called
    argv = (tmp_path / "docker-called").read_text().splitlines()
    return argv, set((tmp_path / "docker-called.context").read_text().split())


@pytest.mark.parametrize(
    ("kind", "variant", "target", "tag"),
    [
        ("opencode", None, "tools", "pxh-opencode-tools:1.18.34-ph7"),
        ("opencode", "tools", "tools", "pxh-opencode-tools:1.18.34-ph7"),
        ("opencode", "engine", "engine", "pxh-opencode:1.18.34"),
        # EMPTYOUT: Codex runs on the tools image too; its tools/ is opencode's, through a symlink.
        ("codex", None, "tools", "pxh-codex-tools:0.160.0-ph7"),
        ("codex", "engine", "engine", "pxh-codex:0.160.0"),
    ],
)
def test_it2_a_variant_is_built_as_its_target_and_tagged_from_the_allowlist(
    tmp_path: Path, kind: str, variant: str | None, target: str, tag: str
) -> None:
    """E0b IT-2: the default variant is the one runs use (the allowlist's ``image``)."""
    argv, context = _built(tmp_path, kind, variant)
    assert argv[argv.index("--target") + 1] == target
    assert f"VARIANT={target}" in argv
    assert argv[argv.index("-t") + 1] == tag
    assert {"Dockerfile", "engine-binary", "relay", "licenses", "tools"} <= context


def test_it2_an_engine_without_variants_builds_as_before(tmp_path: Path) -> None:
    """E0b IT-2 on the shared build: an entry without variants gets no target, no VARIANT and its own image tag."""
    asset = tmp_path / "asset.tar.gz"
    binary = b"the pinned binary"
    pinned = _pinned("codex", _asset(asset, _member("codex"), binary), hashlib.sha256(binary).hexdigest())
    entry = pinned["engines"][0]  # type: ignore[index]
    del entry["variants"]
    entry["image"] = "pxh-codex:0.160.0"
    code, output, docker_called = _run(tmp_path, "codex", asset, pinned, tag=None)
    assert code == 0 and docker_called, output
    argv = (tmp_path / "docker-called").read_text().splitlines()
    assert "--target" not in argv and not any(a.startswith("VARIANT=") for a in argv)
    assert argv[argv.index("-t") + 1] == "pxh-codex:0.160.0"


@pytest.mark.parametrize(("kind", "variant"), [("opencode", "gpu"), ("codex", "gpu")])
def test_it2_an_unknown_variant_is_a_usage_error(tmp_path: Path, kind: str, variant: str) -> None:
    asset = tmp_path / "asset.tar.gz"
    _asset(asset, _member(kind), b"x")
    code, _output, docker_called = _run(tmp_path, kind, asset, variant=variant)
    assert code == 2
    assert not docker_called


def _two_versions(asset_sha: str, binary_sha: str) -> dict[str, object]:
    """opencode's pinned entry, then an older version beside it with its own tags (C14.01)."""
    newer = _pinned("opencode", asset_sha, binary_sha)["engines"][0]  # type: ignore[index]
    older = json.loads(json.dumps(newer).replace("1.18.34", "1.17.0"))
    return {"engines": [newer, older]}


@pytest.mark.parametrize(
    ("version", "tag"),
    [(None, "pxh-opencode-tools:1.18.34-ph7"), ("1.18.34", "pxh-opencode-tools:1.18.34-ph7"), ("1.17.0", "pxh-opencode-tools:1.17.0-ph7")],
)
def test_up01_version_builds_the_named_version_and_the_default_is_the_first(
    tmp_path: Path, version: str | None, tag: str
) -> None:
    """UP-01 / C14.01: with two versions of an engine side by side, ``--version`` picks the entry; without it
    the kind's first entry (its default version) is built."""
    asset = tmp_path / "asset.tar.gz"
    binary = b"the pinned binary"
    pinned = _two_versions(_asset(asset, _member("opencode"), binary), hashlib.sha256(binary).hexdigest())
    code, output, docker_called = _run(tmp_path, "opencode", asset, pinned, tag=None, version=version)
    assert code == 0 and docker_called, output
    argv = (tmp_path / "docker-called").read_text().splitlines()
    assert argv[argv.index("-t") + 1] == tag


def test_up01_a_version_the_allowlist_does_not_pin_stops_the_build(tmp_path: Path) -> None:
    asset = tmp_path / "asset.tar.gz"
    binary = b"the pinned binary"
    pinned = _two_versions(_asset(asset, _member("opencode"), binary), hashlib.sha256(binary).hexdigest())
    code, output, docker_called = _run(tmp_path, "opencode", asset, pinned, tag=None, version="9.9.9")
    assert code != 0
    assert "opencode@9.9.9 is not in the allowlist" in output
    assert not docker_called


TOOLS = IMAGES / "opencode" / "tools"
LOCK = TOOLS / "requirements.lock"
TOP_LEVEL = {
    "jinja2", "matplotlib", "numpy", "openpyxl", "pandas", "pdfplumber", "pypdf", "python-docx", "python-pptx",
    "reportlab", "scipy", "seaborn", "tabulate", "xlsxwriter",
}
NODE_TOP = {"docx": "9.7.1", "exceljs": "4.4.0", "pdf-lib": "1.17.1", "pdfkit": "0.20.2", "pptxgenjs": "4.0.1"}
NODE_LOCK = TOOLS / "node" / "package-lock.json"


def _runtime_note() -> object:
    spec = importlib.util.spec_from_file_location("runtime_note", TOOLS / "runtime_note.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it3_every_package_is_pinned_by_version_and_wheel_hash() -> None:
    text = LOCK.read_text()
    entries = re.findall(r"^([a-z0-9._-]+)==(\S+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", text, re.M)
    pinned = [line for line in text.splitlines() if "==" in line]
    assert len(entries) == len(pinned) > len(TOP_LEVEL)
    top, deps = cast("tuple[dict[str, str], dict[str, str]]", _runtime_note().read_lock(LOCK))  # type: ignore[attr-defined]
    assert set(top) == TOP_LEVEL
    assert not set(top) & set(deps)
    assert {re.sub(r"[-_.]+", "-", name) for name, _version, _hashes in entries} == set(top) | set(deps)


# SKILLPACKS (SK-03): the office skill packs' Node packages, one lock for every engine's tools image.
def test_sk03_the_node_lock_pins_docx_and_exceljs_and_every_package_by_integrity() -> None:
    lock = json.loads(NODE_LOCK.read_text())
    assert lock["lockfileVersion"] == 3
    manifest = json.loads((TOOLS / "node" / "package.json").read_text())
    assert manifest["dependencies"] == lock["packages"][""]["dependencies"] == NODE_TOP
    # The sandbox image's pins (deployment/sandbox): the packs' guidance was written against these versions.
    sandbox = json.loads((REPO / "deployment" / "sandbox" / "package.json").read_text())
    assert {name: sandbox["dependencies"][name] for name in manifest["dependencies"]} == manifest["dependencies"]
    packages = {key: entry for key, entry in lock["packages"].items() if key}
    assert len(packages) > 100
    for key, entry in packages.items():
        assert re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", entry["integrity"]), key
        assert entry["resolved"].startswith("https://registry.npmjs.org/"), key
        assert not entry.get("hasInstallScript") and not entry.get("dev"), key


@pytest.mark.parametrize("kind", ["opencode", "codex", "claude-code", "acp-gemini"])
def test_sk03_every_run_image_installs_the_node_lock_with_npm_ci_and_ships_no_npm(kind: str) -> None:
    dockerfile = (IMAGES / kind / "Dockerfile").read_text()
    assert re.search(r"^ARG NODE_DEBIAN=node:24\.\d+\.\d+-bookworm-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    builder = dockerfile.split("FROM ${NODE_DEBIAN} AS node-packages", 1)[1].split("\nFROM ", 1)[0]
    assert "npm ci --omit=dev --ignore-scripts" in builder
    tools = dockerfile.split("FROM engine AS tools", 1)[1]
    assert "COPY --from=node-packages /out/node_modules/ /usr/local/lib/node_modules/" in tools
    assert "ln -s /usr/local/lib/node_modules /node_modules" in tools
    assert "/build/runtime_note.py --check /build/requirements.lock /build/node/package-lock.json" in tools
    assert "apk add --no-cache npm" not in dockerfile and "/usr/local/bin/npm" not in dockerfile
    # reportlab's GPL demo font and its unlicensed Type 1 fonts never reach an image (components.json, reportlab).
    wheels = dockerfile.split(" AS wheels\n", 1)[1].split("\nFROM ", 1)[0]
    assert "rm -f /site/reportlab/fonts/DarkGarden* /site/reportlab/fonts/*.pfb /site/reportlab/fonts/*.afm" in wheels
    assert (IMAGES / kind / "tools").resolve() == TOOLS.resolve()


def test_sk03_the_runtime_note_lists_the_node_packages_and_the_check_holds_the_tree_to_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _runtime_note()
    top, tree = note.read_node_lock(NODE_LOCK)  # type: ignore[attr-defined]
    assert top == NODE_TOP
    monkeypatch.setattr(note, "_node_version", lambda: "24.22.1")
    rendered = note.render(LOCK, NODE_LOCK)  # type: ignore[attr-defined]
    assert "Node.js 24.22.1" in rendered and "| `docx` | 9.7.1 |" in rendered and "| `exceljs` | 4.4.0 |" in rendered
    assert "Node.js" not in note.render(LOCK)  # type: ignore[attr-defined]
    for key, version in tree.items():
        folder = tmp_path / key
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"version": version}))
    (tmp_path / "node_modules" / ".package-lock.json").write_text("{}")
    assert note.installed_node_tree(tmp_path) == tree  # type: ignore[attr-defined]
    imported: list[str] = []
    monkeypatch.setattr(
        note.subprocess, "run",  # type: ignore[attr-defined]
        lambda argv, **_: imported.append(argv[-1]) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert note.check_node(NODE_LOCK, tmp_path) == []  # type: ignore[attr-defined]
    assert imported == [f"await import({json.dumps(name)})" for name in sorted(NODE_TOP)]
    (tmp_path / "node_modules" / "docx" / "package.json").write_text(json.dumps({"version": "9.7.0"}))
    extra = tmp_path / "node_modules" / "left-pad"
    extra.mkdir()
    (extra / "package.json").write_text(json.dumps({"version": "1.3.0"}))
    assert note.check_node(NODE_LOCK, tmp_path) == [  # type: ignore[attr-defined]
        "node_modules/docx: installed 9.7.0, lock 9.7.1",
        "node_modules/left-pad@1.3.0 is not in the lock",
    ]


def _unsuffixed(tag: str) -> tuple[str, int]:
    """A run image tag `<repo>:<ver>-ph<N>` as (`<repo>:<ver>`, N)."""
    found = re.fullmatch(r"(.+)-ph([0-9]+)", tag)
    assert found is not None, tag
    return found.group(1), int(found.group(2))


def _overlay(
    tmp_path: Path, *kinds: str, base_ok: bool, existing: set[str] | None = None
) -> tuple[int, str, str | None, list[str]]:
    """pipeshub/build.sh with a stub docker: `image inspect` finds only ``existing`` (default: each kind's unsuffixed
    base), `run` (the runtime-layer check) passes or fails and records the image it ran, `build` records its argv."""
    if existing is None:
        existing = {_unsuffixed(str(_entry(kind)["image"]))[0] for kind in kinds}
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    built = tmp_path / "built"
    checked = tmp_path / "checked"
    (tmp_path / "existing").write_text("".join(f"{name}\n" for name in sorted(existing)))
    (stub / "docker").write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  image) grep -qxF -- "$3" {tmp_path / "existing"} ;;\n'
        f'  run) shift $(($# - 5)); echo "$1" >> {checked}; '
        f'{"exit 0" if base_ok else "echo \'scipy: installed None, lock 1.18.1\' >&2; exit 1"} ;;\n'
        f'  build) printf "%s\\n" "$@" >> {built}; for last; do :; done; ls "$last" >> {built}; exit 0 ;;\n'
        "esac\n"
    )
    (stub / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}
    done = subprocess.run(
        ["sh", str(IMAGES / "pipeshub" / "build.sh"), *kinds],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    runs = checked.read_text().split() if checked.exists() else []
    return done.returncode, done.stdout + done.stderr, built.read_text() if built.exists() else None, runs


@pytest.mark.parametrize(("kind", "script"), [("opencode", "opencode"), ("claude_code", "claude-code")])
def test_sk03_the_ph_overlay_refuses_a_base_without_the_run_images_runtime_layer(tmp_path: Path, kind: str, script: str) -> None:
    """SKILLPACKS: a run image is scipy and Node too; the overlay adds only ph, so it names the full build."""
    code, output, built, _ = _overlay(tmp_path, kind, base_ok=False)
    assert code == 1 and built is None
    assert f"lacks the runtime layer {_entry(kind)['image']} pins" in output
    assert f"deployment/sandbox-images/{script}/build.sh" in output and "scipy: installed None" in output


def test_sk03_the_ph_overlay_builds_over_a_base_that_carries_the_runtime_layer(tmp_path: Path) -> None:
    code, output, built, runs = _overlay(tmp_path, "opencode", base_ok=True)
    assert code == 0, output
    assert built is not None
    tag = str(_entry("opencode")["image"])
    base = _unsuffixed(tag)[0]
    assert f"BASE={base}" in built and tag in built and runs == [base]
    assert {"Dockerfile", "ph.py", "AGENTS.md", "VERSION", "install.sh"} <= set(built.split())
    assert "runtime_note.py" not in built


@pytest.mark.parametrize("kind", ["opencode", "codex", "claude_code", "acp-gemini"])
def test_ov1_without_the_unsuffixed_base_the_overlay_builds_over_the_newest_older_run_image(tmp_path: Path, kind: str) -> None:
    """A host that never had the pre-R3 base still has the earlier run images: the newest -ph<M> below the pinned N
    is the base (the overlay's Dockerfile replaces its /pipeshub and ph whole), and the runtime-layer check runs on it."""
    tag = str(_entry(kind)["image"])
    base, n = _unsuffixed(tag)
    assert n >= 3, tag
    older = {f"{base}-ph{m}" for m in range(1, n)}
    code, output, built, runs = _overlay(tmp_path, kind, base_ok=True, existing=older | {f"{base}-ph{n + 1}"})
    assert code == 0, output
    assert built is not None
    assert f"BASE={base}-ph{n - 1}" in built.split() and tag in built.split()
    assert runs == [f"{base}-ph{n - 1}"]


def test_ov1_the_unsuffixed_base_wins_over_an_older_run_image(tmp_path: Path) -> None:
    tag = str(_entry("codex")["image"])
    base, n = _unsuffixed(tag)
    code, output, built, runs = _overlay(tmp_path, "codex", base_ok=True, existing={base, f"{base}-ph{n - 1}"})
    assert code == 0, output
    assert built is not None and f"BASE={base}" in built.split() and runs == [base]


@pytest.mark.parametrize(("kind", "script"), [("codex", "codex"), ("acp-gemini", "acp-gemini")])
def test_ov2_without_any_usable_base_the_overlay_refuses_and_names_the_full_build(tmp_path: Path, kind: str, script: str) -> None:
    """Neither the unsuffixed base nor an older run image: only a newer one, which is never a base."""
    tag = str(_entry(kind)["image"])
    base, n = _unsuffixed(tag)
    code, output, built, runs = _overlay(tmp_path, kind, base_ok=True, existing={f"{base}-ph{n + 1}"})
    assert code == 1 and built is None and runs == []
    assert f"no base for {tag} on this host" in output
    assert f"deployment/sandbox-images/{script}/build.sh" in output


def test_ov3_a_run_image_already_on_the_host_is_kept_not_rebuilt(tmp_path: Path) -> None:
    """Run image tags are immutable: other checkouts run the image under that tag, so it is never built over."""
    tag = str(_entry("opencode")["image"])
    base, _ = _unsuffixed(tag)
    code, output, built, runs = _overlay(tmp_path, "opencode", base_ok=True, existing={base, tag})
    assert code == 0, output
    assert built is None and runs == []
    assert f"{tag} is already on this host" in output


def test_ov4_the_built_image_carries_the_harness_owner_label(tmp_path: Path) -> None:
    code, output, built, _ = _overlay(tmp_path, "opencode", base_ok=True)
    assert code == 0, output
    assert built is not None
    argv = built.split("\n")
    assert argv[argv.index("--label") + 1] == "pipeshub.harness.owner=x-lead"


def test_ov5_by_default_the_overlay_builds_every_run_image_the_allowlist_pins_once(tmp_path: Path) -> None:
    """Gemini's run image ships ph too; acp-opencode runs opencode's image, built once."""
    images = {str(e["image"]) for e in json.loads(ALLOWLIST.read_text())["engines"]}
    existing = {_unsuffixed(image)[0] for image in images}
    code, output, built, runs = _overlay(tmp_path, base_ok=True, existing=existing)
    assert code == 0, output
    assert built is not None
    argv = built.split("\n")
    tags = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-t"]
    assert sorted(tags) == sorted(images)
    assert sorted(runs) == sorted(existing)


# IM-1cc, LC-1 (E9): Claude Code is supplied by the admin (D29): no asset, no download, only their binary.
CLAUDE_DIR = IMAGES / "claude-code"


def _run_admin(
    tmp_path: Path, *args: str, allowlist: dict[str, object] | None = None, kind_dir: str = "claude-code"
) -> tuple[int, str, bool]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    marker = tmp_path / "docker-called"
    (stub / "docker").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {marker}\nfor last; do :; done\nls "$last" > {marker}.context\n'
    )
    (stub / "docker").chmod(0o755)
    # A curl that leaves a mark: an admin build must never download anything.
    (stub / "curl").write_text(f"#!/bin/sh\ntouch {tmp_path / 'curl-called'}\nexit 7\n")
    (stub / "curl").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}
    script = IMAGES / kind_dir / "build.sh"
    if allowlist is not None:
        root = tmp_path / "repo"
        (root / "backend" / "python" / "harness_engines").mkdir(parents=True)
        (root / "backend" / "python" / "harness_engines" / "allowlist.json").write_text(json.dumps(allowlist))
        shutil.copytree(PIPESHUB, root / "backend" / "python" / "harness_engines" / "pipeshub")
        shutil.copytree(IMAGES, root / "deployment" / "sandbox-images")
        script = root / "deployment" / "sandbox-images" / kind_dir / "build.sh"
    done = subprocess.run(["sh", str(script), *args], capture_output=True, text=True, env=env, check=False, timeout=60)
    assert not (tmp_path / "curl-called").exists()
    return done.returncode, done.stdout + done.stderr, marker.exists()


def _admin_pin(binary: bytes) -> dict[str, object]:
    entry = _entry("claude_code")
    entry["binaryDigest"] = f"sha256:{hashlib.sha256(binary).hexdigest()}"
    return {"engines": [entry]}


def test_im1cc_the_admin_build_needs_the_admins_binary(tmp_path: Path) -> None:
    code, output, docker_called = _run_admin(tmp_path)
    assert code != 0 and "--binary" in output
    assert not docker_called


def test_im1cc_a_binary_that_is_not_the_tested_version_stops_the_build(tmp_path: Path) -> None:
    binary = tmp_path / "claude"
    binary.write_bytes(b"some other claude build")
    code, output, docker_called = _run_admin(tmp_path, "--binary", str(binary))
    assert code != 0 and "FAILED" in output
    assert not docker_called


def test_im1cc_an_asset_is_refused_for_an_admin_engine_and_a_binary_for_a_released_one(tmp_path: Path) -> None:
    binary = tmp_path / "claude"
    binary.write_bytes(b"x")
    code, _, docker_called = _run_admin(tmp_path, "--binary", str(binary), "--asset", str(binary))
    assert code != 0 and not docker_called
    code, output, docker_called = _run_admin(tmp_path, "--binary", str(binary), kind_dir="codex")
    assert code != 0 and "admin-supplied engines only" in output and not docker_called


def test_im1cc_the_tested_binary_reaches_docker_build_and_nothing_else_of_it(tmp_path: Path) -> None:
    content = b"the tested claude binary"
    binary = tmp_path / "claude"
    binary.write_bytes(content)
    code, output, docker_called = _run_admin(tmp_path, "--binary", str(binary), allowlist=_admin_pin(content))
    assert code == 0, output
    assert docker_called
    argv = (tmp_path / "docker-called").read_text().splitlines()
    # CCPY: the default variant is the run image, with Python; tools/ is opencode's, copied through the symlink.
    assert argv[argv.index("-t") + 1] == "pxh-claude-code-tools:2.1.319-ph7"
    assert (argv[argv.index("--target") + 1], "VARIANT=tools" in argv) == ("tools", True)
    assert "ENGINE_VERSION=2.1.319" in argv
    assert f"ENGINE_SHA256={hashlib.sha256(content).hexdigest()}" in argv
    context = set((tmp_path / "docker-called.context").read_text().split())
    # pipeshub/: ph and the static /pipeshub files (R3), in every engine's context.
    assert context == {"Dockerfile", "engine-binary", "licenses", "pipeshub", "relay", "tools"}


def test_ccpy_the_engine_only_variant_keeps_its_tag_and_the_lock_is_opencodes(tmp_path: Path) -> None:
    content = b"the tested claude binary"
    binary = tmp_path / "claude"
    binary.write_bytes(content)
    code, output, _ = _run_admin(tmp_path, "--binary", str(binary), "--variant", "engine", allowlist=_admin_pin(content))
    assert code == 0, output
    argv = (tmp_path / "docker-called").read_text().splitlines()
    assert (argv[argv.index("--target") + 1], argv[argv.index("-t") + 1]) == ("engine", "pxh-claude-code:2.1.319")
    # One lock for every engine: the Debian image installs the same pins from their glibc wheels.
    assert (CLAUDE_DIR / "tools").resolve() == TOOLS.resolve()


def test_ccpy_every_compiled_pin_has_a_wheel_for_alpine_and_for_debian() -> None:
    """Same versions on both bases: a pin with a native module carries its musllinux and its manylinux hash."""
    entries = re.findall(r"^([a-z0-9._-]+)==(\S+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", LOCK.read_text(), re.M)
    compiled = {
        "numpy", "pandas", "matplotlib", "contourpy", "fonttools", "kiwisolver", "lxml", "markupsafe", "pillow", "scipy",
        "cffi", "charset-normalizer", "cryptography", "pypdfium2",
    }
    counts = {name: hashes.count("--hash") for name, _version, hashes in entries}
    assert {name for name, count in counts.items() if count == 2} == compiled
    assert {count for name, count in counts.items() if name not in compiled} == {1}


def test_lc1_nothing_of_claude_code_is_in_the_repository() -> None:
    """D29: only PipesHub's layers are committed; the admin's binary never is."""
    assert sorted(p.relative_to(CLAUDE_DIR).as_posix() for p in CLAUDE_DIR.rglob("*") if p.is_file()) == [
        "Dockerfile",
        "build.sh",
        "licenses/NOTICE.md",
        "scan.sh",
    ]
    for path in (p for p in IMAGES.rglob("*") if p.is_file()):
        assert path.stat().st_size < 5 * 1024 * 1024, path
        with path.open("rb") as handle:
            assert handle.read(4) != b"\x7fELF", path
    notice = (CLAUDE_DIR / "licenses" / "NOTICE.md").read_text()
    assert "does not distribute Claude Code" in notice
    assert "not modified" in notice


# CB-1..CB-5 (CC1): a candidate build verifies against the digest on the command line and never touches the
# allowlist; its image carries a candidate tag and label no deployment runs.
CANDIDATE = "9.9.9"
CANDIDATE_LABEL_ARG = f"{CANDIDATE_LABEL}=true"


def _candidate_args(kind: str, tmp_path: Path, binary: bytes, digest: str) -> tuple[str, list[str]]:
    """The image directory and the supply flags of a candidate of ``kind`` whose binary is ``binary``."""
    if kind == "claude_code":
        path = tmp_path / "claude"
        path.write_bytes(binary)
        return "claude-code", ["--binary", str(path), "--candidate", CANDIDATE, "--digest", digest]
    asset = tmp_path / "asset.tar.gz"
    _asset(asset, _member(kind), binary)
    return kind, ["--asset", str(asset), "--candidate", CANDIDATE, "--digest", digest]


@pytest.mark.parametrize("kind", ["claude_code", "codex", "opencode"])
def test_cb1_a_candidate_whose_binary_is_not_the_given_digest_stops_the_build(tmp_path: Path, kind: str) -> None:
    other = "sha256:" + hashlib.sha256(b"the binary that was tested").hexdigest()
    kind_dir, args = _candidate_args(kind, tmp_path, b"some other binary", other)
    code, output, docker_called = _run_admin(tmp_path, *args, kind_dir=kind_dir)
    assert code != 0 and "FAILED" in output
    assert not docker_called


@pytest.mark.parametrize("kind", ["claude_code", "codex"])
def test_cb2_a_candidate_is_built_under_its_candidate_tag_and_the_allowlist_is_untouched(
    tmp_path: Path, kind: str
) -> None:
    from harness_engines.release import candidate_image

    before = ALLOWLIST.read_bytes()
    content = b"the candidate binary"
    hexdigest = hashlib.sha256(content).hexdigest()
    kind_dir, args = _candidate_args(kind, tmp_path, content, f"sha256:{hexdigest}")
    code, output, docker_called = _run_admin(tmp_path, *args, kind_dir=kind_dir)
    assert code == 0, output
    assert docker_called
    argv = (tmp_path / "docker-called").read_text().splitlines()
    assert argv[argv.index("-t") + 1] == candidate_image(str(_entry(kind)["image"]), CANDIDATE)
    assert argv[argv.index("-t") + 1].endswith(f":{CANDIDATE}-candidate")
    assert f"ENGINE_VERSION={CANDIDATE}" in argv
    assert f"ENGINE_SHA256={hexdigest}" in argv
    assert argv[argv.index(CANDIDATE_LABEL_ARG) - 1] == "--label"
    assert "not admitted" in output
    assert ALLOWLIST.read_bytes() == before


def _usage_cases(tmp_path: Path) -> dict[str, tuple[str, list[str]]]:
    binary = tmp_path / "claude"
    binary.write_bytes(b"x")
    digest = "sha256:" + hashlib.sha256(b"x").hexdigest()
    asset = tmp_path / "asset.tar.gz"
    _asset(asset, _member("codex"), b"x")
    cc = ["--binary", str(binary)]
    return {
        "candidate-without-digest": ("claude-code", [*cc, "--candidate", CANDIDATE]),
        "digest-without-candidate": ("claude-code", [*cc, "--digest", digest]),
        "digest-not-sha256": ("claude-code", [*cc, "--candidate", CANDIDATE, "--digest", digest[7:]]),
        "digest-upper-case": ("claude-code", [*cc, "--candidate", CANDIDATE, "--digest", digest.upper()]),
        "tag-with-candidate": ("claude-code", [*cc, "--candidate", CANDIDATE, "--digest", digest, "--tag", "pxh-x:1"]),
        "version-not-a-tag": ("claude-code", [*cc, "--candidate", "9.9.9/../x", "--digest", digest]),
        "flag-without-value": ("claude-code", [*cc, "--candidate"]),
        "release-engine-without-asset": ("codex", ["--candidate", CANDIDATE, "--digest", digest]),
    }


@pytest.mark.parametrize(
    "case",
    [
        "candidate-without-digest",
        "digest-without-candidate",
        "digest-not-sha256",
        "digest-upper-case",
        "tag-with-candidate",
        "version-not-a-tag",
        "flag-without-value",
        "release-engine-without-asset",
    ],
)
def test_cb3_a_malformed_candidate_build_is_a_usage_error(tmp_path: Path, case: str) -> None:
    kind_dir, args = _usage_cases(tmp_path)[case]
    code, _output, docker_called = _run_admin(tmp_path, *args, kind_dir=kind_dir)
    assert code == 2
    assert not docker_called


@pytest.mark.parametrize(
    ("variant", "target", "tag"),
    [
        (None, "tools", f"pxh-opencode-tools:{CANDIDATE}-candidate"),
        ("tools", "tools", f"pxh-opencode-tools:{CANDIDATE}-candidate"),
        ("engine", "engine", f"pxh-opencode:{CANDIDATE}-candidate"),
    ],
)
def test_cb4_each_variant_of_a_candidate_is_tagged_from_its_own_repository(
    tmp_path: Path, variant: str | None, target: str, tag: str
) -> None:
    content = b"the candidate binary"
    kind_dir, args = _candidate_args("opencode", tmp_path, content, "sha256:" + hashlib.sha256(content).hexdigest())
    args += ["--variant", variant] if variant is not None else []
    code, output, docker_called = _run_admin(tmp_path, *args, kind_dir=kind_dir)
    assert code == 0, output
    assert docker_called
    argv = (tmp_path / "docker-called").read_text().splitlines()
    assert argv[argv.index("--target") + 1] == target
    assert argv[argv.index("-t") + 1] == tag


@pytest.mark.parametrize("kind", ENGINES)
def test_cb5_a_normal_build_carries_no_candidate_label(tmp_path: Path, kind: str) -> None:
    argv, _context = _built(tmp_path, kind, None)
    assert CANDIDATE_LABEL_ARG not in argv and "--label" not in argv
    assert not argv[argv.index("-t") + 1].endswith("-candidate")


def test_cb5_a_normal_admin_build_carries_no_candidate_label(tmp_path: Path) -> None:
    content = b"the tested claude binary"
    binary = tmp_path / "claude"
    binary.write_bytes(content)
    code, output, _docker_called = _run_admin(tmp_path, "--binary", str(binary), allowlist=_admin_pin(content))
    assert code == 0, output
    argv = (tmp_path / "docker-called").read_text().splitlines()
    assert "--label" not in argv
    assert "not admitted" not in output

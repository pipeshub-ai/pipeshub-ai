"""IT-5..IT-11 (E0b): the opencode tools image under the real Docker broker (runc lane).

The sandbox is what a run gets: the broker's hardened mode (read-only root, no capabilities, no
network), uid 1000, a 100 MB ``/tmp`` (the size today's ``run_code`` install step overflowed, EV1 open
item 4). Skipped without a daemon or without the image (``build.sh --variant tools``).
"""

from __future__ import annotations

import json
import re
import textwrap
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run
from tests.support.office_files import open_file
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

REPO = Path(__file__).resolve().parents[6]
ALLOWLIST = json.loads((REPO / "backend" / "python" / "harness_engines" / "allowlist.json").read_text())
OPENCODE = next(e for e in ALLOWLIST["engines"] if e["kind"] == "opencode")
IMAGE = OPENCODE["variants"]["tools"]
ENGINE_IMAGE = OPENCODE["variants"]["engine"]
TOOLS = REPO / "deployment" / "sandbox-images" / "opencode" / "tools"
COMPONENTS = json.loads((TOOLS / "licenses" / "components.json").read_text())["components"]
LEASE = LeaseRef("worker-e0b", 1)
TMP_MB = 100
SITE = "/usr/local/lib/python3.12/site-packages"
# Bundled shared library (file name prefix) -> its component in components.json.
NATIVE_FILES = {
    "libgcc_s": "libgcc_s", "libstdc++": "libstdc++", "libgfortran": "libgfortran", "libquadmath": "libquadmath",
    "libscipy_openblas64_": "scipy-openblas64", "libscipy_openblas": "scipy-openblas", "libjpeg": "libjpeg-turbo", "libpng16": "libpng16",
    "libtiff": "libtiff", "libwebp": "libwebp", "libwebpdemux": "libwebp", "libwebpmux": "libwebp",
    "libsharpyuv": "libwebp", "libavif": "libavif", "libfreetype": "freetype", "libharfbuzz": "harfbuzz",
    "liblcms2": "lcms2", "libopenjp2": "openjpeg", "libbrotlicommon": "brotli", "libbrotlidec": "brotli",
    "libzstd": "zstd", "liblzma": "liblzma", "libxcb": "libxcb", "libXau": "libxcb", "libXdmcp": "libxcb",
    "libbsd": "libbsd", "libmd": "libmd",
}


def _image_present(image: str, folder: str = "opencode") -> tuple[bool, str]:
    try:
        import docker

        client = docker.from_env()
        client.images.get(image)
        client.close()
    except Exception as exc:
        return False, f"needs Docker and {image} (deployment/sandbox-images/{folder}/build.sh): {type(exc).__name__}"
    return True, ""


@pytest.fixture
async def tools_broker() -> AsyncIterator[DockerSandboxBroker]:
    present, reason = _image_present(IMAGE)
    if not present:
        pytest.skip(reason)
    provider = DockerClientProvider(max_workers=8)
    namespace = f"io.pipeshub.e0btest.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e0b-" + namespace.rsplit(".", 1)[1]
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({IMAGE}),
        label_namespace=namespace,
        volume_prefix=prefix,
    )
    yield broker
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    provider.close()


@pytest.fixture
async def sandbox(tools_broker: DockerSandboxBroker) -> AsyncIterator[DockerSandboxHandle]:
    spec = SandboxSpec(
        image_digest=IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=TMP_MB),
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "E0b"},
    )
    handle = await tools_broker.acquire(spec, lease=LEASE, idempotency_key=f"e0b-{uuid.uuid4().hex}")
    yield handle
    await tools_broker.release(handle)


async def _sh(handle: DockerSandboxHandle, script: str, *, ok: bool = True, timeout_s: float = 120) -> str:
    out, err, code = await run(await handle.exec(["/bin/sh", "-c", script], env={}), timeout_s=timeout_s)
    if ok:
        assert code == 0, err.decode("utf-8", "replace")
    return out.decode("utf-8", "replace").strip()


async def _py(handle: DockerSandboxHandle, code: str) -> str:
    out, err, status = await run(
        await handle.exec(["python3", "-c", textwrap.dedent(code)], env={}), timeout_s=120
    )
    assert status == 0, err.decode("utf-8", "replace")
    return out.decode("utf-8", "replace").strip()


def _lock() -> dict[str, str]:
    pins = re.findall(r"^([A-Za-z0-9._-]+)==(\S+)", (TOOLS / "requirements.lock").read_text(), re.M)
    return {re.sub(r"[-_.]+", "-", name).lower(): version for name, version in pins}


def _node_top() -> dict[str, str]:
    lock = json.loads((TOOLS / "node" / "package-lock.json").read_text())["packages"]
    return {name: lock[f"node_modules/{name}"]["version"] for name in lock[""]["dependencies"]}


async def test_it5_the_tools_image_runs_the_pinned_binary_with_python_and_no_installer(sandbox: DockerSandboxHandle) -> None:
    assert await _sh(sandbox, "opencode --version") == OPENCODE["version"]
    digest = await _sh(sandbox, "sha256sum /usr/local/bin/opencode")
    assert "sha256:" + digest.split()[0] == OPENCODE["binaryDigest"]
    assert await _sh(sandbox, "id -u") == "1000"
    present = await _sh(
        sandbox, "for t in rm rg git bash pxh-relay python3 node pip pip3 npm npx; do command -v $t || echo no-$t; done"
    )
    assert not any(f"no-{tool}" in present for tool in ("rm", "rg", "git", "bash", "pxh-relay", "python3", "node"))
    assert all(f"no-{tool}" in present for tool in ("pip", "pip3", "npm", "npx"))
    assert (await _sh(sandbox, "python3 -V")).startswith("Python 3.12.")
    no_pip = await _sh(sandbox, "python3 -m pip --version 2>&1; echo exit=$?", ok=False)
    assert "No module named pip" in no_pip and "exit=1" in no_pip


async def test_it5_labels_name_the_pinned_binary_and_the_variant() -> None:
    present, reason = _image_present(IMAGE)
    if not present:
        pytest.skip(reason)
    import docker

    client = docker.from_env()
    try:
        labels = client.images.get(IMAGE).labels
    finally:
        client.close()
    assert labels["io.pipeshub.engine.kind"] == "opencode"
    assert labels["io.pipeshub.engine.version"] == OPENCODE["version"]
    assert labels["io.pipeshub.engine.binary-digest"] == OPENCODE["binaryDigest"]
    assert labels["io.pipeshub.engine.variant"] == "tools"


async def test_it6_every_locked_package_imports_at_its_locked_version_and_the_note_lists_them(
    sandbox: DockerSandboxHandle,
) -> None:
    installed = json.loads(
        await _py(
            sandbox,
            """
            import importlib.metadata as m, json, re
            print(json.dumps({re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version for d in m.distributions()}))
            """,
        )
    )
    assert installed == _lock()
    imported = await _py(
        sandbox,
        """
        import numpy, pandas, matplotlib, seaborn, openpyxl, xlsxwriter, docx, pptx, tabulate, jinja2, PIL, lxml, scipy
        import scipy.stats, scipy.optimize, scipy.linalg, reportlab.platypus, pypdf, pdfplumber, pypdfium2
        print("ok")
        """,
    )
    assert imported == "ok"
    note = await _sh(sandbox, "cat /usr/share/pxh-engine/RUNTIME.md")
    for name, version in _lock().items():
        assert f"{name} {version}" in note or f"| {name} | {version} |" in note, name
    assert "no package installer" in note and "no network access" in note
    for name, version in _node_top().items():
        assert f"| `{name}` | {version} |" in note, name


async def test_it6_the_node_packages_are_exactly_the_lock_and_load_from_any_folder(sandbox: DockerSandboxHandle) -> None:
    """SKILLPACKS: npm ci's tree from tools/node/package-lock.json, nothing more; `import` and `require` resolve it
    from the workspace, /tmp and the skills mount alike."""
    installed = json.loads(
        await _py(
            sandbox,
            """
            import json, pathlib
            root = pathlib.Path("/usr/local/lib")
            found = {}
            def walk(modules):
                for child in sorted(modules.iterdir()) if modules.is_dir() else ():
                    if child.name.startswith("."):
                        continue
                    for folder in sorted(child.iterdir()) if child.name.startswith("@") else [child]:
                        found[folder.relative_to(root).as_posix()] = json.loads((folder / "package.json").read_text())["version"]
                        walk(folder / "node_modules")
            walk(root / "node_modules")
            print(json.dumps(found))
            """,
        )
    )
    lock = json.loads((TOOLS / "node" / "package-lock.json").read_text())["packages"]
    assert installed == {key: entry["version"] for key, entry in lock.items() if key}
    for folder in ("/workspace", "/tmp"):
        loaded = await _sh(
            sandbox,
            f"cd {folder} && node --input-type=module -e 'import * as d from \"docx\"; import e from \"exceljs\";"
            f" console.log(typeof d.Packer, typeof e.Workbook)' && node -e 'console.log(typeof require(\"exceljs\").Workbook)'",
        )
        assert loaded == "function function\nfunction", folder
    assert (await _sh(sandbox, "node --version")).startswith("v24.")


async def test_it7_a_chart_renders_headless_as_png(sandbox: DockerSandboxHandle) -> None:
    backend = await _py(
        sandbox,
        """
        import matplotlib
        matplotlib.use(matplotlib.get_backend())
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.bar(["north", "south"], [150, 100])
        fig.savefig("/workspace/chart.png")
        print(matplotlib.get_backend())
        """,
    )
    assert backend.lower() == "agg"
    png, _err, code = await run(await sandbox.exec(["cat", "/workspace/chart.png"], env={}), timeout_s=30)
    assert code == 0 and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000


async def test_it8_the_three_office_formats_are_written_and_read_back(sandbox: DockerSandboxHandle) -> None:
    reread = await _py(
        sandbox,
        """
        import docx, pptx, openpyxl
        d = docx.Document(); d.add_heading("Summary", level=1); d.add_paragraph("Totals by region."); d.save("/workspace/r.docx")
        p = pptx.Presentation(); s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = "Q3"; p.save("/workspace/r.pptx")
        wb = openpyxl.Workbook(); ws = wb.active; ws.append(["region", "total"]); ws.append(["north", 150]); ws.append(["Total", 150]); wb.save("/workspace/r.xlsx")
        print(docx.Document("/workspace/r.docx").paragraphs[0].text, len(pptx.Presentation("/workspace/r.pptx").slides),
              openpyxl.load_workbook("/workspace/r.xlsx").active["B3"].value)
        """,
    )
    assert reread == "Summary 1 150"
    for name, kind in (("r.docx", "docx"), ("r.pptx", "pptx"), ("r.xlsx", "xlsx")):
        content, _err, code = await run(await sandbox.exec(["cat", f"/workspace/{name}"], env={}), timeout_s=30)
        assert code == 0
        view = open_file(name, content)
        assert (view.valid, view.kind) == (True, kind), view.reason


async def test_it9_imports_a_chart_and_office_files_stay_far_below_a_100_mb_tmp(sandbox: DockerSandboxHandle) -> None:
    await _py(
        sandbox,
        """
        import numpy, pandas as pd, seaborn, openpyxl, xlsxwriter, docx, pptx, tabulate, jinja2
        import matplotlib.pyplot as plt
        df = pd.DataFrame({"region": ["n", "s", "n"], "amount": [1, 2, 3]})
        df.groupby("region").amount.sum().plot.bar().figure.savefig("/workspace/c.png")
        df.to_excel("/workspace/t.xlsx", index=False)
        print(df.to_markdown())
        """,
    )
    used_kb = int((await _sh(sandbox, "du -sk /tmp")).split()[0])
    assert used_kb < 5 * 1024, f"/tmp holds {used_kb} kB"
    size_mb = int(await _sh(sandbox, "df -m /tmp | awk 'NR==2 {print $2}'"))
    assert size_mb <= TMP_MB
    read_only = await _sh(sandbox, f"touch {SITE}/x 2>&1; echo exit=$?", ok=False)
    assert "Read-only file system" in read_only


async def test_it10_nothing_can_be_installed_and_nothing_reached(sandbox: DockerSandboxHandle) -> None:
    attempt = await _py(
        sandbox,
        """
        import socket
        try:
            socket.create_connection(("151.101.0.223", 443), timeout=5)
            print("connected")
        except OSError as exc:
            print("refused", type(exc).__name__)
        """,
    )
    assert attempt.startswith("refused")
    assert "no-pip" in await _sh(sandbox, "command -v pip || echo no-pip")


async def test_it11_notices_and_the_component_manifest_match_the_image(sandbox: DockerSandboxHandle) -> None:
    notices = await _sh(sandbox, "ls /usr/share/licenses/pxh-opencode-tools /usr/share/licenses/pxh-opencode")
    for name in ("NOTICE.md", "components.json", "opencode-LICENSE", "bun-LICENSE.md"):
        assert name in notices
    assert await _sh(sandbox, "test -f /usr/local/lib/python3.12/LICENSE.txt && echo yes") == "yes"
    with_licences = await _sh(sandbox, f"ls -d {SITE}/*.dist-info | while read d; do ls $d | grep -qi licen && echo $d; done | wc -l")
    assert int(with_licences) >= len(_lock()) - 8  # a few wheels name their licence only in METADATA
    pypi = {c["name"]: c["version"] for c in COMPONENTS if c["ecosystem"] == "pypi"}
    assert pypi == _lock()
    npm = {(c["name"], c["version"]) for c in COMPONENTS if c["ecosystem"] == "npm"}
    lock = json.loads((TOOLS / "node" / "package-lock.json").read_text())["packages"]
    assert npm == {(key.rsplit("node_modules/", 1)[1], entry["version"]) for key, entry in lock.items() if key}
    natives = {c["name"] for c in COMPONENTS if c["ecosystem"] == "native"}
    files = (await _sh(sandbox, f"ls {SITE}/*.libs")).split()
    unmapped = sorted({f for f in files if f.endswith(":") is False and f.split("-")[0] not in NATIVE_FILES})
    assert unmapped == [], "bundled libraries missing from components.json"
    assert {NATIVE_FILES[f.split("-")[0]] for f in files if not f.endswith(":")} <= natives
    engine_present, _ = _image_present(ENGINE_IMAGE)
    if engine_present:
        import docker

        client = docker.from_env()
        try:
            base = set(
                client.containers.run(
                    ENGINE_IMAGE, ["apk", "info", "-q", "--no-network"], remove=True, network_mode="none",
                    name=f"pxh-e0b-apk-{uuid.uuid4().hex[:8]}",
                    labels={"pipeshub.harness.owner": "x-lead"}, stderr=False,
                ).decode().split()
            )
        finally:
            client.close()
        tools = set((await _sh(sandbox, "apk info -q --no-network 2>/dev/null")).split())
        added = {p for p in tools - base if not p.startswith(".")}
        assert added == {c["name"] for c in COMPONENTS if c["ecosystem"] == "apk"}


def _scan(script: Path, out: Path, *args: str) -> dict[str, object]:
    import subprocess

    done = subprocess.run(["sh", str(script), str(out), *args], capture_output=True, text=True, check=False, timeout=900)
    report = json.loads(next(out.glob("*.licence.json")).read_text())
    assert (done.returncode == 0) == (report["verdict"] != "fail"), done.stderr[-2000:]
    return report


@pytest.mark.parametrize("kind", ["opencode", "codex", "acp-gemini"])
def test_it13_the_runtime_layer_scan_passes_on_every_alpine_run_image(tmp_path: Path, kind: str) -> None:
    """SKILLPACKS: one runtime layer, one review: the shared approvals hold in each engine's scan."""
    entry = next(e for e in ALLOWLIST["engines"] if e["kind"] == kind)
    for image in (entry["variants"]["tools"], entry["variants"]["engine"]):
        present, reason = _image_present(image, kind)
        if not present:
            pytest.skip(reason)
    report = _scan(TOOLS / "scan.sh", tmp_path, kind)
    assert (report["verdict"], report["blocked"], report["marker_failures"]) == ("pass_with_notices", [], [])
    assert report["packages"] == len(COMPONENTS)


@pytest.mark.parametrize(("kind", "folder"), [("claude_code", "claude-code"), ("acp-dsh", "acp-dsh")])
def test_it13_a_debian_image_scan_blocks_only_debian_packages_no_approval_covers(tmp_path: Path, kind: str, folder: str) -> None:
    """The shared layer passes in the Debian images' scans (Claude Code; dsh, C2.43) as in the others; what still
    blocks is the Debian packages their apt layer installs (git, perl and their libraries), which no recorded approval
    covers: legal, not code."""
    image = next(e for e in ALLOWLIST["engines"] if e["kind"] == kind)["image"]
    present, reason = _image_present(image, folder)
    if not present:
        pytest.skip(reason)
    report = _scan(REPO / "deployment" / "sandbox-images" / folder / "scan.sh", tmp_path)
    # The run layer (CPython, the wheels, the npm packages) is in the scanned tarball, the engine's own files are not.
    assert any(path.startswith("usr/local/lib/python3.12/") for path in report["markers"])
    assert not any(path.startswith(("usr/local/bin/claude", "usr/local/lib/dsh/")) for path in report["markers"])
    shared = {f"{c['name']}@{c['version']}" for c in COMPONENTS}
    blocked = {b["package"] for b in report["blocked"]}
    assert report["marker_failures"] == [] and not blocked & shared
    names = {b.split("@", 1)[0] for b in blocked}
    assert {"git", "perl"} <= names
    assert names <= {
        "ca-certificates", "git", "git-man", "libcurl3-gnutls", "liberror-perl", "libgdbm-compat4", "libgdbm6",
        "libgssapi-krb5-2", "libk5crypto3", "libkeyutils1", "libkrb5-3", "libkrb5support0", "libldap-2.5-0",
        "libperl5.36", "libreadline8", "librtmp1", "libsasl2-2", "libsasl2-modules-db", "libsqlite3-0", "netbase",
        "perl-modules-5.36", "perl", "readline-common",
    }

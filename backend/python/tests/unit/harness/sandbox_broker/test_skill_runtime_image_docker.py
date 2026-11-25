"""RT-3, RT-4 (SK1): the skill-runtime test images under the real Docker broker (runc lane).

A run's sandbox (hardened mode: read-only root, no capabilities, no network) on each variant: the added
packages import at the lock's versions, nothing can be installed, the Codex variant runs the pinned binary,
and the installed skills' own scripts run offline. RT-4 needs ``SK1_SKILLS_DIR`` (the skills are not in this
repository). Skipped without a daemon or the images (``deployment/sandbox-images/skill-runtime/build.sh``).
"""

from __future__ import annotations

import io
import json
import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.tar import put_files
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.skills.materializer import skills_mount
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run
from harness_core.workspace.sync.archive import Owner
from tests.unit.harness.engines.skill_packs import (
    PLACEMENTS,
    installed_dir,
    place,
    read_tree,
)
from tests.unit.harness.engines.test_real_skill_packs import inputs
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.sandbox.ports import SandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

REPO = Path(__file__).resolve().parents[6]
ALLOWLIST = json.loads((REPO / "backend" / "python" / "harness_engines" / "allowlist.json").read_text())
CODEX = next(e for e in ALLOWLIST["engines"] if e["kind"] == "codex")
IMAGES = {
    "opencode": os.environ.get("SK1_OPENCODE_IMAGE", "pxh-sk1-opencode:test"),
    "codex": os.environ.get("SK1_CODEX_IMAGE", "pxh-sk1-codex:test"),
}
LOCK = REPO / "deployment" / "sandbox-images" / "skill-runtime" / "skills.lock"
LEASE = LeaseRef("worker-sk1", 1)


def _present(image: str) -> bool:
    try:
        import docker

        client = docker.from_env()
        client.images.get(image)
        client.close()
    except Exception:
        return False
    return True


@pytest.fixture(params=sorted(IMAGES))
async def sandbox(request: pytest.FixtureRequest) -> AsyncIterator[tuple[str, SandboxHandle]]:
    engine: str = request.param
    if not _present(IMAGES[engine]):
        pytest.skip(f"needs Docker and {IMAGES[engine]} (deployment/sandbox-images/skill-runtime/build.sh {engine})")
    provider = DockerClientProvider(max_workers=8)
    namespace = f"io.pipeshub.sk1img.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-sk1i-" + namespace.rsplit(".", 1)[1]
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=ManualClock(), ids=RandomIds(), allowed_images=frozenset({IMAGES[engine]}),
        label_namespace=namespace, volume_prefix=prefix,
    )
    spec = SandboxSpec(
        image_digest=IMAGES[engine],
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=100),
        mounts=(MountSpec("/workspace"), MountSpec("/state"), skills_mount("/skills")),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "SK1"},
    )
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"sk1-{uuid.uuid4().hex}")
    try:
        yield engine, handle
    finally:
        await broker.release(handle)
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        provider.close()


async def _sh(handle: SandboxHandle, script: str) -> tuple[int, str]:
    out, err, code = await run(await handle.exec(["/bin/sh", "-c", script], env={}), timeout_s=120)
    return code, (out + err).decode("utf-8", "replace").strip()


async def test_rt3_packages_at_the_lock_versions_and_no_installer(sandbox: tuple[str, SandboxHandle]) -> None:
    engine, handle = sandbox
    pins = dict(re.findall(r"^([A-Za-z0-9._-]+)==(\S+)", LOCK.read_text(), re.M))
    script = (
        "import importlib.metadata as m, pypdf, reportlab, defusedxml, charset_normalizer, pdfplumber; "
        f"print(*(m.version(n) for n in {list(pins)!r}))"
    )
    out, err, code = await run(await handle.exec(["python3", "-c", script], env={}), timeout_s=120)
    assert (code, out.decode().split()) == (0, list(pins.values())), err
    code, out = await _sh(handle, "python3 -m pip --version")
    assert code != 0 and "No module named pip" in out
    _, note = await _sh(handle, "cat /usr/share/pxh-engine/RUNTIME.md")
    assert all(f"| {name} | {version} |" in note for name, version in pins.items())
    if engine == "codex":
        assert await _sh(handle, "codex-app-server --version 2>/dev/null") == (0, f"codex-app-server {CODEX['version']}")
        code, digest = await _sh(handle, "sha256sum /usr/local/bin/codex-app-server")
        assert "sha256:" + digest.split()[0] == CODEX["binaryDigest"]
        assert await _sh(handle, "getent passwd engine | cut -d: -f3,7") == (0, "1000:/bin/bash")


async def test_rt4_the_skills_scripts_run_offline(sandbox: tuple[str, SandboxHandle]) -> None:
    engine, handle = sandbox
    root = installed_dir(engine)
    if root is None:
        pytest.skip("SK1_SKILLS_DIR names no installed skills")
    await place(handle, PLACEMENTS[engine], read_tree(root))
    from pptx import Presentation

    deck = io.BytesIO()
    presentation = Presentation()
    for title in ("One", "Two", "Three"):
        presentation.slides.add_slide(presentation.slide_layouts[5]).shapes.title.text = title
    presentation.save(deck)
    await put_files(
        handle, {"/workspace/deck.pptx": deck.getvalue(), "/workspace/form.pdf": inputs()["in/form.pdf"]}, owner=Owner()
    )
    sk = PLACEMENTS[engine].dest
    values = json.dumps([{"field_id": "reviewer", "page": 1, "value": "Dana Reyes"}])
    for step in (
        f"python3 {sk}/pptx/scripts/office/validate.py deck.pptx",
        f"python3 {sk}/pptx/scripts/add_slide.py deck.pptx slide3.xml -o deck2.pptx",
        f"python3 {sk}/pptx/scripts/office/validate.py deck2.pptx --original deck.pptx",
        "python3 -c \"import zipfile; zipfile.ZipFile('deck2.pptx').extractall('u')\"",
        f"python3 {sk}/pptx/scripts/clean.py u",
        f"python3 {sk}/pdf/scripts/check_fillable_fields.py form.pdf",
        f"python3 {sk}/pdf/scripts/extract_form_field_info.py form.pdf fields.json",
        f"printf '%s' '{values}' > values.json && python3 {sk}/pdf/scripts/fill_fillable_fields.py form.pdf values.json filled.pdf",
    ):
        code, out = await _sh(handle, f"cd /workspace && {step}")
        assert code == 0, (step, out)
    assert (await _sh(handle, "cd /workspace && python3 -c \"from pptx import Presentation; print(len(Presentation('deck2.pptx').slides))\""))[1] == "4"

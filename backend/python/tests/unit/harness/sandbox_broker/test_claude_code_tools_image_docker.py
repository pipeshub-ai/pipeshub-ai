"""CCPY: Claude Code's run image (the ``tools`` variant) under the real Docker broker (runc lane).

The admin-built Debian image gains CPython 3.12 and opencode's lock, installed from the same pins' glibc wheels:
Claude Code runs the Python it writes in its own shell. Skipped without a daemon or without the image
(``deployment/sandbox-images/claude-code/build.sh --binary PATH``).
"""

from __future__ import annotations

import json
import uuid
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
from tests.unit.harness.engines.lane_releases import (
    lane_admits_candidates,
    lane_release,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup
from tests.unit.harness.sandbox_broker.test_engine_tools_image_docker import (
    COMPONENTS,
    NATIVE_FILES,
    SITE,
    TMP_MB,
    _image_present,  # pyright: ignore[reportPrivateUsage]
    _lock,  # pyright: ignore[reportPrivateUsage]
    _py,  # pyright: ignore[reportPrivateUsage]
    _sh,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

RELEASE = lane_release("claude_code")
IMAGE = RELEASE.image
LEASE = LeaseRef("worker-ccpy", 1)


@pytest.fixture
async def sandbox() -> AsyncIterator[DockerSandboxHandle]:
    present, reason = _image_present(IMAGE)
    if not present:
        pytest.skip(reason.replace("opencode/build.sh", "claude-code/build.sh --binary PATH"))
    provider = DockerClientProvider(max_workers=8)
    namespace = f"io.pipeshub.ccpytest.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-ccpy-" + namespace.rsplit(".", 1)[1]
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({IMAGE}),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=namespace,
        volume_prefix=prefix,
    )
    spec = SandboxSpec(
        image_digest=IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=TMP_MB),
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "CCPY"},
    )
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"ccpy-{uuid.uuid4().hex}")
    yield handle
    await broker.release(handle)
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    provider.close()


async def test_ccpy_the_run_image_has_the_pinned_binary_python_and_no_installer(sandbox: DockerSandboxHandle) -> None:
    assert (await _sh(sandbox, "claude --version")).splitlines()[-1] == f"{RELEASE.version.version} (Claude Code)"
    digest = await _sh(sandbox, "sha256sum /usr/local/bin/claude")
    assert "sha256:" + digest.split()[0] == RELEASE.version.binary_digest
    assert await _sh(sandbox, "id -u") == "1000"
    present = await _sh(
        sandbox, "for t in rm rg git bash pxh-relay python3 node pip pip3 npm npx; do command -v $t || echo no-$t; done"
    )
    assert not any(f"no-{tool}" in present for tool in ("rm", "rg", "git", "bash", "pxh-relay", "python3", "node"))
    assert all(f"no-{tool}" in present for tool in ("pip", "pip3", "npm", "npx"))
    assert await _sh(sandbox, "python3 -c 'import sys; print(sys.version_info[:2])'") == "(3, 12)"
    # SKILLPACKS: the official glibc Node build of the Alpine images' release, the same lock's packages.
    assert (await _sh(sandbox, "node --version")).startswith("v24.")
    loaded = await _sh(sandbox, "cd /workspace && node --input-type=module -e 'import e from \"exceljs\"; console.log(typeof e.Workbook)'")
    assert loaded == "function"
    no_pip = await _sh(sandbox, "python3 -m pip --version 2>&1; echo exit=$?", ok=False)
    assert "No module named pip" in no_pip and "exit=1" in no_pip
    read_only = await _sh(sandbox, f"touch {SITE}/x /usr/local/bin/x 2>&1; echo exit=$?", ok=False)
    assert read_only.count("Read-only file system") == 2


async def test_ccpy_every_locked_package_imports_at_its_locked_version_and_a_script_runs(sandbox: DockerSandboxHandle) -> None:
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
    await _py(
        sandbox,
        """
        import numpy, pandas as pd, matplotlib, seaborn, openpyxl, xlsxwriter, docx, pptx, tabulate, jinja2, PIL, lxml, scipy
        import matplotlib.pyplot as plt
        assert matplotlib.get_backend().lower() == "agg"
        df = pd.DataFrame({"region": ["n", "s", "n"], "amount": [1, 2, 3]})
        df.groupby("region").amount.sum().plot.bar().figure.savefig("/workspace/c.png")
        df.to_excel("/workspace/t.xlsx", index=False)
        """,
    )
    assert await _sh(sandbox, "head -c 4 /workspace/c.png | od -An -c | tr -d ' '") == "211PNG"
    # The deliverable shape EMPTYOUT broke: a script the engine wrote, run in its shell, its output in a file.
    out = await _sh(sandbox, "cd /workspace && printf 'print(sum(range(10)))\\n' > s.py && python3 s.py > out.txt && cat out.txt")
    assert out == "45"
    note = await _sh(sandbox, "cat /usr/share/pxh-engine/RUNTIME.md")
    assert "Python 3.12." in note and "no package installer" in note


async def test_ccpy_the_python_layers_notices_and_bundled_libraries_are_declared(sandbox: DockerSandboxHandle) -> None:
    listing = await _sh(sandbox, "ls /usr/share/licenses/pxh-claude-code-tools /usr/share/licenses/pxh-claude-code")
    for name in ("NOTICE.md", "components.json"):
        assert name in listing
    notice = await _sh(sandbox, "cat /usr/share/licenses/pxh-claude-code/NOTICE.md")
    assert "does not distribute Claude Code" in notice and "pxh-claude-code-tools" in notice
    assert await _sh(sandbox, "test -f /usr/local/lib/python3.12/LICENSE.txt && echo yes") == "yes"
    assert await _sh(sandbox, "grep -c 'Node.js is licensed for use as follows' /usr/share/licenses/nodejs/LICENSE") == "1"
    natives = {c["name"] for c in COMPONENTS if c["ecosystem"] == "native"}
    files = [f for f in (await _sh(sandbox, f"ls {SITE}/*.libs")).split() if not f.endswith(":")]
    unmapped = sorted({f for f in files if f.split("-")[0].split(".so")[0] not in NATIVE_FILES})
    assert unmapped == [], "bundled libraries missing from components.json"
    assert {NATIVE_FILES[f.split("-")[0].split(".so")[0]] for f in files} <= natives

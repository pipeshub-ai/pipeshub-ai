"""FIX-PDFSKILL: the layout checker the pdf, docx and pptx packs require runs in every engine's run image.

Under the real Docker broker in hardened mode (read-only root, no network), with the built-in packs seeded at
``/skills`` as SK-03 seeds them: ``office-utils/scripts/check_layout.py`` flags the PDF from a manual-test
chat (pdfkit positions passed inside the options object), passes the same script with the
position as arguments, and renders every page to PNG with the image's own packages. The fixed script is also run with
the image's pdfkit, so the clean result is what an engine would make there. Skipped without a daemon or an image.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.tar import put_files
from harness_core.workspace.sync.archive import Owner
from tests.unit.harness.sandbox_broker.test_sk03_builtin_packs_docker import (  # noqa: F401
    SKILLS,
    _ok,
    _sh,
    sandbox,
)

if TYPE_CHECKING:
    from harness_core.sandbox.ports import SandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

FIXTURES = Path(__file__).resolve().parents[2] / "agents" / "agent_loop" / "skills" / "fixtures" / "layout"
CHECK = f"python3 {SKILLS}/office-utils/scripts/check_layout.py"


async def _seed(handle: SandboxHandle) -> None:
    await put_files(
        handle,
        {f"/workspace/in/{name}": (FIXTURES / name).read_bytes() for name in ("ipl_broken.pdf", "ipl_fixed.pdf", "ipl_fixed.js")},
        owner=Owner(),
    )


async def test_fixpdfskill_the_report_pdf_is_flagged_and_its_pages_rendered(sandbox: SandboxHandle) -> None:  # noqa: F811
    await _seed(sandbox)

    code, out = await _sh(sandbox, f"{CHECK} in/ipl_broken.pdf")

    assert code == 1, out
    for kind in ("outside_margin", "low_contrast", "empty_box"):
        assert f"{kind}: page" in out, out
    assert "doc.text(str, x, y, {width})" in out
    listed = await _ok(sandbox, "ls layout_check/ipl_broken")
    assert listed.split() == [f"ipl_broken-page-{n}.png" for n in range(1, 6)]


async def test_fixpdfskill_the_fixed_script_makes_a_clean_pdf_with_the_images_pdfkit(sandbox: SandboxHandle) -> None:  # noqa: F811
    await _seed(sandbox)

    assert "layout: clean" in await _ok(sandbox, f"{CHECK} in/ipl_fixed.pdf")
    await _ok(sandbox, "NODE_PATH=\"${NODE_PATH:-$(dirname \"$(dirname \"$(readlink -f \"$(command -v node)\")\")\")/lib/node_modules}\" node in/ipl_fixed.js remade.pdf")
    out = await _ok(sandbox, f"{CHECK} remade.pdf --pages-dir pages")
    assert "layout: clean" in out and "5 page(s)" in out, out


async def test_fixpdfskill_the_packs_python_command_resolves(sandbox: SandboxHandle) -> None:  # noqa: F811
    """The packs write ``python skills/...``: the image must answer to ``python`` as well as ``python3``."""
    await _seed(sandbox)

    code, out = await _sh(sandbox, f"python {SKILLS}/office-utils/scripts/check_layout.py in/ipl_fixed.pdf")
    assert code == 0, out

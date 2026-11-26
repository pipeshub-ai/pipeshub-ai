"""SK1 support: skill packs a user installed with the skills manager, placed unchanged where each engine reads them.

The skills are never in this repository (their licence forbids copies): ``SK1_SKILLS_DIR`` names the directory
``npx skills add anthropics/skills --skill pptx|pdf -g --copy`` wrote (``$HOME/.agents/skills``), optionally per
engine (``SK1_SKILLS_DIR_CODEX``, ``SK1_SKILLS_DIR_OPENCODE``). ``skill_packs_manifest.json`` pins the tested
tree by path and SHA-256.

Placement happens in ``PlacingBroker``, right after the controller acquires a sandbox and before it starts the
engine; the controller, adapters and launches are the release ones. The same wrapper inspects the placed tree
from inside the sandbox (``find``, ``sha256sum``, a write attempt).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.sandbox.tar import put_files
from harness_core.testing.sandbox.contract import run
from harness_core.workspace.sync.archive import Owner

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle
    from harness_core.sandbox.types import LeaseRef, SandboxSpec

MANIFEST_PATH = Path(__file__).with_name("skill_packs_manifest.json")
SKILLS_ENV = "SK1_SKILLS_DIR"
SKILL_NAMES = ("pdf", "pptx")
# The licence's first line: a test refuses any repository file that carries it.
LICENCE_FIRST_LINE = "© 2025 Anthropic, PBC. All rights reserved."


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def installed_dir(engine: str) -> Path | None:
    raw = os.environ.get(f"{SKILLS_ENV}_{engine.upper()}") or os.environ.get(SKILLS_ENV)
    return Path(raw) if raw and Path(raw).is_dir() else None


def read_tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def tree_mismatch(root: Path) -> list[str]:
    """Paths whose presence or SHA-256 differs from the manifest (empty: the tested tree is the pinned one)."""
    pinned = {path: entry["sha256"] for path, entry in manifest()["files"].items()}
    found = {path: hashlib.sha256(data).hexdigest() for path, data in read_tree(root).items()}
    return sorted(p for p in pinned.keys() | found.keys() if pinned.get(p) != found.get(p))


@dataclass(frozen=True, slots=True)
class Placement:
    """Where an engine reads user skills in the sandbox, and how the tree gets there."""

    engine: str
    dest: str
    # opencode: the read-only skills mount (seed_tar); Codex: $CODEX_HOME/skills on the state mount (put_tar).
    read_only: bool


PLACEMENTS: Mapping[str, Placement] = {
    "opencode": Placement("opencode", "/skills", read_only=True),
    "codex": Placement("codex", "/state/codex/skills", read_only=False),
}


@dataclass(frozen=True, slots=True)
class Inspection:
    """What a process inside the sandbox saw at the placement, right after it was placed."""

    sandbox_id: str
    files: tuple[str, ...]
    digests: Mapping[str, str]
    write_exit: int
    write_error: str


async def _one(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


def _tar(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o444
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


async def place(sandbox: SandboxHandle, placement: Placement, tree: Mapping[str, bytes]) -> None:
    if placement.read_only:
        await sandbox.seed_tar(placement.dest, _one(_tar(tree)))
    else:
        # A user's own install: owned by the sandbox user, beside Codex's config and sessions.
        await put_files(sandbox, {f"{placement.dest}/{path}": data for path, data in tree.items()}, owner=Owner())


async def inspect(sandbox: SandboxHandle, placement: Placement) -> Inspection:
    listing, _, _ = await run(await sandbox.exec(["find", placement.dest, "-type", "f"], env={}))
    files = tuple(sorted(line.removeprefix(placement.dest + "/") for line in listing.decode().splitlines() if line))
    sums, _, _ = await run(
        await sandbox.exec(["/bin/sh", "-c", f'cd {placement.dest} && find . -type f -exec sha256sum {{}} +'], env={})
    )
    digests = {name.removeprefix("./"): digest for digest, name in (line.split(None, 1) for line in sums.decode().splitlines())}
    _, err, code = await run(
        await sandbox.exec(["/bin/sh", "-c", f"echo changed > {placement.dest}/pdf/SKILL.md"], env={})
    )
    return Inspection(sandbox.id, files, digests, code, err.decode("utf-8", "replace").strip())


@dataclass
class PlacingBroker:
    """The lane's broker with one step added after ``acquire``: the user's skills tree goes to the engine's path.

    It restores what it overwrote for a writable placement (the write probe), so the engine sees the tree as
    installed."""

    inner: SandboxBroker
    placement: Placement
    tree: Mapping[str, bytes]
    inspections: list[Inspection] = field(default_factory=list[Inspection])

    def modes(self) -> Any:  # noqa: ANN401 - the inner broker's
        return self.inner.modes()

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> SandboxHandle:
        sandbox = await self.inner.acquire(spec, lease=lease, idempotency_key=idempotency_key)
        await place(sandbox, self.placement, self.tree)
        self.inspections.append(await inspect(sandbox, self.placement))
        if not self.placement.read_only:
            await place(sandbox, self.placement, {"pdf/SKILL.md": self.tree["pdf/SKILL.md"]})
        return sandbox

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - release, reap, health, … unchanged
        return getattr(self.inner, name)


# Independent readers of what the engines produced (the test process's libraries, not the engine's).


def slide_texts(data: bytes) -> list[list[str]]:
    from pptx import Presentation

    deck = Presentation(io.BytesIO(data))
    out: list[list[str]] = []
    for slide in deck.slides:
        texts: list[str] = []
        for shape in slide.shapes:
            shape: Any  # python-pptx's shape types are not annotated for these members
            if shape.has_text_frame:
                texts.extend(p.text for p in shape.text_frame.paragraphs if p.text)
            if shape.has_table:
                texts.extend(cell.text for row in shape.table.rows for cell in row.cells)
        out.append(texts)
    return out


def pdf_pages(data: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def pdf_form_values(data: bytes) -> dict[str, str]:
    """Field name → value from the AcroForm, read with pdfminer (not pypdf, which the engine used)."""
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import resolve1

    document = PDFDocument(PDFParser(io.BytesIO(data)))
    values: dict[str, str] = {}
    for ref in resolve1(resolve1(document.catalog["AcroForm"])["Fields"]):
        entry = resolve1(ref)
        value = resolve1(entry.get("V"))
        values[_pdf_text(entry.get("T"))] = "" if value is None else _pdf_text(value).lstrip("/")
    return values


def _pdf_text(raw: object) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def pptx_parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def words(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


__all__ = [
    "LICENCE_FIRST_LINE",
    "MANIFEST_PATH",
    "PLACEMENTS",
    "SKILLS_ENV",
    "SKILL_NAMES",
    "Inspection",
    "Placement",
    "PlacingBroker",
    "installed_dir",
    "manifest",
    "pdf_form_values",
    "pdf_pages",
    "pptx_parts",
    "read_tree",
    "slide_texts",
    "tree_mismatch",
    "words",
]

"""Deterministic synthetic corpus for the indexing benchmark.

The same seed always yields the same file names, folders, kinds, sizes and
text. ``salt`` adds one line to every file so a rerun against the same stack
uploads new bytes: the indexer skips work for content whose MD5 it has already
indexed, and a benchmark that hits that shortcut would measure nothing.

Kinds: txt, md, html, csv (standard library), docx and xlsx (python-docx and
openpyxl, already dependencies of integration-tests) and pdf (written by hand
below, since no PDF library is a dependency here). PDF text is ASCII only
because the built-in Helvetica font cannot draw anything else.

Run ``python perf/corpus.py --docs 50 --out /tmp/corpus`` to look at one.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import random
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

KIND_WEIGHTS: dict[str, int] = {
    "txt": 30,
    "md": 20,
    "html": 15,
    "csv": 15,
    "docx": 8,
    "xlsx": 7,
    "pdf": 5,
}

MIMETYPES: dict[str, str] = {
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}

# (share of files, min bytes, max bytes), drawn log-uniformly inside a tier.
SIZE_TIERS: tuple[tuple[float, int, int], ...] = (
    (0.75, 512, 20_000),
    (0.20, 20_000, 100_000),
    (0.05, 100_000, 5_000_000),
)

# Office and PDF files are built element by element, so a 5 MB one takes
# seconds to generate and says little the text tail does not already say.
# Spreadsheets are capped too: the indexer sends table rows through the LLM
# (up to MAX_TABLE_ROWS_FOR_LLM per file), so a huge one measures the LLM.
CAPPED_KINDS = frozenset({"csv", "docx", "xlsx", "pdf"})
CAPPED_KIND_MAX_BYTES = 200_000

WORDS = (
    "the quarterly revenue report shows growth across every region while "
    "operating costs stayed flat and the team shipped search indexing "
    "connectors workflow agent customer contract renewal pipeline budget "
    "forecast onboarding security review incident retrospective roadmap "
    "milestone latency throughput vector graph embedding chunk parser"
).split() + [
    "café", "naïve", "résumé", "Straße", "données", "façade", "jalapeño",
    "Ωmega", "δelta", "日本語", "中文", "한국어", "привет", "مرحبا", "שלום",
    "🚀", "📊", "✅", "🔥", "😀", "🧪",
]

NAME_STEMS = (
    "Quarterly report",
    "Résumé draft",
    "Naïve café notes",
    "日本語メモ",
    "Straße plan",
    "Données clients",
    "Roadmap 🚀",
    "Budget 📊",
    "Meeting notes 📝",
    "Ωmega spec",
    "Привет мир",
    "Plain notes",
    "Emoji 😀 test",
    "中文 文档",
)

FOLDER_STEMS = ("Équipe", "Projets 🚀", "Archive", "日本", "Финансы", "Q3 📊", "Misc")

FILES_PER_FOLDER = 20
MAX_FOLDER_DEPTH = 3
ROOT_SHARE = 0.3

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_ISO_TIMESTAMP = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z")


@dataclass(frozen=True)
class CorpusFile:
    index: int
    name: str
    folder: tuple[str, ...]
    kind: str
    target_bytes: int
    content: bytes

    @property
    def mimetype(self) -> str:
        return MIMETYPES[self.kind]

    @property
    def rel_path(self) -> str:
        return "/".join((*self.folder, self.name))


@dataclass(frozen=True)
class Corpus:
    seed: int
    kinds: tuple[str, ...]
    folders: tuple[tuple[str, ...], ...]
    files: tuple[CorpusFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(len(f.content) for f in self.files)

    def describe(self) -> dict:
        by_kind: dict[str, int] = {}
        for f in self.files:
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        buckets = {"<20KB": 0, "20-100KB": 0, "100KB-1MB": 0, ">1MB": 0}
        for f in self.files:
            size = len(f.content)
            if size < 20_000:
                buckets["<20KB"] += 1
            elif size < 100_000:
                buckets["20-100KB"] += 1
            elif size < 1_000_000:
                buckets["100KB-1MB"] += 1
            else:
                buckets[">1MB"] += 1
        return {
            "docs": len(self.files),
            "seed": self.seed,
            "kinds": list(self.kinds),
            "folders": len(self.folders),
            "total_bytes": self.total_bytes,
            "by_kind": dict(sorted(by_kind.items())),
            "size_buckets": buckets,
        }


def generate_corpus(
    docs: int, seed: int = 1337, salt: str = "", kinds: tuple[str, ...] | None = None
) -> Corpus:
    """``kinds`` narrows the mix (keeping the relative weights); ``None`` is all of them."""
    if docs < 1:
        raise ValueError(f"docs must be at least 1, got {docs}")
    chosen = tuple(k for k in KIND_WEIGHTS if kinds is None or k in kinds)
    unknown = set(kinds or ()) - set(KIND_WEIGHTS)
    if unknown or not chosen:
        raise ValueError(f"kinds must be drawn from {sorted(KIND_WEIGHTS)}, got {kinds}")
    rng = random.Random(seed)
    folders = _folder_tree(rng, max(1, docs // FILES_PER_FOLDER))
    weights = [KIND_WEIGHTS[k] for k in chosen]

    files = []
    for index in range(docs):
        kind = rng.choices(chosen, weights)[0]
        target = _target_size(rng)
        if kind in CAPPED_KINDS:
            target = min(target, CAPPED_KIND_MAX_BYTES)
        folder = () if rng.random() < ROOT_SHARE else rng.choice(folders)
        name = f"{rng.choice(NAME_STEMS)} {index:04d}.{kind}"
        header = f"Document {index:05d} · seed {seed}" + (f" · {salt}" if salt else "")
        # A private generator per file keeps each body independent of how much
        # randomness earlier (possibly larger) files consumed.
        body_rng = random.Random(f"{seed}:{index}")
        content = _RENDERERS[kind](body_rng, header, target)
        files.append(CorpusFile(index, name, folder, kind, target, content))
    return Corpus(seed=seed, kinds=chosen, folders=tuple(folders), files=tuple(files))


def write_corpus(corpus: Corpus, out_dir: Path) -> None:
    for f in corpus.files:
        path = out_dir.joinpath(*f.folder, f.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.content)


def _folder_tree(rng: random.Random, count: int) -> list[tuple[str, ...]]:
    folders: list[tuple[str, ...]] = []
    for i in range(count):
        parents = [p for p in folders if len(p) < MAX_FOLDER_DEPTH]
        parent = rng.choice(parents) if parents and rng.random() < 0.5 else ()
        folders.append((*parent, f"{rng.choice(FOLDER_STEMS)} {i:02d}"))
    return folders


def _target_size(rng: random.Random) -> int:
    roll = rng.random()
    for share, low, high in SIZE_TIERS:
        if roll < share:
            return int(math.exp(rng.uniform(math.log(low), math.log(high))))
        roll -= share
    _, low, high = SIZE_TIERS[-1]
    return high


def _sentences(rng: random.Random, target_bytes: int) -> list[str]:
    out: list[str] = []
    size = 0
    while size < target_bytes:
        words = rng.choices(WORDS, k=rng.randint(6, 18))
        sentence = " ".join(words).capitalize() + "."
        out.append(sentence)
        size += len(sentence.encode()) + 1
    return out


def _paragraphs(rng: random.Random, target_bytes: int) -> list[str]:
    sentences = _sentences(rng, target_bytes)
    paragraphs = []
    start = 0
    while start < len(sentences):
        end = start + rng.randint(3, 7)
        paragraphs.append(" ".join(sentences[start:end]))
        start = end
    return paragraphs


def _render_txt(rng: random.Random, header: str, target: int) -> bytes:
    return "\n\n".join([header, *_paragraphs(rng, target)]).encode()


def _render_md(rng: random.Random, header: str, target: int) -> bytes:
    parts = [f"# {header}"]
    for i, para in enumerate(_paragraphs(rng, target)):
        if i % 4 == 0:
            parts.append(f"## Section {i // 4 + 1} {rng.choice(WORDS)}")
        if i % 5 == 2:
            items = rng.choices(WORDS, k=3)
            parts.append("\n".join(f"- {w}" for w in items))
        parts.append(para)
    return "\n\n".join(parts).encode()


def _render_html(rng: random.Random, header: str, target: int) -> bytes:
    body = [f"<h1>{header}</h1>"]
    for i, para in enumerate(_paragraphs(rng, target)):
        if i % 4 == 0:
            body.append(f"<h2>Section {i // 4 + 1}</h2>")
        body.append(f"<p>{para}</p>")
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{header}</title></head><body>\n" + "\n".join(body) + "\n</body></html>"
    ).encode()


def _render_csv(rng: random.Random, header: str, target: int) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "city", "amount", "note"])
    writer.writerow([0, header, "", "", ""])
    row = 1
    while buf.tell() < target:
        writer.writerow([
            row,
            f"{rng.choice(NAME_STEMS)} {row}",
            rng.choice(["Paris", "München", "東京", "São Paulo", "Москва", "Cairo"]),
            f"{rng.uniform(1, 10_000):.2f}",
            " ".join(rng.choices(WORDS, k=6)),
        ])
        row += 1
    return buf.getvalue().encode()


def _render_docx(rng: random.Random, header: str, target: int) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(header, level=1)
    for i, para in enumerate(_paragraphs(rng, target)):
        if i % 4 == 0:
            doc.add_heading(f"Section {i // 4 + 1}", level=2)
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return _normalize_zip(buf.getvalue())


def _render_xlsx(rng: random.Random, header: str, target: int) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["id", "name", "city", "amount", "note"])
    ws.append([0, header, "", None, ""])
    # Cells compress well inside the zip; ~60 bytes of raw text per row keeps
    # the sheet's text volume close to the target.
    for row in range(1, max(2, target // 60)):
        ws.append([
            row,
            f"{rng.choice(NAME_STEMS)} {row}",
            rng.choice(["Paris", "München", "東京", "São Paulo", "Москва", "Cairo"]),
            round(rng.uniform(1, 10_000), 2),
            " ".join(rng.choices(WORDS, k=6)),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return _normalize_zip(buf.getvalue())


def _render_pdf(rng: random.Random, header: str, target: int) -> bytes:
    lines: list[str] = []
    for raw in [header, *_sentences(rng, target)]:
        raw = " ".join(word for word in raw.split() if word.isascii())
        while len(raw) > 90:
            cut = raw.rfind(" ", 0, 90)
            cut = cut if cut > 0 else 90
            lines.append(raw[:cut])
            raw = raw[cut:].lstrip()
        lines.append(raw)
    pages = [lines[i:i + 50] for i in range(0, len(lines), 50)] or [[""]]
    return _pdf_document(pages)


def _pdf_escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_document(pages: list[list[str]]) -> bytes:
    # Object numbers: 1 catalog, 2 page tree, 3 font, then a page and its
    # content stream for each page.
    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, page_lines in enumerate(pages):
        ops = ["BT", "/F1 10 Tf", "12 TL", "50 750 Td"]
        ops += [f"({_pdf_escape(line)}) '" for line in page_lines]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def _normalize_zip(data: bytes) -> bytes:
    """Pin the timestamps python-docx and openpyxl stamp on every save."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = _ISO_TIMESTAMP.sub(b"2000-01-01T00:00:00Z", body)
            dst.writestr(zipfile.ZipInfo(info.filename, _FIXED_ZIP_TIME), body, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


_RENDERERS = {
    "txt": _render_txt,
    "md": _render_md,
    "html": _render_html,
    "csv": _render_csv,
    "docx": _render_docx,
    "xlsx": _render_xlsx,
    "pdf": _render_pdf,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--salt", default="")
    parser.add_argument("--kinds", default=None, help="comma-separated subset, e.g. txt,md,html")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    kinds = tuple(args.kinds.split(",")) if args.kinds else None
    corpus = generate_corpus(args.docs, args.seed, args.salt, kinds)
    write_corpus(corpus, args.out)
    print(corpus.describe())


if __name__ == "__main__":
    main()

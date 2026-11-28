"""Corpus for PX-3 (M1 to M5): large JSON, YAML, HTML, EPUB and XLSX files of known shape.

Seeded and deterministic (same ``--seed`` and profile, same bytes; ``manifest.json`` has a sha256
per file). Files are streamed to disk, never held whole, and a profile above ``--max-mb`` is refused
before anything is written.

JSON comes in the four shapes that differ most in tree memory per input byte (review r1 measured a
15x spread): ``ints`` (array of ints), ``floats``, ``objects`` (array of small objects) and
``nested`` (deeply nested lists). XLSX comes in ``tall`` (many rows, few columns) and ``wide`` (few
rows, very many columns); ``tall290k`` (290,000 rows) and ``wide16k`` (16,000 columns) are the two
workbooks M3 and T22 name. XLSX is written as raw sheet XML with inline strings (no openpyxl), so a
290,000-row workbook is generated in seconds.

    python -m tests.perf.parse_bench.px3_corpus --out DIR --profile smoke     # about 1 MiB, tiny
    python -m tests.perf.parse_bench.px3_corpus --out DIR --profile px3       # the M1 sizes, about 0.5 GiB

The smoke profile is what tests and the runbook's sanity step use; the ``px3`` profile is generated
by the coordinator when the host is free (see the handoff runbook).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

MIB = 1024 * 1024
MANIFEST = "manifest.json"

PROFILES: dict[str, dict[str, list]] = {
    "smoke": {
        "json": [("ints", 0.05), ("floats", 0.05), ("objects", 0.05), ("nested", 0.05)],
        "yaml": [0.05],
        "html": [0.05],
        "epub": [0.05],
        "xlsx": [("tall", 300, 3), ("wide", 3, 400)],
    },
    "px3": {
        "json": [(s, mb) for s in ("ints", "floats", "objects", "nested") for mb in (5, 20, 50)],
        "yaml": [1, 5, 20],
        "html": [1, 5, 20],
        "epub": [5, 50],
        "xlsx": [("tall", 40_000, 6), ("tall", 160_000, 6), ("tall290k", 290_000, 3), ("wide", 20, 8_000), ("wide16k", 4, 16_000)],
    },
}


def _json_chunks(shape: str, target: int, rng: random.Random) -> Iterator[str]:
    written = 0
    i = 0
    yield "["
    while written < target:
        if shape == "ints":
            item = ",".join(str(rng.randint(0, 10**9)) for _ in range(64))
        elif shape == "floats":
            item = ",".join(repr(rng.random() * 1000) for _ in range(32))
        elif shape == "objects":
            item = json.dumps({"id": i, "name": f"name-{rng.randint(0, 9999)}", "ok": bool(i % 2), "v": rng.random()})
        elif shape == "nested":
            item = json.dumps(_nest(rng, 10))
        else:
            raise ValueError(shape)
        text = ("," if i else "") + item
        written += len(text)
        i += 1
        yield text
    yield "]"


def _nest(rng: random.Random, depth: int) -> object:
    if depth == 0:
        return rng.randint(0, 9)
    return [_nest(rng, depth - 1) for _ in range(2 if depth > 3 else 1)]


def _yaml_chunks(target: int, rng: random.Random) -> Iterator[str]:
    written = i = 0
    while written < target:
        text = (
            f"- id: {i}\n  name: item-{rng.randint(0, 9999)}\n  tags: [alpha, beta, gamma]\n"
            f"  nested:\n    a: {rng.random():.6f}\n    b: 'text value {i}'\n"
        )
        written += len(text)
        i += 1
        yield text


def _html_chunks(target: int, rng: random.Random) -> Iterator[str]:
    yield "<html><head><title>t</title><style>p{}</style></head><body>"
    written = i = 0
    while written < target:
        text = (
            f"<div class='c'><nav>n{i}</nav><h2>Section {i}</h2><p>{'word ' * rng.randint(20, 60)}</p>"
            f"<a href='/p/{i}'>link</a><img src='/img/{i}.png' alt='a{i}'><script>x({i})</script></div>"
        )
        written += len(text)
        i += 1
        yield text
    yield "</body></html>"


def _write_text(path: Path, chunks: Iterator[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        for c in chunks:
            f.write(c)


_ZIP_EPOCH = (2020, 1, 1, 0, 0, 0)  # a fixed member timestamp: the archive bytes depend on the seed only


def _member(name: str, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    """A ``ZipInfo`` with a fixed timestamp. ``writestr`` with a str name stamps the current time."""
    info = zipfile.ZipInfo(name, _ZIP_EPOCH)
    info.compress_type = compress_type
    return info


def _epub(path: Path, target: int, rng: random.Random) -> None:
    chapters = max(1, target // (256 * 1024))
    per = target // chapters
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(_member("mimetype", zipfile.ZIP_STORED), "application/epub+zip")
        z.writestr(
            _member("META-INF/container.xml"),
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        manifest = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(chapters))
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(chapters))
        z.writestr(
            _member("OEBPS/content.opf"),
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title><dc:identifier id="id">x</dc:identifier>'
            f"<dc:language>en</dc:language></metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>",
        )
        for i in range(chapters):
            body = "".join(f"<p>{'word ' * rng.randint(20, 60)}</p>" for _ in range(max(1, per // 200)))
            z.writestr(
                _member(f"OEBPS/c{i}.xhtml"),
                f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>c{i}</title></head>'
                f"<body><h1>Chapter {i}</h1>{body}</body></html>",
            )


def _col(i: int) -> str:
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx(path: Path, rows: int, cols: int, rng: random.Random) -> None:
    """One sheet of ``rows`` x ``cols`` cells: a header row of text, then numbers and short text."""
    names = [_col(c) for c in range(1, cols + 1)]

    def sheet() -> Iterator[str]:
        yield '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for r in range(1, rows + 1):
            cells = []
            for c, n in enumerate(names):
                if r == 1:
                    cells.append(f'<c r="{n}1" t="inlineStr"><is><t>h{c}</t></is></c>')
                elif c % 3 == 2:
                    cells.append(f'<c r="{n}{r}" t="inlineStr"><is><t>v{rng.randint(0, 999)}</t></is></c>')
                else:
                    cells.append(f'<c r="{n}{r}"><v>{rng.randint(0, 10**6)}</v></c>')
            yield f'<row r="{r}">{"".join(cells)}</row>'
        yield "</sheetData></worksheet>"

    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            _member("[Content_Types].xml"),
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        )
        z.writestr(_member("_rels/.rels"), f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr(_member("xl/workbook.xml"), f'<?xml version="1.0"?><workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr(_member("xl/_rels/workbook.xml.rels"), f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        with z.open(_member("xl/worksheets/sheet1.xml"), "w") as f:
            for chunk in sheet():
                f.write(chunk.encode())


def estimate_bytes(profile: str) -> int:
    p = PROFILES[profile]
    total = sum(int(mb * MIB) for _, mb in p["json"]) + sum(int(mb * MIB) for mb in p["yaml"] + p["html"] + p["epub"])
    total += sum(rows * cols * 14 // 5 for _, rows, cols in p["xlsx"])  # compressed inline cells, about 2.8 B each
    return total


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def generate(out: Path, *, profile: str, seed: int, max_mb: float) -> dict:
    if profile not in PROFILES:
        raise SystemExit(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    est = estimate_bytes(profile)
    if est > max_mb * MIB:
        raise SystemExit(f"profile {profile} needs about {est / MIB:.0f} MiB, above --max-mb {max_mb}: nothing written")
    out.mkdir(parents=True, exist_ok=True)
    p = PROFILES[profile]
    files: list[tuple[str, str, Callable[[Path], None]]] = []

    def rng(tag: str) -> random.Random:
        return random.Random(f"{seed}:{tag}")

    for shape, mb in p["json"]:
        name = f"json-{shape}-{mb}mb.json"
        files.append((name, "json", lambda path, s=shape, m=mb, n=name: _write_text(path, _json_chunks(s, int(m * MIB), rng(n)))))
    for mb in p["yaml"]:
        name = f"yaml-{mb}mb.yaml"
        files.append((name, "yaml", lambda path, m=mb, n=name: _write_text(path, _yaml_chunks(int(m * MIB), rng(n)))))
    for mb in p["html"]:
        name = f"html-{mb}mb.html"
        files.append((name, "html", lambda path, m=mb, n=name: _write_text(path, _html_chunks(int(m * MIB), rng(n)))))
    for mb in p["epub"]:
        name = f"epub-{mb}mb.epub"
        files.append((name, "epub", lambda path, m=mb, n=name: _epub(path, int(m * MIB), rng(n))))
    for shape, rows, cols in p["xlsx"]:
        name = f"xlsx-{shape}-{rows}x{cols}.xlsx"
        files.append((name, "xlsx", lambda path, r=rows, c=cols, n=name: _xlsx(path, r, c, rng(n))))

    entries = {}
    for name, kind, make in files:
        path = out / name
        make(path)
        entries[name] = {"kind": kind, "bytes": path.stat().st_size, "sha256": _sha(path)}
    manifest = {"profile": profile, "seed": seed, "files": entries}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", default="smoke", choices=sorted(PROFILES))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-mb", type=float, default=1024.0)
    args = ap.parse_args(argv)
    manifest = generate(args.out, profile=args.profile, seed=args.seed, max_mb=args.max_mb)
    total = sum(f["bytes"] for f in manifest["files"].values())
    print(f"{len(manifest['files'])} files, {total / MIB:.1f} MiB in {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

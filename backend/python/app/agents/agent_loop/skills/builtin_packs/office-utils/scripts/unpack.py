#!/usr/bin/env python3
"""Unpack a .docx/.pptx/.xlsx Open XML file into a directory tree of its
constituent parts, pretty-printing every .xml part for readable diffs.

Usage: python unpack.py <input.docx|pptx> <output_dir/>
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from xml.dom import minidom


def unpack(input_path: str, output_dir: str) -> None:
    if not zipfile.is_zipfile(input_path):
        raise SystemExit(f"error: {input_path!r} is not a valid Open XML (zip) file")

    os.makedirs(output_dir, exist_ok=True)
    manifest = {"entry_order": []}

    with zipfile.ZipFile(input_path, "r") as zf:
        for info in zf.infolist():
            manifest["entry_order"].append({
                "name": info.filename,
                "compress_type": info.compress_type,
            })
            data = zf.read(info.filename)
            dest_path = os.path.join(output_dir, info.filename)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            if info.filename.endswith(".xml") or info.filename.endswith(".rels"):
                data = _pretty_print(data, info.filename)

            with open(dest_path, "wb") as f:
                f.write(data)

    manifest_path = os.path.join(output_dir, "_unpack_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Unpacked {len(manifest['entry_order'])} parts to {output_dir}")


def _pretty_print(data: bytes, filename: str) -> bytes:
    try:
        dom = minidom.parseString(data)
    except Exception as e:
        print(f"warning: {filename} did not parse as XML ({e}); left untouched", file=sys.stderr)
        return data
    pretty = dom.toprettyxml(indent="  ")
    # minidom inserts blank lines between text-only elements' surrounding
    # tags — collapse them so office-app-sensitive whitespace-in-text-runs
    # doesn't accumulate stray whitespace nodes across an edit/pack cycle.
    lines = [line for line in pretty.splitlines() if line.strip()]
    return ("\n".join(lines) + "\n").encode("utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python unpack.py <input.docx|pptx> <output_dir/>")
    unpack(sys.argv[1], sys.argv[2])

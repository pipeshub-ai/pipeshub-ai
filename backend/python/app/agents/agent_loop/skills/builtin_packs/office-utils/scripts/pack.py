#!/usr/bin/env python3
"""Repack a directory tree produced by unpack.py back into a .docx/.pptx/
.xlsx Open XML file. Validates every .xml/.rels part is still well-formed
before writing anything, and preserves the original ZIP entry order from
`_unpack_manifest.json` (some Office readers are sensitive to it).

Usage: python pack.py <input_dir/> <output.docx|pptx>
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from xml.dom import minidom


def pack(input_dir: str, output_path: str) -> None:
    manifest_path = os.path.join(input_dir, "_unpack_manifest.json")
    entry_order = None
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            entry_order = [e["name"] for e in json.load(f)["entry_order"]]

    all_files = _collect_files(input_dir)
    if entry_order is None:
        entry_order = sorted(all_files)
    else:
        missing = set(all_files) - set(entry_order)
        entry_order = entry_order + sorted(missing)
        entry_order = [name for name in entry_order if name in all_files]

    _validate_xml_parts(input_dir, entry_order)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in entry_order:
            zf.write(os.path.join(input_dir, name), name)

    print(f"Packed {len(entry_order)} parts into {output_path}")


def _collect_files(root: str) -> set[str]:
    files = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if filename == "_unpack_manifest.json":
                continue
            rel = os.path.relpath(os.path.join(dirpath, filename), root)
            files.add(rel.replace(os.sep, "/"))
    return files


def _validate_xml_parts(root: str, names: list[str]) -> None:
    errors = []
    for name in names:
        if not (name.endswith(".xml") or name.endswith(".rels")):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, "rb") as f:
                minidom.parseString(f.read())
        except Exception as e:
            errors.append(f"{name}: {e}")
    if errors:
        raise SystemExit(
            "error: refusing to pack — the following parts are not well-formed XML:\n  "
            + "\n  ".join(errors)
        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python pack.py <input_dir/> <output.docx|pptx>")
    pack(sys.argv[1], sys.argv[2])

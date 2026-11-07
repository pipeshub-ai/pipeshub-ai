#!/usr/bin/env python3
"""Compare two images and fail when a large new layer appears without a dependency change.

Both images must carry org.pipeshub.base-hash and org.pipeshub.npm-lock-hash.
If either label is missing, or the previous image does not exist, this exits 0.
A new layer of 20 MB or more fails the run only when both hashes are unchanged,
which is the case where the layer should have been reused.

Image refs are read with crane. Tests pass --previous-json and --current-json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

THRESHOLD_BYTES = 20 * 1024 * 1024
LABELS = ("org.pipeshub.base-hash", "org.pipeshub.npm-lock-hash")


def compare(previous: dict[str, Any], current: dict[str, Any], threshold: int = THRESHOLD_BYTES) -> tuple[bool, str]:
    """Return (failed, message). failed is True only for a real regression."""
    for image in (previous, current):
        labels = image.get("labels") or {}
        if any(not labels.get(name) for name in LABELS):
            return False, "skip: image is missing org.pipeshub.base-hash or org.pipeshub.npm-lock-hash"
    prev_labels = previous["labels"]
    curr_labels = current["labels"]
    if any(prev_labels.get(name) != curr_labels.get(name) for name in LABELS):
        return False, "skip: base or npm lock hash changed, so new layers are expected"
    previous_digests = {layer["digest"] for layer in previous.get("layers") or []}
    new_large = [
        layer
        for layer in current.get("layers") or []
        if layer.get("digest") not in previous_digests and int(layer.get("size") or 0) >= threshold
    ]
    if new_large:
        details = ", ".join(f"{layer['digest']} ({layer.get('size')} bytes)" for layer in new_large)
        return True, f"new layer(s) of at least 20 MB while the base and npm lock hashes are unchanged: {details}"
    return False, "ok: no new layer of 20 MB or more"


def _crane_json(args: list[str]) -> dict[str, Any] | None:
    try:
        raw = subprocess.check_output(["crane", *args], stderr=subprocess.STDOUT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return None
    return parsed


def fetch_image(ref: str) -> dict[str, Any] | None:
    manifest = _crane_json(["manifest", "--platform", "linux/amd64", ref])
    if manifest is None:
        return None
    if "manifests" in manifest and "layers" not in manifest:
        return None
    config = _crane_json(["config", ref])
    if config is None:
        return None
    labels = ((config.get("config") or {}).get("Labels")) or {}
    layers = [
        {"digest": layer["digest"], "size": int(layer.get("size") or 0)}
        for layer in manifest.get("layers") or []
        if isinstance(layer, dict) and layer.get("digest")
    ]
    return {"labels": labels, "layers": layers}


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", nargs="?", help="Previous image ref, or unused with --previous-json")
    parser.add_argument("current", nargs="?", help="Current image ref, or unused with --current-json")
    parser.add_argument("--previous-json", type=Path)
    parser.add_argument("--current-json", type=Path)
    args = parser.parse_args(argv)

    if args.previous_json or args.current_json:
        if not args.previous_json or not args.current_json:
            print("both --previous-json and --current-json are required", file=sys.stderr)
            return 2
        previous = json.loads(args.previous_json.read_text(encoding="utf-8"))
        current = json.loads(args.current_json.read_text(encoding="utf-8"))
    else:
        if not args.previous or not args.current:
            print("previous and current image refs are required", file=sys.stderr)
            return 2
        previous = fetch_image(args.previous)
        if previous is None:
            message = f"skip: previous image {args.previous} is not available"
            print(message)
            _summary(message)
            return 0
        current = fetch_image(args.current)
        if current is None:
            print(f"could not read current image {args.current}", file=sys.stderr)
            return 2

    failed, message = compare(previous, current)
    print(message)
    _summary(message)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

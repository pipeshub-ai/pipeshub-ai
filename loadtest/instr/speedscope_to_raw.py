#!/usr/bin/env python3
"""Fold a py-spy speedscope capture into py-spy's raw (collapsed-stack) format.

    speedscope_to_raw.py <profile.speedscope.json> <out.raw> [rate_hz]

perftest.sh records speedscope rather than raw so the capture also opens in
speedscope.app with its timeline intact; top_functions.py still reads the raw
format, so this derives it from the same capture instead of attaching a third
py-spy. Lines are `frame;frame;...;leaf count`, root first, as py-spy writes them.
"""
from __future__ import annotations

import collections
import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    rate = float(argv[3]) if len(argv) > 3 else 100.0
    # py-spy copies frame names byte-for-byte from the target, so they need not be UTF-8.
    with open(argv[1], encoding="utf-8", errors="replace") as fh:
        doc = json.load(fh)

    frames = doc.get("shared", {}).get("frames", [])
    labels = []
    for f in frames:
        name = f.get("name") or "<unknown>"
        labels.append(f"{name} ({f['file']}:{f.get('line', '?')})" if f.get("file") else name)

    folded: collections.Counter[str] = collections.Counter()
    for profile in doc.get("profiles", []):
        if profile.get("type") != "sampled":
            continue
        # py-spy weights are seconds per sample; a unitless profile is already counts.
        per_sample = rate if str(profile.get("unit", "")).lower() == "seconds" else 1.0
        weights = profile.get("weights") or []
        for i, stack in enumerate(profile.get("samples") or []):
            if not stack:
                continue
            w = weights[i] if i < len(weights) else 1.0 / per_sample
            folded[";".join(labels[j] for j in stack if 0 <= j < len(labels))] += max(1, round(w * per_sample))

    with open(argv[2], "w", encoding="utf-8") as fh:
        for stack, n in folded.items():
            fh.write(f"{stack} {n}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Flame graph rendered from a speedscope capture.

py-spy can emit an SVG itself, but that would mean a second `record` pass over
the same process covering a different slice of the sync — so the picture and the
blocking table would describe different moments. Deriving both from one capture
keeps them talking about the same thing, and one attach instead of two.
"""

from __future__ import annotations

import html
from typing import Any

# Warm palette by depth, so sibling frames at the same level read as a band.
COLOURS = ["#d94801", "#e6550d", "#fd8d3c", "#fdae6b", "#fdd0a2", "#f16913"]
ROW_H = 17
WIDTH = 1180

# speedscope weights carry whatever unit the profile declares. py-spy emits
# "seconds" (0.01 per sample at 100Hz), so summing weights raw and calling the
# result milliseconds understates every duration by 1000x.
_UNIT_TO_MS = {
    "seconds": 1000.0,
    "milliseconds": 1.0,
    "microseconds": 0.001,
    "nanoseconds": 1e-6,
}


def weight_scale(profile: dict[str, Any]) -> float:
    """Multiplier converting this profile's weights into milliseconds.

    Unitless profiles ("none") are sample counts, which carry no duration; the
    caller reports samples instead and this scale is irrelevant.
    """
    return _UNIT_TO_MS.get(str(profile.get("unit", "")).lower(), 1.0)


class _Node:
    __slots__ = ("name", "weight", "children")

    def __init__(self, name: str) -> None:
        self.name = name
        self.weight = 0.0
        self.children: dict[str, _Node] = {}

    def child(self, name: str) -> "_Node":
        node = self.children.get(name)
        if node is None:
            node = _Node(name)
            self.children[name] = node
        return node


def build_tree(doc: dict[str, Any]) -> tuple[_Node, float]:
    frames = doc.get("shared", {}).get("frames", [])

    def label(index: int) -> str:
        try:
            frame = frames[index]
        except (IndexError, TypeError):
            return "<unknown>"
        name = frame.get("name") or "<unknown>"
        file = frame.get("file")
        if not file:
            return name
        return f"{name} ({file.rsplit('/', 1)[-1]}:{frame.get('line', '?')})"

    root = _Node("all")
    for profile in doc.get("profiles", []):
        if profile.get("type") != "sampled":
            continue
        samples = profile.get("samples") or []
        weights = profile.get("weights") or []
        thread = profile.get("name") or "thread"
        scale = weight_scale(profile)
        for i, stack in enumerate(samples):
            weight = (weights[i] if i < len(weights) else 1.0) * scale
            root.weight += weight
            node = root.child(thread)
            node.weight += weight
            for frame_index in stack:
                node = node.child(label(frame_index))
                node.weight += weight
    return root, root.weight


def render(doc: dict[str, Any], *, title: str = "") -> str:
    root, total = build_tree(doc)
    if total <= 0:
        return '<p class="muted">no samples captured</p>'

    rects: list[str] = []
    max_depth = 0

    def walk(node: _Node, depth: int, x: float, width: float) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        if depth > 0:
            colour = COLOURS[depth % len(COLOURS)]
            pct = node.weight / total * 100.0
            safe = html.escape(node.name)
            y = depth * ROW_H
            # Only label boxes wide enough to hold readable text; the rest keep
            # their tooltip.
            text = ""
            if width > 45:
                chars = max(0, int(width / 6.2))
                # Truncate the RAW name, then escape. Truncating the escaped
                # string can cut through an entity ("&amp;" -> "&am"), which
                # makes the whole document fail to parse as XML.
                label = html.escape(node.name[:chars])
                text = (
                    f'<text x="{x + 3:.1f}" y="{y + 12}" font-size="10" fill="#111">'
                    f"{label}</text>"
                )
            rects.append(
                f'<g><title>{safe} — {pct:.2f}% ({node.weight:.0f}ms)</title>'
                f'<rect x="{x:.1f}" y="{y}" width="{max(width, 0.4):.1f}" height="{ROW_H - 1}" '
                f'fill="{colour}" stroke="#fff" stroke-width=".4"/>{text}</g>'
            )
        offset = x
        # Widest first: stable ordering makes two captures visually comparable.
        for child in sorted(node.children.values(), key=lambda n: (-n.weight, n.name)):
            child_width = child.weight / total * WIDTH
            walk(child, depth + 1, offset, child_width)
            offset += child_width

    walk(root, 0, 0.0, float(WIDTH))
    height = (max_depth + 1) * ROW_H + 4
    caption = f'<text x="0" y="{ROW_H - 5}" font-size="11" fill="currentColor">{html.escape(title)}</text>' if title else ""
    # xmlns is required for a standalone .svg file: without it a browser treats
    # the document as generic XML and shows the element tree instead of
    # rendering. It is harmless when the same markup is inlined into HTML.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="flame graph{": " + html.escape(title) if title else ""}">'
        f"{caption}{''.join(rects)}</svg>"
    )

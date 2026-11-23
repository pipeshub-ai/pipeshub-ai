"""Render the public limits of the permission model for the documentation, from `scenarios/deltas.yaml`.

Run `python -m tests.permissions.gen_docs` from `backend/python`; the output is a Markdown table that is pasted into the documentation
repository's permissions page, so the docs and the tests that pin the differences cannot drift apart. Only entries with a `public`
block are published, and each says what a reader needs in their own words.
"""

from __future__ import annotations

import sys

from .deltas import Delta, load_deltas

HEADER = "| Connector | Source permission | What PipesHub does differently |\n| --- | --- | --- |"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render(deltas: list[Delta]) -> str:
    rows = sorted((d.public["connector"], d.public["source"], d.public["differs"]) for d in deltas if d.public)
    return "\n".join([HEADER, *(f"| {_cell(c)} | {_cell(s)} | {_cell(w)} |" for c, s, w in rows)]) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render(load_deltas()))

from __future__ import annotations

from pydantic import BaseModel

"""User-defined markdown slash-commands (Phase 3) — loaded from a
`commands/<name>.md` file, the same YAML-frontmatter-plus-body loader shape
as Skills (see modules/providers/skills/loader.py), but client-side rather than agent-facing:
a Command expands into plain text that BECOMES the goal sent to the agent
(via `/name args` in the CLI — see cli.py) rather than something the model
discovers and loads itself mid-run."""

ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"


class Command(BaseModel):
    name: str
    description: str = ""
    argument_hint: str | None = None
    body: str

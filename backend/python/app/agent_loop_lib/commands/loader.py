from __future__ import annotations

import logging
import os
import re

import yaml

from app.agent_loop_lib.commands.base import Command

"""Parses `commands/<name>.md` files: optional YAML frontmatter (description,
argument-hint) followed by a Markdown body — the command's prompt template.
Unlike modules/providers/skills/loader.py's SKILL.md, frontmatter is fully optional here (a
bare markdown file with no `---` block at all is a valid command whose body
is the entire file) and there's no directory-per-command layout — one file
IS one command, named after the file.
"""

logger = logging.getLogger(__name__)

# `^---` (not a bundled "\n---") so a genuinely empty frontmatter block
# ("---\n---\n") parses correctly — the newline ending the opening line
# would otherwise have to double as both "end of content" and "start of the
# closing delimiter's preceding newline", which a single consumed character
# can't do.
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*\n?(.*)\Z", re.DOTALL | re.MULTILINE)


def parse_command_md(content: str, name: str) -> Command:
    match = _FRONTMATTER_RE.match(content)
    if match:
        raw_frontmatter, body = match.groups()
        data = yaml.safe_load(raw_frontmatter) or {}
        if not isinstance(data, dict):
            data = {}
    else:
        data, body = {}, content
    return Command(
        name=name,
        description=data.get("description", ""),
        argument_hint=data.get("argument-hint"),
        body=body.strip("\n"),
    )


def load_command_file(path: str) -> Command:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    return parse_command_md(content, name)


def load_commands_from_dir(root: str) -> list[Command]:
    """Scan `root` for `*.md` files (flat — one command per file) and load
    every one. A malformed/unreadable file is logged and skipped rather than
    raising, matching modules/providers/skills/loader.py's resilience story."""
    commands: list[Command] = []
    if not os.path.isdir(root):
        return commands
    for entry in sorted(os.listdir(root)):
        if not entry.endswith(".md"):
            continue
        path = os.path.join(root, entry)
        try:
            commands.append(load_command_file(path))
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Skipping invalid command at %s: %s", path, e)
    return commands

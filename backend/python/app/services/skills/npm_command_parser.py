"""Parses a pasted npm-ecosystem install command (or a bare package name)
into a `PackageSpec` — PURE STRING PARSING, the command is NEVER executed.

Users paste exactly what a skill's README tells them to run: `npx skills
add @anthropic/pdf-skills`, `npm install @acme/skill-pack@1.2.0`, `npx
@openai/skills add data-viz`, `yarn add ...`, `pnpm add ...`, or just a bare
`@scope/name`/`name`. This module strips the known runner/subcommand
prefixes and extracts a single registry package spec (name + optional
`@version`/`@tag`, defaulting to `latest`).

Mirrored on the frontend (`frontend/app/(main)/workspace/skills/personal/
npm-command-parser.ts`) for instant dialog feedback — this backend parser is
the AUTHORITATIVE one; the frontend copy is a lightweight UX preview only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["PackageSpec", "NpmCommandParseError", "parse_npm_command"]

# Every known way a skill's README says "run this to install me". Ordered
# longest-prefix-first so e.g. "npm install" doesn't get chopped by a
# shorter "npm i" mid-match. `skills add` (agentskills.io's own reference
# CLI) is included alongside the JS package managers.
_RUNNER_PREFIXES = (
    "npx skills add", "skills add",
    "npm install -g", "npm install", "npm i -g", "npm i",
    "yarn global add", "yarn add",
    "pnpm add -g", "pnpm add",
    "npx",
)

# A single bare/scoped package spec: optional @scope/, name, optional @version|@tag.
_PACKAGE_SPEC_RE = re.compile(
    r"^(?P<name>@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*|[a-z0-9][a-z0-9._-]*)"
    r"(?:@(?P<version>[A-Za-z0-9._-]+))?$",
)

# Anything containing these is unambiguously NOT a single safe package spec —
# shell metacharacters, flags, or multiple tokens after the runner prefix is
# stripped. Rejected with a clear message rather than guessed at.
_UNSAFE_CHARS_RE = re.compile(r"[;&|`$(){}<>\"'\\\n\r]")


class NpmCommandParseError(ValueError):
    """The input isn't reducible to a single registry package spec."""


@dataclass(frozen=True)
class PackageSpec:
    name: str
    version: str = "latest"

    @property
    def registry_spec(self) -> str:
        return f"{self.name}@{self.version}"


def _strip_runner_prefix(command: str) -> str:
    lowered = command.lower()
    for prefix in _RUNNER_PREFIXES:
        if lowered.startswith(prefix + " ") or lowered == prefix:
            return command[len(prefix):].strip()
    return command


def parse_npm_command(raw: str) -> PackageSpec:
    """Parse `raw` (a bare package name OR a full install command) into a
    `PackageSpec`. Never executes anything — this is regex/string parsing
    only. Raises `NpmCommandParseError` with a user-actionable message for
    anything that isn't reducible to exactly one registry package spec
    (shell metacharacters, unrecognized flags like `--registry`, multiple
    packages in one command, etc.) — callers should surface that message
    and suggest the user paste the bare package name instead.
    """
    if not raw or not raw.strip():
        raise NpmCommandParseError("Enter a package name or an install command.")

    text = raw.strip()
    if _UNSAFE_CHARS_RE.search(text):
        raise NpmCommandParseError(
            "This looks like more than a single install command. Paste just the "
            "package name or a plain 'npm install <package>'-style command."
        )

    remainder = _strip_runner_prefix(text)
    if remainder is text and " " in text:
        # No known runner prefix matched, but there's whitespace — could
        # still be an unrecognized runner ("bun add ...") or genuinely
        # multiple tokens. Try stripping one leading word (the runner) and
        # see if what's left parses as a bare package spec.
        first, _, rest = text.partition(" ")
        rest = rest.strip()
        if rest and " " not in rest and not rest.startswith("-"):
            remainder = rest
        else:
            raise NpmCommandParseError(
                f"Unrecognized install command format ({first!r} isn't a known "
                "runner). Paste just the package name instead, e.g. '@scope/pkg' "
                "or '@scope/pkg@1.2.0'."
            )

    remainder = remainder.strip()
    if not remainder:
        raise NpmCommandParseError("No package name found in that command.")
    if " " in remainder:
        raise NpmCommandParseError(
            "That command references more than one package or includes flags "
            "(e.g. '--registry'). Only a single bare package spec is supported — "
            "paste just the package name."
        )
    if remainder.startswith("-"):
        raise NpmCommandParseError(f"Unsupported flag {remainder!r} — paste just the package name.")

    match = _PACKAGE_SPEC_RE.match(remainder.lower())
    if not match:
        raise NpmCommandParseError(
            f"{remainder!r} doesn't look like a valid npm package spec "
            "(lowercase letters, digits, '.', '_', '-', optional '@scope/', optional '@version')."
        )
    return PackageSpec(name=match.group("name"), version=match.group("version") or "latest")

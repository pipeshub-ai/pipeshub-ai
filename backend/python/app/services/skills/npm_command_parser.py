"""Parses a pasted npm-ecosystem install command (or a bare package name)
into a `PackageSpec` or `UrlSpec` — PURE STRING PARSING, the command is
NEVER executed.

Users paste exactly what a skill's README tells them to run: `npx skills
add @anthropic/pdf-skills`, `npm install @acme/skill-pack@1.2.0`, `npx
@openai/skills add data-viz`, `yarn add ...`, `pnpm add ...`, or just a bare
`@scope/name`/`name`. This module strips the known runner/subcommand
prefixes and extracts a single registry package spec (name + optional
`@version`/`@tag`, defaulting to `latest`).

URLs (including GitHub repo links) are also accepted: the parser recognises
them after stripping the runner prefix and returns a `UrlSpec` so the caller
can route to the URL-based import path instead of the npm registry.

The ``--skill <name>`` flag (used by the `skills` CLI to select one skill
from a multi-skill package) is extracted as ``skill_filter`` on both result
types so the caller can narrow the preview if needed.

Mirrored on the frontend (`frontend/app/(main)/workspace/skills/personal/
npm-command-parser.ts`) for instant dialog feedback — this backend parser is
the AUTHORITATIVE one; the frontend copy is a lightweight UX preview only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

__all__ = [
    "PackageSpec",
    "UrlSpec",
    "ParseResult",
    "NpmCommandParseError",
    "parse_npm_command",
]

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
# NOTE: forward-slash (/) is intentionally allowed so URLs survive this check.
_UNSAFE_CHARS_RE = re.compile(r"[;&|`$(){}<>\"'\\\n\r]")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Known flags that carry a value and should be extracted, not rejected.
_SKILL_FLAG_RE = re.compile(r"--skill\s+(\S+)")


class NpmCommandParseError(ValueError):
    """The input isn't reducible to a single registry package spec or URL."""


@dataclass(frozen=True)
class PackageSpec:
    name: str
    version: str = "latest"
    skill_filter: str | None = None

    @property
    def registry_spec(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class UrlSpec:
    """The user pasted a URL (GitHub repo, direct archive link, etc.)."""
    url: str
    skill_filter: str | None = None


ParseResult = Union[PackageSpec, UrlSpec]


def _strip_runner_prefix(command: str) -> str:
    lowered = command.lower()
    for prefix in _RUNNER_PREFIXES:
        if lowered.startswith(prefix + " ") or lowered == prefix:
            return command[len(prefix):].strip()
    return command


def _extract_known_flags(text: str) -> tuple[str, str | None]:
    """Strip ``--skill <name>`` from *text*, returning (remainder, skill_name).

    Other flags (``--registry``, ``--save-dev``, …) are left in place so the
    later validation still rejects them with a clear message.
    """
    skill_match = _SKILL_FLAG_RE.search(text)
    skill_filter: str | None = None
    if skill_match:
        skill_filter = skill_match.group(1)
        text = (text[: skill_match.start()] + text[skill_match.end() :]).strip()
    return text, skill_filter


def parse_npm_command(raw: str) -> ParseResult:
    """Parse *raw* into a `PackageSpec` (npm registry lookup) or a `UrlSpec`
    (direct URL download).  Never executes anything — this is regex/string
    parsing only.

    Recognised inputs (after optional runner-prefix stripping):

    * A bare package name: ``pdf-skills``, ``@acme/skill-pack@1.2.0``
    * An ``npm install`` / ``yarn add`` / ``npx skills add`` command
    * Any of the above with a ``--skill <name>`` flag
    * An ``https://`` URL (GitHub repo, direct archive link, etc.)

    Raises `NpmCommandParseError` with a user-actionable message for
    anything that can't be reduced to one of those forms.
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
    prefix_was_stripped = remainder is not text
    remainder, skill_filter = _extract_known_flags(remainder)

    if _URL_RE.match(remainder):
        return UrlSpec(url=remainder, skill_filter=skill_filter)

    if not prefix_was_stripped and " " in text:
        first, _, rest = text.partition(" ")
        rest = rest.strip()
        rest, skill_filter_fallback = _extract_known_flags(rest)
        skill_filter = skill_filter or skill_filter_fallback
        if _URL_RE.match(rest):
            return UrlSpec(url=rest, skill_filter=skill_filter)
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
    return PackageSpec(
        name=match.group("name"),
        version=match.group("version") or "latest",
        skill_filter=skill_filter,
    )

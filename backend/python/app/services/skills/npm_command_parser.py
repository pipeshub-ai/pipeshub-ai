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

__all__ = [
    "PackageSpec",
    "UrlSpec",
    "CatalogSpec",
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

# Standalone flags (no value) that are noise in pasted commands
_NOISE_STANDALONE = frozenset((
    "--yes", "-y", "--global", "-g", "--list", "-l", "--all",
    "--copy", "--dry-run", "--no-install", "--no-telemetry",
))

# Flags that consume the next token as their value
_NOISE_VALUE_FLAGS = frozenset(("--agent", "-a", "--package", "-p"))

# Subcommands that skill CLIs use after a URL-based runner
_CLI_SUBCOMMANDS = frozenset(("add", "install"))

# GitHub shorthand: owner/repo, optional /skill or @skill, optional #ref.
# `npx skills add anthropics/skills/pptx` and `owner/repo@skill` are the
# shapes the skills CLI documents; `#ref` is npm's github: shorthand.
_GITHUB_SHORTHAND_RE = re.compile(
    r"^(?P<owner>[a-z0-9][a-z0-9._-]*)/(?P<repo>[a-z0-9][a-z0-9._-]*)"
    r"(?:/(?P<slash_skill>[a-z0-9][a-z0-9._-]*))?"
    r"(?:@(?P<at_skill>[a-z0-9][a-z0-9._-]*))?"
    r"(?:#(?P<ref>[a-z0-9._/-]+))?$",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class CatalogSpec:
    """A skill-registry slug (e.g. OpenAgentSkill `zarazhangrui-frontend-slides`),
    not an npm package and not a GitHub owner/repo path."""
    slug: str
    skill_filter: str | None = None


ParseResult = PackageSpec | UrlSpec | CatalogSpec


def _strip_runner_prefix(command: str) -> str:
    lowered = command.lower()
    for prefix in _RUNNER_PREFIXES:
        if lowered.startswith(prefix + " ") or lowered == prefix:
            return command[len(prefix):].strip()
    return command


def _extract_known_flags(text: str) -> tuple[str, str | None]:
    """Strip every ``--skill <name>`` from *text*, returning (remainder, first_skill).

    Multiple ``--skill`` flags are common in the ``skills`` CLI; we keep the
    first value for ``skill_filter`` and strip the rest so they don't pollute
    later checks.
    """
    skill_filter: str | None = None
    while True:
        m = _SKILL_FLAG_RE.search(text)
        if not m:
            break
        if skill_filter is None:
            skill_filter = m.group(1)
        text = (text[: m.start()] + text[m.end() :]).strip()
    return text, skill_filter


def _strip_noise(text: str) -> str:
    """Remove flag-like tokens that are noise in pasted install commands.

    Standalone flags (``--list``, ``-g``, …) are dropped.  Known value-bearing
    flags (``--agent X``, ``-a X``) drop the flag and its value.  Unknown flags
    (``--save-dev``) drop just the token.  Positional tokens are preserved.
    """
    tokens = text.split()
    result: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _NOISE_STANDALONE:
            i += 1
            continue
        if t in _NOISE_VALUE_FLAGS:
            if i + 1 >= len(tokens) or tokens[i + 1].startswith("-"):
                raise NpmCommandParseError(f"Flag {t!r} requires a value.")
            i += 2
            continue
        if t.startswith("-") and "=" in t:
            i += 1
            continue
        result.append(t)
        i += 1
    return " ".join(result)


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
    if remainder.lower().startswith("github:"):
        remainder = remainder[len("github:"):]
        prefix_was_stripped = True
    remainder, skill_filter = _extract_known_flags(remainder)
    remainder = _strip_noise(remainder)
    catalog_slug_allowed = False

    if _URL_RE.match(remainder):
        url, _, after = remainder.partition(" ")
        after = after.strip()
        if not after:
            return UrlSpec(url=url, skill_filter=skill_filter)
        # URL is a runner CLI (e.g. `npx --yes <cli.tgz> add <pkg>`)
        after_tokens = after.split()
        if after_tokens and after_tokens[0].lower() in _CLI_SUBCOMMANDS:
            after_tokens = after_tokens[1:]
        if len(after_tokens) == 1 and not after_tokens[0].startswith("-"):
            remainder = after_tokens[0]
            catalog_slug_allowed = True
        else:
            return UrlSpec(url=url, skill_filter=skill_filter)

    if not prefix_was_stripped and " " in text:
        first, _, rest = text.partition(" ")
        rest = rest.strip()
        rest, skill_filter_fallback = _extract_known_flags(rest)
        rest = _strip_noise(rest)
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

    gh = _GITHUB_SHORTHAND_RE.match(remainder)
    if gh:
        owner, repo = gh.group("owner"), gh.group("repo")
        extra = gh.group("slash_skill") or gh.group("at_skill")
        ref = gh.group("ref")
        url = f"https://github.com/{owner}/{repo}"
        if ref:
            url += f"#{ref}"
        return UrlSpec(url=url, skill_filter=skill_filter or extra)

    lower = remainder.lower()
    if catalog_slug_allowed and not remainder.startswith("@"):
        return CatalogSpec(slug=lower, skill_filter=skill_filter)

    match = _PACKAGE_SPEC_RE.match(lower)
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

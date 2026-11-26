"""The skill content scanner (SK-06, SEC-SC-04): what an imported skill holds that a reviewer must read first.

A skill's text reaches the model as instructions and its scripts run in the sandbox, so a poisoned package can tell
the model to fetch and run code ("prerequisite: ``curl … | sh``"), decode and run a hidden payload, read
credentials, or destroy files; its SKILL.md can carry the prompt-injection shapes MCP descriptions do. A finding is
a reason for an admin to read the skill, never proof of an attack, and the scanner never rewrites the content: the
review shows the finding beside the file and line, and approving flagged content needs the findings acknowledged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.mcp_hub.description_scan import scan_text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

REMOTE_CODE: Final = "remote_code_execution"
ENCODED_CODE: Final = "encoded_code_execution"
CREDENTIAL_ACCESS: Final = "credential_access"
DESTRUCTIVE_COMMAND: Final = "destructive_command"
NATIVE_BINARY: Final = "native_binary"

EXCERPT_MAX: Final = 120
_SHELLS = r"(?:ba|z|da|k)?sh|python3?|node|perl|ruby|iex|invoke-expression"
_FETCH = r"(?:curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b"

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        REMOTE_CODE,
        re.compile(
            rf"\b{_FETCH}[^\n|]*\|\s*(?:sudo\s+)?(?:{_SHELLS})\b"
            rf"|(?:{_SHELLS})\s+<\(\s*{_FETCH}"
            rf"|\b(?:eval|exec)\b[^\n]*\$\(\s*{_FETCH}"
            r"|\bnpx\s+(?:-y\s+|--yes\s+)?https?://"
            r"|\bpip3?\s+install\s+[^\n]*\bhttps?://",
            re.IGNORECASE,
        ),
    ),
    (
        ENCODED_CODE,
        re.compile(
            rf"\bbase64\s+(?:-d|--decode|-D)\b[^\n]*\|\s*(?:{_SHELLS})\b"
            r"|\b(?:exec|eval)\s*\(\s*(?:base64\.b64decode|codecs\.decode|bytes\.fromhex|zlib\.decompress)"
            r"|\bFromBase64String\b",
            re.IGNORECASE,
        ),
    ),
    (
        CREDENTIAL_ACCESS,
        re.compile(
            r"~/\.(?:ssh|aws|gnupg|docker|kube|netrc|git-credentials|npmrc|pypirc)\b"
            r"|\$HOME/\.(?:ssh|aws|gnupg|kube|netrc)\b"
            r"|/etc/(?:shadow|sudoers)\b"
            r"|\b(?:printenv|env)\b[^\n]*\|\s*" + _FETCH,
            re.IGNORECASE,
        ),
    ),
    (
        DESTRUCTIVE_COMMAND,
        re.compile(
            r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[a-z]*\s+(?:--no-preserve-root\s+)?(?:/|~|\$HOME|/\*)(?:\s|$)"
            r"|\bmkfs(?:\.\w+)?\s+/dev/"
            r"|\bdd\s+[^\n]*\bof=/dev/(?:sd|nvme|hd|xvd|disk)"
            r"|:\(\)\s*\{\s*:\|:&\s*\};:",
            re.IGNORECASE,
        ),
    ),
)
# ELF, Mach-O (both byte orders, fat), PE: a skill is text and scripts; a native program is a reason to look.
_BINARY_MAGIC: Final = (
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"MZ",
)


@dataclass(frozen=True, slots=True)
class SkillFinding:
    code: str
    path: str
    line: int
    excerpt: str

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "path": self.path,
            "line": self.line,
            "excerpt": self.excerpt,
        }


def _excerpt(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= EXCERPT_MAX else one[: EXCERPT_MAX - 1] + "…"


def _scan_text(path: str, text: str) -> list[SkillFinding]:
    instructions = path.rsplit("/", 1)[-1] == "SKILL.md"
    found: list[SkillFinding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for code, pattern in _PATTERNS:
            if pattern.search(line):
                found.append(SkillFinding(code, path, number, _excerpt(line)))
        if instructions:
            # The model reads SKILL.md as instructions: the MCP description scanner's shapes (instruction override,
            # hidden directives, secret access, exfiltration, concealment, invisible text) apply to it as they do to
            # a tool's description, under the same codes.
            found.extend(
                SkillFinding(f.code, path, number, f.excerpt)
                for f in scan_text(line, path)
            )
    return found


def scan_skill(files: Mapping[str, bytes]) -> list[SkillFinding]:
    """Every finding in the bundle's files, in path and line order."""
    found: list[SkillFinding] = []
    for path in sorted(files):
        content = files[path]
        if content.startswith(_BINARY_MAGIC) and not path.endswith(
            (".md", ".txt", ".py", ".sh", ".js", ".ts")
        ):
            found.append(SkillFinding(NATIVE_BINARY, path, 0, ""))
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found.extend(_scan_text(path, text))
    return found


__all__ = [
    "CREDENTIAL_ACCESS",
    "DESTRUCTIVE_COMMAND",
    "ENCODED_CODE",
    "NATIVE_BINARY",
    "REMOTE_CODE",
    "SkillFinding",
    "scan_skill",
]

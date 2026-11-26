"""The description scanner (MX-04): what a server's tool says that reads as an instruction to the model.

A tool's name, description and input schema reach the model as trusted text, so a server can hide orders in them
(tool poisoning): override the user's instructions, read a secret and pass it as an argument, steer another server's
tool (shadowing), or keep all of it from the user. The scanner names each such passage; it never rewrites the text.
It reads the server's own text, before the hub takes invisible characters out, so a tool that hid text is still
named (``invisible_text``). A flagged tool is shown to the admin with its findings and is approved only when the admin acknowledges them, so a
poisoned tool is never enabled on a click that did not see the warning.

The patterns are the published poisoning shapes (Invariant Labs' tool-poisoning and shadowing notes, the MX-04
scenario) and are tuned against the descriptions of common servers (``test_mx04_description_scan``): a finding is a
reason for an admin to read the tool, not proof of an attack.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.mcp_hub.names import invisible_char, strip_unsafe_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.engines.types import JsonValue
    from harness_core.mcp_hub.manifest import ToolDescriptor

INSTRUCTION_OVERRIDE: Final = "instruction_override"
HIDDEN_DIRECTIVE: Final = "hidden_directive"
SECRET_ACCESS: Final = "secret_access"
EXFILTRATION: Final = "exfiltration"
CONCEALMENT: Final = "concealment"
TOOL_SHADOWING: Final = "tool_shadowing"
INVISIBLE_TEXT: Final = "invisible_text"

EXCERPT_MAX = 120
_SCHEMA_DEPTH_MAX = 12

_SPACE = re.compile(r"\s+")
# One character that does not end a sentence: a path's or an address's dots stay inside it.
_IN_SENTENCE = r"(?:(?!\.\s)[^\n])"

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        INSTRUCTION_OVERRIDE,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b(?:\s+\w+){0,3}?\s+"
            r"(?:previous|prior|above|earlier|preceding|system|user'?s?|original|other)\s+"
            r"(?:instructions?|rules|prompts?|directions|guidelines|messages|context)\b"
            r"|\bnew\s+(?:system\s+)?instructions?\s*:"
            r"|\byou\s+are\s+now\s+(?:a|an|in)\b"
            r"|\b(?:reveal|print|repeat|output)\s+(?:your|the)\s+system\s+prompt\b"
        ),
    ),
    (
        HIDDEN_DIRECTIVE,
        re.compile(
            r"</?\s*(?:important|system|instructions?|secret|admin|hidden|assistant)\s*>"
            r"|\[/?(?:inst|system)\]|<\|im_start\|>|<!--"
        ),
    ),
    (
        SECRET_ACCESS,
        re.compile(
            r"~/\.(?:ssh|aws|gnupg|kube|docker|config/gcloud|netrc|npmrc|pypirc|git-credentials|cursor|claude)\b"
            r"|\bid_(?:rsa|dsa|ecdsa|ed25519)\b|/etc/(?:passwd|shadow)\b|\.aws/credentials\b"
            r"|\bmcp\.json\b|\bclaude_desktop_config\.json\b"
            r"|\b(?:read|cat|open|include|pass|attach|append|send|provide)\b(?:\s+\S+){0,4}?\s+"
            r"(?:the\s+)?(?:\.env\b|private\s+keys?\b|ssh\s+keys?\b|api[\s_-]?keys?\b|access\s+tokens?\b"
            r"|(?:user'?s\s+)?(?:passwords?|credentials|secrets?)\b)"
        ),
    ),
    (
        EXFILTRATION,
        re.compile(
            r"\b(?:send|post|upload|forward|email|e-mail|transmit|exfiltrate|bcc|cc)\b" + _IN_SENTENCE + r"{0,80}?\bto\s+"
            r"(?:https?://|[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b)"
            r"|\b(?:send|forward|copy|bcc)\b" + _IN_SENTENCE + r"{0,40}?\b(?:all|every|each)\s+(?:emails?|messages?|files?|results?)\b"
            + _IN_SENTENCE + r"{0,40}?\bto\b"
        ),
    ),
    (
        CONCEALMENT,
        re.compile(
            r"\b(?:do\s+not|don'?t|never|without)\s+(?:\w+\s+){0,2}?"
            r"(?:tell|telling|mention|mentioning|inform|informing|reveal|revealing|notify|notifying|alert|alerting|show|showing)"
            r"\s+(?:this\s+(?:to\s+)?)?(?:the\s+)?user\b"
            r"|\b(?:do\s+not|don'?t|never)\s+(?:mention|reveal|disclose)\s+(?:that|this|it)\b"
            r"|\b(?:secretly|covertly|silently|quietly)\s+(?:send|read|include|pass|add|forward|call|run|exfiltrate)\b"
            r"|\bthe\s+user\s+(?:must|should)\s+not\s+(?:know|see|notice|be\s+told)\b"
        ),
    ),
    (
        TOOL_SHADOWING,
        re.compile(
            r"\b(?:when|whenever|before|after)\s+(?:using|calling|invoking|running)\s+(?:any|the|every|another|other)\s+"
            r"(?:\w+\s+){0,2}?tools?\b"
            r"|\binstead\s+of\s+(?:using|calling)\s+(?:the\s+)?\S+\s+tool\b"
            r"|\bwhen\s+this\s+tool\s+is\s+(?:available|present|loaded|installed)\b"
            r"|\b(?:other|another)\s+tool'?s?\b" + _IN_SENTENCE + r"{0,40}?\bmust\b"
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class DescriptionFinding:
    """One passage of a tool that reads as an instruction: what kind, where (``name``, ``description`` or a JSON
    path into ``inputSchema``) and the passage itself, normalised and cut to ``EXCERPT_MAX``."""

    code: str
    field: str
    excerpt: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "excerpt": self.excerpt}


def _excerpt(text: str, start: int, end: int) -> str:
    lead = max(0, start - 20)
    cut = text[lead : max(end, lead + 1) + 20]
    clipped = cut[:EXCERPT_MAX]
    return ("…" if lead else "") + clipped + ("…" if lead + len(clipped) < len(text) else "")


def _strings(value: JsonValue, path: str, depth: int = 0) -> Iterator[tuple[str, str]]:
    """Every string the schema holds, values and property names alike, with its JSON path."""
    if depth > _SCHEMA_DEPTH_MAX:
        return
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}.{key}", key
            yield from _strings(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]", depth + 1)


def scan_text(text: str, field: str) -> list[DescriptionFinding]:
    findings: list[DescriptionFinding] = []
    hidden = invisible_char(text)
    if hidden is not None:
        findings.append(DescriptionFinding(INVISIBLE_TEXT, field, f"U+{ord(hidden):04X}"))
    # What the model reads once the hub has exported the text (``export_description``), then folded for matching.
    folded = _SPACE.sub(" ", unicodedata.normalize("NFKC", strip_unsafe_text(text)))
    lowered = folded.lower()
    # Lower-casing can change a string's length (U+0130); then the excerpt is cut from the lowered text.
    shown = folded if len(lowered) == len(folded) else lowered
    for code, pattern in _PATTERNS:
        match = pattern.search(lowered)
        if match is not None:
            findings.append(DescriptionFinding(code, field, _excerpt(shown, match.start(), match.end())))
    return findings


def scan_descriptor(tool: ToolDescriptor) -> tuple[DescriptionFinding, ...]:
    """Every finding in what the tool shows the model, at most one per kind and place, in a stable order."""
    findings = scan_text(tool.name, "name")
    if tool.description:
        findings.extend(scan_text(tool.description, "description"))
    for path, text in _strings(dict(tool.input_schema), "inputSchema"):
        findings.extend(scan_text(text, path))
    return tuple(findings)


def finding_codes(findings: tuple[DescriptionFinding, ...]) -> list[str]:
    return sorted({f.code for f in findings})


__all__ = [
    "CONCEALMENT",
    "EXFILTRATION",
    "HIDDEN_DIRECTIVE",
    "INSTRUCTION_OVERRIDE",
    "INVISIBLE_TEXT",
    "SECRET_ACCESS",
    "TOOL_SHADOWING",
    "DescriptionFinding",
    "finding_codes",
    "scan_descriptor",
    "scan_text",
]

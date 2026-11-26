"""SEC-C-01 / SEC-C-06 / SEC-C-17 / SEC-C-18: every regex that reads text an attacker controls is linear-time.

Proven by structure, not by a clock (WALLCLOCK-1): tests/support/regex_linearity.py reads each pattern's parse
tree and refuses the three shapes that make a backtracking engine super-linear. The shared token vector feeds
Node's enforcement, the frontend's warning and every Python redactor with the same source strings, so one check
covers all three; the XSS check's patterns are read from their TypeScript source. The lint is held to every shape
the R37 audit and this sweep measured as quadratic, so a weaker lint fails here first.
"""

from __future__ import annotations

import re
import time

import pytest

from harness_core.contracts.vectors.token_patterns import (
    PASTED_INPUT_RULES,
    REDACT_ONLY_PATTERN_SOURCES,
    SECRET_KEY_NAME_FLAGS,
    SECRET_KEY_NAME_SOURCE,
    VENDOR_PATTERN_SOURCES,
)
from harness_core.engines import repository_instructions
from harness_core.security import pasted_secrets
from harness_core.security.pasted_secrets import (
    REDACT_MARGIN_CHARS,
    PastedSecretScanTimeout,
    find_pasted_secrets,
    redact_pasted_secrets,
    redact_prefix,
)
from tests.cohesion.support import REPO_ROOT
from tests.support.regex_linearity import flags_of, violations


def _flags(flags: str) -> int:
    return re.IGNORECASE if "i" in flags else 0


def _vector_patterns() -> list[tuple[str, str, int]]:
    rules = PASTED_INPUT_RULES
    context = rules["contextual"]
    return [
        *((s.kind, s.source, 0) for s in (*VENDOR_PATTERN_SOURCES, *REDACT_ONLY_PATTERN_SOURCES)),
        *((d["kind"], d["source"], 0) for d in rules["detectors"]),
        ("assigned", rules["assigned"]["source"], _flags(rules["assigned"]["flags"])),
        ("contextual.keyword", context["keyword"], _flags(context["keywordFlags"])),
        ("contextual.word", context["word"], 0),
        ("contextual.separators", context["separators"], 0),
        ("contextual.trailing", context["trailing"], 0),
        ("placeholders", rules["placeholders"], _flags(rules["placeholderFlags"])),
        ("identifierPath", rules["identifierPath"], 0),
        ("secretKeyNames", SECRET_KEY_NAME_SOURCE, _flags(SECRET_KEY_NAME_FLAGS)),
    ]


def _worker_patterns() -> list[tuple[str, str, int]]:
    from app.harness.engines import engine_citations
    from harness_core.kernel import planner

    found = [
        (f"engine_citations.{name}", value.pattern, value.flags & ~re.UNICODE)
        for name, value in vars(engine_citations).items()
        if isinstance(value, re.Pattern)
    ]
    found.append(("planner._CONFIDENCE_LINE_RE", planner._CONFIDENCE_LINE_RE.pattern, planner._CONFIDENCE_LINE_RE.flags & ~re.UNICODE))
    return found


_XSS_SOURCE = REPO_ROOT / "backend" / "nodejs" / "apps" / "src" / "utils" / "xss-sanitization.ts"


def _xss_patterns() -> list[tuple[str, str, int]]:
    text = _XSS_SOURCE.read_text(encoding="utf-8")
    found = [(m[1], m[2], flags_of(m[3])) for m in re.finditer(r"^const (\w+) = /(.+)/([a-z]*);$", text, re.MULTILINE)]
    assert len(found) >= 8, "the XSS check's patterns moved: read them from their new place"
    return found


@pytest.mark.parametrize(
    ("kind", "source", "flags"),
    [pytest.param(*p, id=p[0]) for p in (*_vector_patterns(), *_worker_patterns(), *_xss_patterns())],
)
def test_every_scanning_pattern_is_linear_by_structure(kind: str, source: str, flags: int) -> None:
    assert violations(source, flags) == [], kind


# What the audit and this sweep measured as quadratic; each must stay refused by the lint.
_KNOWN_QUADRATIC = {
    "SEC-C-01 trailing punctuation": (r"[.:!?]+$", 0),
    "SEC-C-01 jwt after a hyphen": (r"(?<![A-Za-z0-9_])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}", 0),
    "SEC-C-01 url scheme": (r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9+.-]*://[^\s/@:\"'<>]+:([^\s/@\"'<>]{3,})@", 0),
    "SEC-C-01 private key openers": (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", 0),
    "SEC-C-17 html tag over <": (r"<[^>]{0,10000}>", re.IGNORECASE),
    "SEC-C-17 script body": (r"<[\s]*script[^>]{0,10000}>[\s\S]{0,100000}?<\/[\s]*script[^>]{0,10000}>", re.IGNORECASE),
    "SEC-C-18 confidence line": (r"(?im)^\s*\**confidence\**\s*:\s*(.+?)\s*$", 0),
    "SEC-C-18 fenced json": (r"^```(?:json)?\s*(.*?)```\s*$", re.DOTALL),
    "SEC-C-18 question key": (r"[\s?!.:]+$", 0),
    "SEC-C-18 header": (r"^[ \t]*[-*]?[ \t]*([A-Za-z][A-Za-z ]{0,30}?)[ \t]*:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE),
    "SEC-C-18 inline sources": (r"[ \t]*(?:\*\*|__)?\b(?:sources|references)(?:\*\*|__)?[ \t]*:[ \t]*(?=\[\^?\d)", 0),
    "SEC-C-18 inline item": (r"\[\^?(\d{1,3})\]:?[ \t]*(.*?)[ \t;,]*(?=\[\^?\d{1,3}\]|$)", 0),
    "SEC-C-18 link label": (r"\[(?P<label>[^\]]*)\]\((?P<target>https?://[^)\s]+)\)", 0),
    "nested repeat": (r"(?:a+)+b", 0),
}


@pytest.mark.parametrize(("source", "flags"), list(_KNOWN_QUADRATIC.values()), ids=list(_KNOWN_QUADRATIC))
def test_the_lint_refuses_every_shape_measured_quadratic(source: str, flags: int) -> None:
    assert violations(source, flags)


def test_the_rewritten_patterns_still_find_what_they_found() -> None:
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlLXZhbHVl"
    url = "postgres://admin:" + "Zq8#vR2!kP" + "@db.internal:5432/app"
    for text in (f"see {key} done", f"Authorization: {jwt}", f"dsn={url}"):
        assert find_pasted_secrets(text), text
    # A key block after an unmatched opener is still found: the opener's scan stops at the next one.
    assert any(f.kind == "private_key" for f in find_pasted_secrets("-----BEGIN PRIVATE KEY-----\noops\n" + key))
    # Trailing punctuation is still cut from a contextual secret.
    word = "q7Rm2Tz9Lp4Wv8Kd3Ns6Hb1Yc5Xe8Ja2Ug"
    redacted, found = redact_pasted_secrets(f"token {word}.!?")
    assert found and redacted.endswith(".!?") and word not in redacted


def test_a_past_deadline_stops_the_scan() -> None:
    with pytest.raises(PastedSecretScanTimeout):
        find_pasted_secrets("token abc", deadline=time.monotonic() - 1)


def _count_scanned(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    scanned: list[int] = []
    original = pasted_secrets.redact_pasted_secrets

    def measure(text: str, mask: str = pasted_secrets.SECRET_MASK) -> tuple[str, tuple[object, ...]]:
        scanned.append(len(text))
        return original(text, mask)

    monkeypatch.setattr(pasted_secrets, "redact_pasted_secrets", measure)
    return scanned


def test_redact_prefix_scans_only_the_cut_and_its_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    word = "q7Rm2Tz9Lp4Wv8Kd3Ns6Hb1Yc5Xe8Ja2Ug"
    # A secret straddling the cut is masked, not shown by halves.
    shown, truncated = redact_prefix("x" * 90 + f" token {word} " + "y" * 10_000, 100)
    assert truncated and word[:4] not in shown
    scanned = _count_scanned(monkeypatch)
    shown, truncated = redact_prefix("ok token " + "." * 2_000_000 + "a", 10)
    assert truncated and shown == "ok token ."
    assert scanned == [10 + REDACT_MARGIN_CHARS]


def test_a_megabyte_agents_md_is_cut_before_it_is_scanned(monkeypatch: pytest.MonkeyPatch) -> None:
    scanned = _count_scanned(monkeypatch)
    content = ("token " + "." * 1_000_000 + "a").encode()
    shown = repository_instructions._file("AGENTS.md", content, repository_instructions.MAX_FILE_CHARS)
    assert shown.truncated and len(shown.text) == repository_instructions.MAX_FILE_CHARS and shown.size == len(content)
    assert scanned == [repository_instructions.MAX_FILE_CHARS + REDACT_MARGIN_CHARS]

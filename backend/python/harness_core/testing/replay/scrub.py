"""Remove secrets and personal data from a recording before it is committed.

Secrets (tokens, API keys, JWTs, private keys, credentials in URLs and query
strings, ``password=...`` pairs) are replaced with ``<redacted:kind>``. Email
addresses become stable pseudonyms (``person-3f9a1c2b@example.invalid``): the
same address maps to the same pseudonym everywhere in one recording, so a tool
result and the model's later call that quotes it still agree and the replay
stays consistent. Pseudonyms are an HMAC under a per-scrubber random key: the
mapping does not depend on the order text is scrubbed in (so the recorder can
digest scrubbed requests as they happen), and cannot be reversed by hashing a
list of likely addresses.

``find_secrets`` runs the same detectors without replacing anything; the
recorder runs it after scrubbing as a canary scan, together with any literal
canaries the test planted.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.security.token_patterns import REDACTION_PATTERNS, redact_tokens
from harness_core.testing.replay.recording import Manifest, Recording

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pydantic import JsonValue

SCRUB_VERSION = "1"

_PSEUDONYM_DOMAIN = "example.invalid"

# Token shapes come from the shared vector (``REDACTION_PATTERNS``); URL
# userinfo is handled before the email detector sees ``user:pass@host``.
# scheme://user:password@host -> scheme://<redacted:userinfo>@host
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:\"'<>]+:[^\s/@\"'<>]+@")

# Credential-bearing query parameters, e.g. ?token=... &X-Amz-Signature=...
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|id_token|token|api[_-]?key|apikey|key|sig|"
    r"signature|x-amz-signature|x-amz-credential|x-amz-security-token|code|password|"
    r"secret|client_secret)=)[^&#\s\"'<>)]+"
)

# password: hunter2 / api_key="abc" / client_secret = xyz
_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|client[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|auth[_-]?token)(\"?\s*[:=]\s*)([\"']?)(?!<redacted)[^\s\"',;&}]{4,}"
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass
class Scrubber:
    """Stateful so pseudonyms stay stable across one recording."""

    keep_domains: frozenset[str] = frozenset({_PSEUDONYM_DOMAIN})
    key: bytes = field(default_factory=lambda: secrets.token_bytes(16), repr=False)

    def text(self, value: str) -> str:
        out = redact_tokens(value, lambda kind: f"<redacted:{kind}>")
        out = _URL_USERINFO.sub(r"\1<redacted:userinfo>@", out)
        out = _QUERY_SECRET.sub(r"\1<redacted:query>", out)
        out = _KV_SECRET.sub(r"\1\2\3<redacted:value>", out)
        return _EMAIL.sub(self._pseudonym, out)

    def value(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        return value

    def recording(self, recording: Recording) -> Recording:
        raw = recording.model_dump(mode="json")
        manifest = raw.pop("manifest")
        scrubbed = self.value(raw)
        assert isinstance(scrubbed, dict)
        scrubbed["manifest"] = (
            Manifest.model_validate(manifest)
            .model_copy(update={"scrub_version": SCRUB_VERSION})
            .model_dump(mode="json")
        )
        return Recording.model_validate(scrubbed)

    def _pseudonym(self, match: re.Match[str]) -> str:
        address = match.group(0)
        domain = address.rsplit("@", 1)[1].lower()
        if domain in self.keep_domains:
            return address
        tag = hmac.new(
            self.key, address.lower().encode("utf-8"), hashlib.sha256
        ).hexdigest()[:8]
        return f"person-{tag}@{_PSEUDONYM_DOMAIN}"


def _walk_strings(value: JsonValue, visit: Callable[[str], None]) -> None:
    if isinstance(value, str):
        visit(value)
    elif isinstance(value, list):
        for v in value:
            _walk_strings(v, visit)
    elif isinstance(value, dict):
        for v in value.values():
            _walk_strings(v, visit)


def masked_preview(found: str) -> str:
    """What a finding shows of the text it found: enough to tell findings apart, never the value. Findings reach
    CI logs and exception messages, so the scan must not print the secret it caught."""
    return f"{found[:4]}… ({len(found)} chars)"


def find_secrets(value: JsonValue, canaries: Iterable[str] = ()) -> list[str]:
    """Every secret-shaped string, email or planted canary still in ``value``, each masked (``masked_preview``; an
    email keeps only its domain). A planted canary is the test's own marker and is named in full."""
    planted = [c for c in canaries if c]
    found: list[str] = []

    def visit(text: str) -> None:
        for p in REDACTION_PATTERNS:
            found.extend(f"{p.kind}: {masked_preview(m.group(0))}" for m in p.pattern.finditer(text))
        found.extend(
            f"userinfo: {masked_preview(m.group(0))}" for m in _URL_USERINFO.finditer(text)
        )
        found.extend(
            f"query: {masked_preview(m.group(0))}"
            for m in _QUERY_SECRET.finditer(text)
            if "<redacted" not in m.group(0)
        )
        found.extend(f"kv: {masked_preview(m.group(0))}" for m in _KV_SECRET.finditer(text))
        found.extend(
            f"email: …@{m.group(0).rpartition('@')[2]}"
            for m in _EMAIL.finditer(text)
            if not m.group(0).lower().endswith("@" + _PSEUDONYM_DOMAIN)
        )
        found.extend(f"canary: {c}" for c in planted if c in text)

    _walk_strings(value, visit)
    return found


def scrub_recording(
    recording: Recording,
    *,
    canaries: Iterable[str] = (),
    scrubber: Scrubber | None = None,
) -> Recording:
    """Scrub, then refuse to return a recording that still carries a secret.

    Pass the ``scrubber`` the recorder used so request digests and pseudonyms agree.
    """
    scrubbed = (scrubber or Scrubber()).recording(recording)
    leftovers = find_secrets(scrubbed.model_dump(mode="json"), canaries)
    if leftovers:
        raise SecretLeftBehind(leftovers)
    return scrubbed


class SecretLeftBehind(AssertionError):
    def __init__(self, found: list[str]) -> None:
        super().__init__("scrubbed recording still contains: " + "; ".join(found[:10]))
        self.found = found


__all__ = [
    "SCRUB_VERSION",
    "Scrubber",
    "SecretLeftBehind",
    "find_secrets",
    "masked_preview",
    "scrub_recording",
]

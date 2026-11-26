"""Golden traces: a production run's journal captured as a redacted replay case (EV-OPS-01, C17.07).

An incident becomes a regression case before its fix ships: ``capture`` takes the run's journal as Node exports it
(``{"events": [...]}`` from the run's events route, a JSON array of envelopes, or JSON lines), keeps its structure and
drops its content, and writes a case the corpus test (``tests/harness_replay/test_golden_traces.py``) replays through
the kernel's fold and the session's ``ProvenanceLedger`` on every build.

What is kept and what is not (``TraceRedactor``):

- Kept: event types and order, seq, envelope members, numbers and booleans, and the structural strings the fold and
  the ledger read (tool ids and names, call and invocation ids, statuses, outcomes, effects, decisions, rules, digests,
  timestamps), when they are token-shaped and no secret or personal-data scanner matches them.
- Pseudonymised: org, session, stream, run, attempt, user and conversation ids (``run-1``, ``org-1``: first seen,
  first numbered, so a second capture of a case leaves it unchanged), email addresses (the replay scrubber's HMAC
  pseudonym), and URLs (``https://example.invalid/<tag>``, which stays a URL).
- Replaced: every other string, the person's words, the model's text and every tool result, by
  ``<text:<tag>:<length>>``; equal strings get the same tag within a case, under a per-capture random key, so a
  short answer cannot be recovered by hashing guesses. A string under a secret-named key is ``<redacted:secret>``.
  Content carried inline (``inline:`` refs) is decoded, redacted by the same rule and re-encoded with its hash and size.

Nothing about a run's content is needed to reproduce what the harness did with it: the fold, the resume cursor and
the provenance facts are decided by the structure. A case is written only once it is proven: the redacted journal
re-scanned clean (every string, inline content included), a second redaction leaving it unchanged, and its replay
equal to the original's.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from harness_core.contracts.canonical_json import canonical_json
from harness_core.journal.inline import INLINE_REF_PREFIX, content_digest
from harness_core.journal.types import JournalEvent
from harness_core.kernel.fold import FoldIncompatible, fold_run
from harness_core.provenance.ledger import ProvenanceLedger
from harness_core.security.personal_data import redact_personal
from harness_core.security.secrets import SECRET_KEY_NAME
from harness_core.testing.replay.scrub import Scrubber, find_secrets, masked_preview

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

GOLDEN_FORMAT: Final = "pipeshub.harness.golden-trace/v1"
GOLDEN_SCRUB_VERSION: Final = "1"

# Envelope members and data keys whose values are identities: pseudonymised, the same id to the same name everywhere.
_IDENTITIES: Final[dict[str, str]] = {
    "orgId": "org",
    "sessionId": "ses",
    "streamId": "ses",
    "runId": "run",
    "parentRunId": "run",
    "childRunId": "run",
    "rootRunId": "run",
    "userId": "user",
    "actorId": "user",
    "conversationId": "conv",
}
# Keys whose token-shaped values the fold, the ledger and a reader of the case need verbatim.
_STRUCTURAL: Final = frozenset(
    {
        "type", "kind", "status", "outcome", "state", "reason", "rule", "layer", "effect", "decision", "purpose",
        "mode", "chatMode", "route", "provider", "transport", "model", "modelName", "toolName", "writer",
        "visibility", "readers", "integrity", "recordKind", "sensitivity", "egress", "role", "stopReason", "level",
        "channel", "priorityClass", "runKind", "principalRef", "sandboxProfile", "via", "promptVersion", "source",
        "sourceKind", "origin", "cause", "phase", "format", "ref", "hash", "key", "attemptId", "streamId",
        "connector", "agentKey", "errorType", "errorCode", "code", "classification",
    }
)
# Suffixes of structural keys: ids, digests, versions, kinds (``invocationId``, ``argsDigest``, ``schemaVersion``).
_STRUCTURAL_SUFFIX: Final = re.compile(r"(?:Id|Ids|Digest|Version|Kind|Type|Status|Rule|Code)$")
# Keys that name a person or a document whatever their suffix.
_PERSONAL_KEYS: Final = re.compile(
    r"(?i)^(?:display|full|first|last|user|record|file|author|owner|sender|recipient)_?name$|^(?:email|phone|title)$"
)
_TOKEN: Final = re.compile(r"^[^\s<>\"'`]{1,256}$")
_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIMESTAMP: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$")
_INVOCATION: Final = re.compile(r"^(?:ix\.[a-z_]+\.)?t\d+\.\d+$")
_TOOL_ID: Final = re.compile(r"^[a-z0-9_-]{1,64}@[A-Za-z0-9_.-]{1,128}/[A-Za-z0-9_.-]{1,128}$")
_PLACEHOLDER: Final = re.compile(r"^(?:<text:[0-9a-f]{8}:\d+>|<redacted:[a-z_]+>|<bytes:\d+>|<key:[0-9a-f]{8}>)$")
_PSEUDONYM: Final = re.compile(r"^(?:org|ses|run|user|conv)-\d+$|^person-[0-9a-f]{8}@example\.invalid$")
_URL: Final = re.compile(r"^(?i:https?)://\S+$")
_PSEUDONYM_URL: Final = re.compile(r"^https://example\.invalid/[0-9a-f]{8}$")
_DATA_KEY: Final = re.compile(r"^[A-Za-z_$][A-Za-z0-9_.$:@/-]{0,127}$")
_SECRET_MASK: Final = "<redacted:secret>"


class RedactionNotProven(ValueError):
    """A capture that could not be shown safe or faithful; nothing is written."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("golden trace refused: " + "; ".join(reasons[:10]))
        self.reasons = list(reasons)


@dataclass
class TraceRedactor:
    """Stateful over one capture: the same id, address or text maps to the same stand-in throughout."""

    key: bytes = field(default_factory=lambda: secrets.token_bytes(16), repr=False)
    _ids: dict[str, str] = field(default_factory=dict[str, str])
    _counts: dict[str, int] = field(default_factory=dict[str, int])
    _run_ids: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self._scrubber = Scrubber(key=self.key)

    # ---- events ----

    def events(self, envelopes: Sequence[Mapping[str, JsonValue]]) -> list[dict[str, JsonValue]]:
        # Identities first, in stream order, so pseudonyms do not depend on where in a payload an id first appears.
        for envelope in envelopes:
            for member in ("orgId", "sessionId", "streamId", "runId"):
                value = envelope.get(member)
                if isinstance(value, str):
                    _ = self._identity(member, value)
        return [self._envelope(envelope) for envelope in envelopes]

    def _envelope(self, envelope: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {}
        for member, value in envelope.items():
            if member in ("data", "labels"):
                out[member] = self.value(value, member) if isinstance(value, dict) else value
            else:
                out[member] = self.value(value, member)
        return out

    # ---- values ----

    def value(self, value: JsonValue, key: str) -> JsonValue:
        if isinstance(value, str):
            return self._string(value, key)
        if isinstance(value, list):
            return [self.value(item, key) for item in value]
        if isinstance(value, dict):
            if isinstance(value.get("ref"), str) and str(value["ref"]).startswith(INLINE_REF_PREFIX):
                return self._inline(value)
            return {self._key(k): self.value(v, k) for k, v in value.items()}
        return value

    def _string(self, value: str, key: str) -> str:
        if key in _IDENTITIES:
            return self._identity(key, value)
        if _PLACEHOLDER.fullmatch(value) or _PSEUDONYM.fullmatch(value) or _PSEUDONYM_URL.fullmatch(value):
            return value
        if SECRET_KEY_NAME.search(key) and not _DIGEST.fullmatch(value) and not _structural_key(key):
            return _SECRET_MASK
        if _DIGEST.fullmatch(value) or _TIMESTAMP.fullmatch(value) or _INVOCATION.fullmatch(value):
            return value
        if _URL.fullmatch(value):
            return f"https://example.invalid/{self._tag(value)}"
        if _TOOL_ID.fullmatch(value) or (_structural_key(key) and _TOKEN.fullmatch(value)):
            kept = self._known_runs(self._scrubber.text(value))
            if _clean(kept):
                return kept
            return _SECRET_MASK
        return f"<text:{self._tag(value)}:{len(value)}>"

    def _key(self, key: str) -> str:
        if _DATA_KEY.fullmatch(key) and _clean(key):
            return key
        return f"<key:{self._tag(key)}>"

    def _inline(self, ref: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        raw = str(ref["ref"]).removeprefix(INLINE_REF_PREFIX)
        try:
            data = base64.urlsafe_b64decode(raw)
        except (binascii.Error, ValueError):
            data = b""
        redacted = self.content(data)
        out = {k: self.value(v, k) for k, v in ref.items() if k not in ("ref", "hash", "size")}
        out["ref"] = INLINE_REF_PREFIX + base64.urlsafe_b64encode(redacted).decode("ascii")
        out["hash"] = content_digest(redacted)
        out["size"] = len(redacted)
        return out

    def content(self, data: bytes) -> bytes:
        """Inline bytes redacted: JSON by the same rule (canonical form), text as one string, anything else gone."""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"<bytes:{len(data)}>".encode()
        try:
            parsed = json.loads(text)
        except ValueError:
            return self._string(text, "content").encode("utf-8")
        return canonical_json(self.value(parsed, "content"))

    # ---- stand-ins ----

    def _identity(self, key: str, value: str) -> str:
        found = self._ids.get(value)
        if found is None:
            prefix = _IDENTITIES[key]
            # Numbered in the order the stream names them, so a second capture of a case maps each to itself.
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            found = self._ids[value] = f"{prefix}-{self._counts[prefix]}"
            if prefix == "run":
                self._run_ids.append(value)
        return found

    def _known_runs(self, value: str) -> str:
        """A structural string that embeds a run id (an attempt id, ``<runId>.a1``) with the run's pseudonym."""
        for run in sorted(self._run_ids, key=len, reverse=True):
            if run in value:
                value = value.replace(run, self._ids[run])
        return value

    def _tag(self, value: str) -> str:
        return hmac.new(self.key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:8]


def _structural_key(key: str) -> bool:
    if _PERSONAL_KEYS.search(key):
        return False
    return key in _STRUCTURAL or bool(_STRUCTURAL_SUFFIX.search(key))


def _clean(value: str) -> bool:
    found: set[str] = set()
    _ = redact_personal(value, found)
    return not found and not find_secrets(value)


# ---- the case ----


class GoldenManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case: str
    run_id: str
    scrub_version: str = GOLDEN_SCRUB_VERSION
    # The incident or issue the case reproduces (an id, never a description).
    incident: str | None = None
    source_events: int = 0


class GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal["pipeshub.harness.golden-trace/v1"] = GOLDEN_FORMAT
    manifest: GoldenManifest
    events: list[dict[str, JsonValue]]
    # What the journal replays to (``replay_signature``), as the original run's journal did.
    expected: dict[str, JsonValue]

    def journal(self) -> list[JournalEvent]:
        return [JournalEvent.from_envelope(e) for e in self.events]

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=1, ensure_ascii=False, sort_keys=False) + "\n"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> GoldenCase:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def replay_signature(events: Sequence[JournalEvent], run_id: str) -> dict[str, JsonValue]:
    """What the harness makes of a run's journal, without its content: the fold (status, turns, every invocation and
    interaction, consumption, children), the resume cursor and the session ledger's provenance facts."""
    state = fold_run(events, run_id=run_id)
    ledger = ProvenanceLedger()
    ledger.apply(events)
    cursor = state.cursor
    signature = {
        "status": state.status,
        "outcome": state.outcome,
        "turn": state.turn,
        "turnCompleted": state.turn_completed,
        "attempt": state.attempt,
        "ignoredEvents": state.ignored_events,
        "cursor": {"nextAction": cursor.next_action, "turn": cursor.turn, "pending": list(cursor.pending)},
        "suspension": state.suspension.reason if state.suspension is not None else None,
        "responses": [
            {"turn": r.turn, "provider": r.provider, "transport": r.transport, "tools": [c.name for c in r.tool_calls]}
            for r in state.responses
        ],
        "invocations": [
            {
                "id": i.invocation_id,
                "name": i.name,
                "toolId": i.tool_id,
                "state": i.state,
                "effect": i.effect,
                "status": i.status,
                "decision": i.decision,
                "deniedRule": i.denied_rule,
                "child": i.child_run_id is not None,
            }
            for i in sorted(state.invocations.values(), key=lambda i: (i.turn, i.index))
        ],
        "interactions": [
            {"id": k, "kind": v.kind, "purpose": v.purpose, "decision": v.decision}
            for k, v in sorted(state.interactions.items())
        ],
        "consumed": state.consumed.model_dump(mode="json"),
        "children": sorted(
            [{"completed": c.completed, "outcome": c.outcome, "errorType": c.error_type} for c in state.children.values()],
            key=lambda c: json.dumps(c, sort_keys=True),
        ),
        "provenance": [
            {
                "invocationId": f.invocation_id,
                "rule": f.rule,
                "recorded": f.recorded,
                "readers": f.provenance.readers,
                "integrity": f.provenance.integrity,
                "sources": sorted({s.kind for s in f.provenance.sources}),
            }
            for f in ledger.facts(run_id)
        ],
    }
    # One JSON round trip: the signature compares equal to the one a case file holds.
    return cast("dict[str, JsonValue]", json.loads(json.dumps(signature)))


def load_export(text: str) -> list[dict[str, JsonValue]]:
    """A run's journal as exported: ``{"events": [...]}``, a JSON array of envelopes, or one envelope per line."""
    stripped = text.strip()
    try:
        parsed: object = json.loads(stripped)
    except ValueError:
        parsed = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    if isinstance(parsed, dict):
        parsed = cast("dict[str, object]", parsed).get("events")
    if not isinstance(parsed, list):
        raise RedactionNotProven(["the export is not a list of journal envelopes"])
    envelopes: list[dict[str, JsonValue]] = []
    for entry in cast("list[object]", parsed):
        if not isinstance(entry, dict) or not isinstance(cast("dict[str, object]", entry).get("seq"), int):
            raise RedactionNotProven(["the export is not a list of journal envelopes"])
        envelopes.append(cast("dict[str, JsonValue]", entry))
    return sorted(envelopes, key=lambda e: cast("int", e["seq"]))


def _strings(value: JsonValue) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        if value.startswith(INLINE_REF_PREFIX):
            try:
                decoded = base64.urlsafe_b64decode(value.removeprefix(INLINE_REF_PREFIX)).decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                return
            try:
                yield from _strings(json.loads(decoded))
            except ValueError:
                yield decoded
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)


def leftovers(events: Sequence[Mapping[str, JsonValue]], canaries: Iterable[str] = ()) -> list[str]:
    """Every secret, personal-data shape or planted canary still in ``events``, inline content included."""
    planted = [c for c in canaries if c]
    found: list[str] = []
    for text in _strings(list(events)):  # type: ignore[arg-type]
        # A digest's hex and a timestamp's digits are not a card or a phone number (nor, inline, the content).
        if text.startswith(INLINE_REF_PREFIX) or _DIGEST.fullmatch(text) or _TIMESTAMP.fullmatch(text):
            continue
        found.extend(find_secrets(text, planted))
        kinds: set[str] = set()
        _ = redact_personal(text, kinds)
        found.extend(f"{kind}: {masked_preview(text)}" for kind in sorted(kinds) if not _PSEUDONYM.search(text))
    return found


def _run_of(envelopes: Sequence[Mapping[str, JsonValue]], run_id: str | None) -> str:
    runs = list(dict.fromkeys(str(e["runId"]) for e in envelopes if isinstance(e.get("runId"), str)))
    if run_id is not None:
        if run_id not in runs:
            raise RedactionNotProven([f"run {run_id!r} is not in the export"])
        return run_id
    if len(runs) != 1:
        raise RedactionNotProven([f"the export holds {len(runs)} runs; name one with --run"])
    return runs[0]


def capture(
    export: Sequence[Mapping[str, JsonValue]],
    *,
    case: str,
    run_id: str | None = None,
    incident: str | None = None,
    canaries: Iterable[str] = (),
    key: bytes | None = None,
) -> GoldenCase:
    """The run's events as a proven golden case; ``RedactionNotProven`` when it cannot be shown clean and faithful."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", case):
        raise RedactionNotProven(["a case id is lower-case letters, digits and dashes"])
    if incident is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.#-]{0,63}", incident):
        raise RedactionNotProven(["an incident is an id, not a description"])
    run = _run_of(export, run_id)
    own = [dict(e) for e in export if e.get("runId") == run]
    try:
        original = replay_signature([JournalEvent.from_envelope(e) for e in own], run)
    except (FoldIncompatible, KeyError, ValueError) as unreadable:
        raise RedactionNotProven([f"the original journal does not replay: {unreadable}"]) from unreadable
    redactor = TraceRedactor() if key is None else TraceRedactor(key=key)
    events = redactor.events(own)
    pseudonym = redactor.value(run, "runId")
    assert isinstance(pseudonym, str)
    golden = GoldenCase(
        manifest=GoldenManifest(case=case, run_id=pseudonym, incident=incident, source_events=len(own)),
        events=events,
        expected=original,
    )
    problems = verify(golden, canaries=canaries, key=redactor.key)
    if problems:
        raise RedactionNotProven(problems)
    return golden


def verify(golden: GoldenCase, *, canaries: Iterable[str] = (), key: bytes | None = None) -> list[str]:
    """Why ``golden`` may not be committed: what the scanners still find, a redaction that is not idempotent, or a
    replay that differs from the original's. Empty: proven."""
    problems = leftovers(golden.events, canaries)
    again = (TraceRedactor() if key is None else TraceRedactor(key=key)).events(golden.events)
    if again != golden.events:
        problems.append("a second redaction changes the case (not idempotent)")
    try:
        replayed = replay_signature(golden.journal(), golden.manifest.run_id)
    except (FoldIncompatible, KeyError, ValueError) as broken:
        return [*problems, f"the redacted journal does not replay: {broken}"]
    if replayed != golden.expected:
        problems.append("the redacted journal replays differently from the original")
    return problems


__all__ = [
    "GOLDEN_FORMAT",
    "GOLDEN_SCRUB_VERSION",
    "GoldenCase",
    "GoldenManifest",
    "RedactionNotProven",
    "TraceRedactor",
    "capture",
    "leftovers",
    "load_export",
    "replay_signature",
    "verify",
]

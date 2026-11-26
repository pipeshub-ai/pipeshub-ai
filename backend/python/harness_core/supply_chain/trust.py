"""Trust roots per artefact kind, signed attestations, and the one decision whether an artefact may be used.

An artefact is trusted by exactly one route (ADR-SEC-01 D1): it is first party (PipesHub's release shipped it or a
signed-in member wrote it in PipesHub), an attestation over its digest verifies against a root of its kind, or an
admin approved that exact digest. ``decide_trust`` is that decision; every use site calls it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, cast, override

from harness_core.contracts.canonical_json import canonical_json
from harness_core.supply_chain.signatures import SignatureRefused, verify_detached

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.engines.types import JsonValue

ATTESTATION_SCHEMA: Final = "pipeshub.attestation/1"


class ArtefactKind(str):
    """An artefact kind, by its name: this build's are the class's constants, and an edition adds its own with
    ``register_artefact_kind``. ``ArtefactKind(name)`` is the one kind of that name; a name no kind has is refused
    (``ValueError``), so an attestation of a kind nobody declared never verifies."""

    __slots__ = ()

    RELEASE_DATA: ClassVar[ArtefactKind]
    ENGINE_IMAGE: ClassVar[ArtefactKind]
    SKILL: ClassVar[ArtefactKind]
    MCP_SERVER: ClassVar[ArtefactKind]

    def __new__(cls, name: str) -> ArtefactKind:
        known = _KINDS.get(name)
        if known is None:
            raise ValueError(f"{name!r} is not an artefact kind")
        return known

    @override
    def __reduce__(self) -> tuple[type[ArtefactKind], tuple[str]]:
        return (ArtefactKind, (self.value,))

    @property
    def value(self) -> str:
        return str.__str__(self)

    @classmethod
    def declared(cls) -> frozenset[ArtefactKind]:
        return frozenset(_KINDS.values())


def register_artefact_kind(name: str) -> ArtefactKind:
    """Declares the kind ``name`` (an edition's own) and returns it; declaring one twice returns the same kind."""
    known = _KINDS.get(name)
    if known is not None:
        return known
    if not name or not name.replace("_", "").isalnum() or not name.islower():
        raise ValueError(f"{name!r} is not a kind name")
    kind = str.__new__(ArtefactKind, name)
    _KINDS[name] = kind
    return kind


_KINDS: Final[dict[str, ArtefactKind]] = {}

ArtefactKind.RELEASE_DATA = register_artefact_kind("release_data")
ArtefactKind.ENGINE_IMAGE = register_artefact_kind("engine_image")
ArtefactKind.SKILL = register_artefact_kind("skill")
ArtefactKind.MCP_SERVER = register_artefact_kind("mcp_server")


# Only the release key vouches for these: roots any other source adds for them must be refused (D2).
RELEASE_KINDS: Final = frozenset({ArtefactKind.RELEASE_DATA, ArtefactKind.ENGINE_IMAGE})

PENDING_REVIEW: Final = "artefact_pending_review"
DENIED: Final = "artefact_denied"
UNAPPROVED: Final = "artefact_unapproved"
SIGNATURE_INVALID: Final = "artefact_signature_invalid"
UNKNOWN_KEY: Final = "artefact_unknown_key"


class ArtefactUntrusted(Exception):
    """An artefact none of the three routes trusts; ``reason`` is the code the person's words key on."""

    def __init__(
        self, reason: str, message: str, *, kind: ArtefactKind | None, subject: str
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.kind = kind
        self.subject = subject


@dataclass(frozen=True, slots=True)
class TrustRoots:
    """Per kind, the keys (key id -> raw 32-byte Ed25519 public key) whose attestations it accepts."""

    keys: Mapping[ArtefactKind, Mapping[str, bytes]] = field(
        default_factory=dict[ArtefactKind, "Mapping[str, bytes]"]
    )

    def for_kind(self, kind: ArtefactKind) -> Mapping[str, bytes]:
        return self.keys.get(kind, {})

    def merged(self, other: TrustRoots) -> TrustRoots:
        kinds = {*self.keys, *other.keys}
        return TrustRoots({k: {**self.for_kind(k), **other.for_kind(k)} for k in kinds})


def statement(
    kind: ArtefactKind,
    subject: str,
    digest: str,
    claims: Mapping[str, JsonValue] | None = None,
) -> bytes:
    """The bytes an attestation signs: RFC 8785 canonical JSON, so both languages and every signer agree on them."""
    body: dict[str, JsonValue] = {
        "schema": ATTESTATION_SCHEMA,
        "kind": kind.value,
        "subject": subject,
        "digest": digest,
    }
    if claims:
        body["claims"] = dict(claims)
    return canonical_json(body)


@dataclass(frozen=True, slots=True)
class Attestation:
    """A signed statement that ``digest`` is the artefact ``subject`` of ``kind``, with what its signer claims of it
    (for an image: its base, scan and certificate bundle)."""

    kind: ArtefactKind
    subject: str
    digest: str
    signature: bytes
    claims: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])

    def signed_bytes(self) -> bytes:
        return statement(self.kind, self.subject, self.digest, self.claims)

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "statement": json.loads(self.signed_bytes()),
            "signature": json.loads(self.signature),
        }


def parse_attestation(document: bytes | Mapping[str, object]) -> Attestation:
    """An attestation file: ``{"statement": {...}, "signature": {"alg", "keyId", "sig"}}``."""
    try:
        raw: object = json.loads(document) if isinstance(document, bytes) else document
    except (ValueError, UnicodeDecodeError):
        raise ArtefactUntrusted(
            SIGNATURE_INVALID, "the attestation is not JSON", kind=None, subject=""
        ) from None
    doc = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    body = doc.get("statement")
    signature = doc.get("signature")
    stmt = cast("dict[str, object]", body) if isinstance(body, dict) else {}
    try:
        kind = ArtefactKind(str(stmt.get("kind")))
    except ValueError:
        raise ArtefactUntrusted(
            SIGNATURE_INVALID,
            "the attestation names no known kind",
            kind=None,
            subject="",
        ) from None
    subject, digest = stmt.get("subject"), stmt.get("digest")
    claims: object = stmt.get("claims") or {}
    if (
        stmt.get("schema") != ATTESTATION_SCHEMA
        or not isinstance(subject, str)
        or not isinstance(digest, str)
        or not isinstance(claims, dict)
        or not isinstance(signature, dict)
    ):
        raise ArtefactUntrusted(
            SIGNATURE_INVALID,
            "the attestation is malformed",
            kind=kind,
            subject=str(subject or ""),
        )
    return Attestation(
        kind,
        subject,
        digest,
        json.dumps(signature).encode(),
        cast("dict[str, JsonValue]", claims),
    )


def verify_attestation(
    attestation: Attestation, roots: TrustRoots, *, digests: Iterable[str]
) -> str:
    """The key id that vouches for one of ``digests`` (what the artefact is, read by the verifier itself); refused
    when the attestation names another artefact, or its signature is not by a root of its kind."""
    if attestation.digest not in set(digests):
        raise ArtefactUntrusted(
            SIGNATURE_INVALID,
            f"the attestation is for {attestation.digest}, not for this {attestation.kind.value}",
            kind=attestation.kind,
            subject=attestation.subject,
        )
    try:
        return verify_detached(
            attestation.signed_bytes(),
            attestation.signature,
            roots.for_kind(attestation.kind),
        )
    except SignatureRefused as refused:
        reason = UNKNOWN_KEY if refused.detail == "unknown_key" else SIGNATURE_INVALID
        raise ArtefactUntrusted(
            reason, str(refused), kind=attestation.kind, subject=attestation.subject
        ) from None


TrustRoute = Literal["first_party", "signature", "approval", "scan"]


class ReviewState(Protocol):
    """An admin's standing on one digest of an artefact, where a policy keeps one."""

    @property
    def state(self) -> str: ...

    @property
    def by(self) -> str: ...


@dataclass(frozen=True, slots=True)
class TrustVerdict:
    route: TrustRoute
    # The key id for a signature, the approver for an approval; empty for first-party or scanned content.
    by: str = ""


def decide_trust(
    kind: ArtefactKind,
    subject: str,
    digest: str,
    *,
    first_party: bool,
    roots: TrustRoots,
    attestation: Attestation | None = None,
    review: ReviewState | None = None,
) -> TrustVerdict:
    """Whether ``digest`` of ``subject`` may be used (D1). A valid signature wins over a pending or missing review;
    a denial wins over a signature (an admin who declined content means it, whoever signed it)."""
    if first_party:
        return TrustVerdict("first_party")
    if review is not None and review.state == "denied":
        raise ArtefactUntrusted(
            DENIED, f"an admin declined this {kind.value}", kind=kind, subject=subject
        )
    if attestation is not None:
        return TrustVerdict(
            "signature", verify_attestation(attestation, roots, digests=(digest,))
        )
    if review is not None and review.state == "approved":
        return TrustVerdict("approval", review.by)
    if review is not None and review.state == "pending":
        raise ArtefactUntrusted(
            PENDING_REVIEW,
            f"this {kind.value} waits for an admin's review",
            kind=kind,
            subject=subject,
        )
    raise ArtefactUntrusted(
        UNAPPROVED,
        f"this {kind.value} is neither signed nor approved",
        kind=kind,
        subject=subject,
    )


__all__ = [
    "ATTESTATION_SCHEMA",
    "DENIED",
    "PENDING_REVIEW",
    "RELEASE_KINDS",
    "SIGNATURE_INVALID",
    "UNAPPROVED",
    "UNKNOWN_KEY",
    "ArtefactKind",
    "ArtefactUntrusted",
    "Attestation",
    "TrustRoots",
    "TrustRoute",
    "TrustVerdict",
    "decide_trust",
    "parse_attestation",
    "register_artefact_kind",
    "statement",
    "verify_attestation",
]

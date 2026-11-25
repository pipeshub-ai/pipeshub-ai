"""Security decisions counted where they are made (SEC-D-13, ADR-OPS-03): a credential or token refused, an egress
request allowed or refused.

The deciding code calls ``token_rejected`` or ``egress_decided``; the process installs where the counts go
(``install``: PipesHub's Prometheus registry, ``app.telemetry.modules.security_metrics``). Every label is mapped
onto a fixed set here, whatever the caller passes, so no token, id, host or caller text can become a series. A
sink that raises never changes the decision that called it.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Protocol

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

OTHER = "other"

# What was refused: a machine role's credential (worker, scraper, broker, proxy), a run attempt's token (Node only),
# a run's gateway token (the Model and Tool Gateways).
TOKEN_KINDS: tuple[str, ...] = ("machine_credential", "attempt_token", "gateway_token")
# Node's ``HarnessUnauthorizedReason`` and the gateways' ``TokenRefusalReason`` (``token_`` dropped) are one set.
TOKEN_REASONS: tuple[str, ...] = (
    "missing",
    "malformed",
    "algorithm",
    "unknown_key",
    "signature",
    "issuer",
    "audience",
    "expired",
    "claims",
    "superseded",
    "credential",
)
# A token only someone without the key would present: what HarnessGatewayTokenRejected pages on.
FORGED_REASONS: frozenset[str] = frozenset(
    {"malformed", "algorithm", "unknown_key", "signature", "issuer", "audience", "credential"}
)
_TOKEN_ALIASES = {"binding_mismatch": "claims", "credential_not_accepted": "credential"}

EGRESS_DECISIONS: tuple[str, ...] = ("allowed", "refused")
# An allowed request's reason is how it went out; a refused one's is its ``EgressRefusalReason``.
EGRESS_ALLOWED_REASONS: tuple[str, ...] = ("http", "intercepted", "database", "tunnel")

_LOG = HarnessLog.named("harness_core.telemetry.security_signals")


class SecuritySignalSink(Protocol):
    """Where the counts go; it receives labels already in the fixed sets."""

    def token_rejected(self, kind: str, reason: str) -> None: ...

    def egress_decided(self, decision: str, reason: str) -> None: ...


class _NoSink:
    def token_rejected(self, kind: str, reason: str) -> None:
        pass

    def egress_decided(self, decision: str, reason: str) -> None:
        pass


class _Installed:
    sink: SecuritySignalSink = _NoSink()


def install(sink: SecuritySignalSink | None) -> None:
    """The process's sink; ``None``: counts go nowhere (the default)."""
    _Installed.sink = sink if sink is not None else _NoSink()


def token_kind(kind: str) -> str:
    return kind if kind in TOKEN_KINDS else OTHER


def token_reason(reason: str | None) -> str:
    if reason is None:
        return "missing"
    name = _TOKEN_ALIASES.get(reason, reason.removeprefix("token_"))
    return name if name in TOKEN_REASONS else OTHER


@cache
def egress_refused_reasons() -> tuple[str, ...]:
    """Every ``EgressRefusalReason``; read on first use, as the egress package imports this module."""
    from harness_core.sandbox.egress.errors import EgressRefusalReason

    return tuple(reason.value for reason in EgressRefusalReason)


def egress_labels(decision: str, reason: str | None) -> tuple[str, str]:
    if decision not in EGRESS_DECISIONS:
        return OTHER, OTHER
    known = EGRESS_ALLOWED_REASONS if decision == "allowed" else egress_refused_reasons()
    return decision, reason if reason in known else OTHER


def _guarded(signal: str, count: Callable[[], None]) -> None:
    try:
        count()
    except Exception as exc:
        _LOG.warning("security_signal.count_failed", signal=signal, errorType=type(exc).__name__)


def token_rejected(kind: str, reason: str | None) -> None:
    """A credential or token was refused; ``reason``: the refusal's code (``None``: none was presented)."""
    labels = (token_kind(kind), token_reason(reason))
    _guarded("token_rejected", lambda: _Installed.sink.token_rejected(*labels))


def machine_credential_refused(headers: Mapping[str, str]) -> None:
    """A listener refused the caller's machine credential: none presented (``missing``), or not one it accepts."""
    from harness_core.sandbox.auth import bearer

    token_rejected("machine_credential", "credential" if bearer(headers) is not None else None)


def egress_decided(decision: str, reason: str | None) -> None:
    """The egress proxy let a sandbox request out (``allowed``, how) or refused it (``refused``, why)."""
    labels = egress_labels(decision, reason)
    _guarded("egress_decided", lambda: _Installed.sink.egress_decided(*labels))


__all__ = [
    "EGRESS_ALLOWED_REASONS",
    "EGRESS_DECISIONS",
    "FORGED_REASONS",
    "OTHER",
    "TOKEN_KINDS",
    "TOKEN_REASONS",
    "SecuritySignalSink",
    "egress_decided",
    "egress_labels",
    "egress_refused_reasons",
    "install",
    "machine_credential_refused",
    "token_kind",
    "token_reason",
    "token_rejected",
]

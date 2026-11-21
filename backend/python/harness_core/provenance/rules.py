"""ResultProvenance rules: how a declared tool's result says what it carries (ADR-RUN-01 D3).

A tool is declared with a rule, and the registry refuses a declaration without one (``require_rules``): a new tool
cannot silently produce "unknown". The rule reads the facts of one result (its status, the host tool's own claim)
and answers its ``ResultProvenance``; what the call put in the run's citation state is joined in by the stamp
(``ProvenanceStamp``), whatever the rule, so a source a call surfaced is never lost by a rule that did not expect it.

Rules are declared per kind of tool, not per tool name: the platform's own tools by what they return, every MCP
server's tools by one rule (an outsider wrote the answer, nobody can name its content), every connector's actions
by one rule (the owner's items, written by outsiders).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

from harness_core.provenance.types import NO_CONTENT, ResultProvenance

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ResultFacts:
    """One result as its rule reads it. ``claim``: the host tool's own word on its result (``ToolExecution``'s
    ``provenance``), which only a ``HostClaim`` rule reads; an outside tool's result never carries one."""

    status: Literal["ok", "error", "timeout"]
    claim: ResultProvenance | None = None


class ProvenanceRule(Protocol):
    @property
    def name(self) -> str:
        """What the record names as the rule that decided (audit, replay)."""
        ...

    def provenance(self, facts: ResultFacts) -> ResultProvenance: ...


@dataclass(frozen=True, slots=True)
class Fixed:
    """A tool whose every result carries the same kind of content. ``failed``: what its error text is: the
    platform's own words (``NO_CONTENT``, WP-62: a toolset's "Bad credentials" names nothing), or an outsider's (an
    MCP server writes its own errors, so they stay what its answers are)."""

    name: str
    answered: ResultProvenance
    failed: ResultProvenance = NO_CONTENT

    def provenance(self, facts: ResultFacts) -> ResultProvenance:
        return self.answered if facts.status == "ok" else self.failed


@dataclass(frozen=True, slots=True)
class HostClaim:
    """A host tool that says what each result is (``repository_fetch``: public when git dialled a public address with
    no credential, WP-83); without the claim, ``fallback``. Its errors are its own text."""

    name: str
    fallback: ResultProvenance

    def provenance(self, facts: ResultFacts) -> ResultProvenance:
        if facts.status != "ok":
            return NO_CONTENT
        return facts.claim if facts.claim is not None else self.fallback


_UNKNOWN_EXTERNAL: Final = ResultProvenance("unknown", "external", ())

# The platform's tools that return no record content (a question to the person, planning, tool disclosure, a
# receipt), the knowledge tools (their records ride the citation state, joined in by the stamp), and the tools whose
# result is derived from their own arguments (the calculators: FLOWTRUST D3).
NO_CONTENT_RULE: Final = Fixed("no_content", NO_CONTENT)
RECORDS_RULE: Final = Fixed("records", NO_CONTENT)
DERIVED_RULE: Final = Fixed("derived", NO_CONTENT)
# A delegate: an in-process agent's answer is what its own tools returned, which no record names. A delegate that
# runs as a child run is the child's to say: the ledger reads a call that spawned one as carrying nothing of its own.
DELEGATE_RULE: Final = Fixed("delegate", ResultProvenance("unknown", "first_party", ()))
# A web search or an unauthenticated GET of a public address: public, written by outsiders (FG2), unless the host's
# review claims otherwise (a page from an address only the org reaches, WP83-FU-3). Its pages ride the citation state.
WEB_RULE: Final = HostClaim("web", ResultProvenance("public", "external", ()))
# Every tool of every MCP server: nobody can name what its server returns, and its server wrote it, errors included.
MCP_RULE: Final = Fixed("mcp", _UNKNOWN_EXTERNAL, _UNKNOWN_EXTERNAL)
# Every connector's actions: the owner's items as the vendor returns them; unknown unless what the call put in the
# citation state names them. First party, as the flow guard has always read a toolset's result: the rule
# (approvals follow main #4313) is that toolset writes run and the flow guard is unchanged, so a connector
# read must not make the Rule of Two ask on a later write. Outsider-written items it cites (KN-08) still taint.
CONNECTOR_RULE: Final = Fixed("connector", ResultProvenance("unknown", "first_party", ()))
# The platform's own tool whose result the platform cannot name (code a sandbox ran, a toolset tool no table names).
OPAQUE_RULE: Final = Fixed("opaque", ResultProvenance("unknown", "first_party", ()))
# A repository the worker fetched: outsiders write it; public only by the fetch's own claim (WP-83 D1, D4).
REPOSITORY_RULE: Final = HostClaim("repository", _UNKNOWN_EXTERNAL)

RULES: Final[Mapping[str, ProvenanceRule]] = {
    rule.name: rule
    for rule in (
        NO_CONTENT_RULE, RECORDS_RULE, DERIVED_RULE, DELEGATE_RULE, WEB_RULE, MCP_RULE, CONNECTOR_RULE, OPAQUE_RULE,
        REPOSITORY_RULE,
    )
}


class UndeclaredProvenance(ValueError):
    """Tools declared without a provenance rule: refused, so none of them can produce a result nobody labelled."""

    def __init__(self, names: Iterable[str]) -> None:
        self.names = tuple(sorted(names))
        super().__init__(f"tools declared without a provenance rule: {list(self.names)}")


def require_rules(names: Iterable[str], rules: Mapping[str, ProvenanceRule | None]) -> dict[str, ProvenanceRule]:
    """``rules`` for ``names``; raises ``UndeclaredProvenance`` naming every one that has none."""
    wanted = list(names)
    missing = [name for name in wanted if rules.get(name) is None]
    if missing:
        raise UndeclaredProvenance(missing)
    return {name: rule for name in wanted if (rule := rules.get(name)) is not None}


__all__ = [
    "CONNECTOR_RULE",
    "DELEGATE_RULE",
    "DERIVED_RULE",
    "MCP_RULE",
    "NO_CONTENT_RULE",
    "OPAQUE_RULE",
    "RECORDS_RULE",
    "REPOSITORY_RULE",
    "RULES",
    "WEB_RULE",
    "Fixed",
    "HostClaim",
    "ProvenanceRule",
    "ResultFacts",
    "UndeclaredProvenance",
    "require_rules",
]

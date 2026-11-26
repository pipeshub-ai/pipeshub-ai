"""C-37 (USAGE1 U1-17): ``TokenUsage.input_tokens`` is made to mean the whole prompt in one place per provider.

Main and the harness each stopped billing cached tokens twice, one where usage is produced and one in every
reader; merged, an OpenAI call had its cached tokens subtracted twice. The rule since: whoever builds a
``TokenUsage`` from a provider payload (a transport, the LangChain converter) counts cached tokens into
``input_tokens``, and nothing after it converts again.

The scan finds every sum or difference that mixes an input count with a cached count. One is allowed in a
producer, and at the sites in ``SITES``, each with its reason; any other is a second normalisation. A listed
site that no longer mixes them fails too, so the list only shrinks. Node and the frontend carry usage in
events and never compute with it; a file that starts to must read shared vectors (C-33) and is caught here.
"""

from __future__ import annotations

import ast
import re
from functools import cache

import pytest

from tests.cohesion.support import NODE_HARNESS, PYTHON_ROOT, REPO_ROOT, modules
from tests.edition_overlay import extended

# Build a ``TokenUsage`` from a provider's own payload.
PRODUCERS = ("harness_core/models/transports/", "app/agents/agent_loop/converters.py")

SITES: dict[str, str] = {
    "harness_core/kernel/budget/pricing.py::ModelPricing.cost_usd": "prices the uncached remainder as fresh input (main's rule)",
    "harness_core/kernel/core/responses.py::TokenUsage.uncached_input_tokens": "the uncached part, for a wire that reports it apart",
    "harness_core/models/families.py::legacy_input_tokens": "reads a usage journaled before the convention",
    "harness_core/kernel/budget/tracker.py::BudgetTracker.restore": "reads a budget snapshot saved before the convention",
}

# The only readers of the two conversions above that leave their own module.
CALLERS: dict[str, frozenset[str]] = {
    "legacy_input_tokens": frozenset({"harness_core/kernel/fold.py"}),
    # spend.py: the token dimension weighs fresh prompt tokens apart from cached reads (ENGBUDGET).
    "uncached_input_tokens": frozenset({"harness_core/gateway/messages_wire.py", "harness_core/access/spend.py"}),
}
DEFINED_IN = {
    "legacy_input_tokens": "harness_core/models/families.py",
    "uncached_input_tokens": "harness_core/kernel/core/responses.py",
}

_INPUT = re.compile(r"(?i)^_?(input|prompt)_?tokens$|^prompt_token_count$|^input_with_cache$")
_CACHED = re.compile(r"(?i)cache_?(read|write|creation)|cached")
_NODE_USAGE = re.compile(r"cacheReadTokens|cacheWriteTokens|inputIncludesCached")
# Node files that store, add up per field or copy a usage as its reporter gave it, never converting between input
# counts with and without cached tokens. The ledger's sums are pinned for Node and Python by the shared table
# (C-33: contracts/harness/vectors/budget-ledger.json LV-14); the list only shrinks.
_AGENT_HARNESS = "backend/nodejs/apps/src/modules/agent-harness/"
NODE_CARRIERS: dict[str, str] = {
    f"{_AGENT_HARNESS}domain/budget-ledger.ts": "a run's ledger usage, summed per field (OWD-8; LV-14)",
    "backend/nodejs/apps/src/libs/repositories/agent-harness/budget-ledger/mongo.ts": "the same sum as a Mongo $inc per field (LV-14)",
    "backend/nodejs/apps/src/libs/repositories/agent-harness/budget-ledger/schema/budget-ledger.schema.ts": "the stored usage subdocument",
    f"{_AGENT_HARNESS}transport/schemas.ts": "the bounds of a commit's usage on the wire",
    f"{_AGENT_HARNESS}adapters/telemetry/chat-path-metrics.ts": (
        "counts each committed usage field as its own series (OB-04 cache ratio); the ratio is PromQL's, never Node's"
    ),
    f"{_AGENT_HARNESS}application/execution-service.ts": (
        "copies a run's journal usage counts into the grant auditor's evidence; Python reconciles them (C9.01)"
    ),
}


def _words(node: ast.AST) -> set[str]:
    words: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            words.add(child.id)
        elif isinstance(child, ast.Attribute):
            words.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            words.add(child.value)
    return words


def _mixes(node: ast.AST) -> bool:
    words = _words(node)
    return any(_INPUT.search(w) for w in words) and any(_CACHED.search(w) for w in words)


def mixing_sites(source: str) -> set[str]:
    """Qualified names of the functions in ``source`` that add or subtract input and cached token counts."""
    found: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = (*scope, child.name)
            arithmetic = (isinstance(child, ast.BinOp) and isinstance(child.op, (ast.Add, ast.Sub))) or (
                isinstance(child, ast.AugAssign) and isinstance(child.op, (ast.Add, ast.Sub))
            )
            if arithmetic and _mixes(child):
                found.add(".".join(inner) or "<module>")
            visit(child, inner)

    visit(ast.parse(source), ())
    return found


@cache
def _sources() -> dict[str, str]:
    return {
        path.relative_to(PYTHON_ROOT).as_posix(): path.read_text(encoding="utf-8-sig")
        for path in modules("app", "harness_core", "harness_engines", include_testing=True)
    }


@cache
def _found() -> frozenset[str]:
    return frozenset(
        f"{path}::{name}"
        for path, text in _sources().items()
        if "tokens" in text.lower()
        for name in mixing_sites(text)
    )


def test_c37_cached_and_input_tokens_are_combined_only_by_producers_and_listed_sites() -> None:
    # An edition overlay's own sites (``tests/edition_overlay.py``, ``usage_convention.json``) are listed too.
    listed = extended(SITES, "usage_convention", "sites")
    outside = sorted(site for site in _found() if not site.startswith(PRODUCERS) and site not in listed)
    assert outside == [], (
        "a second place converts between input counts with and without cached tokens; "
        f"TokenUsage.input_tokens already includes them: {outside}"
    )


@pytest.mark.parametrize("site", sorted(SITES))
def test_c37_every_listed_site_still_converts(site: str) -> None:
    assert site in _found(), f"{site} no longer mixes input and cached tokens; drop it from SITES"


@pytest.mark.parametrize("name", sorted(CALLERS))
def test_c37_each_conversion_has_its_one_reader(name: str) -> None:
    users = {path for path, text in _sources().items() if re.search(rf"\b{name}\b", text)} - {DEFINED_IN[name]}
    assert users == CALLERS[name], f"{name} is read by {sorted(users)}"


def test_c37_node_and_the_frontend_carry_usage_and_never_compute_with_it() -> None:
    roots = (
        NODE_HARNESS,
        # the harness's Mongo repositories
        REPO_ROOT / "backend" / "nodejs" / "apps" / "src" / "libs" / "repositories" / "agent-harness",
        REPO_ROOT / "frontend" / "app",
        REPO_ROOT / "frontend" / "lib",
    )
    readers = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for root in roots
        for pattern in ("*.ts", "*.tsx")
        for path in root.rglob(pattern)
        if "generated" not in path.parts
        and "node_modules" not in path.parts
        and _NODE_USAGE.search(path.read_text(encoding="utf-8", errors="replace"))
    )
    outside = [path for path in readers if path not in NODE_CARRIERS]
    assert outside == [], f"usage counts read outside Python: share vectors with it first (C-33): {outside}"
    assert sorted(NODE_CARRIERS) == readers, "a listed Node file no longer carries usage counts: drop it"


@pytest.mark.parametrize(
    ("source", "sites"),
    [
        ("def f(u):\n    return u.input_tokens - u.cache_read_tokens\n", {"f"}),
        ("def f(u):\n    return u.input_tokens + u.cache_read_tokens + u.cache_write_tokens\n", {"f"}),
        ("class A:\n    def g(self, raw):\n        self.input_tokens += raw['cacheReadInputTokens']\n", {"A.g"}),
        ("def f(u):\n    return max(0, u.prompt_tokens - cached)\n", {"f"}),
        ("x = usage.input_tokens + usage.cache_write_tokens\n", {"<module>"}),
        # Not conversions: a total, a price, a count on its own.
        ("def f(u):\n    return u.input_tokens + u.output_tokens\n", set()),
        ("def f(u, p):\n    return u.cache_read_tokens * p.cache_read_price + u.output_tokens * p.output_price\n", set()),
        ("def f(u):\n    return u.cache_read_tokens + u.cache_write_tokens\n", set()),
    ],
)
def test_c37_the_scan_tells_a_conversion_from_a_total(source: str, sites: set[str]) -> None:
    assert mixing_sites(source) == sites

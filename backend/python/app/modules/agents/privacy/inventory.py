"""Every place this code calls a model, and every prompt section, with how each
relates to an answer's label.

A model call is found statically (`scan_model_calls`): a call of `ainvoke`,
`astream`, `abatch`, `agenerate`, `complete`, `complete_structured`,
`invoke_with_structured_output_and_reflection`, `images.generate`,
`images.edit` or `get_llm_gateway().invoke`. Every file with one is listed in
`MODEL_CALL_SITES` with its class and how many calls it holds, so a new call
anywhere fails the inventory test until someone classifies it. At run time the
LangChain tripwire is the backstop for a call this list got wrong.

`SCOPED_SITES` are the calls that do not go through a request's transports and
so open a `model_call_scope` of their own; the test checks the scope is there.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from app.modules.agents.privacy.ledger import CallKind

if TYPE_CHECKING:
    from pathlib import Path


class SiteClass(StrEnum):
    # A request transport, or a wrapper around one: every call is observed by `with_ledger`.
    TRANSPORT = "transport"
    # Resolves its model from the request's transport registry, so it is observed there.
    REGISTRY = "registry"
    # Opens its own `model_call_scope` (see `SCOPED_SITES`).
    SCOPED = "scoped"
    # A model-call helper; each caller is classified where it calls it.
    HELPER = "helper"
    # Runs after the answer's label is final; reported, never labelled.
    AFTER_LABEL = "after_label"
    # Never runs inside a chat request: indexing, parsing, health, other routes, evals.
    NOT_CHAT = "not_chat"
    # The method name matches, but it is not a model call.
    NOT_MODEL = "not_model"


@dataclass(frozen=True)
class Site:
    cls: SiteClass
    calls: int
    why: str


MODEL_CALL_SITES: Final[dict[str, Site]] = {
    # The agent loop and the request's transports.
    "app/agent_loop_lib/agent/__init__.py": Site(SiteClass.REGISTRY, 1, "Agent.step, the main loop and every child"),
    "app/agent_loop_lib/models/transport.py": Site(SiteClass.REGISTRY, 2, "Model over a registry transport"),
    "app/agent_loop_lib/hooks/middleware/builtin/auto_compact.py": Site(SiteClass.REGISTRY, 1, "auto-compaction"),
    "app/agent_loop_lib/modules/pipeline/critic/plan_critic.py": Site(SiteClass.REGISTRY, 1, "critique_plan"),
    "app/agent_loop_lib/modules/pipeline/critic/result_critic.py": Site(SiteClass.REGISTRY, 1, "verify_result"),
    "app/agent_loop_lib/modules/pipeline/planner/default.py": Site(SiteClass.REGISTRY, 1, "create_plan"),
    "app/agent_loop_lib/modules/pipeline/planner/plan_ahead.py": Site(SiteClass.REGISTRY, 1, "plan ahead"),
    "app/agent_loop_lib/modules/pipeline/planner/replanner.py": Site(SiteClass.REGISTRY, 1, "replan"),
    "app/agent_loop_lib/transport/anthropic.py": Site(SiteClass.TRANSPORT, 2, "direct SDK transport"),
    "app/agent_loop_lib/transport/gemini.py": Site(SiteClass.TRANSPORT, 1, "direct SDK transport"),
    "app/agent_loop_lib/transport/opik_tracing.py": Site(SiteClass.TRANSPORT, 2, "tracing wrapper"),
    "app/agent_loop_lib/transport/registry.py": Site(SiteClass.TRANSPORT, 1, "registry helper"),
    "app/agents/agent_loop/cancellation_transport.py": Site(SiteClass.TRANSPORT, 2, "cancellation wrapper"),
    "app/agents/agent_loop/image_guard.py": Site(SiteClass.TRANSPORT, 2, "image cap wrapper"),
    "app/agents/agent_loop/langchain_transport.py": Site(SiteClass.TRANSPORT, 4, "LangChain transport"),
    "app/agents/agent_loop/output_cap_transport.py": Site(SiteClass.TRANSPORT, 2, "output cap wrapper"),
    "app/agents/agent_loop/usage_transport.py": Site(SiteClass.TRANSPORT, 2, "usage wrapper"),
    "app/modules/agents/privacy/transport.py": Site(SiteClass.TRANSPORT, 2, "the label wrapper itself"),
    # Calls made inside a chat request outside its transports.
    "app/modules/named_entities/linking/verifier.py": Site(SiteClass.SCOPED, 1, "entity verifier, inside a tool"),
    "app/utils/pattern_match.py": Site(SiteClass.SCOPED, 1, "grep generation, inside the search tool"),
    "app/utils/concept_table_search.py": Site(SiteClass.SCOPED, 1, "concept extraction, inside table discovery"),
    "app/utils/streaming.py": Site(SiteClass.HELPER, 5, "no-tools answer and structured output helpers"),
    "app/services/llm_gateway/gateway.py": Site(SiteClass.HELPER, 1, "the gateway the streaming helpers call"),
    "app/utils/aimodels.py": Site(SiteClass.HELPER, 2, "image adapters; the image tool scopes them"),
    "app/agent_loop_lib/modules/providers/skills/extractor.py": Site(
        SiteClass.AFTER_LABEL, 1, "skill learning after the run (off by default)",
    ),
    # Not part of a chat request.
    "app/agent_loop_lib/eval/rubric.py": Site(SiteClass.NOT_CHAT, 1, "evals"),
    "app/agent_loop_lib/tools/builtin/coordination/route_task.py": Site(
        SiteClass.NOT_CHAT, 1, "registered only by the control plane",
    ),
    "app/api/routes/health.py": Site(SiteClass.NOT_CHAT, 2, "health probes"),
    "app/api/routes/search.py": Site(SiteClass.NOT_CHAT, 2, "the knowledge search page"),
    "app/connectors/sources/demo/harness/answer_judge.py": Site(SiteClass.NOT_CHAT, 1, "demo data harness"),
    "app/modules/derive_relations/implicit_relations/step3_llm.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/entity_resolution/resolver.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/knowledge/concepts/dedup.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/knowledge/step0_base.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/knowledge/step1_queries.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/knowledge/step3_filter.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/knowledge/step4_extract.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/named_entities/resolution.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/modules/named_entities/strategies/agent/transport.py": Site(SiteClass.NOT_CHAT, 2, "indexing"),
    "app/modules/named_entities/strategies/single_call.py": Site(SiteClass.NOT_CHAT, 3, "indexing"),
    "app/modules/parsers/csv/csv_parser.py": Site(SiteClass.NOT_CHAT, 3, "parsing"),
    "app/modules/parsers/excel/excel_parser.py": Site(SiteClass.NOT_CHAT, 2, "parsing"),
    "app/modules/parsers/pdf/vlm_ocr_strategy.py": Site(SiteClass.NOT_CHAT, 1, "parsing"),
    "app/modules/transformers/document_extraction.py": Site(SiteClass.NOT_CHAT, 4, "indexing"),
    "app/modules/transformers/image_description.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/utils/indexing_helpers.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    "app/utils/query_decompose.py": Site(SiteClass.NOT_CHAT, 2, "not imported by any service"),
    "app/utils/record_extract.py": Site(SiteClass.NOT_CHAT, 1, "the records extract route"),
    "app/utils/table_enrichment.py": Site(SiteClass.NOT_CHAT, 1, "indexing"),
    # The Linear API client's paginator is called `complete`.
    "app/connectors/sources/linear/connector.py": Site(SiteClass.NOT_MODEL, 10, "Linear API pagination"),
    "app/connectors/sources/linear/source_reconciler.py": Site(SiteClass.NOT_MODEL, 1, "Linear API pagination"),
}


@dataclass(frozen=True)
class ScopedSite:
    site: str
    kind: CallKind
    why: str


# The calls in a chat request that bypass its transports, each opening a
# `model_call_scope(site, kind)` in the file named.
SCOPED_SITES: Final[dict[str, ScopedSite]] = {
    "app/modules/named_entities/linking/verifier.py": ScopedSite(
        "entity_verifier", CallKind.INSIDE_TOOL, "the linker names the readable records behind every candidate",
    ),
    "app/utils/pattern_match.py": ScopedSite(
        "grep_generation", CallKind.INSIDE_TOOL, "given the tool query and the asker's words only",
    ),
    "app/utils/concept_table_search.py": ScopedSite(
        "concept_extraction", CallKind.INSIDE_TOOL, "given the tool query and phrases already searched",
    ),
    "app/agents/actions/image_generator/image_generator.py": ScopedSite(
        "image_generation", CallKind.INSIDE_TOOL, "a model-written prompt; an edit's source image is read through the registry",
    ),
}


# Every section of the chat system prompt, and what puts its sources in the label.
PROMPT_SECTIONS: Final[dict[str, str]] = {
    "identity": "fixed text, or a saved agent's configuration (agents: config)",
    "agent_instructions": "a saved agent's configuration (agents: config)",
    "project_instructions": "the request names no project: incomplete (untagged_prompt_part:project_instructions)",
    "custom_instructions": "the org's own instructions: fixed text",
    "operating_rules": "fixed text",
    "response_format": "fixed text",
    "base_system_prompt": "fixed text",
    "citation_rules": "fixed text",
    "turn_citation_rules": "fixed text",
    "finding_information": "fixed text",
    "exact_numbers": "fixed text",
    "deep_research": "fixed text",
    "code_execution": "fixed text",
    "sql_workflow": "fixed text",
    "available_tools": "tool schemas: fixed text",
    "preloaded_tools": "tool schemas: fixed text",
    "code_navigation": "fixed text",
    "knowledge_sources": "chat route: connector types only; assistant: nodes; saved agent: agents (config)",
    "capability_summary": "runtime status; per-user MCP server notes make the answer personal",
    "conversation_rules": "the chat's roster",
    "user_context": "the asker's own profile",
    "sender_context": "the asker's own profile and the roster",
    "answer_confidence": "fixed text",
    "worked_traces": "fixed text",
    "skills_overview": (
        "each skill listed, by where it came from (the run's skill manager tells the ledger): a seeded "
        "builtin or one this run wrote adds nothing; one assigned to the run's agent, the agent "
        "(agents: config); any other is a person's own, so the asker (personal)"
    ),
    "preloaded_skills": "each skill named or loaded, as skills_overview",
    "current_request": "the asker's words",
    "request_interpretation": "the intent call's output, from inputs the ledger observed",
    "goal_brief": "the intent call's output, from inputs the ledger observed",
    "time_context": "fixed text",
    "request_context": "goal constraints: prefetch (records), the artifact reminder (artifact parents), notices",
    "unavailable_tools": "runtime status",
    "entity_filter": "the asker's own filter choice",
    "attachments": "the request's attachments: chat uploads, or records by id",
    "extra_sections": "the catch-all for the extra sections below",
    "selected_nodes": "the selected nodes named (nodes)",
    "period_summary": "fixed text",
}

_METHODS: Final = frozenset({"ainvoke", "astream", "abatch", "agenerate", "complete", "complete_structured"})
_HELPERS: Final = frozenset({"invoke_with_structured_output_and_reflection"})
# A file holding none of these cannot hold a model call, so it is not parsed.
_WORDS: Final = (*(f".{m}(" for m in _METHODS), *_HELPERS, "images.", "get_llm_gateway")


def _is_model_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _HELPERS
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _METHODS or func.attr in _HELPERS:
        return True
    if func.attr in ("generate", "edit") and isinstance(func.value, ast.Attribute) and func.value.attr == "images":
        return True
    return (
        func.attr == "invoke" and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name) and func.value.func.id == "get_llm_gateway"
    )


def scan_model_calls(root: Path, *, include: tuple[str, ...] = ("app",), exclude: tuple[str, ...] = ()) -> dict[str, int]:
    """`{path relative to root: number of model calls}` for every file under
    `root/include` with at least one, skipping paths that start with an `exclude` prefix."""
    found: dict[str, int] = {}
    for top in include:
        for path in sorted((root / top).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in exclude):
                continue
            text = path.read_text(encoding="utf-8-sig")
            if not any(word in text for word in _WORDS):
                continue
            tree = ast.parse(text)
            calls = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_model_call(node))
            if calls:
                found[rel] = calls
    return found


def render_report(
    sites: dict[str, Site] = MODEL_CALL_SITES, scoped: dict[str, ScopedSite] = SCOPED_SITES,
) -> str:
    """The inventory as a Markdown table."""
    lines = ["| file | class | calls | why |", "| --- | --- | --- | --- |"]
    for path, site in sorted(sites.items(), key=lambda kv: (kv[1].cls.value, kv[0])):
        lines.append(f"| `{path}` | {site.cls.value} | {site.calls} | {site.why} |")
    lines += ["", "| scoped call | file | kind | why |", "| --- | --- | --- | --- |"]
    for path, entry in sorted(scoped.items(), key=lambda kv: kv[1].site):
        lines.append(f"| `{entry.site}` | `{path}` | {entry.kind.value} | {entry.why} |")
    return "\n".join(lines)


__all__ = [
    "MODEL_CALL_SITES",
    "PROMPT_SECTIONS",
    "SCOPED_SITES",
    "ScopedSite",
    "Site",
    "SiteClass",
    "render_report",
    "scan_model_calls",
]

"""Retry/continue-iteration context builders, extracted from
`modules/agents/qna/nodes.py` (Phase 0 of the agent-loop migration).
"""


from __future__ import annotations

import json
import logging
import re

from app.modules.agents.qna.chat_state import ChatState


def _extract_missing_params_from_error(error_msg: str) -> list[str]:
    """Extract missing parameter names from validation error"""
    missing = []

    # Pattern: "page_title\n  Field required"
    pattern = r'(\w+)\s+Field required'
    matches = re.findall(pattern, error_msg, re.IGNORECASE)
    missing.extend(matches)

    # Pattern: "Field required [type=missing, input_value={...}, input_type=dict]"
    # Extract field name from context
    pattern2 = r'(\w+)\s*\n\s*Field required'
    matches2 = re.findall(pattern2, error_msg, re.IGNORECASE | re.MULTILINE)
    missing.extend(matches2)

    return list(set(missing))  # Remove duplicates


def _extract_invalid_params_from_args(args: dict, error_msg: str) -> list[str]:
    """Detect parameters that were provided but not expected"""
    # This is harder - would need to compare against schema
    # For now, just return empty
    return []


def _build_retry_context(state: ChatState) -> str:
    """Build context for retry with error details"""
    errors = state.get("execution_errors", [])
    reflection = state.get("reflection", {})
    fix_instruction = reflection.get("fix_instruction", "")

    if not errors:
        return ""

    error_summary = errors[0]
    failed_tool = error_summary.get('tool_name', 'unknown')
    failed_args = error_summary.get("args", {})
    error_msg = error_summary.get('error', 'unknown')[:500]

    # Extract missing/invalid parameters from error
    missing_params = _extract_missing_params_from_error(error_msg)
    invalid_params = _extract_invalid_params_from_args(failed_args, error_msg)

    retry_context = f"""## 🔴 RETRY MODE - PREVIOUS ATTEMPT FAILED

**Failed Tool**: {failed_tool}
**Error**: {error_msg}

**Previous Args**:
```json
{json.dumps(failed_args, indent=2)}
```

**Fix Instruction**: {fix_instruction}
"""

    # Add parameter hints if validation error
    if "validation error" in error_msg.lower() or "field required" in error_msg.lower():
        retry_context += "\n## ⚠️ PARAMETER VALIDATION ERROR\n\n"

        if missing_params:
            retry_context += f"**Missing required parameters**: {', '.join(missing_params)}\n"

        if invalid_params:
            retry_context += f"**Invalid parameters used**: {', '.join(invalid_params)}\n"

        retry_context += "\n**CHECK TOOL SCHEMA**: Look at the parameter list for this tool above.\n"
        retry_context += "**USE EXACT PARAMETER NAMES** from the schema.\n\n"

    retry_context += """
**IMPORTANT**:
- If user asked to CREATE, you MUST still use CREATE tool after fixing
- Fix the parameters and retry with corrected values
- Don't switch to different tool type
- Use EXACT parameter names from tool schema
"""

    return retry_context


def _build_continue_context(state: ChatState, log: logging.Logger) -> str:
    """
    Build the context injected into the planner prompt when re-planning after a
    partial iteration (continue_with_more_tools).

    Design principles:
    - Generic: works for any tool combination, not Jira/email specific.
    - No truncation: every tool result is emitted in full so the planner has
      complete information to chain calls and generate write content.
    - Retrieval knowledge is surfaced from state["final_results"] (the
      deduplicated merged blocks) AND from the raw tool result string so
      nothing is lost.
    - Completed write/action tools are flagged to prevent accidental repeats.
    """
    tool_results = state.get("all_tool_results", [])
    if not tool_results:
        return ""

    # ── Classify results: retrieval vs everything else ────────────────────────
    def _is_retrieval(tool_name: str) -> bool:
        name = tool_name.lower()
        return "retrieval" in name or "search_internal_knowledge" in name

    retrieval_results = [r for r in tool_results if _is_retrieval(r.get("tool_name", ""))]
    api_results       = [r for r in tool_results if not _is_retrieval(r.get("tool_name", ""))]

    parts = []

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Retrieved knowledge
    # Prefer state["final_results"] (merged/deduplicated blocks) but also
    # include the raw tool result text so nothing is omitted.
    # ══════════════════════════════════════════════════════════════════════════
    final_results = state.get("final_results", []) or []

    if retrieval_results or final_results:
        parts.append("## 📚 RETRIEVED KNOWLEDGE")
        parts.append(
            "Use this as the authoritative source when generating content for "
            "any write action (create, update, send, post, comment, etc.). "
            "Write the full content inline — do NOT summarise or reduce to bullet points."
        )
        parts.append("")

        knowledge_written = False

        # 1a. Emit every block from final_results (no limit, no truncation)
        if final_results:
            knowledge_lines = []
            for i, block in enumerate(final_results):
                text = ""
                if isinstance(block, dict):
                    text = (
                        block.get("text", "")
                        or block.get("content", "")
                        or block.get("chunk", "")
                        or ""
                    )
                    if not text and "blocks" in block:
                        # Nested block list (e.g. Confluence page structure)
                        text = "\n".join(
                            b.get("text", "") for b in block["blocks"] if isinstance(b, dict)
                        )
                text = str(text).strip()
                if text:
                    knowledge_lines.append(f"[KB-{i+1}]\n{text}")
            if knowledge_lines:
                parts.append("\n\n".join(knowledge_lines))
                knowledge_written = True

        # 1b. Always also emit the full raw retrieval result strings so
        #     nothing is lost if final_results was populated differently.
        for r in retrieval_results:
            if r.get("status") == "success":
                raw = str(r.get("result", "")).strip()
                if raw:
                    parts.append(f"\n[Raw retrieval output from {r.get('tool_name', 'retrieval')}]\n{raw}")
                    knowledge_written = True

        if not knowledge_written:
            parts.append("(No knowledge content retrieved yet.)")

        parts.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — All other tool results (full, untruncated)
    # ══════════════════════════════════════════════════════════════════════════
    if api_results:
        parts.append("## 🔧 TOOL RESULTS")
        parts.append(
            "Extract any IDs, keys, references, or values you need for the next steps "
            "directly from the results below."
        )
        parts.append("")
        for result in api_results:
            tool_name   = result.get("tool_name", "unknown")
            status      = result.get("status", "unknown")
            result_data = result.get("result", "")

            # Emit in full — no character cap
            if isinstance(result_data, dict):
                result_str = json.dumps(result_data, default=str, indent=2)
            else:
                result_str = str(result_data)

            parts.append(f"### {tool_name} ({status})\n{result_str}")
        parts.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Duplicate-prevention guard for already-completed tools
    # ══════════════════════════════════════════════════════════════════════════
    completed_tools = [
        r.get("tool_name", "unknown")
        for r in tool_results
        if r.get("status") == "success"
    ]
    if completed_tools:
        parts.append(
            "⚠️ ALREADY COMPLETED — DO NOT REPEAT: The following tools already "
            "succeeded. Planning them again will create duplicates:\n" +
            "\n".join(f"  ✅ {t}" for t in completed_tools) +
            "\nOnly plan the remaining incomplete steps."
        )
        parts.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Generic planning instructions (tool-agnostic)
    # ══════════════════════════════════════════════════════════════════════════
    parts.append("## 📋 PLANNING INSTRUCTIONS FOR THIS CYCLE")
    parts.append(
        "1. Use the TOOL RESULTS above to extract any identifiers (IDs, keys, URLs, "
        "addresses, timestamps, etc.) needed for subsequent tool calls.\n"
        "2. When a write tool needs content (email body, Jira comment, Confluence page, "
        "Slack message, etc.), write the FULL content INLINE in the tool arguments. "
        "Draw from the RETRIEVED KNOWLEDGE shown above — use it verbatim or synthesize "
        "it into well-structured prose. Do NOT summarize to bullet points or leave "
        "placeholders. The retrieved text above IS the authoritative source — use it.\n"
        "3. ⚠️ CRITICAL: Do NOT hallucinate or generate content from your own training "
        "knowledge for write actions. ONLY use content from the RETRIEVED KNOWLEDGE "
        "section above. If information is not in the retrieved knowledge, say so.\n"
        "4. Use `{{tool_name.data.field[0].subfield}}` placeholder syntax ONLY for "
        "referencing identifiers/keys (IDs, issue keys, thread IDs, etc.) from previous "
        "tool results, NEVER for content fields.\n"
        "5. Do NOT re-fetch or re-retrieve data that is already present above.\n"
        "6. Do NOT repeat any tool listed in the ALREADY COMPLETED section."
    )

    return "\n".join(parts)


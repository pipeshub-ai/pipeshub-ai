"""Tool-results context builder for the response-synthesis prompt,
extracted from `modules/agents/qna/nodes.py` (Phase 0 of the agent-loop
migration).
"""

from __future__ import annotations

import json
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.modules.agents.context.tool_result_extractor import ToolResultExtractor
from app.modules.agents.qna.reference_data import generate_field_instructions


async def _build_tool_results_context(
    tool_results: list[dict],
    final_results: list[dict],
    *,
    has_retrieval_in_context: bool = False,
    ref_mapper: object | None = None,
    config_service: ConfigurationService | None = None,
    is_multimodal_llm: bool = False,
    has_attachments: bool = False,
) -> str:
    """Build context from tool results for response generation.

    Args:
        tool_results: All tool results (success + error) from this cycle.
        final_results: Retrieval results already embedded in qna_message_content.
                       Pass [] when they are already in qna_message_content to avoid
                       duplication; use has_retrieval_in_context=True instead to signal
                       that retrieval knowledge IS present in the conversation context.
        has_retrieval_in_context: True when retrieval knowledge blocks are already in
                       the user message (qna_message_content). This tells the LLM to
                       use MODE 3 (combined citations + referenceData) even though the
                       blocks aren't repeated in this tool-results section.
        ref_mapper: CitationRefMapper for building tiny citation URLs.
    """
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    # has_retrieval is True when blocks are in final_results, already in context, or attachments are present
    has_retrieval = bool(final_results) or has_retrieval_in_context or has_attachments
    non_retrieval = [r for r in successful if "retrieval" not in r.get("tool_name", "").lower()]
    has_web_results = any(
        r.get("tool_name", "").lower() in ("web_search", "fetch_url")
        for r in non_retrieval
    )

    parts = []

    # All failed
    if failed and not successful:
        parts.append("\n## ⚠️ Tools Failed\n")
        for r in failed[:3]:
            err = str(r.get("result", "Unknown error"))[:200]
            parts.append(f"- {r.get('tool_name', 'unknown')}: {err}\n")
        parts.append("\n❌ DO NOT fabricate data. Explain error to user.\n")
        return "".join(parts)

    # Has data
    if has_retrieval:
        # When blocks come from final_results, show count. When they're already in
        # qna_message_content (has_retrieval_in_context=True), just remind the LLM.
        if final_results:
            parts.append("\n## 📚 Internal Knowledge Available\n\n")
            parts.append(f"You have {len(final_results)} knowledge blocks.\n")
        else:
            parts.append("\n## 📚 Internal Knowledge in Context\n\n")
            parts.append(
                "Internal knowledge blocks (with Citation IDs) are present "
                "in the conversation above.\n"
            )
        parts.append(
            "Cite key facts from internal knowledge using markdown links: [source](ref1). Use the EXACT Citation ID from the context. Limit to the most relevant citations — do NOT cite every sentence.\n"
            "Do NOT manually number citations — the system assigns numbers automatically.\n"
            "If unsure of the exact Citation ID, omit the citation rather than guessing.\n"
        )

    if non_retrieval:
        from app.utils.tool_handlers import ToolHandlerRegistry

        web_tool_results: list[tuple[dict, Any]] = []
        api_tool_results: list[tuple[dict, Any]] = []
        for r in non_retrieval:
            content = ToolResultExtractor.extract_data_from_result(r.get("result", ""))
            result_type = content.get("result_type", "") if isinstance(content, dict) else ""
            if result_type in ("web_search", "url_content") or (
                isinstance(content, dict) and ("web_results" in content or "blocks" in content)
            ):
                web_tool_results.append((r, content))
            else:
                api_tool_results.append((r, content))

        if web_tool_results:
            parts.append("\n## 🌐 Web Results\n\n")
            for r, content in web_tool_results:
                tool_name = r.get("tool_name", "unknown")
                handler = ToolHandlerRegistry.get_handler(content)
                formatted_blocks = await handler.format_message(
                    content, {"ref_mapper": ref_mapper, "config_service": config_service, "is_multimodal_llm": is_multimodal_llm}
                )
                parts.append(f"### {tool_name}\n")
                for block in formatted_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block["text"] + "\n\n")

            has_only_snippets = all(
                (isinstance(c, dict) and c.get("result_type") == "web_search")
                for _, c in web_tool_results
            )
            if has_only_snippets:
                parts.append(
                    "\n**⚠️ IMPORTANT — fetch_url tool available:**\n"
                    "The web results above are **search snippets only**. "
                    "You MUST call the `fetch_url` tool on the most relevant URL(s) to retrieve "
                    "the full page content before answering.\n\n"
                )

        if api_tool_results:
            parts.append("\n## 🔧 API Tool Results\n\n")
            parts.append(
                "Transform raw data into professional, informative markdown. Follow these rules:\n"
                "- **Be specific**: Show exact values (dates, times, names, emails, statuses) — never summarize vaguely.\n"
                "- **Include links**: Extract ALL URL fields from the data and render as clickable markdown links.\n"
                "- **People fields**: Show names WITH email addresses when available: `Name (email@example.com)`.\n"
                "- **For single items**: Show all relevant fields as detailed field-value pairs.\n"
                "- **For lists**: Use clean, scannable markdown tables with the most important fields. "
                "Prioritize user-actionable, business-relevant data. Include custom fields that have values.\n"
                "- **Exclude**: Internal system metadata, aggregate calculations, empty/null fields, technical IDs "
                "that aren't user-facing. Show user-facing IDs/keys, hide internal ones.\n"
                "- Store all IDs, keys, and links in referenceData.\n\n"
            )

            for r, content in api_tool_results:
                tool_name = r.get('tool_name', 'unknown')

                if isinstance(content, (dict, list)):
                    content_str = json.dumps(content, indent=2, default=str)
                else:
                    content_str = str(content)

                parts.append(f"### {tool_name}\n")
                parts.append(f"```json\n{content_str}\n```\n\n")



    parts.append("\n---\n## 📝 RESPONSE INSTRUCTIONS\n\n")

    if has_retrieval and non_retrieval:
        parts.append(
            "**⚠️ MODE 3 — COMBINED RESPONSE (MANDATORY)**\n"
            "You have BOTH internal knowledge blocks (with Citation IDs) AND API tool results.\n"
            "This is the MOST ACCURATE mode — you have both indexed historical content AND live current data.\n"
            "You MUST:\n"
            "  1. Synthesize BOTH sources into ONE coherent, comprehensive answer\n"
            "  2. Use retrieval results for historical context, background, and comprehensive coverage\n"
            "  3. Use API results for current state, real-time data, and exact IDs/keys\n"
            "  4. When sources conflict, prioritize API results for current state, but mention historical context from retrieval\n"
            "  5. Cite key facts from internal knowledge using markdown links: [source](ref1). Limit to the most relevant citations — do NOT cite every sentence.\n"
        )
        if has_web_results:
            parts.append(
                "  6. Cite web search results using the url/citation id.\n"
                "  7. Format all API items as clickable links and include them in `referenceData`\n"
            )
        else:
            parts.append(
                "  6. Format all API items as clickable links and include them in `referenceData`\n"
            )
    elif has_retrieval:
        parts.append(
            "**INTERNAL KNOWLEDGE**: Use knowledge blocks with inline citations [source](ref1). The system assigns citation numbers automatically.\n"
        )
    elif has_web_results:
        parts.append(
            "**WEB SEARCH DATA**: Cite web search results using the url/citation id.\n"
            "Use EXACTLY the URL/citation id from the tool results.\n"
        )
    else:
        parts.append(
            "**API DATA**: Transform into professional markdown. "
            "Show user-facing IDs (keys), hide internal IDs.\n"
        )

    if len(non_retrieval) > 1:
        parts.append(
            "\n**IMPORTANT**: You have results from MULTIPLE tools. "
            "Merge and present results from ALL tools — do NOT ignore any tool's output. "
            "Deduplicate overlapping items but ensure every unique result is included.\n"
        )

    parts.append(
        "\n## 🔗 LINK REQUIREMENTS (MANDATORY)\n\n"
        "For EVERY item from ANY external service, you MUST include a clickable markdown link.\n"
        "**How to find links**: Scan ALL fields in the raw JSON for values starting with `http://` or `https://`. "
        "Common URL field names: `url`, `webLink`, `webViewLink`, `self`, `htmlUrl`, `permalink`, "
        "`link`, `href`, `joinUrl`, `joinWebUrl`, `webUrl` — but ANY field containing a URL should be used.\n"
        "**Format**: `[Item Title or Name](url_value)` — always use the item's title/name/subject/key as the link text.\n"
        "**If no URL found**: Still mention the item by name/key/ID so the user can locate it.\n\n"
        "## referenceData (MANDATORY for ALL items)\n\n"
        "For EVERY entity (site, file, notebook, page, issue, project, channel, etc.), include an entry in `referenceData` "
        "with all applicable fields below (top-level vs nested in `metadata`):\n\n"
        f"{generate_field_instructions()}\n\n"
    )

    # The JSON schema returned depends on what sources are present
    if has_retrieval and non_retrieval:
        parts.append(
            "Return ONLY JSON matching MODE 3:\n"
            "{\"answer\": \"...with inline [source](/record/abc/preview#blockIndex=0)[source](/record/def/preview#blockIndex=3) citations...\", "
            "\"confidence\": \"<Very High | High | Medium | Low>\", "
            "\"answerMatchType\": \"Derived From Blocks\", "
            "\"referenceData\": [{\"name\": \"...\", \"id\": \"...\", \"type\": \"...\", \"app\": \"...\", "
            "\"webUrl\": \"...\", \"metadata\": {...}}]}\n"
        )
    elif has_retrieval:
        parts.append(
            "Return ONLY JSON:\n"
            "{\"answer\": \"...with inline [source](/record/abc/preview#blockIndex=0)[source](/record/def/preview#blockIndex=3) citations...\", "
            "\"confidence\": \"<Very High | High | Medium | Low>\", "
            "\"answerMatchType\": \"Derived From Blocks\", "
        )
    else:
        parts.append(
            "Return ONLY JSON:\n"
            "{\"answer\": \"...\", \"confidence\": \"<Very High | High | Medium | Low>\", "
            "\"answerMatchType\": \"Derived From Tool Execution\", "
            "\"referenceData\": [{\"name\": \"...\", \"id\": \"...\", \"type\": \"...\", \"app\": \"...\", "
            "\"webUrl\": \"...\", \"metadata\": {...}}]}\n"
        )

    return "".join(parts)

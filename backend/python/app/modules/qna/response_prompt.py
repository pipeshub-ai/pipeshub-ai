"""
Response Synthesis Prompt System — OPTION B IMPLEMENTATION

Uses get_message_content() - the EXACT same function the chatbot uses - to format
blocks with R-markers. This ensures identical formatting and block numbering between
chatbot and agent.

Flow:
1. Multiple parallel retrieval calls return raw results (no formatting)
2. Results are merged and deduplicated in nodes.py (merge_and_number_retrieval_results)
3. get_message_content() formats blocks and assigns block numbers (same as chatbot)
4. Block numbers are synced back to results for citation processing
5. Formatted content is included in the system prompt

This approach ensures the agent sees the exact same block format as the chatbot.
"""

from datetime import datetime
from typing import Any, Dict, List, Tuple

# Constants
CONTENT_PREVIEW_LENGTH = 250
CONVERSATION_PREVIEW_LENGTH = 300

# ============================================================================
# RESPONSE SYNTHESIS SYSTEM PROMPT
# ============================================================================

response_system_prompt = """You are an expert AI assistant within an enterprise who can answer any query based on the company's knowledge sources, user information, and tool execution results.

<core_role>
You are responsible for:
- **Synthesizing** data from internal knowledge blocks and tool execution results into coherent, comprehensive answers
- **Formatting** responses professionally with proper Markdown
- **Citing** internal knowledge sources accurately with inline citations [R1-0][R2-3]
- **Presenting** information in a user-friendly, scannable format
- **Answering directly** without describing your process or tools used
</core_role>

<internal_knowledge_context>
{internal_context}
</internal_knowledge_context>

<user_context>
{user_context}
</user_context>

<conversation_history>
{conversation_history}

**IMPORTANT**: Use this conversation history to:
1. Understand follow-up questions and maintain context across turns
2. Reference previous information instead of re-retrieving
3. Build upon previous responses naturally
4. Remember IDs and values mentioned in previous turns (page IDs, project keys, etc.)
</conversation_history>

<answer_guidelines>
## Answer Comprehensiveness (CRITICAL)

1. **Provide Detailed Answers**:
   - Give thoughtful, explanatory, sufficiently detailed answers — not just short factual replies
   - Include every key point that addresses the query directly
   - Do not summarize or omit important details
   - Make responses self-contained and complete
   - For multi-part questions, address each part with equal attention

2. **Rich Markdown Formatting**:
   - Generate answers in fully valid markdown format with proper headings
   - Use tables, lists, bold, italic, sub-sections as appropriate
   - Ensure citations don't break the markdown format

3. **Source Integration**:
   - For user-specific queries (identity, role, workplace), use the User Information section
   - Integrate user information with knowledge blocks when relevant
   - Prioritize internal knowledge sources when available
   - Combine multiple sources coherently when beneficial

4. **Multi-Query Handling**:
   - Identify and address each distinct question in the user's query
   - Ensure all questions receive equal attention with proper citations
   - For questions that cannot be answered: explain what is missing, don't skip them

5. **Relevance Check (IMPORTANT)**:
   - **ONLY reference blocks that are directly relevant to the user's query.**
   - The retrieval system may return documents from different topics — verify each block is actually about what the user asked before citing it.
   - If a block appears off-topic (e.g., a security policy when the user asked about product editions), skip it.
   - Do NOT use a block simply because it was returned; use it only if its content directly supports the answer.
</answer_guidelines>

<citation_rules>
## Citation Guidelines (CRITICAL - MANDATORY)

**⚠️ Every factual claim from internal knowledge MUST be cited immediately after the claim.**

### Citation Format Rules:

1. **Use EXACT Block Numbers**: Each knowledge block has a label like R1-0, R1-2, R2-3.
   Use these EXACT labels in square brackets as citations.
   - ✅ CORRECT: [R1-0], [R2-3]
   - ❌ WRONG: [ceb988e7-c37c-4a5a-b8ef-59f37bbde594] (never use UUIDs)
   - ❌ WRONG: [R?-?] (never use placeholder markers)

2. **Inline After Each Claim**: Put [R1-0] IMMEDIATELY after the specific fact it supports
   - ✅ "Revenue grew 29% [R1-0]. The company has 500 employees [R2-3]."
   - ❌ "Revenue grew 29%. The company has 500 employees. [R1-0][R2-3]"

3. **One Citation Per Bracket**: [R1-0][R2-3] NOT [R1-0, R2-3]

4. **DIFFERENT Citations for DIFFERENT Facts**: Each block covers specific content.
   Cite the SPECIFIC block that contains each fact.
   - ✅ "Governance primitives are needed [R1-0]. Coherence maintenance is a gap [R1-2]. Retention boundaries define storage [R1-4]."
   - ❌ "Governance, coherence, and retention are gaps [R1-0][R1-0][R1-0]."

5. **Top 4-5 Most Relevant**: Don't cite every block — use the most relevant ones

6. **Code Block Citations**: Put citations on the NEXT line after ```, never on the same line

7. **Include blockNumbers Array**: List ALL cited block numbers as strings
   - ✅ "blockNumbers": ["R1-0", "R1-2", "R2-3"]

8. **MANDATORY**: Every fact from internal knowledge MUST have a citation. No exceptions.
</citation_rules>

<output_format_rules>
## Output Format (CRITICAL)

### MODE 1: Structured JSON with Citations (When Internal Knowledge is Available)

**When to use:** ALWAYS when internal knowledge sources are in the context

```json
{{
  "answer": "Your answer in markdown with citations [R1-0][R2-3] after each fact.",
  "reason": "How you derived the answer from blocks",
  "confidence": "Very High | High | Medium | Low",
  "answerMatchType": "Exact Match | Derived From Blocks | Derived From User Info | Enhanced With Full Record",
  "blockNumbers": ["R1-0", "R1-2", "R2-3"]
}}
```

### MODE 2: Structured JSON for Tool Results (When NO Internal Knowledge)

**When to use:** Only external tools used, no internal document citations needed

```json
{{
  "answer": "# Title\\n\\nUser-friendly markdown content.",
  "confidence": "High",
  "answerMatchType": "Derived From Tool Execution",
  "referenceData": [
    {{"name": "PA-123: Fix login bug", "key": "PA-123", "type": "jira_issue", "url": "https://org.atlassian.net/browse/PA-123"}},
    {{"name": "API Docs", "id": "12345", "type": "confluence_page", "url": "https://org.atlassian.net/wiki/spaces/ENG/pages/12345"}},
    {{"name": "Design Brief.pdf", "id": "1abc", "type": "drive_file", "url": "https://drive.google.com/file/d/1abc/view"}}
  ]
}}
```

### MODE 3: Combined — Internal Knowledge + API Tool Results (MANDATORY when BOTH are present)

**When to use:** When you have BOTH internal knowledge blocks (with R-labels) AND API tool results

```json
{{
  "answer": "Your comprehensive answer weaving both sources. Cite internal knowledge facts inline [R1-0][R2-3]. Format API results with clickable links like [PA-123](url).",
  "confidence": "High",
  "answerMatchType": "Derived From Blocks",
  "blockNumbers": ["R1-0", "R2-3"],
  "referenceData": [
    {{"name": "PA-123: Fix login bug", "key": "PA-123", "type": "jira_issue", "url": "https://org.atlassian.net/browse/PA-123"}}
  ]
}}
```

**⚠️ CRITICAL — MODE 3 Rules:**
- `blockNumbers` MUST contain every R-label you cited in the answer
- `referenceData` MUST contain every external-service item (Jira, Confluence, Drive, Gmail, Slack)
- Do NOT omit knowledge citations just because API results are also present — cite BOTH
- Synthesise both sources into ONE coherent answer; do not produce two separate sections

**Tool Results — Show vs Hide:**
- ✅ SHOW: Jira ticket keys (PA-123), project keys, names, statuses, dates
- ❌ HIDE: Internal numeric IDs, UUIDs, database hashes

**🔗 Links — MANDATORY for External Service Items:**
- **Jira issue**: Always format as a clickable link `[KEY-123](url)` using the `url` field.
  If no URL is present, write `KEY-123` so the user can search for it.
- **Confluence page/space**: Format as `[Page Title](url)` using the `url` field.
- **Google Drive file**: Format as `[filename](url)` using the `webViewLink` / `url` field.
- **Gmail message**: Format as `[subject](url)` using the Gmail URL.
- **Slack channel/message**: Include `#channel-name` and link if a URL is available.
- Include every item's `url` in the `referenceData` array so the frontend can render it.
</output_format_rules>

<tool_output_transformation>
## Tool Output Transformation
1. **NEVER return raw tool output** — parse and extract meaningful info
2. **Transform Professionally** — clean hierarchy, scannable structure
3. **Hide Technical Details** — show meaningful names, not internal IDs
</tool_output_transformation>

<source_prioritization>
## Source Priority Rules
1. **User-Specific Questions**: Use User Information, no citations needed
2. **Company Knowledge Questions**: Use internal knowledge blocks, cite all relevant blocks with [R1-0] inline
3. **Tool/API Data Questions**: Use tool results only, format professionally, include referenceData, no block citations needed
4. **Combined Sources (MANDATORY MODE 3)**: When BOTH internal knowledge AND API results are present:
   - Cite ALL relevant internal knowledge facts with inline [R1-0] citations AND include `blockNumbers`
   - Format ALL API results with links AND include them in `referenceData`
   - Weave both into one unified, coherent answer — do NOT skip citations just because API results exist
</source_prioritization>

<critical_reminders>
**MOST CRITICAL RULES:**

1. **ANSWER DIRECTLY** — No "I searched for X" or "The tool returned Y"
2. **CITE AFTER EACH CLAIM** — [R1-0] right after the fact it supports
3. **DIFFERENT CITATIONS FOR DIFFERENT FACTS** — don't repeat same citation
4. **BE COMPREHENSIVE** — thorough, complete answers
5. **Format Professionally** — clean markdown hierarchy
6. **INCLUDE LINKS**
</critical_reminders>

***Your entire response/output is going to consist of a single JSON, and you will NOT wrap it within JSON md markers***
"""


# ============================================================================
# CONTEXT BUILDERS
# ============================================================================

def build_internal_context_for_response(
    final_results,
    virtual_record_id_to_result=None,
    include_full_content=True,
) -> str:
    """
    Build internal knowledge context formatted for response synthesis.

    This is the agent's clean context format — it does NOT embed the chatbot's
    qna_prompt_instructions_1 / qna_prompt_instructions_2 instruction wrappers.
    Those wrappers create duplicate / conflicting instructions when embedded
    inside the agent's system prompt (which already has its own tool usage and
    citation rules in response_system_prompt).

    What this function provides:
    - context_metadata per record (same rich format as get_message_content):
        File: X | Type: Y | URL: Z  — lets the LLM distinguish documents
    - Block numbers (R1-0, R2-3 …) consistent with build_record_label_mapping
      and _sync_block_numbers_from_get_message_content
    - Block content

    Block numbers must be pre-assigned on each result dict via
    _sync_block_numbers_from_get_message_content() before this function is
    called.  That is done by build_response_prompt() below.
    """
    if not final_results:
        return "No internal knowledge sources available.\n\nOutput Format: Use Clean Professional Markdown"

    from app.models.blocks import BlockType, GroupType

    # ── Pre-scan: identify records that have ONLY image blocks ──────────────────
    # For such records (e.g. JPEG files) we will emit a synthetic summary block so
    # the LLM has a citable block number instead of seeing an empty block section.
    _vid_non_image_count: dict = {}  # virtual_record_id -> count of non-image blocks
    _vid_first_block: dict = {}       # virtual_record_id -> first result dict (for block_number)
    _vid_summary: dict = {}           # virtual_record_id -> summary text to use as fallback

    for _r in final_results:
        _vid = _r.get("virtual_record_id") or _r.get("metadata", {}).get("virtualRecordId")
        if not _vid:
            continue
        if _vid not in _vid_non_image_count:
            _vid_non_image_count[_vid] = 0
            _vid_first_block[_vid] = _r
        if _r.get("block_type") != BlockType.IMAGE.value:
            _vid_non_image_count[_vid] += 1

    # For image-only records, extract summary text from semantic_metadata
    if virtual_record_id_to_result:
        for _vid, _count in _vid_non_image_count.items():
            if _count == 0:
                _rec = virtual_record_id_to_result.get(_vid)
                if _rec:
                    _sm = _rec.get("semantic_metadata")
                    # semantic_metadata can be a dict OR a SemanticMetadata dataclass object
                    if hasattr(_sm, "summary"):
                        _summary = getattr(_sm, "summary", "") or ""
                    elif isinstance(_sm, dict):
                        _summary = _sm.get("summary", "") or ""
                    else:
                        _summary = ""
                    _vid_summary[_vid] = _summary or _rec.get("record_name", "")

    # ────────────────────────────────────────────────────────────────────────────

    context_parts = [
        "<context>",
        "## Internal Knowledge Sources Available",
        "",
        "⚠️ **CRITICAL**: You MUST respond in Structured JSON with citations.",
        "Use the EXACT Block Numbers shown below (e.g., [R1-0], [R2-3]) as citations.",
        "",
    ]

    seen_virtual_record_ids: set = set()
    seen_blocks: set = set()
    # fallback_record_number is ONLY used when block_number is NOT pre-assigned
    fallback_record_number = 0
    # Track whether we have emitted at least one visible block for the current record
    current_record_had_visible_block = False
    current_record_virtual_id = None

    for result in final_results:
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            metadata = result.get("metadata", {})
            virtual_record_id = metadata.get("virtualRecordId")

        if not virtual_record_id:
            continue

        if virtual_record_id not in seen_virtual_record_ids:
            # ── Close previous record ────────────────────────────────────────
            if seen_virtual_record_ids:
                # If the previous record had no visible blocks (image-only),
                # emit a synthetic summary block so the LLM has something to cite.
                if not current_record_had_visible_block and current_record_virtual_id:
                    _syn_num = _vid_first_block.get(current_record_virtual_id, {}).get("block_number")
                    if not _syn_num:
                        _syn_num = f"R{fallback_record_number}-0"
                    _syn_summary = _vid_summary.get(current_record_virtual_id, "")
                    if _syn_summary:
                        context_parts.append(f"* Block Number: {_syn_num}")
                        context_parts.append("* Block Type: summary")
                        context_parts.append(f"* Block Content: {_syn_summary}")
                        context_parts.append("")
                context_parts.append("</record>")

            seen_virtual_record_ids.add(virtual_record_id)
            fallback_record_number += 1
            current_record_virtual_id = virtual_record_id
            current_record_had_visible_block = False

            record = None
            if virtual_record_id_to_result and virtual_record_id in virtual_record_id_to_result:
                record = virtual_record_id_to_result[virtual_record_id]

            metadata = result.get("metadata", {})

            context_parts.append("<record>")

            # ── context_metadata (rich format: "File: X | Type: Y | URL: Z") ──
            # This is the SAME metadata the chatbot shows via qna_prompt_context.
            # It tells the LLM exactly what document the blocks come from so it
            # can correctly decide which record to cite or fetch_full_record.
            context_metadata = ""
            if record:
                context_metadata = record.get("context_metadata", "")
            if context_metadata:
                context_parts.append(context_metadata)
            else:
                # Fallback: at least show record name so the LLM isn't blind
                record_name = (
                    (record.get("record_name") if record else None)
                    or metadata.get("recordName")
                    or metadata.get("origin")
                    or "Unknown"
                )
                context_parts.append(f"File: {record_name}")

            context_parts.append("Record blocks (sorted):")

        result_id = f"{virtual_record_id}_{result.get('block_index', 0)}"
        if result_id in seen_blocks:
            continue
        seen_blocks.add(result_id)

        block_type = result.get("block_type")
        block_index = result.get("block_index", 0)

        # Use pre-assigned block_number (set by _sync_block_numbers_from_get_message_content).
        # Fall back to a computed value only when not present.
        block_number = result.get("block_number")
        if not block_number:
            block_number = f"R{fallback_record_number}-{block_index}"
            result["block_number"] = block_number

        content = result.get("content", "")

        if block_type == BlockType.IMAGE.value:
            continue

        current_record_had_visible_block = True

        if block_type == GroupType.TABLE.value:
            table_summary, child_results = result.get("content", ("", []))
            context_parts.append(f"* Block Group Number: {block_number}")
            context_parts.append("* Block Group Type: table")
            context_parts.append(f"* Table Summary: {table_summary}")
            context_parts.append("* Table Rows/Blocks:")
            if isinstance(child_results, list):
                for child in child_results[:5]:
                    child_block_number = child.get("block_number")
                    if not child_block_number:
                        child_block_number = f"R{fallback_record_number}-{child.get('block_index', 0)}"
                        child["block_number"] = child_block_number
                    context_parts.append(f"  - Block Number: {child_block_number}")
                    context_parts.append(f"  - Block Content: {child.get('content', '')}")
        else:
            context_parts.append(f"* Block Number: {block_number}")
            context_parts.append(f"* Block Type: {block_type}")
            context_parts.append(f"* Block Content: {content}")

        context_parts.append("")

    # ── Close the last record ────────────────────────────────────────────────
    if seen_virtual_record_ids:
        if not current_record_had_visible_block and current_record_virtual_id:
            _syn_num = _vid_first_block.get(current_record_virtual_id, {}).get("block_number")
            if not _syn_num:
                _syn_num = f"R{fallback_record_number}-0"
            _syn_summary = _vid_summary.get(current_record_virtual_id, "")
            if _syn_summary:
                context_parts.append(f"* Block Number: {_syn_num}")
                context_parts.append("* Block Type: summary")
                context_parts.append(f"* Block Content: {_syn_summary}")
                context_parts.append("")
        context_parts.append("</record>")

    context_parts.append("</context>")

    return "\n".join(context_parts)


def build_conversation_history_context(previous_conversations, max_history=5) -> str:
    """Build conversation history for context"""
    if not previous_conversations:
        return "This is the start of our conversation."

    recent = previous_conversations[-max_history:]
    history_parts = ["## Recent Conversation History\n"]
    for idx, conv in enumerate(recent, 1):
        role = conv.get("role")
        content = conv.get("content", "")
        if role == "user_query":
            history_parts.append(f"\nUser (Turn {idx}): {content}")
        elif role == "bot_response":
            abbreviated = content[:CONVERSATION_PREVIEW_LENGTH] + "..." if len(content) > CONVERSATION_PREVIEW_LENGTH else content
            history_parts.append(f"Assistant (Turn {idx}): {abbreviated}")
    history_parts.append("\nUse this history to understand context and handle follow-up questions naturally.")
    return "\n".join(history_parts)


def _sync_block_numbers_from_get_message_content(final_results: List[Dict[str, Any]]) -> None:
    """
    Sync block_number on each result to match what get_message_content() assigned.

    get_message_content() assigns block numbers internally as it formats blocks.
    This function replicates that numbering logic to ensure result["block_number"]
    matches the R-markers in the formatted text.

    Logic (from chat_helpers.py get_message_content()):
        seen_virtual_record_ids = set()
        record_number = 1
        for i, result in enumerate(flattened_results):
            virtual_record_id = result.get("virtual_record_id")
            if virtual_record_id not in seen_virtual_record_ids:
                if i > 0:
                    record_number += 1
                seen_virtual_record_ids.add(virtual_record_id)
            block_number = f"R{record_number}-{block_index}"
    """
    seen_virtual_record_ids = set()
    record_number = 1

    for i, result in enumerate(final_results):
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            virtual_record_id = result.get("metadata", {}).get("virtualRecordId")

        if virtual_record_id and virtual_record_id not in seen_virtual_record_ids:
            if i > 0:
                record_number += 1
            seen_virtual_record_ids.add(virtual_record_id)

        block_index = result.get("block_index", 0)
        result["block_number"] = f"R{record_number}-{block_index}"
        BLOCK_GROUP_CONTENT_LENGTH = 2
        # Also sync child results in table blocks
        from app.models.blocks import GroupType
        block_type = result.get("block_type", "")
        if block_type == GroupType.TABLE.value:
            content = result.get("content", ("", []))
            if isinstance(content, tuple) and len(content) == BLOCK_GROUP_CONTENT_LENGTH:
                table_summary, child_results = content
                if isinstance(child_results, list):
                    for child in child_results:
                        child_block_index = child.get("block_index", 0)
                        child["block_number"] = f"R{record_number}-{child_block_index}"


def build_record_label_mapping(final_results: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a mapping from R-labels (e.g. "R1", "R2") to actual virtual_record_ids.

    The numbering mirrors the logic in get_message_content() so labels are consistent
    with what the LLM sees in the context blocks.

    Returns:
        {"R1": "uuid-for-first-record", "R2": "uuid-for-second-record", ...}
    """
    label_to_vid: Dict[str, str] = {}
    seen: set = set()
    record_number = 1

    for i, result in enumerate(final_results):
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            virtual_record_id = result.get("metadata", {}).get("virtualRecordId")

        if virtual_record_id and virtual_record_id not in seen:
            if i > 0:
                record_number += 1
            seen.add(virtual_record_id)
            label_to_vid[f"R{record_number}"] = virtual_record_id

    return label_to_vid


def build_user_context(user_info, org_info) -> str:
    """Build user context for personalization"""
    if not user_info or not org_info:
        return "No user context available."

    parts = ["## User Information\n"]
    parts.append("**IMPORTANT**: Use your judgment to determine when this information is relevant.\n")
    if user_info.get("userEmail"):
        parts.append(f"- **User Email**: {user_info['userEmail']}")
    if user_info.get("fullName"):
        parts.append(f"- **Name**: {user_info['fullName']}")
    if user_info.get("designation"):
        parts.append(f"- **Role**: {user_info['designation']}")
    if org_info.get("name"):
        parts.append(f"- **Organization**: {org_info['name']}")
    if org_info.get("accountType"):
        parts.append(f"- **Account Type**: {org_info['accountType']}")
    return "\n".join(parts)


# ============================================================================
# RESPONSE PROMPT BUILDER
# ============================================================================

def build_response_prompt(state, max_iterations=30) -> str:
    """Build the response synthesis system prompt.

    Internal knowledge context is NO LONGER embedded here.  It is placed in the
    user message by respond_node via get_message_content() — the exact same
    function the chatbot uses.  This eliminates the duplicate / conflicting
    instructions that the old approach (build_internal_context_for_response in
    the system prompt) caused and aligns agent behaviour with the chatbot.
    """
    current_datetime = datetime.utcnow().isoformat() + "Z"

    final_results = state.get("final_results", [])

    # Brief status line for the system prompt so the LLM knows whether knowledge
    # is available without duplicating the full context.
    if state.get("qna_message_content"):
        internal_context = (
            "Internal knowledge (records, block numbers, and content) has been "
            "retrieved and is provided in the user message using the standard "
            "R-label format (e.g. R1-0, R2-3). "
            "Cite facts using these EXACT block numbers immediately after each claim."
        )
    elif final_results:
        internal_context = (
            f"{len(final_results)} knowledge blocks are available. "
            "Cite each fact with its EXACT block number [R1-0][R2-3] immediately after the claim."
        )
    else:
        internal_context = (
            "No internal knowledge sources available for this query. "
            "Use tool results or user context to answer, or explain that information is unavailable."
        )

    user_context = ""
    if state.get("user_info") and state.get("org_info"):
        user_context = build_user_context(state["user_info"], state["org_info"])
    else:
        user_context = "No user context available."

    conversation_history = build_conversation_history_context(
        state.get("previous_conversations", [])
    )

    base_prompt = state.get("system_prompt", "")
    instructions = state.get("instructions", "")

    # Use provided current_time/timezone if available, else fall back to server UTC
    provided_current_time = state.get("current_time")
    provided_timezone = state.get("timezone")
    if provided_current_time:
        current_datetime = provided_current_time
    # current_datetime already set above as fallback

    complete_prompt = response_system_prompt
    complete_prompt = complete_prompt.replace("{internal_context}", internal_context)
    complete_prompt = complete_prompt.replace("{user_context}", user_context)
    complete_prompt = complete_prompt.replace("{conversation_history}", conversation_history)
    complete_prompt = complete_prompt.replace("{current_datetime}", current_datetime)

    # Add timezone context if provided
    if provided_timezone:
        complete_prompt += f"\n\n**User Timezone**: {provided_timezone}"

    if base_prompt and base_prompt not in ["You are an enterprise questions answering expert", ""]:
        complete_prompt = f"{base_prompt}\n\n{complete_prompt}"

    if instructions and instructions.strip():
        complete_prompt = f"## Agent Instructions\n{instructions.strip()}\n\n{complete_prompt}"

    return complete_prompt


def create_response_messages(state) -> List[Any]:
    """
    Create messages for response synthesis.

    FIX: Reduced citation instruction duplication in the user query suffix.
    The system prompt already has complete rules — no need for 20 more lines here.
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
    )

    messages = []

    # 1. System prompt
    system_prompt = build_response_prompt(state)
    messages.append(SystemMessage(content=system_prompt))

    # 2. Conversation history
    previous_conversations = state.get("previous_conversations", [])

    from app.modules.agents.qna.conversation_memory import ConversationMemory
    memory = ConversationMemory.extract_tool_context_from_history(previous_conversations)
    state["conversation_memory"] = memory

    all_reference_data = []
    for conv in previous_conversations:
        role = conv.get("role")
        content = conv.get("content", "")
        if role == "user_query":
            messages.append(HumanMessage(content=content))
        elif role == "bot_response":
            messages.append(AIMessage(content=content))
            ref_data = conv.get("referenceData", [])
            if ref_data:
                all_reference_data.extend(ref_data)

    if all_reference_data:
        ref_data_text = _format_reference_data_for_response(all_reference_data)
        if messages and isinstance(messages[-1], AIMessage):
            messages[-1].content = messages[-1].content + "\n\n" + ref_data_text

    # 3. Current user message
    #
    # PREFERRED PATH: respond_node pre-built the user message using get_message_content()
    # — the exact same function the chatbot uses.  This produces consistent R-label block
    # numbers, rich context_metadata per record, the standard tool instructions, and the
    # correct JSON output-format instructions.  Use it directly as the HumanMessage.
    #
    # FALLBACK PATH: no retrieval results (pure API-tool query or direct answer) — use
    # the bare query with a short JSON reminder appended.
    qna_message_content = state.get("qna_message_content")

    current_query = state["query"]

    if ConversationMemory.should_reuse_tool_results(current_query, previous_conversations):
        enriched_query = ConversationMemory.enrich_query_with_context(current_query, previous_conversations)
        current_query = enriched_query
        state["is_contextual_followup"] = True
    else:
        state["is_contextual_followup"] = False

    if qna_message_content:
        # get_message_content() output already contains the query (via qna_prompt_instructions_1),
        # all record context, block numbers, and the JSON output-format spec.
        # Use it directly — no extra reminder needed.
        messages.append(HumanMessage(content=qna_message_content))
    else:
        # Fallback: plain query + brief JSON reminder for non-retrieval responses
        query_with_context = current_query

        has_knowledge = bool(state.get("final_results"))
        has_knowledge_tool = False
        if state.get("all_tool_results"):
            for tool_result in state["all_tool_results"]:
                if tool_result.get("tool_name") == "internal_knowledge_retrieval":
                    has_knowledge_tool = True
                    break

        if has_knowledge or has_knowledge_tool:
            query_with_context += (
                "\n\n**⚠️ Respond in JSON format. Cite each fact with its EXACT block number "
                "[R1-0][R2-3] immediately after the claim. Use DIFFERENT block numbers for "
                "DIFFERENT facts. Include blockNumbers array.**"
            )

        messages.append(HumanMessage(content=query_with_context))

    return messages


def _format_reference_data_for_response(all_reference_data: List[Dict]) -> str:
    """Format reference data for inclusion in response messages"""
    if not all_reference_data:
        return ""

    result = "## Reference Data (from previous responses):\n"
    spaces = [item for item in all_reference_data if item.get("type") == "confluence_space"]
    projects = [item for item in all_reference_data if item.get("type") == "jira_project"]
    issues = [item for item in all_reference_data if item.get("type") == "jira_issue"]
    pages = [item for item in all_reference_data if item.get("type") == "confluence_page"]
    max_items = 10

    if spaces:
        result += "**Confluence Spaces**: " + ", ".join([f"{i.get('name','?')} (id={i.get('id','?')})" for i in spaces[:max_items]]) + "\n"
    if projects:
        result += "**Jira Projects**: " + ", ".join([f"{i.get('name','?')} (key={i.get('key','?')})" for i in projects[:max_items]]) + "\n"
    if issues:
        result += "**Jira Issues**: " + ", ".join([f"{i.get('key','?')}" for i in issues[:max_items]]) + "\n"
    if pages:
        result += "**Confluence Pages**: " + ", ".join([f"{i.get('title','?')} (id={i.get('id','?')})" for i in pages[:max_items]]) + "\n"
    return result


# ============================================================================
# RESPONSE MODE DETECTION
# ============================================================================

def detect_response_mode(response_content) -> Tuple[str, Any]:
    """Detect if response is structured JSON or conversational"""
    if isinstance(response_content, dict):
        if "answer" in response_content and ("chunkIndexes" in response_content or "citations" in response_content or "blockNumbers" in response_content):
            return "structured", response_content
        return "conversational", response_content

    if not isinstance(response_content, str):
        return "conversational", str(response_content)

    content = response_content.strip()

    if "```json" in content or (content.startswith("```") and "```" in content[3:]):
        try:
            from app.utils.streaming import extract_json_from_string
            parsed = extract_json_from_string(content)
            if isinstance(parsed, dict) and "answer" in parsed:
                return "structured", parsed
        except (ValueError, Exception):
            pass

    if content.startswith('{') and content.endswith('}'):
        try:
            import json

            from app.utils.citations import fix_json_string
            cleaned_content = fix_json_string(content)
            parsed = json.loads(cleaned_content)
            if "answer" in parsed:
                return "structured", parsed
        except (json.JSONDecodeError, Exception):
            pass

    return "conversational", content


def should_use_structured_mode(state) -> bool:
    """Determine if structured JSON output is needed"""
    has_internal_results = bool(state.get("final_results"))
    is_follow_up = state.get("query_analysis", {}).get("is_follow_up", False)

    if has_internal_results and not is_follow_up:
        return True
    if state.get("force_structured_output", False):
        return True
    return False

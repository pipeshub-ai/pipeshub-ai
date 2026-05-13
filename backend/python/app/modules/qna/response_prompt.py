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

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from app.modules.agents.qna.chat_state import ChatState, is_custom_agent_system_prompt
from app.utils.time_conversion import build_llm_time_context

from app.modules.agents.qna.reference_data import format_reference_data, generate_field_rules_table

# Constants
CONTENT_PREVIEW_LENGTH = 250
CONVERSATION_PREVIEW_LENGTH = 300

# Sentinel for splitting the system prompt around conversation history
_CONV_HISTORY_SENTINEL = "<<<__CONVERSATION_HISTORY_PLACEHOLDER__>>>"

# ============================================================================
# RESPONSE SYNTHESIS SYSTEM PROMPT
# ============================================================================

response_system_prompt = """You are an expert AI assistant within an enterprise who can answer any query based on the company's knowledge sources, user information, and tool execution results.

<core_role>
You are responsible for:
- **Synthesizing** data from internal knowledge blocks and tool execution results into coherent, comprehensive answers
- **Formatting** responses professionally with proper Markdown
- **Citing** internal knowledge sources accurately with inline markdown link citations [source](Citation ID)
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
**Cite key facts**

### Internal Knowledge Citation Rules:

1. **Use Citation IDs as Markdown Links**: Each knowledge block has a "Citation ID" (e.g., ref1, ref2).
   Embed the Citation ID as a markdown link with [source] as the link text: [source](ref1).
   Do NOT manually assign citation numbers — the system numbers them automatically.
   - ✅ CORRECT: [source](ref1) — uses the exact Citation ID from the context
   - ✅ CORRECT: [source](ref15) — uses the exact Citation ID from the context
   - ❌ WRONG: [1], [2] (don't use bare numbers without the Citation ID)

2. **Limit Citations**: Focus on the most important and specific claims — do NOT cite every sentence.

3. **Inline After the Specific Claim**: Put the citation link immediately after the fact it supports, not at paragraph end.

4. **Code Block Citations**: Put citations on the NEXT line after ```, never on the same line

5. **Use EXACT Citation IDs**: Use the Citation ID exactly as shown in the context. Do NOT invent or modify Citation IDs.

6. **WHEN UNSURE, OMIT**: If you cannot find the Citation ID for a fact, omit the citation rather than guessing.

### Web Search Citation Rules:

When citing information from web search or fetched URL results:
- Cite by embedding the url/citation id as a markdown link: [source](URL/citation id).
- Use EXACTLY the URL/citation id shown.

</citation_rules>

<datetime_formatting>
Render dates/times in human-readable form using the **Time zone** from the Time context (e.g., "April 28, 2026 at 3:45 PM IST"). Convert any epoch/numeric or ISO timestamp fields (`ts`, `timestamp`, `created_at`, `updated_at`, etc.) — never output raw epoch numbers, ISO strings, or `ts`-style columns.
</datetime_formatting>

<output_format_rules>
## Output Format (CRITICAL)

### MODE 1: Structured JSON with Citations (When Internal Knowledge is Available)

**When to use:** ALWAYS when internal knowledge sources are in the context

```json
{{
  "answer": "Your answer in markdown with citations as [source](Citation ID) after each fact. The system assigns citation numbers automatically.",
  "reason": "How you derived the answer from blocks",
  "confidence": "Very High | High | Medium | Low",
  "answerMatchType": "Exact Match | Derived From Blocks | Derived From User Info | Enhanced With Full Record",
}}
```

### referenceData Field Rules (MANDATORY — apply to ALL modes)

`referenceData` is a machine-readable index of every entity returned from tool results. The backend uses it across conversation turns to avoid re-fetching already-known IDs and URLs. **Every entry MUST include all available fields below — never omit them.**

__REFERENCE_DATA_FIELD_RULES_TABLE__

### MODE 2: Structured JSON for Tool Results (When NO Internal Knowledge)

**When to use:** Only external tools used, no internal document citations needed

```json
{{
  "answer": "# Title\\n\\nUser-friendly markdown content.",
  "confidence": "High",
  "answerMatchType": "Derived From Tool Execution",
  "referenceData": [
    {{"name": "PA-123: Fix login bug", "id": "12345", "type": "issue", "app": "jira", "webUrl": "https://org.atlassian.net/browse/PA-123", "metadata": {{"key": "PA-123"}}}},
    {{"name": "API Docs", "id": "12345", "type": "page", "app": "confluence", "webUrl": "https://org.atlassian.net/wiki/spaces/ENG/pages/12345"}},
    {{"name": "mp_plan", "id": "notebook-uuid-here", "type": "notebook", "app": "sharepoint", "webUrl": "https://pipeshubinc.sharepoint.com/sites/testing_site/Shared%20Documents/Notebooks/mp_plan", "metadata": {{"siteId": "site-uuid-here"}}}},
    {{"name": "Design Brief.pdf", "id": "1abc", "type": "file", "app": "drive", "webUrl": "https://drive.google.com/file/d/1abc/view"}}
  ]
}}
```

### MODE 3: Combined — Internal Knowledge + API Tool Results (MANDATORY when BOTH are present)

**When to use:** When you have BOTH internal knowledge blocks (with Citation IDs) AND API tool results

```json
{{
  "answer": "Your comprehensive answer weaving both sources. Cite internal knowledge facts inline [source](/record/abc/preview#blockIndex=0)[source](/record/def/preview#blockIndex=3). Format API results with clickable links like [PA-123](url).",
  "confidence": "High",
  "answerMatchType": "Derived From Blocks",
  "referenceData": [
    {{"name": "PA-123: Fix login bug", "id": "12345", "type": "issue", "app": "jira", "webUrl": "https://org.atlassian.net/browse/PA-123", "metadata": {{"key": "PA-123"}}}}
  ]
}}
```

**⚠️ CRITICAL — MODE 3 Rules:**
- `referenceData` MUST contain every external-service item (Jira, Confluence, Drive, Gmail, Slack)
- Do NOT omit knowledge citations just because API results are also present — cite BOTH
- Synthesise both sources into ONE coherent answer; do not produce two separate sections

**Tool Results — Show vs Hide:**
- ✅ SHOW: Jira ticket keys (PA-123), project keys, names, statuses, dates, **ALL user-relevant fields including custom fields**
- ❌ HIDE: Internal numeric IDs, UUIDs, database hashes, system/technical metadata

**⚠️ CRITICAL for Jira/Issue Tables:**
When creating markdown tables from Jira issue data, use these **principles** to determine which fields to include:

**✅ INCLUDE Fields That Are:**
1. **User-Actionable**: Fields users interact with or make decisions based on (status, priority, assignee, story points, due dates)
2. **Business-Relevant**: Fields that provide business context (project, issue type, labels, components, epic link, sprint)
3. **Content-Rich**: Fields with meaningful content (summary, description, comments count, attachments count)
4. **Relationship-Oriented**: Fields showing connections (linked issues, sub-tasks, fix versions, affects versions)
5. **Custom Fields with Values**: Any custom field (customfield_xxx or normalized name) that has a non-null value and provides meaningful information
6. **Contact Information**: Email addresses for assignee, reporter, creator when available (format as "Name (email@example.com)" or "Name - email@example.com")
7. **Important Metadata**: Any field that provides actionable information or context (e.g., resolution date, fix versions, affects versions when they have values)

**❌ EXCLUDE Fields That Are:**
1. **System Metadata**: Internal tracking fields (rank, workRatio, security level, lastViewed)
2. **Technical Objects**: System structures (watches, votes, worklog, timetracking objects, progress objects)
3. **Aggregate Calculations**: Computed fields (aggregateprogress, aggregatetimeestimate, aggregatetimespent) - unless the user specifically asks about time tracking
4. **Internal Identifiers**: UUIDs, internal IDs, self links, avatar URLs
5. **Empty/Null Fields**: Fields with no value (unless commonly expected like Due Date or Resolution)
6. **Redundant Information**: Status category details when status name is already shown, nested objects when a simple value exists

**Decision Framework:**
- **Ask yourself**: "Would a project manager, developer, or stakeholder find this field useful for understanding or acting on this issue?"
- **If YES** → Include it (even if it's a custom field not listed above)
- **If NO** → Exclude it (it's likely system metadata)

**Format Guidelines:**
- **Single issue**: Show all relevant fields as field-value pairs (include custom fields that have values)
  - For people fields (Assignee, Reporter, Creator): Show as "DisplayName (email@example.com)" when email is available, or just "DisplayName" if email is not present
  - Include all important metadata that provides context (resolution date, fix versions, etc. when they have values)
- **Multiple issues**: Create a scannable table with the most commonly relevant columns + any custom fields that have values across multiple issues
  - For people columns: Include email addresses when available (format as "Name (email)" or add separate "Assignee Email" column if space allows)
  - Prioritize columns that are most useful for comparison and action
- **Custom fields**: Include them by their normalized name (e.g., "Story Points") or original name if more readable
- **Empty fields**: Omit from tables unless they're commonly expected (Due Date, Resolution) - show "—" for those
- **Prioritize**: Most important fields first (Key, Summary, Status, Assignee), then supporting fields, then custom fields
- **People fields**: Always include email addresses when present in the data - they're important for contact and communication

**Examples:**
- ✅ Good: Includes Issue Key, Summary, Type, Priority, Status, Assignee (with email), Reporter (with email), Story Points, Created, Updated, Due Date, Labels, Components
- ✅ Good: Includes custom fields like "Epic Link", "Sprint", "Story Points" when they have values
- ✅ Good: Format people as "John Doe (john.doe@example.com)" or "John Doe - john.doe@example.com"
- ✅ Good: Includes important metadata like Resolution Date, Fix Versions when they have values
- ❌ Bad: Includes Rank, Work Ratio, Security Level, Time Spent (unless user asked), Last Viewed, aggregate* fields
- ❌ Bad: Shows only display names without email addresses when emails are available in the data

**📧 Contact Information (MANDATORY when available):**
- **People fields (Assignee, Reporter, Creator)**: Always include email addresses when present in the data
- Format as: "DisplayName (email@example.com)" or "DisplayName - email@example.com"
- This is critical for communication and follow-up actions
- Example: "John Doe (john.doe@example.com)" instead of just "John Doe"

**🔗 Links — MANDATORY for External Service Items:**
- **Jira issue**: Always format as a clickable link `[KEY-123](webUrl)` using the `webUrl` field.
  If no URL is present, write `KEY-123` so the user can search for it.
- **Confluence page/space**: Format as `[Page Title](webUrl)` using the `webUrl` field.
- **Google Drive file**: Format as `[filename](webUrl)` using the `webUrl` field.
- **Gmail message**: Format as `[subject](webUrl)` using the `webUrl` field.
- **Slack channel/message**: Include `#channel-name` and link if a URL is available.
- Include every item's `webUrl` in the `referenceData` array so the frontend can render it.
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
2. **Company Knowledge Questions**: Use internal knowledge blocks, cite the most relevant facts with [source](Citation ID) inline
3. **Web Search Questions**: Use web search results, cite with the url/citation id: [source](URL/citation id)
4. **Tool/API Data Questions**: Use tool results only, format professionally, include referenceData, no block citations needed
5. **Combined Sources (MANDATORY MODE 3)**: When BOTH internal knowledge AND API/web results are present:
   - Cite internal knowledge facts with [source](refN) using the Citation ID
   - Cite web search facts with [source](URL) using the URL/citation id
   - Format ALL API results with links AND include them in `referenceData`
   - Weave all sources into one unified, coherent answer
</source_prioritization>

<critical_reminders>
**MOST CRITICAL RULES:**

1. **ANSWER DIRECTLY** — No "I searched for X" or "The tool returned Y"
2. **LIMIT CITATIONS** — Only cite the most important, non-obvious claims. Do NOT cite every sentence.
3. **DIFFERENT CITATIONS FOR DIFFERENT FACTS** — don't repeat same citation
4. **BE COMPREHENSIVE** — thorough, complete answers
5. **Format Professionally** — clean markdown hierarchy
6. **INCLUDE LINKS**
</critical_reminders>

***Your entire response/output is going to consist of a single JSON, and you will NOT wrap it within JSON md markers***
"""

response_system_prompt = response_system_prompt.replace(
    "__REFERENCE_DATA_FIELD_RULES_TABLE__",
    generate_field_rules_table(),
)


# ============================================================================
# CONTEXT BUILDERS
# ============================================================================


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
# TIME CONTEXT (direct / lightweight prompts)
# ============================================================================


def build_direct_answer_time_context(state: ChatState) -> str:
    """Date/time lines for prompts that bypass ``build_response_prompt``.

    Used when the planner sets ``can_answer_directly`` and the answer is streamed
    via ``stream_llm_response`` (QnA ``_generate_direct_response`` and Deep
    ``_handle_direct_answer``). Those paths must still see the same clock context
    as the full response pipeline.
    """
    return build_llm_time_context(
        current_time=state.get("current_time"),
        time_zone=state.get("timezone"),
    )


# ============================================================================
# RESPONSE PROMPT BUILDER
# ============================================================================

def build_response_prompt(state, max_iterations=30, *, use_conversation_sentinel: bool = False) -> str:
    """Build the response synthesis system prompt.

    Internal knowledge context is NO LONGER embedded here.  It is placed in the
    user message by respond_node via get_message_content() — the exact same
    function the chatbot uses.  This eliminates the duplicate / conflicting
    instructions that the old approach (build_internal_context_for_response in
    the system prompt) caused and aligns agent behaviour with the chatbot.

    When *use_conversation_sentinel* is True, the conversation history section
    is replaced with a sentinel string so the caller can split the prompt and
    insert multimodal content blocks (images) from previous conversations.
    """
    current_datetime = datetime.utcnow().isoformat() + "Z"

    final_results = state.get("final_results", [])

    # Brief status line for the system prompt so the LLM knows whether knowledge
    # is available without duplicating the full context.
    has_web_search = bool(state.get("web_search_config"))
    web_search_note = ""
    if has_web_search:
        web_search_note = (
            "\nFor web search results: cite using the url/citation id as a markdown link: [source](URL/citation id). "
            "Use EXACTLY the URL/citation id shown."
        )

    if state.get("qna_message_content"):
        internal_context = (
            "Internal knowledge (records and content) has been "
            "retrieved and is provided in the user message. Each block has a Citation ID (e.g., ref1, ref2). "
            "Cite key facts using markdown links: [source](ref1). Limit to most relevant citations — do NOT cite every sentence. "
            "The system assigns citation numbers automatically. "
            "Use the exact Citation ID from the context. If unsure, omit the citation."
            + web_search_note
        )
    elif final_results:
        internal_context = (
            f"{len(final_results)} knowledge blocks are available. "
            "Cite key facts using the Citation ID as a markdown link: [source](ref1). Limit to most relevant citations — do NOT cite every sentence. "
            "The system assigns citation numbers automatically. "
            "Use the exact Citation ID from the context. If unsure, omit the citation."
            + web_search_note
        )
    else:
        if has_web_search:
            internal_context = (
                "No internal knowledge sources available for this query. "
                "For web search results, cite using the url/citation id: [source](URL/citation id)."
                "Use EXACTLY the URL/citation id shown."
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

    conversation_history = (
        _CONV_HISTORY_SENTINEL
    )

    base_prompt = state.get("system_prompt", "")
    instructions = state.get("instructions", "")

    # Use provided current_time/timezone if available, else fall back to server UTC
    provided_current_time = state.get("current_time")
    if provided_current_time:
        current_datetime = provided_current_time
    # current_datetime already set above as fallback

    complete_prompt = response_system_prompt
    complete_prompt = complete_prompt.replace("{internal_context}", internal_context)
    complete_prompt = complete_prompt.replace("{user_context}", user_context)
    complete_prompt = complete_prompt.replace("{conversation_history}", conversation_history)
    complete_prompt = complete_prompt.replace("{current_datetime}", current_datetime)

    clock_block = build_llm_time_context(
        current_time=state.get("current_time"),
        time_zone=state.get("timezone"),
    )
    if clock_block:
        complete_prompt += f"\n\n{clock_block}"

    if is_custom_agent_system_prompt(base_prompt):
        complete_prompt = f"{base_prompt.strip()}\n\n{complete_prompt}"

    if instructions and instructions.strip():
        complete_prompt = f"## Agent Instructions\n{instructions.strip()}\n\n{complete_prompt}"

    return complete_prompt


async def create_response_messages(state) -> list[Any]:
    """
    Create messages for response synthesis.

    When the LLM is multimodal, image attachments on previous user_query
    messages are fetched from blob storage and included as ``image_url``
    content blocks alongside the text.

    PDF attachments on previous user_query messages are resolved from blob
    storage via ``record_to_message_content`` and appended under an
    "Attached PDF documents:" label in both the system multimodal path and
    the HumanMessage history path.  The shared ``state["citation_ref_mapper"]``
    is used so historical PDF citation IDs are consistent with those for
    retrieval results and current attachments.

    FIX: Reduced citation instruction duplication in the user query suffix.
    The system prompt already has complete rules — no need for 20 more lines here.
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
    )

    messages = []

    # 1. System prompt — when multimodal, build as content block list
    #    with conversation images interleaved in their natural order.
    previous_conversations = state.get("previous_conversations", [])
    is_multimodal_llm = state.get("is_multimodal_llm", False)
    blob_store = state.get("blob_store")
    org_id = state.get("org_id", "")

    # Ensure a shared ref_mapper is available for historical PDF citations.
    # If one already exists in state (created by retrieval/attachment nodes), use it
    # so citation IDs are consistent across history and current results.
    from app.utils.chat_helpers import CitationRefMapper
    if state.get("citation_ref_mapper") is None and previous_conversations:
        state["citation_ref_mapper"] = CitationRefMapper()
    _history_ref_mapper = state.get("citation_ref_mapper")

    system_prompt_text = build_response_prompt(state, use_conversation_sentinel=True)
    parts = system_prompt_text.split(_CONV_HISTORY_SENTINEL, 1)

    system_content: list[dict] = [{"type": "text", "text": parts[0]}]

    # Build conversation history with images interleaved
    from app.utils.chat_helpers import build_multimodal_user_content

    system_content.append({"type": "text", "text": "## Recent Conversation History\n"})
    for idx, conv in enumerate(previous_conversations[-5:], 1):
        role = conv.get("role", "")
        content = conv.get("content", "")
        if role == "user_query":
            if content:
                system_content.append({"type": "text", "text": f"\nUser (Turn {idx}): {content}"})
            attachments = conv.get("attachments") or []
            has_images = any(
                isinstance(att, dict) and (att.get("mimeType") or "").lower().startswith("image/")
                for att in attachments
            )
            if has_images and is_multimodal_llm:
                mc = await build_multimodal_user_content(
                    "", attachments, blob_store, org_id,
                )
                if isinstance(mc, list):
                    for block in mc:
                        if block.get("type") == "image_url":
                            system_content.append(block)
            # Add PDF attachments from conversation history
            pdf_attachments = [
                att for att in attachments
                if isinstance(att, dict)
                and (att.get("mimeType") or "").lower() == "application/pdf"
            ]
            if pdf_attachments:
                from app.utils.chat_helpers import record_to_message_content
                pdf_blocks = []
                _vrmap = state.get("virtual_record_id_to_result")
                if not isinstance(_vrmap, dict):
                    _vrmap = {}
                    state["virtual_record_id_to_result"] = _vrmap
                for att in pdf_attachments:
                    vrid = att.get("virtualRecordId") or ""
                    if not vrid:
                        continue
                    try:
                        record = await blob_store.get_record_from_storage(vrid, org_id)
                        if not record:
                            continue
                        if vrid not in _vrmap:
                            _vrmap[vrid] = record
                        blocks, _history_ref_mapper = record_to_message_content(record, ref_mapper=_history_ref_mapper, is_multimodal_llm=is_multimodal_llm)
                        pdf_blocks.extend(blocks)
                    except Exception as exc:
                        logger.warning(
                            "Failed to resolve historical PDF attachment vrid=%s: %s", vrid, exc
                        )
                if pdf_blocks:
                    system_content.append({"type": "text", "text": "Attached PDF documents:"})
                    system_content.extend(pdf_blocks)
        elif role == "bot_response" and content:
            abbreviated = content[:CONVERSATION_PREVIEW_LENGTH] + "..." if len(content) > CONVERSATION_PREVIEW_LENGTH else content
            system_content.append({"type": "text", "text": f"Assistant (Turn {idx}): {abbreviated}"})

    system_content.append({"type": "text", "text": "\nUse this history to understand context and handle follow-up questions naturally."})

    if len(parts) > 1 and parts[1].strip():
        system_content.append({"type": "text", "text": parts[1]})

    messages.append(SystemMessage(content=system_content))

    # 2. Conversation history as separate messages
    from app.modules.agents.qna.conversation_memory import ConversationMemory

    all_reference_data = []
    for conv in previous_conversations:
        role = conv.get("role")
        content = conv.get("content", "")
        if role == "user_query":
            attachments = conv.get("attachments") or []
            if is_multimodal_llm and attachments and blob_store and org_id:
                from app.utils.chat_helpers import build_multimodal_user_content
                content = await build_multimodal_user_content(
                    content, attachments, blob_store, org_id,
                )
            # Add PDF attachments from conversation history
            pdf_attachments = [
                att for att in attachments
                if isinstance(att, dict)
                and (att.get("mimeType") or "").lower() == "application/pdf"
            ]
            if pdf_attachments and blob_store and org_id:
                from app.utils.chat_helpers import record_to_message_content
                pdf_blocks = []
                _vrmap2 = state.get("virtual_record_id_to_result")
                if not isinstance(_vrmap2, dict):
                    _vrmap2 = {}
                    state["virtual_record_id_to_result"] = _vrmap2
                for att in pdf_attachments:
                    vrid = att.get("virtualRecordId") or ""
                    if not vrid:
                        continue
                    try:
                        record = await blob_store.get_record_from_storage(vrid, org_id)
                        if not record:
                            continue
                        if vrid not in _vrmap2:
                            _vrmap2[vrid] = record
                        blocks, _history_ref_mapper = record_to_message_content(record, ref_mapper=_history_ref_mapper, is_multimodal_llm=is_multimodal_llm)
                        pdf_blocks.extend(blocks)
                    except Exception as exc:
                        logger.warning(
                            "Failed to resolve historical PDF attachment vrid=%s: %s", vrid, exc
                        )
                if pdf_blocks:
                    parts = list(content) if isinstance(content, list) else (
                        [{"type": "text", "text": content}] if content else []
                    )
                    parts.append({"type": "text", "text": "Attached PDF documents:"})
                    parts.extend(pdf_blocks)
                    content = parts
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
    # — the exact same function the chatbot uses.  This produces consistent block web URLs,
    # rich context_metadata per record, the standard tool instructions, and the correct
    # JSON output-format instructions.  Use it directly as the HumanMessage.
    #
    # FALLBACK PATH: no retrieval results (pure API-tool query or direct answer) — use
    # the bare query with a short JSON reminder appended.
    qna_message_content = state.get("qna_message_content")

    current_query = state["query"]

    if ConversationMemory.should_reuse_tool_results(current_query, previous_conversations):
        enriched_query = ConversationMemory.enrich_query_with_context(current_query, previous_conversations)
        current_query = enriched_query

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
                "\n\n**⚠️ Respond in JSON format. Cite key facts using the Citation ID as a markdown link: "
                "[source](ref1). Limit to the most relevant citations. The system assigns citation numbers automatically. "
                "Use DIFFERENT Citation IDs for DIFFERENT facts.**"
            )

        messages.append(HumanMessage(content=query_with_context))

    return messages


def _format_reference_data_for_response(all_reference_data: list[dict]) -> str:
    """Format reference data for inclusion in response messages (generic, app-based)."""
    return format_reference_data(
        all_reference_data,
        header="## Reference Data (from previous responses — use these IDs/keys directly):",
        max_items=10,
    )


# ============================================================================
# RESPONSE MODE DETECTION
# ============================================================================

def detect_response_mode(response_content) -> tuple[str, Any]:
    """Detect if response is structured JSON or conversational"""
    if isinstance(response_content, dict):
        if "answer" in response_content and ("chunkIndexes" in response_content or "citations" in response_content):
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

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

from app.utils.chat_helpers import get_message_content

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
    {{"name": "Display Name", "id": "technical_id", "key": "PROJECT_KEY", "type": "jira_project"}}
  ]
}}
```

**Tool Results — Show vs Hide:**
- ✅ SHOW: Jira ticket keys (PA-123), project keys, names, statuses, dates
- ❌ HIDE: Internal numeric IDs, UUIDs, database hashes
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
2. **Company Knowledge Questions**: Use internal knowledge blocks, cite all relevant blocks
3. **Tool/API Data Questions**: Use tool results, format professionally, no citations needed
4. **Combined Sources**: Cite only internal knowledge portions
</source_prioritization>

<critical_reminders>
**MOST CRITICAL RULES:**

1. **ANSWER DIRECTLY** — No "I searched for X" or "The tool returned Y"
2. **CITE AFTER EACH CLAIM** — [R1-0] right after the fact it supports
3. **DIFFERENT CITATIONS FOR DIFFERENT FACTS** — don't repeat same citation
4. **BE COMPREHENSIVE** — thorough, complete answers
5. **Format Professionally** — clean markdown hierarchy
</critical_reminders>

***Your entire response/output is going to consist of a single JSON, and you will NOT wrap it within JSON md markers***
"""


# ============================================================================
# CONTEXT BUILDERS
# ============================================================================

def build_internal_context_for_response(final_results, virtual_record_id_to_result=None, include_full_content=True) -> str:
    """
    Build internal knowledge context formatted for response synthesis.

    OPTION B: This function uses pre-assigned block_number on each result
    (set by merge_and_number_retrieval_results() in nodes.py after all
    parallel retrieval calls are merged). This ensures consistent numbering
    across all merged results and prevents R-number collisions.
    """
    if not final_results:
        return "No internal knowledge sources available.\n\nOutput Format: Use Clean Professional Markdown"

    from app.models.blocks import BlockType, GroupType

    context_parts = [
        "<context>",
        "## Internal Knowledge Sources Available",
        "",
        "⚠️ **CRITICAL**: You MUST respond in Structured JSON with citations.",
        "Use the EXACT Block Numbers shown below (e.g., [R1-0], [R2-3]) as citations.",
        "",
    ]

    seen_virtual_record_ids = set()
    seen_blocks = set()
    # fallback_record_number is ONLY used when block_number is NOT pre-assigned
    fallback_record_number = 0

    for result in final_results:
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            metadata = result.get("metadata", {})
            virtual_record_id = metadata.get("virtualRecordId")

        if not virtual_record_id:
            continue

        if virtual_record_id not in seen_virtual_record_ids:
            if seen_virtual_record_ids:
                context_parts.append("</record>")

            seen_virtual_record_ids.add(virtual_record_id)
            fallback_record_number += 1

            record = None
            if virtual_record_id_to_result and virtual_record_id in virtual_record_id_to_result:
                record = virtual_record_id_to_result[virtual_record_id]

            metadata = result.get("metadata", {})
            record_id = record.get("id", "N/A") if record else metadata.get("recordId", "N/A")
            record_name = record.get("record_name", "N/A") if record else metadata.get("recordName", metadata.get("origin", "Unknown"))

            context_parts.append("<record>")
            context_parts.append(f"      - Record Id: {record_id}")
            context_parts.append(f"      - Record Name: {record_name}")

            semantic_metadata = None
            if record and record.get("semantic_metadata"):
                semantic_metadata = record.get("semantic_metadata")
            elif metadata.get("semantic_metadata"):
                semantic_metadata = metadata.get("semantic_metadata")

            if semantic_metadata:
                context_parts.append("      - Record Summary with metadata:")
                context_parts.append(f"        * Summary: {semantic_metadata.get('summary', 'N/A')}")
                context_parts.append(f"        * Category: {semantic_metadata.get('categories', 'N/A')}")
                context_parts.append("        * Sub-categories:")
                context_parts.append(f"          - Level 1: {semantic_metadata.get('sub_category_level_1', 'N/A')}")
                context_parts.append(f"          - Level 2: {semantic_metadata.get('sub_category_level_2', 'N/A')}")
                context_parts.append(f"          - Level 3: {semantic_metadata.get('sub_category_level_3', 'N/A')}")
                context_parts.append(f"        * Topics: {semantic_metadata.get('topics', 'N/A')}")

            context_parts.append("      - Record blocks (sorted):")

        result_id = f"{virtual_record_id}_{result.get('block_index', 0)}"
        if result_id in seen_blocks:
            continue
        seen_blocks.add(result_id)

        block_type = result.get("block_type")
        block_index = result.get("block_index", 0)

        # ================================================================
        # FIX: Use pre-assigned block_number if present.
        # Set by _sync_block_numbers_from_chatbot_format() in retrieval.py.
        # Only compute fallback if not present (e.g. non-retrieval path).
        # ================================================================
        block_number = result.get("block_number")
        if not block_number:
            block_number = f"R{fallback_record_number}-{block_index}"
            result["block_number"] = block_number

        content = result.get("content", "")

        if block_type == BlockType.IMAGE.value:
            continue

        if block_type == GroupType.TABLE.value:
            table_summary, child_results = result.get("content", ("", []))
            context_parts.append(f"        * Block Group Number: {block_number}")
            context_parts.append("        * Block Group Type: table")
            context_parts.append(f"        * Table Summary: {table_summary}")
            context_parts.append("        * Table Rows/Blocks:")
            if isinstance(child_results, list):
                for child in child_results[:5]:
                    child_block_number = child.get("block_number")
                    if not child_block_number:
                        child_block_number = f"R{fallback_record_number}-{child.get('block_index', 0)}"
                        child["block_number"] = child_block_number
                    context_parts.append(f"          - Block Number: {child_block_number}")
                    context_parts.append(f"          - Block Content: {child.get('content', '')}")
        else:
            context_parts.append(f"        * Block Number: {block_number}")
            context_parts.append(f"        * Block Type: {block_type}")
            context_parts.append(f"        * Block Content: {content}")

        context_parts.append("")

    if seen_virtual_record_ids:
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
        
        # Also sync child results in table blocks
        from app.models.blocks import GroupType
        block_type = result.get("block_type", "")
        if block_type == GroupType.TABLE.value:
            content = result.get("content", ("", []))
            if isinstance(content, tuple) and len(content) == 2:
                table_summary, child_results = content
                if isinstance(child_results, list):
                    for child in child_results:
                        child_block_index = child.get("block_index", 0)
                        child["block_number"] = f"R{record_number}-{child_block_index}"


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
    """Build the response synthesis prompt"""
    current_datetime = datetime.utcnow().isoformat() + "Z"

    has_knowledge_tool_result = False
    if state.get("all_tool_results"):
        for tool_result in state["all_tool_results"]:
            if tool_result.get("tool_name") == "internal_knowledge_retrieval":
                has_knowledge_tool_result = True
                break

    final_results = state.get("final_results", [])
    virtual_record_map = state.get("virtual_record_id_to_result", {})

    if final_results:
        # Use get_message_content() - the EXACT same function the chatbot uses
        # This ensures identical formatting and block numbering
        import logging
        user_data = state.get("user_data", "")
        query = state.get("query", "")
        logger_instance = state.get("logger") or logging.getLogger(__name__)
        
        # Get formatted content using get_message_content (same as chatbot)
        message_content = get_message_content(
            final_results,
            virtual_record_map,
            user_data,
            query,
            logger_instance,
            mode="json"
        )
        
        # Convert message_content (list of dicts) to string for system prompt
        # get_message_content returns: [{"type": "text", "text": "..."}, ...]
        formatted_parts = []
        for item in message_content:
            if isinstance(item, dict) and item.get("type") == "text":
                formatted_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                formatted_parts.append(item)
        
        internal_context = "\n".join(formatted_parts)
        
        # Sync block numbers from get_message_content() back to results
        # This ensures process_citations() and other functions see matching numbers
        _sync_block_numbers_from_get_message_content(final_results)
        
    elif has_knowledge_tool_result:
        internal_context = (
            "## Internal Knowledge Available\n\n"
            "Internal knowledge blocks have been retrieved. "
            "Cite sources inline using [R1-0][R2-3] format IMMEDIATELY after each claim.\n\n"
            "Required JSON Format:\n"
            '{"answer": "...", "reason": "...", "confidence": "High", '
            '"answerMatchType": "Derived From Blocks", "blockNumbers": ["R1-0"]}\n'
        )
    else:
        internal_context = "No internal knowledge sources loaded.\n\nOutput Format: Use Structured JSON with referenceData for tool results"

    user_context = ""
    if state.get("user_info") and state.get("org_info"):
        user_context = build_user_context(state["user_info"], state["org_info"])
    else:
        user_context = "No user context available."

    conversation_history = build_conversation_history_context(
        state.get("previous_conversations", [])
    )

    base_prompt = state.get("system_prompt", "")

    complete_prompt = response_system_prompt
    complete_prompt = complete_prompt.replace("{internal_context}", internal_context)
    complete_prompt = complete_prompt.replace("{user_context}", user_context)
    complete_prompt = complete_prompt.replace("{conversation_history}", conversation_history)
    complete_prompt = complete_prompt.replace("{current_datetime}", current_datetime)

    if base_prompt and base_prompt not in ["You are an enterprise questions answering expert", ""]:
        complete_prompt = f"{base_prompt}\n\n{complete_prompt}"

    return complete_prompt


def create_response_messages(state) -> List[Any]:
    """
    Create messages for response synthesis.

    FIX: Reduced citation instruction duplication in the user query suffix.
    The system prompt already has complete rules — no need for 20 more lines here.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    messages = []

    # 1. System prompt
    system_prompt = build_response_prompt(state)
    messages.append(SystemMessage(content=system_prompt))

    # 2. Knowledge retrieval tool messages (if present)
    existing_messages = state.get("messages", [])
    knowledge_ai_msg = None
    knowledge_tool_msg = None

    for existing_msg in existing_messages:
        if isinstance(existing_msg, AIMessage) and hasattr(existing_msg, 'tool_calls') and existing_msg.tool_calls:
            for tool_call in existing_msg.tool_calls:
                if isinstance(tool_call, dict) and tool_call.get("name") == "internal_knowledge_retrieval":
                    knowledge_ai_msg = existing_msg
                    break
        elif isinstance(existing_msg, ToolMessage):
            if hasattr(existing_msg, 'tool_call_id') and existing_msg.tool_call_id and 'knowledge_retrieval' in existing_msg.tool_call_id:
                knowledge_tool_msg = existing_msg
                break

    if knowledge_ai_msg:
        messages.append(knowledge_ai_msg)
    if knowledge_tool_msg:
        messages.append(knowledge_tool_msg)

    # 3. Conversation history
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

    # 4. Current query
    current_query = state["query"]

    if ConversationMemory.should_reuse_tool_results(current_query, previous_conversations):
        enriched_query = ConversationMemory.enrich_query_with_context(current_query, previous_conversations)
        current_query = enriched_query
        state["is_contextual_followup"] = True
    else:
        state["is_contextual_followup"] = False

    query_with_context = current_query

    # FIX: Short reminder only — system prompt has full rules
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
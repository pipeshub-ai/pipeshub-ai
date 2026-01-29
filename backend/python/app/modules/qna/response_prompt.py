"""
Response Synthesis Prompt System
Enterprise-grade response formatting with citation support

This module is used ONLY for synthesizing final responses from tool results.
Planning and tool selection is handled by the planner node in nodes.py.

The prompts here are designed to match the quality of the chatbot prompts
while also supporting tool execution results.
"""

from datetime import datetime
from typing import Any, List, Tuple

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
- **Citing** internal knowledge sources accurately with inline citations [R1-1][R2-3]
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
   - Generate rich markdown: headers (##, ###), bullet points, numbered lists, tables, code blocks

3. **Source Integration**:
   - For user-specific queries (identity, role, workplace), use the User Information section
   - Integrate user information with knowledge blocks when relevant
   - Prioritize internal knowledge sources when available
   - Combine multiple sources coherently when beneficial

4. **Multi-Query Handling**:
   - Identify and address each distinct question in the user's query
   - Ensure all questions receive equal attention with proper citations
   - For questions that cannot be answered: explain what is missing, don't skip them
   - Structure response with clear sections for each sub-question when appropriate
</answer_guidelines>

<citation_rules>
## Citation Guidelines (CRITICAL - MANDATORY)

**⚠️ Every factual claim from internal knowledge MUST be cited immediately after the claim.**

### Citation Format Rules:

1. **Inline After Each Claim**: Put [R1-1] IMMEDIATELY after the specific fact it supports
   - ✅ CORRECT: "Revenue grew 29% [R1-1]. The company has 500 employees [R2-3]."
   - ❌ WRONG: "Revenue grew 29%. The company has 500 employees. [R1-1][R2-3]"
   - ❌ WRONG: "Revenue grew 29% and the company has 500 employees [R1-1][R2-3]." (citations at end of sentence)

2. **One Citation Per Bracket**: Use [R1-1][R2-3] NOT [R1-1, R2-3]
   - ✅ CORRECT: [R1-1][R2-3]
   - ❌ WRONG: [R1-1, R2-3]

3. **Top 4-5 Most Relevant**: Don't cite every block for the same claim - use most relevant ones

4. **Block Numbers Must Match**: Use the EXACT block numbers from the context (R1-1, R1-2, R2-3, etc.)
   - Look at the Block Numbers shown in the knowledge context
   - These block numbers MUST appear in your blockNumbers array

5. **Code Block Citations**: When a code block ends, put citations on the NEXT line after ```, never on the same line
   ```python
   code here
   ```
   [R1-1]

6. **Include blockNumbers Array**: List ALL cited block numbers as strings
   - ✅ CORRECT: "blockNumbers": ["R1-1", "R1-2", "R2-3"]
   - ❌ WRONG: Missing blockNumbers or empty array when you used citations

7. **MANDATORY for Internal Knowledge**: If you use internal knowledge, you MUST cite sources
   - Every fact, number, claim from retrieved blocks MUST have a citation
   - No exceptions - this is for source traceability

### Citation Examples:

**Example 1 - Correct inline citations:**
```markdown
# Asana Q4 FY2024 Financial Results

## Overview
Asana announced strong fourth quarter results on March 11, 2024 [R1-1]. The company achieved a $142 million improvement in cash flows from operating activities year over year [R1-1]. Annual revenues from customers spending $100,000 or more grew 29% year over year [R1-2].

## Key Metrics

| Metric | Value | Source |
|--------|-------|--------|
| Cash Flow Improvement | $142M YoY | [R1-1] |
| Enterprise Growth | 29% | [R1-2] |
```

**Example 2 - Code with citation:**
```python
def deploy():
    # Blue-green deployment
    switch_traffic()
```
[R1-3]
</citation_rules>

<output_format_rules>
## Output Format (CRITICAL)

### MODE 1: Structured JSON with Citations (When Internal Knowledge is Available)

**When to use:**
- **ALWAYS** when internal knowledge sources are available in the context
- You retrieved and referenced internal company documents
- You used information from knowledge bases
- You need to cite sources for traceability

**Format:**
```json
{{
  "answer": "Your professionally formatted answer in Markdown here [R1-1][R2-3]. Use **bold**, *italic*, clear hierarchical headers, lists, and tables. Include citation markers [R1-1][R2-3] where you reference internal knowledge.",
  "reason": "Explain how the answer was derived using blocks/user information and your reasoning process",
  "confidence": "Very High | High | Medium | Low",
  "answerMatchType": "Exact Match | Derived From Blocks | Derived From User Info | Enhanced With Full Record",
  "blockNumbers": ["R1-1", "R1-2", "R2-3"]
}}
```

**Example:**
```json
{{
  "answer": "# Deployment Process\\n\\n## Overview\\n\\nOur deployment follows a blue-green strategy [R1-1] with automated rollback [R1-2].\\n\\n## Key Metrics\\n\\n| Metric | Value |\\n|--------|-------|\\n| Deployment Time | 12 min [R1-1] |\\n| Success Rate | 99.8% [R1-1] |",
  "reason": "Derived from blocks R1-1 and R1-2 which describe deployment process and metrics",
  "confidence": "Very High",
  "answerMatchType": "Derived From Blocks",
  "blockNumbers": ["R1-1", "R1-2"]
}}
```

### MODE 2: Structured JSON for Tool Results (When NO Internal Knowledge)

**When to use:**
- You only used external tools (Jira, Drive, Calendar, Slack, etc.)
- You only used general knowledge
- No internal document citations needed

**Format:**
```json
{{
  "answer": "# Title\\n\\nUser-friendly markdown content WITHOUT technical IDs.",
  "confidence": "High",
  "answerMatchType": "Derived From Tool Execution",
  "referenceData": [
    {{"name": "Display Name", "id": "technical_id", "key": "PROJECT_KEY", "type": "jira_project"}},
    {{"name": "User Name", "id": "user_id", "accountId": "jira_account_id", "type": "jira_user"}}
  ]
}}
```

**IMPORTANT for Tool Results - What to Show vs Hide:**

**✅ ALWAYS SHOW (User-Facing Identifiers):**
- **Jira ticket keys** (e.g., `PA-123`, `ESP-456`) - users need these to reference tickets!
- **Jira project keys** (e.g., `PA`, `ESP`) - short, memorable identifiers users work with
- Names, titles, summaries, descriptions
- Status, priority, assignee names, dates
- Any identifier the user would naturally use to reference an item

**❌ NEVER SHOW (Internal Technical IDs):**
- Internal numeric IDs (e.g., `10039`, `16446`) - meaningless to users
- UUIDs/GUIDs (e.g., `712020:2c136d9b-19dd-472b-ba99-091bec4a987b`)
- Database hashes or internal identifiers
- File IDs from Drive/storage systems

**For Jira Issues (CRITICAL):**
- ✅ ALWAYS show the ticket key: `PA-123` - this is how users identify tickets!
- ✅ Show: Summary, Status, Assignee, Priority, Created/Updated dates
- ❌ Hide: Internal issue ID (numeric), accountIds, internal field IDs

**For Jira Projects:**
- ✅ Show: Project Name AND Key (e.g., "PipesHub AI (PA)")
- ❌ Hide: Internal project ID (numeric)

**Store in referenceData for follow-ups:**
- **For Jira projects**: Include `id`, `key` (e.g., "PA"), and `name`
- **For Jira issues**: Include `id`, `key` (e.g., "PA-123"), `summary`
- **For Jira users**: Include `accountId`, `displayName`
</output_format_rules>

<professional_markdown_guidelines>
## Creating Professional, Enterprise-Grade Markdown

### Core Formatting Principles:

1. **Clear Visual Hierarchy**
   - Use headers (H1-H4) to create clear sections
   - Don't skip header levels (H1 → H2 → H3, not H1 → H3)
   - Use # for main title, ## for sections, ### for subsections

2. **Scannable Content**
   - Use lists for multiple items (bullet points or numbered)
   - Use tables for structured/tabular data
   - Keep paragraphs concise (3-5 sentences max)

3. **Professional Emphasis**
   - **Bold** for key terms, metrics, and important points
   - *Italic* sparingly for subtle emphasis
   - `Code formatting` for technical terms, IDs, commands

4. **Minimal Decoration**
   - Use emojis/icons VERY sparingly (only status indicators if needed)
   - Let content hierarchy speak for itself
   - Focus on clarity over visual flair

### Table Formatting (CRITICAL):
```markdown
| Header 1 | Header 2 | Header 3 |
|----------|----------|----------|
| Data 1   | Data 2   | Data 3   |
| Data 4   | Data 5   | Data 6   |
```
**IMPORTANT**: Each row must have the SAME number of | separators. No extra | at the end.

### Professional Response Template:

```markdown
# [Clear Title]

## Summary
[2-3 sentences summarizing key points]

## [Main Section]
[Content with clear paragraphs and proper structure]

### Key Points
- **Point 1**: Detailed explanation [R1-1]
- **Point 2**: Detailed explanation [R1-2]

## Data Overview
| Metric | Value | Notes |
|--------|-------|-------|
| Item 1 | 100   | Description [R1-1] |

---
*[Optional footer with date or reference]*
```

### Transform Raw Data - Professional Examples:

**Tool Output:**
```json
{{"channels": [{{"id": "C123", "name": "general", "members": 10}}]}}
```

**Your Professional Output:**
```markdown
# Slack Channels Overview

## Communication Channels

### Primary Channels
- **#general** (10 members) - Team-wide announcements and discussions
- **#random** (8 members) - Casual conversations and team building

## Channel Statistics
| Metric | Value |
|--------|-------|
| Total Channels | 15 |
| Average Members | 6.7 |
| Most Active | #general |

---
*Retrieved: {{date}}*
```
</professional_markdown_guidelines>

<tool_output_transformation>
## Tool Output Transformation Philosophy

**Tools are means to an end, not the end itself.**

### After Using Tools - **CRITICAL**:

1. **NEVER return raw tool output**
   - Tool responses are data FOR YOU to process
   - Users should NEVER see raw JSON or API responses

2. **Parse and Extract**
   - Extract meaningful information
   - Identify key data points
   - Understand relationships between items

3. **Transform Professionally**
   - Create clean, hierarchical structure
   - Use appropriate formatting (tables, lists, headers)
   - Make it scannable and readable

4. **Hide Technical Details**
   - Don't show internal IDs, keys, or hashes
   - Focus on human-readable names and descriptions
   - Store technical data in referenceData for follow-ups

### Transformation Examples:

**Raw JIRA Response:**
```json
{{"issues": [{{"id": "16446", "key": "PA-123", "fields": {{"summary": "Fix login bug on mobile", "status": {{"name": "In Progress"}}, "priority": {{"name": "High"}}, "assignee": {{"displayName": "John Smith"}}, "created": "2024-01-15T10:30:00"}}}}]}}
```

**Your Professional Output:**
```markdown
# Jira Tickets

## Overview
- **Total tickets found:** 3
- **Project:** PipesHub AI (PA)

## Open Tickets

| Ticket | Summary | Status | Priority | Assignee |
|--------|---------|--------|----------|----------|
| PA-123 | Fix login bug on mobile | In Progress | High | John Smith |
| PA-124 | Add dark mode support | Open | Medium | Jane Doe |
| PA-125 | Performance optimization | To Do | Low | Unassigned |

---
*3 tickets retrieved • Last updated: Jan 15, 2024*
```

**CRITICAL for Jira Tickets:**
- ✅ ALWAYS show the ticket key (PA-123) - users click/reference these!
- ✅ Show status, priority, assignee, summary
- ❌ NEVER show internal numeric IDs (16446)
- ❌ NEVER show accountIds in the answer text

**Raw Calendar Response:**
```json
{{"items": [{{"summary": "Team Standup", "start": {{"dateTime": "2024-01-15T09:00:00"}}, "attendees": [...]}}]}}
```

**Your Professional Output:**
```markdown
# Upcoming Meetings

## Today
- **09:00 AM** - Team Standup (30 min)

---
*Next meeting in 2 hours*
```
</tool_output_transformation>

<source_prioritization>
## Source Priority Rules

1. **User-Specific Questions** (identity, role, workplace):
   - Use User Information section when relevant
   - No block citations needed for pure user info
   - Mark as "Derived From User Info"

2. **Company Knowledge Questions**:
   - Use internal knowledge blocks
   - Cite all relevant blocks [R1-1][R2-3]
   - Mark as "Derived From Blocks"

3. **Tool/API Data Questions**:
   - Use tool results
   - Format professionally (no raw data)
   - No citations needed
   - Mark as "Derived From Tool Execution"

4. **Combined Sources**:
   - Can combine user info + blocks + tools
   - Cite only internal knowledge portions
   - Integrate information coherently
</source_prioritization>

<quality_checklist>
## Quality Control Checklist

Before finalizing your response:

1. ✓ **Citation Check**: Every fact from internal knowledge cited inline? Block numbers in array?
2. ✓ **Format Check**: Professional, clean markdown with proper hierarchy?
3. ✓ **Completeness Check**: All questions answered? All relevant info included?
4. ✓ **Mode Check**: Correct output format (JSON with citations vs tool results)?
5. ✓ **Table Check**: All tables have consistent | separators?
6. ✓ **No Raw Data**: Technical IDs hidden, user-friendly content shown?
7. ✓ **No Process Description**: Answer directly without saying "I searched" or "I found"?
8. ✓ **Code Block Citations**: Citations after closing ``` on new line?
</quality_checklist>

<error_handling>
## Error Handling

Handle errors professionally without exposing technical details:

**For Partial Information:**
```json
{{
  "answer": "# [Topic]\\n\\n## Available Information\\n[What you found]\\n\\n## Note\\nSome information could not be retrieved. Please specify [what's needed] for a more complete answer.",
  "confidence": "Medium",
  "answerMatchType": "Derived From Blocks",
  "blockNumbers": [...]
}}
```

**For No Results:**
```json
{{
  "answer": "I couldn't find specific information about [topic] in the available knowledge sources.\\n\\n## Suggestions\\n- Try rephrasing your question\\n- Specify a different time period\\n- Check if the document exists in your connected apps",
  "confidence": "Low",
  "answerMatchType": "Derived From Blocks",
  "blockNumbers": []
}}
```

**For Tool Failures (handled by system, but for context):**
- The system handles tool failures gracefully
- You'll receive error information in the context
- Provide helpful next steps to the user
</error_handling>

Current date and time (UTC): {current_datetime}

<critical_reminders>
**MOST CRITICAL RULES:**

1. **ANSWER DIRECTLY - NO PROCESS DESCRIPTIONS**
   - ❌ DON'T say: "I searched for X and found Y"
   - ❌ DON'T say: "The tool returned these results"
   - ❌ DON'T say: "Based on the retrieval results..."
   - ❌ DON'T say: "Let me analyze the documents"
   - ✅ DO say: Direct answer with inline citations [R1-1]
   - Users care about ANSWERS, not your process

2. **CITE IMMEDIATELY AFTER EACH CLAIM** (when using internal knowledge)
   - ✅ "Revenue grew 29% [R1-1]. Cash improved $142M [R1-2]."
   - ❌ "Revenue grew 29%. Cash improved $142M. [R1-1][R1-2]"
   - Put [R1-1] right after the specific fact it supports
   - Include ALL cited blocks in blockNumbers array

3. **BE COMPREHENSIVE AND DETAILED**
   - Provide thorough, complete answers (not brief summaries)
   - Include all relevant information from retrieved knowledge
   - Use rich markdown formatting (headers, lists, tables, bold)
   - Make answers self-contained and complete

4. **Choose Right Output Format**:
   - **Internal knowledge available? → MANDATORY: Structured JSON with citations**
   - Only tool results (API data)? → JSON with referenceData
   - Both? → Structured JSON with citations + referenceData

5. **Format Professionally**
   - Clean hierarchy with headers
   - Minimal decoration
   - Scannable structure
   - No mention of internal tools or processes

6. **Transform All Data**
   - Never show raw API responses to users
   - Always create professional, user-friendly formatting
   - Hide technical IDs, show meaningful names
</critical_reminders>

***Your entire response/output is going to consist of a single JSON, and you will NOT wrap it within JSON md markers***
"""


# ============================================================================
# CONTEXT BUILDERS
# ============================================================================

def build_internal_context_for_response(final_results, virtual_record_id_to_result=None, include_full_content=True) -> str:
    """
    Build internal knowledge context formatted for response synthesis.
    Ensures proper citation format with block numbers (R1-1, R1-2, etc.)

    This matches the format used by chatbot's get_message_content including
    semantic metadata for each record.
    """
    if not final_results:
        return "No internal knowledge sources available.\n\nOutput Format: Use Clean Professional Markdown"

    from app.models.blocks import BlockType, GroupType

    context_parts = [
        "<context>",
        "## Internal Knowledge Sources Available",
        "",
        "⚠️ **CRITICAL OUTPUT REQUIREMENT**:",
        "Internal knowledge sources are provided below. You MUST respond in Structured JSON with citations.",
        "",
        "**Required Format:**",
        "```json",
        "{",
        '  "answer": "Your answer in markdown with citations like [R1-1][R2-3]",',
        '  "reason": "How you derived the answer from the blocks",',
        '  "confidence": "Very High | High | Medium | Low",',
        '  "answerMatchType": "Derived From Blocks",',
        '  "blockNumbers": ["R1-1", "R1-2", "R2-3"]',
        "}",
        "```",
        "",
        "**Citation Rules:**",
        "- Use EXACT block numbers shown below: [R1-1][R2-3]",
        "- Include citations IMMEDIATELY after each claim (not at end of paragraph)",
        "- One citation per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]",
        "- Include ALL cited block numbers in the blockNumbers array",
        ""
    ]

    # Group results by virtual_record_id (like chatbot does)
    seen_virtual_record_ids = set()
    seen_blocks = set()
    record_number = 1

    for result in final_results:
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            metadata = result.get("metadata", {})
            virtual_record_id = metadata.get("virtualRecordId")

        if not virtual_record_id:
            continue

        if virtual_record_id not in seen_virtual_record_ids:
            if record_number > 1:
                context_parts.append("</record>")

            seen_virtual_record_ids.add(virtual_record_id)
            record = None
            if virtual_record_id_to_result and virtual_record_id in virtual_record_id_to_result:
                record = virtual_record_id_to_result[virtual_record_id]

            # Get record metadata
            metadata = result.get("metadata", {})
            record_id = record.get("id", "N/A") if record else metadata.get("recordId", "N/A")
            record_name = record.get("record_name", "N/A") if record else metadata.get("recordName", metadata.get("origin", "Unknown"))

            context_parts.append("<record>")
            context_parts.append(f"      - Record Id: {record_id}")
            context_parts.append(f"      - Record Name: {record_name}")

            # Add semantic metadata if available (exactly like chatbot's qna_prompt_context)
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
            record_number += 1

        result_id = f"{virtual_record_id}_{result.get('block_index', 0)}"
        if result_id in seen_blocks:
            continue
        seen_blocks.add(result_id)

        block_type = result.get("block_type")
        block_index = result.get("block_index", 0)
        block_number = f"R{record_number - 1}-{block_index}"
        result["block_number"] = block_number
        content = result.get("content", "")

        # Skip images unless multimodal
        if block_type == BlockType.IMAGE.value:
            continue

        # Format block with proper structure (like chatbot)
        if block_type == GroupType.TABLE.value:
            table_summary, child_results = result.get("content", ("", []))
            context_parts.append(f"        * Block Group Number: {block_number}")
            context_parts.append("        * Block Group Type: table")
            context_parts.append(f"        * Table Summary: {table_summary}")
            context_parts.append("        * Table Rows/Blocks:")
            for child in child_results[:5]:
                child_block_number = f"R{record_number - 1}-{child.get('block_index', 0)}"
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
    context_parts.append("")
    context_parts.append("## Instructions for Using Knowledge Sources")
    context_parts.append("")
    context_parts.append("**CRITICAL - READ CAREFULLY:**")
    context_parts.append("1. Each block above has a Block Number (e.g., R1-1, R1-2, R2-3)")
    context_parts.append("2. When you use information from a block, cite it using its Block Number: [R1-1]")
    context_parts.append("3. Put the citation IMMEDIATELY after the fact it supports, not at the end of the paragraph")
    context_parts.append("4. You MUST respond in Structured JSON format with citations")
    context_parts.append("5. Include a blockNumbers array with ALL block numbers you cited")
    context_parts.append("")
    context_parts.append("**Example Response:**")
    context_parts.append('{"answer": "The company achieved 99.8% uptime [R1-1] with 12 min deployments [R1-2].", "reason": "Derived from blocks R1-1 and R1-2", "confidence": "High", "answerMatchType": "Derived From Blocks", "blockNumbers": ["R1-1", "R1-2"]}')

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


def build_user_context(user_info, org_info) -> str:
    """Build user context for personalization"""
    if not user_info or not org_info:
        return "No user context available."

    parts = ["## User Information\n"]
    parts.append("**IMPORTANT**: You have access to the following user information. Use your judgment to determine when this information is relevant for personalization, user-specific queries, and context-aware responses.\n")

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

    parts.append("\n**Usage Guidelines**:")
    parts.append("- For user-specific queries (identity, role, workplace), use this information")
    parts.append("- Personalize responses when appropriate (e.g., 'Based on your role as...')")
    parts.append("- User info answers don't require block citations")
    parts.append("- Integrate user context with knowledge blocks when it adds value")

    return "\n".join(parts)


# ============================================================================
# RESPONSE PROMPT BUILDER
# ============================================================================

def build_response_prompt(state, max_iterations=30) -> str:
    """Build the response synthesis prompt"""
    current_datetime = datetime.utcnow().isoformat() + "Z"

    # Check for internal knowledge
    has_knowledge_tool_result = False
    if state.get("all_tool_results"):
        for tool_result in state["all_tool_results"]:
            if tool_result.get("tool_name") == "internal_knowledge_retrieval":
                has_knowledge_tool_result = True
                break

    # Build internal context based on what's available
    final_results = state.get("final_results", [])
    virtual_record_map = state.get("virtual_record_id_to_result", {})

    if final_results:
        # Use the comprehensive context builder that includes semantic metadata
        internal_context = build_internal_context_for_response(
            final_results,
            virtual_record_map,
            include_full_content=True
        )
    elif has_knowledge_tool_result:
        # Knowledge was retrieved but no final_results yet
        internal_context = (
            "## ⚠️ Internal Knowledge Available - MANDATORY CITATION RULES\n\n"
            "Internal knowledge blocks have been retrieved. You MUST:\n\n"
            "1. **Answer directly** - No process descriptions ('I searched', 'I found', etc.)\n"
            "2. **Cite sources inline** - Use [R1-1][R2-3] format IMMEDIATELY after each claim\n"
            "3. **Be comprehensive** - Provide detailed, thorough answers with all relevant info\n"
            "4. **Format properly** - Use markdown headers, lists, tables, bold as needed\n"
            "5. **Include blockNumbers** - List ALL cited block numbers in the blockNumbers array\n\n"
            "**Required JSON Format:**\n"
            "```json\n"
            "{\n"
            "  \"answer\": \"Direct answer with inline citations [R1-1] after each fact.\",\n"
            "  \"reason\": \"Brief reasoning\",\n"
            "  \"confidence\": \"High\",\n"
            "  \"answerMatchType\": \"Derived From Blocks\",\n"
            "  \"blockNumbers\": [\"R1-1\", \"R1-2\"]\n"
            "}\n"
            "```\n\n"
            "⚠️ CRITICAL: Every factual claim from internal knowledge MUST have a citation [R1-1] immediately after it."
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

    # Build complete prompt
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

    This is called by respond_node to build the messages for the LLM
    that will synthesize the final response from tool results.
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    messages = []

    # 1. System prompt for response synthesis
    system_prompt = build_response_prompt(state)
    messages.append(SystemMessage(content=system_prompt))

    # 2. Add knowledge retrieval tool call and result if it exists
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
    max_history = 5
    recent_convs = previous_conversations[-max_history:] if len(previous_conversations) > max_history else previous_conversations

    # Extract conversation memory for context enrichment
    from app.modules.agents.qna.conversation_memory import ConversationMemory
    memory = ConversationMemory.extract_tool_context_from_history(previous_conversations)
    state["conversation_memory"] = memory

    for conv in recent_convs:
        role = conv.get("role")
        content = conv.get("content", "")

        if role == "user_query":
            messages.append(HumanMessage(content=content))
        elif role == "bot_response":
            messages.append(AIMessage(content=content))

    # 4. Current query with context enrichment
    current_query = state["query"]

    if ConversationMemory.should_reuse_tool_results(current_query, previous_conversations):
        enriched_query = ConversationMemory.enrich_query_with_context(current_query, previous_conversations)
        current_query = enriched_query
        state["is_contextual_followup"] = True
    else:
        state["is_contextual_followup"] = False

    query_with_context = current_query

    # Add format instructions if internal knowledge is available
    has_knowledge = bool(state.get("final_results"))
    has_knowledge_tool = False
    if state.get("all_tool_results"):
        for tool_result in state["all_tool_results"]:
            if tool_result.get("tool_name") == "internal_knowledge_retrieval":
                has_knowledge_tool = True
                break

    if has_knowledge or has_knowledge_tool:
        query_with_context += "\n\n**⚠️ CRITICAL: Internal Knowledge is Available - MANDATORY Instructions:**\n"
        query_with_context += "\n"
        query_with_context += "1. **ANSWER DIRECTLY**: Provide the answer to the user's question. DO NOT say 'I searched', 'I found', 'The tool returned', etc.\n"
        query_with_context += "2. **CITE YOUR SOURCES**: Use inline citations [R1-1] IMMEDIATELY after each factual claim (not at end of paragraph).\n"
        query_with_context += "3. **BE COMPREHENSIVE**: Provide detailed, thorough answers with all relevant information.\n"
        query_with_context += "4. **USE MARKDOWN**: Format with headers, lists, tables, bold as appropriate.\n"
        query_with_context += "\n"
        query_with_context += "**Required JSON Output Format:**\n"
        query_with_context += "```json\n"
        query_with_context += "{\n"
        query_with_context += '  "answer": "Detailed answer with inline citations [R1-1][R2-3] after each claim.",\n'
        query_with_context += '  "reason": "Brief explanation of how you derived the answer from the blocks",\n'
        query_with_context += '  "confidence": "Very High | High | Medium | Low",\n'
        query_with_context += '  "answerMatchType": "Derived From Blocks",\n'
        query_with_context += '  "blockNumbers": ["R1-1", "R1-2", "R2-3"]\n'
        query_with_context += "}\n"
        query_with_context += "```\n"
        query_with_context += "\n"
        query_with_context += "⚠️ IMPORTANT:\n"
        query_with_context += "- Do NOT include 'citations' field (system handles it)\n"
        query_with_context += "- Include ALL referenced block numbers in blockNumbers array\n"
        query_with_context += "- Answer the user's question directly without meta-commentary\n"
        query_with_context += "- Citations format: [R1-1][R2-3] NOT [R1-1, R2-3]\n"
        query_with_context += "- Put citation IMMEDIATELY after the fact, not at end of sentence\n"

    messages.append(HumanMessage(content=query_with_context))

    return messages


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

    # Check for markdown code blocks
    if "```json" in content or (content.startswith("```") and "```" in content[3:]):
        try:
            from app.utils.streaming import extract_json_from_string
            parsed = extract_json_from_string(content)
            if isinstance(parsed, dict) and "answer" in parsed:
                return "structured", parsed
        except (ValueError, Exception):
            pass

    # Try regular JSON parsing
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

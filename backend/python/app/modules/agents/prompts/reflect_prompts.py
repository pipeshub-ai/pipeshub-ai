"""
Reflection node system prompts.

The reflect node analyzes tool execution results and decides
whether to retry, continue, or respond.
"""

REFLECT_PROMPT = """Analyze tool execution results and decide next action.

## Execution Results
{execution_summary}

## User Query
{query}

## Status
- Retry: {retry_count}/{max_retries}
- Iteration: {iteration_count}/{max_iterations}

## Decision Options

1. **respond_success** - Task completed successfully
   - Use when: Tools succeeded AND task is complete
   - Example: User asked to "get tickets", tickets retrieved

2. **respond_error** - Unrecoverable error
   - Use when: Permissions issue, resource not found, rate limit
   - Example: 403 Forbidden, 404 Not Found

3. **respond_clarify** - Need user input
   - Use when: Ambiguous query, missing critical info
   - Example: Unbounded JQL after retry

4. **retry_with_fix** - Fixable error, retry possible
   - Use when: Syntax error, type error, correctable mistake
   - Example: Wrong parameter type, invalid JQL syntax

5. **continue_with_more_tools** - Need more steps
   - Use when: Tools succeeded but task incomplete
   - Example: User asked to "create and comment", only created

## Task Completion Check

**Complete** if:
- User asked to "get/list" AND we got data → respond_success
- User asked to "create" AND we created → respond_success
- All requested actions done → respond_success

**Incomplete** if:
- User asked to "create and comment" but only created → continue_with_more_tools
- User asked to "update" but only retrieved data → continue_with_more_tools
- Task has multiple parts and not all done → continue_with_more_tools
- User asked for "conversation history" / "messages between X and Y" / "last N days" but only search results were returned → continue_with_more_tools (need slack.get_channel_history)
- User asked for "complete" / "all" / "entire" list but only got partial results (e.g., 20 items from search) → continue_with_more_tools (need full fetch or pagination)

## Common Error Fixes
- "Unbounded JQL" → Add `AND updated >= -30d`
- "User not found" → Call `jira.search_users` first
- "Invalid type" → Check parameter types, convert if needed
- "Space ID type error" → Call `confluence.get_spaces` to get numeric ID
- "Used slack.search_all for conversation history" → Use `slack.get_channel_history` instead
- "Told user to call a tool" → Continue with the tool yourself (continue_with_more_tools)

## Handling Empty/Null Results

### When Search Returns Empty

**Pattern**: `{{"results": []}}` or `{{"data": []}}`

**Decision Logic:**
1. Check if content was in conversation history → respond_success with conversation data
2. Check if task was "search" → respond_success (found nothing is valid result)
3. Check if task needs content → respond_clarify (ask for correct name/location)

**Example:**
- Search for "Page X" → empty results
- BUT user just discussed "Page X" in previous message
- → respond_success and use conversation content

### Empty Result Recovery
```json
{{
  "decision": "respond_success",
  "reasoning": "Search returned empty but content exists in conversation history",
  "task_complete": true
}}
```

**When to use conversation context:**
- Search returned empty results
- BUT previous assistant message contains the information user needs
- User is referencing content that was just displayed
- → respond_success and let respond_node extract from conversation

**When to clarify:**
- Search returned empty results
- No conversation history with relevant content
- User provided specific name/location that doesn't exist
- → respond_clarify to ask for correct information

## Output (JSON only)
{{
  "decision": "respond_success|respond_error|respond_clarify|retry_with_fix|continue_with_more_tools",
  "reasoning": "Brief explanation",
  "fix_instruction": "For retry: what to change",
  "clarifying_question": "For clarify: what to ask",
  "error_context": "For error: user-friendly explanation",
  "task_complete": true/false,
  "needs_more_tools": "What tools needed next (if continue)"
}}"""

"""Cross-service workflow-pattern examples for the ReAct/planner prompt,
extracted from `modules/agents/qna/nodes.py` (Phase 0 of the agent-loop
migration).
"""


from __future__ import annotations

from app.modules.agents.context.connector_detection import (
    _has_confluence_tools,
    _has_outlook_tools,
    _has_slack_tools,
    _has_teams_tools,
)
from app.modules.agents.qna.chat_state import ChatState


def _build_workflow_patterns(state: ChatState) -> str:
    """
    Build multi-step workflow patterns based on available toolsets.

    Returns cross-service patterns only when the relevant toolsets are both
    active, so the prompt stays lean for single-service agents.
    """
    patterns = []

    has_outlook = _has_outlook_tools(state)
    has_confluence = _has_confluence_tools(state)
    has_slack = _has_slack_tools(state)
    has_teams = _has_teams_tools(state)

    if has_outlook and has_confluence:
        patterns.append("""### Cross-Service Pattern: Recurring Meetings + Holiday Exclusions

When user asks to extend/create recurring meetings and skip holidays/weekends:

**Step 1 — Find or create the recurring event:**
- For EXTEND: Use `outlook.get_recurring_events_ending` or `outlook.search_calendar_events_in_range`
  to find the event. Extract the `seriesMasterId` (or `id` if type is `seriesMaster`).
  Check conversation history first — if the event was recently displayed, use that data directly.
- For CREATE: Use `outlook.create_calendar_event` with the recurrence pattern.

**Step 2 — Update recurrence end date (extend only):**
`outlook.update_calendar_event(event_id=<seriesMasterId>, recurrence={pattern: <keep_existing>, range: {type: "endDate", startDate: "<original_start>", endDate: "<new_end_date>"}})`
Preserve the EXISTING pattern — only change `range.endDate`.
After updating, verify: re-fetch the event and confirm the end date changed.

**Step 3 — Get holiday information from Confluence:**
Use `confluence.search_content(query="Holidays <year>")` to find holiday pages.
If no results, try: "Company holidays <year>", "Holiday calendar <year>", "holidays", "holiday calendar".
When found, use `confluence.get_page_content(page_id=...)` to read the FULL page content.

**Step 4 — Parse holiday dates from page content (USE THE TOOL):**
Call `date_calculator.parse_holiday_dates(text=<raw_page_content>, year=<year>)`
This extracts ALL dates from the page deterministically. Do NOT parse dates manually.
The tool returns a clean list of YYYY-MM-DD strings.

**Step 5 — Compute ALL exclusion dates (USE THE TOOL):**
Call `date_calculator.get_exclusion_dates(
    start_date=<today or day after old end date>,
    end_date=<series end date>,
    holiday_dates=<list from Step 4>
)`
This returns the COMPLETE deduplicated list of weekends + holidays. Do NOT compute dates manually.
The tool returns `exclusion_dates` (the list) and `breakdown` (counts for the summary).

**Step 6 — Delete excluded occurrences:**
For each event, call:
`outlook.delete_recurring_event_occurrence(
    event_id=<seriesMasterId>,
    occurrence_dates=<exclusion_dates from Step 5>,
    timezone=<user_timezone>
)`
Pass the ENTIRE `exclusion_dates` list from Step 5 directly. ONE call per event.

**CRITICAL RULES:**
- ALWAYS use `date_calculator.parse_holiday_dates` for extracting dates from Confluence pages.
- ALWAYS use `date_calculator.get_exclusion_dates` for computing the exclusion list.
- NEVER enumerate weekend or holiday dates manually — the tools are deterministic and complete.
- The `seriesMasterId` is the ID needed for update/delete operations on recurring series.
- Always use the user's timezone for the `timezone` parameter.
- Do NOT ask the user for event IDs — resolve them from search results or conversation history.
""")

    if has_outlook:
        patterns.append("""### Pattern: Extend a Recurring Event

1. Find the event: check conversation history first, or use
   `outlook.get_recurring_events_ending(end_before="...", timezone="...")` or
   `outlook.search_calendar_events_in_range(keyword="event name", ...)`
2. Get the series master ID from results (`seriesMasterId` or `id` of seriesMaster type).
3. Get the current recurrence pattern from the event result.
4. Update recurrence end date:
   `outlook.update_calendar_event(event_id=<master_id>, recurrence={pattern: <existing_pattern>, range: {type: "endDate", startDate: "<original_start>", endDate: "<NEW_END_DATE>"}})`
5. Preserve the EXISTING pattern — only change the range endDate.
6. Run post-action cleanup: search holidays on Confluence, compute exclusion dates using
   `date_calculator.get_exclusion_dates`, delete occurrences with `delete_recurring_event_occurrence`.

### Pattern: Create a Recurring Event + Cleanup

1. Create the event with `outlook.create_calendar_event` including the recurrence pattern.
2. Search Confluence for company holidays.
3. Parse holidays: `date_calculator.parse_holiday_dates(text=<page_content>, year=<year>)`
4. Compute exclusions: `date_calculator.get_exclusion_dates(start_date=<today>, end_date=<series_end>, holiday_dates=<holidays>)`
5. Delete occurrences: `outlook.delete_recurring_event_occurrence(event_id=<id>, occurrence_dates=<exclusion_dates>)`
""")

    if has_teams and has_slack:
        patterns.append("""### Cross-Service Pattern: Meeting Transcript → Summary → Slack

When user asks to summarize meeting(s) and send to Slack:

**Step 1 — Collect requirements (BEFORE any tool calls):**
Ensure you have:
- Which meeting(s): date range, name/keyword, or "all from [date]"
- Slack target: channel name or user (ALWAYS ask if not specified)
- Summary focus (optional): "action items", "decisions", "full summary"
If anything is missing, ask for ALL missing items in one message.

**Step 2 — Fetch meetings:**
Use `teams.get_meetings` (by date) or `teams.search_calendar_events_in_range` (by keyword).
Extract `id` and `joinUrl` from each result.

**Step 3 — Fetch transcripts:**
For each meeting, call `teams.get_meeting_transcript(event_id=..., joinUrl=...)`.
If a meeting has no transcript, note it — do NOT skip silently.

**Step 4 — Generate summary (LLM task, not a tool call):**
YOU write the summary from the transcript. Include:
- Meeting title + date
- Attendees
- Key discussion points
- Decisions made
- Action items (who, what, deadline)
- Open questions
Format for Slack mrkdwn: *bold*, _italic_, • bullets.

**Step 5 — Send to Slack:**
Call `slack.send_message(channel="...", message="<your summary>")`.
NEVER pass raw transcript as the message — always your generated summary.
For multiple meetings, either send one message per meeting or one combined message.

**Step 6 — Confirm:**
Brief report: which meetings summarized, where sent, any without transcripts.
""")

    if not patterns:
        return ""

    return "## Multi-Step Workflow Patterns\n\n" + "\n".join(patterns)


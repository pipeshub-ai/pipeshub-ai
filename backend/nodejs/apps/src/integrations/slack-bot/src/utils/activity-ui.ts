/** Tool / sub-agent rows of the activity messages earlier versions posted. */
const ACTIVITY_TOOL_LINE = /^•\s+(?:[✓✗]\s+\S.*|.+\.\.\.)$/;
/** Live status line, e.g. `_Thinking..._` / `_Load Skill..._`. */
const ACTIVITY_STATUS_LINE = /^_.+\.\.\._$/;
const ACTIVITY_THINKING_HEADER = /^\*Thinking\*$/;

function hasActivityMarkers(text: string): boolean {
  return text.split("\n").some((line) => {
    const trimmed = line.trim();
    return (
      ACTIVITY_TOOL_LINE.test(trimmed) ||
      ACTIVITY_STATUS_LINE.test(trimmed) ||
      ACTIVITY_THINKING_HEADER.test(trimmed)
    );
  });
}

/**
 * Removes activity-timeline content (tools, thinking, narration, status) so
 * prior bot messages are not re-injected into Slack thread context.
 * Keeps ask-user questions and other non-activity bot text.
 */
export function stripSlackActivityTimeline(text: string): string {
  if (!text) {
    return "";
  }

  // Activity messages are dedicated timeline posts (answer is separate).
  // Narration is plain text between tool rows, so marker presence means the
  // whole message is activity and should be dropped from thread context.
  if (hasActivityMarkers(text)) {
    return "";
  }

  return text.trim();
}

/** Backend may leave a Confidence trailer on narration flushed before a tool. */
const CONFIDENCE_TRAILER_RE =
  /(?:\n*-{3,}\s*\n)?\s*Confidence:\s*(?:Very High|High|Medium|Low)\s*$/i;

export function cleanNarration(text: string): string {
  return text.replace(CONFIDENCE_TRAILER_RE, "").trim();
}

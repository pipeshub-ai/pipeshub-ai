import { markdownToSlackMrkdwn } from "./md_to_mrkdwn";

const MAX_REASONING_CHARS = 600;
const MAX_NARRATION_CHARS = 800;
const MAX_ACTIVITY_TEXT_CHARS = 2900;

/** Tool / sub-agent rows from {@link SlackActivityBuilder.format}. */
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

export interface ActivityToolRow {
  toolName: string;
  label: string;
  done: boolean;
  failed?: boolean;
}

function toolDisplayLabel(row: ActivityToolRow): string {
  return row.label.replace(/^Using\s+/i, "");
}

function formatToolCountLine(
  marker: "running" | "success" | "failed",
  label: string,
  count: number,
): string {
  const suffix = count > 1 ? ` (×${count})` : "";
  if (marker === "running") {
    return `• ${label}${suffix}...`;
  }
  if (marker === "failed") {
    return `• ✗ ${label}${suffix}`;
  }
  return `• ✓ ${label}${suffix}`;
}

/**
 * Collapse consecutive same-tool rows, but keep success and failure as
 * separate lines so a mixed group does not look entirely failed.
 */
function formatDedupedToolLines(tools: ActivityToolRow[]): string[] {
  const lines: string[] = [];
  let i = 0;
  while (i < tools.length) {
    const first = tools[i];
    if (!first) break;
    const toolName = first.toolName;
    let j = i + 1;
    while (j < tools.length && tools[j]?.toolName === toolName) {
      j += 1;
    }
    const group = tools.slice(i, j);
    const displayLabel =
      toolDisplayLabel(group.find((t) => t.done) ?? first);

    const running = group.filter((t) => !t.done);
    const failed = group.filter((t) => t.done && t.failed);
    const succeeded = group.filter((t) => t.done && !t.failed);

    if (succeeded.length > 0) {
      lines.push(formatToolCountLine("success", displayLabel, succeeded.length));
    }
    if (failed.length > 0) {
      lines.push(formatToolCountLine("failed", displayLabel, failed.length));
    }
    if (running.length > 0) {
      lines.push(formatToolCountLine("running", displayLabel, running.length));
    }
    i = j;
  }
  return lines;
}

export interface ActivitySubAgentRow {
  role: string;
  status: "running" | "completed" | "failed";
}

type TimelineEntry =
  | { kind: "narration"; text: string }
  | { kind: "tool"; row: ActivityToolRow }
  | { kind: "subagent"; row: ActivitySubAgentRow }
  | { kind: "reasoning"; text: string };

/** Backend may leave a Confidence trailer on narration flushed before a tool. */
const CONFIDENCE_TRAILER_RE =
  /(?:\n*-{3,}\s*\n)?\s*Confidence:\s*(?:Very High|High|Medium|Low)\s*$/i;

function cleanNarration(text: string): string {
  return text.replace(CONFIDENCE_TRAILER_RE, "").trim();
}

function truncateNarration(text: string): string {
  if (text.length <= MAX_NARRATION_CHARS) {
    return text;
  }
  return `${text.slice(0, MAX_NARRATION_CHARS).trimEnd()}…`;
}

/**
 * Builds the Slack activity message body (narration / thinking / tools /
 * sub-agents), updated in place during an AG-UI stream.
 */
export class SlackActivityBuilder {
  private statusMessage = "Thinking...";
  private timeline: TimelineEntry[] = [];
  private openToolName: string | null = null;

  setStatus(message: string): void {
    // Empty string clears the live status line after the run finishes.
    this.statusMessage = message.trim();
  }

  /** Settled preamble text that preceded a tool call (frontend narration). */
  appendNarration(text: string): void {
    const cleaned = cleanNarration(text);
    if (!cleaned) return;
    this.timeline.push({ kind: "narration", text: truncateNarration(cleaned) });
  }

  appendReasoning(delta: string): void {
    if (!delta) return;
    const last = this.timeline[this.timeline.length - 1];
    if (last?.kind === "reasoning") {
      last.text = (last.text + delta).slice(-MAX_REASONING_CHARS * 2);
      return;
    }
    this.timeline.push({ kind: "reasoning", text: delta });
  }

  finishReasoning(): void {
    // Formatting truncates on render.
  }

  startTool(toolName: string, label: string): void {
    this.openToolName = toolName;
    this.timeline.push({
      kind: "tool",
      row: { toolName, label, done: false },
    });
    this.statusMessage = `${label}...`;
  }

  finishTool(toolName: string, label: string, failed = false): void {
    const open =
      [...this.timeline]
        .reverse()
        .find(
          (e): e is Extract<TimelineEntry, { kind: "tool" }> =>
            e.kind === "tool" && !e.row.done && e.row.toolName === toolName,
        ) ??
      (this.openToolName
        ? [...this.timeline]
            .reverse()
            .find(
              (e): e is Extract<TimelineEntry, { kind: "tool" }> =>
                e.kind === "tool" &&
                !e.row.done &&
                e.row.toolName === this.openToolName,
            )
        : undefined) ??
      [...this.timeline]
        .reverse()
        .find(
          (e): e is Extract<TimelineEntry, { kind: "tool" }> =>
            e.kind === "tool" && !e.row.done,
        );

    if (open) {
      open.row.done = true;
      open.row.failed = failed;
      open.row.label = label;
    } else {
      this.timeline.push({
        kind: "tool",
        row: { toolName, label, done: true, failed },
      });
    }
    this.openToolName = null;
    this.statusMessage = "Thinking...";
  }

  startSubAgent(role: string): void {
    this.timeline.push({ kind: "subagent", row: { role, status: "running" } });
    this.statusMessage = `Delegating to ${role}...`;
  }

  finishSubAgent(role: string, status: "completed" | "failed"): void {
    const row =
      [...this.timeline]
        .reverse()
        .find(
          (e): e is Extract<TimelineEntry, { kind: "subagent" }> =>
            e.kind === "subagent" &&
            e.row.role === role &&
            e.row.status === "running",
        ) ??
      [...this.timeline]
        .reverse()
        .find(
          (e): e is Extract<TimelineEntry, { kind: "subagent" }> =>
            e.kind === "subagent" && e.row.status === "running",
        );
    if (row) {
      row.row.status = status;
    }
    this.statusMessage = "Thinking...";
  }

  /** True when there is a tools/thinking/narration/sub-agent timeline worth keeping. */
  hasTimeline(): boolean {
    return this.timeline.length > 0;
  }

  format(): string {
    const sections: string[] = [];
    let i = 0;

    while (i < this.timeline.length) {
      const entry = this.timeline[i];
      if (!entry) break;

      if (entry.kind === "narration") {
        // Model narration is standard Markdown; activity posts use Slack mrkdwn.
        sections.push(markdownToSlackMrkdwn(entry.text));
        i += 1;
        continue;
      }

      if (entry.kind === "reasoning") {
        const reasoning = entry.text.trim();
        if (reasoning) {
          const truncated =
            reasoning.length > MAX_REASONING_CHARS
              ? `${reasoning.slice(0, MAX_REASONING_CHARS).trimEnd()}…`
              : reasoning;
          const converted = markdownToSlackMrkdwn(truncated);
          const italic = converted
            .split("\n")
            .map((line) => (line.trim() ? `_${line.trim()}_` : ""))
            .filter(Boolean)
            .join("\n");
          sections.push(`*Thinking*\n${italic}`);
        }
        i += 1;
        continue;
      }

      if (entry.kind === "tool") {
        const tools: ActivityToolRow[] = [];
        while (i < this.timeline.length && this.timeline[i]?.kind === "tool") {
          const toolEntry = this.timeline[i] as Extract<
            TimelineEntry,
            { kind: "tool" }
          >;
          tools.push(toolEntry.row);
          i += 1;
        }
        sections.push(formatDedupedToolLines(tools).join("\n"));
        continue;
      }

      // subagent
      const lines: string[] = [];
      while (i < this.timeline.length && this.timeline[i]?.kind === "subagent") {
        const subEntry = this.timeline[i] as Extract<
          TimelineEntry,
          { kind: "subagent" }
        >;
        const sub = subEntry.row;
        if (sub.status === "running") {
          lines.push(`• Delegating to ${sub.role}...`);
        } else if (sub.status === "failed") {
          lines.push(`• ✗ ${sub.role}`);
        } else {
          lines.push(`• ✓ ${sub.role}`);
        }
        i += 1;
      }
      if (lines.length > 0) {
        sections.push(lines.join("\n"));
      }
    }

    if (this.statusMessage.trim()) {
      sections.push(`_${this.statusMessage}_`);
    }

    let text = sections.join("\n\n");
    if (!text) {
      text = "_Thinking..._";
    }
    if (text.length > MAX_ACTIVITY_TEXT_CHARS) {
      text = `${text.slice(0, MAX_ACTIVITY_TEXT_CHARS - 1)}…`;
    }
    return text;
  }
}

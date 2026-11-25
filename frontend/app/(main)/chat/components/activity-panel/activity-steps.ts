import type { ActivityTool, MessagePart } from '../../types';
import { isSkillTool } from '../../utils/tool-display';

/**
 * The Activity panel's steps (ACTRESULTS): a run's tool calls in order, with the plumbing a person never asked
 * about folded into one quiet line and a run of the same tool called again and again grouped under one row.
 */

export type StepItem =
  | { kind: 'quiet'; key: string; lookup: 'tools' | 'skills' }
  | { kind: 'step'; key: string; tool: ActivityTool }
  | { kind: 'group'; key: string; tools: ActivityTool[] };

/** The lazy-toolset meta tools (`lazy_tools_wiring.META_TOOL_NAMES`, less `call_tool`, which runs the real one). */
const TOOL_LOOKUPS: ReadonlySet<string> = new Set(['list_toolsets', 'fetch_tools', 'search_tools']);

/** A tool id without its source, toolset or `dynamic__` prefix. */
export function bareToolName(name: string): string {
  const slash = name.lastIndexOf('/');
  const tail = slash >= 0 ? name.slice(slash + 1) : name;
  const sep = tail.lastIndexOf('__');
  return sep >= 0 ? tail.slice(sep + 2) : tail;
}

function lookupOf(tool: ActivityTool): 'tools' | 'skills' | null {
  if (tool.status !== 'completed' && tool.status !== 'running') return null;
  const bare = bareToolName(tool.name);
  if (TOOL_LOOKUPS.has(bare)) return 'tools';
  return isSkillTool(bare) ? 'skills' : null;
}

/** A step that asks something of the person, or opens a child run, stands alone. */
const groupable = (tool: ActivityTool): boolean => tool.status !== 'awaiting_approval' && tool.child === undefined;

export function stepItems(tools: readonly ActivityTool[]): StepItem[] {
  const items: StepItem[] = [];
  for (const tool of tools) {
    const last = items[items.length - 1];
    const lookup = lookupOf(tool);
    if (lookup !== null) {
      if (last?.kind === 'quiet') {
        if (lookup === 'skills') last.lookup = 'skills';
        continue;
      }
      items.push({ kind: 'quiet', key: `quiet-${tool.toolCallId}`, lookup });
      continue;
    }
    if (groupable(tool) && last?.kind === 'group' && last.tools[0]!.name === tool.name) {
      last.tools.push(tool);
      continue;
    }
    if (groupable(tool) && last?.kind === 'step' && groupable(last.tool) && last.tool.name === tool.name) {
      items[items.length - 1] = { kind: 'group', key: `group-${last.tool.toolCallId}`, tools: [last.tool, tool] };
      continue;
    }
    items.push({ kind: 'step', key: tool.toolCallId, tool });
  }
  return items;
}

const PART_STATUS: Record<ActivityTool['status'], NonNullable<MessagePart['status']>> = {
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  awaiting_approval: 'awaiting_approval',
  denied: 'blocked',
  blocked: 'blocked',
};

/** A step as the chat's own `tool_call` part, so the chat's renderer draws its arguments and result. */
export function partOfTool(tool: ActivityTool): MessagePart {
  return {
    type: 'tool_call',
    toolCallId: tool.toolCallId,
    toolName: tool.name,
    status: PART_STATUS[tool.status],
    ...(tool.displayName ? { displayName: tool.displayName } : {}),
    ...(tool.args !== undefined ? { args: tool.args } : {}),
    ...(tool.argsSummary ? { argsSummary: tool.argsSummary } : {}),
    ...(tool.step ? { step: tool.step } : {}),
    ...(tool.skills ? { skills: tool.skills } : {}),
    ...(tool.resultPreview !== undefined ? { resultPreview: tool.resultPreview } : {}),
    ...(tool.resultSummary ? { resultSummary: tool.resultSummary } : {}),
    ...(tool.resultView !== undefined ? { resultView: tool.resultView } : {}),
    ...(tool.artifactId ? { artifactId: tool.artifactId } : {}),
    ...(tool.durationMs !== undefined ? { durationMs: tool.durationMs } : {}),
    ...(tool.failure ? { errorCode: tool.failure.code } : {}),
    ...(tool.approvalAnswer ? { approvalAnswer: tool.approvalAnswer } : {}),
  };
}

/**
 * An engine run's steps: its tool calls from its own timeline (`useRunSteps`), then the held asks the activity
 * lists that the timeline does not name. A call that waited for an ask is one step with it (FIX-TOOLWORDS): the ask's
 * status while it waits or was refused, and how it was answered. Commands and files have their own sections.
 */
export function engineRunTools(parts: readonly MessagePart[], held: readonly ActivityTool[]): ActivityTool[] {
  const asks = new Map(held.map((tool) => [tool.toolCallId, tool] as const));
  const merged = new Set<string>();
  const own = parts.flatMap((part) => {
    const tool = toolOfPart(part);
    if (!tool) return [];
    const ask = part.interactionId ? asks.get(part.interactionId) : undefined;
    if (!ask) return [tool];
    merged.add(ask.toolCallId);
    return [withAsk(tool, ask)];
  });
  const named = new Set(own.map((tool) => tool.toolCallId));
  return [...own, ...held.filter((tool) => !named.has(tool.toolCallId) && !merged.has(tool.toolCallId))];
}

function withAsk(tool: ActivityTool, ask: ActivityTool): ActivityTool {
  const open = ask.status === 'awaiting_approval' || ask.status === 'denied' || ask.status === 'blocked';
  return {
    ...tool,
    ...(open ? { status: ask.status } : {}),
    ...(tool.displayName || !ask.displayName ? {} : { displayName: ask.displayName }),
    ...(ask.approvalAnswer ? { approvalAnswer: ask.approvalAnswer } : {}),
    ...(ask.reason ? { reason: ask.reason } : {}),
  };
}

/** A child run's `tool_call` part as a step, so its steps draw like the run's own. */
export function toolOfPart(part: MessagePart): ActivityTool | null {
  if (part.type !== 'tool_call' || !part.toolCallId) return null;
  const status: ActivityTool['status'] =
    part.status === 'completed' || part.status === 'failed' || part.status === 'awaiting_approval' || part.status === 'blocked'
      ? part.status
      : 'running';
  return {
    toolCallId: part.toolCallId,
    name: part.toolName ?? '',
    status,
    ...(part.displayName ? { displayName: part.displayName } : {}),
    ...(part.args !== undefined ? { args: part.args } : {}),
    ...(part.argsSummary ? { argsSummary: part.argsSummary } : {}),
    ...(part.step ? { step: part.step } : {}),
    ...(part.skills ? { skills: part.skills } : {}),
    ...(part.resultPreview !== undefined ? { resultPreview: part.resultPreview } : {}),
    ...(part.resultSummary ? { resultSummary: part.resultSummary } : {}),
    ...(part.resultView !== undefined ? { resultView: part.resultView } : {}),
    ...(part.durationMs !== undefined ? { durationMs: part.durationMs } : {}),
    ...(part.errorCode ? { failure: { code: part.errorCode } } : {}),
    ...(part.approvalAnswer ? { approvalAnswer: part.approvalAnswer } : {}),
  };
}

const QUOTED = /["“]([^"”\n]{1,120})["”]/;
const URL_IN = /\bhttps?:\/\/[^\s"'<>]+/;

/** What the step was about, from its argument summary: the quoted query, else the page it read. */
export function stepSubject(argsSummary: string | undefined): string | undefined {
  const line = argsSummary?.split('\n')[0]?.trim();
  if (!line) return undefined;
  const quoted = QUOTED.exec(line)?.[1]?.trim();
  if (quoted) return quoted;
  const url = URL_IN.exec(line)?.[0];
  return url ? url.replace(/^https?:\/\//, '').replace(/\/$/, '').slice(0, 120) : undefined;
}

const EMPTY_RESULTS = new Set(['', '[]', '{}', 'null', '""']);

export type StepOutcome =
  | { kind: 'summary'; text: string }
  | { kind: 'found'; count: number }
  | { kind: 'empty' }
  | { kind: 'done' };

/** A completed step's outcome in a line: the server's summary, else what its view or preview says. */
export function completedOutcome(tool: ActivityTool, viewTotal: number | null): StepOutcome {
  const summary = tool.resultSummary
    ?.split('\n')
    .map((line) => line.replace(/^[-*]\s+/, '').trim())
    .find(Boolean);
  if (summary) return { kind: 'summary', text: summary.length > 160 ? `${summary.slice(0, 159)}…` : summary };
  if (viewTotal !== null) return { kind: 'found', count: viewTotal };
  if (tool.resultPreview !== undefined && EMPTY_RESULTS.has(tool.resultPreview.trim())) return { kind: 'empty' };
  return { kind: 'done' };
}

/**
 * How a job's goal changed since it started (ADR-UI-01 D5, UI-B5 UIP2-FU-4), from its own journal: the messages
 * added to it and the choices its person made when it asked first, in the order they happened. Access the person
 * allowed is not a change of goal (the brief's "sent" says it); the jobs the coordinator re-planned it into are read
 * from the conversation's runs, not from this journal (`JobBriefService.history`).
 */
import {
  describeTool,
  toolKey,
  type ToolKey,
} from '../../../integrations/slack-bot/src/utils/tool-display';
import type { JournalEvent } from '../domain/journal';
import { foldTimeline } from './timeline';

export type JobGoalChange =
  | { kind: 'message'; at: number; text: string }
  | {
      kind: 'choice';
      at: number;
      decision: 'approved' | 'denied';
      tool: string;
      action: string;
      app?: string;
      actionKey: ToolKey;
    }
  | { kind: 'replanned'; at: number; runId: string; goal: string | null };

/** Longest message text the history carries, as the timeline's own input view keeps it. */
const MAX_TEXT = 10_000;

const millis = (ts: string | undefined): number => {
  const at = ts === undefined ? NaN : Date.parse(ts);
  return Number.isFinite(at) ? at : 0;
};

/** The job's own changes, oldest first: what its journal says was added to it and decided for it. */
export function jobGoalChanges(
  events: readonly JournalEvent[],
  runId: string,
): JobGoalChange[] {
  const timeline = foldTimeline(events, runId);
  const queuedAt = new Map<number, number>();
  const resolvedAt = new Map<string, number>();
  for (const event of events) {
    if (event.runId !== runId) continue;
    const data = event.data ?? {};
    if (
      event.type === 'input.queued' &&
      typeof data['messageSeq'] === 'number'
    ) {
      if (!queuedAt.has(data['messageSeq'])) {
        queuedAt.set(data['messageSeq'], millis(event.ts));
      }
    } else if (
      event.type === 'interaction.resolved' &&
      typeof data['interactionId'] === 'string'
    ) {
      resolvedAt.set(data['interactionId'], millis(event.ts));
    }
  }
  const messages: JobGoalChange[] = timeline.inputs.map((input) => ({
    kind: 'message',
    at: queuedAt.get(input.messageSeq) ?? 0,
    text: input.text.slice(0, MAX_TEXT),
  }));
  const choices: JobGoalChange[] = timeline.approvals.flatMap((approval) => {
    if (approval.status !== 'approved' && approval.status !== 'denied')
      return [];
    const described = describeTool(approval.toolName, approval.displayName);
    return [
      {
        kind: 'choice' as const,
        at: resolvedAt.get(approval.interactionId) ?? 0,
        decision: approval.status,
        tool: approval.toolName,
        action: described.name,
        ...(described.app !== undefined ? { app: described.app } : {}),
        actionKey: toolKey(approval.toolName),
      },
    ];
  });
  return sortChanges([...messages, ...choices]);
}

/** Oldest first; a tie keeps the order given (a message before the choice it came with). */
export const sortChanges = (
  changes: readonly JobGoalChange[],
): JobGoalChange[] =>
  changes
    .map((change, index) => ({ change, index }))
    .sort((a, b) => a.change.at - b.change.at || a.index - b.index)
    .map(({ change }) => change);

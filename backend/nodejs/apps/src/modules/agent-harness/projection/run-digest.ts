/**
 * The end-of-run summary (G-07, GA-05): what a terminated run did, folded
 * from its journal alone. No model writes it, so a reload, a notice and a
 * second client read the same facts, and a run that ended with no answer
 * (failed, cancelled) still has one.
 */
import type { CutShortReason } from '../contracts/generated/core/enums';
import type { BudgetAmount } from '../domain/budget-ledger';
import type { JournalEvent } from '../domain/journal';
import { ActivityFold, ActivityView } from './activity';
import { checkedSources, sourceKey } from './citation-source';
import { savedArtifacts } from './job-files';
import { RunEnd, Timeline } from './timeline';

export const MAX_DIGEST_TOOLS = 10;
export const MAX_DIGEST_ARTIFACTS = 10;

export type DigestOutcome = 'answered' | 'needs_input' | 'failed' | 'cancelled';

export type EndedRunStatus = 'completed' | 'failed' | 'cancelled';

/** One tool the run used and how often, as the activity panel names it. */
export interface DigestTool {
  name: string;
  displayName?: string;
  count: number;
}

export interface DigestArtifact {
  name: string;
  /** The saved file's record, for a link; absent for a file the run kept in its workspace only. */
  recordId?: string;
  /** What its card shows (BGUI-11: the report and the Outputs tab draw the answer's own file card). */
  mimeType?: string;
  sizeBytes?: number;
  artifactType?: string;
  version?: number;
}

export interface RunDigest {
  outcome: DigestOutcome;
  /** Which limit cut the run's work short (BUDGETEND). */
  cutShortBy?: CutShortReason;
  actions: {
    total: number;
    failed: number;
    /** Stopped by a rule or a person's denial. */
    refused: number;
    /** Most used first, at most `MAX_DIGEST_TOOLS`. */
    tools: DigestTool[];
  };
  commands: { total: number; failed: number };
  /** Records it cited (read) and calls it completed that change something outside the run (changed). */
  records: { read: number; changed: number };
  /** Files it saved, at most `MAX_DIGEST_ARTIFACTS`, in the order it made them. */
  artifacts: DigestArtifact[];
  /** Every file it changed; `working`: those it did not deliver as an artifact (`job-files.ts`). */
  files: { added: number; modified: number; deleted: number; working: number };
  approvals: {
    asked: number;
    approved: number;
    denied: number;
    /** Never decided: expired, or closed when the run ended. */
    unanswered: number;
  };
  /** Questions it asked the person; `open` when the run ended waiting on one. */
  questions: { asked: number; open: boolean };
  delegated: number;
  /** The run's own committed spend, counting its children; null without a ledger account. */
  cost: BudgetAmount | null;
  /** Part of `cost` is the gateway's estimate of a call stopped early, not yet replaced by the provider's (WP-66). */
  costEstimated: boolean;
  durationMs: number | null;
}

const FAILED_COMMANDS: ReadonlySet<string> = new Set(['failed', 'timed_out']);

function outcomeOf(
  end: RunEnd | null,
  status: EndedRunStatus,
): Pick<RunDigest, 'outcome' | 'cutShortBy'> {
  if (end === null) {
    return { outcome: status === 'completed' ? 'answered' : status };
  }
  switch (end.status) {
    case 'completed':
      return {
        outcome: end.outcome,
        ...(end.truncatedBy !== undefined
          ? { cutShortBy: end.truncatedBy }
          : {}),
      };
    case 'cancelled':
      return { outcome: 'cancelled' };
    case 'failed':
    case 'quarantined':
    default:
      return { outcome: 'failed' };
  }
}

function actionsOf(activity: ActivityView): RunDigest['actions'] {
  const tools = new Map<string, DigestTool>();
  let failed = 0;
  let refused = 0;
  for (const tool of activity.tools) {
    if (tool.status === 'failed') failed += 1;
    if (tool.status === 'blocked' || tool.status === 'denied') refused += 1;
    const seen = tools.get(tool.name);
    if (seen !== undefined) {
      seen.count += 1;
      continue;
    }
    tools.set(tool.name, {
      name: tool.name,
      ...(tool.displayName !== undefined
        ? { displayName: tool.displayName }
        : {}),
      count: 1,
    });
  }
  return {
    total: activity.tools.length,
    failed,
    refused,
    tools: [...tools.values()]
      .sort((a, b) => b.count - a.count)
      .slice(0, MAX_DIGEST_TOOLS),
  };
}

function approvalsOf(timeline: Timeline): RunDigest['approvals'] {
  const counts = { asked: 0, approved: 0, denied: 0, unanswered: 0 };
  for (const approval of timeline.approvals) {
    counts.asked += 1;
    if (approval.status === 'approved') counts.approved += 1;
    else if (approval.status === 'denied') counts.denied += 1;
    else counts.unanswered += 1;
  }
  return counts;
}

/**
 * The records it cited, and the calls declared `write` (a change outside the
 * run: an issue filed, a message sent) that succeeded and were made: a dry
 * run's shadowed write is not a change. `execute` (code in its
 * sandbox) and `workspace` effects stay in the run, so they are not changes.
 */
function recordsOf(
  timeline: Timeline,
  events: readonly JournalEvent[],
  runId: string,
): RunDigest['records'] {
  const read = new Set(
    checkedSources(timeline.answer?.citations ?? []).map((source) =>
      source.kind === 'live' ? sourceKey(source) : `record:${source.recordId}`,
    ),
  );
  const writes = new Set<string>();
  const changed = new Set<string>();
  for (const event of events) {
    if (event.runId !== runId || event.data === undefined) continue;
    const invocation = event.data['invocationId'];
    if (typeof invocation !== 'string') continue;
    if (event.type === 'tool.intent' && event.data['effect'] === 'write') {
      writes.add(invocation);
    } else if (
      event.type === 'tool.outcome' &&
      event.data['status'] === 'ok' &&
      // A dry run's call (G-03) is recorded as what it would have done: it changed nothing.
      event.data['shadow'] === undefined &&
      writes.has(invocation)
    ) {
      changed.add(invocation);
    }
  }
  return { read: read.size, changed: changed.size };
}

function artifactsOf(timeline: Timeline): DigestArtifact[] {
  const artifacts: DigestArtifact[] = [];
  const seen = new Set<string>();
  for (const artifact of savedArtifacts(timeline.artifacts)) {
    const key = artifact.recordId ?? artifact.artifactId;
    if (seen.has(key)) continue;
    seen.add(key);
    artifacts.push({
      name: artifact.fileName,
      ...(artifact.recordId !== undefined
        ? { recordId: artifact.recordId }
        : {}),
      ...(artifact.mimeType !== '' &&
      artifact.mimeType !== 'application/octet-stream'
        ? { mimeType: artifact.mimeType }
        : {}),
      ...(artifact.sizeBytes > 0 ? { sizeBytes: artifact.sizeBytes } : {}),
      ...(artifact.artifactType !== ''
        ? { artifactType: artifact.artifactType }
        : {}),
      ...(artifact.version !== undefined ? { version: artifact.version } : {}),
    });
  }
  return artifacts.slice(0, MAX_DIGEST_ARTIFACTS);
}

function durationOf(timeline: Timeline): number | null {
  if (timeline.startedAt === undefined || timeline.endedAt === undefined) {
    return null;
  }
  const took = Date.parse(timeline.endedAt) - Date.parse(timeline.startedAt);
  return Number.isFinite(took) && took >= 0 ? took : null;
}

/**
 * The summary of a run that ended with `status`: its journal's own terminal
 * event, when there is one, decides the outcome.
 */
export function foldRunDigest(
  events: readonly JournalEvent[],
  runId: string,
  status: EndedRunStatus,
  cost: BudgetAmount | null,
  costEstimated = false,
): RunDigest {
  const fold = new ActivityFold({ runId });
  for (const event of events) fold.apply(event);
  const activity = fold.view(status);
  const { timeline } = fold;
  const files = { added: 0, modified: 0, deleted: 0, working: 0 };
  // The activity view marks what was delivered (`job-files.ts`), as the panel's Outputs tab reads it.
  for (const file of activity.files) {
    files[file.change] += 1;
    if (file.delivered !== true) files.working += 1;
  }
  const end = outcomeOf(timeline.end, status);
  return {
    ...end,
    actions: actionsOf(activity),
    commands: {
      total: activity.commands.length + activity.omittedCommands,
      failed: activity.commands.filter((c) => FAILED_COMMANDS.has(c.status))
        .length,
    },
    records: recordsOf(timeline, events, runId),
    artifacts: artifactsOf(timeline),
    files,
    approvals: approvalsOf(timeline),
    questions: {
      asked: timeline.questions.length,
      open: end.outcome === 'needs_input',
    },
    delegated: timeline.children.length,
    cost: cost === null ? null : { ...cost },
    costEstimated: cost !== null && costEstimated,
    durationMs: durationOf(timeline),
  };
}

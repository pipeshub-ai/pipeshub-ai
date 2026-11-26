/**
 * Journal history checks (TEST-STRATEGY §2.14, JRN-22): the rules of
 * `harness_core.testing.history`, numbered the same, plus H7 for durable v1
 * `agentEvents`. H8 (EV-06): every tool call ends with exactly one outcome,
 * a `tool.outcome` or a `tool.unknown`, by its run's end. A sealed or
 * claim-checked event's data is not read: its envelope is (a claim's fence is
 * its envelope's).
 */
import type { HarnessEvent } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';

export interface HistoryViolation {
  rule: 'H1' | 'H2' | 'H3' | 'H4' | 'H5' | 'H6' | 'H7' | 'H8';
  stream: string;
  seq: number;
  runId: string | null;
  message: string;
}

export const TERMINAL_EVENTS: ReadonlySet<string> = new Set([
  'run.completed',
  'run.failed',
  'run.cancelled',
  'run.quarantined',
]);

interface RunHistory {
  events: number;
  admitted: boolean;
  pinned: boolean;
  fence: number | null;
  intents: Set<string>;
  endedAt: number | null;
  /** H8: each opened call and whether it has ended; the calls with an outcome. */
  calls: Map<string, boolean>;
  outcomes: Set<string>;
}

type Broken = [HistoryViolation['rule'], string];

/** What admission journals with an imported history, ahead of the run's `run.admitted`. */
const ADMISSION_PREFIX: ReadonlySet<string> = new Set([
  'run.admitted',
  'session.imported',
  'x.pipeshub.history.labelled',
]);

const dataOf = (event: JournalEvent): Record<string, unknown> | undefined =>
  event.data;

function runRules(event: JournalEvent, run: RunHistory): Broken[] {
  const broken: Broken[] = [];
  const data = dataOf(event);
  if (!run.admitted && !ADMISSION_PREFIX.has(event.type)) {
    broken.push(['H2', `the run starts with ${event.type}, not run.admitted`]);
  }
  if (run.endedAt !== null) {
    broken.push(['H6', `${event.type} after the run ended at seq ${String(run.endedAt)}`]);
  }
  if (event.type === 'run.spec_pinned') {
    if (run.pinned) broken.push(['H3', 'a second run.spec_pinned']);
    run.pinned = true;
  } else if (event.writer === 'worker' && !run.pinned) {
    broken.push(['H3', `worker event ${event.type} before run.spec_pinned`]);
  }
  if (event.type === 'run.claimed') {
    const claimed = typeof data?.['fence'] === 'number' ? data['fence'] : event.fence;
    if (typeof claimed !== 'number' || (run.fence !== null && claimed <= run.fence)) {
      broken.push(['H4', `claim fence ${String(claimed)} does not rise above ${String(run.fence)}`]);
    }
    if (typeof claimed === 'number') run.fence = claimed;
  }
  if (event.fence !== undefined && event.fence !== run.fence) {
    broken.push([
      'H4',
      `${event.type} carries fence ${String(event.fence)}, the current claim's is ${String(run.fence)}`,
    ]);
  }
  const invocation = data?.['invocationId'];
  if ((event.type === 'tool.intent' || event.type === 'tool.denied') && typeof invocation === 'string') {
    run.intents.add(invocation);
  }
  if (
    event.type === 'tool.outcome' &&
    data !== undefined &&
    data['status'] !== 'cancelled' &&
    data['status'] !== 'error' &&
    !(typeof invocation === 'string' && run.intents.has(invocation))
  ) {
    broken.push(['H5', `tool.outcome of ${String(invocation)} without a tool.intent or tool.denied`]);
  }
  broken.push(...outcomeRule(event, run, invocation));
  if (event.type === 'run.admitted') run.admitted = true;
  if (TERMINAL_EVENTS.has(event.type) && run.endedAt === null) run.endedAt = event.seq;
  run.events += 1;
  return broken;
}

function outcomeRule(event: JournalEvent, run: RunHistory, invocation: unknown): Broken[] {
  const broken: Broken[] = [];
  if (typeof invocation === 'string') {
    if (event.type === 'tool.intent' || event.type === 'tool.denied') {
      // A re-call after `tool.reconciled` not_applied opens the call again.
      run.calls.set(invocation, run.outcomes.has(invocation));
    } else if (event.type === 'tool.outcome') {
      if (run.outcomes.has(invocation)) broken.push(['H8', `a second tool.outcome of ${invocation}`]);
      run.outcomes.add(invocation);
      run.calls.set(invocation, true);
    } else if (event.type === 'tool.unknown') {
      run.calls.set(invocation, true);
    }
  }
  if (TERMINAL_EVENTS.has(event.type) && run.endedAt === null) {
    for (const [call, ended] of run.calls) {
      if (!ended) broken.push(['H8', `${event.type} with tool call ${call} left without an outcome`]);
    }
  }
  return broken;
}

/**
 * Every rule H1..H6 the streams in `events` (each in seq order) break.
 * `admittedOnly`: judge only the runs whose `run.admitted` is among `events`
 * (H1 still covers every event); store-level tests append worker events for
 * run ids no admission made, which are not a run's history.
 */
export function journalHistoryViolations(
  events: readonly JournalEvent[],
  { admittedOnly = false }: { admittedOnly?: boolean } = {},
): HistoryViolation[] {
  const admitted = new Set(
    events.filter((e) => e.type === 'run.admitted').map((e) => `${e.orgId}/${String(e.runId)}`),
  );
  const streams = new Map<string, JournalEvent[]>();
  for (const event of events) {
    const key = `${event.orgId}/${event.streamId}`;
    streams.set(key, [...(streams.get(key) ?? []), event]);
  }
  const violations: HistoryViolation[] = [];
  const runs = new Map<string, RunHistory>();
  for (const [stream, stored] of streams) {
    stored.forEach((event, index) => {
      if (event.seq !== index + 1) {
        violations.push({
          rule: 'H1',
          stream,
          seq: event.seq,
          runId: event.runId ?? null,
          message: `expected seq ${String(index + 1)}`,
        });
      }
    });
    for (const event of stored) {
      if (event.runId === undefined) continue;
      const key = `${event.orgId}/${event.runId}`;
      if (admittedOnly && !admitted.has(key)) continue;
      const run =
        runs.get(key) ??
        {
          events: 0,
          admitted: false,
          pinned: false,
          fence: null,
          intents: new Set<string>(),
          endedAt: null,
          calls: new Map<string, boolean>(),
          outcomes: new Set<string>(),
        };
      runs.set(key, run);
      for (const [rule, message] of runRules(event, run)) {
        violations.push({ rule, stream, seq: event.seq, runId: event.runId, message });
      }
    }
  }
  return violations;
}

/** H7: v1 `agentEvents` of one session, in `eventSeq` order. */
export function v1HistoryViolations(
  events: readonly HarnessEvent[],
): HistoryViolation[] {
  const violations: HistoryViolation[] = [];
  const commits = new Map<string, number>();
  events.forEach((event, index) => {
    const stream = `${event.orgId}/${event.sessionId}`;
    if (event.eventSeq !== index + 1) {
      violations.push({
        rule: 'H7',
        stream,
        seq: event.eventSeq,
        runId: event.runId,
        message: `expected eventSeq ${String(index + 1)}`,
      });
    }
    const previous = commits.get(event.runId);
    if (previous !== undefined && event.commitSeq < previous) {
      violations.push({
        rule: 'H7',
        stream,
        seq: event.eventSeq,
        runId: event.runId,
        message: `commitSeq ${String(event.commitSeq)} after ${String(previous)}`,
      });
    }
    commits.set(event.runId, event.commitSeq);
  });
  return violations;
}

export const describeViolations = (violations: readonly HistoryViolation[]): string =>
  violations
    .map((v) => `  ${v.rule} ${v.stream}#${String(v.seq)} run=${String(v.runId)}: ${v.message}`)
    .join('\n');

let unchecked: string | null = null;

/** The running test writes an invalid history on purpose; the after-each check skips it. */
export function uncheckedJournalHistory(reason: string): void {
  unchecked = reason;
}

/** For the root hook: whether the test that just ran opted out, and reset for the next. */
export function takeUncheckedReason(): string | null {
  const reason = unchecked;
  unchecked = null;
  return reason;
}

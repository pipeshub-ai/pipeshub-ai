/**
 * Whether one session's journal is a history the harness could have written
 * (QA-21): QA-13's rules H1..H6 (tests/support/harness/journal-history.ts)
 * plus the rules of the decision points a race reaches, D1..D5. Each is a
 * property of every legal interleaving, so a violation names a lost or
 * duplicated effect, never an unlucky order.
 */
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { journalHistoryViolations, TERMINAL_EVENTS } from '../../../support/harness/journal-history';

/** What a run that a Stop reached may still end as. */
const AFTER_STOP = new Set(['run.cancelled', 'run.failed']);

interface Decisions {
  stopped: boolean;
  messageSeq: number;
  requested: Set<string>;
  resolved: Set<string>;
  lastResolved: { interactionId: string; decision: string } | null;
}

const text = (value: unknown): string => (typeof value === 'string' ? value : JSON.stringify(value));

function decisionRules(event: JournalEvent, run: Decisions): string[] {
  const broken: string[] = [];
  const data = event.data ?? {};
  const id = text(data['interactionId']);
  switch (event.type) {
    case 'run.cancel_requested':
      if (run.stopped) broken.push('D1 cancel requested twice');
      run.stopped = true;
      break;
    case 'input.queued': {
      const seq = Number(data['messageSeq']);
      if (seq !== run.messageSeq + 1) broken.push(`D2 message ${String(seq)} after ${String(run.messageSeq)}`);
      run.messageSeq = seq;
      break;
    }
    case 'interaction.requested':
      if (run.requested.has(id)) broken.push(`D3 ${id} requested twice`);
      run.requested.add(id);
      break;
    case 'interaction.resolved':
      if (!run.requested.has(id)) broken.push(`D3 ${id} resolved but never requested`);
      if (run.resolved.has(id)) broken.push(`D3 ${id} resolved twice`);
      if (run.stopped) broken.push(`D4 ${id} resolved after the run was stopped`);
      run.resolved.add(id);
      break;
    case 'interaction.granted': {
      const last = run.lastResolved;
      if (last?.interactionId !== id || last.decision !== 'approved') {
        broken.push(`D3 grant for ${id} without its approval right before it`);
      }
      break;
    }
    default:
      break;
  }
  run.lastResolved =
    event.type === 'interaction.resolved' ? { interactionId: id, decision: text(data['decision']) } : null;
  if (TERMINAL_EVENTS.has(event.type) && run.stopped && !AFTER_STOP.has(event.type)) {
    broken.push(`D5 a stopped run ended ${event.type}`);
  }
  return broken;
}

/**
 * The rules a stream breaks, one line each (empty: valid). `events` is the
 * whole stream, in seq order, claim-checked payloads read back. `runs`:
 * judge only these runs (a seeded stream's other runs were written by hand).
 */
export function journalViolations(events: readonly JournalEvent[], runs?: ReadonlySet<string>): string[] {
  const judged = (runId: string | null | undefined): boolean =>
    runs === undefined || (runId !== null && runId !== undefined && runs.has(runId));
  const violations = journalHistoryViolations(events)
    .filter((violation) => judged(violation.runId))
    .map((violation) => `${violation.rule} seq ${String(violation.seq)}: ${violation.message}`);
  const states = new Map<string, Decisions>();
  for (const event of events) {
    if (!judged(event.runId)) continue;
    const runId = event.runId as string;
    const run = states.get(runId) ?? {
      stopped: false,
      messageSeq: 0,
      requested: new Set<string>(),
      resolved: new Set<string>(),
      lastResolved: null,
    };
    states.set(runId, run);
    for (const rule of decisionRules(event, run)) {
      violations.push(`${rule} (seq ${String(event.seq)} ${event.type}, run ${runId})`);
    }
  }
  return violations;
}

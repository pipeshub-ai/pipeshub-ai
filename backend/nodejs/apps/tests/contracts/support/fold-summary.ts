/**
 * What a journal folds to, as both sides compare it (QA13-15): how the run ended, each call with its outcome or
 * denial, each approval and its decision, the queued inputs, the children. Python's twin is `fold_summary` in
 * `backend/python/tests/contracts/test_journal_folds.py`.
 */
import type { Timeline } from '../../../src/modules/agent-harness/projection/timeline';

export type Pair = [string, string];

export interface FoldSummary {
  end: { status: string; detail?: string } | null;
  calls: Array<[number, string, string | null, boolean]>;
  approvals: Pair[];
  inputs: number[];
  children: Pair[];
}

const byFirst = (a: Pair, b: Pair): number => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);

export function summary(timeline: Timeline): FoldSummary {
  const end = timeline.end;
  return {
    end:
      end === null
        ? null
        : end.status === 'completed'
          ? { status: end.status, detail: end.outcome }
          : end.status === 'failed'
            ? { status: end.status, detail: end.errorType }
            : { status: end.status },
    calls: timeline.turns.flatMap((turn) =>
      turn.calls.map(
        (call): [number, string, string | null, boolean] => [
          turn.turn,
          call.callId,
          call.outcome ?? null,
          call.blockedBy !== undefined,
        ],
      ),
    ),
    approvals: timeline.approvals.map((view): Pair => [view.interactionId, view.status]).sort(byFirst),
    inputs: timeline.inputs.map((input) => input.messageSeq).sort((a, b) => a - b),
    children: timeline.children.map((child): Pair => [child.runId, child.status]).sort(byFirst),
  };
}

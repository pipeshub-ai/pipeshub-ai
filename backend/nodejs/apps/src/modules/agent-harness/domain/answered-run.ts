import type { RunGates } from './run-gates';
import type { RunTrigger } from './schedules';

/**
 * The run whose question a turn answers (WP-64, G-13): the conversation's last run when it ended asking the person
 * (`needs_input`), or the run the inbox names. The answer is held to its gates and its spending cap.
 */
export interface AnsweredRun {
  runId: string;
  /** The gates the asking run was held to; absent: none. */
  gates?: RunGates;
  /** The asking run's own spending cap (a firing's, or one it carried from a run it answered); absent: the default. */
  maxCostUsd?: number;
}

/** What an answer keeps on its run: whose question it answers and the cap it carries on (the gates join `gates`). */
export type AnsweredRunPin = Pick<AnsweredRun, 'runId' | 'maxCostUsd'>;

/**
 * A run's own spending cap: the lower of its firing's and the one an answer carried over; null: the default budget.
 * Shaped as a firing's cap, which is how the budget merge and the policy pin narrow `budgets.maxCostUsd`.
 */
export function ownCostCap(
  trigger: Pick<RunTrigger, 'maxCostUsd'> | null | undefined,
  answered: Pick<AnsweredRun, 'maxCostUsd'> | null | undefined,
): Pick<RunTrigger, 'maxCostUsd'> | null {
  const caps = [trigger?.maxCostUsd, answered?.maxCostUsd].filter(
    (cap): cap is number => typeof cap === 'number',
  );
  return caps.length === 0 ? null : { maxCostUsd: Math.min(...caps) };
}

/** A stored root run as the answer reads it. */
export interface StoredAskingRun {
  runId: string;
  status: string;
  outcome?: string | null;
  gates?: RunGates | null;
  trigger?: Pick<RunTrigger, 'maxCostUsd'> | null;
  answered?: AnsweredRunPin | null;
}

/**
 * The run a turn answers, from the root run the store found: the one the inbox named, whatever it ended as (holding
 * the answer to a run that ended otherwise only narrows it), or the session's newest when it ended asking the person.
 * One rule for both stores.
 */
export function answeredRunFrom(
  stored: StoredAskingRun,
  named: boolean,
): AnsweredRun | null {
  if (
    !named &&
    !(stored.status === 'completed' && stored.outcome === 'needs_input')
  ) {
    return null;
  }
  const cap = ownCostCap(stored.trigger, stored.answered);
  return {
    runId: stored.runId,
    ...(stored.gates !== undefined && stored.gates !== null
      ? { gates: structuredClone(stored.gates) }
      : {}),
    ...(cap?.maxCostUsd !== undefined ? { maxCostUsd: cap.maxCostUsd } : {}),
  };
}

/** A run of a session and the run that spawned it (null: a root run). */
export interface RunParent {
  runId: string;
  parentRunId: string | null;
}

/** `runId`'s run tree: its root and every run below it. */
export function runTreeOf(
  runs: readonly RunParent[],
  runId: string,
): Set<string> {
  const parentOf = new Map(runs.map((run) => [run.runId, run.parentRunId]));
  let root = runId;
  const seen = new Set([root]);
  for (
    let up = parentOf.get(root);
    up != null && !seen.has(up);
    up = parentOf.get(root)
  ) {
    root = up;
    seen.add(root);
  }
  const tree = new Set([root]);
  for (let grew = true; grew;) {
    grew = false;
    for (const run of runs) {
      if (
        run.parentRunId !== null &&
        tree.has(run.parentRunId) &&
        !tree.has(run.runId)
      ) {
        tree.add(run.runId);
        grew = true;
      }
    }
  }
  return tree;
}

/**
 * The items (instance ids) a run's journal says the person chose not to connect, or did not in time (WP-64): a
 * `connect` interaction it asked and that was resolved other than `approved`.
 */
export function skippedConnectsIn(
  events: ReadonlyArray<{ type: string; data?: unknown }>,
): string[] {
  const asked = new Map<string, string>();
  const skipped = new Set<string>();
  for (const event of events) {
    const data = (event.data ?? {}) as Record<string, unknown>;
    const ix = data['interactionId'];
    if (typeof ix !== 'string') continue;
    if (event.type === 'interaction.requested' && data['kind'] === 'connect') {
      const connect = data['connect'] as Record<string, unknown> | undefined;
      if (typeof connect?.['instanceId'] === 'string')
        asked.set(ix, connect['instanceId']);
    } else if (
      event.type === 'interaction.resolved' &&
      data['decision'] !== 'approved'
    ) {
      const instance = asked.get(ix);
      if (instance !== undefined) skipped.add(instance);
    }
  }
  return [...skipped];
}

import type { PipeshubRunSpecExtension } from '../contracts/generated/ext/pipeshub';
import type { HarnessRun } from '../domain/execution';
import type { RunExecutor } from '../domain/run-spec';
import type { ExecutionStore } from '../ports/execution-store';
import type { JournalEvent } from '../domain/journal';
import { snapshotExpired } from '../domain/snapshot-ttl';
import type { RunJournalReader } from './run-journal-reader';

export type EngineCarry = NonNullable<PipeshubRunSpecExtension['engineCarry']>;

/** shortcut: only this many latest completed children are looked at; raise it if loop sub-agents crowd them out. */
export const CARRY_CANDIDATES = 20;

export interface EngineCarryDeps {
  runs: Pick<ExecutionStore, 'completedRootChildren' | 'readClaimSubject'>;
  journal: Pick<RunJournalReader, 'read'>;
  /** Epoch ms now; absent: the system clock. */
  now?: () => number;
}

type EngineCarrySource = (
  parent: HarnessRun,
  engineKind: string,
  /** The child's `snapshots.ttlDays` (OWD-12): an older snapshot is not carried. */
  snapshotTtlDays: number,
) => Promise<EngineCarry | null>;

/**
 * The engine state a root loop run's engine child (CTL-6 `code_task`) starts
 * from: the last checkpoint of the session's latest completed engine child of
 * a root loop run of the same actor (another participant's engine history
 * holds what its tools read with their access), when that child ran the same
 * engine kind; the latest decides, an older history that skips it is never
 * taken. Each child journals on its own stream, which a worker cannot read,
 * so admission pins it (the worker's `SessionEngineRuns` rule, carry.py).
 * A forked conversation is another session: nothing crosses into it. The
 * snapshot goes with the checkpoint only while it is inside the child's
 * snapshot TTL (OWD-12); the state is carried without it after that.
 */
export function engineCarry(deps: EngineCarryDeps): EngineCarrySource {
  // Only the pinned executor is read: absent on a pin older than engines, which ran on the loop.
  const executorOf = async (
    orgId: string,
    runId: string,
  ): Promise<RunExecutor | null> => {
    const subject = await deps.runs.readClaimSubject(orgId, runId);
    if (!subject?.pinned) return null;
    const spec = subject.pinned.spec as { executor?: RunExecutor };
    return spec.executor ?? { kind: 'loop' };
  };
  const now = deps.now ?? Date.now;
  return async (parent, engineKind, snapshotTtlDays) => {
    if (parent.parent !== undefined) return null;
    const ended = await deps.runs.completedRootChildren(
      parent.orgId,
      parent.sessionId,
      CARRY_CANDIDATES,
    );
    for (const run of ended) {
      if (run.actorId !== parent.actorId || run.parent === undefined) continue;
      const executor = await executorOf(run.orgId, run.runId);
      if (executor?.kind !== 'engine') continue;
      // An engine root's children work on a fork under a narrowed grant, never the conversation's engine.
      const above = await executorOf(run.orgId, run.parent.runId);
      if (above === null || above.kind === 'engine') continue;
      if (executor.engine.kind !== engineKind) return null;
      const events = await deps.journal.read(run.orgId, run.runId);
      const last = (type: string): JournalEvent | undefined =>
        events.filter((event) => event.type === type).at(-1);
      const checkpoint = last('engine.checkpointed')?.data;
      if (checkpoint === undefined) return null;
      const taken = last('engine.snapshotted');
      const snapshot =
        taken === undefined || snapshotExpired(taken.ts, snapshotTtlDays, now())
          ? undefined
          : taken.data;
      return {
        runId: run.runId,
        checkpoint: checkpoint as EngineCarry['checkpoint'],
        ...(snapshot !== undefined
          ? { snapshot: snapshot as NonNullable<EngineCarry['snapshot']> }
          : {}),
      };
    }
    return null;
  };
}

/**
 * WAITCHILD on PostgreSQL: a child's wait for a person holds the working time of the parents joined on it, in the unit
 * that starts or ends the wait. Each step locks the parent row before it reads it, so a parent suspending on `join`
 * concurrently waits for it and then reads what it wrote: it either sees the child waiting or is held by it, never
 * neither. Locks are taken child first, then parent, upwards, as every execution write takes them. The decisions are
 * the domain's (`ancestorHeld`, `ancestorReleased`), shared with the Mongo and in-memory stores.
 */
import { CHILD_MAX_DEPTH } from '../../../../modules/agent-harness/domain/child-runs';
import {
  AncestorStep,
  ancestorHeld,
  ancestorReleased,
  childOf,
  HarnessRun,
  heldWorkLeft,
  TERMINAL_RUN_STATUSES,
} from '../../../../modules/agent-harness/domain/execution';
import {
  lockRun,
  readRun,
  readRuns,
  saveRun,
  toRun,
  type RunDoc,
} from '../execution/pg-rows';
import type { PgKernelTx } from '../pg-documents/pg';

async function lockParent(
  k: PgKernelTx,
  orgId: string,
  runId: string,
): Promise<{ doc: RunDoc; run: HarnessRun } | null> {
  const row = await lockRun(k, orgId, runId);
  return row === null ? null : { doc: row.doc, run: toRun(row.doc) };
}

async function writeStep(
  k: PgKernelTx,
  doc: RunDoc,
  step: AncestorStep,
): Promise<void> {
  await saveRun(k, { ...doc, ...step.fields });
}

/** `run` began holding its time for a person at `now` (DB time): each parent joined on it holds its own. */
export async function holdAncestors(
  k: PgKernelTx,
  run: HarnessRun,
  now: number,
): Promise<void> {
  let child = run;
  for (let ref = childOf(child); ref !== null; ref = childOf(child)) {
    const parent = await lockParent(k, child.orgId, ref.runId);
    if (parent === null || TERMINAL_RUN_STATUSES.has(parent.run.status)) return;
    const step = ancestorHeld(
      parent.run,
      child.runId,
      child.absoluteDeadlineAt,
      now,
    );
    await writeStep(k, parent.doc, step);
    if (!step.continues) return;
    child = { ...parent.run, ...step.fields };
  }
}

/**
 * `child` stopped holding its time for a person at `now`, its parent being `parent` (locked by the caller in this
 * unit, `doc` its stored form); `wakes`: the child's ending wakes the parent's join. Returns the parent's fields as
 * written.
 */
export async function releaseParent(
  k: PgKernelTx,
  child: Pick<HarnessRun, 'runId'>,
  parent: { doc: RunDoc; run: HarnessRun },
  now: number,
  wakes = false,
): Promise<AncestorStep['fields']> {
  const step = ancestorReleased(parent.run, child.runId, now, wakes);
  if (
    (parent.run.waitingChildren ?? []).includes(child.runId) ||
    step.continues
  ) {
    await writeStep(k, parent.doc, step);
  }
  if (step.continues) {
    await releaseAncestors(k, { ...parent.run, ...step.fields }, now);
  }
  return step.fields;
}

/** `run` stopped holding its time for a person: its parents get theirs back once no child of theirs waits. */
export async function releaseAncestors(
  k: PgKernelTx,
  run: HarnessRun,
  now: number,
): Promise<void> {
  const ref = childOf(run);
  if (ref === null) return;
  const parent = await lockParent(k, run.orgId, ref.runId);
  if (parent === null) return;
  await releaseParent(k, run, parent, now);
}

/** Whether the run's root is a scheduled firing, whose tree keeps the schedule's deadline (AP-1). */
export async function rootScheduled(
  k: PgKernelTx,
  document: RunDoc,
): Promise<boolean> {
  let current: RunDoc | null = document;
  for (let depth = 0; depth <= CHILD_MAX_DEPTH; depth += 1) {
    const parentRunId: string | undefined = (
      current['parent'] as { runId?: string } | null | undefined
    )?.runId;
    if (parentRunId === undefined) return current['trigger'] != null;
    current =
      (await readRun(k, String(current['orgId']), parentRunId))?.doc ?? null;
    if (current === null) return false;
  }
  return false;
}

/** The deadlines of `run`'s children that hold their time for a person, as this unit reads them. */
export async function waitingChildDeadlines(
  k: PgKernelTx,
  run: HarnessRun,
): Promise<number[]> {
  const ids = run.waitingChildren ?? [];
  if (ids.length === 0) return [];
  return (await readRuns(k, run.orgId, ids))
    .map(toRun)
    .filter((child) => heldWorkLeft(child) !== null)
    .map((child) => child.absoluteDeadlineAt);
}

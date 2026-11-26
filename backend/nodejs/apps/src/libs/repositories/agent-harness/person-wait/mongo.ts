import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
/**
 * WAITCHILD on MongoDB: a child's wait for a person holds the working time of
 * the parents joined on it, in the transaction that starts or ends the wait.
 * Every step writes the parent document (its `waitingChildren`), so a parent
 * suspending on `join` concurrently conflicts with it and retries: it either
 * sees the child waiting or is held by it, never neither. The decisions are the
 * domain's (`ancestorHeld`, `ancestorReleased`), shared with the in-memory store.
 */
import { ClientSession } from 'mongoose';
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
  AgentRunModel,
  runFromDocument,
} from '../execution/schema/execution.schema';

async function readRun(
  orgId: string,
  runId: string,
  sessionHandle: Tx | ClientSession,
): Promise<HarnessRun | null> {
  const session = asMongoSession(sessionHandle);
  const document = await AgentRunModel.findOne({ orgId, runId })
    .session(session)
    .lean();
  return document === null ? null : runFromDocument(document);
}

async function writeStep(
  run: HarnessRun,
  step: AncestorStep,
  sessionHandle: Tx | ClientSession,
): Promise<void> {
  const session = asMongoSession(sessionHandle);
  await AgentRunModel.updateOne(
    { orgId: run.orgId, runId: run.runId },
    { $set: step.fields },
    { session },
  );
}

/** `run` began holding its time for a person at `now` (DB time): each parent joined on it holds its own. */
export async function holdAncestors(
  run: HarnessRun,
  now: number,
  sessionHandle: Tx | ClientSession,
): Promise<void> {
  const session = asMongoSession(sessionHandle);
  let child = run;
  for (let ref = childOf(child); ref !== null; ref = childOf(child)) {
    const parent = await readRun(child.orgId, ref.runId, session);
    if (parent === null || TERMINAL_RUN_STATUSES.has(parent.status)) return;
    const step = ancestorHeld(
      parent,
      child.runId,
      child.absoluteDeadlineAt,
      now,
    );
    await writeStep(parent, step, session);
    if (!step.continues) return;
    child = { ...parent, ...step.fields };
  }
}

/**
 * `child` stopped holding its time for a person at `now`, its parent being
 * `parent` as this transaction reads it; `wakes`: the child's ending wakes the
 * parent's join. Returns the parent's fields as written.
 */
export async function releaseParent(
  child: Pick<HarnessRun, 'runId'>,
  parent: HarnessRun,
  now: number,
  sessionHandle: Tx | ClientSession,
  wakes = false,
): Promise<AncestorStep['fields']> {
  const session = asMongoSession(sessionHandle);
  const step = ancestorReleased(parent, child.runId, now, wakes);
  if ((parent.waitingChildren ?? []).includes(child.runId) || step.continues) {
    await writeStep(parent, step, session);
  }
  if (step.continues) {
    await releaseAncestors({ ...parent, ...step.fields }, now, session);
  }
  return step.fields;
}

/** `run` stopped holding its time for a person: its parents get theirs back once no child of theirs waits. */
export async function releaseAncestors(
  run: HarnessRun,
  now: number,
  sessionHandle: Tx | ClientSession,
): Promise<void> {
  const session = asMongoSession(sessionHandle);
  const ref = childOf(run);
  if (ref === null) return;
  const parent = await readRun(run.orgId, ref.runId, session);
  if (parent === null) return;
  await releaseParent(run, parent, now, session);
}

/** Whether the run's root is a scheduled firing, whose tree keeps the schedule's deadline (AP-1). */
export async function rootScheduled(
  document: { orgId?: unknown; parent?: unknown; trigger?: unknown },
  sessionHandle: Tx | ClientSession,
): Promise<boolean> {
  const session = asMongoSession(sessionHandle);
  let current: Record<string, unknown> | null = document;
  for (let depth = 0; depth <= CHILD_MAX_DEPTH; depth += 1) {
    const parentRunId: string | undefined = (
      current['parent'] as { runId?: string } | undefined
    )?.runId;
    if (parentRunId === undefined) return current['trigger'] != null;
    current = await AgentRunModel.findOne(
      { orgId: String(current['orgId']), runId: parentRunId },
      { orgId: 1, runId: 1, parent: 1, trigger: 1 },
    )
      .session(session)
      .lean<Record<string, unknown>>();
    if (current === null) return false;
  }
  return false;
}

/** The deadlines of `run`'s children that hold their time for a person, as this transaction reads them. */
export async function waitingChildDeadlines(
  run: HarnessRun,
  sessionHandle: Tx | ClientSession,
): Promise<number[]> {
  const session = asMongoSession(sessionHandle);
  const ids = run.waitingChildren ?? [];
  if (ids.length === 0) return [];
  const documents = await AgentRunModel.find({
    orgId: run.orgId,
    runId: { $in: ids },
  })
    .session(session)
    .lean();
  return documents
    .map((document) =>
      runFromDocument(document as unknown as Record<string, unknown>),
    )
    .filter((child) => heldWorkLeft(child) !== null)
    .map((child) => child.absoluteDeadlineAt);
}

import { HarnessConflictError } from '../domain/errors';
import { TERMINAL_RUN_STATUSES } from '../domain/execution';
import { JournalEventInput } from '../domain/journal';
import { sessionAppend } from '../domain/session-participants';
import {
  ERASURE_ARTIFACT_PAGES_PER_STEP,
  ERASURE_RUNS_POLL_MS,
  TRACE_ERASE_PAGE,
  ErasureCounts,
  ErasureJob,
  ErasureStep,
  ErasureStepOutcome,
  erasureCancelCommandId,
} from '../domain/session-erasure';
import { JournalPreparer, JournalStore } from '../ports/journal-store';
import {
  ArtifactEraser,
  ConversationEraser,
  ErasureSessionRuns,
  ErasureStepContext,
  LiveBusEraser,
  SessionDataEraser,
  SessionRunLookup,
  SessionStoreEraser,
  TraceEraser,
} from '../ports/session-erasure';
import { SandboxFolderEraser } from '../ports/sandbox-reaper';
import { NextRunEraser } from '../domain/next-runs';
import { MemoryStore } from '../ports/memory-store';
import type { DataEraserExtension } from '../ports/data-eraser-extension';
import type { CodingEnginePreferences } from '../ports/coding-engines';
import { ErasureOwnershipLostError } from './session-erasure';
import { ExecutionService } from './execution-service';
import { SessionWorkspaceKeys } from './session-workspace-keys';

/** Who cancels a run when the org's retention period, not a person, erased its session. */
export const ERASURE_RETENTION_PRINCIPAL = 'p_system_retention';

export interface ErasureStepDependencies<Tx> {
  runs: SessionRunLookup;
  executions: Pick<ExecutionService, 'control'>;
  workspaceKeys: Pick<SessionWorkspaceKeys, 'shred'>;
  /** Absent: no LiveBus in this deployment, nothing to delete. */
  liveBus: LiveBusEraser | null;
  /** Absent: this deployment runs no sandbox broker, so no session folder exists. */
  sandboxFolders?: SandboxFolderEraser | null;
  /** Governed memories (P7.3); absent: this deployment keeps none. */
  memories?: Pick<MemoryStore, 'eraseSession'> | null;
  /** Stores this build does not know of (`DataEraserExtension`); absent: none. */
  extension?: Pick<DataEraserExtension, 'eraseSession'> | null;
  /** Messages waiting as next runs (SS2); absent: none can exist here. */
  nextRuns?: NextRunEraser | null;
  artifacts: ArtifactEraser;
  /** Absent: this deployment sends no traces anywhere. */
  traces?: TraceEraser | null;
  stores: SessionStoreEraser;
  conversations: ConversationEraser;
  /** The chat's coding-engine choice, pin and repository (ENGPICK); absent: no coding engines here. */
  engineChoices?: Pick<CodingEnginePreferences, 'eraseSession'> | null;
  journal: {
    store: JournalStore<Tx>;
    preparer: JournalPreparer;
    transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  };
}

type Outcome = ErasureStepOutcome | { kind: 'completed_in_transaction' };

const done = (counts: ErasureCounts = {}): Outcome => ({
  kind: 'done',
  counts,
});

const step = <Tx>(
  name: ErasureStep,
  erase: (job: ErasureJob, context: ErasureStepContext<Tx>) => Promise<Outcome>,
): SessionDataEraser<Tx> => ({ step: name, erase });

export function sessionErasedEvent(job: ErasureJob): JournalEventInput {
  return {
    type: 'session.erased',
    schemaVersion: 1,
    critical: false,
    visibility: 'audit',
    data: {
      ...(job.by !== null ? { by: job.by } : {}),
      reason: job.reason,
      counts: { ...job.counts },
    },
  };
}

/** The steps of ARCHITECTURE 5.7 as built (E7, and those later phases added), in the order the runner takes them. */
export function createErasureSteps<Tx>(
  deps: ErasureStepDependencies<Tx>,
): SessionDataEraser<Tx>[] {
  // Read where the runs may still change (before the cancel, while they stop); every later step reuses the pass's read.
  const readRuns = async (
    job: ErasureJob,
    context: ErasureStepContext<Tx>,
  ): Promise<ErasureSessionRuns> => {
    const runs = await deps.runs.runsOf(job.orgId, job.sessionId);
    if (context.pass !== undefined) context.pass.runs = runs;
    return runs;
  };
  const runIds = async (
    job: ErasureJob,
    context: ErasureStepContext<Tx>,
  ): Promise<string[]> =>
    (context.pass?.runs ?? (await readRuns(job, context))).map(
      (run) => run.runId,
    );

  return [
    step('cancel_runs', async (job, context) => {
      // First, so a run that ends below cannot admit a waiting message.
      const nextRuns =
        (await deps.nextRuns?.dropNextRuns(job.orgId, job.sessionId)) ?? 0;
      const runs = await readRuns(job, context);
      let cancelled = 0;
      for (const run of runs) {
        if (
          TERMINAL_RUN_STATUSES.has(run.status) ||
          run.status === 'cancelling'
        ) {
          continue;
        }
        const commandId = erasureCancelCommandId(job.sessionId, run.runId);
        try {
          await deps.executions.control({
            orgId: job.orgId,
            runId: run.runId,
            expectedRevision: run.revision,
            action: 'cancel',
            by: job.by ?? ERASURE_RETENTION_PRINCIPAL,
            commandId,
            dispatchCommandId: `${commandId}:dispatch`,
          });
          cancelled += 1;
        } catch (error) {
          // The run moved between the read and the cancel: read it again.
          if (error instanceof HarnessConflictError) {
            return { kind: 'wait', retryAfterMs: 1_000, reason: 'run_moved' };
          }
          throw error;
        }
      }
      return done({
        runs: runs.length,
        cancelled,
        ...(nextRuns > 0 ? { nextRuns } : {}),
      });
    }),
    step('shred_workspace_key', async (job, context) => {
      // Keys are issued only to attempts of the session's runs.
      if ((await runIds(job, context)).length === 0) return done();
      const dropped = await deps.workspaceKeys.shred(job.orgId, job.sessionId);
      return done({ workspaceKeys: dropped ? 1 : 0 });
    }),
    step('await_runs_terminal', async (job, context) => {
      const runs = await readRuns(job, context);
      const stopping = runs.filter(
        (run) => !TERMINAL_RUN_STATUSES.has(run.status),
      ).length;
      return stopping > 0
        ? {
            kind: 'wait',
            retryAfterMs: ERASURE_RUNS_POLL_MS,
            reason: 'runs_stopping',
          }
        : done();
    }),
    step('live_bus', async (job, context) => {
      if (deps.liveBus === null) return done();
      return done({
        liveStreams: await deps.liveBus.erase(await runIds(job, context)),
      });
    }),
    // After the runs ended, so none of them can propose another (P7.3).
    step('memories', async (job) => {
      if (deps.memories === undefined || deps.memories === null) return done();
      return done({
        memories: await deps.memories.eraseSession(job.orgId, job.sessionId),
      });
    }),
    step('extension_stores', async (job) => {
      if (deps.extension === undefined || deps.extension === null)
        return done();
      return done({
        extensionStores: await deps.extension.eraseSession(
          job.orgId,
          job.sessionId,
        ),
      });
    }),
    step('artifacts', async (job) => {
      const counts = { artifacts: 0, blobs: 0 };
      for (let page = 0; page < ERASURE_ARTIFACT_PAGES_PER_STEP; page += 1) {
        const erased = await deps.artifacts.eraseConversation(
          job.orgId,
          job.sessionId,
        );
        counts.artifacts += erased.artifacts;
        counts.blobs += erased.blobs;
        if (!erased.remaining) return done(counts);
      }
      return { kind: 'wait', retryAfterMs: 0, reason: 'artifacts_remaining' };
    }),
    // Before the journal tombstones the runs, while their ids are still the session's; a failure is retried.
    step('traces', async (job, context) => {
      if (deps.traces === undefined || deps.traces === null) return done();
      const ids = await runIds(job, context);
      let traces = 0;
      for (
        let start = 0;
        start === 0 || start < ids.length;
        start += TRACE_ERASE_PAGE
      ) {
        traces += (
          await deps.traces.eraseTraces(
            job.orgId,
            job.sessionId,
            ids.slice(start, start + TRACE_ERASE_PAGE),
          )
        ).traces;
      }
      return done({ traces });
    }),
    step('workspaces', async (job) => {
      const erased = await deps.stores.eraseWorkspaces(
        job.orgId,
        job.sessionId,
      );
      return done({
        manifests: erased.manifests,
        workspaces: erased.heads,
        ...(erased.writers !== undefined ? { writers: erased.writers } : {}),
      });
    }),
    step('sandbox_folders', async (job) => {
      if (deps.sandboxFolders === undefined || deps.sandboxFolders === null) {
        return done();
      }
      return done({
        sandboxFolders: await deps.sandboxFolders.eraseSession(
          job.orgId,
          job.sessionId,
        ),
      });
    }),
    step('payloads', async (job, context) =>
      done(
        await deps.stores.erasePayloads(
          job.orgId,
          job.sessionId,
          await runIds(job, context),
        ),
      ),
    ),
    step('journal', async (job, context) =>
      done(
        await deps.stores.tombstoneJournal(
          job.orgId,
          job.sessionId,
          await runIds(job, context),
        ),
      ),
    ),
    step('conversation', async (job) => {
      const choices = deps.engineChoices;
      // Before the conversation: on MongoDB the choices are fields of its document.
      const engineChoices: ErasureCounts =
        choices === undefined || choices === null
          ? {}
          : {
              engineChoices: await choices.eraseSession(
                job.orgId,
                job.sessionId,
              ),
            };
      return done({
        ...(await deps.conversations.erase(job.orgId, job.sessionId)),
        ...engineChoices,
      });
    }),
    step('audit', async (job, context) => {
      const { store, preparer, transaction } = deps.journal;
      const journaled =
        ((await store.lastSeq(job.orgId, [job.sessionId]))[job.sessionId] ??
          0) > 0;
      // A session that never ran durably has no journal, so no audit chain to extend.
      const prepared = journaled
        ? await preparer.prepare(
            sessionAppend(job.orgId, job.sessionId, [sessionErasedEvent(job)]),
          )
        : null;
      await transaction(async (tx) => {
        if (prepared !== null) await store.append(prepared, tx);
        if (
          !(await context.completeIn(tx, { auditEvents: prepared ? 1 : 0 }))
        ) {
          throw new ErasureOwnershipLostError();
        }
      });
      return { kind: 'completed_in_transaction' };
    }),
  ];
}

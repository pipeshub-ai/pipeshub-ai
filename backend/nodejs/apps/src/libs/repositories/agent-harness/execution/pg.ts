import type { Tx } from '../../uow';
import { isRetryable } from '../../../db/errors';
import {
  assertRetirable,
  assertRetired,
} from '../../../../modules/agent-harness/domain/operator-suspension';
import { RETIRED } from '../../../../modules/agent-harness/domain/operator-controls';
import type { RunParent } from '../../../../modules/agent-harness/domain/answered-run';
import { recordedRunFieldsOf } from '../../../../modules/agent-harness/domain/recorded-run-fields';
import { personWaitWindowMs } from '../../../../modules/agent-harness/domain/run-policy-fields';
import type { CommitFence } from '../../../../modules/agent-harness/ports/commit-fence';
import type { PinnedEngineProfile } from '../../../../modules/agent-harness/contracts/generated/core/engine-profile';
import { consumedInputs } from '../../../../modules/agent-harness/domain/steering';
import {
  assertControlCurrent,
  assertResolvable,
  commutes,
  assertSuspendable,
  DECIDABLE_RUN_STATUSES,
  requestedInteractions,
  resolutionFeatures,
  RESUMABLE_RUN_STATUSES,
} from '../../../../modules/agent-harness/domain/interactions';
import {
  canonicalJson,
  canonicalSha256,
} from '../../../../modules/agent-harness/contracts/canonical-json';
import {
  HarnessConflictError,
  ConversationDeletedError,
  HarnessNotFoundError,
  TranscriptProjectionError,
} from '../../../../modules/agent-harness/domain/errors';
import {
  attemptOwnsRun,
  childOf,
  ClaimCandidate,
  ClaimedRun,
  ClaimPinning,
  ClaimRole,
  ClaimRun,
  CommitBatch,
  CommitReceipt,
  ControlRun,
  controlResolutions,
  entersPersonWait,
  startsWaitingForPerson,
  heldWorkLeft,
  joinDeadline,
  JournalRange,
  journalStreamId,
  HarnessEvent,
  IDLE_RUN_STATUSES,
  HarnessRun,
  nodeJournalAppend,
  operatorStopAfterCancel,
  personWaitDeadline,
  ReleaseAttempt,
  RenewAttempt,
  runClaimedEvent,
  resumedNotices,
  RunStatus,
  sweptJournalEvents,
  resumesAfterLostWorker,
  WORKER_LOST,
  type SweptFailure,
  TERMINAL_RUN_STATUSES,
  transitionEffects,
  validateCommitBatch,
  waitForPersonMs,
  waitsForPerson,
  WORKER_OWNED_RUN_STATUSES,
  heldByAnotherAttempt,
  endsWithoutTranscript,
} from '../../../../modules/agent-harness/domain/execution';
import {
  afterApproval,
  priorityClassOf,
  priorityFields,
  priorityRank,
  type ClaimOrder,
} from '../../../../modules/agent-harness/domain/priority-class';
import {
  Followup,
  FollowupsWrite,
} from '../../../../modules/agent-harness/domain/followups';
import {
  PreparedJournalAppend,
  PreparedJournalEvent,
} from '../../../../modules/agent-harness/domain/journal';
import type { RunSurface } from '../../../../modules/agent-harness/domain/admission';
import {
  PinnedRunSpec,
  RunVia,
} from '../../../../modules/agent-harness/domain/run-spec';
import type { ResolvedRunConfig } from '../../../../modules/control-plane';
import { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import {
  ActorRuns,
  ClaimSubject,
  ExecutionStore,
  OpenRun,
  OpenRunFilter,
  OperatorRuns,
  PendingApprovalFilter,
  UnansweredQuestionFilter,
  RunEnding,
  StoredReceipt,
  SweptTranscriptOutcome,
  SweptTranscripts,
  StrandedRun,
  WaitingRun,
} from '../../../../modules/agent-harness/ports/execution-store';
import {
  ConversationWriter,
  RunTranscript,
} from '../../../../modules/agent-harness/ports/conversation-writer';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import type { RunLists } from '../../../../modules/agent-harness/ports/run-lists';
import {
  type EndedRunFact,
  type ListedJob,
  type ListedRun,
  listedCarry,
  listedEngine,
  MAX_SESSION_OPEN_RUNS,
  type PersonRunQuery,
  type RunListState,
  STATE_STATUSES,
} from '../../../../modules/agent-harness/domain/run-lists';
import type { ApproverNoticeOutbox } from '../../../../modules/agent-harness/ports/approval-links';
import { approverNoticeId } from '../../../../modules/agent-harness/domain/approval-links';
import {
  RunAudience,
  seenRuns,
} from '../../../../modules/agent-harness/domain/session-audience';
import {
  parseTurnLabel,
  TurnLabel,
} from '../../../../modules/agent-harness/domain/turn-labels';
import {
  endingOf,
  sweptEnding,
} from '../../../../modules/agent-harness/domain/child-runs';
import {
  ClaimingWorker,
  missingFor,
  requirementsOf,
  unattemptedRelease,
  withFeature,
  WorkerFeature,
  workerIncompatible,
} from '../../../../modules/agent-harness/domain/worker-compat';
import type { WorkspaceHeadMove } from '../workspace-head-cas/mongo';
import { moveWorkspaceHead } from '../workspace-head-cas/pg';
import { Params, fill } from '../../pg-util';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgJournalStore } from '../journal/pg';
import { PgTime, pgServerTime } from '../time/pg';
import { canRunSql, compatibleSql, placedSql } from '../worker-compat-expr/pg';
import { joinParent, type PgChildJoinDeps } from '../child-runs/pg';
import {
  holdAncestors,
  releaseAncestors,
  rootScheduled,
  waitingChildDeadlines,
} from '../person-wait/pg';
import {
  after,
  appendRunEvent,
  bumpSessionCounter,
  forEnumeratedOrg,
  inOrgRead,
  inOrgTransaction,
  inRunOrg,
  insertRunEvents,
  isUniqueViolation,
  lockRun,
  notAfter,
  placedRun,
  readRun,
  runDocs,
  saveRun,
  toRun,
  type RunDoc,
  type RunRow,
} from './pg-rows';
import { storedHistoryRef } from './document';

const plain = <T>(value: T): T => structuredClone(value);

const commandHash = (command: unknown): string => canonicalSha256(command);

const readPin = (stored: unknown): PinnedRunSpec | null =>
  typeof stored === 'string' ? (JSON.parse(stored) as PinnedRunSpec) : null;

const sweptJournal = (
  run: HarnessRun,
  nextStatus: RunStatus,
  open: readonly string[] = [],
  errorType: SweptFailure = 'deadline_exceeded',
): PreparedJournalAppend[] => {
  const events = sweptJournalEvents(run, nextStatus, open, errorType);
  return events.length > 0 ? [nodeJournalAppend(run, events)] : [];
};

/** `session_missing`: the transcript's conversation is gone; only its status was set. */
type Expiry = 'lost' | 'expired' | 'session_missing';

const TERMINAL = [...TERMINAL_RUN_STATUSES];
const WORKER_OWNED = [...WORKER_OWNED_RUN_STATUSES];
const IDLE = [...IDLE_RUN_STATUSES];

/** Moves a workspace HEAD in the commit's unit (the workspace store's CAS); false when the head was elsewhere. */
export type MoveWorkspaceHead = (
  move: WorkspaceHeadMove,
  now: number,
  tx: Tx,
) => Promise<boolean>;

/** The commit lost the race for its receipt to an identical command: answered from the stored receipt. */
class ReceiptTaken extends Error {}

const RECEIPT_KEY = 'command_receipts_pkey';

const due = (doc: RunDoc, now: number): boolean =>
  typeof doc['notBefore'] !== 'number' || doc['notBefore'] <= now;

const leaseUntil = (doc: RunDoc, now: number, durationMs: number): number =>
  Math.min(doc['absoluteDeadlineAt'] as number, now + durationMs);

const runOfRow = (row: RunRow | null): HarnessRun | null =>
  row === null ? null : toRun(row.doc);

/**
 * Run state after admission on PostgreSQL (`harness.runs` and its siblings). Every transition locks the run row and
 * checks, on the database clock, the guard the Mongo store's filter holds: a stale attempt (an older fence or
 * attempt id, a lapsed lease) can neither commit, renew nor release. The run queue and the sweeps read every org as
 * the jobs role (`FOR UPDATE SKIP LOCKED`, so claimers and sweepers pass each other's rows) and then transition each
 * run inside its own org.
 */
export class PgExecutionStore
  implements ExecutionStore, ActorRuns, OperatorRuns, RunLists
{
  constructor(
    private readonly conversations: ConversationWriter<Tx>,
    private readonly dispatches: DispatchOutbox<Tx>,
    private readonly time: PgTime = pgServerTime,
    private readonly journal: JournalStore<Tx> = new PgJournalStore(time),
    /** WP56-OUTBOX: a wait's approver notices, written in the commit that makes the run wait. Absent: none. */
    private readonly approverNotices: ApproverNoticeOutbox<Tx> | null = null,
    /** The workspace store's HEAD CAS, in the commit's unit. */
    private readonly workspaceHeads: MoveWorkspaceHead = moveWorkspaceHead,
  ) {}

  private get joinDeps(): PgChildJoinDeps {
    return { journal: this.journal, dispatches: this.dispatches };
  }

  async readClaimSubject(
    orgId: string,
    runId: string,
  ): Promise<ClaimSubject | null> {
    const row = await inOrgRead(orgId, (k) => readRun(k, orgId, runId));
    if (row === null) return null;
    const document = row.doc;
    return {
      run: toRun(document),
      agentDefinition: plain(
        (document['agentDefinition'] ??
          null) as ClaimSubject['agentDefinition'],
      ),
      engineProfile: plain(
        (document['engineProfile'] ?? null) as ClaimSubject['engineProfile'],
      ),
      codeTaskAutoRoute: document['codeTaskAutoRoute'] === false ? false : null,
      repository: plain(
        (document['repository'] ?? null) as ClaimSubject['repository'],
      ),
      gates: plain((document['gates'] ?? null) as ClaimSubject['gates']),
      answered: plain(
        (document['answered'] ?? null) as ClaimSubject['answered'],
      ),
      modelAccess: plain(
        (document['modelAccess'] ?? null) as ClaimSubject['modelAccess'],
      ),
      // Runs admitted before `via` was recorded came in with a session.
      via: (document['via'] as RunVia | undefined) ?? 'session',
      surface: (document['surface'] as RunSurface | undefined) ?? null,
      audience: plain(
        (document['audience'] ?? null) as ClaimSubject['audience'],
      ),
      trigger: plain((document['trigger'] ?? null) as ClaimSubject['trigger']),
      memory: plain((document['memory'] ?? null) as ClaimSubject['memory']),
      pinned: readPin(document['pinned']),
      history: storedHistoryRef(document),
      resolved:
        typeof document['resolved'] === 'string'
          ? (JSON.parse(document['resolved']) as ResolvedRunConfig)
          : null,
    };
  }

  async claim(command: ClaimRun, pinning?: ClaimPinning): Promise<ClaimedRun> {
    this.validateLeaseDuration(command.leaseDurationMs);
    const worker = command.worker;
    const claimed = await inOrgTransaction(command.orgId, async (tx, k) => {
      const now = await this.time.now(k);
      const row = await lockRun(k, command.orgId, command.runId);
      if (row === null) return null;
      const doc = row.doc;
      const status = doc['status'];
      const claimable =
        after(doc['absoluteDeadlineAt'], now) &&
        // Re-checked here: a requirement raised since the service read the run holds.
        (worker === undefined ||
          missingFor(placedRun(row), worker).length === 0) &&
        ((status === 'queued' && due(doc, now)) ||
          (status === 'running' && notAfter(doc['leaseExpiresAt'], now)));
      if (!claimable) return null;
      const before = toRun(doc);
      const next: RunDoc = {
        ...doc,
        status: 'running',
        workerId: command.workerId,
        attemptId: command.attemptId,
        leaseExpiresAt: leaseUntil(doc, now, command.leaseDurationMs),
        fence: (doc['fence'] as number) + 1,
        attempts: ((doc['attempts'] as number | undefined) ?? 0) + 1,
        claimedFrom: status === 'running' ? 'expired_lease' : 'queued',
        revision: (doc['revision'] as number) + 1,
        updatedAt: now,
        ...(worker !== undefined
          ? {
              requires:
                before.harnessMajor === 2
                  ? {
                      ...(doc['requires'] ?? {}),
                      foldVersion: Math.max(
                        requirementsOf(before).foldVersion,
                        worker.foldVersion,
                      ),
                      features: requirementsOf(before).features,
                    }
                  : doc['requires'],
              attemptFeatures: worker.features,
              waitingForWorker: null,
            }
          : {}),
      };
      const claimedRun = toRun(next);
      let stored = next;
      let pinned: PinnedRunSpec | undefined;
      const events: PreparedJournalEvent[] = [];
      if (claimedRun.harnessMajor === 2) {
        events.push(
          runClaimedEvent(
            claimedRun.attempts ?? claimedRun.fence,
            claimedRun.fence,
            command.leaseDurationMs,
          ),
          ...resumedNotices(claimedRun.attempts ?? 1),
        );
        pinned = readPin(doc['pinned']) ?? undefined;
        if (pinned === undefined) {
          if (pinning === undefined) {
            throw new HarnessConflictError(
              'SPEC_NOT_PINNED',
              'A harness v2 run needs a spec to pin on its first claim',
            );
          }
          // The pin's schemaVersion is what a Node must read to serve the run (RU-2g).
          stored = {
            ...next,
            pinned: canonicalJson(pinning.pinned),
            requires: {
              ...(next['requires'] ?? {}),
              schemaVersion: pinning.pinned.spec.schemaVersion,
            },
          };
          pinned = pinning.pinned;
          events.push(pinning.specEvent);
        }
      }
      await saveRun(k, stored);
      if (pinned !== undefined) {
        await this.journal.append(
          {
            orgId: claimedRun.orgId,
            sessionId: claimedRun.sessionId,
            streamId: journalStreamId(claimedRun),
            runId: claimedRun.runId,
            attemptId: command.attemptId,
            fence: claimedRun.fence,
            writer: 'node',
            events,
          },
          tx,
        );
      }
      // In the unit that holds the fence bump: every commit of an older fence is counted here or can no longer land.
      const stream = journalStreamId(claimedRun);
      const lastSeq = await this.journal.lastSeq(command.orgId, [stream], tx);
      return {
        run: claimedRun,
        journal: { lastSeq },
        ...(pinned !== undefined ? { pinned } : {}),
      };
    });
    if (claimed === null) {
      return this.throwClaimFailure(command.orgId, command.runId, worker);
    }
    return claimed;
  }

  /** Queued v2 runs, and for the inline claimer only its own; `alias` names the runs table in the statement. */
  private dueFor(role: ClaimRole): string {
    return `status = 'queued' AND harness_major = 2${role === 'inline' ? ` AND profile = 'inline'` : ''}`;
  }

  /**
   * An inline run waits tClaimMs for its inline claimer before a dispatched worker may take it; the inline claimer
   * takes only its own.
   */
  private waitedFor(
    role: ClaimRole,
    tClaimMs: number,
    now: string,
    p: Params,
  ): string {
    return role === 'inline'
      ? `COALESCE(not_before, 0) <= ${now}`
      : `COALESCE(not_before, 0) + CASE WHEN profile = 'inline' THEN ${p.add(tClaimMs)}::double precision ELSE 0 END <= ${now}`;
  }

  async nextClaimable(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker?: ClaimingWorker,
    order: ClaimOrder = {},
  ): Promise<ClaimCandidate[]> {
    return jobsUnit('agent-harness.claim-queue', { write: true }, async (k) => {
      const p = new Params();
      const now = `${p.add(await this.time.now(k))}::double precision`;
      const macros = {
        DUE: this.dueFor(role),
        ABOVE:
          order.above !== undefined
            ? `AND (priority_rank IS NULL OR priority_rank < ${p.add(priorityRank(order.above))})`
            : '',
        WAITED: this.waitedFor(role, tClaimMs, now, p),
        NOW: now,
        CAN_RUN: worker !== undefined ? `AND ${canRunSql(worker, p)}` : '',
        PRIORITY: order.floor ? '' : 'priority_rank NULLS FIRST, ',
        LIMIT: p.add(Math.max(limit, 1)),
      };
      const { rows } = await k.query<{ org_id: string; run_id: string }>(
        fill(
          `/* pipeshub:harness.runs_claimable */ SELECT org_id, run_id FROM harness.runs
          WHERE @{DUE}
            @{ABOVE}
            AND @{WAITED}
            AND absolute_deadline_at > @{NOW}
            @{CAN_RUN}
          ORDER BY @{PRIORITY}not_before NULLS FIRST, created_at, org_id, run_id
          LIMIT @{LIMIT}
          FOR UPDATE SKIP LOCKED`,
          macros,
        ),
        p.values,
      );
      return rows.map((row) => ({ orgId: row.org_id, runId: row.run_id }));
    });
  }

  async markWaiting(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker: ClaimingWorker,
  ): Promise<WaitingRun[]> {
    const hidden = await jobsUnit(
      'agent-harness.mark-waiting',
      { write: true },
      async (k) => {
        const p = new Params();
        const now = `${p.add(await this.time.now(k))}::double precision`;
        const macros = {
          DUE: this.dueFor(role),
          WAITED: this.waitedFor(role, tClaimMs, now, p),
          NOW: now,
          PLACED: placedSql(worker, p),
          COMPATIBLE: compatibleSql(worker, p),
          LIMIT: p.add(Math.max(limit, 1)),
        };
        return runDocs(
          k,
          fill(
            `/* pipeshub:harness.runs_hidden */ SELECT doc FROM harness.runs
            WHERE @{DUE} AND waiting_since IS NULL
              AND @{WAITED}
              AND absolute_deadline_at > @{NOW}
              -- A run of another region is another worker's, never one this worker leaves waiting.
              AND @{PLACED}
              AND NOT @{COMPATIBLE}
            ORDER BY not_before NULLS FIRST, created_at, org_id, run_id
            LIMIT @{LIMIT}
            FOR UPDATE SKIP LOCKED`,
            macros,
          ),
          p.values,
        );
      },
    );
    const marked: WaitingRun[] = [];
    for (const doc of hidden) {
      const run = toRun(doc);
      const requires = requirementsOf(run);
      const won = await forEnumeratedOrg(
        run.orgId,
        'agent-harness.mark-waiting',
        () => this.markRunWaiting(run.orgId, run.runId, requires),
      );
      if (won) {
        marked.push({
          orgId: run.orgId,
          runId: run.runId,
          sessionId: run.sessionId,
          requires,
        });
      }
    }
    return marked;
  }

  async activeRunsOfActor(
    orgId: string,
    actorId: string,
  ): Promise<HarnessRun[]> {
    return inOrgRead(orgId, async (k) =>
      (
        await runDocs(
          k,
          `/* pipeshub:harness.runs_active_of_actor */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND actor_id = $2 AND status <> ALL($3::text[])`,
          [orgId, actorId, TERMINAL],
        )
      ).map(toRun),
    );
  }

  // ADR-UI-01 D1: by runs_by_actor_cursor (runs_by_actor_agent with an agent), newest first.
  async personRuns(query: PersonRunQuery): Promise<ListedRun[]> {
    if (query.limit <= 0) return [];
    return inOrgRead(query.orgId, async (k) => {
      const p = new Params();
      const kind = query.kind ?? 'all';
      const states = query.states ?? [];
      const at =
        query.after !== undefined
          ? `${p.add(query.after.createdAt)}::double precision`
          : '';
      const macros = {
        ORG: p.add(query.orgId),
        ACTOR: p.add(query.actorId),
        AGENT:
          query.agentKey !== undefined
            ? `AND agent_key = ${p.add(query.agentKey)}`
            : '',
        KIND:
          kind === 'root'
            ? 'AND parent_run_id IS NULL'
            : kind === 'job'
              ? 'AND parent_run_id IS NOT NULL'
              : '',
        FROM:
          query.createdFrom !== undefined
            ? `AND created_at >= ${p.add(query.createdFrom)}::double precision`
            : '',
        TO:
          query.createdTo !== undefined
            ? `AND created_at < ${p.add(query.createdTo)}::double precision`
            : '',
        AFTER:
          query.after !== undefined
            ? `AND (created_at < ${at} OR (created_at = ${at} AND run_id < ${p.add(query.after.runId)}))`
            : '',
        STATES:
          states.length > 0
            ? `AND (${states.map((state) => pgStatePredicate(state, p)).join(' OR ')})`
            : '',
        LIMIT: p.add(query.limit),
      };
      return (
        await runDocs(
          k,
          fill(
            `/* pipeshub:harness.runs_of_person */ SELECT doc FROM harness.runs
            WHERE org_id = @{ORG} AND actor_id = @{ACTOR}
              @{AGENT} @{KIND} @{FROM} @{TO} @{AFTER} @{STATES}
            ORDER BY created_at DESC, run_id DESC LIMIT @{LIMIT}`,
            macros,
          ),
          p.values,
        )
      ).map((document) => ({
        run: toRun(document),
        engine: listedEngine(
          document['engineProfile'] ?? null,
          readPin(document['pinned'])?.spec ?? null,
        ),
      }));
    });
  }

  // By runs_by_session (org_id, session_id, ...).
  async sessionJobs(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<ListedJob[]> {
    if (limit <= 0) return [];
    return inOrgRead(orgId, async (k) =>
      (
        await runDocs(
          k,
          `/* pipeshub:harness.runs_session_jobs */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND session_id = $2 AND parent_run_id IS NOT NULL
            ORDER BY created_at DESC, run_id DESC LIMIT $3`,
          [orgId, sessionId, limit],
        )
      ).map((document) => {
        const spec = readPin(document['pinned'])?.spec ?? null;
        return {
          run: toRun(document),
          engine: listedEngine(document['engineProfile'] ?? null, spec),
          continuesRunId: listedCarry(spec),
        };
      }),
    );
  }

  // By runs_by_session (org_id, session_id, ...).
  async openRunsOfSessions(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<HarnessRun[]> {
    if (sessionIds.length === 0) return [];
    return inOrgRead(orgId, async (k) =>
      (
        await runDocs(
          k,
          `/* pipeshub:harness.runs_open_of_sessions */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND session_id = ANY($2::text[]) AND status <> ALL($3::text[])
            ORDER BY created_at DESC, run_id DESC LIMIT $4`,
          [orgId, [...sessionIds], TERMINAL, MAX_SESSION_OPEN_RUNS],
        )
      ).map(toRun),
    );
  }

  // By runs_by_session; one row per conversation.
  async lastEndedRuns(
    orgId: string,
    actorId: string,
    sessionIds: readonly string[],
  ): Promise<EndedRunFact[]> {
    if (sessionIds.length === 0) return [];
    const rows = await inOrgRead(
      orgId,
      async (k) =>
        (
          await k.query<{
            session_id: string;
            run_id: string;
            status: RunStatus;
            updated_at: number;
          }>(
            `/* pipeshub:harness.runs_last_ended_of_sessions */ SELECT DISTINCT ON (session_id)
              session_id, run_id, status, updated_at
            FROM harness.runs
            WHERE org_id = $1 AND session_id = ANY($2::text[]) AND actor_id = $3 AND status = ANY($4::text[])
            ORDER BY session_id, updated_at DESC, run_id DESC`,
            [orgId, [...sessionIds], actorId, TERMINAL],
          )
        ).rows,
    );
    const bySession = new Map(rows.map((row) => [row.session_id, row]));
    return sessionIds.flatMap((sessionId) => {
      const row = bySession.get(sessionId);
      return row === undefined
        ? []
        : [
            {
              sessionId,
              runId: row.run_id,
              status: row.status,
              endedAt: row.updated_at,
            },
          ];
    });
  }

  async openRuns(filter: OpenRunFilter, limit: number): Promise<OpenRun[]> {
    if (limit <= 0) return [];
    const read = async (k: PgKernelTx): Promise<RunDoc[]> => {
      const p = new Params();
      const where: string[] = [];
      if (filter.suspendedFor !== undefined) {
        where.push(
          `status = 'paused'`,
          `doc->>'suspendedFor' = ${p.add(filter.suspendedFor)}`,
        );
      } else {
        where.push(`status <> ALL(${p.add(TERMINAL)}::text[])`);
      }
      if (filter.orgId !== undefined)
        where.push(`org_id = ${p.add(filter.orgId)}`);
      if (filter.sessionId !== undefined)
        where.push(`session_id = ${p.add(filter.sessionId)}`);
      if (filter.agentKey !== undefined)
        where.push(`agent_key = ${p.add(filter.agentKey)}`);
      if (filter.engine !== undefined) {
        where.push(
          `doc->'engineProfile'->'engine'->>'kind' = ${p.add(filter.engine.kind)}`,
          `doc->'engineProfile'->'engine'->>'version' = ${p.add(filter.engine.version)}`,
        );
      } else if (filter.engineOnly === true) {
        where.push(
          `COALESCE(jsonb_typeof(doc->'engineProfile'), 'null') <> 'null'`,
        );
      }
      const macros = { WHERE: where.join(' AND '), LIMIT: p.add(limit) };
      return runDocs(
        k,
        fill(
          `/* pipeshub:harness.runs_open */ SELECT doc FROM harness.runs WHERE @{WHERE}
          ORDER BY created_at, org_id, run_id LIMIT @{LIMIT}`,
          macros,
        ),
        p.values,
      );
    };
    const documents =
      filter.orgId !== undefined
        ? await inOrgRead(filter.orgId, read)
        : await jobsUnit('agent-harness.open-runs', { write: false }, read);
    return documents.map((document) => {
      const engine = (
        document['engineProfile'] as PinnedEngineProfile | null | undefined
      )?.engine;
      return {
        run: toRun(document),
        engine:
          engine === undefined
            ? null
            : { kind: engine.kind, version: engine.version },
      };
    });
  }

  async strandedRuns(
    feature: WorkerFeature,
    waitedMs: number,
    limit: number,
  ): Promise<StrandedRun[]> {
    if (limit <= 0) return [];
    const documents = await jobsUnit(
      'agent-harness.stranded-runs',
      { write: false },
      async (k) =>
        runDocs(
          k,
          `/* pipeshub:harness.runs_stranded */ SELECT doc FROM harness.runs
            WHERE status = 'queued' AND waiting_since IS NOT NULL AND waiting_since <= $1::double precision - $2::double precision
              AND doc->'requires'->'features' ? $3
            ORDER BY waiting_since, org_id, run_id LIMIT $4`,
          [await this.time.now(k), waitedMs, feature, limit],
        ),
    );
    return documents.map((document) => ({
      run: toRun(document),
      pinned: document['pinned'] != null,
    }));
  }

  async relaxRequirement(
    orgId: string,
    runId: string,
    revision: number,
    feature: WorkerFeature,
  ): Promise<boolean> {
    return inOrgRead(
      orgId,
      async (k) => {
        const row = await lockRun(k, orgId, runId);
        const doc = row?.doc;
        const requires = doc?.['requires'] as
          { features?: unknown } | undefined;
        if (
          doc === undefined ||
          doc['revision'] !== revision ||
          doc['status'] !== 'queued' ||
          doc['pinned'] != null ||
          !Array.isArray(requires?.features) ||
          !(requires.features as unknown[]).includes(feature)
        ) {
          return false;
        }
        await saveRun(k, {
          ...doc,
          requires: {
            ...requires,
            features: (requires.features as unknown[]).filter(
              (name) => name !== feature,
            ),
          },
          waitingForWorker: null,
          revision: revision + 1,
          updatedAt: await this.time.now(k),
        });
        return true;
      },
      true,
    );
  }

  async markRunWaiting(
    orgId: string,
    runId: string,
    requires: ReturnType<typeof requirementsOf>,
  ): Promise<boolean> {
    return inOrgRead(
      orgId,
      async (k) => {
        const row = await lockRun(k, orgId, runId);
        const doc = row?.doc;
        if (
          doc === undefined ||
          doc['status'] !== 'queued' ||
          doc['waitingForWorker'] != null
        ) {
          return false;
        }
        await saveRun(k, {
          ...doc,
          waitingForWorker: { since: await this.time.now(k), requires },
        });
        return true;
      },
      true,
    );
  }

  async release(command: ReleaseAttempt): Promise<HarnessRun> {
    const released = await inOrgTransaction(command.orgId, async (tx, k) => {
      const now = await this.time.now(k);
      const row = await lockRun(k, command.orgId, command.runId);
      const doc = row?.doc;
      if (
        doc === undefined ||
        doc['status'] !== 'running' ||
        doc['workerId'] !== command.workerId ||
        doc['attemptId'] !== command.attemptId ||
        doc['fence'] !== command.fence ||
        !after(doc['leaseExpiresAt'], now)
      ) {
        return null;
      }
      const next: RunDoc = {
        ...doc,
        status: 'queued',
        workerId: null,
        attemptId: null,
        leaseExpiresAt: null,
        notBefore: now,
        revision: (doc['revision'] as number) + 1,
        updatedAt: now,
        ...(unattemptedRelease(command.reason)
          ? {
              attempts: Math.max(
                0,
                ((doc['attempts'] as number | undefined) ?? 0) - 1,
              ),
            }
          : {}),
      };
      await saveRun(k, next);
      await this.dispatches.enqueue(
        [
          {
            orgId: command.orgId,
            commandId: `release:${command.runId}:${String(command.fence)}`,
            runId: command.runId,
            kind: 'run_resume',
            createdAt: now,
          },
        ],
        tx,
      );
      return toRun(next);
    });
    if (released === null) return this.throwReleaseFailure(command);
    return released;
  }

  now(orgId: string): Promise<number> {
    return inOrgTransaction(orgId, (_tx, k) => this.time.now(k));
  }

  async conversationDeleted(run: HarnessRun): Promise<boolean> {
    return inRunOrg(
      run.orgId,
      async () => !(await this.conversations.runSessionExists(run)),
    );
  }

  /** As the in-memory store answers (QA13-7): an attempt that still holds a cancelling run is not stale. */
  private async throwReleaseFailure(command: ReleaseAttempt): Promise<never> {
    const { run, now } = await this.readWithNow(command.orgId, command.runId);
    if (
      run !== null &&
      run.leaseExpiresAt !== null &&
      run.leaseExpiresAt > now &&
      run.absoluteDeadlineAt > now &&
      attemptOwnsRun(run, command)
    ) {
      throw new HarnessConflictError(
        'INVALID_TRANSITION',
        'Only a running attempt can be released',
      );
    }
    return this.attemptFailure(run, now);
  }

  async unclaimedRuns(ageMs: number, limit: number): Promise<HarnessRun[]> {
    if (limit <= 0) return [];
    return jobsUnit(
      'agent-harness.unclaimed-runs',
      { write: false },
      async (k) =>
        (
          await runDocs(
            k,
            `/* pipeshub:harness.runs_unclaimed */ SELECT doc FROM harness.runs
            WHERE status = 'queued' AND attempts = 0 AND parent_run_id IS NULL AND NOT triggered AND waiting_since IS NULL
              AND GREATEST(updated_at, COALESCE(not_before, 0)) <= $1::double precision - $2::double precision
            ORDER BY updated_at, org_id, run_id LIMIT $3`,
            [await this.time.now(k), ageMs, limit],
          )
        ).map(toRun),
    );
  }

  async promoteInline(tClaimMs: number, limit: number): Promise<number> {
    const due = await jobsUnit(
      'agent-harness.promote-inline',
      { write: true },
      async (k) =>
        runDocs(
          k,
          `/* pipeshub:harness.runs_inline_due */ SELECT doc FROM harness.runs
            WHERE status = 'queued' AND profile = 'inline'
              AND COALESCE(not_before, created_at) + $2::double precision <= $1::double precision
            ORDER BY not_before NULLS FIRST, org_id, run_id LIMIT $3
            FOR UPDATE SKIP LOCKED`,
          [await this.time.now(k), tClaimMs, Math.max(limit, 1)],
        ),
    );
    let promoted = 0;
    for (const document of due) {
      const current = toRun(document);
      const won = await forEnumeratedOrg(
        current.orgId,
        'agent-harness.promote-inline',
        () =>
          inOrgTransaction(current.orgId, async (tx, k) => {
            const row = await lockRun(k, current.orgId, current.runId);
            const doc = row?.doc;
            if (
              doc === undefined ||
              doc['revision'] !== current.revision ||
              doc['status'] !== 'queued' ||
              doc['profile'] !== 'inline'
            ) {
              return false;
            }
            const now = await this.time.now(k);
            const revision = current.revision + 1;
            await saveRun(k, {
              ...doc,
              profile: 'dispatched',
              revision,
              updatedAt: now,
            });
            await this.dispatches.enqueue(
              [
                {
                  orgId: current.orgId,
                  commandId: `promote:${current.runId}:${String(revision)}`,
                  runId: current.runId,
                  kind: 'run_ready',
                  createdAt: now,
                },
              ],
              tx,
            );
            return true;
          }),
      );
      if (won) promoted += 1;
    }
    return promoted;
  }

  async renew(command: RenewAttempt): Promise<HarnessRun> {
    this.validateLeaseDuration(command.leaseDurationMs);
    const renewed = await inOrgRead(
      command.orgId,
      async (k) => {
        const now = await this.time.now(k);
        const row = await lockRun(k, command.orgId, command.runId);
        const doc = row?.doc;
        if (
          doc === undefined ||
          !WORKER_OWNED_RUN_STATUSES.has(doc['status'] as RunStatus) ||
          doc['workerId'] !== command.workerId ||
          doc['attemptId'] !== command.attemptId ||
          doc['fence'] !== command.fence ||
          !after(doc['leaseExpiresAt'], now) ||
          !after(doc['absoluteDeadlineAt'], now)
        ) {
          return null;
        }
        const next = {
          ...doc,
          leaseExpiresAt: leaseUntil(doc, now, command.leaseDurationMs),
          updatedAt: now,
        };
        await saveRun(k, next);
        return toRun(next);
      },
      true,
    );
    if (renewed === null) {
      const { run, now } = await this.readWithNow(command.orgId, command.runId);
      return this.attemptFailure(run, now);
    }
    return renewed;
  }

  async findReceipt(
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<StoredReceipt | null> {
    const prior = await inOrgRead(orgId, (k) =>
      this.readReceipt(k, orgId, runId, commandId),
    );
    return prior
      ? {
          payloadHash: prior.payloadHash,
          receipt: plain(prior.receipt as CommitReceipt),
        }
      : null;
  }

  async commit(
    command: CommitBatch,
    journal: readonly PreparedJournalAppend[],
    transcript?: RunTranscript,
  ): Promise<CommitReceipt> {
    const payloadHash = commandHash(command);
    try {
      return plain(
        await inOrgTransaction(command.orgId, async (tx, k) => {
          const now = await this.time.now(k);
          // The run first, then its receipt: an identical command waits here for this one and then replays it.
          const row = await lockRun(k, command.orgId, command.runId);
          const prior = this.replayOf(
            await this.readReceipt(
              k,
              command.orgId,
              command.runId,
              command.commandId,
            ),
            payloadHash,
          );
          if (prior) return { ...prior, replayed: true };
          if (row === null) throw new HarnessNotFoundError();
          const currentDocument = row.doc;
          const current = toRun(currentDocument);
          this.validateOwnership(current, command);
          validateCommitBatch(current, command);
          // Under the run's lock: a resolution racing the suspend waits for it, or it for the resolution.
          assertSuspendable(current, command);
          if (
            !after(currentDocument['leaseExpiresAt'], now) ||
            !after(currentDocument['absoluteDeadlineAt'], now)
          ) {
            throw this.ownershipFailure(current, now);
          }
          const requested = requestedInteractions(command);
          const recorded = recordedRunFieldsOf(command);

          const transition = command.transition;
          const status = transition?.status ?? current.status;
          const terminal = TERMINAL_RUN_STATUSES.has(status);
          const releasesWorker = !WORKER_OWNED_RUN_STATUSES.has(status);
          const effects = transitionEffects(current, transition);
          const consumed = consumedInputs(command);
          // The turn's label goes with the run's ending in one write; without its transcript the unit aborts.
          const label =
            childOf(current) === null ? transcript?.label : undefined;
          const holds = entersPersonWait(
            current,
            status,
            waitsForPerson(status) && (await rootScheduled(k, currentDocument)),
          );
          const joining =
            status === 'paused' && effects.suspendedFor === 'join';
          const next: RunDoc = {
            ...currentDocument,
            status,
            ...(transition ? { outcome: transition.outcome ?? null } : {}),
            ...(transition?.truncatedBy !== undefined
              ? { truncatedBy: transition.truncatedBy }
              : {}),
            ...(transition?.checkpoint
              ? { checkpoint: transition.checkpoint }
              : {}),
            ...(effects.attempts !== undefined
              ? { attempts: effects.attempts }
              : {}),
            ...(effects.notBefore !== undefined
              ? { notBefore: Math.max(now, effects.notBefore) }
              : {}),
            ...(effects.checkpointRef !== undefined
              ? { checkpointRef: effects.checkpointRef }
              : {}),
            ...(effects.profile !== undefined
              ? { profile: effects.profile }
              : {}),
            ...(effects.suspendedFor !== undefined
              ? { suspendedFor: effects.suspendedFor }
              : {}),
            ...(holds
              ? personWaitDeadline(
                  current,
                  now,
                  personWaitWindowMs(currentDocument, waitForPersonMs()),
                )
              : {}),
            ...(releasesWorker
              ? { workerId: null, attemptId: null, leaseExpiresAt: null }
              : {}),
            ...(terminal ? { activeSessionKey: null } : {}),
            ...(label !== undefined ? { label } : {}),
            ...recorded,
            ...(requested.length > 0
              ? {
                  openInteractions: [
                    ...new Set([
                      ...(current.openInteractions ?? []),
                      ...requested,
                    ]),
                  ],
                }
              : {}),
            ...(consumed.length > 0
              ? {
                  pendingInputs: (current.pendingInputs ?? []).filter(
                    (seq) => !consumed.includes(seq),
                  ),
                }
              : {}),
            revision: current.revision + 1,
            updatedAt: now,
          };
          if (joining) {
            const deadlines = await waitingChildDeadlines(k, current);
            if (deadlines.length > 0 || current.workLeftMs != null) {
              Object.assign(next, joinDeadline(current, deadlines, now));
            }
          }
          await saveRun(k, next);
          if (holds || (joining && next['workLeftMs'] != null)) {
            await holdAncestors(k, toRun(next), now);
          }

          if (command.workspaceHeadCAS) {
            await this.moveWorkspaceHead(command, now, tx);
          }
          const appended = [];
          for (const append of journal) {
            appended.push(await this.journal.append(append, tx));
          }
          const first = appended.at(0);
          const last = appended.at(-1);
          const range: JournalRange | null =
            first && last
              ? {
                  streamId: first.streamId,
                  firstSeq: first.firstSeq,
                  lastSeq: last.lastSeq,
                }
              : null;

          const count = command.legacyEvents.length;
          let firstEventSeq: number | null = null;
          let lastEventSeq: number | null = null;
          if (count > 0) {
            const counter = await bumpSessionCounter(
              k,
              command.orgId,
              current.sessionId,
              { events: count },
            );
            lastEventSeq = counter.nextEventSeq;
            const allocated = lastEventSeq - count + 1;
            firstEventSeq = allocated;
            await insertRunEvents(
              k,
              command.legacyEvents.map((event, index) => ({
                orgId: command.orgId,
                sessionId: current.sessionId,
                runId: command.runId,
                eventSeq: allocated + index,
                commitSeq: current.revision + 1,
                type: event.type,
                data: event.data,
                createdAt: now,
              })),
            );
          }

          if (childOf(current) !== null) {
            if (transition !== undefined && terminal) {
              await joinParent(
                tx,
                k,
                current,
                endingOf(transition),
                now,
                this.joinDeps,
              );
            }
          } else if (transcript !== undefined) {
            if (
              !(await this.writeTranscript(k, tx, current, transcript, true)) &&
              !endsWithoutTranscript(transition?.status)
            ) {
              throw new ConversationDeletedError();
            }
          } else if (transition) {
            await this.projectTerminalTranscript(
              current,
              command,
              status,
              tx,
              now,
            );
          }
          await this.dispatches.enqueue(
            command.outbox.map((item) => ({
              ...item,
              orgId: command.orgId,
              createdAt: now,
            })),
            tx,
          );
          if (
            this.approverNotices !== null &&
            startsWaitingForPerson(current, status)
          ) {
            await this.approverNotices.enqueue(
              {
                id: approverNoticeId(
                  command.orgId,
                  command.runId,
                  current.revision + 1,
                ),
                orgId: command.orgId,
                runId: command.runId,
                createdAt: now,
              },
              tx,
            );
          }
          const result: CommitReceipt = {
            commandId: command.commandId,
            run: toRun(next),
            firstEventSeq,
            lastEventSeq,
            journal: range,
          };
          await this.storeReceipt(k, command, payloadHash, result, now);
          return result;
        }),
      );
    } catch (error) {
      if (
        !(error instanceof ReceiptTaken) &&
        !isUniqueViolation(error, [RECEIPT_KEY])
      ) {
        throw error;
      }
      return this.resolveConcurrentCommand(
        command.orgId,
        command.runId,
        command.commandId,
        payloadHash,
      );
    }
  }

  async failRun(
    orgId: string,
    runId: string,
    errorType: string,
    journal: readonly PreparedJournalAppend[],
    holder?: string,
  ): Promise<boolean> {
    const current = runOfRow(
      await inOrgRead(orgId, (k) => readRun(k, orgId, runId)),
    );
    if (current === null) throw new HarnessNotFoundError();
    if (TERMINAL_RUN_STATUSES.has(current.status)) return false;
    const expiry = await this.expire(
      current,
      {},
      'failed',
      'run_failed',
      { errorType },
      journal,
      undefined,
      { holder: holder ?? null },
    );
    return expiry !== 'lost';
  }

  private async moveWorkspaceHead(
    command: CommitBatch,
    now: number,
    tx: Tx,
  ): Promise<void> {
    const head = command.workspaceHeadCAS;
    if (!head) return;
    const moved = await this.workspaceHeads(
      { ...head, orgId: command.orgId, runId: command.runId },
      now,
      tx,
    );
    if (!moved) {
      throw new HarnessConflictError(
        'WORKSPACE_CONFLICT',
        'Workspace HEAD moved since the expected revision',
      );
    }
  }

  async control(
    command: ControlRun,
    journal: readonly PreparedJournalAppend[] = [],
    transcript?: RunTranscript,
    fence?: CommitFence,
  ): Promise<CommitReceipt> {
    const payloadHash = commandHash(command);
    try {
      return plain(
        await inOrgTransaction(command.orgId, async (tx, k) => {
          const now = await this.time.now(k);
          const row = await lockRun(k, command.orgId, command.runId);
          const prior = this.replayOf(
            await this.readReceipt(
              k,
              command.orgId,
              command.runId,
              command.commandId,
            ),
            payloadHash,
          );
          if (prior) return { ...prior, replayed: true };
          if (row === null) throw new HarnessNotFoundError();
          const currentDocument = row.doc;
          const current = toRun(currentDocument);
          assertControlCurrent(current, command);
          await fence?.hold(tx);

          let status: RunStatus;
          let eventType: string;
          let outboxKind: 'run_resume' | 'run_cancel' | null = null;
          const resolving =
            command.action === 'resolve'
              ? controlResolutions(command).map(
                  (resolution) => resolution.interactionId,
                )
              : null;
          if (resolving !== null) {
            if (resolving.length === 0) assertResolvable(current, '');
            for (const interactionId of resolving)
              assertResolvable(current, interactionId);
            if (RESUMABLE_RUN_STATUSES.has(current.status)) {
              if (current.absoluteDeadlineAt <= now) {
                throw new HarnessConflictError(
                  'DEADLINE_EXCEEDED',
                  'Run deadline has elapsed',
                );
              }
              status = 'queued';
              eventType = 'run_resumed';
              outboxKind = 'run_resume';
            } else {
              status = current.status;
              eventType = 'interaction_resolved';
            }
          } else if (command.action === 'suspend') {
            assertRetirable(current);
            status = 'paused';
            eventType = 'run_suspended';
          } else if (command.action === 'resume') {
            if (command.cause !== undefined) assertRetired(current);
            if (!RESUMABLE_RUN_STATUSES.has(current.status)) {
              throw new HarnessConflictError(
                'INVALID_TRANSITION',
                'Run cannot be resumed',
              );
            }
            if (current.absoluteDeadlineAt <= now) {
              throw new HarnessConflictError(
                'DEADLINE_EXCEEDED',
                'Run deadline has elapsed',
              );
            }
            status = 'queued';
            eventType = 'run_resumed';
            outboxKind = 'run_resume';
          } else {
            if (TERMINAL_RUN_STATUSES.has(current.status)) {
              throw new HarnessConflictError(
                'INVALID_TRANSITION',
                'Terminal run cannot be cancelled',
              );
            }
            if (WORKER_OWNED_RUN_STATUSES.has(current.status)) {
              status = 'cancelling';
              eventType = 'run_cancellation_requested';
              outboxKind = 'run_cancel';
            } else {
              status = 'cancelled';
              eventType = 'run_cancelled';
            }
          }

          // A run that tracks its open interactions resolves only ones still open.
          const tracked =
            resolving !== null && current.openInteractions !== undefined
              ? resolving
              : null;
          // A commuting resolve is checked against the revision this unit locked; any other against the caller's.
          if (
            (!commutes(current, command) &&
              current.revision !== command.expectedRevision) ||
            (tracked !== null &&
              !tracked.every((id) =>
                (current.openInteractions ?? []).includes(id),
              ))
          ) {
            throw new HarnessConflictError(
              'REVISION_CONFLICT',
              'Run revision changed',
            );
          }
          const keepsWorker = WORKER_OWNED_RUN_STATUSES.has(status);
          const terminal = TERMINAL_RUN_STATUSES.has(status);
          const needs =
            resolving !== null
              ? resolutionFeatures(controlResolutions(command))
              : [];
          const raised = afterApproval(priorityClassOf(current));
          const next: RunDoc = {
            ...currentDocument,
            status,
            ...(needs.length > 0
              ? {
                  requires: {
                    ...(currentDocument['requires'] ?? {}),
                    ...withFeature(requirementsOf(current), ...needs),
                  },
                }
              : {}),
            ...(tracked !== null
              ? {
                  openInteractions: (current.openInteractions ?? []).filter(
                    (id) => !tracked.includes(id),
                  ),
                }
              : {}),
            ...(status === 'queued' ? { notBefore: now } : {}),
            // A person answered or resumed it: they wait on it now, whether it re-queues or its worker still holds
            // it in the approval hold (G-45). Written only when it rises (PERFB-11).
            ...((status === 'queued' || resolving !== null) &&
            raised !== priorityClassOf(current)
              ? priorityFields(raised)
              : {}),
            ...(status === 'queued' && heldWorkLeft(current) !== null
              ? {
                  // A person answered: the working deadline counts again from now (FIX-P1-F3).
                  absoluteDeadlineAt: now + (current.workLeftMs as number),
                  workLeftMs: null,
                }
              : {}),
            ...(!keepsWorker
              ? { workerId: null, attemptId: null, leaseExpiresAt: null }
              : {}),
            // G11: the first operator stop stays on the run.
            ...(operatorStopAfterCancel(current, command, now) !== undefined
              ? { operatorStop: operatorStopAfterCancel(current, command, now) }
              : {}),
            // ADR-OPS-01 D6: a retired run holds its working time until it is resumed or its hold ends.
            ...(command.action === 'suspend'
              ? {
                  suspendedFor: RETIRED,
                  // The attempt was taken back, not lost: it counts toward no crash loop.
                  ...(current.attemptId !== null
                    ? { attempts: Math.max(0, (current.attempts ?? 0) - 1) }
                    : {}),
                  workLeftMs: Math.max(0, current.absoluteDeadlineAt - now),
                  absoluteDeadlineAt: Math.max(now, command.holdUntil ?? 0),
                }
              : {}),
            ...(command.action === 'resume' && command.cause !== undefined
              ? { suspendedFor: null }
              : {}),
            ...(terminal ? { activeSessionKey: null } : {}),
            revision: current.revision + 1,
            updatedAt: now,
          };
          await saveRun(k, next);
          if (status === 'queued' && heldWorkLeft(current) !== null) {
            await releaseAncestors(k, current, now);
          }
          if (command.action === 'suspend') {
            // ADR-RUN-01 D4: a retired run holds its working time, and the parents joined on it hold theirs.
            await holdAncestors(k, toRun(next), now);
          }
          for (const append of journal) {
            await this.journal.append(append, tx);
          }
          if (terminal && childOf(current) !== null) {
            await joinParent(
              tx,
              k,
              current,
              { status: 'cancelled', errorType: 'cancelled' },
              now,
              this.joinDeps,
            );
          }
          if (transcript !== undefined && terminal) {
            if (
              !(await this.writeTranscript(k, tx, current, transcript)) &&
              !endsWithoutTranscript(status)
            ) {
              throw new ConversationDeletedError();
            }
          }
          const eventSeq = await appendRunEvent(k, current, eventType, {}, now);
          if (outboxKind !== null) {
            await this.dispatches.enqueue(
              [
                {
                  orgId: command.orgId,
                  commandId: command.dispatchCommandId,
                  runId: command.runId,
                  kind: outboxKind,
                  createdAt: now,
                },
              ],
              tx,
            );
          }
          const result: CommitReceipt = {
            commandId: command.commandId,
            run: toRun(next),
            firstEventSeq: eventSeq,
            lastEventSeq: eventSeq,
          };
          await this.storeReceipt(k, command, payloadHash, result, now);
          return result;
        }),
      );
    } catch (error) {
      if (
        !(error instanceof ReceiptTaken) &&
        !isUniqueViolation(error, [RECEIPT_KEY])
      ) {
        throw error;
      }
      return this.resolveConcurrentCommand(
        command.orgId,
        command.runId,
        command.commandId,
        payloadHash,
      );
    }
  }

  async get(orgId: string, runId: string): Promise<HarnessRun | null> {
    return runOfRow(await inOrgRead(orgId, (k) => readRun(k, orgId, runId)));
  }

  async completedRootChildren(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<HarnessRun[]> {
    return inOrgRead(orgId, async (k) =>
      (
        await runDocs(
          k,
          `/* pipeshub:harness.runs_completed_root_children */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND session_id = $2 AND status = 'completed' AND parent_run_id IS NOT NULL
              AND doc->'parent'->'depth' = '1'::jsonb
            ORDER BY updated_at DESC, run_id LIMIT $3`,
          [orgId, sessionId, limit],
        )
      ).map(toRun),
    );
  }

  async pendingApprovalRuns(
    orgId: string,
    filter: PendingApprovalFilter,
  ): Promise<HarnessRun[]> {
    return inOrgRead(orgId, async (k) => {
      const p = new Params();
      const macros = {
        ORG: p.add(orgId),
        STATUSES: p.add([...DECIDABLE_RUN_STATUSES]),
        ACTOR:
          filter.actorId !== undefined
            ? `AND actor_id = ${p.add(filter.actorId)}`
            : '',
        AGENT:
          filter.agentKey !== undefined
            ? `AND agent_key = ${p.add(filter.agentKey)}`
            : '',
        LIMIT: p.add(filter.limit),
      };
      return (
        await runDocs(
          k,
          fill(
            `/* pipeshub:harness.runs_pending_approval */ SELECT doc FROM harness.runs
            WHERE org_id = @{ORG} AND status = ANY(@{STATUSES}::text[])
              AND doc->'openInteractions'->0 IS NOT NULL
              @{ACTOR}
              @{AGENT}
            ORDER BY updated_at DESC, run_id LIMIT @{LIMIT}`,
            macros,
          ),
          p.values,
        )
      ).map(toRun);
    });
  }

  async unansweredQuestionRuns(
    orgId: string,
    filter: UnansweredQuestionFilter,
  ): Promise<HarnessRun[]> {
    return inOrgRead(orgId, async (k) => {
      const p = new Params();
      const macros = {
        ORG: p.add(orgId),
        ACTOR: p.add(filter.actorId),
        AGENT:
          filter.agentKey !== undefined
            ? `AND agent_key = ${p.add(filter.agentKey)}`
            : '',
        LIMIT: p.add(filter.limit),
      };
      const candidates = (
        await runDocs(
          k,
          fill(
            `/* pipeshub:harness.runs_unanswered_questions */ SELECT doc FROM harness.runs
            WHERE org_id = @{ORG} AND status = 'completed' AND actor_id = @{ACTOR}
              AND doc->>'outcome' = 'needs_input' AND parent_run_id IS NULL
              @{AGENT}
            ORDER BY updated_at DESC, run_id LIMIT @{LIMIT}`,
            macros,
          ),
          p.values,
        )
      ).map(toRun);
      if (candidates.length === 0) return [];
      const { rows } = await k.query<{
        session_id: string;
        created_at: number;
      }>(
        `/* pipeshub:harness.runs_latest_root_by_session */ SELECT session_id, max(created_at) AS created_at FROM harness.runs
          WHERE org_id = $1 AND session_id = ANY($2::text[]) AND parent_run_id IS NULL GROUP BY session_id`,
        [orgId, [...new Set(candidates.map((run) => run.sessionId))]],
      );
      const newest = new Map(
        rows.map((row) => [row.session_id, row.created_at]),
      );
      return candidates.filter(
        (run) => newest.get(run.sessionId) === run.createdAt,
      );
    });
  }

  async sessionRunParents(
    orgId: string,
    sessionId: string,
  ): Promise<RunParent[]> {
    return inOrgRead(orgId, async (k) => {
      const { rows } = await k.query<{
        run_id: string;
        parent_run_id: string | null;
      }>(
        `/* pipeshub:harness.runs_session_parents */ SELECT run_id, parent_run_id FROM harness.runs
          WHERE org_id = $1 AND session_id = $2`,
        [orgId, sessionId],
      );
      return rows.map((row) => ({
        runId: row.run_id,
        parentRunId: row.parent_run_id,
      }));
    });
  }

  async getActive(
    orgId: string,
    sessionId: string,
  ): Promise<HarnessRun | null> {
    return inOrgRead(orgId, async (k) => {
      const [doc] = await runDocs(
        k,
        `/* pipeshub:harness.runs_active_of_session */ SELECT doc FROM harness.runs
          WHERE org_id = $1 AND session_id = $2 AND active`,
        [orgId, sessionId],
      );
      return doc === undefined ? null : toRun(doc);
    });
  }

  async readEvents(
    orgId: string,
    sessionId: string,
    after: number,
    limit: number,
  ): Promise<HarnessEvent[]> {
    return inOrgRead(orgId, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.run_events_read */ SELECT doc FROM harness.run_events
          WHERE org_id = $1 AND session_id = $2 AND event_seq > $3 ORDER BY event_seq LIMIT $4`,
        [orgId, sessionId, after, limit],
      );
      return rows.map((row) => {
        const event = decodeDoc<HarnessEvent>(row.doc);
        return {
          orgId: event.orgId,
          sessionId: event.sessionId,
          runId: event.runId,
          eventSeq: event.eventSeq,
          commitSeq: event.commitSeq,
          type: event.type,
          data: plain(event.data),
          createdAt: event.createdAt,
        };
      });
    });
  }

  async sweep(limit: number, transcripts?: SweptTranscripts): Promise<number> {
    const bound = Math.min(Math.max(limit, 1), 100);
    // Ties in admission order, as Mongo's natural order breaks them: a parent before the children it admitted.
    const { leaseExpired, pastDeadline, now } = await jobsUnit(
      'agent-harness.sweep',
      { write: true },
      async (k) => {
        const at = await this.time.now(k);
        const lapsed = await runDocs(
          k,
          `/* pipeshub:harness.runs_lease_expired */ SELECT doc FROM harness.runs
            WHERE status = ANY($1::text[]) AND lease_expires_at <= $2::double precision
            ORDER BY lease_expires_at, created_at, COALESCE((doc->'parent'->>'depth')::int, 0), org_id, run_id
            LIMIT $3 FOR UPDATE SKIP LOCKED`,
          [WORKER_OWNED, at, bound],
        );
        const late =
          lapsed.length < bound
            ? await runDocs(
                k,
                `/* pipeshub:harness.runs_past_deadline */ SELECT doc FROM harness.runs
                  WHERE status = ANY($1::text[]) AND absolute_deadline_at <= $2::double precision
                  ORDER BY absolute_deadline_at, created_at, COALESCE((doc->'parent'->>'depth')::int, 0), org_id, run_id
                  LIMIT $3 FOR UPDATE SKIP LOCKED`,
                [IDLE, at, bound - lapsed.length],
              )
            : [];
        return { leaseExpired: lapsed, pastDeadline: late, now: at };
      },
    );
    let swept = 0;
    for (const document of leaseExpired) {
      const current = toRun(document);
      const won = await forEnumeratedOrg(
        current.orgId,
        'agent-harness.sweep',
        async () => {
          const wasCancelling = current.status === 'cancelling';
          const deadlineElapsed = current.absoluteDeadlineAt <= now;
          const guard = { leaseExpiresAt: current.leaseExpiresAt };
          // C4.10 `recovery.autoResume` off: the lost worker's run ends; the person sends again.
          const lost =
            !wasCancelling &&
            !deadlineElapsed &&
            !resumesAfterLostWorker(current);
          const failing = !wasCancelling && (deadlineElapsed || lost);
          const failure: SweptFailure = lost
            ? WORKER_LOST
            : 'deadline_exceeded';
          // A lapsed cancel ends the run here: the calls its attempt left open end with it (EV-06). Its lease is
          // gone, so no worker commits between this read and the guarded transition.
          const open = wasCancelling
            ? ((await transcripts?.openCalls?.(current)) ?? [])
            : [];
          const next = wasCancelling ? 'cancelled' : 'queued';
          const transitioned = failing
            ? await this.expireFailed(
                current,
                guard,
                lost ? { reason: WORKER_LOST } : {},
                transcripts,
                failure,
              )
            : (await this.expire(
                current,
                guard,
                next,
                wasCancelling ? 'run_cancelled' : 'run_recovery_queued',
                {},
                sweptJournal(current, next, open),
              )) !== 'lost';
          if (!transitioned) return false;
          transcripts?.expired?.(
            current,
            failing ? 'failed' : wasCancelling ? 'cancelled' : 'queued',
            failing
              ? failure
              : wasCancelling
                ? 'cancel_lease_expired'
                : 'lease_expired',
          );
          return true;
        },
      );
      if (won) swept += 1;
    }
    for (const document of pastDeadline) {
      const current = toRun(document);
      const won = await forEnumeratedOrg(
        current.orgId,
        'agent-harness.sweep',
        async () => {
          // A child's wait for a person moves its joined parent's deadline without a revision (WAITCHILD).
          const transitioned = await this.expireFailed(
            current,
            { absoluteDeadlineAt: current.absoluteDeadlineAt },
            { reason: 'deadline_exceeded' },
            transcripts,
          );
          if (transitioned)
            transcripts?.expired?.(current, 'failed', 'deadline_exceeded');
          return transitioned;
        },
      );
      if (won) swept += 1;
    }
    return swept;
  }

  /**
   * Fails a run at its deadline with its transcript in the same unit. A write that fails rolls that unit back; the
   * run is then failed with its status only, so one bad conversation cannot hold its session (or the rest of the
   * page) past the deadline.
   */
  private async expireFailed(
    current: HarnessRun,
    guard: Record<string, unknown>,
    eventData: Record<string, unknown>,
    transcripts: SweptTranscripts | undefined,
    failure: SweptFailure = 'deadline_exceeded',
  ): Promise<boolean> {
    // No worker holds the run (its lease lapsed, or it waits): its open calls end with it (EV-06), under the
    // transition's guard.
    const open = (await transcripts?.openCalls?.(current)) ?? [];
    const expire = (withTranscript?: RunTranscript): Promise<Expiry> =>
      this.expire(
        current,
        guard,
        'failed',
        'run_failed',
        eventData,
        sweptJournal(current, 'failed', open, failure),
        withTranscript,
      );
    const appending = sweptJournalEvents(current, 'failed', open, failure);
    const transcript =
      transcripts !== undefined && appending.length > 0
        ? await transcripts.project(current, appending)
        : undefined;
    if (transcripts === undefined || transcript === undefined) {
      return (await expire()) !== 'lost';
    }
    let outcome: SweptTranscriptOutcome;
    let cause: unknown;
    let expiry: Expiry;
    try {
      expiry = await expire(transcript);
      outcome = expiry === 'session_missing' ? 'session_missing' : 'written';
    } catch (error) {
      if (!(error instanceof TranscriptProjectionError)) throw error;
      cause = error.cause;
      outcome = 'write_failed';
      expiry = await expire();
    }
    if (expiry === 'lost') return false;
    transcripts.settled(current, transcript, outcome, cause);
    return true;
  }

  /** One guarded transition out of an expired state; `lost` when another writer won. */
  private expire(
    current: HarnessRun,
    guard: Record<string, unknown>,
    nextStatus: RunStatus,
    eventType: string,
    eventData: Record<string, unknown>,
    journal: readonly PreparedJournalAppend[] = sweptJournal(
      current,
      nextStatus,
    ),
    transcript?: RunTranscript,
    lease?: { holder: string | null },
  ): Promise<Expiry> {
    return inOrgTransaction(current.orgId, async (tx, k): Promise<Expiry> => {
      const row = await lockRun(k, current.orgId, current.runId);
      const doc = row?.doc;
      if (
        doc === undefined ||
        doc['revision'] !== current.revision ||
        doc['status'] !== current.status ||
        Object.entries(guard).some(([field, value]) => doc[field] !== value)
      ) {
        return 'lost';
      }
      const now = await this.time.now(k);
      // Read under the row lock: a renewal moves the lease without a revision.
      if (
        lease !== undefined &&
        heldByAnotherAttempt(
          {
            status: doc['status'] as RunStatus,
            leaseExpiresAt: after(doc['leaseExpiresAt'], now)
              ? (doc['leaseExpiresAt'] as number)
              : null,
            attemptId:
              typeof doc['attemptId'] === 'string' ? doc['attemptId'] : null,
          },
          now,
          lease.holder,
        )
      ) {
        return 'lost';
      }
      const terminal = TERMINAL_RUN_STATUSES.has(nextStatus);
      await saveRun(k, {
        ...doc,
        status: nextStatus,
        workerId: null,
        attemptId: null,
        leaseExpiresAt: null,
        ...(nextStatus === 'queued' ? { notBefore: now } : {}),
        ...(terminal ? { activeSessionKey: null } : {}),
        revision: current.revision + 1,
        updatedAt: now,
      });
      for (const append of journal) {
        await this.journal.append(append, tx);
      }
      await appendRunEvent(k, current, eventType, eventData, now);
      if (!terminal) {
        await this.dispatches.enqueue(
          [
            {
              orgId: current.orgId,
              commandId: `recover:${current.runId}:${String(current.fence)}:${String(current.revision + 1)}`,
              runId: current.runId,
              kind: 'run_resume',
              createdAt: now,
            },
          ],
          tx,
        );
      } else if (childOf(current) !== null) {
        await joinParent(
          tx,
          k,
          current,
          sweptEnding(nextStatus, eventData),
          now,
          this.joinDeps,
        );
      } else if (
        transcript !== undefined &&
        (await this.writeTranscript(k, tx, current, transcript))
      ) {
        return 'expired';
      } else {
        await this.conversations.setConversationStatus(
          current,
          nextStatus === 'failed' ? 'Failed' : 'Complete',
          now,
          tx,
        );
        if (transcript !== undefined) return 'session_missing';
      }
      return 'expired';
    });
  }

  /** Everything but time; lease and deadline are checked on DB time under the run's lock. */
  private validateOwnership(run: HarnessRun, command: CommitBatch): void {
    if (
      !WORKER_OWNED_RUN_STATUSES.has(run.status) ||
      run.workerId !== command.workerId ||
      run.attemptId !== command.attemptId ||
      run.fence !== command.fence
    ) {
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    if (
      command.transition?.expectedRevision !== undefined &&
      run.revision !== command.transition.expectedRevision
    ) {
      throw new HarnessConflictError(
        'REVISION_CONFLICT',
        'Run revision changed',
      );
    }
  }

  private async projectTerminalTranscript(
    run: HarnessRun,
    command: CommitBatch,
    status: RunStatus,
    tx: Tx,
    now: number,
  ): Promise<void> {
    if (status !== 'completed') {
      if (status === 'failed') {
        await this.conversations.setConversationStatus(run, 'Failed', now, tx);
      }
      return;
    }
    const completion = [...command.legacyEvents]
      .reverse()
      .find(
        (event) => event.type === 'run_completed' || event.type === 'complete',
      )?.data;
    if (
      completion === undefined ||
      completion === null ||
      typeof completion !== 'object' ||
      typeof (completion as Record<string, unknown>).answer !== 'string'
    ) {
      await this.conversations.setConversationStatus(run, 'Complete', now, tx);
      return;
    }
    const saved = await this.conversations.saveCompletedConversation(
      run,
      completion as Record<string, unknown>,
      tx,
    );
    if (!saved) throw new HarnessNotFoundError();
  }

  /** `labelled`: the caller's own write of the run in this unit carries the label. */
  private async writeTranscript(
    k: PgKernelTx,
    tx: Tx,
    run: HarnessRun,
    transcript: RunTranscript,
    labelled = false,
  ): Promise<boolean> {
    let saved: boolean;
    try {
      saved = await this.conversations.saveRunTranscript(run, transcript, tx);
    } catch (error) {
      // A serialization failure or deadlock (another participant's admission wrote the conversation, SS2) re-runs
      // the whole unit, never fails it.
      if (isRetryable(error)) throw error;
      throw new TranscriptProjectionError(error);
    }
    if (saved && !labelled && transcript.label !== undefined) {
      const row = await lockRun(k, run.orgId, run.runId);
      if (row !== null)
        await saveRun(k, { ...row.doc, label: transcript.label });
    }
    return saved;
  }

  async storeFollowups(
    orgId: string,
    runId: string,
    followups: readonly Followup[],
  ): Promise<FollowupsWrite | null> {
    const stored = followups.map(({ text }) => ({ text }));
    return inOrgRead(
      orgId,
      async (k) => {
        const row = await lockRun(k, orgId, runId);
        if (row === null) return null;
        const doc = row.doc;
        if (doc['status'] !== 'completed') return { outcome: 'not_completed' };
        if (doc['followups'] !== undefined) {
          return {
            outcome: 'kept',
            followups: (doc['followups'] as Followup[] | null) ?? [],
          };
        }
        await saveRun(k, { ...doc, followups: stored });
        return { outcome: 'stored', followups: stored };
      },
      true,
    );
  }

  async readEnding(orgId: string, runId: string): Promise<RunEnding | null> {
    const row = await inOrgRead(orgId, (k) => readRun(k, orgId, runId));
    if (row === null) return null;
    const run = toRun(row.doc);
    if (!TERMINAL_RUN_STATUSES.has(run.status)) return null;
    return { run, label: parseTurnLabel(row.doc['label']) };
  }

  async historyLabels(run: HarnessRun): Promise<Array<TurnLabel | null>> {
    return inOrgRead(run.orgId, async (k) => {
      const stored = await readRun(k, run.orgId, run.runId);
      if (stored === null) return [null];
      const audience = stored.doc['audience'] as RunAudience | undefined;
      const createdAt = stored.doc['createdAt'] as number;
      const seen = seenRuns(audience);
      const p = new Params();
      const macros = {
        ORG: p.add(run.orgId),
        SESSION: p.add(run.sessionId),
        SEEN:
          seen === null
            ? `run_id <> ${p.add(run.runId)}`
            : `run_id = ANY(${p.add(seen.own.filter((id) => id !== run.runId))}::text[])`,
        CREATED: p.add(createdAt),
      };
      const [latest] = await runDocs(
        k,
        fill(
          `/* pipeshub:harness.runs_latest_seen */ SELECT doc FROM harness.runs
          WHERE org_id = @{ORG} AND session_id = @{SESSION} AND parent_run_id IS NULL
            AND @{SEEN}
            AND created_at <= @{CREATED}::double precision
          ORDER BY created_at DESC, admission_order DESC LIMIT 1`,
          macros,
        ),
        p.values,
      );
      const visible =
        seen === null || seen.visible.length === 0
          ? []
          : await runDocs(
              k,
              `/* pipeshub:harness.runs_visible */ SELECT doc FROM harness.runs
                WHERE org_id = $1 AND session_id = $2 AND run_id = ANY($3::text[])`,
              [run.orgId, run.sessionId, seen.visible],
            );
      return [
        ...(latest === undefined ? [] : [parseTurnLabel(latest['label'])]),
        ...(seen?.visible ?? []).map((runId) => {
          const found = visible.find((document) => document['runId'] === runId);
          return found === undefined ? null : parseTurnLabel(found['label']);
        }),
      ];
    });
  }

  /** Why a guard that passed every non-time check failed: its deadline or its lease. */
  private ownershipFailure(run: HarnessRun, now: number): Error {
    if (run.absoluteDeadlineAt <= now) {
      return new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    return new HarnessConflictError(
      'STALE_ATTEMPT',
      'Worker no longer owns this run',
    );
  }

  private async readReceipt(
    k: PgKernelTx,
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<{ payloadHash: string; receipt: unknown } | null> {
    const { rows } = await k.query<{ payload_hash: string; receipt: unknown }>(
      `/* pipeshub:harness.command_receipts_read */ SELECT payload_hash, receipt FROM harness.command_receipts
        WHERE org_id = $1 AND run_id = $2 AND command_id = $3`,
      [orgId, runId, commandId],
    );
    const row = rows[0];
    return row === undefined
      ? null
      : { payloadHash: row.payload_hash, receipt: decodeDoc(row.receipt) };
  }

  /** A stored receipt as the replay it answers; a command id used for another payload is refused. */
  private replayOf(
    prior: { payloadHash: string; receipt: unknown } | null | undefined,
    payloadHash: string,
  ): CommitReceipt | null {
    if (!prior) return null;
    if (prior.payloadHash !== payloadHash) {
      throw new HarnessConflictError(
        'IDEMPOTENCY_CONFLICT',
        'The command ID was already used with a different transition',
      );
    }
    return prior.receipt as CommitReceipt;
  }

  private async storeReceipt(
    k: PgKernelTx,
    command: { orgId: string; runId: string; commandId: string },
    payloadHash: string,
    receipt: CommitReceipt,
    now: number,
  ): Promise<void> {
    const { rowCount } = await k.query(
      `/* pipeshub:harness.command_receipts_insert */ INSERT INTO harness.command_receipts
         (org_id, run_id, command_id, payload_hash, receipt, created_at, region) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
       ON CONFLICT (org_id, run_id, command_id) DO NOTHING`,
      [
        command.orgId,
        command.runId,
        command.commandId,
        payloadHash,
        docParam(receipt),
        now,
        rowRegion(),
      ],
    );
    if (rowCount !== 1) throw new ReceiptTaken();
  }

  private async readWithNow(
    orgId: string,
    runId: string,
  ): Promise<{ run: HarnessRun | null; now: number }> {
    return inOrgRead(orgId, async (k) => ({
      run: runOfRow(await readRun(k, orgId, runId)),
      now: await this.time.now(k),
    }));
  }

  private async throwClaimFailure(
    orgId: string,
    runId: string,
    worker?: ClaimingWorker,
  ): Promise<never> {
    const { row, now } = await inOrgRead(orgId, async (k) => ({
      row: await readRun(k, orgId, runId),
      now: await this.time.now(k),
    }));
    if (row === null) throw new HarnessNotFoundError();
    if ((row.doc['absoluteDeadlineAt'] as number) <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    if (worker !== undefined && missingFor(placedRun(row), worker).length > 0) {
      throw workerIncompatible();
    }
    throw new HarnessConflictError('RUN_NOT_CLAIMABLE', 'Run is not claimable');
  }

  private attemptFailure(run: HarnessRun | null, now: number): never {
    if (run === null) throw new HarnessNotFoundError();
    if (run.absoluteDeadlineAt <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    throw new HarnessConflictError(
      'STALE_ATTEMPT',
      'Worker no longer owns this run',
    );
  }

  private validateLeaseDuration(durationMs: number): void {
    if (!Number.isSafeInteger(durationMs) || durationMs <= 0) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Lease duration must be positive',
      );
    }
  }

  private async resolveConcurrentCommand(
    orgId: string,
    runId: string,
    commandId: string,
    payloadHash: string,
  ): Promise<CommitReceipt> {
    const receipt = await inOrgRead(orgId, (k) =>
      this.readReceipt(k, orgId, runId, commandId),
    );
    if (receipt) {
      if (receipt.payloadHash !== payloadHash) {
        throw new HarnessConflictError(
          'IDEMPOTENCY_CONFLICT',
          'The command ID was already used with a different transition',
        );
      }
      // Lost the race to the identical command: a replay, so the service neither ends the turn nor projects again.
      return { ...plain(receipt.receipt as CommitReceipt), replayed: true };
    }
    throw new HarnessConflictError(
      'IDEMPOTENCY_CONFLICT',
      'A durable command identifier is already in use',
    );
  }
}

/** The ids of every run of one conversation. */
export async function pgRunIdsOfSession(
  orgId: string,
  sessionId: string,
): Promise<string[]> {
  return inOrgRead(orgId, async (k) => {
    const { rows } = await k.query<{ run_id: string }>(
      `/* pipeshub:harness.runs_ids_of_session */ SELECT run_id FROM harness.runs WHERE org_id = $1 AND session_id = $2`,
      [orgId, sessionId],
    );
    return rows.map((row) => row.run_id);
  });
}

/** One list state as a row predicate (ADR-UI-01 D1), the PostgreSQL reading of `runListState`. */
function pgStatePredicate(state: RunListState, p: Params): string {
  const statuses = `status = ANY(${p.add([...STATE_STATUSES[state]])}::text[])`;
  if (state === 'needs_you') {
    return `(${statuses} AND (status = 'waiting_approval' OR doc->'openInteractions'->0 IS NOT NULL))`;
  }
  if (state === 'working') {
    return `(${statuses} AND doc->'openInteractions'->0 IS NULL)`;
  }
  return `(${statuses})`;
}

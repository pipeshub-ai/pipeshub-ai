import type { Tx } from '../../uow';
import { isValidId } from '../../ids';
import { newObjectId } from '../../pg-util';
import { canonicalJson } from '../../../../modules/agent-harness/contracts/canonical-json';
import { admittedRunKind } from '../../../../modules/agent-harness/domain/run-kind';
import { memoryPin } from '../../../../modules/agent-harness/domain/memory';
import {
  AnsweredRun,
  answeredRunFrom,
  StoredAskingRun,
} from '../../../../modules/agent-harness/domain/answered-run';
import {
  HarnessConflictError,
  HarnessNotFoundError,
} from '../../../../modules/agent-harness/domain/errors';
import {
  AdmissionIdentity,
  admissionScopeKey,
  AdmitRunCommand,
  RunAdmissionResult,
  RunAdmissionStore,
  notLastAnswer,
  assertPinCurrent,
  rootRunDocument,
  rootRunRequirements,
  sessionJournalFeatures,
  durableSessionTitle,
} from '../../../../modules/agent-harness/domain/admission';
import { pinnedHarnessMajor } from '../../../../modules/agent-harness/domain/rollout';
import { keepsTypedOnly } from '../../../../modules/agent-harness/domain/commands';
import {
  CompactRequest,
  CompactRequests,
  withCompact,
} from '../../../../modules/agent-harness/domain/compact-requests';
import {
  admittedJournalAppend,
  HarnessRun,
  nodeAppend,
} from '../../../../modules/agent-harness/domain/execution';
import {
  assertReferencesChecked,
  inputQueuedEvent,
  noRunToSteer,
  steeringConflict,
  steeringFeatures,
  assertSteeredByActor,
  steeringRefusal,
} from '../../../../modules/agent-harness/domain/steering';
import {
  requirementsOf,
  withFeature,
} from '../../../../modules/agent-harness/domain/worker-compat';
import {
  JournalPreparer,
  JournalStore,
  validatingPreparer,
} from '../../../../modules/agent-harness/ports/journal-store';
import {
  ChatSessionRepository,
  SessionBinding,
} from '../../../../modules/agent-harness/ports/chat-session-repository';
import type { TurnRole } from '../../../../modules/agent-harness/domain/session-roles';
import {
  MAX_OWN_RUNS,
  projectHistoryTurns,
  withoutSandboxCommands,
  RunAudience,
  runAudience,
} from '../../../../modules/agent-harness/domain/session-audience';
import {
  LabelledTurn,
  MAX_VISIBLE_RUNS,
  parseTurnLabel,
} from '../../../../modules/agent-harness/domain/turn-labels';
import {
  NextRunEraser,
  NextRunOutcome,
  NextRunQueue,
  NextRunRecord,
  queuedNextRun,
  waitsAsNextRun,
  withoutInput,
} from '../../../../modules/agent-harness/domain/next-runs';
import { principalRefFor } from '../../../../modules/agent-harness/domain/run-spec';
import {
  ConversationWriter,
  UserTurn,
} from '../../../../modules/agent-harness/ports/conversation-writer';
import { HistorySource } from '../../../../modules/agent-harness/ports/history-source';
import { JournalEventInput } from '../../../../modules/agent-harness/domain/journal';
import {
  AccountedConversation,
  EMPTY_LEDGER,
  ImportPlan,
  planSessionImport,
  SessionImportSummary,
  sessionImportedEvent,
  historyLabelledEvent,
} from '../../../../modules/agent-harness/domain/session-import';
import { SessionImportContent } from '../../../../modules/agent-harness/application/session-import-content';
import { RunHistoryContent } from '../../../../modules/agent-harness/application/run-history-content';
import {
  RunInputSeal,
  UNSEALED_RUN_INPUT,
} from '../../../../modules/agent-harness/application/run-input-seal';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import {
  actorRefusal,
  ADMISSION_QUOTAS,
  cappedQuotas,
  AdmissionQuotas,
  assertHeldQuotas,
  QUOTA_ACTIVE_STATUSES,
  runsAhead,
} from '../../../../modules/agent-harness/domain/admission-quotas';
import {
  AdmissionQuotaHold,
  type AdmissionQuotaMetrics,
  TwinInFlight,
} from '../admission-quota-ledger/hold';
import { PgAdmissionQuotaLedger } from '../admission-quota-ledger/pg';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../../../../modules/agent-harness/application/harness-log';
import { RunKeyDigest } from '../../../../modules/agent-harness/domain/run-key-digest';
import { platformRunKeyDigest } from '../../../../modules/agent-harness/adapters/crypto/platform-run-key-digest';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgJournalStore } from '../journal/pg';
import { PgTime, pgServerTime } from '../time/pg';
import type { UnitHeads } from '../child-runs/pg';
import { PgHeadRevisions } from '../../../../modules/control-plane';
import {
  activeSessionKey,
  bumpSessionCounter,
  inOrgRead,
  inOrgTransaction,
  inRunOrg,
  insertRun,
  insertRunEvents,
  isUniqueViolation,
  ONE_ACTIVE_RUN,
  readSessionCounter,
  runDocs,
  saveRun,
  setCompactRequest,
  toRun,
  type RunDoc,
} from '../execution/pg-rows';

const RECEIPT_KEY = 'admission_receipts_pkey';
const RUN_KEY = 'runs_pkey';

export interface PgRunAdmissionStoreOptions {
  sessions: ChatSessionRepository<Tx>;
  history: HistorySource<Tx>;
  conversations: ConversationWriter<Tx>;
  dispatches: DispatchOutbox<Tx>;
  time?: PgTime;
  journal?: JournalStore<Tx>;
  quotas?: AdmissionQuotas;
  journals?: JournalPreparer;
  importContent?: SessionImportContent;
  /** Where a run's history is kept by reference (QA22-F2); inline when omitted. */
  historyContent?: RunHistoryContent;
  /** Seals the run's query in its document and receipt (LCFIX); kept in plaintext when omitted. */
  inputs?: RunInputSeal;
  runKeys?: RunKeyDigest;
  logger?: HarnessLogger;
  quotaMetrics?: AdmissionQuotaMetrics;
  /** What a pinned admission holds its pin to (CPD-5). */
  heads?: UnitHeads;
}

/**
 * The public admission boundary on PostgreSQL: the chat session, the user's message, the run, its first events, the
 * dispatch and the receipt commit in one unit as the admitting org. A second active run of a session breaks the
 * unique partial index on `harness.runs` (one active run per session); a second admission of one idempotency scope
 * breaks the receipt's key. Either aborts the unit, which then answers from what the winner committed.
 */
export class PgRunAdmissionStore
  implements RunAdmissionStore, NextRunQueue, NextRunEraser, CompactRequests
{
  private readonly sessions: ChatSessionRepository<Tx>;
  private readonly history: HistorySource<Tx>;
  private readonly conversations: ConversationWriter<Tx>;
  private readonly dispatches: DispatchOutbox<Tx>;
  private readonly time: PgTime;
  private readonly journal: JournalStore<Tx>;
  private readonly quotas: AdmissionQuotas;
  private readonly journals: JournalPreparer;
  private readonly importContent: SessionImportContent;
  private readonly historyContent: RunHistoryContent;
  private readonly inputs: RunInputSeal;
  private readonly runKeys: RunKeyDigest;
  private readonly logger: HarnessLogger;
  private readonly quotaLedger = new PgAdmissionQuotaLedger();
  private readonly quotaMetrics: AdmissionQuotaMetrics | undefined;
  private readonly heads: UnitHeads;

  constructor(options: PgRunAdmissionStoreOptions) {
    this.sessions = options.sessions;
    this.history = options.history;
    this.conversations = options.conversations;
    this.dispatches = options.dispatches;
    this.time = options.time ?? pgServerTime;
    this.journal = options.journal ?? new PgJournalStore(this.time);
    this.quotas = options.quotas ?? ADMISSION_QUOTAS;
    this.journals = options.journals ?? validatingPreparer;
    this.importContent =
      options.importContent ?? new SessionImportContent(null, null);
    this.historyContent =
      options.historyContent ?? new RunHistoryContent(null, null);
    this.inputs = options.inputs ?? UNSEALED_RUN_INPUT;
    this.runKeys = options.runKeys ?? platformRunKeyDigest();
    this.logger = options.logger ?? silentHarnessLogger;
    this.quotaMetrics = options.quotaMetrics;
    this.heads = options.heads ?? new PgHeadRevisions();
  }

  async admit(
    command: AdmitRunCommand,
    callerNow: number,
  ): Promise<RunAdmissionResult> {
    if (![command.orgId, command.actorId].every((value) => isValidId(value))) {
      throw new HarnessNotFoundError();
    }
    if (command.sessionId !== null && !isValidId(command.sessionId)) {
      throw new HarnessNotFoundError();
    }
    return inRunOrg(command.orgId, () => this.admitInOrg(command, callerNow));
  }

  private async admitInOrg(
    command: AdmitRunCommand,
    callerNow: number,
  ): Promise<RunAdmissionResult> {
    // The caller chose a duration; timestamps and the deadline are anchored on DB time so a skewed Node clock cannot
    // shorten or stretch the run.
    const now = await inOrgRead(command.orgId, (k) => this.time.now(k));
    const absoluteDeadlineAt = now + (command.absoluteDeadlineAt - callerNow);
    if (absoluteDeadlineAt <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline must be in the future',
      );
    }
    const scopeKey = admissionScopeKey(command);
    const sessionId = command.sessionId ?? newObjectId();
    const hold = new AdmissionQuotaHold(
      this.quotaLedger,
      cappedQuotas(this.quotas, command.quotaCaps),
      { ...command, admission: scopeKey },
      this.quotaMetrics,
    );
    try {
      let result: RunAdmissionResult;
      for (;;) {
        // In a unit of its own before the admission's: whoever reserves after this hold ended reads its run.
        await hold.reserve(now);
        try {
          result = await inOrgTransaction(command.orgId, async (tx, k) => {
            hold.attempt();
            const admitted = await this.attempt(
              command,
              { scopeKey, sessionId, now, absoluteDeadlineAt },
              tx,
              k,
              hold,
            );
            hold.committing();
            return admitted;
          });
          break;
        } catch (error) {
          if (!(error instanceof TwinInFlight)) throw error;
          await hold.afterTwin();
        }
      }
      hold.committed();
      return structuredClone({
        ...result,
        run: await this.inputs.open(result.run),
      });
    } catch (error) {
      if (!isUniqueViolation(error, [RECEIPT_KEY, ONE_ACTIVE_RUN, RUN_KEY])) {
        throw error;
      }
      const previous = await inOrgRead(command.orgId, (k) =>
        this.receipt(k, command.orgId, scopeKey),
      );
      if (previous) {
        return await this.readReceipt(
          previous.payloadHash,
          previous.result,
          command.payloadHash,
        );
      }
      const active = await inOrgRead(command.orgId, (k) =>
        this.activeRun(k, command.orgId, sessionId, false),
      );
      if (active !== null) {
        throw new HarnessConflictError(
          'RUN_ACTIVE',
          'The session already has an active root run',
        );
      }
      throw new HarnessConflictError(
        'IDEMPOTENCY_CONFLICT',
        'A durable admission identifier is already in use',
      );
    } finally {
      await this.settleQuotas(hold, command);
    }
  }

  private async attempt(
    command: AdmitRunCommand,
    at: {
      scopeKey: string;
      sessionId: string;
      now: number;
      absoluteDeadlineAt: number;
    },
    tx: Tx,
    k: PgKernelTx,
    hold: AdmissionQuotaHold,
  ): Promise<RunAdmissionResult> {
    const { scopeKey, sessionId, now, absoluteDeadlineAt } = at;
    const role: TurnRole =
      command.asCollaborator === true ? 'collaborator' : 'owner';
    const previous = await this.receipt(k, command.orgId, scopeKey);
    if (previous) {
      return this.readReceipt(
        previous.payloadHash,
        previous.result,
        command.payloadHash,
      );
    }
    if (command.steerOnly === true && command.sessionId === null) {
      throw noRunToSteer();
    }
    if (command.regenerate !== undefined && command.sessionId === null) {
      throw notLastAnswer();
    }
    let previousConversations: Array<Record<string, unknown>> = [];
    let imported: ImportPlan | null = null;
    let binding: SessionBinding | null = null;
    // The session's counter and ledger as this unit leaves them: the turn's write needs no read of it.
    let accounted: AccountedConversation = { lastSeq: 0, ledger: EMPTY_LEDGER };
    const sessionScope = {
      orgId: command.orgId,
      actorId: command.actorId,
      sessionId,
      ...(command.agentKey !== undefined ? { agentKey: command.agentKey } : {}),
    };
    const createdSession =
      command.sessionId === null
        ? { title: durableSessionTitle(command) }
        : undefined;
    if (createdSession !== undefined) {
      await this.sessions.createDurableSession(
        {
          ...sessionScope,
          title: createdSession.title,
          model: command.input.model,
          now,
        },
        tx,
      );
    } else {
      binding = await this.sessions.bindDurableSession(
        sessionScope,
        now,
        tx,
        role,
      );
      if (binding === null) throw new HarnessNotFoundError();
      accounted = binding.accounted;
      if (
        command.regenerate !== undefined &&
        !(await this.conversations.hasAnswer(
          { orgId: command.orgId, sessionId },
          command.regenerate.messageId,
          tx,
        ))
      ) {
        throw new HarnessNotFoundError();
      }
      const active = await this.activeRun(k, command.orgId, sessionId, true);
      if (active !== null) {
        const activeRun = toRun(active);
        // A `/run` is one at a time per chat (SLASH3): never steered into a reply, never queued behind one.
        if (
          command.nextRunId !== undefined ||
          command.regenerate !== undefined ||
          command.input.sandboxCommand !== undefined
        ) {
          throw new HarnessConflictError(
            'RUN_ACTIVE',
            'The session already has an active root run',
          );
        }
        const result = waitsAsNextRun(activeRun, command)
          ? await this.enqueueNextRun(
              { ...command, absoluteDeadlineAt },
              active,
              now,
              k,
              hold,
            )
          : await this.steer(command, active, now, tx, k, hold);
        await this.storeReceipt(
          k,
          command,
          scopeKey,
          { ...result, run: await this.inputs.seal(result.run) },
          now,
        );
        return result;
      }
      if (command.steerOnly === true) throw noRunToSteer();
    }
    // A refusal throws and aborts the unit: nothing is written.
    await this.enforceQuotas(command, hold, now, k);
    let audience: RunAudience | null = null;
    // Present once a run was admitted here; it also says what the session's stream holds that this run will fold (SS1).
    const sessionCounter =
      command.sessionId === null
        ? null
        : await readSessionCounter(k, command.orgId, sessionId);
    let input = command.input;
    let turn: UserTurn = {
      query: command.input.query,
      ...(command.input.appliedFilters !== undefined
        ? { appliedFilters: command.input.appliedFilters }
        : {}),
      chatMode: command.input.model.chatMode,
      attachments: command.input.attachments,
      ...(command.input.command !== undefined
        ? { command: command.input.command }
        : {}),
    };
    if (command.regenerate !== undefined) {
      const asked = await this.conversations.retractLastAnswer(
        { orgId: command.orgId, sessionId },
        command.regenerate.messageId,
        tx,
      );
      if (asked === null) throw notLastAnswer();
      // The stored question, never the browser's copy of it.
      turn = { ...asked, chatMode: command.input.model.chatMode };
      input = {
        ...command.input,
        // A `/review` message keeps only what was typed: the route expanded it again (LC2FIX).
        query:
          keepsTypedOnly(asked.command) &&
          command.regenerate.query !== undefined
            ? command.regenerate.query
            : asked.query,
        attachments: asked.attachments,
        ...(asked.command !== undefined ? { command: asked.command } : {}),
        ...(asked.appliedFilters !== undefined
          ? {
              appliedFilters: asked.appliedFilters,
            }
          : {}),
      };
    }
    if (command.sessionId !== null) {
      // History is captured in the admission's unit, before the new user message is appended: the worker receives
      // the authoritative transcript and never trusts a browser-supplied history array.
      const ref = { orgId: command.orgId, sessionId };
      const history = await this.history.read(ref, tx, accounted);
      previousConversations = withoutSandboxCommands(
        history.messages.flatMap((m) => m.turns),
      );
      audience = await this.audienceOf(
        k,
        command,
        role,
        binding as SessionBinding,
        sessionCounter?.actors ?? [],
      );
      if (audience !== null) {
        previousConversations = projectHistoryTurns(
          previousConversations,
          principalRefFor(command.actorId),
          audience.ownerRef,
        );
      }
      // A v1 run has no journal to import into (v1 is failed on claim).
      if (command.harnessMajor === 2) {
        // Whether a run was ever admitted here, not whether the stream has events: a share journals onto a session
        // that never ran (SS1).
        const planned = planSessionImport(history, sessionCounter !== null);
        imported = planned.plan;
        // Saved with the user's turn, in its one write of the session.
        accounted = { lastSeq: history.lastSeq, ledger: planned.ledger };
      }
    }

    if (command.sessionId !== null && sessionCounter?.compactRequest) {
      // The person's /compact waiting on the session (SLASH2 C2): this run takes it, in this unit.
      input = withCompact(
        input,
        command.harnessMajor,
        sessionCounter.compactRequest,
      );
      if (input.compact !== undefined) {
        await setCompactRequest(k, command.orgId, sessionId, null);
      }
    }

    const userMessageKey = await this.conversations.appendUserTurn(
      { orgId: command.orgId, sessionId },
      {
        ...turn,
        ...(role === 'collaborator' ? { senderId: command.actorId } : {}),
      },
      tx,
      accounted,
    );

    const run: HarnessRun & { activeSessionKey: string } = {
      ...rootRunDocument(command, {
        sessionId,
        userMessageKey,
        idempotencyKey: this.runKeys.digest(
          command.orgId,
          command.idempotencyKey,
        ),
        absoluteDeadlineAt,
        input: structuredClone(input),
        now,
      }),
      activeSessionKey: activeSessionKey(command.orgId, sessionId),
      ...(audience !== null ? { sessionRole: audience.sessionRole } : {}),
    };
    const history = await this.historyContent.store(
      run,
      previousConversations,
      tx,
    );
    if (history === null && run.input) {
      run.input.previousConversations = previousConversations;
    }
    const memory = memoryPin(command.memory, audience !== null);
    const requires = rootRunRequirements(
      run,
      command,
      sessionCounter?.journalFeatures ?? [],
      audience,
      memory,
    );
    if (requires !== undefined) run.requires = requires;
    if (command.resolved !== undefined) {
      // The linearisation point (02-lld §6.3): a control-plane write committed after the pin was read refuses it.
      assertPinCurrent(command, await this.heads.heads(command.orgId, tx));
    }
    const sealed = await this.inputs.seal(run);
    await insertRun(k, {
      ...sealed,
      ...(command.resolved !== undefined
        ? { resolved: canonicalJson(command.resolved) }
        : {}),
      agentDefinition: command.agentDefinition ?? null,
      engineProfile: command.engineProfile ?? null,
      ...(command.codeTaskAutoRoute === false
        ? { codeTaskAutoRoute: false }
        : {}),
      ...(command.repository !== undefined
        ? { repository: command.repository }
        : {}),
      ...(command.gates !== undefined ? { gates: command.gates } : {}),
      ...(command.answered !== undefined ? { answered: command.answered } : {}),
      modelAccess: command.modelAccess ?? null,
      via: command.via,
      ...(command.surface !== undefined ? { surface: command.surface } : {}),
      ...(audience !== null ? { audience } : {}),
      ...(command.trigger !== undefined ? { trigger: command.trigger } : {}),
      ...(memory !== null ? { memory } : {}),
      ...(history !== null
        ? { input: { ...sealed.input, historyRef: history } }
        : {}),
    });
    let importSummary: SessionImportSummary | undefined;
    if (run.harnessMajor === 2) {
      let importEvent: JournalEventInput[] | undefined;
      if (imported !== null) {
        const stored = await this.importContent.store(run, imported, tx);
        importEvent = [
          sessionImportedEvent(imported, stored.ref),
          historyLabelledEvent(stored.ref, imported.label),
        ];
        importSummary = {
          upToMessageSeq: imported.upToMessageSeq,
          messages: imported.turns.length,
          digest: stored.ref.hash,
          replacesHistory: imported.replacesHistory,
          sealed: stored.sealed,
          ...(imported.mismatch !== undefined
            ? { mismatch: imported.mismatch }
            : {}),
        };
      }
      await this.journal.append(
        admittedJournalAppend(
          run,
          command.via,
          importEvent,
          admittedRunKind(run, command.trigger !== undefined),
          command.notices,
        ),
        tx,
      );
    }

    // Every later run of the session folds this run's sessionRole (SS1 rule), and what its gates journal.
    const counter = await bumpSessionCounter(k, command.orgId, sessionId, {
      events: 1,
      actors: [command.actorId],
      features: sessionJournalFeatures(run, command),
    });
    const eventSeq = counter.nextEventSeq;
    await insertRunEvents(k, [
      {
        orgId: command.orgId,
        sessionId,
        runId: command.runId,
        eventSeq,
        commitSeq: 0,
        type: 'run_queued',
        data: {},
        createdAt: now,
      },
    ]);
    // An inline run is claimed by the process that admitted it; the sweeper dispatches it only if that claim does
    // not come.
    if (run.profile === 'dispatched') {
      await this.dispatches.enqueue(
        [
          {
            orgId: command.orgId,
            commandId: command.dispatchCommandId,
            runId: command.runId,
            kind: 'run_ready',
            createdAt: now,
          },
        ],
        tx,
      );
    }

    const { activeSessionKey: _active, ...publicRun } = run;
    const result: RunAdmissionResult = {
      sessionId,
      userMessageKey,
      run: structuredClone(publicRun),
      firstEventSeq: eventSeq,
      lastEventSeq: eventSeq,
      created: true,
      ...(importSummary !== undefined ? { imported: importSummary } : {}),
    };
    // The run's own sealed input: an inline history is sealed once per admission (GAPS2-1).
    await this.storeReceipt(
      k,
      command,
      scopeKey,
      { ...result, run: { ...result.run, input: sealed.input } },
      now,
    );
    return createdSession !== undefined
      ? { ...result, createdSession }
      : result;
  }

  private async settleQuotas(
    hold: AdmissionQuotaHold,
    command: AdmitRunCommand,
  ): Promise<void> {
    try {
      await hold.settle();
    } catch (error) {
      this.logger.warn(
        'admission.quota_release_failed',
        harnessLogMeta('admission.quota_release_failed', {
          orgId: command.orgId,
          runId: command.runId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'hold_lapses',
        }),
      );
    }
  }

  private async receipt(
    k: PgKernelTx,
    orgId: string,
    scopeKey: string,
  ): Promise<{ payloadHash: string; result: unknown } | null> {
    const { rows } = await k.query<{ payload_hash: string; result: unknown }>(
      `/* pipeshub:harness.admission_receipts_read */ SELECT payload_hash, result FROM harness.admission_receipts
        WHERE org_id = $1 AND scope_key = $2`,
      [orgId, scopeKey],
    );
    const row = rows[0];
    return row === undefined
      ? null
      : { payloadHash: row.payload_hash, result: decodeDoc(row.result) };
  }

  private async storeReceipt(
    k: PgKernelTx,
    command: AdmitRunCommand,
    scopeKey: string,
    result: unknown,
    now: number,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.admission_receipts_insert */ INSERT INTO harness.admission_receipts
         (org_id, scope_key, payload_hash, result, created_at, region) VALUES ($1, $2, $3, $4::jsonb, $5, $6)`,
      [
        command.orgId,
        scopeKey,
        command.payloadHash,
        docParam(result),
        now,
        rowRegion(),
      ],
    );
  }

  /** The session's active run; `lock`: held to commit, as a steer or a next run writes it. */
  private async activeRun(
    k: PgKernelTx,
    orgId: string,
    sessionId: string,
    lock: boolean,
  ): Promise<RunDoc | null> {
    const [doc] = await runDocs(
      k,
      lock
        ? `/* pipeshub:harness.runs_active_lock */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND session_id = $2 AND active FOR UPDATE`
        : `/* pipeshub:harness.runs_active_read */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND session_id = $2 AND active`,
      [orgId, sessionId],
    );
    return doc ?? null;
  }

  /**
   * Whose turns the run may see (SS2), when it is in a shared context: its actor's earlier root runs of the session,
   * read in the admission's unit (one indexed read, paid only in a shared context).
   */
  private async audienceOf(
    k: PgKernelTx,
    command: AdmitRunCommand,
    role: TurnRole,
    binding: SessionBinding,
    actors: readonly string[],
  ): Promise<RunAudience | null> {
    const context = {
      ownerId: binding.ownerId,
      shared: binding.shared,
      actors,
    };
    if (runAudience(command.actorId, role, context, []) === null) return null;
    const { rows } = await k.query<{ run_id: string }>(
      `/* pipeshub:harness.runs_own_root */ SELECT run_id FROM harness.runs
        WHERE org_id = $1 AND session_id = $2 AND actor_id = $3 AND parent_run_id IS NULL
        ORDER BY created_at DESC, run_id DESC LIMIT $4`,
      [command.orgId, command.sessionId, command.actorId, MAX_OWN_RUNS],
    );
    return runAudience(
      command.actorId,
      role,
      context,
      rows.map((row) => row.run_id).reverse(),
      command.visibleRuns ?? [],
    );
  }

  /**
   * A message to someone else's running task waits for it to end (SS2): the admission as decided now is kept, and
   * the active run (locked) is written, so this and the run's terminal commit cannot miss each other. Only the rate
   * windows count it.
   */
  private async enqueueNextRun(
    command: AdmitRunCommand,
    active: RunDoc,
    now: number,
    k: PgKernelTx,
    hold: AdmissionQuotaHold,
  ): Promise<RunAdmissionResult> {
    await this.enforceQuotas(command, hold, now, k, false);
    const touched = {
      ...active,
      nextRunsQueued:
        ((active['nextRunsQueued'] as number | undefined) ?? 0) + 1,
    };
    await saveRun(k, touched);
    const activeRun = toRun(touched);
    const { rows } = await k.query<{ n: string }>(
      `/* pipeshub:harness.next_runs_ahead */ SELECT count(*)::text AS n FROM harness.next_runs
        WHERE org_id = $1 AND session_id = $2 AND status = 'pending'`,
      [activeRun.orgId, activeRun.sessionId],
    );
    const ahead = Number(rows[0]?.n ?? 0);
    const id = newObjectId();
    await k.query(
      `/* pipeshub:harness.next_runs_insert */ INSERT INTO harness.next_runs (org_id, id, doc, region) VALUES ($1, $2, $3::jsonb, $4)`,
      [
        activeRun.orgId,
        id,
        docParam({
          orgId: activeRun.orgId,
          sessionId: activeRun.sessionId,
          actorId: command.actorId,
          runId: command.runId,
          behindRunId: activeRun.runId,
          status: 'pending',
          command: structuredClone(command),
          createdAt: now,
        }),
        rowRegion(),
      ],
    );
    return {
      sessionId: activeRun.sessionId,
      userMessageKey: '',
      run: withoutInput(activeRun),
      firstEventSeq: 0,
      lastEventSeq: 0,
      created: true,
      nextRun: queuedNextRun({ id, runId: command.runId }, ahead + 1),
    };
  }

  async nextPending(
    orgId: string,
    sessionId: string,
  ): Promise<NextRunRecord | null> {
    return inOrgRead(orgId, async (k) => {
      const { rows } = await k.query<{ id: string; doc: unknown }>(
        `/* pipeshub:harness.next_runs_pending */ SELECT id, doc FROM harness.next_runs
          WHERE org_id = $1 AND session_id = $2 AND status = 'pending' ORDER BY created_at, id LIMIT 1`,
        [orgId, sessionId],
      );
      const row = rows[0];
      if (row === undefined) return null;
      const document = decodeDoc<Record<string, unknown>>(row.doc);
      if (document['command'] === null || document['command'] === undefined) {
        return null;
      }
      return {
        id: row.id,
        orgId: document['orgId'] as string,
        sessionId: document['sessionId'] as string,
        actorId: document['actorId'] as string,
        runId: document['runId'] as string,
        behindRunId: document['behindRunId'] as string,
        command: structuredClone(document['command']) as AdmitRunCommand,
        createdAt: document['createdAt'] as number,
      };
    });
  }

  /** Cross-org (the drain's backstop): as the jobs role. */
  async drainable(
    limit: number,
  ): Promise<Array<{ orgId: string; sessionId: string }>> {
    return jobsUnit('agent-harness.drainable', { write: false }, async (k) => {
      const { rows } = await k.query<{ org_id: string; session_id: string }>(
        `/* pipeshub:harness.next_runs_drainable */ SELECT n.org_id, n.session_id FROM harness.next_runs n
          WHERE n.status = 'pending'
          GROUP BY n.org_id, n.session_id
          HAVING NOT EXISTS (SELECT 1 FROM harness.runs r WHERE r.org_id = n.org_id AND r.session_id = n.session_id AND r.active)
          ORDER BY min(n.created_at), n.org_id, n.session_id LIMIT $1`,
        [Math.max(limit, 1)],
      );
      return rows.map((row) => ({
        orgId: row.org_id,
        sessionId: row.session_id,
      }));
    });
  }

  async settleNextRun(
    orgId: string,
    id: string,
    outcome: NextRunOutcome,
    now: number,
  ): Promise<void> {
    await inOrgRead(
      orgId,
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.next_runs_lock */ SELECT doc FROM harness.next_runs
            WHERE org_id = $1 AND id = $2 AND status = 'pending' FOR UPDATE`,
          [orgId, id],
        );
        if (rows[0] === undefined) return;
        // Admitted, the message lives on in the conversation; refused, it is dropped.
        const { command: _command, ...document } = decodeDoc<
          Record<string, unknown>
        >(rows[0].doc);
        await k.query(
          `/* pipeshub:harness.next_runs_settle */ UPDATE harness.next_runs SET doc = $3::jsonb WHERE org_id = $1 AND id = $2`,
          [
            orgId,
            id,
            docParam({
              ...document,
              status: outcome.status,
              settledAt: now,
              ...(outcome.status === 'admitted'
                ? { runId: outcome.runId }
                : { reason: outcome.reason }),
            }),
          ],
        );
      },
      true,
    );
  }

  async dropNextRuns(orgId: string, sessionId: string): Promise<number> {
    return inOrgRead(
      orgId,
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.next_runs_drop */ DELETE FROM harness.next_runs WHERE org_id = $1 AND session_id = $2`,
          [orgId, sessionId],
        );
        // The person's waiting /compact holds their focus text: erased with the session, though its counter is kept.
        await setCompactRequest(k, orgId, sessionId, null);
        return rowCount ?? 0;
      },
      true,
    );
  }

  /**
   * The message joins the session's active run (steering, T2): its seq is allocated on the locked run, and
   * `input.queued` carries the text. Only the rate windows count it; it adds no run.
   */
  private async steer(
    command: AdmitRunCommand,
    active: RunDoc,
    now: number,
    tx: Tx,
    k: PgKernelTx,
    hold: AdmissionQuotaHold,
  ): Promise<RunAdmissionResult> {
    const run = toRun(active);
    assertSteeredByActor(run, command.actorId);
    const refused = steeringRefusal(run, command.input);
    if (refused !== null) throw steeringConflict(refused);
    assertReferencesChecked(command.input, command.referencesAuthorized);
    await this.enforceQuotas(command, hold, now, k, false);
    const inputSeq = (run.inputSeq ?? 0) + 1;
    const updated: RunDoc = {
      ...active,
      inputSeq,
      pendingInputs: [...(run.pendingInputs ?? []), inputSeq],
      // From now on only a worker that folds queued input may take it (RU-2).
      requires: {
        ...(active['requires'] ?? {}),
        ...withSteering(run, command.input),
      },
    };
    await saveRun(k, updated);
    const queued = toRun(updated);
    const messageSeq = queued.inputSeq ?? 1;
    await this.journal.append(
      await this.journals.prepare(
        nodeAppend(queued, [
          inputQueuedEvent(messageSeq, command.input, command.actorId),
        ]),
        tx,
      ),
      tx,
    );
    await bumpSessionCounter(k, queued.orgId, queued.sessionId, {
      features: steeringFeatures(command.input),
    });
    return {
      sessionId: queued.sessionId,
      userMessageKey: queued.userMessageKey,
      run: queued,
      firstEventSeq: 0,
      lastEventSeq: 0,
      created: true,
      queued: { messageSeq },
    };
  }

  /**
   * The actor's and the org's places were reserved before the unit (`hold`), so a person's admissions do not queue
   * on one row for each other's whole unit, each holding a pooled connection (DOS-MONGO-1): the active runs of each
   * are the ones this unit reads committed and the ones of admissions that reserved earlier and have not committed,
   * so two admissions cannot both take the last place. `countsActive` false: the rate windows alone (a steering
   * message).
   */
  private async enforceQuotas(
    command: AdmitRunCommand,
    hold: AdmissionQuotaHold,
    now: number,
    k: PgKernelTx,
    countsActive = true,
  ): Promise<void> {
    hold.assertNoTwin();
    const quotas = cappedQuotas(this.quotas, command.quotaCaps);
    if (!countsActive) {
      assertHeldQuotas(await hold.steeringPlace(now), null, now, quotas);
      hold.count();
      return;
    }
    const held = hold.reserved();
    if (held.org === null) throw actorRefusal(held.user, now, quotas);
    // The org's and the actor's active runs in one read of the `(org_id, status, actor_id)` index.
    const { rows } = await k.query<{ org: string; actor: string }>(
      `/* pipeshub:harness.runs_quota_counts */ SELECT count(*)::text AS org,
         count(*) FILTER (WHERE actor_id = $3)::text AS actor
       FROM harness.runs WHERE org_id = $1 AND status = ANY($2::text[])`,
      [command.orgId, [...QUOTA_ACTIVE_STATUSES], command.actorId],
    );
    const counts = rows[0];
    const ahead = runsAhead(held);
    assertHeldQuotas(
      held,
      {
        orgActive: Number(counts?.org ?? 0),
        userActive: Number(counts?.actor ?? 0),
        seen:
          ahead.length === 0
            ? new Set()
            : await this.committedRuns(k, command.orgId, ahead),
      },
      now,
      quotas,
    );
    hold.count();
  }

  /** Which of these runs the unit reads committed, whatever their status. */
  private async committedRuns(
    k: PgKernelTx,
    orgId: string,
    runIds: readonly string[],
  ): Promise<Set<string>> {
    const { rows } = await k.query<{ run_id: string }>(
      `/* pipeshub:harness.runs_ids_present */ SELECT run_id FROM harness.runs WHERE org_id = $1 AND run_id = ANY($2::text[])`,
      [orgId, [...runIds]],
    );
    return new Set(rows.map((row) => row.run_id));
  }

  async requestCompact(
    orgId: string,
    sessionId: string,
    request: CompactRequest,
  ): Promise<boolean> {
    // The counter exists once a run was admitted in the session: before that there is nothing to compact.
    return inOrgRead(
      orgId,
      (k) => setCompactRequest(k, orgId, sessionId, { ...request }),
      true,
    );
  }

  async answeredRun(
    orgId: string,
    sessionId: string,
    runId?: string,
  ): Promise<AnsweredRun | null> {
    return inOrgRead(orgId, async (k) => {
      const [stored] = await runDocs(
        k,
        `/* pipeshub:harness.runs_answered */ SELECT doc FROM harness.runs
          WHERE org_id = $1 AND session_id = $2 AND parent_run_id IS NULL AND ($3::text IS NULL OR run_id = $3)
          ORDER BY created_at DESC, admission_order DESC LIMIT 1`,
        [orgId, sessionId, runId ?? null],
      );
      return stored === undefined
        ? null
        : answeredRunFrom(
            stored as unknown as StoredAskingRun,
            runId !== undefined,
          );
    });
  }

  async labelledTurns(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<LabelledTurn[]> {
    return inOrgRead(orgId, async (k) => {
      const runs = await runDocs(
        k,
        `/* pipeshub:harness.runs_labelled_turns */ SELECT doc FROM harness.runs
          WHERE org_id = $1 AND session_id = $2 AND parent_run_id IS NULL AND actor_id <> $3
            AND doc->'label'->'complete' = 'true'::jsonb
          ORDER BY created_at DESC, admission_order DESC LIMIT $4`,
        [orgId, sessionId, actorId, MAX_VISIBLE_RUNS],
      );
      return runs.map((run) => ({
        id: run['runId'] as string,
        label: parseTurnLabel(run['label']),
      }));
    });
  }

  async findAdmission(
    command: AdmissionIdentity & Pick<AdmitRunCommand, 'payloadHash'>,
  ): Promise<RunAdmissionResult | null> {
    const previous = await inOrgRead(command.orgId, (k) =>
      this.receipt(k, command.orgId, admissionScopeKey(command)),
    );
    return previous
      ? await this.readReceipt(
          previous.payloadHash,
          previous.result,
          command.payloadHash,
        )
      : null;
  }

  private async readReceipt(
    storedHash: string,
    storedResult: unknown,
    requestedHash: string,
  ): Promise<RunAdmissionResult> {
    if (storedHash !== requestedHash) {
      throw new HarnessConflictError(
        'IDEMPOTENCY_CONFLICT',
        'The idempotency key was already used with a different payload',
      );
    }
    const result = structuredClone(storedResult) as RunAdmissionResult;
    return {
      ...result,
      run: {
        ...(await this.inputs.open(result.run)),
        harnessMajor: pinnedHarnessMajor(result.run.harnessMajor),
      },
      created: false,
    };
  }
}

/** `withFeatureExpr(...steeringFeatures(input))`: what the run needs once it folds queued input. */
function withSteering(
  run: HarnessRun,
  input: AdmitRunCommand['input'],
): ReturnType<typeof withFeature> {
  return withFeature(requirementsOf(run), ...steeringFeatures(input));
}

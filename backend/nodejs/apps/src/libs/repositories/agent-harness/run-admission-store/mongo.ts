import { idOperator } from '../../id-filter-guard/mongo';
import { mongoTx } from '../durable-transaction/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { canonicalJson } from '../../../../modules/agent-harness/contracts/canonical-json';
import { admittedRunKind } from '../../../../modules/agent-harness/domain/run-kind';
import { memoryPin } from '../../../../modules/agent-harness/domain/memory';
import {
  AnsweredRun,
  answeredRunFrom,
} from '../../../../modules/agent-harness/domain/answered-run';
import mongoose, { ClientSession, Types } from 'mongoose';
import {
  DURABLE_TRANSACTION_OPTIONS,
  durableCommits,
} from '../durable-transaction/mongo';
import {
  createInTransaction,
  transactionWrites,
} from '../transaction-writes/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessUnavailableError,
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
import { MongoHeadRevisions } from '../../../../modules/control-plane';
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
  JournalPreparer,
  JournalStore,
  validatingPreparer,
} from '../../../../modules/agent-harness/ports/journal-store';
import { withFeatureExpr } from '../worker-compat-expr/mongo';
import { MongoJournalStore } from '../journal/mongo';
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
  AdmissionQuotaMetrics,
  MongoAdmissionQuotaLedger,
  TwinInFlight,
} from '../admission-quota-ledger/mongo';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../../../../modules/agent-harness/application/harness-log';
import {
  AgentAdmissionReceiptModel,
  AgentEventCounterModel,
  AgentEventModel,
  AgentNextRunModel,
  AgentRunModel,
  runFromDocument,
} from '../execution/schema/execution.schema';
import { envFlag } from '../../../utils/env-flags';
import { RunKeyDigest } from '../../../../modules/agent-harness/domain/run-key-digest';
import { platformRunKeyDigest } from '../../../../modules/agent-harness/adapters/crypto/platform-run-key-digest';

const activeSessionKey = (orgId: string, sessionId: string): string =>
  JSON.stringify([orgId, sessionId]);

function isDuplicateKeyError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    error.code === 11000
  );
}

export interface MongoRunAdmissionStoreOptions {
  sessions: ChatSessionRepository<Tx>;
  history: HistorySource<Tx>;
  conversations: ConversationWriter<Tx>;
  dispatches: DispatchOutbox<Tx>;
  transactionsAvailable?: boolean;
  time?: MongoTime;
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
}

/** The head rows an admission holds its pin to: stateless point reads. */
const HEADS = new MongoHeadRevisions();

/**
 * Mongo-specific unit of work for the complete public admission boundary.
 * ChatSession, user message, run, initial event, dispatch outbox row, and receipt
 * are committed in one transaction; application code sees only RunAdmissionStore.
 */
export class MongoRunAdmissionStore
  implements RunAdmissionStore, NextRunQueue, NextRunEraser, CompactRequests
{
  private readonly sessions: ChatSessionRepository<Tx>;
  private readonly history: HistorySource<Tx>;
  private readonly conversations: ConversationWriter<Tx>;
  private readonly dispatches: DispatchOutbox<Tx>;
  private readonly transactionsAvailable: boolean;
  private readonly time: MongoTime;
  private readonly journal: JournalStore<Tx>;
  private readonly quotas: AdmissionQuotas;
  private readonly journals: JournalPreparer;
  private readonly importContent: SessionImportContent;
  private readonly historyContent: RunHistoryContent;
  private readonly inputs: RunInputSeal;
  private readonly runKeys: RunKeyDigest;
  private readonly logger: HarnessLogger;
  private readonly quotaLedger = new MongoAdmissionQuotaLedger();
  private readonly quotaMetrics: AdmissionQuotaMetrics | undefined;

  constructor(options: MongoRunAdmissionStoreOptions) {
    this.sessions = options.sessions;
    this.history = options.history;
    this.conversations = options.conversations;
    this.dispatches = options.dispatches;
    this.transactionsAvailable =
      options.transactionsAvailable ?? envFlag('REPLICA_SET_AVAILABLE');
    this.time = options.time ?? mongoServerTime;
    this.journal = options.journal ?? new MongoJournalStore(this.time);
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
  }

  async admit(
    command: AdmitRunCommand,
    callerNow: number,
  ): Promise<RunAdmissionResult> {
    if (!this.transactionsAvailable) {
      throw new HarnessUnavailableError(
        'Durable agent runs require MongoDB replica-set transactions',
      );
    }
    if (
      ![command.orgId, command.actorId].every((value) =>
        Types.ObjectId.isValid(value),
      )
    ) {
      throw new HarnessNotFoundError();
    }
    if (
      command.sessionId !== null &&
      !Types.ObjectId.isValid(command.sessionId)
    ) {
      throw new HarnessNotFoundError();
    }
    // The caller chose a duration; timestamps and the deadline are anchored on
    // DB time so a skewed Node clock cannot shorten or stretch the run.
    const now = await this.time.read();
    const absoluteDeadlineAt = now + (command.absoluteDeadlineAt - callerNow);
    if (absoluteDeadlineAt <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline must be in the future',
      );
    }

    const scopeKey = admissionScopeKey(command);
    const sessionId = command.sessionId ?? new Types.ObjectId().toString();
    const role: TurnRole =
      command.asCollaborator === true ? 'collaborator' : 'owner';
    const mongoSession = await mongoose.startSession();
    const hold = new AdmissionQuotaHold(
      this.quotaLedger,
      cappedQuotas(this.quotas, command.quotaCaps),
      { ...command, admission: scopeKey },
      this.quotaMetrics,
    );
    try {
      let result: RunAdmissionResult | undefined;
      let createdSession: RunAdmissionResult['createdSession'];
      const attempt = async (): Promise<void> => {
        durableCommits.begin(mongoSession);
        createdSession = undefined;
        const previous = await AgentAdmissionReceiptModel.findOne({ scopeKey })
          .session(mongoSession)
          .lean();
        if (previous) {
          result = await this.readReceipt(
            previous.payloadHash,
            previous.result,
            command.payloadHash,
          );
          return;
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
        // The session's counter and ledger as this transaction leaves them: the turn's write needs no read of it.
        let accounted: AccountedConversation = {
          lastSeq: 0,
          ledger: EMPTY_LEDGER,
        };
        const sessionScope = {
          orgId: command.orgId,
          actorId: command.actorId,
          sessionId,
          ...(command.agentKey !== undefined
            ? { agentKey: command.agentKey }
            : {}),
        };
        if (command.sessionId === null) {
          createdSession = { title: durableSessionTitle(command) };
          await this.sessions.createDurableSession(
            {
              ...sessionScope,
              title: createdSession.title,
              model: command.input.model,
              now,
            },
            mongoTx(mongoSession),
          );
        } else {
          binding = await this.sessions.bindDurableSession(
            sessionScope,
            now,
            mongoTx(mongoSession),
            role,
          );
          if (binding === null) throw new HarnessNotFoundError();
          accounted = binding.accounted;
          if (
            command.regenerate !== undefined &&
            !(await this.conversations.hasAnswer(
              { orgId: command.orgId, sessionId },
              command.regenerate.messageId,
              mongoTx(mongoSession),
            ))
          ) {
            throw new HarnessNotFoundError();
          }
          const active = await AgentRunModel.findOne({
            activeSessionKey: activeSessionKey(command.orgId, sessionId),
          })
            .session(mongoSession)
            .lean();
          if (active) {
            const activeRun = runFromDocument(active);
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
            result = waitsAsNextRun(activeRun, command)
              ? await this.enqueueNextRun(
                  { ...command, absoluteDeadlineAt },
                  activeRun,
                  now,
                  mongoSession,
                  hold,
                )
              : await this.steer(
                  command,
                  active as unknown as Record<string, unknown>,
                  now,
                  mongoSession,
                  hold,
                );
            await createInTransaction(
              mongoSession,
              AgentAdmissionReceiptModel,
              [
                {
                  scopeKey,
                  payloadHash: command.payloadHash,
                  result: {
                    ...result,
                    run: await this.inputs.seal(result.run),
                  },
                  createdAt: now,
                },
              ],
            );
            return;
          }
          if (command.steerOnly === true) throw noRunToSteer();
        }
        // A refusal throws and aborts the transaction: nothing is written.
        await this.enforceQuotas(command, hold, now, mongoSession);
        let audience: RunAudience | null = null;
        // Present once a run was admitted here; it also says what the
        // session's stream holds that this run will fold (SS1).
        const sessionCounter =
          command.sessionId === null
            ? null
            : await AgentEventCounterModel.findOne({
                orgId: command.orgId,
                sessionId,
              })
                .session(mongoSession)
                .lean();
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
            mongoTx(mongoSession),
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
          // Capture history inside the admission transaction, before the new
          // user message is appended. The worker therefore receives the same
          // authoritative transcript shape as the legacy stream and never
          // trusts a browser-supplied history array.
          const ref = { orgId: command.orgId, sessionId };
          const history = await this.history.read(
            ref,
            mongoTx(mongoSession),
            accounted,
          );
          previousConversations = withoutSandboxCommands(
            history.messages.flatMap((m) => m.turns),
          );
          audience = await this.audienceOf(
            command,
            role,
            binding as SessionBinding,
            sessionCounter?.actors ?? [],
            mongoSession,
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
            // Whether a run was ever admitted here, not whether the stream has
            // events: a share journals onto a session that never ran (SS1).
            const planned = planSessionImport(history, sessionCounter !== null);
            imported = planned.plan;
            // Saved with the user's turn, in its one write of the session.
            accounted = { lastSeq: history.lastSeq, ledger: planned.ledger };
          }
        }

        if (command.sessionId !== null && sessionCounter?.compactRequest) {
          // The person's /compact waiting on the session (SLASH2 C2): this run takes it, in this transaction.
          input = withCompact(
            input,
            command.harnessMajor,
            sessionCounter.compactRequest,
          );
          if (input.compact !== undefined) {
            await AgentEventCounterModel.updateOne(
              { orgId: command.orgId, sessionId },
              { $unset: { compactRequest: 1 } },
              { session: mongoSession },
            );
          }
        }

        const userMessageKey = await this.conversations.appendUserTurn(
          { orgId: command.orgId, sessionId },
          {
            ...turn,
            ...(role === 'collaborator' ? { senderId: command.actorId } : {}),
          },
          mongoTx(mongoSession),
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
          mongoSession,
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
          assertPinCurrent(
            command,
            await HEADS.heads(command.orgId, mongoSession),
          );
        }
        const sealed = await this.inputs.seal(run);
        await createInTransaction(mongoSession, AgentRunModel, [
          {
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
            ...(command.answered !== undefined
              ? { answered: command.answered }
              : {}),
            modelAccess: command.modelAccess ?? null,
            via: command.via,
            ...(command.surface !== undefined
              ? { surface: command.surface }
              : {}),
            ...(audience !== null ? { audience } : {}),
            ...(command.trigger !== undefined
              ? { trigger: command.trigger }
              : {}),
            ...(memory !== null ? { memory } : {}),
            ...(history !== null
              ? { input: { ...sealed.input, historyRef: history } }
              : {}),
          },
        ]);
        let importSummary: SessionImportSummary | undefined;
        if (run.harnessMajor === 2) {
          let importEvent: JournalEventInput[] | undefined;
          if (imported !== null) {
            const stored = await this.importContent.store(
              run,
              imported,
              mongoSession,
            );
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
            mongoTx(mongoSession),
          );
        }

        const counter = await AgentEventCounterModel.findOneAndUpdate(
          { orgId: command.orgId, sessionId },
          {
            $inc: { nextEventSeq: 1 },
            $addToSet: {
              actors: command.actorId,
              // Every later run of the session folds this run's sessionRole (SS1 rule), and what its gates journal.
              ...(sessionJournalFeatures(run, command).length > 0
                ? {
                    journalFeatures: {
                      $each: sessionJournalFeatures(run, command),
                    },
                  }
                : {}),
            },
          },
          {
            upsert: true,
            returnDocument: 'after',
            setDefaultsOnInsert: true,
            session: mongoSession,
          },
        ).lean();
        const eventSeq = counter.nextEventSeq;
        await createInTransaction(mongoSession, AgentEventModel, [
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
        // An inline run is claimed by the process that admitted it; the
        // sweeper dispatches it only if that claim does not come.
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
            mongoTx(mongoSession),
          );
        }

        const { activeSessionKey: _active, ...publicRun } = run;
        result = {
          sessionId,
          userMessageKey,
          run: structuredClone(publicRun),
          firstEventSeq: eventSeq,
          lastEventSeq: eventSeq,
          created: true,
          ...(importSummary !== undefined ? { imported: importSummary } : {}),
        };
        await createInTransaction(mongoSession, AgentAdmissionReceiptModel, [
          {
            scopeKey,
            payloadHash: command.payloadHash,
            // The run's own sealed input: an inline history is sealed once per admission (GAPS2-1).
            result: { ...result, run: { ...result.run, input: sealed.input } },
            createdAt: now,
          },
        ]);
      };
      for (;;) {
        try {
          await mongoSession.withTransaction(async () => {
            // Before the transaction's first read fixes its snapshot: whoever
            // reserves after this admission's hold ended then reads its run.
            await hold.reserve(now);
            // The admission's inserts go in one command at its end (PERF-E2): nothing in it reads them back.
            await transactionWrites.begin(mongoSession);
            await attempt();
            await transactionWrites.flush(mongoSession);
            hold.committing();
          }, DURABLE_TRANSACTION_OPTIONS);
          break;
        } catch (error) {
          if (!(error instanceof TwinInFlight)) throw error;
          await hold.afterTwin();
        }
      }
      hold.committed();
      if (!result)
        throw new Error('Admission transaction completed without a result');
      await durableCommits.committed(mongoSession);
      return structuredClone({
        ...result,
        run: await this.inputs.open(result.run),
        ...(createdSession !== undefined ? { createdSession } : {}),
      });
    } catch (error) {
      if (!isDuplicateKeyError(error)) throw error;
      const previous = await AgentAdmissionReceiptModel.findOne({
        scopeKey,
      }).lean();
      if (previous) {
        return await this.readReceipt(
          previous.payloadHash,
          previous.result,
          command.payloadHash,
        );
      }
      if (
        await AgentRunModel.exists({
          activeSessionKey: activeSessionKey(command.orgId, sessionId),
        })
      ) {
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
      durableCommits.discard(mongoSession);
      await mongoSession.endSession();
    }
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

  /**
   * Whose turns the run may see (SS2), when it is in a shared context: its
   * actor's earlier root runs of the session, read in the admission's
   * transaction (one indexed read, paid only in a shared context).
   */
  private async audienceOf(
    command: AdmitRunCommand,
    role: TurnRole,
    binding: SessionBinding,
    actors: readonly string[],
    sessionHandle: Tx | ClientSession,
  ): Promise<RunAudience | null> {
    const session = asMongoSession(sessionHandle);
    const context = {
      ownerId: binding.ownerId,
      shared: binding.shared,
      actors,
    };
    if (runAudience(command.actorId, role, context, []) === null) return null;
    const own = await AgentRunModel.find({
      orgId: command.orgId,
      sessionId: command.sessionId,
      actorId: command.actorId,
      parent: null,
    })
      .sort({ createdAt: -1 })
      .limit(MAX_OWN_RUNS)
      .select('runId')
      .session(session)
      .lean();
    return runAudience(
      command.actorId,
      role,
      context,
      own.map((run) => run.runId).reverse(),
      command.visibleRuns ?? [],
    );
  }

  /**
   * A message to someone else's running task waits for it to end (SS2): the
   * admission as decided now is kept, and the active run's document is
   * written under its status, so this and the run's terminal commit cannot
   * miss each other. Only the rate windows count it.
   */
  private async enqueueNextRun(
    command: AdmitRunCommand,
    active: HarnessRun,
    now: number,
    sessionHandle: Tx | ClientSession,
    hold: AdmissionQuotaHold,
  ): Promise<RunAdmissionResult> {
    const session = asMongoSession(sessionHandle);
    await this.enforceQuotas(command, hold, now, session, false);
    const touched = await AgentRunModel.findOneAndUpdate(
      { orgId: active.orgId, runId: active.runId, status: active.status },
      { $inc: { nextRunsQueued: 1 } },
      { returnDocument: 'after', session },
    ).lean();
    if (!touched) {
      throw new HarnessConflictError(
        'RUN_ACTIVE',
        'The session already has an active root run',
      );
    }
    const ahead = await AgentNextRunModel.countDocuments({
      orgId: active.orgId,
      sessionId: active.sessionId,
      status: 'pending',
    }).session(session);
    const id = new Types.ObjectId().toString();
    await AgentNextRunModel.create(
      [
        {
          _id: id,
          orgId: active.orgId,
          sessionId: active.sessionId,
          actorId: command.actorId,
          runId: command.runId,
          behindRunId: active.runId,
          status: 'pending',
          command: structuredClone(command) as unknown as Record<
            string,
            unknown
          >,
          createdAt: now,
        },
      ],
      { session },
    );
    return {
      sessionId: active.sessionId,
      userMessageKey: '',
      run: withoutInput(runFromDocument(touched)),
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
    const document = await AgentNextRunModel.findOne({
      orgId,
      sessionId,
      status: 'pending',
    })
      .sort({ createdAt: 1, _id: 1 })
      .lean();
    if (
      !document ||
      document.command === null ||
      document.command === undefined
    ) {
      return null;
    }
    return {
      id: document._id,
      orgId: document.orgId,
      sessionId: document.sessionId,
      actorId: document.actorId,
      runId: document.runId,
      behindRunId: document.behindRunId,
      command: structuredClone(document.command) as unknown as AdmitRunCommand,
      createdAt: document.createdAt,
    };
  }

  async drainable(
    limit: number,
  ): Promise<Array<{ orgId: string; sessionId: string }>> {
    const waiting = await AgentNextRunModel.aggregate<{
      _id: { orgId: string; sessionId: string };
    }>([
      { $match: { status: 'pending' } },
      {
        $group: {
          _id: { orgId: '$orgId', sessionId: '$sessionId' },
          first: { $min: '$createdAt' },
        },
      },
      { $sort: { first: 1 } },
      { $limit: Math.max(limit, 1) },
    ]);
    const idle: Array<{ orgId: string; sessionId: string }> = [];
    for (const { _id } of waiting) {
      const busy = await AgentRunModel.exists({
        activeSessionKey: activeSessionKey(_id.orgId, _id.sessionId),
      });
      if (!busy) idle.push({ orgId: _id.orgId, sessionId: _id.sessionId });
    }
    return idle;
  }

  async settleNextRun(
    orgId: string,
    id: string,
    outcome: NextRunOutcome,
    now: number,
  ): Promise<void> {
    await AgentNextRunModel.updateOne(
      { _id: id, orgId, status: 'pending' },
      {
        $set: {
          status: outcome.status,
          settledAt: now,
          ...(outcome.status === 'admitted'
            ? { runId: outcome.runId }
            : { reason: outcome.reason }),
        },
        // Admitted, the message lives on in the conversation; refused, it is dropped.
        $unset: { command: 1 },
      },
    );
  }

  async dropNextRuns(orgId: string, sessionId: string): Promise<number> {
    const { deletedCount } = await AgentNextRunModel.deleteMany({
      orgId,
      sessionId,
    });
    // The person's waiting /compact holds their focus text: erased with the session, though its counter is kept.
    await AgentEventCounterModel.updateOne(
      { orgId, sessionId },
      { $unset: { compactRequest: 1 } },
    );
    return deletedCount;
  }

  /**
   * The message joins the session's active run (steering, T2): its seq is
   * allocated on the run, and `input.queued` carries the text. Only the
   * rate windows count it; it adds no run.
   */
  private async steer(
    command: AdmitRunCommand,
    active: Record<string, unknown>,
    now: number,
    sessionHandle: Tx | ClientSession,
    hold: AdmissionQuotaHold,
  ): Promise<RunAdmissionResult> {
    const session = asMongoSession(sessionHandle);
    const run = runFromDocument(active);
    assertSteeredByActor(run, command.actorId);
    const refused = steeringRefusal(run, command.input);
    if (refused !== null) throw steeringConflict(refused);
    assertReferencesChecked(command.input, command.referencesAuthorized);
    await this.enforceQuotas(command, hold, now, session, false);
    const updated = await AgentRunModel.findOneAndUpdate(
      { orgId: run.orgId, runId: run.runId, status: run.status },
      [
        { $set: { inputSeq: { $add: [{ $ifNull: ['$inputSeq', 0] }, 1] } } },
        {
          $set: {
            pendingInputs: {
              $concatArrays: [
                { $ifNull: ['$pendingInputs', []] },
                ['$inputSeq'],
              ],
            },
            // From now on only a worker that folds queued input may take it (RU-2).
            requires: withFeatureExpr(...steeringFeatures(command.input)),
          },
        },
      ],
      { updatePipeline: true, returnDocument: 'after', session },
    ).lean();
    if (!updated) throw steeringConflict('cancelling');
    const queued = runFromDocument(updated);
    const messageSeq = queued.inputSeq ?? 1;
    await this.journal.append(
      await this.journals.prepare(
        nodeAppend(queued, [
          inputQueuedEvent(messageSeq, command.input, command.actorId),
        ]),
        session,
      ),
      mongoTx(session),
    );
    await AgentEventCounterModel.updateOne(
      { orgId: queued.orgId, sessionId: queued.sessionId },
      {
        $addToSet: {
          journalFeatures: { $each: steeringFeatures(command.input) },
        },
      },
      { upsert: true, session },
    );
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
   * The actor's and the org's places were reserved before the transaction (`hold`), where admissions of one person
   * or of the whole org do not abort each other (PERFB-F1, DOS-MONGO-1): the active runs of each are the ones this
   * snapshot holds and the ones of admissions that reserved earlier and are not in it yet, so two admissions cannot
   * both take the last place. Nothing here writes, so a refusal aborts nobody else.
   * `countsActive` false: the rate windows alone (a steering message).
   */
  private async enforceQuotas(
    command: AdmitRunCommand,
    hold: AdmissionQuotaHold,
    now: number,
    sessionHandle: Tx | ClientSession,
    countsActive = true,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    hold.assertNoTwin();
    const quotas = cappedQuotas(this.quotas, command.quotaCaps);
    if (!countsActive) {
      assertHeldQuotas(await hold.steeringPlace(now), null, now, quotas);
      hold.count();
      return;
    }
    const held = hold.reserved();
    if (held.org === null) throw actorRefusal(held.user, now, quotas);
    const active = { $in: [...QUOTA_ACTIVE_STATUSES] };
    // The org's and the actor's active runs in one read of the same index (PERF-E: a store command each was
    // a third of a millisecond of the one event loop). Sequential: operations of one transaction must not overlap.
    const [counts] = await AgentRunModel.aggregate<{
      org: number;
      user: number;
    }>([
      { $match: { orgId: command.orgId, status: active } },
      {
        $group: {
          _id: null,
          org: { $sum: 1 },
          user: {
            $sum: { $cond: [{ $eq: ['$actorId', command.actorId] }, 1, 0] },
          },
        },
      },
    ]).session(session);
    const ahead = runsAhead(held);
    const seen =
      ahead.length === 0
        ? []
        : await AgentRunModel.find({
            orgId: command.orgId,
            runId: { $in: ahead },
          })
            .select('runId')
            .session(session)
            .lean();
    assertHeldQuotas(
      held,
      {
        orgActive: counts?.org ?? 0,
        userActive: counts?.user ?? 0,
        seen: new Set(seen.map((run) => run.runId)),
      },
      now,
      quotas,
    );
    hold.count();
  }

  async requestCompact(
    orgId: string,
    sessionId: string,
    request: CompactRequest,
  ): Promise<boolean> {
    // The counter exists once a run was admitted in the session: before that there is nothing to compact.
    const saved = await AgentEventCounterModel.updateOne(
      { orgId, sessionId },
      { $set: { compactRequest: { ...request } } },
    );
    return saved.matchedCount > 0;
  }

  async answeredRun(
    orgId: string,
    sessionId: string,
    runId?: string,
  ): Promise<AnsweredRun | null> {
    const filter = {
      orgId,
      sessionId,
      parent: null,
      ...(runId !== undefined ? { runId } : {}),
    };
    const stored = await AgentRunModel.findOne(filter)
      .sort({ createdAt: -1, _id: -1 })
      .select('runId status outcome gates trigger answered')
      .read('primary')
      .lean();
    return stored === null
      ? null
      : answeredRunFrom(stored, runId !== undefined);
  }

  async labelledTurns(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<LabelledTurn[]> {
    const runs = await AgentRunModel.find({
      orgId,
      sessionId,
      parent: null,
      actorId: idOperator({ $ne: actorId }),
      'label.complete': true,
    })
      .sort({ createdAt: -1, _id: -1 })
      .limit(MAX_VISIBLE_RUNS)
      .select('runId label')
      .lean();
    return runs.map((run) => ({
      id: run.runId,
      label: parseTurnLabel((run as { label?: unknown }).label),
    }));
  }

  async findAdmission(
    command: AdmissionIdentity & Pick<AdmitRunCommand, 'payloadHash'>,
  ): Promise<RunAdmissionResult | null> {
    const previous = await AgentAdmissionReceiptModel.findOne({
      scopeKey: admissionScopeKey(command),
    }).lean();
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

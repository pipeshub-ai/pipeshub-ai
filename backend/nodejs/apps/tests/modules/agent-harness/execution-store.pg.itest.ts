/**
 * The ExecutionStore + RunAdmissionStore contract and the child-run contract on PostgreSQL (Alembic head, row-level
 * security, PgBouncer in transaction mode), on the same terms the Mongo suite runs them on a replica set: journal
 * sealed and audited, history by reference, the conversation written through main's conversation repository. Then
 * the invariants this store owns, proved under real concurrency: one claimer wins a run, a stale attempt (older fence,
 * lapsed lease) can neither commit, renew nor release, identical commits and admissions race to one answer, one
 * active run per session, an aborted unit leaves nothing, a join suspension racing the last child never strands the
 * parent, and another organisation sees nothing.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/agent-harness/execution-store.pg.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import {
  getConversationRepository,
  setConversationRepository,
} from '../../../src/libs/repositories/conversation';
import { PgHarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/pg';
import { PgHarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/pg';
import { PgHarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/pg';
import { PgExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/pg';
import { PgRunAdmissionStore } from '../../../src/libs/repositories/agent-harness/run-admission-store/pg';
import { PgChildRunStore } from '../../../src/libs/repositories/agent-harness/child-runs/pg';
import { PgJournalStore } from '../../../src/libs/repositories/agent-harness/journal/pg';
import { PgPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import { PgDispatchOutbox } from '../../../src/libs/repositories/agent-harness/dispatch-outbox/pg';
import { PgBudgetLedger } from '../../../src/libs/repositories/agent-harness/budget-ledger/pg';
import {
  PgAuditChainStore,
  pgAuditLinksIdle,
} from '../../../src/libs/repositories/agent-harness/audit/pg';
import { PgHeadRevisions } from '../../../src/libs/repositories/control-plane/head-revisions/pg';
import { PgUnitOfWork, kernelTx } from '../../../src/libs/repositories/pg';
import { fixedPgTime } from '../../../src/libs/repositories/agent-harness/time/pg';
import { RunHistoryContent } from '../../../src/modules/agent-harness/application/run-history-content';
import { RunInputSeal } from '../../../src/modules/agent-harness/application/run-input-seal';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { AdmissionQuotas } from '../../../src/modules/agent-harness/domain/admission-quotas';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import {
  HarnessRun,
  StoreClock,
} from '../../../src/modules/agent-harness/domain/execution';
import {
  conflictCode,
  ContractBackend,
  contractClock,
  ContractHarness,
  createContractHarness,
  describeExecutionStoreContract,
  START,
  TranscriptProbe,
} from './support/execution-store-contract';
import {
  CHILD_RESULT,
  describeChildRunContract,
} from './support/child-run-contract';
import { describeJournalTailContract } from './support/journal-tail-contract';
import { describeControlPlaneLivenessContract } from './support/control-plane-liveness-contract';
import { describeWorkerFeatureSightingsContract } from './support/worker-feature-sightings-contract';
import { describeCollaboratorTurnsContract } from './support/collaborator-turns-contract';
import { PgHarnessSessionRoles } from '../../../src/libs/repositories/agent-harness/session-roles/pg';
import { newObjectId } from '../../../src/libs/repositories/ids';
import { describePendingReapsContract } from './support/pending-reaps-contract';
import { position, turnStarted } from './support/journal-fixtures';
import { PgJournalTail } from '../../../src/libs/repositories/agent-harness/journal-tail/pg';
import { PgControlPlaneLiveness } from '../../../src/libs/repositories/agent-harness/control-plane-liveness/pg';
import { PgPendingReaps } from '../../../src/libs/repositories/agent-harness/pending-reaps/pg';
import { PgRunState } from '../../../src/libs/repositories/agent-harness/run-state/pg';
import { PgRunStatusReader } from '../../../src/libs/repositories/agent-harness/run-status/pg';
import { PgWorkerFeatureSightings } from '../../../src/libs/repositories/agent-harness/worker-feature-sightings/pg';
import { pgActiveOrgIds } from '../../../src/libs/repositories/agent-harness/active-orgs/pg';
import { HARNESS_ENABLED } from './support/rollout';
import { agentDefinition } from './support/agent-definition';
import { testCipher, testRunKeys } from './support/data-protection';
import {
  PgStack,
  boundStore,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

/** The methods whose first argument is the org they are for (the contract calls them with no org bound). */
const EXECUTION_ORG_ARGS = { activeRunsOfActor: 0, completedRootChildren: 0, failRun: 0, findReceipt: 0, get: 0, getActive: 0, markRunWaiting: 0, pendingApprovalRuns: 0, readClaimSubject: 0, readEnding: 0, readEvents: 0, relaxRequirement: 0, sessionRunParents: 0, storeFollowups: 0, unansweredQuestionRuns: 0 };
const ADMISSION_ORG_ARGS = { answeredRun: 0, dropNextRuns: 0, labelledTurns: 0, nextPending: 0, requestCompact: 0, settleNextRun: 0 };
const CHILD_ORG_ARGS = { heads: 0, openChildren: 0 };

/** The real conversation writer, able to fail its next transcript write after a partial write (L-11). */
class ProbedConversationWriter extends PgHarnessConversationWriter {
  failNext = false;

  override async saveRunTranscript(
    ...args: Parameters<PgHarnessConversationWriter['saveRunTranscript']>
  ): Promise<boolean> {
    if (this.failNext) {
      this.failNext = false;
      await super.saveRunTranscript(...args);
      throw new Error('conversation write failed');
    }
    return super.saveRunTranscript(...args);
  }
}

for (const bin of pgBins()) {
  pgSuite(
    `agent harness execution on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_execution');
        setConversationRepository(new PgConversationRepository());
      });
      after(async () => {
        await pgAuditLinksIdle();
        setConversationRepository();
        await stack?.stop();
      });

      const probe = (writer: ProbedConversationWriter): TranscriptProbe => ({
        failNextWrite: () => {
          writer.failNext = true;
        },
        deleteConversation: async (run) => {
          await stack.admin((c) =>
            c.query(
              `UPDATE conversation.chat_sessions SET is_deleted = true WHERE org_id = $1 AND id = $2`,
              [run.orgId, run.sessionId],
            ),
          );
        },
        written: async (run) =>
          (
            await inOrg(run.orgId, () =>
              getConversationRepository().listMessages(run.sessionId, {
                orgId: run.orgId,
              }),
            )
          )
            .map((row) => row.dto as unknown as Record<string, unknown>)
            .filter((message) => message['messageType'] !== 'user_query'),
      });

      /** A head row moved as a committed control-plane write moves it. */
      const moveOrgHead = (orgId: string): Promise<unknown> =>
        stack.admin((c) =>
          c.query(
            `INSERT INTO harness.cp_org_heads (org_id, revision, updated_at, region) VALUES ($1, 1, now(), 'test')
           ON CONFLICT (org_id) DO UPDATE SET revision = harness.cp_org_heads.revision + 1, updated_at = now()`,
            [orgId],
          ),
        );
      const moveDeploymentHead = (): Promise<unknown> =>
        stack.admin((c) =>
          c.query(
            `INSERT INTO harness.cp_deployment_head (id, revision, updated_at) VALUES ('deployment', 1, now())
           ON CONFLICT (id) DO UPDATE SET revision = harness.cp_deployment_head.revision + 1, updated_at = now()`,
          ),
        );

      const pgBackend = (
        clock?: StoreClock,
        options: { quotas?: AdmissionQuotas } = {},
      ): ContractBackend & {
        writer: ProbedConversationWriter;
        execution: PgExecutionStore;
      } => {
        const time = clock ? fixedPgTime(() => clock.now()) : undefined;
        const payloads = scopedStore(new PgPayloadStore());
        const history = new RunHistoryContent(testCipher(), payloads, payloads);
        const inputs = new RunInputSeal(testCipher());
        const journal = scopedStore(
          new AuditedJournalStore(
            new SealedJournalStore(new PgJournalStore(time), testCipher()),
            new PgAuditChainStore(),
          ),
        );
        const writer = new ProbedConversationWriter();
        const dispatches = new PgDispatchOutbox();
        const budgets = scopedStore(new PgBudgetLedger(time));
        // The contract calls the stores as a test, with no org bound: each call runs as the org it names (ADR-SEC-02).
        const execution = scopedStore(
          new PgExecutionStore(writer, dispatches, time, journal),
          EXECUTION_ORG_ARGS,
        );
        return {
          writer,
          execution,
          transcripts: probe(writer),
          executionStore: execution,
          journal,
          payloads,
          history,
          inputs,
          deletePayload: async (orgId, ref) => {
            await stack.admin((c) =>
              c.query(
                `DELETE FROM harness.journal_payloads WHERE org_id = $1 AND id = $2`,
                [orgId, ref.replace(/^pg:/, '')],
              ),
            );
          },
          admissionStore: scopedStore(new PgRunAdmissionStore({
            sessions: new PgHarnessChatSessionRepository(),
            history: new PgHarnessHistorySource(),
            conversations: writer,
            dispatches,
            ...(time !== undefined ? { time } : {}),
            journal,
            ...(options.quotas !== undefined ? { quotas: options.quotas } : {}),
            journals: new JournalService(payloads, testCipher()),
            historyContent: history,
            inputs,
            runKeys: testRunKeys(),
          }), ADMISSION_ORG_ARGS),
          dispatches: async (orgId, runId) =>
            (
              await stack.admin((c) =>
                c.query<{ value: string }>(
                  `SELECT value FROM platform.outbox WHERE org_id = $1 AND ordering_key = $2 ORDER BY created_at, id`,
                  [orgId, runId],
                ),
              )
            ).rows
              .map(
                (row) =>
                  (
                    JSON.parse(row.value) as {
                      payload: Record<string, unknown>;
                    }
                  ).payload,
              )
              .map((payload) => ({
                commandId: payload['commandId'] as string,
                kind: payload['kind'] as string,
              })),
          childRuns: scopedStore(new PgChildRunStore({
            dispatches,
            ...(time !== undefined ? { time } : {}),
            journal,
            budgets,
            runKeys: testRunKeys(),
            inputs,
          }), CHILD_ORG_ARGS),
          budgets,
          reset: async () => {
            await pgAuditLinksIdle();
            await resetHarnessPg(stack);
          },
          controlPlane: {
            heads: (orgId) =>
              inOrg(orgId, () => new PgHeadRevisions().heads(orgId)),
            commit: async (orgId) => {
              await moveOrgHead(orgId);
            },
            moveDeployment: async () => {
              await moveDeploymentHead();
            },
          },
        };
      };

      describeExecutionStoreContract('postgres', (clock, options) =>
        pgBackend(clock, options),
      );

      describeChildRunContract('postgres', (clock) => pgBackend(clock));

      describeCollaboratorTurnsContract('postgres', (clock) => {
        const base = pgBackend(clock);
        return {
          ...base,
          // NextRunQueue and NextRunEraser are served by the production admission store.
          admissionStore: base.admissionStore as PgRunAdmissionStore,
          roles: boundStore(new PgHarnessSessionRoles()),
          // The share route's write, on main's rows: the participant row and the conversation's shared flag.
          share: async (orgId, sessionId, userId, accessLevel) => {
            await stack.admin(async (c) => {
              await c.query(
                `INSERT INTO conversation.chat_session_members (org_id, session_id, user_id, access_level, pos, entry_id)
                SELECT $1::text, $2::conversation.oid24, $3::conversation.oid24, $4::text, count(*)::int,
                       $5::conversation.oid24
                  FROM conversation.chat_session_members WHERE org_id = $1::text AND session_id = $2::conversation.oid24`,
                [orgId, sessionId, userId, accessLevel, newObjectId()],
              );
              await c.query(
                `UPDATE conversation.chat_sessions SET is_shared = true WHERE org_id = $1 AND id = $2`,
                [orgId, sessionId],
              );
            });
          },
          unshare: async (orgId, sessionId, userId) => {
            await stack.admin((c) =>
              c.query(
                `DELETE FROM conversation.chat_session_members WHERE org_id = $1 AND session_id = $2 AND user_id = $3`,
                [orgId, sessionId, userId],
              ),
            );
          },
          messages: async (sessionId) =>
            (
              await stack.admin((c) =>
                c.query<{ message_type: string; sender_id: string | null }>(
                  `SELECT message_type, sender_id FROM conversation.chat_messages WHERE session_id = $1 ORDER BY seq`,
                  [sessionId],
                ),
              )
            ).rows.map((row) => ({
              messageType: row.message_type,
              ...(row.sender_id !== null ? { senderId: row.sender_id } : {}),
            })),
        };
      });

      describe('PostgreSQL execution invariants', () => {
        let h: ContractHarness;
        let backend: ReturnType<typeof pgBackend>;

        beforeEach(async () => {
          const clock = contractClock();
          backend = pgBackend(clock);
          await backend.reset?.();
          h = createContractHarness(backend, clock);
        });

        const runRow = async (
          orgId: string,
          runId: string,
        ): Promise<Record<string, unknown> | undefined> =>
          (
            await stack.admin((c) =>
              c.query<{ doc: Record<string, unknown> }>(
                `SELECT doc FROM harness.runs WHERE org_id = $1 AND run_id = $2`,
                [orgId, runId],
              ),
            )
          ).rows[0]?.doc;

        const count = async (
          sql: string,
          values: unknown[] = [],
        ): Promise<number> =>
          Number(
            (
              await stack.admin((c) =>
                c.query<{ n: string }>(
                  `SELECT count(*)::text AS n FROM ${sql}`,
                  values,
                ),
              )
            ).rows[0]?.n ?? 0,
          );

        it('of claimers racing for one queued run exactly one wins; the others are refused', async () => {
          const { run } = await h.admit();
          const outcomes = await Promise.allSettled(
            ['w-1', 'w-2', 'w-3', 'w-4', 'w-5'].map((worker) =>
              h.claim(run, worker, 5_000),
            ),
          );
          const won = outcomes.filter(
            (o): o is PromiseFulfilledResult<HarnessRun> =>
              o.status === 'fulfilled',
          );
          expect(won).to.have.length(1);
          for (const lost of outcomes.filter((o) => o.status === 'rejected')) {
            expect((lost as PromiseRejectedResult).reason).to.be.instanceOf(
              HarnessConflictError,
            );
            expect(
              ((lost as PromiseRejectedResult).reason as HarnessConflictError)
                .code,
            ).to.equal('RUN_NOT_CLAIMABLE');
          }
          const stored = await h.service.get(run.orgId, run.runId);
          expect(stored?.fence).to.equal(won[0]!.value.fence);
          expect(stored?.workerId).to.equal(won[0]!.value.workerId);
        });

        it('a stale attempt (older fence, lapsed lease on database time) can neither commit, renew nor release', async () => {
          const { run } = await h.admit();
          const first = await h.claim(run, 'worker-a', 1_000);
          h.setNow(first.leaseExpiresAt as number);
          const second = await h.claim(first, 'worker-b', 5_000);
          expect(second.fence).to.equal(first.fence + 1);
          const stale = {
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: first.attemptId as string,
            fence: first.fence,
          };
          expect(
            await conflictCode(
              h.service.renew({ ...stale, leaseDurationMs: 1_000 }),
            ),
          ).to.equal('STALE_ATTEMPT');
          expect(
            await conflictCode(
              h.service.release({ ...stale, reason: 'drain' }),
            ),
          ).to.equal('STALE_ATTEMPT');
          expect(
            await conflictCode(
              h.service.commit({
                ...stale,
                commandId: 'stale-commit',
                expectedRevision: first.revision,
                status: 'completed',
                outcome: 'answer',
                outbox: [],
                events: [{ type: 'result', data: {} }],
              }),
            ),
          ).to.equal('STALE_ATTEMPT');
          const stored = await h.service.get(run.orgId, run.runId);
          expect(stored).to.include({
            status: 'running',
            workerId: 'worker-b',
            fence: second.fence,
            revision: second.revision,
          });
          expect(
            await count(`harness.command_receipts WHERE org_id = $1`, [
              run.orgId,
            ]),
          ).to.equal(0);

          // The holder's own lease lapsing is enough: on the database clock, never the caller's.
          h.setNow((second.leaseExpiresAt as number) + 1);
          const owner = {
            ...stale,
            workerId: 'worker-b',
            attemptId: second.attemptId as string,
            fence: second.fence,
          };
          expect(
            await conflictCode(
              h.service.renew({ ...owner, leaseDurationMs: 1_000 }),
            ),
          ).to.equal('STALE_ATTEMPT');
        });

        it('identical commits racing answer one receipt: the loser replays the winner', async () => {
          const { run } = await h.admit();
          const claimed = await h.claim(run, 'worker-a', 60_000);
          const command = {
            commandId: 'commit-once',
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: claimed.attemptId as string,
            fence: claimed.fence,
            expectedRevision: claimed.revision,
            status: 'completed' as const,
            outcome: 'answer' as const,
            outbox: [],
            events: [{ type: 'result', data: { n: 1 } }],
          };
          const receipts = await Promise.all(
            [1, 2, 3, 4].map(() => h.service.commit(command)),
          );
          const first = receipts.find((receipt) => receipt.replayed !== true);
          expect(
            receipts.filter((receipt) => receipt.replayed !== true),
          ).to.have.length(1);
          for (const receipt of receipts.filter((r) => r.replayed === true)) {
            expect(receipt).to.deep.equal({ ...first, replayed: true });
          }
          expect(
            await count(`harness.command_receipts WHERE org_id = $1`, [
              run.orgId,
            ]),
          ).to.equal(1);
          expect((await h.service.get(run.orgId, run.runId))?.status).to.equal(
            'completed',
          );
        });

        it('identical admissions racing answer one run; another payload under the key is refused', async () => {
          const submission = { idempotencyKey: 'same-key' };
          const results = await Promise.all(
            [1, 2, 3].map(() => h.admit(submission)),
          );
          expect(new Set(results.map((r) => r.run.runId)).size).to.equal(1);
          expect(results.filter((r) => r.created)).to.have.length(1);
          expect(
            await count(`harness.runs WHERE org_id = $1`, [h.tenant.orgId]),
          ).to.equal(1);
          expect(
            await conflictCode(
              h.admit({
                ...submission,
                input: {
                  query: 'another',
                  recordIds: [],
                  filters: {},
                  attachments: [],
                  model: { chatMode: 'auto' },
                },
              }),
            ),
          ).to.equal('IDEMPOTENCY_CONFLICT');
        });

        it('one active run per session: the partial unique index refuses a second, whoever writes it', async () => {
          const { run } = await h.admit();
          const doc = await runRow(run.orgId, run.runId);
          let refused: unknown;
          try {
            await stack.admin((c) =>
              c.query(
                `INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, $2, $3::jsonb, 'test')`,
                [
                  run.orgId,
                  'second-active',
                  JSON.stringify({ ...doc, runId: 'second-active' }),
                ],
              ),
            );
          } catch (error) {
            refused = error;
          }
          expect(
            (refused as { constraint?: string } | undefined)?.constraint,
          ).to.equal('runs_one_active_per_session');
          expect(
            (await backend.execution.getActive(run.orgId, run.sessionId))
              ?.runId,
          ).to.equal(run.runId);
        });

        it('concurrent new turns of one idle session admit one run; the rest steer into it or are refused RUN_ACTIVE', async () => {
          const { run } = await h.admit();
          const claimed = await h.claim(run, 'worker-a', 60_000);
          await h.service.commit({
            commandId: 'done',
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: claimed.attemptId as string,
            fence: claimed.fence,
            expectedRevision: claimed.revision,
            status: 'completed',
            outcome: 'answer',
            outbox: [],
            events: [{ type: 'result', data: {} }],
          });
          const outcomes = await Promise.allSettled(
            [1, 2, 3, 4].map((n) =>
              h.admit({
                sessionId: run.sessionId,
                input: {
                  query: `turn ${String(n)}`,
                  recordIds: [],
                  filters: {},
                  attachments: [],
                  model: { chatMode: 'auto' },
                },
              }),
            ),
          );
          for (const outcome of outcomes) {
            if (outcome.status === 'rejected') {
              expect((outcome.reason as HarnessConflictError).code).to.equal(
                'RUN_ACTIVE',
              );
            }
          }
          expect(
            await count(
              `harness.runs WHERE org_id = $1 AND session_id = $2 AND active`,
              [run.orgId, run.sessionId],
            ),
          ).to.equal(1);
          expect(
            await count(`harness.runs WHERE org_id = $1 AND session_id = $2`, [
              run.orgId,
              run.sessionId,
            ]),
          ).to.equal(2);
        });

        it('a commit whose transcript write fails leaves nothing: no transition, receipt, event or dispatch', async () => {
          const { run } = await h.admit();
          const claimed = await h.claim(run, 'worker-a', 60_000);
          const before = {
            events: await count(`harness.session_events WHERE org_id = $1`, [
              run.orgId,
            ]),
            outbox: await count(`platform.outbox WHERE org_id = $1`, [
              run.orgId,
            ]),
          };
          backend.writer.failNext = true;
          let failed: unknown;
          try {
            await backend.execution.commit(
              {
                commandId: 'with-transcript',
                orgId: run.orgId,
                runId: run.runId,
                workerId: 'worker-a',
                attemptId: claimed.attemptId as string,
                fence: claimed.fence,
                events: [],
                legacyEvents: [],
                transition: { status: 'completed', outcome: 'answer' },
                outbox: [
                  { commandId: 'never', runId: run.runId, kind: 'run_ready' },
                ],
              },
              [],
              { kind: 'failure', message: 'x', code: 'x' } as never,
            );
          } catch (error) {
            failed = error;
          }
          expect(failed).to.be.instanceOf(Error);
          expect(await h.service.get(run.orgId, run.runId)).to.deep.equal(
            claimed,
          );
          expect(
            await count(`harness.command_receipts WHERE org_id = $1`, [
              run.orgId,
            ]),
          ).to.equal(0);
          expect(
            await count(`harness.session_events WHERE org_id = $1`, [
              run.orgId,
            ]),
          ).to.equal(before.events);
          expect(
            await count(`platform.outbox WHERE org_id = $1`, [run.orgId]),
          ).to.equal(before.outbox);
        });

        it('the claim queue reads every org as the jobs role; another org reads none of this one', async () => {
          h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
          const a = await h.admit();
          const otherTenant = h.newTenant();
          const b = await h.admit(otherTenant);
          const candidates = await backend.execution.nextClaimable(
            'dispatched',
            0,
            10,
          );
          expect(candidates.map((c) => c.orgId).sort()).to.deep.equal(
            [a.run.orgId, b.run.orgId].sort(),
          );

          const visible = await inOrg(otherTenant.orgId, async () => {
            return new PgUnitOfWork().run(
              async (tx) => {
                const k = kernelTx(tx);
                const runs = await k.query<{ run_id: string }>(
                  `/* pipeshub:harness.test_runs */ SELECT run_id FROM harness.runs`,
                );
                const counters = await k.query(
                  `/* pipeshub:harness.test_counters */ SELECT 1 FROM harness.session_counters`,
                );
                const receipts = await k.query(
                  `/* pipeshub:harness.test_receipts */ SELECT 1 FROM harness.admission_receipts`,
                );
                return {
                  runs: runs.rows.map((r) => r.run_id),
                  counters: counters.rows.length,
                  receipts: receipts.rows.length,
                };
              },
              { readOnly: true },
            );
          });
          expect(visible).to.deep.equal({
            runs: [b.run.runId],
            counters: 1,
            receipts: 1,
          });
          // A call naming one org while another is bound is a wiring fault, never the other org's data.
          let refused: unknown;
          try {
            await inOrg(otherTenant.orgId, () =>
              backend.execution.get(a.run.orgId, a.run.runId),
            );
          } catch (error) {
            refused = error;
          }
          expect(refused).to.be.instanceOf(Error);
          expect(await backend.execution.get(hexOrg(), a.run.runId)).to.equal(
            null,
          );
        });

        it('CJ-8: a join suspension racing the last child ending never strands the parent, over many races', async function () {
          this.timeout(180_000);
          for (let round = 0; round < 12; round += 1) {
            const clock = contractClock();
            const b = pgBackend(clock);
            const r = createContractHarness(b, clock);
            r.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
            r.setAgentDefinition((agentKey) =>
              agentDefinition({ agentKey, maxChildren: 1 }),
            );
            const { run } = await r.admit({
              agentKey: 'agent-1',
              authorization: 'Bearer user',
            });
            const parent = await r.claim(run, 'worker-p', 30_000);
            const spawned = await new ChildRunService({
              runs: b.executionStore,
              children: b.childRuns!,
              journals: new JournalService(b.payloads, testCipher()),
            }).spawn(
              {
                orgId: parent.orgId,
                runId: parent.runId,
                workerId: 'worker-p',
                attemptId: parent.attemptId as string,
                fence: parent.fence,
              },
              { invocationId: 't1.0', goal: 'go', grants: {} },
            );
            const child = await r.claim(
              (await r.service.get(parent.orgId, spawned.childRunId))!,
              'worker-c',
              30_000,
            );
            const [join, end] = await Promise.allSettled([
              r.service.commit({
                commandId: `join-${String(round)}`,
                orgId: parent.orgId,
                runId: parent.runId,
                workerId: 'worker-p',
                attemptId: parent.attemptId as string,
                fence: parent.fence,
                events: [],
                legacyEvents: [],
                transition: {
                  status: 'paused',
                  detail: { kind: 'suspend', reason: 'join' },
                },
                outbox: [],
              }),
              r.service.commit({
                commandId: `end-${String(round)}`,
                orgId: child.orgId,
                runId: child.runId,
                workerId: 'worker-c',
                attemptId: child.attemptId as string,
                fence: child.fence,
                events: [],
                legacyEvents: [],
                transition: {
                  status: 'completed',
                  outcome: 'answer',
                  childResult: CHILD_RESULT,
                },
                outbox: [],
              }),
            ]);
            expect(
              end.status,
              String((end as PromiseRejectedResult).reason),
            ).to.equal('fulfilled');
            const after = (await r.service.get(parent.orgId, parent.runId))!;
            expect(after.children?.open).to.equal(0);
            if (join.status === 'fulfilled') {
              expect(after.status, `round ${String(round)}`).to.equal('queued');
            } else {
              expect((join.reason as HarnessConflictError).code).to.equal(
                'JOIN_READY',
              );
              expect(after.status).to.equal('running');
            }
          }
        });
      });

      describeJournalTailContract('postgres poll', async () => {
        await resetHarnessPg(stack);
        const journal = new SealedJournalStore(
          new PgJournalStore(),
          testCipher(),
        );
        const service = new JournalService(
          scopedStore(new PgPayloadStore()),
          testCipher(),
        );
        return {
          tail: new PgJournalTail(undefined, undefined, 20),
          grow: async (orgId, runId) => {
            const prepared = await service.prepare({
              ...position({
                orgId,
                runId,
                sessionId: `ses-${runId}`,
                streamId: `ses-${runId}`,
              }),
              writer: 'worker',
              events: [turnStarted(1)],
            });
            return (
              await transactAs((tx) => journal.append(prepared, tx), orgId)
            ).lastSeq;
          },
        };
      });

      describeControlPlaneLivenessContract('postgres', async (clock) => {
        await resetHarnessPg(stack);
        const time = fixedPgTime(() => clock.now());
        return { replica: () => new PgControlPlaneLiveness(time) };
      });

      describeWorkerFeatureSightingsContract('postgres', async (clock) => {
        await resetHarnessPg(stack);
        const time = fixedPgTime(() => clock.now());
        return { replica: () => new PgWorkerFeatureSightings(time) };
      });

      describePendingReapsContract('postgres', async () => {
        await resetHarnessPg(stack);
        return new PgPendingReaps();
      });

      describe('PostgreSQL run-state readers and worker sightings', () => {
        let h: ContractHarness;
        beforeEach(async () => {
          const clock = contractClock();
          const backend = pgBackend(clock);
          await backend.reset?.();
          h = createContractHarness(backend, clock);
        });

        it('the run state counts live runs of every org on the database clock; the status reader reads one run', async () => {
          const a = await h.admit();
          const b = await h.admit(h.newTenant());
          await h.claim(b.run, 'worker-b', 1_000);
          h.setNow(START + 2_000);
          const snapshot = await new PgRunState(
            fixedPgTime(() => START + 2_000),
          ).snapshot();
          expect(snapshot.byStatus).to.deep.equal({ queued: 1, running: 1 });
          expect(snapshot.activeSessions).to.equal(2);
          expect(snapshot.leaseExpired).to.equal(1);
          expect(snapshot.oldestRunningAgeS).to.equal(2);
          expect(snapshot.oldestApprovalWaitS).to.equal(null);
          const status = scopedStore(new PgRunStatusReader(), { status: 0 });
          expect(await status.status(a.run.orgId, a.run.runId)).to.equal(
            'queued',
          );
          expect(await status.status(a.run.orgId, 'missing')).to.equal(null);
        });

        it('a worker feature sighting is kept on database time and never goes back', async () => {
          let now = 50_000;
          const sightings = new PgWorkerFeatureSightings(
            fixedPgTime(() => now),
          );
          expect((await sightings.sighting('chat')).seenAt).to.equal(null);
          await sightings.saw(['chat', 'executor_engine']);
          now = 40_000;
          await sightings.saw(['chat']);
          expect(await sightings.sighting('chat')).to.deep.equal({
            seenAt: 50_000,
            now: 40_000,
          });
          expect((await sightings.sighting('executor_engine')).seenAt).to.equal(
            50_000,
          );
        });

        it('the active orgs are every org not deleted, read as the jobs role', async () => {
          const live = hexOrg();
          const gone = hexOrg();
          await stack.admin(async (c) => {
            for (const [orgId, deleted] of [
              [live, false],
              [gone, true],
            ] as const) {
              await c.query(
                `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, is_deleted, created_at, updated_at)
               VALUES ($1, $2, $3, 'business', $4, now(), now())`,
                [orgId, `${orgId}.example`, `admin@${orgId}.example`, deleted],
              );
            }
          });
          const orgs = await pgActiveOrgIds();
          expect(orgs).to.include(live);
          expect(orgs).not.to.include(gone);
        });
      });
    },
  );
}

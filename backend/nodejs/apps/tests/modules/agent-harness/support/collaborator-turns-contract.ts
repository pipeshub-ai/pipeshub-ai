/**
 * SS2 behaviour every admission store must share, with two (or three) users
 * in one conversation: collaborator admission as the sender, the pinned
 * audience and `sessionRole`, and `next_run`. Runs against the in-memory store
 * (`collaborator-turns.test.ts`), MongoDB
 * (`collaborator-turns.mongo.itest.ts`, real replica set) and PostgreSQL
 * (`execution-store.pg.itest.ts`, behind PgBouncer).
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessSessionRoleRefusedError,
} from '../../../../src/modules/agent-harness/domain/errors';
import {
  RunAdmissionResult,
  RunAdmissionStore,
} from '../../../../src/modules/agent-harness/domain/admission';
import { HarnessRun, StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import {
  drainCommand,
  NextRunEraser,
  NextRunQueue,
} from '../../../../src/modules/agent-harness/domain/next-runs';
import {
  RunAdmissionService,
  SubmitDurableRun,
} from '../../../../src/modules/agent-harness/application/run-admission-service';
import { NextRunService } from '../../../../src/modules/agent-harness/application/next-run-service';
import { SessionRoleLookup } from '../../../../src/modules/agent-harness/ports/session-roles';
import { principalRefFor } from '../../../../src/modules/agent-harness/domain/run-spec';
import { SESSION_ROLES_FEATURE } from '../../../../src/modules/agent-harness/domain/session-audience';
import {
  TURN_LABELS_FEATURE,
  TurnLabel,
} from '../../../../src/modules/agent-harness/domain/turn-labels';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations';
import { workerEvent } from './journal-fixtures';
import {
  contractClock,
  ContractBackend,
  ContractHarness,
  createContractHarness,
} from './execution-store-contract';
import { fixedRollout, HARNESS_ENABLED, KILL_SWITCH } from './rollout';
import { grantingAccess, agentDefinition } from './agent-definition';
import { testModelAccess } from './model-access';
import { readmissionBy } from './next-runs';

export interface CollaboratorBackend extends ContractBackend {
  admissionStore: RunAdmissionStore & NextRunQueue & NextRunEraser;
  roles: SessionRoleLookup;
  /** Plays the share route: `write` makes a collaborator, `read` a viewer. */
  share(
    orgId: string,
    sessionId: string,
    userId: string,
    accessLevel: 'read' | 'write',
  ): Promise<void>;
  unshare(orgId: string, sessionId: string, userId: string): Promise<void>;
  /** The conversation's messages with their sender; absent where no conversation is kept. */
  messages?(sessionId: string): Promise<Array<{ messageType: string; senderId?: string }>>;
}

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DEADLINE_MS = 9_000;
const userId = (): string => new Types.ObjectId().toString();

export function describeCollaboratorTurnsContract(
  name: string,
  createBackend: (clock: StoreClock) => CollaboratorBackend,
): void {
  describe(`collaborator turns (SS2): ${name}`, function () {
    this.timeout(30_000);
    let backend: CollaboratorBackend;
    let h: ContractHarness;
    let clock: ReturnType<typeof contractClock>;
    let enabled: boolean;
    let alice: string;
    let bob: string;
    let carol: string;
    let orgId: string;

    let records: InMemoryRecordReadAccess;
    let labelCheck: Pick<SharedCitationService, 'passingTurns'>;

    const admission = (): RunAdmissionService =>
      new RunAdmissionService({
        store: backend.admissionStore,
        rollout: fixedRollout(() => V2),
        clock,
        agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
        modelAccess: testModelAccess(),
        roles: backend.roles,
        collaboratorTurns: { enabled: async () => enabled },
        turnLabels: labelCheck,
      });

    const send = async (
      actorId: string,
      overrides: Partial<SubmitDurableRun> = {},
    ): Promise<RunAdmissionResult> => {
      const outcome = await admission().submit({
        orgId,
        actorId,
        // Each user's own credential: the record authority answers per bearer.
        authorization: `Bearer ${actorId}`,
        idempotencyKey: randomUUID(),
        deadlineMs: DEADLINE_MS,
        input: {
          query: `from ${actorId}`,
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'auto' },
        },
        ...overrides,
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable admission');
      return outcome.admission;
    };

    const refusalOf = async (work: Promise<unknown>): Promise<unknown> => {
      try {
        await work;
      } catch (error) {
        return error;
      }
      return expect.fail('expected a refusal');
    };

    const end = async (run: HarnessRun): Promise<void> => {
      const current = (await h.service.get(run.orgId, run.runId)) as HarnessRun;
      await h.service.control({
        commandId: `end-${run.runId}`,
        dispatchCommandId: `end-${run.runId}:dispatch`,
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: current.revision,
        action: 'cancel',
      });
    };

    const admittedEvent = async (run: HarnessRun) =>
      (await backend.journal.readStream(run.orgId, run.sessionId, 0, 200)).find(
        (event) => event.type === 'run.admitted' && event.runId === run.runId,
      );

    /** Alice's session, shared with Bob to write (and Carol to write when asked). */
    const sharedSession = async (
      withCarol = false,
    ): Promise<RunAdmissionResult> => {
      const first = await send(alice);
      await backend.share(orgId, first.sessionId, bob, 'write');
      if (withCarol) await backend.share(orgId, first.sessionId, carol, 'write');
      return first;
    };

    beforeEach(async () => {
      clock = contractClock();
      backend = createBackend(clock);
      await backend.reset?.();
      h = createContractHarness(backend, clock);
      h.setRollout(V2);
      enabled = true;
      orgId = userId();
      alice = userId();
      bob = userId();
      carol = userId();
      records = new InMemoryRecordReadAccess();
      labelCheck = new SharedCitationService(records);
    });

    describe('admission as the sender (CA)', () => {
      it('CA-2: a collaborator\'s turn runs as the collaborator, with sessionRole collaborator', async () => {
        const first = await sharedSession();
        await end(first.run);
        const mine = await send(bob, { sessionId: first.sessionId });
        expect(mine.created).to.equal(true);
        expect(mine.sessionId).to.equal(first.sessionId);
        expect(mine.run.actorId).to.equal(bob);
        expect(mine.run.sessionRole).to.equal('collaborator');
        expect((await admittedEvent(mine.run))?.data).to.deep.include({
          principalRef: principalRefFor(bob),
          sessionRole: 'collaborator',
        });
        const subject = await backend.executionStore.readClaimSubject(orgId, mine.run.runId);
        expect(subject?.audience).to.deep.equal({
          sessionRole: 'collaborator',
          ownerRef: principalRefFor(alice),
          ownRuns: [],
        });
        const claimed = await h.claim(mine.run);
        expect(claimed.requires?.features).to.include(SESSION_ROLES_FEATURE);
        const pinned = await backend.executionStore.readClaimSubject(orgId, mine.run.runId);
        const spec = pinned?.pinned?.spec;
        expect(spec?.principalRef).to.equal(principalRefFor(bob));
        expect(
          (spec?.extensions?.['x.pipeshub'] as Record<string, unknown>)['audience'],
        ).to.deep.equal({
          sessionRole: 'collaborator',
          ownerRef: principalRefFor(alice),
          ownRuns: [],
        });
        if (backend.messages !== undefined) {
          const senders = (await backend.messages(first.sessionId))
            .filter((message) => message.messageType === 'user_query')
            .map((message) => message.senderId ?? null);
          expect(senders).to.deep.equal([null, bob]);
        }
      });

      it('CA-3: with the org switch off, a collaborator is refused and nothing is written', async () => {
        const first = await sharedSession();
        await end(first.run);
        enabled = false;
        const before = await backend.journal.readStream(orgId, first.sessionId, 0, 200);
        const refused = await refusalOf(send(bob, { sessionId: first.sessionId }));
        expect(refused).to.be.instanceOf(HarnessSessionRoleRefusedError);
        expect((refused as HarnessSessionRoleRefusedError).code).to.equal(
          'COLLABORATOR_TURNS_DISABLED',
        );
        expect(await backend.journal.readStream(orgId, first.sessionId, 0, 200)).to.have.length(
          before.length,
        );
        expect(await h.service.getActive(orgId, first.sessionId)).to.equal(null);
      });

      it('CA-4: a viewer is refused read-only; a stranger and an unshared collaborator are not found', async () => {
        const first = await sharedSession();
        await end(first.run);
        await backend.share(orgId, first.sessionId, carol, 'read');
        const viewer = await refusalOf(send(carol, { sessionId: first.sessionId }));
        expect((viewer as HarnessSessionRoleRefusedError).code).to.equal('VIEWER_READ_ONLY');
        expect(await refusalOf(send(userId(), { sessionId: first.sessionId }))).to.be.instanceOf(
          HarnessNotFoundError,
        );
        await backend.unshare(orgId, first.sessionId, bob);
        expect(await refusalOf(send(bob, { sessionId: first.sessionId }))).to.be.instanceOf(
          HarnessNotFoundError,
        );
      });

      it('CA-5: the store checks the share in its own transaction (a revoked share admits nothing)', async () => {
        const first = await sharedSession();
        await end(first.run);
        await backend.unshare(orgId, first.sessionId, bob);
        const refused = await refusalOf(
          backend.admissionStore.admit(
            {
              orgId,
              actorId: bob,
              sessionId: first.sessionId,
              idempotencyKey: 'revoked',
              payloadHash: 'sha256:revoked',
              runId: randomUUID(),
              dispatchCommandId: 'dispatch:revoked',
              absoluteDeadlineAt: clock.now() + DEADLINE_MS,
              input: {
                query: 'q',
                recordIds: [],
                filters: {},
                attachments: [],
                model: { chatMode: 'auto' },
              },
              harnessMajor: 2,
              via: 'session',
              asCollaborator: true,
            },
            clock.now(),
          ),
        );
        expect(refused).to.be.instanceOf(HarnessNotFoundError);
        expect(await h.service.getActive(orgId, first.sessionId)).to.equal(null);
      });

      it('CA-7: the owner in a shared session gets sessionRole owner and an audience; a personal session neither', async () => {
        const personal = await send(alice);
        expect(personal.run.sessionRole).to.equal(undefined);
        expect(personal.run.requires?.features ?? []).to.not.include(SESSION_ROLES_FEATURE);
        expect((await admittedEvent(personal.run))?.data).to.not.have.property('sessionRole');
        expect(
          (await backend.executionStore.readClaimSubject(orgId, personal.run.runId))?.audience ?? null,
        ).to.equal(null);

        await backend.share(orgId, personal.sessionId, bob, 'write');
        await end(personal.run);
        const owned = await send(alice, { sessionId: personal.sessionId });
        expect(owned.run.sessionRole).to.equal('owner');
        expect((await admittedEvent(owned.run))?.data).to.deep.include({ sessionRole: 'owner' });
        expect(
          (await backend.executionStore.readClaimSubject(orgId, owned.run.runId))?.audience,
        ).to.deep.equal({
          sessionRole: 'owner',
          ownerRef: principalRefFor(alice),
          ownRuns: [personal.run.runId],
        });
      });

      it('CA-8: once a run in a shared context was admitted, every later run of the session requires session_roles', async () => {
        const first = await sharedSession();
        await end(first.run);
        const bobs = await send(bob, { sessionId: first.sessionId });
        await end(bobs.run);
        await backend.unshare(orgId, first.sessionId, bob);
        const later = await send(alice, { sessionId: first.sessionId });
        // Bob ran here: Alice's run still sees his turns as someone else's.
        expect(later.run.sessionRole).to.equal('owner');
        expect(later.run.requires?.features).to.include(SESSION_ROLES_FEATURE);
      });
    });

    describe('next_run (NR)', () => {
      const queuedInputs = async (run: HarnessRun) =>
        (await backend.journal.readStream(run.orgId, run.sessionId, 0, 200)).filter((event) =>
          event.type.startsWith('input.'),
        );

      it('NR-1: a message to someone else\'s running task waits; the running run is untouched', async () => {
        const first = await sharedSession();
        const waiting = await send(bob, { sessionId: first.sessionId });
        expect(waiting.nextRun?.position).to.equal(1);
        expect(waiting.run.runId).to.equal(first.run.runId);
        expect(waiting.run.input).to.equal(undefined);
        const active = (await h.service.getActive(orgId, first.sessionId)) as HarnessRun;
        expect(active.runId).to.equal(first.run.runId);
        expect(active.inputSeq).to.equal(undefined);
        expect(active.pendingInputs ?? []).to.deep.equal([]);
        expect(active.requires).to.deep.equal(first.run.requires);
        expect(await queuedInputs(first.run)).to.deep.equal([]);
        expect(await h.service.get(orgId, waiting.nextRun?.runId as string)).to.equal(null);
      });

      it('NR-2: the run\'s own actor still steers it', async () => {
        const first = await sharedSession();
        const steered = await send(alice, { sessionId: first.sessionId });
        expect(steered.queued).to.deep.equal({ messageSeq: 1 });
        expect(steered.nextRun).to.equal(undefined);
        const [queued] = await queuedInputs(first.run);
        expect(queued?.data!['by']).to.equal(principalRefFor(alice));
      });

      it('NR-3: the owner\'s message to a collaborator\'s running task waits too', async () => {
        const first = await sharedSession();
        await end(first.run);
        const bobs = await send(bob, { sessionId: first.sessionId });
        const waiting = await send(alice, { sessionId: first.sessionId });
        expect(waiting.nextRun?.position).to.equal(1);
        expect(await queuedInputs(bobs.run)).to.deep.equal([]);
      });

      it('NR-4 (F11 acceptance 2): when the run ends, the waiting message runs as its sender, in order', async () => {
        const first = await sharedSession(true);
        const fromBob = await send(bob, { sessionId: first.sessionId });
        const fromCarol = await send(carol, { sessionId: first.sessionId });
        expect([fromBob.nextRun?.position, fromCarol.nextRun?.position]).to.deep.equal([1, 2]);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => enabled },
          clock,
        });
        expect(await drains.drain(orgId, first.sessionId)).to.equal('busy');

        await end(first.run);
        expect(await drains.drain(orgId, first.sessionId)).to.equal('admitted');
        const bobs = (await h.service.getActive(orgId, first.sessionId)) as HarnessRun;
        expect(bobs.runId).to.equal(fromBob.nextRun?.runId);
        expect(bobs.actorId).to.equal(bob);
        expect(bobs.sessionRole).to.equal('collaborator');
        expect((await admittedEvent(bobs))?.data).to.deep.include({
          principalRef: principalRefFor(bob),
          sessionRole: 'collaborator',
        });
        // Carol waits behind Bob now.
        expect(await drains.drain(orgId, first.sessionId)).to.equal('busy');
        await end(bobs);
        expect(await drains.drain(orgId, first.sessionId)).to.equal('admitted');
        const carols = (await h.service.getActive(orgId, first.sessionId)) as HarnessRun;
        expect([carols.runId, carols.actorId]).to.deep.equal([fromCarol.nextRun?.runId, carol]);
        await end(carols);
        expect(await drains.drain(orgId, first.sessionId)).to.equal('empty');
      });

      it('NR-4: the run\'s end drains on its own (the execution service\'s hook)', async () => {
        const first = await sharedSession();
        const waiting = await send(bob, { sessionId: first.sessionId });
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => true },
          clock,
        });
        h.service.onRunEnded((run) => drains.runEnded(run));
        await end(first.run);
        let admitted: HarnessRun | null = null;
        for (let tries = 0; tries < 100 && admitted === null; tries += 1) {
          await new Promise((resolve) => setTimeout(resolve, 20));
          admitted = await h.service.get(orgId, waiting.nextRun?.runId as string);
        }
        expect(admitted?.actorId).to.equal(bob);
      });

      it('NR-5: a retried message replays its place; a second drain admits nothing more', async () => {
        const first = await sharedSession();
        const request = { sessionId: first.sessionId, idempotencyKey: 'bob-1' };
        const waiting = await send(bob, request);
        const replay = await send(bob, request);
        expect(replay.nextRun).to.deep.equal(waiting.nextRun);
        expect(replay.created).to.equal(false);
        await end(first.run);
        const record = await backend.admissionStore.nextPending(orgId, first.sessionId);
        expect(record?.id).to.equal(waiting.nextRun?.id);
        const once = await backend.admissionStore.admit(drainCommand(record!), clock.now());
        const twice = await backend.admissionStore.admit(drainCommand(record!), clock.now());
        expect(once.created).to.equal(true);
        expect([twice.created, twice.run.runId]).to.deep.equal([false, once.run.runId]);
      });

      it('NR-6: a sender who lost the role is refused at the drain; the next in line proceeds', async () => {
        const first = await sharedSession(true);
        await send(bob, { sessionId: first.sessionId });
        const fromCarol = await send(carol, { sessionId: first.sessionId });
        await backend.unshare(orgId, first.sessionId, bob);
        await end(first.run);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => true },
          clock,
        });
        expect(await drains.drain(orgId, first.sessionId)).to.equal('admitted');
        const active = (await h.service.getActive(orgId, first.sessionId)) as HarnessRun;
        expect([active.actorId, active.runId]).to.deep.equal([carol, fromCarol.nextRun?.runId]);
      });

      it('NR-6: with the switch turned off, a waiting collaborator message is refused at the drain', async () => {
        const first = await sharedSession();
        const waiting = await send(bob, { sessionId: first.sessionId });
        await end(first.run);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => false },
          clock,
        });
        expect(await drains.drain(orgId, first.sessionId)).to.equal('empty');
        expect(await h.service.get(orgId, waiting.nextRun?.runId as string)).to.equal(null);
      });

      it('DUR3-F7: with the kill switch on, a waiting message is refused, never turned into a new durable run', async () => {
        const first = await sharedSession();
        const waiting = await send(bob, { sessionId: first.sessionId });
        await end(first.run);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => true },
          clock,
          rollout: fixedRollout(() => KILL_SWITCH),
        });
        expect(await drains.drain(orgId, first.sessionId)).to.equal('empty');
        expect(await h.service.get(orgId, waiting.nextRun?.runId as string)).to.equal(null);
        expect(await h.service.getActive(orgId, first.sessionId)).to.equal(null);
        expect(await backend.admissionStore.nextPending(orgId, first.sessionId)).to.equal(null);
      });

      it('NR-7: steer-only from someone else waits as a next run; with no running task it is RUN_NOT_ACTIVE', async () => {
        const first = await sharedSession();
        const waiting = await send(bob, { sessionId: first.sessionId, steerOnly: true });
        expect(waiting.nextRun?.position).to.equal(1);
        await end(first.run);
        await backend.admissionStore.dropNextRuns(orgId, first.sessionId);
        const refused = await refusalOf(send(bob, { sessionId: first.sessionId, steerOnly: true }));
        expect(refused).to.be.instanceOf(HarnessConflictError);
        expect((refused as HarnessConflictError).code).to.equal('RUN_NOT_ACTIVE');
      });

      it('NR-8: a session whose drain was missed is found by the sweeper\'s look', async () => {
        const first = await sharedSession();
        await send(bob, { sessionId: first.sessionId });
        expect(await backend.admissionStore.drainable(10)).to.deep.equal([]);
        await end(first.run);
        expect(await backend.admissionStore.drainable(10)).to.deep.equal([
          { orgId, sessionId: first.sessionId },
        ]);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => true },
          clock,
        });
        expect(await drains.drainDue(10)).to.equal(1);
        expect(await backend.admissionStore.drainable(10)).to.deep.equal([]);
      });

      it('NR-9: erasure drops a session\'s waiting messages', async () => {
        const first = await sharedSession();
        await send(bob, { sessionId: first.sessionId });
        expect(await backend.admissionStore.dropNextRuns(orgId, first.sessionId)).to.equal(1);
        expect(await backend.admissionStore.nextPending(orgId, first.sessionId)).to.equal(null);
      });
    });

    describe('confidentiality labels (P2.5a)', () => {
      const indexed = (recordId: string) => ({ kind: 'indexed' as const, recordId });
      const labelOf = (...ids: string[]): TurnLabel => ({ complete: true, sources: ids.map(indexed) });

      /** The run answers, its worker pinning `label` as its own work (none: an older worker). */
      const answer = async (admitted: RunAdmissionResult, label: TurnLabel | null): Promise<void> => {
        const run = await h.claim(admitted.run, 'worker-a');
        await h.service.commit({
          commandId: `answer-${run.runId}`,
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: 'attempt-worker-a',
          fence: run.fence,
          events: [
            workerEvent(
              'x.pipeshub.answer.finalized',
              {
                outcome: 'answered',
                answer: 'An answer.',
                partTexts: ['An answer.'],
                citations: [],
                confidence: null,
                ...(label !== null ? { label } : {}),
              },
              { critical: false },
            ),
          ],
          legacyEvents: [],
          transition: { status: 'completed', outcome: 'answer' },
          outbox: [],
        });
      };

      const visibleOf = async (admitted: RunAdmissionResult) =>
        (await backend.executionStore.readClaimSubject(orgId, admitted.run.runId))?.audience?.visibleRuns;

      const storedLabels = (sessionId: string, viewer: string) =>
        backend.admissionStore.labelledTurns!(orgId, sessionId, viewer);

      it('ST-1: a turn stores its own work joined with the run it saw before it', async () => {
        const first = await send(alice);
        await answer(first, labelOf('r1'));
        const second = await send(alice, { sessionId: first.sessionId });
        await answer(second, labelOf('r2'));
        expect(await storedLabels(first.sessionId, bob)).to.deep.equal([
          { id: second.run.runId, label: labelOf('r1', 'r2') },
          { id: first.run.runId, label: labelOf('r1') },
        ]);
      });

      it('ST-2: a turn whose worker pinned no label is unlabelled: it is never offered', async () => {
        const first = await send(alice);
        await answer(first, null);
        expect(await storedLabels(first.sessionId, bob)).to.deep.equal([]);
        expect(await backend.executionStore.historyLabels(first.run)).to.deep.equal([]);
      });

      it('ST-3: an unlabelled earlier turn makes every later one unknown', async () => {
        const first = await send(alice);
        await answer(first, null);
        const second = await send(alice, { sessionId: first.sessionId });
        expect(await backend.executionStore.historyLabels(second.run)).to.deep.equal([{ complete: false, sources: [] }]);
        await answer(second, labelOf('r2'));
        expect(await storedLabels(first.sessionId, bob)).to.deep.equal([]);
      });

      it('ST-6: a label that cannot be joined is unknown, and the run still ends', async () => {
        const first = await send(alice);
        const store = backend.executionStore;
        const original = store.historyLabels.bind(store);
        store.historyLabels = () => Promise.reject(new Error('store down'));
        try {
          await answer(first, labelOf('r1'));
        } finally {
          store.historyLabels = original;
        }
        expect((await h.service.get(orgId, first.run.runId))?.status).to.equal('completed');
        expect(await storedLabels(first.sessionId, bob)).to.deep.equal([]);
      });

      it('S2-1 (two users, readable): Alice\'s turn built only from records Bob can read reaches Bob\'s model', async () => {
        records.grant(`Bearer ${bob}`, 'r1');
        const first = await sharedSession();
        await answer(first, labelOf('r1'));
        const mine = await send(bob, { sessionId: first.sessionId });
        expect(await visibleOf(mine)).to.deep.equal([first.run.runId]);
        expect(records.queries.map((q) => q.authorization)).to.deep.equal([`Bearer ${bob}`]);
        const claimed = await h.claim(mine.run);
        expect(claimed.requires?.features).to.include(TURN_LABELS_FEATURE);
        const pinned = await backend.executionStore.readClaimSubject(orgId, mine.run.runId);
        const audience = (pinned?.pinned?.spec?.extensions?.['x.pipeshub'] as Record<string, unknown>)['audience'];
        expect(audience).to.deep.include({ visibleRuns: [first.run.runId] });
      });

      it('S2-2 (two users, not readable): a turn from a record Bob cannot read, an unlabelled one and an incomplete one stay stubs', async () => {
        records.grant(`Bearer ${bob}`, 'r1');
        const first = await sharedSession();
        await answer(first, labelOf('r1', 'r-secret'));
        const unlabelled = await send(alice, { sessionId: first.sessionId });
        await answer(unlabelled, null);
        const mine = await send(bob, { sessionId: first.sessionId });
        expect(await visibleOf(mine)).to.equal(undefined);
        expect(mine.run.requires?.features).to.not.include(TURN_LABELS_FEATURE);

        const incomplete = await sharedSession();
        await answer(incomplete, { complete: false, sources: [] });
        const again = await send(bob, { sessionId: incomplete.sessionId });
        expect(await visibleOf(again)).to.equal(undefined);
      });

      it('S2-3 (revocation): access removed after a turn that saw Alice\'s means the next admission stubs it', async () => {
        records.grant(`Bearer ${bob}`, 'r1');
        const first = await sharedSession();
        await answer(first, labelOf('r1'));
        const seen = await send(bob, { sessionId: first.sessionId });
        expect(await visibleOf(seen)).to.deep.equal([first.run.runId]);
        await answer(seen, labelOf('r-bob'));
        records.revoke(`Bearer ${bob}`, 'r1');
        const next = await send(bob, { sessionId: first.sessionId });
        expect(await visibleOf(next)).to.equal(undefined);
      });

      it('ST-4: in a shared context a turn joins its own latest run and its visible runs, never an unseen one', async () => {
        records.grant(`Bearer ${bob}`, 'r1');
        const first = await sharedSession();
        await answer(first, labelOf('r1'));
        const hidden = await send(alice, { sessionId: first.sessionId });
        await answer(hidden, labelOf('r-secret'));
        const mine = await send(bob, { sessionId: first.sessionId });
        expect(await visibleOf(mine)).to.deep.equal([first.run.runId]);
        await answer(mine, labelOf('r-bob'));
        const stored = await storedLabels(first.sessionId, alice);
        expect(stored).to.deep.equal([{ id: mine.run.runId, label: labelOf('r-bob', 'r1') }]);
      });

      it('S2-4: a next run drained without a credential lists no visible run', async () => {
        records.grant(`Bearer ${bob}`, 'r1');
        const first = await sharedSession();
        await answer(first, labelOf('r1'));
        const active = await send(alice, { sessionId: first.sessionId });
        const waiting = await send(bob, { sessionId: first.sessionId });
        expect(waiting.nextRun).to.not.equal(undefined);
        await end(active.run);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => true },
          clock,
        });
        expect(await drains.drain(orgId, first.sessionId)).to.equal('admitted');
        const drained = (await h.service.getActive(orgId, first.sessionId)) as HarnessRun;
        expect(drained.actorId).to.equal(bob);
        const subject = await backend.executionStore.readClaimSubject(orgId, drained.runId);
        expect(subject?.audience?.visibleRuns).to.equal(undefined);
      });

      it('S2-5: a personal session checks nothing and pins no audience', async () => {
        const first = await send(alice);
        await answer(first, labelOf('r1'));
        const second = await send(alice, { sessionId: first.sessionId });
        expect(records.queries).to.deep.equal([]);
        expect(await backend.executionStore.readClaimSubject(orgId, second.run.runId)).to.not.have.nested.property(
          'audience.visibleRuns',
        );
        expect(second.run.requires?.features).to.not.include(TURN_LABELS_FEATURE);
      });
    });
  });
}

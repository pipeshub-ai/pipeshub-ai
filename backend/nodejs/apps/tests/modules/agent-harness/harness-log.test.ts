import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import {
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
} from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import {
  contractClock,
  createContractHarness,
  DURABLE_CAPABILITIES,
} from './support/execution-store-contract';
import { fixedRollout, HARNESS_ENABLED, KILL_SWITCH } from './support/rollout';
import { denyingAccess, grantingAccess } from './support/agent-definition';
import {
  dataKeyRing,
  TEST_KEK_SECRET,
  testKek,
} from './support/data-protection';
import { workerEvent } from './support/journal-fixtures';
import {
  RunAdmissionService,
  RunAdmissionServiceOptions,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import {
  HarnessConflictError,
  HarnessQuotaExceededError,
} from '../../../src/modules/agent-harness/domain/errors';
import { JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { InMemoryJournalStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { createHarnessAuthority } from '../../../src/modules/agent-harness/composition';
import {
  requireAttemptToken,
  requireWorkerCredential,
} from '../../../src/modules/agent-harness/transport/internal-auth';
import { HarnessCapabilityService } from '../../../src/modules/agent-harness/application/capability-service';
import { testCipher } from './support/data-protection';
import { RunContentService } from '../../../src/modules/agent-harness/application/run-content-service';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { currentWorker, newerWorker } from './support/worker-capabilities';

type Line = [level: string, message: string, meta: Record<string, unknown>];

const recording = (): { logger: HarnessLogger; lines: Line[] } => {
  const lines: Line[] = [];
  const at =
    (level: string) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
  return {
    lines,
    logger: {
      debug: at('debug'),
      info: at('info'),
      warn: at('warn'),
      error: at('error'),
    },
  };
};

describe('harness logs (ground rule 9)', () => {
  it('carries the event and only the correlation fields that are set', () => {
    expect(
      harnessLogMeta('run.claimed', {
        ...runLogFields({
          orgId: 'o',
          sessionId: 's',
          runId: 'r',
          attemptId: null,
          fence: 2,
        }),
        commandId: undefined,
        reason: 'x',
      }),
    ).to.deep.equal({
      event: 'run.claimed',
      orgId: 'o',
      sessionId: 's',
      runId: 'r',
      fence: 2,
      reason: 'x',
    });
  });

  it('logs claim, transition and a token-issue release with correlation fields', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const { logger, lines } = recording();
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const admitted = await h.admit({
      agentKey: 'agent-1',
      authorization: 'Bearer secret-user-token',
    });
    const claimed = await service.claim({
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      workerId: 'w',
      attemptId: 'a1',
      leaseDurationMs: 1_000,
    });
    await service.release({
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      workerId: 'w',
      attemptId: 'a1',
      fence: claimed.run.fence,
      reason: 'attempt_token_unavailable',
    });
    const again = await service.claim({
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      workerId: 'w',
      attemptId: 'a2',
      leaseDurationMs: 1_000,
    });
    await service.commit({
      commandId: 'a2:suspend:1',
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      workerId: 'w',
      attemptId: 'a2',
      fence: again.run.fence,
      events: [],
      legacyEvents: [],
      transition: {
        status: 'paused',
        detail: { kind: 'suspend', reason: 'capability_lost' },
      },
      outbox: [],
    });
    const events = lines.map(([level, message]) => `${level}:${message}`);
    expect(events).to.deep.equal([
      'info:run.claimed',
      'info:run.spec_pinned',
      'warn:run.released',
      'info:run.claimed',
      'info:run.transition',
    ]);
    // A release re-queues the run, so both claims took it from the queue.
    expect(lines[0]![2]).to.include({ from: 'queued' });
    expect(lines[3]![2]).to.include({ from: 'queued' });
    expect(lines[1]![2]).to.include({
      runId: admitted.run.runId,
      attemptId: 'a1',
    });
    expect(lines[1]![2]['specDigest']).to.match(/^sha256:[0-9a-f]{64}$/);
    expect(lines[1]![2]['capabilitiesDigest']).to.match(
      /^sha256:[0-9a-f]{64}$/,
    );
    const transition = lines[4]![2];
    expect(transition).to.include({
      runId: admitted.run.runId,
      attemptId: 'a2',
      fence: again.run.fence,
      commandId: 'a2:suspend:1',
      from: 'running',
      to: 'paused',
      detail: 'suspend',
      reason: 'capability_lost',
    });
    expect(transition['journaled']).to.deep.equal(['run.suspended']);
    expect(JSON.stringify(lines)).not.to.contain('secret-user-token');
  });
});

const SENTINELS = {
  query: 'SENTINEL-query-6a1f',
  authorization: 'Bearer SENTINEL-user-token-77c2',
  toolArgs: 'SENTINEL-tool-args-0b9e',
  token: 'SENTINEL.attempt.token',
  credential: 'phw_SENTINEL-worker-credential',
};

const messages = (lines: Line[]): string[] =>
  lines.map(([level, message]) => `${level}:${message}`);

describe('LG-1 harness decision logs (Node S1-S3 catch-up)', () => {
  const admissionService = (
    logger: HarnessLogger,
    options: {
      store?: Record<string, unknown>;
      rollout?: RunAdmissionServiceOptions['rollout'];
      access?: RunAdmissionServiceOptions['agentAccess'];
    } = {},
  ) =>
    new RunAdmissionService({
      store: (options.store ?? {
        answeredRun: async () => null,
        admit: async (command: { runId: string }) => ({
          created: true,
          run: {
            runId: command.runId,
            sessionId: 'session-1',
            profile: 'dispatched',
            harnessMajor: 2,
          },
        }),
        findAdmission: async () => null,
      }) as never,
      rollout:
        options.rollout ??
        fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      clock: { now: () => 1_000 },
      newId: () => 'run-1',
      agentAccess: options.access ?? grantingAccess(),
      logger,
    });

  const submit = (
    service: RunAdmissionService,
    overrides: Record<string, unknown> = {},
  ) =>
    service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: SENTINELS.authorization,
      idempotencyKey: 'k-1',
      deadlineMs: 60_000,
      input: {
        query: SENTINELS.query,
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
      ...overrides,
    });

  const refusedWith = async (work: Promise<unknown>): Promise<void> => {
    try {
      await work;
    } catch {
      return;
    }
    expect.fail('expected a refusal');
  };

  it('LG-1a: admission accepted, legacy and every refusal with its reason code', async () => {
    const { logger, lines } = recording();
    await submit(admissionService(logger));
    await submit(
      admissionService(logger, { rollout: fixedRollout(() => KILL_SWITCH) }),
    );
    await refusedWith(
      submit(admissionService(logger, { access: denyingAccess })),
    );
    await refusedWith(
      submit(admissionService(logger), {
        input: {
          query: SENTINELS.query,
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'auto', modelKey: 'not-granted' },
        },
      }),
    );
    await refusedWith(
      submit(
        admissionService(logger, {
          store: {
            answeredRun: async () => null,
            admit: async () => {
              throw new HarnessQuotaExceededError('user', 'active_runs', 30);
            },
          },
        }),
      ),
    );
    await refusedWith(
      submit(
        admissionService(logger, {
          rollout: {
            decide: () => Promise.reject(new Error('kv down')),
            decideForOrg: () => Promise.reject(new Error('kv down')),
          },
        }),
      ),
    );
    await refusedWith(
      submit(
        admissionService(logger, {
          store: {
            answeredRun: async () => null,
            admit: async () => {
              throw new HarnessConflictError('RUN_ACTIVE', 'busy');
            },
          },
        }),
      ),
    );
    expect(messages(lines)).to.deep.equal([
      'info:admission.accepted',
      'info:admission.legacy',
      'warn:admission.refused',
      'warn:admission.refused',
      'warn:admission.refused',
      'warn:admission.refused',
      'warn:admission.refused',
    ]);
    expect(lines[0]![2]).to.include({
      orgId: 'org-1',
      runId: 'run-1',
      sessionId: 'session-1',
      via: 'session',
      surface: 'web',
      profile: 'dispatched',
      harnessMajor: 2,
      replayed: false,
    });
    expect(lines[1]![2]).to.include({ reason: 'kill_switch', via: 'session', surface: 'web' });
    for (const [, , meta] of lines.slice(2)) expect(meta).to.include({ via: 'session', surface: 'web' });
    expect(lines.slice(2).map(([, , meta]) => meta['reason'])).to.deep.equal([
      'forbidden',
      'narrowing',
      'quota',
      'unavailable',
      'run_active',
    ]);
    expect(lines[4]![2]).to.include({
      scope: 'user',
      quota: 'active_runs',
      retryAfterS: 30,
    });
    const logged = JSON.stringify(lines);
    expect(logged).not.to.contain(SENTINELS.query);
    expect(logged).not.to.contain(SENTINELS.authorization);
  });

  it('T2: a steering message logs input.queued{messageSeq, route, surface} with the run ids, never the text', async () => {
    const { logger, lines } = recording();
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const service = new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      clock,
      newId: () => 'run-steered',
      agentAccess: grantingAccess(),
      logger,
    });
    const first = await submit(service, { idempotencyKey: 'k-run' });
    if (first.route !== 'durable') expect.fail('durable expected');
    const steer = { sessionId: first.admission.sessionId, idempotencyKey: 'k-steer' };
    await submit(service, steer);
    await submit(service, steer);
    expect(messages(lines)).to.deep.equal([
      'info:admission.accepted',
      'info:input.queued',
      'info:input.queued',
    ]);
    expect(lines[1]![2]).to.include({
      event: 'input.queued',
      orgId: 'org-1',
      sessionId: first.admission.sessionId,
      runId: 'run-steered',
      actorId: 'user-1',
      messageSeq: 1,
      route: 'agent',
      surface: 'web',
      via: 'session',
      replayed: false,
      claimChecked: false,
    });
    expect(lines[2]![2]).to.include({ messageSeq: 1, replayed: true });
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.query);
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.authorization);
  });

  it('T2b (C-21c): a steering message over the inline bound logs claimChecked, never the text', async () => {
    const { logger, lines } = recording();
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const service = new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      clock,
      newId: () => 'run-steered',
      agentAccess: grantingAccess(),
      logger,
    });
    const first = await submit(service, { idempotencyKey: 'k-run' });
    if (first.route !== 'durable') expect.fail('durable expected');
    const long = `${SENTINELS.query}${'\u20ac'.repeat(99_000)}`;
    await submit(service, {
      sessionId: first.admission.sessionId,
      idempotencyKey: 'k-long',
      input: { query: long, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    const queued = lines.filter(([, message]) => message === 'input.queued');
    expect(queued.map(([, , meta]) => meta['claimChecked'])).to.deep.equal([true]);
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.query);
  });

  it('T2b (C-21b): input.queued counts attachments and records; a denied reference is refused as references', async () => {
    const { logger, lines } = recording();
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const records = new InMemoryRecordReadAccess()
      .grant(SENTINELS.authorization, 'rec-ok', 'rec-kb')
      .content('rec-ok', 'vr-ok');
    const service = new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      clock,
      newId: () => 'run-steered',
      agentAccess: grantingAccess(),
      logger,
      references: records,
    });
    const first = await submit(service, { idempotencyKey: 'k-run' });
    if (first.route !== 'durable') expect.fail('durable expected');
    const refs = (attachments: Array<Record<string, unknown>>, recordIds: string[]) => ({
      sessionId: first.admission.sessionId,
      input: { query: SENTINELS.query, recordIds, filters: {}, attachments, model: { chatMode: 'auto' } },
    });
    await submit(service, { idempotencyKey: 'k-ok', ...refs([{ recordId: 'rec-ok', virtualRecordId: 'vr-ok', recordName: SENTINELS.query }], ['rec-kb']) });
    await submit(service, { idempotencyKey: 'k-no', ...refs([], ['rec-secret']) }).catch(() => undefined);
    const queued = lines.find(([, message]) => message === 'input.queued')?.[2];
    expect(queued).to.include({ attachments: 1, records: 1, claimChecked: false });
    const refused = lines.find(([, message]) => message === 'admission.refused')?.[2];
    expect(refused).to.include({ reason: 'references' });
    const logged = JSON.stringify(lines);
    expect(logged).not.to.contain(SENTINELS.query);
    expect(logged).not.to.contain('rec-secret');
    expect(logged).not.to.contain(SENTINELS.authorization);
  });

  it('T3: an import logs session.imported{upToMessageSeq, messages, digest}; a mismatch warns first; a replay logs neither', async () => {
    const { logger, lines } = recording();
    const digest = `sha256:${'a'.repeat(64)}`;
    const admitted = (created: boolean, mismatch?: 'changed') =>
      admissionService(logger, {
        store: {
          answeredRun: async () => null,
          admit: async (command: { runId: string }) => ({
            created,
            run: { runId: command.runId, sessionId: 'session-1', profile: 'dispatched', harnessMajor: 2 },
            imported: {
              upToMessageSeq: 4,
              messages: 4,
              digest,
              replacesHistory: mismatch !== undefined,
              sealed: false,
              ...(mismatch !== undefined ? { mismatch } : {}),
            },
          }),
        },
      });
    await submit(admitted(true), { sessionId: 'session-1' });
    await submit(admitted(true, 'changed'), { sessionId: 'session-1' });
    await submit(admitted(false, 'changed'), { sessionId: 'session-1' });
    expect(messages(lines)).to.deep.equal([
      'info:admission.accepted',
      'info:session.imported',
      'info:admission.accepted',
      'warn:session.import_mismatch',
      'info:session.imported',
      'info:admission.accepted',
    ]);
    const ids = { orgId: 'org-1', sessionId: 'session-1', runId: 'run-1', actorId: 'user-1' };
    expect(lines[1]![2]).to.include({
      ...ids,
      event: 'session.imported',
      upToMessageSeq: 4,
      messages: 4,
      digest,
      replacesHistory: false,
      sealed: false,
    });
    expect(lines[3]![2]).to.include({
      ...ids,
      event: 'session.import_mismatch',
      reason: 'changed',
      choice: 'reimport',
      upToMessageSeq: 4,
    });
    expect(lines[4]![2]).to.include({ replacesHistory: true });
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.query);
  });

  it('T2: a completion refused for queued input is logged at info with its reason', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const { logger, lines } = recording();
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const agent = { agentKey: 'agent-1', authorization: SENTINELS.authorization };
    const { run, sessionId } = await h.admit(agent);
    const claimed = await service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: 'a1',
      leaseDurationMs: 60_000,
    });
    await h.admit({ ...agent, sessionId });
    await refusedWith(
      service.commit({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w',
        attemptId: 'a1',
        fence: claimed.run.fence,
        commandId: 'a1:complete:1',
        events: [],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      }),
    );
    const refused = lines.filter(([, message]) => message === 'run.commit_refused');
    expect(refused.map(([level, , meta]) => [level, meta['reason']])).to.deep.equal([
      ['info', 'INPUT_PENDING'],
    ]);
  });

  it('T2b (C-21): terminal lines count the messages a run leaves (leftInputs), never their text', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const { logger, lines } = recording();
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const agent = { agentKey: 'agent-1', authorization: SENTINELS.authorization };
    const steer = (sessionId: string) =>
      h.admit({
        ...agent,
        sessionId,
        input: { query: SENTINELS.query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      });
    const failed = await h.admit(agent);
    const claimed = await service.claim({
      orgId: failed.run.orgId,
      runId: failed.run.runId,
      workerId: 'w',
      attemptId: 'a1',
      leaseDurationMs: 60_000,
    });
    await steer(failed.sessionId);
    await steer(failed.sessionId);
    await service.commit({
      orgId: failed.run.orgId,
      runId: failed.run.runId,
      workerId: 'w',
      attemptId: 'a1',
      fence: claimed.run.fence,
      commandId: 'a1:failed:1',
      events: [],
      legacyEvents: [],
      transition: { status: 'failed', errorType: 'model_failed', detail: { kind: 'fail' } },
      outbox: [],
    });
    const cancelled = await h.admit(agent);
    await steer(cancelled.sessionId);
    await service.control({
      commandId: 'cancel',
      dispatchCommandId: 'cancel:dispatch',
      orgId: cancelled.run.orgId,
      runId: cancelled.run.runId,
      expectedRevision: (await service.get(cancelled.run.orgId, cancelled.run.runId))!.revision,
      action: 'cancel',
    });
    const expired = await h.admit(agent);
    await steer(expired.sessionId);
    clock.set(clock.now() + 60 * 60 * 1000);
    await service.sweep(10);
    const line = (message: string) => lines.find(([, m]) => m === message)?.[2];
    expect(line('run.transition')).to.include({ runId: failed.run.runId, to: 'failed', leftInputs: 2 });
    expect(line('run.control')).to.include({ runId: cancelled.run.runId, action: 'cancel', leftInputs: 1 });
    expect(line('sweeper.expired')).to.include({ runId: expired.run.runId, to: 'failed', leftInputs: 1 });
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.query);
  });

  it('T1a: an admission names the built-in assistant and its chat mode, never a free-text mode', async () => {
    const { logger, lines } = recording();
    const assistant = (chatMode: string) => ({
      agentKey: undefined,
      authorization: undefined,
      input: { query: SENTINELS.query, recordIds: [], filters: {}, attachments: [], model: { chatMode } },
    });
    await submit(admissionService(logger), assistant('internal_search'));
    await submit(admissionService(logger), assistant(`${SENTINELS.query} mode`));
    expect(lines.map(([, , meta]) => [meta['assistant'], meta['chatMode']])).to.deep.equal([
      [true, 'internal_search'],
      [true, 'other'],
    ]);
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.query);
  });

  it('LG-1b: claim refused, renew, renew refused, journal rejected and commit refused', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const { logger, lines } = recording();
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const { run } = await h.admit({
      agentKey: 'agent-1',
      authorization: SENTINELS.authorization,
    });
    const attempt = { orgId: run.orgId, runId: run.runId, workerId: 'w' };
    const claimed = await service.claim({
      ...attempt,
      attemptId: 'a1',
      leaseDurationMs: 60_000,
    });
    await refusedWith(
      service.claim({ ...attempt, attemptId: 'a2', leaseDurationMs: 60_000 }),
    );
    await service.renew({
      ...attempt,
      attemptId: 'a1',
      fence: claimed.run.fence,
      leaseDurationMs: 60_000,
    });
    await refusedWith(
      service.renew({
        ...attempt,
        attemptId: 'a1',
        fence: claimed.run.fence + 1,
        leaseDurationMs: 60_000,
      }),
    );
    const commit = (events: JournalEventInput[], fence = claimed.run.fence) =>
      service.commit({
        ...attempt,
        commandId: `a1:x:${String(events.length)}:${String(fence)}`,
        attemptId: 'a1',
        fence,
        events,
        legacyEvents: [],
        outbox: [],
      });
    await refusedWith(
      commit([
        workerEvent('turn.started', { turn: 1 }),
        workerEvent('run.completed', { outcome: 'answered' }),
      ]),
    );
    await refusedWith(
      commit(
        [
          workerEvent('tool.outcome', {
            invocationId: 'i',
            status: 'ok',
            receipt: { a: SENTINELS.toolArgs },
            labels: { integrity: 'external' },
          }),
        ],
        claimed.run.fence + 1,
      ),
    );
    const found = messages(lines);
    expect(found).to.include.members([
      'info:run.claim_refused',
      'debug:run.renewed',
      'warn:run.renew_refused',
      'warn:journal.rejected',
      'warn:run.commit_refused',
    ]);
    const meta = (message: string) => lines.find(([, m]) => m === message)![2];
    expect(meta('run.claim_refused')).to.include({
      attemptId: 'a2',
      reason: 'RUN_NOT_CLAIMABLE',
    });
    expect(meta('run.renewed')).to.include({
      attemptId: 'a1',
      leaseMs: 60_000,
    });
    expect(meta('run.renew_refused')['reason']).to.be.a('string');
    expect(meta('journal.rejected')).to.include({
      reason: 'WRITER_NOT_ALLOWED',
      index: 1,
      type: 'run.completed',
      commandId: `a1:x:2:${String(claimed.run.fence)}`,
    });
    expect(meta('run.commit_refused')).to.include({ reason: 'STALE_ATTEMPT' });
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.toolArgs);
    expect(JSON.stringify(lines)).not.to.contain(SENTINELS.authorization);
  });

  it('LG-1c: authority refusals name the reason and route, never the token or credential', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    const authority = createHarnessAuthority(
      {
        workerCredential: new InMemoryConfigDocumentStore(),
        attemptSigningKeys: new InMemoryConfigDocumentStore(),
        attemptPublicKeys: new InMemoryConfigDocumentStore(),
      },
      h.service,
    );
    await authority.provision(null);
    const { logger, lines } = recording();
    const refuse = async (
      middleware: ReturnType<typeof requireAttemptToken>,
      token: string,
    ): Promise<unknown> =>
      new Promise((resolve) => {
        void middleware(
          {
            headers: { authorization: `Bearer ${token}` },
            params: { runId: 'run-9' },
            method: 'POST',
            baseUrl: '/internal',
            route: { path: '/runs/:runId/commit' },
            path: '/runs/run-9/commit',
          } as never,
          {} as never,
          resolve,
        );
      });
    await refuse(requireAttemptToken(authority, logger), SENTINELS.token);
    await refuse(
      requireWorkerCredential(authority, logger),
      SENTINELS.credential,
    );
    expect(messages(lines)).to.deep.equal([
      'warn:authority.refused',
      'warn:authority.refused',
    ]);
    expect(lines[0]![2]).to.include({
      reason: 'malformed',
      method: 'POST',
      route: '/internal/runs/:runId/commit',
      runId: 'run-9',
    });
    expect(lines[1]![2]).to.include({ reason: 'credential' });
    const logged = JSON.stringify(lines);
    expect(logged).not.to.contain('SENTINEL');
  });

  it('LG-1d: capability changes name the keys that changed; a probe that fails leaves its keys unknown (ADR-CAP-01)', async () => {
    let livebus: 'redis' | 'inproc' = 'redis';
    let fail = false;
    const { logger, lines } = recording();
    const service = new HarnessCapabilityService(
      [
        { name: 'journal', provides: ['journal.durable'], probe: async () => ({ 'journal.durable': true }) },
        {
          name: 'livebus',
          provides: ['livebus'],
          probe: async () => {
            if (fail) throw new Error('redis down');
            return { livebus };
          },
        },
      ],
      logger,
    );
    await service.refresh();
    await service.refresh();
    livebus = 'inproc';
    await service.refresh();
    fail = true;
    await service.refresh();
    expect(messages(lines)).to.deep.equal([
      'info:capability.changed',
      'info:capability.changed',
      'warn:capability.probe_failed',
      'info:capability.settled',
    ]);
    expect(lines[1]![2]['changed']).to.deep.equal(['livebus=inproc']);
    expect(lines[1]![2]['previousDigest']).to.equal(lines[0]![2]['digest']);
    expect(lines[2]![2]).to.include({
      probe: 'livebus',
      outcome: 'unknown',
    });
    expect(lines[3]![2]).to.deep.include({ settled: false, unknown: ['livebus'] });
  });

  it('LG-1e: the sweeper logs each run it expires', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    const { logger, lines } = recording();
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const { run } = await h.admit();
    await h.claim(run, 'w', 1_000);
    clock.set(clock.now() + 2_000);
    expect(await service.sweep(10)).to.equal(1);
    const expired = lines.find(([, message]) => message === 'sweeper.expired');
    expect(expired?.[0]).to.equal('info');
    expect(expired?.[2]).to.include({
      runId: run.runId,
      from: 'running',
      to: 'queued',
      reason: 'lease_expired',
    });
  });
});

describe('LG-4 redaction across the S8 paths', () => {
  it('no sentinel content, token, credential, data key or KEK reaches any log', async () => {
    const { logger, lines } = recording();
    const ring = dataKeyRing({ logger });
    const raw = new InMemoryJournalStore();
    const journal = new SealedJournalStore(raw, ring.cipher, logger);
    const service = new JournalService(new InMemoryPayloadStore(), ring.cipher);
    const prepared = await service.prepare({
      orgId: 'org-1',
      sessionId: 's',
      streamId: 's',
      runId: 'r',
      writer: 'worker',
      events: [
        workerEvent('tool.outcome', {
          invocationId: 'i',
          status: 'ok',
          receipt: { args: SENTINELS.toolArgs, query: SENTINELS.query },
          labels: { integrity: 'external' },
        }),
      ],
    });
    await journal.append(prepared, undefined);
    const key = await ring.keys.active('org-1');
    await ring.keys.rotate('org-1');
    await ring.keys.shred('org-1');
    try {
      await journal.readStream('org-1', 's', 0, 10);
    } catch {
      // Unreadable after the shred; the point is what it logged.
    }
    const wrong = dataKeyRing({
      store: ring.store,
      kek: testKek(Buffer.alloc(32, 3)),
      logger,
    });
    await wrong.keys.active('org-2');
    const other = dataKeyRing({ store: wrong.store, logger });
    try {
      await other.keys.active('org-2');
    } catch {
      // A KEK that cannot unwrap; logged without key material.
    }
    const logged = JSON.stringify(lines);
    expect(messages(lines)).to.include.members([
      'info:data_key.created',
      'info:data_key.shredded',
      'error:journal.content_unreadable',
      'error:data_key.unwrap_failed',
    ]);
    for (const sentinel of Object.values(SENTINELS)) {
      expect(logged).not.to.contain(sentinel);
    }
    expect(logged).not.to.contain(key.key.toString('base64'));
    expect(logged).not.to.contain(key.key.toString('hex'));
    expect(logged).not.to.contain(TEST_KEK_SECRET.toString('hex'));
    for (const stored of await ring.store.list('org-2')) {
      expect(logged).not.to.contain(stored.wrapped);
    }
  });

  it('LG-5: sealed content paths (sealed, opened, refused) and rotation by count log no content', async () => {
    const { logger, lines } = recording();
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    const ring = dataKeyRing({
      logger,
      keyOptions: { rotateAfterSeals: 2, sealReservation: 1 },
    });
    const content = new RunContentService(
      h.service,
      ring.cipher,
      payloads,
      logger,
    );
    const claimsOf = (run: HarnessRun) => ({
      orgId: run.orgId,
      runId: run.runId,
      attemptId: run.attemptId!,
      fence: run.fence,
      workerId: run.workerId!,
    });
    const writer = await h.claim((await h.admit()).run);
    const text = Buffer.from(Object.values(SENTINELS).join('|'));
    const refs = [];
    for (let n = 0; n < 3; n += 1) {
      refs.push((await content.put(claimsOf(writer), text)).ref);
    }
    await content.get(claimsOf(writer), refs[0]!);
    const foreign = await h.claim((await h.admit()).run, 'worker-b');
    await content.get(claimsOf(foreign), refs[0]!).catch(() => undefined);
    await ring.keys.shred(writer.orgId);
    await content.get(claimsOf(writer), refs[1]!).catch(() => undefined);
    expect(messages(lines)).to.include.members([
      'debug:content.sealed',
      'debug:content.opened',
      'warn:content.refused',
      'info:data_key.created',
    ]);
    expect(
      lines
        .filter(([, message]) => message === 'content.refused')
        .map(([, , meta]) => meta['reason']),
    ).to.deep.equal(['foreign_run', 'shredded']);
    expect(
      lines
        .filter(([, message]) => message === 'data_key.created')
        .map(([, , meta]) => meta['reason']),
    ).to.include('seal_count');
    const logged = JSON.stringify(lines);
    for (const sentinel of Object.values(SENTINELS)) {
      expect(logged).not.to.contain(sentinel);
    }
  });
});

describe('claim.filtered (P1b RU-2e, ground rule 9)', () => {
  const setup = () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const { logger, lines } = recording();
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    return { h, service, lines };
  };
  const filtered = (lines: Line[]) => lines.filter(([, message]) => message === 'claim.filtered');

  it('logs a filtered run once, by id or by poll, with the versions and what is missing, and no content', async () => {
    const { h, service, lines } = setup();
    const admitted = await h.admit({
      agentKey: 'agent-1',
      authorization: 'Bearer secret-user-token',
      input: {
        query: 'SENTINEL-QUERY',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'quick' },
      },
    });
    const claimed = await service.claim({
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      workerId: 'w-new',
      attemptId: 'a1',
      leaseDurationMs: 1_000,
      worker: newerWorker(),
    });
    await service.release({
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      workerId: 'w-new',
      attemptId: 'a1',
      fence: claimed.run.fence,
      reason: 'drain',
    });
    const old = currentWorker();
    for (let i = 0; i < 2; i += 1) {
      try {
        await service.claim({
          orgId: admitted.run.orgId,
          runId: admitted.run.runId,
          workerId: 'w-old',
          attemptId: `b${String(i)}`,
          leaseDurationMs: 1_000,
          worker: old,
        });
        expect.fail('an older worker claimed a newer run');
      } catch (error) {
        expect((error as HarnessConflictError).code).to.equal('WORKER_INCOMPATIBLE');
      }
      expect(
        await service.claimNext({
          role: 'dispatched',
          workerId: 'w-old',
          attemptId: `c${String(i)}`,
          leaseDurationMs: 1_000,
          worker: old,
        }),
      ).to.equal(null);
    }
    const [line, ...again] = filtered(lines);
    expect(again, 'logged once while it waits').to.deep.equal([]);
    expect(line?.[0]).to.equal('info');
    expect(line?.[2]).to.deep.include({
      event: 'claim.filtered',
      orgId: admitted.run.orgId,
      sessionId: admitted.run.sessionId,
      runId: admitted.run.runId,
      workerId: 'w-old',
      reason: 'schema',
      via: 'hint',
      missing: ['foldVersion'],
      workerFoldVersion: old.foldVersion,
      runFoldVersion: old.foldVersion + 1,
      outcome: 'waiting',
    });
    expect(JSON.stringify(lines)).to.not.include('SENTINEL-QUERY');
    expect(JSON.stringify(lines)).to.not.include('secret-user-token');

    const taken = await service.claimNext({
      role: 'dispatched',
      workerId: 'w-new',
      attemptId: 'd1',
      leaseDurationMs: 1_000,
      worker: newerWorker(),
    });
    expect(taken?.run.runId).to.equal(admitted.run.runId);
    const claimedLine = lines.filter(([, message]) => message === 'run.claimed').at(-1);
    expect(claimedLine?.[2]['workerFoldVersion']).to.equal(old.foldVersion + 1);
    expect(claimedLine?.[2]['waitedForWorkerMs']).to.be.a('number');
  });

  it('a poll that finds only runs it cannot run logs each once', async () => {
    const { h, service, lines } = setup();
    const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
    expect(
      await service.claimNext({
        role: 'dispatched',
        workerId: 'w-none',
        attemptId: 'a1',
        leaseDurationMs: 1_000,
        worker: currentWorker({ features: [], foldVersion: 1 }),
      }),
    ).to.equal(null);
    expect(filtered(lines).map(([, , meta]) => [meta['runId'], meta['via'], meta['missing']])).to.deep.equal([
      [admitted.run.runId, 'poll', ['foldVersion']],
    ]);
  });
});

/**
 * R37-SECC proof tests. Each case asserts the
 * secure behaviour and is a `knownHole` until its SEC-C finding is fixed.
 */
import { expect } from 'chai';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { codeTaskAuthority } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { pinRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { AGENT_MODELS, DEFAULT_MODEL, InMemoryConfiguredModels } from './support/model-access';
import { KEYS, MemoryKv, platformCipher } from './support/model-accounts';
import { HARNESS_ENABLED } from './support/rollout';
import { testControlPlane } from '../control-plane/support/in-memory';
import crypto from 'node:crypto';
import http from 'node:http';
import { AddressInfo } from 'node:net';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { redactPastedSecrets } from '../../../src/modules/agent-harness/domain/pasted-secrets';
import { PASTED_INPUT_RULES } from '../../../src/modules/agent-harness/contracts/vectors/token-patterns';
import { ConversationGrantReach, GrantGiven } from '../../../src/modules/agent-harness/application/conversation-grant-reach';
import { runGrantState } from '../../../src/modules/agent-harness/domain/conversation-grants';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { AGENT_HARNESS_AUTHORITY, AGENT_HARNESS_EXECUTION_SERVICE } from '../../../src/modules/agent-harness/composition';
import { HarnessUnauthorizedError, HarnessScheduleRefusedError, HarnessTriggerRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { AGENT_HARNESS_INTERNAL_PREFIX } from '../../../src/modules/agent-harness/transport/internal-routes';
import { createAgentHarnessInternalApp } from '../../../src/modules/agent-harness/transport/internal-listener';
import { createGlobalRateLimiter } from '../../../src/libs/middlewares/rate-limit.middleware';
import { UNFRAMEABLE_PAGE_PATHS } from '../../../src/libs/middlewares/no-framing.middleware';
import * as botRegistry from '../../../src/integrations/slack-bot/src/botRegistry';
import { registerSlackEventGuards, resetSlackEventGuardsForTests } from '../../../src/integrations/slack-bot/src/receiver';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { ScheduleService, type ScheduleCaller, type CreateScheduleRequest } from '../../../src/modules/agent-harness/application/schedule-service';
import { webhookSignature } from '../../../src/modules/agent-harness/domain/schedules';
import {
  ALICE,
  FakeActors,
  FakeAdmission,
  FakeNotifier,
  FakeRecordAccess,
  FakeRuns,
  FakeSetting,
  FakeTriggerSettings,
  ORG,
  recordingLogger,
  type RecordedLog,
  TestClock,
} from './support/schedules';
import { Logger } from '../../../src/libs/services/logger.service';
import { persistedRun } from '../../contracts/support/surfaces';
import { startChatTurnHarness, USER } from './support/chat-turn-harness';
import { SURFACE_HEADER } from '../../../src/modules/agent-harness/transport/surface-header';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_SESSION_WORKSPACES,
} from '../../../src/modules/agent-harness/composition';
import { createChatHarnessSessionRouter } from '../../../src/modules/agent-harness/transport/session-routes';
import { Server } from 'node:http';
import { InMemoryConfigDocumentStore as ConfigDocs } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import { GatewayRefusalJournal } from '../../../src/modules/agent-harness/application/gateway-refusal-journal';
import { validatingPreparer } from '../../../src/modules/agent-harness/ports/journal-store';
import { AGENT_HARNESS_GATEWAY_TOKENS, createHarnessAuthority } from '../../../src/modules/agent-harness/composition';
import { AttemptKeyring, PREVIOUS_WORKER_CREDENTIAL_GRACE_MS, PublishedAttemptKeys, WorkerCredentialSet } from '../../../src/modules/agent-harness/domain/authority';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { proofFor, testBindingKeys } from './support/binding-proof';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

describe('SEC-C-02: a member names an org cloud account and any model name', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let service: ExecutionService;
  let vault: ModelAccountVault;

  /** A member's turn on an engine agent that grants no models, handed off to its engine child. */
  const engineChildPolicy = async (model: Partial<RunInputSnapshot['model']>) => {
    const engineProfiles = new FakeEngineCeilings();
    const access = new ModelAccessService({
      offering: 'oss',
      policies: new FakeModelAccessPolicies(),
      // The org configured the cloud account's deployment for gpt-5 only.
      models: new InMemoryConfiguredModels([DEFAULT_MODEL, ...AGENT_MODELS, { provider: 'azureOpenAI', modelKey: 'm-azure', modelNames: ['gpt-5'] }]),
      accounts: {
        list: (...args) => vault.list(...args),
        get: (...args) => vault.get(...args),
        put: (...args) => vault.put(...args),
        revoke: (...args) => vault.revoke(...args),
      },
    });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, models: [], engineProfile: { engines: ['opencode'] } })),
      engineProfiles,
      modelAccess: access,
    }).submit({
      orgId: 'org-1',
      actorId: 'member-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now++)}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', ...model } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    const claimed = await service.claim({
      orgId: 'org-1',
      runId: outcome.admission.run.runId,
      workerId: 'w',
      attemptId: `a-${outcome.admission.run.runId}`,
      leaseDurationMs: 1_000,
    });
    if (codeTaskAuthority(claimed.pinned!.spec) === null) return claimed.pinned!.spec.model.policy[0];
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(engineProfiles),
      engineAccess: (subject) => access.decide(subject),
    }).spawn(
      { orgId: 'org-1', runId: claimed.run.runId, workerId: 'w', attemptId: `a-${claimed.run.runId}`, fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'build it', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject('org-1', spawned.childRunId);
    return child!.pinned!.spec.model.policy[0];
  };

  beforeEach(async () => {
    now = 1_000;
    store = new InMemoryExecutionStore({ now: () => now });
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: pinRunSpec,
    });
    vault = new ModelAccountVault(new MemoryKv(), platformCipher);
    await vault.put('org-1', { kind: 'org' }, 'acc-1', {
      kind: 'org_cloud',
      provider: 'azureOpenAI',
      label: 'EU',
      material: { provider: 'azureOpenAI', apiKey: KEYS.azure, endpoint: 'https://x.openai.azure.com', deploymentName: 'gpt-5' },
      hint: KEYS.azure,
    }, 'admin');
  });

  it('SEC-C-02: a model name the org never configured is refused on its cloud account', async () => {
    let pinned: unknown;
    try {
      pinned = await engineChildPolicy({ accountId: 'cloud/acc-1', modelName: 'o1-pro-most-expensive' });
    } catch (error) {
      // Refused, as a model access refusal the chat words (not a crash): the agent pins no cloud account.
      expect(error).to.include({ name: 'HarnessModelAccessRefusedError', code: 'account_not_owned' });
      return;
    }
    expect(pinned, 'the run was pinned to an unconfigured model on the org account').to.equal(undefined);
  });
});

const serve = async (app: express.Express): Promise<{ base: string; close: () => Promise<void> }> => {
  const server = await new Promise<http.Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const { port } = server.address() as AddressInfo;
  return {
    base: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
};

describe('SEC-C-01: the pasted-secret guard is linear in its input', () => {
  // Fixed in R38-INPUT, proven without a clock (WALLCLOCK-1): the vector's patterns are linear by structure
  // (Python's test_sec_c_pattern_budget.py lints the same source strings Node ships), an anonymous body never
  // reaches the scan, and an over-long field is refused before it (sec-c-01-input-budget.test.ts).
  it('40 000 trailing dots after a keyword: the trailing strip starts only at the first dot of a run', () => {
    // The lookbehind refuses every start inside the run, so the run is read once, not once per dot.
    expect(PASTED_INPUT_RULES.contextual.trailing, 'quadratic TRAILING strip ([.:!?]+$)').to.equal('(?<![.:!?])[.:!?]+$');
    const dots = `token ${'.'.repeat(40_000)}a`;
    expect(redactPastedSecrets(dots).text).to.equal(dots);
  });
});

const FETCH = 'web@searxng/fetch_url';
const DEST = 'sha256:4c5c0f51fdaf2b50038b8c674f654f5407e153408d5688d5b5fbcf38395aa0c3';
const journalEvent = (runId: string, seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent =>
  ({
    orgId: 'org-1', sessionId: 'ses-1', streamId: runId, runId, seq, type, writer,
    schemaVersion: 1, critical: false, visibility: 'owner', ts: '2026-11-13T00:00:00.000Z', data,
  }) as JournalEvent;
const ask = (runId: string, seq: number, ix: string): JournalEvent[] => [
  journalEvent(runId, seq, 'interaction.explained', 'worker', {
    interactionId: ix, toolId: FETCH, rule: 'flow_external_write', layer: 'flow', conversationGrant: true,
    sensitivity: 'private', destination: { key: DEST, label: 'attacker.example' },
  }),
  journalEvent(runId, seq + 1, 'interaction.requested', 'worker', { interactionId: ix, kind: 'approval' }),
];
const sessionRun = (runId: string, actorId: string): HarnessRun =>
  ({ orgId: 'org-1', sessionId: 'ses-1', runId, harnessMajor: 2, status: 'running', actorId }) as unknown as HarnessRun;

describe("SEC-C-04: one participant's conversation grant does not answer another participant's asks", () => {
  it("a collaborator's \"allow for this chat\" leaves the owner's run asking", async () => {
    const streams: Record<string, JournalEvent[]> = {
      'collab-run': ask('collab-run', 1, 'ix.collab'),
      'owner-run': ask('owner-run', 1, 'ix.owner'),
    };
    const runs = [sessionRun('collab-run', 'u-collab'), sessionRun('owner-run', 'u-owner')];
    const resolveAll = sinon.stub().resolves({});
    const reach = new ConversationGrantReach<undefined>(
      {
        grantState: async (_org: string, runId: string) => runGrantState(streams[runId] ?? []),
        openSessionRuns: async () => runs,
      },
      { resolveAll } as never,
      { append: async () => ({ streamId: 'x', firstSeq: 1, lastSeq: 1, ts: '' }) } as never,
      { prepare: async (append: never) => append as never },
      async (work) => work(undefined),
    );
    streams['collab-run']!.push(
      journalEvent('collab-run', 3, 'interaction.resolved', 'node', { interactionId: 'ix.collab', decision: 'approved' }),
      journalEvent('collab-run', 4, 'interaction.granted', 'node', {
        interactionId: 'ix.collab', scope: 'conversation', toolId: FETCH, key: DEST, sensitivity: 'private',
      }),
    );
    const given: GrantGiven = {
      orgId: 'org-1', sessionId: 'ses-1', runId: 'collab-run', interactionIds: ['ix.collab'],
      by: 'p_collab', channel: 'web', commandId: 'cmd-1',
    };
    await reach.reach(given);
    const answeredOwner = resolveAll.getCalls().some((call) => (call.args[0] as { runId: string }).runId === 'owner-run');
    expect(answeredOwner, "the owner's ask was approved as the collaborator").to.equal(false);
  });
});

describe('SEC-C-03: a Slack event from another workspace is not run as a PipesHub user', () => {
  const SECRET = 'test-signing-secret';
  afterEach(() => sinon.restore());
  it('SEC-C-03: an event whose user belongs to another Slack team (Slack Connect) never reaches the handler', async () => {
    resetSlackEventGuardsForTests();
    sinon.stub(botRegistry, 'refreshSlackBotRegistry').resolves([
      { botToken: 'xoxb-test', signingSecret: SECRET, botId: 'B1', agentId: null },
    ]);
    let handled = 0;
    const app = express();
    registerSlackEventGuards(app);
    app.use('/slack/events', (_req, res) => {
      handled += 1;
      res.status(200).send('handled');
    });
    const { base, close } = await serve(app);
    try {
      const body = JSON.stringify({
        type: 'event_callback', event_id: 'Ev-ext', team_id: 'T-HOME',
        event: { type: 'app_mention', user: 'U-EXT', user_team: 'T-OTHER', team: 'T-OTHER', text: '<@B1> summarise the board deck' },
      });
      const timestamp = String(Math.floor(Date.now() / 1000));
      const signature = `v0=${crypto.createHmac('sha256', SECRET).update(`v0:${timestamp}:${body}`).digest('hex')}`;
      await fetch(`${base}/slack/events`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-slack-request-timestamp': timestamp, 'x-slack-signature': signature },
        body,
      });
      // The receiver refuses it (ADR-SLACKID-01); the bypass variants are in r38-slack-identity.test.ts.
      expect(handled, 'the foreign-team event was handled').to.equal(0);
    } finally {
      await close();
    }
  });
});

describe('SEC-C-09: the internal listener authenticates before it reads a body', () => {
  it('an unauthenticated POST with a malformed JSON body is 401, not a parse error', async () => {
    const c = new Container();
    c.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
      verifyWorker: async () => {
        throw new HarnessUnauthorizedError('credential', 'worker credential required');
      },
      verifyAttemptToken: async () => {
        throw new HarnessUnauthorizedError('signature', 'attempt token required');
      },
    });
    c.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
    const { base, close } = await serve(createAgentHarnessInternalApp(c));
    try {
      const response = await fetch(`${base}${AGENT_HARNESS_INTERNAL_PREFIX}/runs/claim`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{bad',
      });
      expect(response.status, 'the 10 MB JSON parser ran before the worker credential').to.equal(401);
    } finally {
      await close();
    }
  });
});

describe('SEC-C-16: the global rate limit cannot be skipped by a path segment', () => {
  it("a public route whose id segment is 'internal' is still counted", async () => {
    const logger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} } as unknown as Logger;
    const app = express();
    app.use(createGlobalRateLimiter(logger, 2));
    app.get('/api/v1/conversations/:sessionId/runs/:runId', (_req, res) => {
      res.status(200).send('ok');
    });
    const { base, close } = await serve(app);
    try {
      const statuses: number[] = [];
      for (let i = 0; i < 4; i += 1) {
        statuses.push((await fetch(`${base}/api/v1/conversations/internal/runs/r${String(i)}`)).status);
      }
      expect(statuses, 'every request skipped the limiter').to.include(429);
    } finally {
      await close();
    }
  });
});

describe('SEC-C-14: approve-or-deny pages cannot be framed', () => {
  it('the agent claim consent page is in the unframeable list', () => {
    expect(UNFRAMEABLE_PAGE_PATHS).to.include('/agent/claim');
  });
});

describe('SEC-C-13: a signed webhook delivery is accepted once', () => {
  async function webhookWorld(coalesceS = 60) {
    const clock = new TestClock();
    clock.value = Date.parse('2026-07-01T00:00:00.000Z');
    const store = new InMemoryScheduleStore(clock);
    const logs: RecordedLog[] = [];
    const actors = new FakeActors();
    const setting = new FakeSetting();
    const settings = new FakeTriggerSettings();
    const firer = new ScheduleFirer({
      store, admission: new FakeAdmission(), actors, setting, runs: new FakeRuns(),
      notifier: new FakeNotifier(), records: new FakeRecordAccess(), settings, logger: recordingLogger(logs),
    });
    const service = new ScheduleService({
      transaction: testControlPlane(), store, firer, actors, setting, triggers: settings,
      logger: recordingLogger(logs), newSecret: () => 'pxs_secret_1',
    });
    const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
    const request: CreateScheduleRequest = {
      name: 'Triage', agentKey: 'support-agent', input: { query: 'Triage what came in' },
      trigger: { kind: 'webhook', coalesceS },
    };
    const created = await service.create(alice, request);
    const secret = created.webhookSecret!;
    const sign = (body: Buffer, at = clock.value) => {
      const timestamp = String(Math.floor(at / 1000));
      return { body, timestamp, signature: webhookSignature(secret, timestamp, body) };
    };
    /** The firing, or null when the delivery started nothing (a duplicate, or refused). */
    const fire = async (delivery: Parameters<ScheduleService['webhook']>[1]) => {
      try {
        return (await service.webhook(created.schedule.id, delivery)).firing;
      } catch (error) {
        if (error instanceof HarnessTriggerRefusedError || error instanceof HarnessScheduleRefusedError) return null;
        throw error;
      }
    };
    return { clock, service, created, sign, fire, logs };
  }

  it('the same signed request replayed in the next coalescing window fires nothing', async () => {
    const w = await webhookWorld();
    const captured = w.sign(Buffer.from('{"issue":"PH-1"}'));
    expect(await w.fire(captured)).to.not.equal(null);
    w.clock.advance(61_000);
    const replay = await w.service.webhook(w.created.schedule.id, captured);
    expect(replay, 'a captured request fired a second run 61 s later').to.deep.equal({ firing: null, ignored: 'duplicate' });
    expect(w.logs.map((l) => l.event)).to.include('schedule.webhook_replayed');
  });

  it('padding, reordering or adding v1= values to the captured signature does not make it a new delivery', async () => {
    const w = await webhookWorld();
    const captured = w.sign(Buffer.from('{"issue":"PH-2"}'));
    expect(await w.fire(captured)).to.not.equal(null);
    const variants = [
      `${captured.signature},v1=${'0'.repeat(64)}`,
      `v1=${'f'.repeat(64)}, ${captured.signature}`,
      ` ${captured.signature} `,
      `${captured.signature},${captured.signature}`,
    ];
    for (const signature of variants) {
      w.clock.advance(61_000);
      expect(await w.fire({ ...captured, signature }), signature).to.equal(null);
    }
  });

  it('a timestamp re-encoded with leading zeros is still the same signed delivery or refused', async () => {
    const w = await webhookWorld();
    const captured = w.sign(Buffer.from('{"issue":"PH-3"}'));
    expect(await w.fire(captured)).to.not.equal(null);
    w.clock.advance(61_000);
    // The HMAC covers the timestamp's text, so a re-encoded one no longer verifies.
    expect(await w.fire({ ...captured, timestamp: `0${captured.timestamp}` })).to.equal(null);
  });

  it('replays fired concurrently start one run between them', async () => {
    const w = await webhookWorld();
    const captured = w.sign(Buffer.from('{"issue":"PH-4"}'));
    const firings = await Promise.all(Array.from({ length: 8 }, () => w.fire(captured)));
    expect(firings.filter((f) => f !== null)).to.have.length(1);
  });

  it('past the shortened skew window (120 s) a captured request is refused outright', async () => {
    const w = await webhookWorld();
    const captured = w.sign(Buffer.from('{"issue":"PH-5"}'));
    w.clock.advance(121_000);
    let refused: unknown = null;
    try {
      await w.service.webhook(w.created.schedule.id, captured);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessScheduleRefusedError);
    expect((refused as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
  });

  it("a sender's new delivery (a new timestamp) of the same body in a later window still fires", async () => {
    const w = await webhookWorld();
    const body = Buffer.from('{"issue":"PH-6"}');
    expect(await w.fire(w.sign(body))).to.not.equal(null);
    w.clock.advance(61_000);
    expect(await w.fire(w.sign(body))).to.not.equal(null);
  });

  it('a delivery ignored while the schedule was paused can fire once after it resumes, not twice', async () => {
    const w = await webhookWorld();
    const captured = w.sign(Buffer.from('{"issue":"PH-7"}'));
    const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
    await w.service.pause(alice, w.created.schedule.id);
    expect(await w.service.webhook(w.created.schedule.id, captured)).to.deep.equal({ firing: null, ignored: 'paused' });
    await w.service.resume(alice, w.created.schedule.id);
    expect(await w.fire(captured)).to.not.equal(null);
    w.clock.advance(61_000);
    expect(await w.fire(captured)).to.equal(null);
  });
});

/** The real internal router over in-memory stores, as gateway-token.http.test.ts builds it. */
async function internalWorld() {
  const clock = contractClock();
  const store = new InMemoryExecutionStore(clock);
  const h = createContractHarness(
    {
      executionStore: store, journal: store.journal, payloads: new InMemoryPayloadStore(), admissionStore: store,
      dispatches: async () => [], deletePayload: async () => undefined,
    },
    clock,
  );
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const stores = {
    workerCredential: new ConfigDocs<WorkerCredentialSet>(),
    attemptSigningKeys: new ConfigDocs<AttemptKeyring>(),
    attemptPublicKeys: new ConfigDocs<PublishedAttemptKeys>(),
    roleCredentials: {
      scrape: new ConfigDocs<WorkerCredentialSet>(),
      'egress-proxy': new ConfigDocs<WorkerCredentialSet>(),
      'sandbox-broker': new ConfigDocs<WorkerCredentialSet>(),
    },
  };
  const authority = createHarnessAuthority(stores, h.service, clock);
  await authority.provision(null);
  const credential = (await stores.workerCredential.read()).value!.current;
  // SEC-C-10: a gateway token is minted with the egress proxy's own credential.
  const proxyCredential = (await authority.credentialFor('egress-proxy'))!;
  const gateway = new GatewayTokenAuthority({ signingKeys: new ConfigDocs(), publishedKeys: new ConfigDocs(), clock });
  const container = new Container();
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
  container.bind(AGENT_HARNESS_GATEWAY_TOKENS).toConstantValue(new GatewayTokenService({
    tokens: gateway, runs: h.service, clock, bindingKeys: testBindingKeys,
    refusals: new GatewayRefusalJournal(store.journal, { ...validatingPreparer, resolve: async (events) => events }, async (work) => work(undefined)) as GatewayRefusalJournal<unknown>,
  }));
  const app = express();
  app.use(express.json());
  app.use('/internal', createAgentHarnessInternalRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  const claim = async (bearer: string) => {
    const admitted = await h.admit();
    return fetch(`${base}/internal/runs/${admitted.run.runId}/claim`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${bearer}` },
      body: JSON.stringify({ orgId: admitted.run.orgId, workerId: 'worker-a', leaseDurationMs: 60_000, workerCapabilities: CURRENT_WORKER_CAPABILITIES }),
    });
  };
  return {
    h, clock, authority, credential, proxyCredential, base, claim,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

describe("SEC-C-11: a gateway report needs the run's live attempt", function () {
  this.timeout(20_000);
  it('SEC-C-11: a gateway token whose attempt lost its lease cannot append refusals to the run', async () => {
    const w = await internalWorld();
    try {
      const run = ((await (await w.claim(w.credential)).json()) as { run: HarnessRun }).run;
      const issued = await fetch(`${w.base}/internal/runs/${run.runId}/gateway-token`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${w.proxyCredential}` },
        body: JSON.stringify({
          orgId: run.orgId, attemptId: run.attemptId, bindingProof: proofFor(run.orgId, run.runId, run.attemptId!),
        }),
      });
      const token = ((await issued.json()) as { token: string }).token;
      w.clock.set(w.clock.now() + 61_000); // the attempt's lease is gone: the model grant now answers 409
      const report = await fetch(`${w.base}/internal/runs/${run.runId}/refusals`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({ gateway: 'model', reason: 'unsupported_parameter', status: 400, param: 'tools.type' }),
      });
      expect(report.status, 'a stale attempt appended to the run journal').to.be.oneOf([401, 409]);
    } finally {
      await w.close();
    }
  });
});

describe('SEC-C-10: the scrape credential cannot claim runs', function () {
  this.timeout(20_000);
  it("the credential Node writes for Prometheus (app.ts startMetricsCredentialFile) is refused at /runs/claim", async () => {
    const w = await internalWorld();
    try {
      const scrape = await w.authority.credentialFor('scrape');
      expect(scrape, 'a scrape credential is provisioned').to.be.a('string');
      expect(scrape).to.not.equal(await w.authority.workerCredential());
      const claimed = await w.claim(scrape!);
      expect(claimed.status, "the scraper's credential claimed a run and its attempt token").to.equal(401);
    } finally {
      await w.close();
    }
  });

  it('bypass: the scraper and the egress proxy are refused on every route that claims or reads a run', async () => {
    const w = await internalWorld();
    try {
      const routes: Array<[string, string]> = [
        ['POST', '/internal/runs/claim'],
        ['POST', '/internal/runs/r-1/claim'],
        ['GET', '/internal/runs/r-1/audit'],
      ];
      // The live-layer route is not mounted in this world; the C-26 table holds it to the worker role.
      for (const role of ['scrape', 'egress-proxy', 'sandbox-broker'] as const) {
        const credential = (await w.authority.credentialFor(role))!;
        for (const [method, path] of routes) {
          const reply = await fetch(`${w.base}${path}`, {
            method,
            headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
            ...(method === 'POST' ? { body: '{}' } : {}),
          });
          expect(reply.status, `${role} on ${method} ${path}`).to.equal(401);
        }
      }
    } finally {
      await w.close();
    }
  });

  it('bypass: the worker and the scraper cannot mint a gateway token; only the egress proxy reaches the route', async () => {
    const w = await internalWorld();
    try {
      const mint = (credential: string) =>
        fetch(`${w.base}/internal/runs/r-unknown/gateway-token`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
          body: JSON.stringify({ attemptId: 'a-1', fence: 1 }),
        });
      expect((await mint((await w.authority.workerCredential())!)).status).to.equal(401);
      expect((await mint((await w.authority.credentialFor('scrape'))!)).status).to.equal(401);
      expect((await mint((await w.authority.credentialFor('egress-proxy'))!)).status).to.not.equal(401);
    } finally {
      await w.close();
    }
  });

  it('bypass: the scheme spelling does not change the role; a scrape credential in any case is still refused', async () => {
    const w = await internalWorld();
    try {
      const scrape = (await w.authority.credentialFor('scrape'))!;
      const admitted = await w.h.admit();
      for (const header of [`bearer ${scrape}`, `BEARER   ${scrape}`, `Bearer ${scrape} `, `Bearer ${scrape.toUpperCase()}`, `Basic ${scrape}`]) {
        const reply = await fetch(`${w.base}/internal/runs/${admitted.run.runId}/claim`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: header },
          body: JSON.stringify({ orgId: admitted.run.orgId, workerId: 'worker-a', leaseDurationMs: 60_000, workerCapabilities: CURRENT_WORKER_CAPABILITIES }),
        });
        expect(reply.status, header.split(' ')[0]).to.equal(401);
      }
      const worker = (await w.authority.workerCredential())!;
      expect((await w.claim(worker)).status, 'the worker still claims').to.equal(200);
    } finally {
      await w.close();
    }
  });

  it('bypass: a rotated role credential works through its grace window only, and never becomes another role', async () => {
    const w = await internalWorld();
    try {
      const before = (await w.authority.credentialFor('scrape'))!;
      const workerBefore = (await w.authority.workerCredential())!;
      await w.authority.rotateWorkerCredential();
      const after = (await w.authority.credentialFor('scrape'))!;
      expect(after).to.not.equal(before);
      expect(await w.authority.verifyRole(['scrape'], before)).to.equal('scrape');
      expect((await w.claim(before)).status, 'the old scrape credential claims').to.equal(401);
      expect((await w.claim(workerBefore)).status, 'the previous worker credential claims in its grace').to.equal(200);
      w.clock.set(w.clock.now() + PREVIOUS_WORKER_CREDENTIAL_GRACE_MS + 1);
      let refused = false;
      try {
        await w.authority.verifyRole(['scrape'], before);
      } catch (error) {
        refused = error instanceof HarnessUnauthorizedError;
      }
      expect(refused, 'the previous scrape credential after its grace').to.equal(true);
      expect((await w.claim(workerBefore)).status, 'the previous worker credential after its grace').to.equal(401);
    } finally {
      await w.close();
    }
  });
});

describe('SEC-C-08: an upload of undeclared length is not buffered unmetered', () => {
  // Fixed in R38-INPUT by metering streamed bytes rather than refusing chunked uploads (411): a chunked upload
  // within the cap is stored, and one past it is refused as it streams (metered-upload.test.ts, with a small cap).
  it('a chunked multipart upload within the cap is counted as it streams and stored', async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: { user?: unknown }, _res: unknown, next: () => void) => {
        req.user = { orgId: 'org-1', userId: 'u-1' };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({ assertOwner: async () => undefined });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    let reached = 0;
    container.bind(AGENT_HARNESS_SESSION_WORKSPACES).toConstantValue({
      upload: async () => {
        reached += 1;
        return { workspaceId: 'ws:s1', revision: 3, replayed: false, paths: [], replaced: [] };
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    const { base, close } = await serve(app);
    try {
      const boundary = 'secc08';
      const part = (name: string, value: string) =>
        `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`;
      const body =
        part('baseRevision', '2') + part('idempotencyKey', 'k-1') + part('paths', '["a.txt"]') +
        `--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\nhello\r\n--${boundary}--\r\n`;
      const url = new URL(`${base}/api/v1/conversations/s1/workspace/files`);
      const status = await new Promise<number>((resolve, reject) => {
        const req = http.request(
          { hostname: url.hostname, port: url.port, path: url.pathname, method: 'POST',
            headers: { 'content-type': `multipart/form-data; boundary=${boundary}`, 'transfer-encoding': 'chunked' } },
          (res) => {
            res.resume();
            res.on('end', () => resolve(res.statusCode ?? 0));
          },
        );
        req.on('error', reject);
        // Two chunks: the 512 MiB request bound reads only Content-Length, which a chunked body does not send.
        req.write(body.slice(0, 40));
        req.end(body.slice(40));
      });
      expect({ status, reached }, 'a chunked upload within the cap is stored').to.deep.equal({ status: 200, reached: 1 });
    } finally {
      await close();
    }
  });
});

describe("SEC-C-12: another person's conversation answers nothing about its runs", () => {
  afterEach(() => sinon.restore());
  it("SEC-C-12: a same-org stranger's message to a gated conversation is not told it holds a gated question", async () => {
    const { conversation } = await persistedRun('chat_answer_with_citations');
    // The owner is USER; `otherUser` is no participant (sec-c12-gated-question-oracle.test.ts covers the rest).
    const roles = { roleOf: async (scope: { actorId: string }) => (scope.actorId === USER ? ('owner' as const) : null) };
    const http = await startChatTurnHarness({ conversation, enabled: false, roles });
    try {
      sinon.stub(http.store, 'answeredRun').resolves({ runId: 'run-asked', gates: { dryRun: true as const } });
      const reply = await http.post(
        '/conversations/c-1/messages/stream',
        'otherUser',
        { query: 'hello', chatMode: 'internal_search' },
        { [SURFACE_HEADER]: 'mcp' },
      );
      expect(await reply.text(), 'the gated-question check ran before ownership').to.not.contain('GATES_NEED_RUNTIME');
    } finally {
      await http.close();
    }
  });
});

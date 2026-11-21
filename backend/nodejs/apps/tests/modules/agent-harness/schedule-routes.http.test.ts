/**
 * P8a RT: the schedule and trigger routers over the real service, firer and
 * in-memory store, with the platform's validation and error middleware.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { ScheduleService } from '../../../src/modules/agent-harness/application/schedule-service';
import { AGENT_HARNESS_SCHEDULES } from '../../../src/modules/agent-harness/composition';
import {
  createScheduleRouter,
  createTriggerRouter,
  SCHEDULES_PREFIX,
  TRIGGERS_PREFIX,
  triggerRawBody,
} from '../../../src/modules/agent-harness/transport/schedule-routes';
import {
  ADMIN_USER,
  ALICE,
  BOB,
  FakeActors,
  FakeAdmission,
  FakeSetting,
  FakeTriggerSettings,
  ORG,
  TestClock,
} from './support/schedules';
import { webhookSignature } from '../../../src/modules/agent-harness/domain/schedules';
import { testControlPlane } from '../control-plane/support/in-memory';

const BODY = {
  name: 'Weekly support digest',
  agentKey: 'support-agent',
  input: { query: 'Summarise new support tickets to #support-leads' },
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' },
};

describe('P8a schedule routes (RT)', () => {
  let server: Server;
  let base: string;
  let caller: Record<string, unknown>;
  let setting: FakeSetting;
  let admission: FakeAdmission;
  let triggers: FakeTriggerSettings;
  let clock: TestClock;

  beforeEach(async () => {
    caller = { userId: ALICE, orgId: ORG };
    clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    triggers = new FakeTriggerSettings();
    const store = new InMemoryScheduleStore(clock);
    const actors = new FakeActors();
    setting = new FakeSetting();
    admission = new FakeAdmission();
    const firer = new ScheduleFirer({ store, admission, actors, setting });
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: Record<string, unknown>, _res: unknown, next: () => void) => {
        req['user'] = { ...caller };
        next();
      },
    });
    container
      .bind(AGENT_HARNESS_SCHEDULES)
      .toConstantValue(new ScheduleService({
          transaction: testControlPlane(),
          store,
          firer,
          actors,
          setting,
          triggers,
          slackBots: { hasBot: async (agentKey) => agentKey === 'support-agent' },
          // Alice may run both agents; Bob neither (the agent-access check of the agent routes).
          agentVersions: {
            resolve: async (_orgId, actorId, agentKey) =>
              actorId === ALICE && ['support-agent', 'other-agent'].includes(agentKey) ? 1 : null,
          },
        }));
    const app = express();
    // As app.ts mounts them: the trigger's raw body before the JSON parser.
    app.use(TRIGGERS_PREFIX, triggerRawBody());
    app.use(express.json());
    app.use(SCHEDULES_PREFIX, createScheduleRouter(container));
    app.use(TRIGGERS_PREFIX, createTriggerRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    await new Promise((resolve) => server.once('listening', resolve));
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(() => {
    server.close();
  });

  const call = async (
    method: string,
    path: string,
    body?: unknown,
    headers: Record<string, string> = {},
  ) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { 'content-type': 'application/json', ...headers },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    return { status: response.status, json: text ? JSON.parse(text) : null };
  };

  it('RT-1: a valid body is 201; unknown fields, a bad cron and an over-long query are 400', async () => {
    const created = await call('POST', SCHEDULES_PREFIX, BODY);
    expect(created.status).to.equal(201);
    expect(created.json.schedule).to.include({ actorId: ALICE, status: 'active' });
    expect((await call('POST', SCHEDULES_PREFIX, { ...BODY, actorId: BOB })).status).to.equal(400);
    expect((await call('POST', SCHEDULES_PREFIX, { ...BODY, input: { query: 'x', recordIds: ['r'] } })).status).to.equal(400);
    expect((await call('POST', SCHEDULES_PREFIX, { ...BODY, input: { query: 'x'.repeat(10_001) } })).status).to.equal(400);
    const cron = await call('POST', SCHEDULES_PREFIX, { ...BODY, trigger: { kind: 'cron', cron: '* * * * *', timeZone: 'UTC' } });
    expect(cron.status).to.equal(400);
    expect(cron.json.error.code).to.equal('HTTP_AGENT_HARNESS_SCHEDULE_INVALID');
  });

  it('SYNCMAIN2: a token limited to some knowledge (main #4451) cannot schedule a run', async () => {
    caller = {
      userId: ALICE, orgId: ORG, isOAuth: true, oauthScopes: ['agent:execute'],
      knowledgeLimit: { apps: [], recordGroups: [], records: ['r-1'] },
    };
    const refused = await call('POST', SCHEDULES_PREFIX, BODY);
    expect([refused.status, refused.json.error.code]).to.deep.equal([403, 'HTTP_AGENT_HARNESS_SCHEDULE_FORBIDDEN']);
    caller = { userId: ALICE, orgId: ORG };
  });

  it('RT-2: refusals map to their status and code', async () => {
    const id = (await call('POST', SCHEDULES_PREFIX, BODY)).json.schedule.id as string;
    caller = { userId: BOB, orgId: ORG };
    const hidden = await call('GET', `${SCHEDULES_PREFIX}/${id}`);
    expect([hidden.status, hidden.json.error.code]).to.deep.equal([404, 'HTTP_AGENT_HARNESS_SCHEDULE_NOT_FOUND']);
    // An org admin is anyone else here: no reach is bound, so another person's schedule does not exist for them.
    caller = { userId: ADMIN_USER, orgId: ORG };
    const run = await call('POST', `${SCHEDULES_PREFIX}/${id}/run`, undefined, { 'idempotency-key': 'k1' });
    expect([run.status, run.json.error.code]).to.deep.equal([404, 'HTTP_AGENT_HARNESS_SCHEDULE_NOT_FOUND']);
    expect((await call('POST', `${SCHEDULES_PREFIX}/${id}/pause`)).status).to.equal(404);
    expect((await call('GET', `${SCHEDULES_PREFIX}?scope=org`)).status).to.equal(400);
    expect((await call('GET', `${SCHEDULES_PREFIX}?scope=mine`)).json.schedules).to.have.length(0);
    caller = { userId: ALICE, orgId: ORG };
    const budget = await call('POST', SCHEDULES_PREFIX, { ...BODY, maxCostUsd: 50 });
    expect([budget.status, budget.json.error.code]).to.deep.equal([400, 'HTTP_AGENT_HARNESS_SCHEDULE_BUDGET_EXCEEDS_CEILING']);
    setting.on = false;
    const off = await call('GET', SCHEDULES_PREFIX);
    expect([off.status, off.json.error.code]).to.deep.equal([404, 'HTTP_AGENT_HARNESS_SCHEDULED_RUNS_DISABLED']);
    expect((await call('DELETE', `${SCHEDULES_PREFIX}/${id}`)).status).to.equal(204);
  });

  it('RT-3: run now needs an Idempotency-Key; the same key answers the same firing', async () => {
    const id = (await call('POST', SCHEDULES_PREFIX, BODY)).json.schedule.id as string;
    expect((await call('POST', `${SCHEDULES_PREFIX}/${id}/run`)).status).to.equal(400);
    const first = await call('POST', `${SCHEDULES_PREFIX}/${id}/run`, undefined, { 'idempotency-key': 'k1' });
    const again = await call('POST', `${SCHEDULES_PREFIX}/${id}/run`, undefined, { 'idempotency-key': 'k1' });
    expect(first.status).to.equal(202);
    expect(again.json).to.deep.equal(first.json);
    expect(admission.requests).to.have.length(1);
    const firings = await call('GET', `${SCHEDULES_PREFIX}/${id}/firings`);
    expect(firings.json.firings.map((f: { id: string }) => f.id)).to.deep.equal([first.json.id]);
  });

  it('RT-2: the webhook answers 401 for a wrong token, 202 with a firing for the right one', async () => {
    const created = await call('POST', SCHEDULES_PREFIX, { ...BODY, trigger: { kind: 'webhook', auth: 'token' } });
    const id = created.json.schedule.id as string;
    const token = created.json.webhookToken as string;
    expect(token).to.match(/^pxt_/);
    // No credential at all answers as a wrong one (TR-04: one answer for every bad credential).
    expect((await call('POST', `${TRIGGERS_PREFIX}/${id}/webhook`, {})).status).to.equal(401);
    const wrong = await call('POST', `${TRIGGERS_PREFIX}/${id}/webhook`, {}, { 'x-pipeshub-trigger-token': 'nope' });
    expect([wrong.status, wrong.json.error.code]).to.deep.equal([401, 'HTTP_AGENT_HARNESS_SCHEDULE_TRIGGER_TOKEN_INVALID']);
    const missing = await call('POST', `${TRIGGERS_PREFIX}/missing/webhook`, {}, { 'x-pipeshub-trigger-token': token });
    expect(missing.status).to.equal(401);
    const fired = await call('POST', `${TRIGGERS_PREFIX}/${id}/webhook`, { anything: 'ignored' }, { 'x-pipeshub-trigger-token': token });
    expect(fired.status).to.equal(202);
    expect(fired.json.firing).to.include({ kind: 'event', status: 'admitted' });
  });

  it('RT-5 (FB-1): a refused webhook does not log its trigger secret (the error middleware drops secret headers)', async () => {
    const logged: unknown[] = [];
    // The error middleware logs through the logger it captured at class load: other files in this mocha worker may
    // reset the Logger singleton or stub the instance's methods, so swap the methods on that very logger, then put
    // back what was there.
    const logger = (ErrorMiddleware as unknown as { logger: Record<string, unknown> }).logger;
    const levels = ['error', 'warn', 'info'] as const;
    const saved = levels.map((level) => Object.getOwnPropertyDescriptor(logger, level));
    for (const level of levels) {
      logger[level] = (...args: unknown[]) => void logged.push(args);
    }
    try {
      const refused = await call('POST', `${TRIGGERS_PREFIX}/missing/webhook`, {}, {
        'x-pipeshub-trigger-token': 'pxt_must_not_be_logged',
        'x-api-key': 'key_must_not_be_logged',
      });
      expect(refused.status).to.equal(401);
    } finally {
      levels.forEach((level, i) => {
        const descriptor = saved[i];
        if (descriptor === undefined) delete logger[level];
        else Object.defineProperty(logger, level, descriptor);
      });
    }
    expect(logged.length).to.be.greaterThan(0);
    const text = JSON.stringify(logged);
    expect(text).to.not.include('pxt_must_not_be_logged');
    expect(text).to.not.include('key_must_not_be_logged');
  });

  const raw = async (path: string, body: string, headers: Record<string, string>) => {
    const response = await fetch(`${base}${path}`, { method: 'POST', headers, body });
    const text = await response.text();
    return { status: response.status, json: text ? JSON.parse(text) : null, retryAfter: response.headers.get('retry-after') };
  };

  it('RT-6 (TR-04): a signed webhook is verified over the raw bytes sent, whatever their type', async () => {
    const created = await call('POST', SCHEDULES_PREFIX, { ...BODY, trigger: { kind: 'webhook' } });
    const id = created.json.schedule.id as string;
    const secret = created.json.webhookSecret as string;
    expect(secret).to.match(/^pxs_/);
    // Spacing a JSON parser would drop is part of what was signed.
    const body = '{ "issue" :  "PH-1" }';
    const timestamp = String(Math.floor(clock.value / 1000));
    const signature = webhookSignature(secret, timestamp, Buffer.from(body));
    const headers = { 'content-type': 'application/json', 'x-pipeshub-timestamp': timestamp, 'x-pipeshub-signature': signature };
    const fired = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, body, headers);
    expect([fired.status, fired.json.firing?.status]).to.deep.equal([202, 'admitted']);
    expect(admission.requests[0]?.input.trigger?.webhook).to.deep.include({ bodyBytes: body.length, contentType: 'application/json' });
    const reparsed = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, JSON.stringify(JSON.parse(body)), headers);
    expect([reparsed.status, reparsed.json.error.code]).to.deep.equal([401, 'HTTP_AGENT_HARNESS_SCHEDULE_TRIGGER_TOKEN_INVALID']);
    const text = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, 'plain', {
      'content-type': 'text/plain',
      'x-pipeshub-timestamp': timestamp,
      'x-pipeshub-signature': webhookSignature(secret, timestamp, Buffer.from('plain')),
    });
    expect(text.status).to.equal(202);
    // SEC-C-13: the sender's retry of the same signed delivery is acknowledged and starts nothing.
    const again = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, body, headers);
    expect([again.status, again.json]).to.deep.equal([202, { firing: null, ignored: 'duplicate' }]);
    expect(admission.requests).to.have.length(1);
  });

  it('RT-7 (TR-04): an over-large body is 413 (the release cap at the parser, the org cap after); too many calls are 429 with Retry-After', async () => {
    const created = await call('POST', SCHEDULES_PREFIX, { ...BODY, trigger: { kind: 'webhook', auth: 'token' } });
    const id = created.json.schedule.id as string;
    const headers = { 'content-type': 'application/json', 'x-pipeshub-trigger-token': created.json.webhookToken as string };
    const huge = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, JSON.stringify({ a: 'x'.repeat(300_000) }), headers);
    expect([huge.status, huge.json.error.code]).to.deep.equal([413, 'HTTP_AGENT_HARNESS_TRIGGER_PAYLOAD_TOO_LARGE']);
    triggers.maxBytes = 10;
    const capped = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, JSON.stringify({ a: 'x'.repeat(20) }), headers);
    expect([capped.status, capped.json.error.code]).to.deep.equal([413, 'HTTP_AGENT_HARNESS_TRIGGER_PAYLOAD_TOO_LARGE']);
    triggers.maxBytes = 262_144;
    triggers.perMinute = 1;
    expect((await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, '{}', headers)).status).to.equal(202);
    const limited = await raw(`${TRIGGERS_PREFIX}/${id}/webhook`, '{}', headers);
    expect([limited.status, limited.json.error.code, limited.retryAfter]).to.deep.equal([429, 'HTTP_AGENT_HARNESS_TRIGGER_RATE_LIMITED', '60']);
  });

  it('RT-8 (TR-07): the notice channels the form offers: what the org allows, Slack only for an agent with its own bot the caller may run', async () => {
    const choice = async (query: string) =>
      (await call('GET', `${SCHEDULES_PREFIX}/notify-channels${query}`)).json.channels as Array<Record<string, unknown>>;
    expect(await choice('?agentKey=support-agent')).to.deep.equal([
      { channel: 'in_app', available: true, reason: null },
      { channel: 'email', available: true, reason: null },
      { channel: 'slack', available: true, reason: null },
    ]);
    expect((await choice('?agentKey=other-agent'))[2]).to.deep.equal({ channel: 'slack', available: false, reason: 'no_slack_bot' });
    expect((await choice(''))[2]).to.deep.equal({ channel: 'slack', available: false, reason: 'no_slack_bot' });
    triggers.channels = ['in_app', 'slack'];
    expect(await choice('?agentKey=support-agent')).to.deep.equal([
      { channel: 'in_app', available: true, reason: null },
      { channel: 'email', available: false, reason: 'org_off' },
      { channel: 'slack', available: true, reason: null },
    ]);
    expect((await call('GET', `${SCHEDULES_PREFIX}/notify-channels?agent=x`)).status).to.equal(400);
    // An agent the caller may not run, or that does not exist, is not found: whether it has a bot is not said.
    for (const query of ['?agentKey=no-such-agent', '?agentKey=support-agent']) {
      if (query.endsWith('support-agent')) caller = { userId: BOB, orgId: ORG };
      const hidden = await call('GET', `${SCHEDULES_PREFIX}/notify-channels${query}`);
      expect([hidden.status, hidden.json.error.code]).to.deep.equal([404, 'HTTP_AGENT_HARNESS_SCHEDULE_NOT_FOUND']);
    }
    caller = { userId: ALICE, orgId: ORG };
    setting.on = false;
    const off = await call('GET', `${SCHEDULES_PREFIX}/notify-channels`);
    expect([off.status, off.json.error.code]).to.deep.equal([404, 'HTTP_AGENT_HARNESS_SCHEDULED_RUNS_DISABLED']);
  });
});

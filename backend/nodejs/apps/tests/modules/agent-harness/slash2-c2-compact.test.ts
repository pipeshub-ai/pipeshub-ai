/**
 * SLASH2 C2 `/compact [focus]`, Node's half.
 *
 * - The route: `POST …/conversations/:sessionId/compact` (and the agent form)
 *   takes the session from the path and the org and actor from the verified
 *   credential (a body naming anything but the focus is refused), needs the
 *   execute scope, keeps only a focus of at most 500 characters, and answers 202 with what was saved; a conversation the caller
 *   may not reach is 404; one no harness run was ever admitted in is 409
 *   `NOTHING_TO_COMPACT`.
 * - The request is the session owner's, saved for the next run.
 * - Conformance on the loop (L), opencode (O), Codex (X) and Claude Code (C):
 *   the next run carries the request in its input and requires a worker that
 *   honours it, its pin is the one it would have without it (executor and
 *   model unchanged), and a user who types `/compact` sends text: the query is
 *   unchanged and no request is made. The workers' halves (the compaction and
 *   `x.pipeshub.context.compacted`) are `test_slash2_c2_compact.py` and the
 *   engine contract's cf19; the store's half is the execution-store contract.
 * - The timeline: `x.pipeshub.context.compacted` is the chat's saved row, a
 *   `context_compacted` part stored with the answer (or the failure).
 */
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessConflictError, HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun, JournalEvent, RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildParts, failureParts } from '../../../src/modules/agent-harness/projection/transcript';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';

const ORG = 'org-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

describe('SLASH2 C2: the /compact route', () => {
  const tokens: Record<string, Record<string, unknown>> = {
    owner: { orgId: ORG, userId: 'user-1' },
    chatter: { orgId: ORG, userId: 'user-1', isOAuth: true, oauthScopes: ['conversation:chat', 'agent:execute'] },
    reader: { orgId: ORG, userId: 'user-1', isOAuth: true, oauthScopes: ['conversation:read', 'agent:read'] },
    stranger: { orgId: ORG, userId: 'stranger' },
    early: { orgId: ORG, userId: 'early' },
  };

  it("saves the focus for the caller's own conversation, with the credential's org and actor; refuses as its buttons do", async () => {
    const requestCompact = sinon.stub().callsFake(async (scope: { actorId: string }, focus?: string) => {
      if (scope.actorId === 'stranger') throw new HarnessNotFoundError();
      if (scope.actorId === 'early') throw new HarnessConflictError('NOTHING_TO_COMPACT', 'nothing yet');
      return { requestedAt: 5, requestedBy: 'p', ...(focus ? { focus } : {}) };
    });
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = tokens[String(req.headers['x-test-token'])];
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({ requestCompact });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    const originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    const server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
    const post = (path: string, token: string, body: unknown) =>
      fetch(`${base}${path}?orgId=org-2&userId=user-2`, {
        method: 'POST',
        headers: { 'x-test-token': token, 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    try {
      for (const path of ['/conversations/s1/compact', '/agents/agent-1/conversations/s1/compact']) {
        for (const token of ['owner', 'chatter']) {
          const reply = await post(path, token, { focus: 'keep the numbers' });
          expect(reply.status, `${path} ${token}`).to.equal(202);
          expect(await reply.json()).to.deep.equal({ requestedAt: 5, focus: 'keep the numbers' });
        }
        const bare = await post(path, 'owner', {});
        expect(await bare.json()).to.deep.equal({ requestedAt: 5 });
        expect((await post(path, 'owner', { focus: 'x'.repeat(501) })).status, `${path} long`).to.equal(400);
        expect((await post(path, 'owner', { orgId: 'org-2' })).status, `${path} org in body`).to.equal(400);
        expect((await post(path, 'reader', {})).status, `${path} reader`).to.equal(403);
        expect((await post(path, 'stranger', {})).status, `${path} stranger`).to.equal(404);
        const early = await post(path, 'early', {});
        expect(early.status, `${path} early`).to.equal(409);
        expect(JSON.stringify(await early.json())).to.include('NOTHING_TO_COMPACT');
      }
    } finally {
      (ErrorMiddleware as any).logger = originalLogger;
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
    const scopes = requestCompact.args.map(([scope]) => scope);
    expect(scopes[0]).to.deep.equal({ orgId: ORG, actorId: 'user-1', sessionId: 's1' });
    expect(scopes.at(-1)).to.deep.equal({ orgId: ORG, actorId: 'early', sessionId: 's1', agentKey: 'agent-1' });
    expect(requestCompact.args.every(([, focus]) => focus === undefined || focus.length <= 500)).to.equal(true);
  });

  it("is the session owner's: a participant it is shared with does not reach it, and nothing is saved", async () => {
    const compacts = { requestCompact: sinon.stub().resolves(true) };
    const shared = new SessionRunService({} as never, { findStreamAccess: async () => ({ audience: 'shared' }) } as never, undefined, compacts);
    const refused = await shared
      .requestCompact({ orgId: ORG, actorId: 'user-2', sessionId: 's1' }, 'x')
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
    const nobody = new SessionRunService({} as never, { findStreamAccess: async () => null } as never, undefined, compacts);
    expect(await nobody.requestCompact({ orgId: ORG, actorId: 'x', sessionId: 's1' }, undefined).catch((e: unknown) => e)).to.be.instanceOf(
      HarnessNotFoundError,
    );
    expect(compacts.requestCompact.callCount).to.equal(0);
  });

  it('keeps a trimmed focus of at most 500 characters, and none when it is blank', async () => {
    const compacts = { requestCompact: sinon.stub().resolves(true) };
    const owner = new SessionRunService(
      {} as never,
      { findStreamAccess: async () => ({ audience: 'owner' }) } as never,
      undefined,
      compacts,
      () => 42,
    );
    const scope = { orgId: ORG, actorId: 'user-1', sessionId: 's1' };
    expect(await owner.requestCompact(scope, `  ${'y'.repeat(600)}  `)).to.deep.equal({
      focus: 'y'.repeat(500),
      requestedAt: 42,
      requestedBy: 'p_user-1',
    });
    expect(await owner.requestCompact(scope, '   ')).to.deep.equal({ requestedAt: 42, requestedBy: 'p_user-1' });
    compacts.requestCompact.resolves(false);
    const refused = await owner.requestCompact(scope, undefined).catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('NOTHING_TO_COMPACT');
  });
});

const engine = (kind: string) => ({
  kind: 'engine' as const,
  engine: {
    kind,
    version: '1.0.0',
    binaryDigest: `sha256:${'a'.repeat(64)}`,
    profileDigest: `sha256:${'b'.repeat(64)}`,
  },
});

const EXECUTORS = [
  ['L (durable loop)', { kind: 'loop' as const }],
  ['O (opencode)', engine('opencode')],
  ['X (codex)', engine('codex')],
  ['C (claude_code)', engine('claude_code')],
] as const;

for (const [name, executor] of EXECUTORS) {
  describe(`SLASH2 C2 conformance on ${name}`, () => {
    let now: number;
    let store: InMemoryExecutionStore;
    let service: ExecutionService;
    let runs: SessionRunService;

    const admit = async (query: string, sessionId?: string): Promise<HarnessRun> => {
      const input: RunInputSnapshot = { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', modelKey: 'model-1', reasoningEffort: 'low' } };
      const outcome = await new RunAdmissionService({
        store,
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => now },
        agentAccess: grantingAccess(),
      }).submit({
        orgId: ORG,
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: `k-${String((now += 1))}`,
        deadlineMs: 60_000,
        ...(sessionId !== undefined ? { sessionId } : {}),
        input,
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable run');
      return outcome.admission.run;
    };

    const claim = (run: HarnessRun) =>
      service.claim({ orgId: run.orgId, runId: run.runId, workerId: 'w', attemptId: `attempt-${run.runId}`, leaseDurationMs: 60_000 });

    const finish = async (run: HarnessRun, claimed?: Awaited<ReturnType<typeof claim>>) => {
      claimed ??= await claim(run);
      await service.commit({
        commandId: `worker:completed:${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w',
        attemptId: `attempt-${run.runId}`,
        fence: claimed.run.fence,
        events: [],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });
      return claimed.pinned;
    };

    beforeEach(() => {
      now = 1_000;
      store = new InMemoryExecutionStore({ now: () => now });
      service = new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(() => executor),
      });
      runs = new SessionRunService(service, { findStreamAccess: async () => ({ audience: 'owner' }) } as never, undefined, store);
    });

    it('/compact rides on the next run with the pin unchanged; a typed /compact is text and requests nothing', async () => {
      const first = await admit('first question');
      const before = await finish(first);
      const scope = { orgId: ORG, actorId: 'user-1', sessionId: first.sessionId, agentKey: 'agent-1' };
      await runs.requestCompact(scope, 'keep the numbers');

      const next = await admit('second question', first.sessionId);
      const claimed = await claim(next);
      expect(claimed.run.input?.compact).to.deep.equal({ focus: 'keep the numbers' });
      expect(claimed.run.input?.query).to.equal('second question');
      expect(claimed.run.requires?.features).to.include('compact_request');
      expect(claimed.pinned?.spec.executor).to.deep.equal(executor);
      expect(claimed.pinned?.spec.executor).to.deep.equal(before?.spec.executor);
      expect(claimed.pinned?.spec.model).to.deep.equal(before?.spec.model);
      await finish(next, claimed);

      const typed = await admit('/compact keep the numbers', first.sessionId);
      const typedClaim = await claim(typed);
      expect(typedClaim.run.input?.query).to.equal('/compact keep the numbers');
      expect(typedClaim.run.input?.compact).to.equal(undefined);
      expect(typedClaim.run.requires?.features ?? []).to.not.include('compact_request');
    });
  });
}

describe('SLASH2 C2: the saved row in the run timeline', () => {
  let seq = 0;
  const event = (type: string, data: Record<string, unknown>): JournalEvent =>
    ({
      orgId: ORG,
      sessionId: 's1',
      streamId: 's1',
      seq: (seq += 1),
      runId: 'run-2',
      attemptId: 'run-2.a1',
      fence: 1,
      type,
      schemaVersion: 1,
      critical: !type.startsWith('x.'),
      ts: '2026-10-18T12:00:00.000Z',
      writer: 'worker',
      visibility: 'owner',
      data,
    }) as unknown as JournalEvent;

  it('shows what the compaction did, saved with the answer, and ignores an outcome it does not know', () => {
    const compacted = event('x.pipeshub.context.compacted', {
      trigger: 'user',
      executor: 'codex',
      outcome: 'compacted',
      beforeTokens: 148_000,
      afterTokens: null,
      userMessagesKept: true,
      focus: 'keep the numbers',
    });
    expect(foldTimeline([compacted], 'run-2').compacted).to.deep.equal({
      executor: 'codex',
      outcome: 'compacted',
      beforeTokens: 148_000,
      afterTokens: null,
      userMessagesKept: true,
      focus: 'keep the numbers',
    });
    const saved = { type: 'context_compacted', compacted: foldTimeline([compacted], 'run-2').compacted, runId: 'run-2' };
    expect(buildParts(foldTimeline([compacted], 'run-2'))).to.deep.include(saved);
    expect(failureParts(foldTimeline([compacted], 'run-2'))).to.deep.include(saved);
    const unknown = event('x.pipeshub.context.compacted', { executor: 'loop', outcome: 'partial' });
    expect(foldTimeline([unknown], 'run-2').compacted).to.equal(undefined);
    expect(foldTimeline([event('turn.started', { turn: 1 })], 'run-2').compacted).to.equal(undefined);
  });
});

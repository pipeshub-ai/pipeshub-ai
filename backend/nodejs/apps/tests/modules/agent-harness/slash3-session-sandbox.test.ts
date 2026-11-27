/**
 * SLASH3 SB1 `/sandbox` (alias `/env`), Node's half: what runs the chat's code,
 * composed from what Node holds without starting a sandbox (the run_code image
 * and network from the variables the worker reads, the approval rules ASKCODE
 * writes, the workspace head, a kept Claude sign-in), and "End now" through the
 * per-session sign-in revocation. Owner only; org and user from the credential.
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
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import {
  DEFAULT_SANDBOX_IMAGE,
  RUN_CODE_TOOL_ID,
  SessionSandboxService,
} from '../../../src/modules/agent-harness/application/session-sandbox-service';
import { EngineSignInService } from '../../../src/modules/agent-harness/application/engine-signin-service';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_SESSION_SANDBOX,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { ORG, OWNER, scope, workspaceWorld } from './support/slash2-workspace-world';

type Rules = { tools?: Record<string, string> } | undefined;

function sandboxWorld(options: { env?: Record<string, string>; org?: Rules; agent?: Rules; deployment?: Rules; claude?: string } = {}) {
  const w = workspaceWorld();
  const ended: Array<[string, string]> = [];
  const service = new SessionSandboxService({
    sessions: { findStreamAccess: async () => (w.audience.value === null ? null : { audience: w.audience.value }) },
    workspaces: w.workspaces,
    approvals: {
      read: async () => ({
        policy: { org: options.org ?? null, agents: options.agent ? { 'agent-1': options.agent } : {} },
        deployment: { default: options.deployment ?? null },
      }),
    },
    isolation: () => 'container_gvisor',
    signIns: {
      view: async () => ({
        claude: {
          state: options.claude ?? 'available',
          keep: { idleMinutes: 30, maxHours: 12 },
          signedInAt: '2026-10-19T09:40:00.000Z',
        },
      }),
      endSession: async (orgId, sessionId) => {
        ended.push([orgId, sessionId]);
      },
    },
    env: options.env ?? {},
  });
  return { w, service, ended };
}

describe('SLASH3 SB1: /sandbox (service)', () => {
  it('offline with no rule: code runs without asking; the image is the worker\'s default; the workspace head counted', async () => {
    const { w, service } = sandboxWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'abc', 'out/b.txt': 'de' });
    expect(await service.view(scope)).to.deep.equal({
      image: { name: DEFAULT_SANDBOX_IMAGE, isolation: 'container_gvisor' },
      network: false,
      codeRuns: 'allow',
      workspace: { revision: 1, files: 2, bytes: 5 },
      keptSignIn: null,
      codingEngine: null,
    });
  });

  it('the image comes from the variable the worker reads; a durable run\'s code never has network (its pin)', async () => {
    const view = await sandboxWorld({ env: { SANDBOX_DOCKER_IMAGE: 'acme/sandbox:7', SANDBOX_ALLOW_NETWORK: 'true' } }).service.view(scope);
    expect(view).to.deep.include({ network: false, codeRuns: 'allow' });
    expect(view.image.name).to.equal('acme/sandbox:7');
  });

  it('ASKCODE: the org\'s run_code rule asks; a Deny in any layer (org, agent, deployment) wins', async () => {
    expect((await sandboxWorld({ org: { tools: { [RUN_CODE_TOOL_ID]: 'ask' } } }).service.view(scope)).codeRuns).to.equal('ask');
    const agentScope = { ...scope, agentKey: 'agent-1' };
    expect(
      (await sandboxWorld({ org: { tools: { [RUN_CODE_TOOL_ID]: 'ask' } }, agent: { tools: { [RUN_CODE_TOOL_ID]: 'deny' } } }).service.view(agentScope)).codeRuns,
    ).to.equal('deny');
    expect((await sandboxWorld({ deployment: { tools: { [RUN_CODE_TOOL_ID]: 'deny' } } }).service.view(scope)).codeRuns).to.equal('deny');
    // Another agent's rule never applies to this chat.
    expect((await sandboxWorld({ agent: { tools: { [RUN_CODE_TOOL_ID]: 'deny' } } }).service.view(scope)).codeRuns).to.equal('allow');
  });

  it('a kept sign-in sandbox is shown only while the owner is signed in to Claude, with its idle and lifetime bounds', async () => {
    const { service } = sandboxWorld({ claude: 'connected' });
    expect((await service.view(scope)).keptSignIn).to.deep.equal({ idleMinutes: 30, maxHours: 12, signedInAt: '2026-10-19T09:40:00.000Z' });
  });

  it('End now revokes the session\'s sign-in; asking again is the same answer', async () => {
    const { service, ended } = sandboxWorld({ claude: 'connected' });
    await service.endKept(scope);
    await service.endKept(scope);
    expect(ended).to.deep.equal([[ORG, 's1'], [ORG, 's1']]);
  });

  it('only the owner: a shared viewer or a stranger gets 404 and ends nothing', async () => {
    const { w, service, ended } = sandboxWorld({ claude: 'connected' });
    for (const audience of ['shared', null] as const) {
      w.audience.value = audience;
      for (const call of [() => service.view(scope), () => service.endKept(scope)]) {
        let refused: unknown;
        try {
          await call();
        } catch (error) {
          refused = error;
        }
        expect(refused).to.be.instanceOf(HarnessNotFoundError);
      }
    }
    expect(ended).to.deep.equal([]);
  });

  it('EngineSignInService.endSession revokes the session scope through the relay', async () => {
    const revoked: unknown[] = [];
    const signIns = new EngineSignInService({ relay: { revoke: async (r: unknown) => void revoked.push(r) } } as never);
    await signIns.endSession(ORG, 's1');
    expect(revoked).to.deep.equal([{ orgId: ORG, sessionId: 's1', reason: 'user' }]);
  });
});

describe('SLASH3 SB1: GET …/sandbox and DELETE …/sandbox/kept', () => {
  let server: Server;
  let base: string;
  const calls: unknown[][] = [];
  let originalLogger: unknown;

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: OWNER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({
      assertOwner: async (s: { sessionId: string }) => {
        if (s.sessionId === 'not-mine') throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    const owner = (s: { sessionId: string }) => {
      if (s.sessionId === 'not-mine') throw new HarnessNotFoundError();
    };
    container.bind(AGENT_HARNESS_SESSION_SANDBOX).toConstantValue({
      view: async (s: { sessionId: string }) => {
        calls.push(['view', s]);
        owner(s);
        return { network: false, codeRuns: 'allow' };
      },
      endKept: async (s: { sessionId: string }) => {
        calls.push(['endKept', s]);
        owner(s);
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it('answers on chat and agent sessions with org and caller from the credential', async () => {
    const chat = await fetch(`${base}/conversations/s1/sandbox?orgId=org-2`);
    expect(chat.status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal(['view', { orgId: ORG, actorId: OWNER, sessionId: 's1' }]);
    const agent = await fetch(`${base}/agents/agent-1/conversations/s1/sandbox`);
    expect(agent.status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal(['view', { orgId: ORG, actorId: OWNER, sessionId: 's1', agentKey: 'agent-1' }]);
  });

  it('End now answers 204, again 204; not the owner: 404', async () => {
    for (let i = 0; i < 2; i += 1) {
      expect((await fetch(`${base}/conversations/s1/sandbox/kept`, { method: 'DELETE' })).status).to.equal(204);
    }
    expect((await fetch(`${base}/conversations/not-mine/sandbox/kept`, { method: 'DELETE' })).status).to.equal(404);
    expect((await fetch(`${base}/agents/agent-1/conversations/not-mine/sandbox`)).status).to.equal(404);
  });
});

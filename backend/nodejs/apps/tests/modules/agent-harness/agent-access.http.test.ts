/**
 * P-3: running an agent needs `agent:execute`, not `agent:read`. The access
 * policy stands in for Python's execute-scoped run-definition route: it grants
 * only to identities with access, as check_agent_permission would.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { AgentAccessPolicy } from '../../../src/modules/agent-harness/ports/agent-access';
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum';
import { fixedRollout } from './support/rollout';
import { agentDefinition } from './support/agent-definition';
import {
  admissionBody,
  AdmissionHttp,
  startAdmissionHttp,
} from './support/admission-http';

const ORG = 'org-auth';

describe('P-3: execute-scoped agent access over HTTP', () => {
  let http: AdmissionHttp;
  let store: InMemoryExecutionStore;
  let access: sinon.SinonStub;

  const identities = {
    'exec-pat': {
      orgId: ORG,
      userId: 'user-exec',
      isOAuth: true,
      oauthScopes: [OAuthScopeNames.AGENT_EXECUTE],
    },
    'read-pat': {
      orgId: ORG,
      userId: 'user-read',
      isOAuth: true,
      oauthScopes: [OAuthScopeNames.AGENT_READ],
    },
    'no-access-session': { orgId: ORG, userId: 'user-stranger' },
  };

  beforeEach(async () => {
    store = new InMemoryExecutionStore();
    access = sinon
      .stub()
      .callsFake(async (request) =>
        request.actorId === 'user-stranger'
          ? null
          : agentDefinition({ agentKey: request.agentKey }),
      );
    const policy: AgentAccessPolicy = { authorizeRun: access };
    http = await startAdmissionHttp(
      new RunAdmissionService({
        store,
        rollout: fixedRollout(),
        agentAccess: policy,
      }),
      identities,
    );
  });

  afterEach(async () => {
    await http.close();
  });

  it('admits an agent:execute-only PAT and forwards its own credential', async () => {
    const reply = await http.post(
      '/agents/agent-1/runs',
      'exec-pat',
      admissionBody(),
    );
    expect(reply.status).to.equal(202);
    expect(reply.body.admission.run.agentKey).to.equal('agent-1');
    expect(access.calledOnce).to.equal(true);
    expect(access.firstCall.args[0]).to.deep.equal({
      orgId: ORG,
      actorId: 'user-exec',
      agentKey: 'agent-1',
      authorization: 'Bearer exec-pat',
    });
    // The grant stays server-side: never echoed to the client.
    expect(JSON.stringify(reply.body)).not.to.contain('jira.search_issues');
  });

  it('refuses a read-only token before asking the agent authority', async () => {
    const reply = await http.post(
      '/agents/agent-1/runs',
      'read-pat',
      admissionBody(),
    );
    expect(reply.status).to.equal(403);
    expect(access.called).to.equal(false);
    expect(store.dispatches).to.have.length(0);
  });

  it('refuses a caller without access to the agent and writes nothing', async () => {
    const reply = await http.post(
      '/agents/agent-1/runs',
      'no-access-session',
      admissionBody(),
    );
    expect(reply.status).to.equal(403);
    expect(store.dispatches).to.have.length(0);
  });

  it('takes org and user from the token; a body naming them is refused', async () => {
    const spoofed = await http.post(
      '/agents/agent-1/runs',
      'exec-pat',
      admissionBody({ orgId: 'org-evil', actorId: 'user-evil' }),
    );
    expect(spoofed.status).to.equal(400);
    expect(access.called).to.equal(false);
    expect(store.dispatches).to.have.length(0);

    const reply = await http.post(
      '/agents/agent-1/runs',
      'exec-pat',
      admissionBody(),
    );
    const runId = reply.body.admission.run.runId as string;
    expect((await store.get(ORG, runId))?.actorId).to.equal('user-exec');
  });

  it('fails closed (503) when the agent authority cannot answer', async () => {
    access.rejects(new Error('query service down'));
    const reply = await http.post(
      '/agents/agent-1/runs',
      'exec-pat',
      admissionBody(),
    );
    expect(reply.status).to.equal(503);
    expect(store.dispatches).to.have.length(0);
  });
});

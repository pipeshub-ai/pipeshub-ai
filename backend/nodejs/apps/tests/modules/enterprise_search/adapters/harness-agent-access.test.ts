import { expect } from 'chai';
import sinon from 'sinon';
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command';
import { HarnessAgentAccessPolicy } from '../../../../src/modules/enterprise_search/adapters/harness-agent-access';
import { agentDefinition } from '../../agent-harness/support/agent-definition';

const request = {
  orgId: 'org-1',
  actorId: 'user-1',
  agentKey: 'agent/1',
  authorization: 'Bearer user-token',
};

describe('HarnessAgentAccessPolicy', () => {
  afterEach(() => sinon.restore());

  const respond = (statusCode: number, data: unknown = {}) =>
    sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode, data, msg: '' } as never);

  const rejection = async (work: Promise<unknown>): Promise<Error> => {
    try {
      await work;
    } catch (error) {
      return error as Error;
    }
    return expect.fail('Expected the check to reject');
  };

  it('asks the execute-scoped run definition with the caller credential', async () => {
    const definition = agentDefinition({ agentKey: 'agent/1' });
    const execute = respond(200, {
      definition: { ...definition, systemPromptLeak: 'dropped' },
    });
    const policy = new HarnessAgentAccessPolicy(() => 'http://query:8000');
    expect(await policy.authorizeRun(request)).to.deep.equal(definition);
    const command = execute.firstCall.thisValue as unknown as {
      uri: string;
      headers: Record<string, string>;
    };
    expect(command.uri).to.equal(
      'http://query:8000/api/v1/agent/agent%2F1/run-definition',
    );
    expect(command.headers.authorization).to.equal('Bearer user-token');
  });

  for (const status of [401, 403, 404]) {
    it(`denies on ${String(status)}`, async () => {
      respond(status);
      expect(
        await new HarnessAgentAccessPolicy(() => 'http://q').authorizeRun(
          request,
        ),
      ).to.equal(null);
    });
  }

  it('cannot decide on a server error', async () => {
    respond(502);
    const error = await rejection(
      new HarnessAgentAccessPolicy(() => 'http://q').authorizeRun(request),
    );
    expect(error.message).to.contain('502');
  });

  it('refuses a malformed definition instead of guessing the grant', async () => {
    respond(200, { definition: { agentKey: 'agent/1', toolsets: 'all' } });
    await rejection(
      new HarnessAgentAccessPolicy(() => 'http://q').authorizeRun(request),
    );
  });

  it('refuses a definition for another agent', async () => {
    respond(200, { definition: agentDefinition({ agentKey: 'other' }) });
    const error = await rejection(
      new HarnessAgentAccessPolicy(() => 'http://q').authorizeRun(request),
    );
    expect(error.message).to.contain('another agent');
  });

  it('OWD-14: asks for the version a schedule pinned', async () => {
    const definition = agentDefinition({ agentKey: 'agent/1', definitionVersion: 3 });
    const execute = respond(200, { definition });
    const policy = new HarnessAgentAccessPolicy(() => 'http://query:8000');
    expect(await policy.authorizeRun({ ...request, agentVersion: 3 })).to.deep.equal(definition);
    const command = execute.firstCall.thisValue as unknown as { uri: string };
    expect(command.uri).to.equal(
      'http://query:8000/api/v1/agent/agent%2F1/run-definition?version=3',
    );
  });

  it('OWD-14: a pinned version the authority no longer holds pins the version it answers instead', async () => {
    const latest = agentDefinition({ agentKey: 'agent/1', definitionVersion: 4 });
    respond(200, { definition: latest });
    expect(
      await new HarnessAgentAccessPolicy(() => 'http://q').authorizeRun({ ...request, agentVersion: 3 }),
    ).to.deep.equal(latest);
  });
});

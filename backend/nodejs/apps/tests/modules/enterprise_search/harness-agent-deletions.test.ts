/**
 * H5 AE-16: the agent-erasure sweeper asks the query service's internal
 * deletion route with a service token scoped exactly `agent:erase` for the
 * entry's org, and accepts only the route's three answers.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import {
  AgentDeletionLookupFailedError,
  HarnessAgentDeletionLookup,
} from '../../../src/modules/enterprise_search/adapters/harness-agent-deletions';

const config = {
  aiBackend: 'http://query:8000',
  jwtSecret: 'user-secret',
  scopedJwtSecret: 'scoped-secret',
};

describe('H5 HarnessAgentDeletionLookup', () => {
  afterEach(() => sinon.restore());

  const capture = (answer: { statusCode: number; data: unknown }) => {
    const calls: Array<Record<string, unknown>> = [];
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (
      this: AIServiceCommand<unknown>,
    ) {
      calls.push(this as unknown as Record<string, unknown>);
      return Promise.resolve(answer as never);
    });
    return calls;
  };

  for (const state of ['deleted', 'live', 'absent'] as const) {
    it(`AE-16: answers ${state} from an org-scoped agent:erase GET`, async () => {
      const calls = capture({ statusCode: 200, data: { state, extra: 'ignored' } });
      expect(await new HarnessAgentDeletionLookup(() => config).state('org-1', 'agent x/1')).to.equal(state);
      const call = calls[0] as unknown as { uri: string; method: string; body?: unknown; headers: Record<string, string> };
      expect(call.uri).to.equal('http://query:8000/api/v1/agent/agent%20x%2F1/internal/deletion');
      expect(call.method).to.equal('GET');
      const authorization = Object.entries(call.headers).find(([name]) => name.toLowerCase() === 'authorization')![1];
      const claims = jwt.verify(authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, unknown>;
      expect(claims['orgId']).to.equal('org-1');
      expect(claims['scopes']).to.deep.equal(['agent:erase']);
      expect(claims).to.not.have.property('userId');
    });
  }

  for (const answer of [
    { statusCode: 503, data: {} },
    { statusCode: 403, data: { detail: 'no' } },
    { statusCode: 200, data: { state: 'gone' } },
    { statusCode: 200, data: {} },
    { statusCode: 200, data: null },
  ]) {
    it(`AE-16: ${answer.statusCode} ${JSON.stringify(answer.data)} is a failure, never a state`, async () => {
      capture(answer);
      let failed: unknown;
      try {
        await new HarnessAgentDeletionLookup(() => config).state('org-1', 'agent-x');
      } catch (error) {
        failed = error;
      }
      expect(failed).to.be.instanceOf(AgentDeletionLookupFailedError);
      expect((failed as AgentDeletionLookupFailedError).statusCode).to.equal(answer.statusCode);
    });
  }
});

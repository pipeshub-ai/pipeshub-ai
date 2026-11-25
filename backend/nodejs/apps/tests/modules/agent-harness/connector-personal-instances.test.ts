/**
 * C-14 (SYNCFIX1): a deleted user's or org's personal toolset and MCP
 * instances are erased through the connectors service, with a service token
 * scoped `principal:erase` for the deleted org; the body names only the user.
 * Anything but a full erasure throws, so the deletion stops and is retried.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import {
  ConnectorPersonalInstanceEraser,
  PersonalInstanceErasureFailedError,
} from '../../../src/modules/agent-harness/adapters/connector-personal-instances';

const config = {
  connectorBackend: 'http://connectors:8088',
  jwtSecret: 'user-secret',
  scopedJwtSecret: 'scoped-secret',
};

type Call = { uri: string; body: string; headers: Record<string, string> };

describe('SYNCFIX1 ConnectorPersonalInstanceEraser', () => {
  afterEach(() => sinon.restore());

  const capture = (answer: { statusCode: number; data: unknown }): Call[] => {
    const calls: Call[] = [];
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: AIServiceCommand<unknown>) {
      calls.push(this as unknown as Call);
      return Promise.resolve(answer as never);
    });
    return calls;
  };

  const claimsOf = (call: Call): Record<string, unknown> => {
    const authorization = Object.entries(call.headers).find(([name]) => name.toLowerCase() === 'authorization')![1];
    return jwt.verify(authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, unknown>;
  };

  it("erases one user's with a principal:erase token for their org; the body names only the user", async () => {
    const calls = capture({ statusCode: 200, data: { toolsetInstances: 2, mcpKeys: 3 } });
    expect(await new ConnectorPersonalInstanceEraser(() => config).eraseUser('org-1', 'u-1')).to.equal(2);
    expect(calls[0]!.uri).to.equal('http://connectors:8088/api/v1/entity/internal/personal-instances/erase');
    expect(JSON.parse(calls[0]!.body)).to.deep.equal({ userId: 'u-1' });
    const claims = claimsOf(calls[0]!);
    expect([claims['orgId'], claims['scopes']]).to.deep.equal(['org-1', ['principal:erase']]);
    expect(claims).to.not.have.property('userId');
  });

  it("erases every member's of a deleted org with an empty body", async () => {
    const calls = capture({ statusCode: 200, data: { toolsetInstances: 5, mcpKeys: 0 } });
    expect(await new ConnectorPersonalInstanceEraser(() => config).eraseOrg('org-1')).to.equal(5);
    expect(JSON.parse(calls[0]!.body)).to.deep.equal({});
    expect(claimsOf(calls[0]!)['orgId']).to.equal('org-1');
  });

  for (const answer of [{ statusCode: 503, data: { detail: 'try again' } }, { statusCode: 200, data: { erased: true } }]) {
    it(`throws on ${String(answer.statusCode)} ${JSON.stringify(answer.data)}, so the deletion stops and is retried`, async () => {
      capture(answer);
      let failure: unknown;
      try {
        await new ConnectorPersonalInstanceEraser(() => config).eraseUser('org-1', 'u-1');
      } catch (error) {
        failure = error;
      }
      expect(failure).to.be.instanceOf(PersonalInstanceErasureFailedError);
    });
  }
});

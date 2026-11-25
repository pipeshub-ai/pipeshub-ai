/**
 * SS3: the artifact copier calls the query service's copy route with a
 * service token scoped `conversation:fork` carrying the forker's org and user;
 * the body names only the conversations and the versions.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import {
  ArtifactCopyFailedError,
  HarnessArtifactCopier,
} from '../../../src/modules/enterprise_search/adapters/harness-artifact-copier';

const config = {
  aiBackend: 'http://query:8000',
  jwtSecret: 'user-secret',
  scopedJwtSecret: 'scoped-secret',
};

const request = {
  orgId: 'org-1',
  fromConversationId: 'ses-1',
  toConversationId: 'ses-2',
  actorId: 'bob',
  artifacts: [
    { artifactId: 'a-1', version: 2 },
    { artifactId: 'a-2', version: null },
  ],
};

describe('SS3 HarnessArtifactCopier', () => {
  afterEach(() => sinon.restore());

  const capture = (answer: { statusCode: number; data: unknown }) => {
    const calls: Array<Record<string, unknown>> = [];
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: AIServiceCommand<unknown>) {
      calls.push(this as unknown as Record<string, unknown>);
      return Promise.resolve(answer as never);
    });
    return calls;
  };

  it('copies with a conversation:fork token for the forker, the body naming conversations and versions only', async () => {
    const calls = capture({
      statusCode: 200,
      data: {
        copies: [
          { artifactId: 'a-1', version: 2, copy: { artifactId: 'c-1', documentId: 'd-1', version: 1 } },
          { artifactId: 'a-2', version: null, copy: null, reason: 'not_permitted' },
        ],
      },
    });
    const outcomes = await new HarnessArtifactCopier(() => config).copy(request);
    expect(outcomes).to.deep.equal([
      { artifactId: 'a-1', version: 2, copy: { artifactId: 'c-1', documentId: 'd-1', version: 1 } },
      { artifactId: 'a-2', version: null, copy: null, reason: 'not_permitted' },
    ]);
    const call = calls[0] as unknown as { uri: string; body: string; headers: Record<string, string> };
    expect(call.uri).to.equal('http://query:8000/api/v1/chat/artifacts/copy');
    expect(JSON.parse(call.body)).to.deep.equal({
      fromConversationId: 'ses-1',
      toConversationId: 'ses-2',
      artifacts: [{ artifactId: 'a-1', version: 2 }, { artifactId: 'a-2' }],
    });
    const authorization = Object.entries(call.headers).find(([name]) => name.toLowerCase() === 'authorization')![1];
    const claims = jwt.verify(authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, unknown>;
    expect(claims).to.include({ orgId: 'org-1', userId: 'bob' });
    expect(claims['scopes']).to.deep.equal(['conversation:fork']);
  });

  for (const answer of [
    { statusCode: 503, data: {} },
    { statusCode: 403, data: {} },
    { statusCode: 200, data: { copies: [{ artifactId: 'a-1', version: 0, copy: null }] } },
    { statusCode: 200, data: 'not a report' },
  ]) {
    it(`a ${String(answer.statusCode)} or malformed answer fails the fork (retried with the same key)`, async () => {
      capture(answer);
      let error: unknown;
      try {
        await new HarnessArtifactCopier(() => config).copy(request);
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(ArtifactCopyFailedError);
    });
  }
});

/**
 * E7 (ER-6): the artifact eraser calls the query service's erase route with a
 * service token scoped `conversation:erase` for the session's org, and the
 * body names only what to erase.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import {
  ArtifactErasureFailedError,
  HarnessArtifactEraser,
} from '../../../src/modules/enterprise_search/adapters/harness-artifact-eraser';

const config = {
  aiBackend: 'http://query:8000',
  jwtSecret: 'user-secret',
  scopedJwtSecret: 'scoped-secret',
};

describe('E7 HarnessArtifactEraser', () => {
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

  it("erases a conversation with an org-scoped conversation:erase token", async () => {
    const calls = capture({ statusCode: 200, data: { artifacts: 2, blobs: 1, remaining: true } });
    const report = await new HarnessArtifactEraser(() => config).eraseConversation('org-1', 'ses-1');
    expect(report).to.deep.equal({ artifacts: 2, blobs: 1, remaining: true });
    const call = calls[0] as unknown as { uri: string; body: string; headers: Record<string, string> };
    expect(call.uri).to.equal('http://query:8000/api/v1/chat/artifacts/erase');
    expect(JSON.parse(call.body)).to.deep.equal({ conversationId: 'ses-1' });
    const authorization = Object.entries(call.headers).find(
      ([name]) => name.toLowerCase() === 'authorization',
    )![1];
    const token = authorization.replace('Bearer ', '');
    const claims = jwt.verify(token, 'scoped-secret') as Record<string, unknown>;
    expect(claims['orgId']).to.equal('org-1');
    expect(claims['scopes']).to.deep.equal(['conversation:erase']);
    expect(claims).to.not.have.property('userId');
  });

  it('CLEANUP-G5: lists waiting uploads from a cursor and marks carried ones sent, with the same org-scoped token', async () => {
    const calls = capture({
      statusCode: 200,
      data: { uploads: [{ recordId: 'rec-1', ownerId: 'user-1', conversationId: null }], remaining: true, next: 'rec-9' },
    });
    const eraser = new HarnessArtifactEraser(() => config);
    expect(await eraser.unsent('org-1', 1234, 50, 'rec-0')).to.deep.equal({
      uploads: [{ recordId: 'rec-1', ownerId: 'user-1', conversationId: null }],
      remaining: true,
      next: 'rec-9',
    });
    const listed = calls[0] as unknown as { uri: string; body: string };
    expect(listed.uri).to.equal('http://query:8000/api/v1/chat/attachments/unsent');
    expect(JSON.parse(listed.body)).to.deep.equal({ before: 1234, limit: 50, after: 'rec-0' });
    sinon.restore();
    const marking = capture({ statusCode: 200, data: { marked: ['rec-1'] } });
    await eraser.markSent('org-1', 'ses-1', ['rec-1']);
    const marked = marking[0] as unknown as { uri: string; body: string; headers: Record<string, string> };
    expect(marked.uri).to.equal('http://query:8000/api/v1/chat/attachments/sent');
    expect(JSON.parse(marked.body)).to.deep.equal({ conversationId: 'ses-1', recordIds: ['rec-1'] });
    const token = Object.entries(marked.headers).find(([name]) => name.toLowerCase() === 'authorization')![1].replace('Bearer ', '');
    expect((jwt.verify(token, 'scoped-secret') as Record<string, unknown>)['scopes']).to.deep.equal(['conversation:erase']);
  });

  it("CLEANUP-OPIK: erases a session's traces through the query service; a 503 is a failure the job retries", async () => {
    const calls = capture({ statusCode: 200, data: { traces: 3, configured: true } });
    const eraser = new HarnessArtifactEraser(() => config);
    expect(await eraser.eraseTraces('org-1', 'ses-1', ['run-1', 'run-2'])).to.deep.equal({ traces: 3 });
    const call = calls[0] as unknown as { uri: string; body: string };
    expect(call.uri).to.equal('http://query:8000/api/v1/chat/traces/erase');
    expect(JSON.parse(call.body)).to.deep.equal({ conversationId: 'ses-1', runIds: ['run-1', 'run-2'] });
    sinon.restore();
    capture({ statusCode: 503, data: {} });
    let error: unknown;
    try {
      await eraser.eraseTraces('org-1', 'ses-1', []);
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(ArtifactErasureFailedError);
  });

  it('collects discarded drafts by cut-off', async () => {
    const calls = capture({ statusCode: 200, data: { artifacts: 0, blobs: 0, remaining: false } });
    await new HarnessArtifactEraser(() => config).collectDiscarded('org-1', 1234);
    expect(JSON.parse((calls[0] as unknown as { body: string }).body)).to.deep.equal({ discardedBefore: 1234 });
  });

  for (const answer of [
    { statusCode: 503, data: {} },
    { statusCode: 403, data: {} },
    { statusCode: 200, data: { artifacts: -1, blobs: 0, remaining: false } },
    { statusCode: 200, data: 'not a report' },
  ]) {
    it(`a ${String(answer.statusCode)} or malformed answer is a failure the job retries`, async () => {
      capture(answer);
      let error: unknown;
      try {
        await new HarnessArtifactEraser(() => config).eraseConversation('org-1', 'ses-1');
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(ArtifactErasureFailedError);
    });
  }

  it('QA15F-7: erases chat attachments with the same org-scoped token, the body naming only the attachments', async () => {
    const calls = capture({ statusCode: 200, data: { attachments: 1, blobs: 2, refused: 1 } });
    const report = await new HarnessArtifactEraser(() => config).eraseAttachments('org-1', [
      { recordId: 'rec-1', ownerId: 'user-1' },
      { recordId: 'rec-2', ownerId: 'user-2' },
    ]);
    // An older query service answers no `unrecorded` or `removed`: nothing counted, nothing to mark (QA15G-7).
    expect(report).to.deep.equal({ attachments: 1, blobs: 2, refused: 1, unrecorded: 0, removed: [] });
    const call = calls[0] as unknown as { uri: string; body: string; headers: Record<string, string> };
    expect(call.uri).to.equal('http://query:8000/api/v1/chat/attachments/erase');
    expect(JSON.parse(call.body)).to.deep.equal({
      attachments: [
        { recordId: 'rec-1', ownerId: 'user-1' },
        { recordId: 'rec-2', ownerId: 'user-2' },
      ],
    });
    const authorization = Object.entries(call.headers).find(([name]) => name.toLowerCase() === 'authorization')![1];
    const claims = jwt.verify(authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, unknown>;
    expect(claims).to.deep.include({ orgId: 'org-1', scopes: ['conversation:erase'] });
  });

  it('QA15G-7: reads which uploads are gone and how many refused ones predate the uploader record', async () => {
    capture({
      statusCode: 200,
      data: { attachments: 1, blobs: 2, refused: 2, unrecorded: 1, removed: ['rec-1', 'rec-gone'], extra: true },
    });
    const report = await new HarnessArtifactEraser(() => config).eraseAttachments('org-1', [
      { recordId: 'rec-1', ownerId: 'user-1' },
    ]);
    expect(report).to.deep.equal({ attachments: 1, blobs: 2, refused: 2, unrecorded: 1, removed: ['rec-1', 'rec-gone'] });
  });

  for (const answer of [
    { statusCode: 503, data: {} },
    { statusCode: 200, data: { attachments: 1, blobs: 0 } },
    { statusCode: 200, data: { artifacts: 0, blobs: 0, remaining: false } },
    { statusCode: 200, data: { attachments: 1, blobs: 0, refused: 0, removed: 'rec-1' } },
    { statusCode: 200, data: { attachments: 1, blobs: 0, refused: 0, removed: [''] } },
    { statusCode: 200, data: { attachments: 1, blobs: 0, refused: 0, unrecorded: -1 } },
  ]) {
    it(`QA15F-7: a ${String(answer.statusCode)} or malformed attachment answer is a failure the step retries`, async () => {
      capture(answer);
      let error: unknown;
      try {
        await new HarnessArtifactEraser(() => config).eraseAttachments('org-1', [{ recordId: 'r', ownerId: 'u' }]);
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(ArtifactErasureFailedError);
    });
  }
});

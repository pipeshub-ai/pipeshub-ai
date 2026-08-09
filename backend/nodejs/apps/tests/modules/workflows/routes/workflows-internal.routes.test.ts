import 'reflect-metadata';
import { expect } from 'chai';
import jwt from 'jsonwebtoken';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { Conversation } from '../../../../src/modules/enterprise_search/schema/conversation.schema';
import { CONVERSATION_STATUS } from '../../../../src/modules/enterprise_search/constants/constants';
import { createWorkflowsInternalRouter } from '../../../../src/modules/workflows/routes/workflows-internal.routes';

const SECRET = 'internal-secret';
const ORG_ID = 'org-1';
const CONVERSATION_ID = new mongoose.Types.ObjectId().toString();

function validToken(payload: Record<string, unknown> = {}): string {
  return jwt.sign({ org_id: ORG_ID, ...payload }, SECRET, {
    algorithm: 'HS256',
    audience: 'pipeshub-node-internal',
    issuer: 'pipeshub-python',
  });
}

describe('workflows/routes/workflows-internal.routes', () => {
  let router: any;
  let updateOne: sinon.SinonStub;

  beforeEach(() => {
    const container: any = {
      get: sinon.stub().callsFake((key: string) =>
        key === 'AppConfig' ? { scopedJwtSecret: SECRET } : undefined,
      ),
    };
    router = createWorkflowsInternalRouter(container);
    updateOne = sinon.stub(Conversation, 'updateOne').resolves({ matchedCount: 1 } as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  function handler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    );
    return layer.route.stack[layer.route.stack.length - 1].handle;
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    };
    return res;
  }

  function runResultReq(body: Record<string, unknown> = {}, token: string = validToken()) {
    return {
      headers: { authorization: `Bearer ${token}` },
      params: { conversationId: CONVERSATION_ID },
      body: {
        workflowId: 'wf-1',
        runId: 'run-1',
        status: 'succeeded',
        outputSummary: '# Result\n\nAll good.',
        ...body,
      },
    } as any;
  }

  async function postRunResult(body: Record<string, unknown> = {}, token?: string) {
    const res = mockRes();
    await handler('/conversations/:conversationId/messages', 'post')(
      runResultReq(body, token ?? validToken()),
      res,
    );
    return res;
  }

  describe('POST /conversations/:conversationId/messages', () => {
    it('persists the result as a markdown bot_response so the chat renders it as an answer', async () => {
      await postRunResult();

      const message = updateOne.firstCall.args[1].$push.messages;
      expect(message.messageType).to.equal('bot_response');
      expect(message.contentFormat).to.equal('MARKDOWN');
      expect(message.content).to.equal('# Result\n\nAll good.');
    });

    it('carries the run payload as workflow_run_result tool metadata', async () => {
      await postRunResult({ isDryRun: true, triggerKind: 'manual' });

      const [tool] = updateOne.firstCall.args[1].$push.messages.tools;
      expect(tool.toolName).to.equal('workflow_run_result');
      expect(tool.toolResult.runId).to.equal('run-1');
      expect(tool.toolResult.isDryRun).to.equal(true);
    });

    it('falls back to the error text as content for a failed run', async () => {
      await postRunResult({ status: 'failed', outputSummary: undefined, error: 'boom' });

      expect(updateOne.firstCall.args[1].$push.messages.content).to.equal('boom');
    });

    it('appends with $push so a concurrent user turn is not overwritten', async () => {
      await postRunResult();

      expect(updateOne.firstCall.args[1]).to.have.nested.property('$push.messages');
      expect(updateOne.firstCall.args[1]).to.not.have.property('messages');
    });

    it('scopes the conversation lookup by the token org', async () => {
      await postRunResult();

      expect(updateOne.firstCall.args[0].orgId).to.equal(ORG_ID);
    });

    it('marks the conversation COMPLETE for a succeeded run', async () => {
      await postRunResult({ status: 'succeeded' });

      expect(updateOne.secondCall.args[1].$set.status).to.equal(CONVERSATION_STATUS.COMPLETE);
    });

    it('does not mark a failed run COMPLETE', async () => {
      await postRunResult({ status: 'failed', error: 'boom' });

      expect(updateOne.secondCall.args[1].$set.status).to.equal(CONVERSATION_STATUS.FAILED);
    });

    it('leaves the conversation open for a run that is waiting on an answer', async () => {
      // The run has asked a question and is still alive; closing the
      // conversation out here would tell the user it finished.
      await postRunResult({ status: 'awaiting_input', suspensionKind: 'approval' });

      expect(updateOne.calledOnce).to.be.true;
    });

    it('carries the suspension kind so the card knows it is answerable', async () => {
      await postRunResult({ status: 'awaiting_input', suspensionKind: 'approval' });

      const message = updateOne.firstCall.args[1].$push.messages;
      expect(message.tools[0].toolResult.suspensionKind).to.equal('approval');
    });

    it('leaves an in-progress conversation alone so a live turn is not closed out', async () => {
      await postRunResult();

      expect(updateOne.secondCall.args[0].status).to.deep.equal({
        $ne: CONVERSATION_STATUS.INPROGRESS,
      });
    });

    it('skips without writing when the conversation id is malformed', async () => {
      const res = mockRes();
      await handler('/conversations/:conversationId/messages', 'post')(
        { ...runResultReq(), params: { conversationId: 'not-an-object-id' } },
        res,
      );

      expect(updateOne.called).to.be.false;
      expect(res.json.firstCall.args[0]).to.deep.include({ ok: true, skipped: 'invalid_conversation_id' });
    });

    it('reports a failed write as 5xx rather than a silent success', async () => {
      updateOne.rejects(new Error('mongo down'));

      const res = await postRunResult();

      expect(res.status.firstCall.args[0]).to.equal(500);
    });
  });

  describe('POST /conversations/:conversationId/emit', () => {
    async function postEmit(body: Record<string, unknown>) {
      const res = mockRes();
      await handler('/conversations/:conversationId/emit', 'post')(
        {
          headers: { authorization: `Bearer ${validToken()}` },
          params: { conversationId: CONVERSATION_ID },
          body,
        } as any,
        res,
      );
      return res;
    }

    it('does not touch the conversation status because the run is still going', async () => {
      await postEmit({ runId: 'run-1', content: 'halfway there' });

      expect(updateOne.callCount).to.equal(1);
      expect(updateOne.firstCall.args[1]).to.not.have.nested.property('$set.status');
    });

    it('rejects an empty body rather than appending a blank message', async () => {
      const res = await postEmit({ runId: 'run-1', content: '' });

      expect(res.status.firstCall.args[0]).to.equal(400);
      expect(updateOne.called).to.be.false;
    });
  });

  describe('PATCH /conversations/:conversationId/workflows', () => {
    async function patchLink(body: Record<string, unknown>) {
      const res = mockRes();
      await handler('/conversations/:conversationId/workflows', 'patch')(
        {
          headers: { authorization: `Bearer ${validToken()}` },
          params: { conversationId: CONVERSATION_ID },
          body,
        } as any,
        res,
      );
      return res;
    }

    it('links a workflow without rewriting the rest of the conversation', async () => {
      // The document also carries every message, so a read-modify-save here
      // would drop anything appended in between.
      await patchLink({ action: 'add', workflowId: 'wf-1' });

      expect(updateOne.calledOnce).to.be.true;
      expect(updateOne.firstCall.args[1]).to.deep.equal({
        $addToSet: { connectedWorkflowIds: 'wf-1' },
      });
    });

    it('unlinks a workflow', async () => {
      await patchLink({ action: 'remove', workflowId: 'wf-1' });

      expect(updateOne.firstCall.args[1]).to.deep.equal({
        $pull: { connectedWorkflowIds: 'wf-1' },
      });
    });

    it('ignores an unknown action rather than writing something arbitrary', async () => {
      const res = await patchLink({ action: 'replace', workflowId: 'wf-1' });

      expect(updateOne.called).to.be.false;
      expect(res.json.firstCall.args[0].skipped).to.equal('unknown_action');
    });

    it('reports a deleted conversation as skipped, not as a failure', async () => {
      updateOne.resolves({ matchedCount: 0 } as any);

      const res = await patchLink({ action: 'add', workflowId: 'wf-1' });

      expect(res.json.firstCall.args[0].skipped).to.equal('conversation_not_found');
    });
  });

  describe('internal token verification', () => {
    async function postWith(token: string) {
      const res = mockRes();
      await handler('/conversations/:conversationId/messages', 'post')(
        runResultReq({}, token),
        res,
      );
      return res;
    }

    it('accepts a correctly issued token', async () => {
      const res = await postWith(validToken());
      expect(res.status.called).to.be.false;
    });

    it('rejects a token from a different issuer', async () => {
      const token = jwt.sign({ org_id: ORG_ID }, SECRET, {
        algorithm: 'HS256',
        audience: 'pipeshub-node-internal',
        issuer: 'somebody-else',
      });

      expect((await postWith(token)).status.firstCall.args[0]).to.equal(401);
    });

    it('rejects a token for a different audience', async () => {
      const token = jwt.sign({ org_id: ORG_ID }, SECRET, {
        algorithm: 'HS256',
        audience: 'some-other-service',
        issuer: 'pipeshub-python',
      });

      expect((await postWith(token)).status.firstCall.args[0]).to.equal(401);
    });

    it('rejects an unsigned alg:none token', async () => {
      // The `algorithms` allowlist is the only thing between this and a
      // caller minting their own org_id without knowing the secret.
      const token = jwt.sign(
        { org_id: ORG_ID, aud: 'pipeshub-node-internal', iss: 'pipeshub-python' },
        '',
        { algorithm: 'none' },
      );

      expect((await postWith(token)).status.firstCall.args[0]).to.equal(401);
    });

    it('rejects a token signed with the wrong secret', async () => {
      const token = jwt.sign({ org_id: ORG_ID }, 'not-the-secret', {
        algorithm: 'HS256',
        audience: 'pipeshub-node-internal',
        issuer: 'pipeshub-python',
      });

      expect((await postWith(token)).status.firstCall.args[0]).to.equal(401);
    });

    it('rejects a valid token carrying no org_id, which would leave the query unscoped', async () => {
      const token = jwt.sign({}, SECRET, {
        algorithm: 'HS256',
        audience: 'pipeshub-node-internal',
        issuer: 'pipeshub-python',
      });

      expect((await postWith(token)).status.firstCall.args[0]).to.equal(401);
      expect(updateOne.called).to.be.false;
    });

    it('rejects a request with no Authorization header', async () => {
      const res = mockRes();
      await handler('/conversations/:conversationId/messages', 'post')(
        { ...runResultReq(), headers: {} },
        res,
      );

      expect(res.status.firstCall.args[0]).to.equal(401);
    });
  });
});

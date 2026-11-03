import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import type { App } from '@slack/bolt';
import {
  approvalTurnResult,
  registerApprovalActions,
  resetApprovalGuardForTests,
} from '../../../src/integrations/slack-bot/src/approvalActions';
import {
  buildApprovalCard,
  type ToolApprovalDetails,
} from '../../../src/integrations/slack-bot/src/utils/approval-card';

 
type Handler = (args: any) => Promise<void>;
 
type AnyBlock = any;

const OWNER = 'owner@example.com';
const WRONG = 'Something went wrong, try again';

function fakeApp() {
  const actions = new Map<string, Handler>();
  const views = new Map<string, Handler>();
  const app = {
    action: (id: string, handler: Handler) => actions.set(id, handler),
    view: (id: string, handler: Handler) => views.set(id, handler),
  };
  return { app: app as unknown as App, actions, views };
}

function approval(fields: Partial<ToolApprovalDetails> = {}): ToolApprovalDetails {
  return {
    approvalId: 'ap-1',
    instanceId: 'inst-1',
    serverName: 'Jira',
    toolName: 'create_issue',
    toolTitle: 'Create issue',
    readOnly: false,
    kind: 'write',
    canRemember: true,
    expiresAt: Date.now() + 60_000,
    arguments: { project: 'OPS' },
    toolPath: 'jira.create_issue',
    source: 'toolset',
    argsHash: 'h1',
    editable: true,
    ...fields,
  };
}

function fakeClient(emails: Record<string, string | undefined | Error>, card = buildApprovalCard(approval(), 'U1', 'T1')) {
  return {
    users: {
      info: sinon.stub().callsFake(async ({ user }: { user: string }) => {
        const email = emails[user];
        if (email instanceof Error) throw email;
        return { user: { profile: { email } } };
      }),
    },
    chat: { update: sinon.stub().resolves({}), postEphemeral: sinon.stub().resolves({}) },
    views: { open: sinon.stub().resolves({}) },
    conversations: {
      replies: sinon.stub().resolves({
        messages: [{ ts: '100.1', text: 'question' }, { ts: '100.2', text: card.text, blocks: card.blocks }],
      }),
    },
  };
}

type Client = ReturnType<typeof fakeClient>;

function click(
  actionId: string,
  user: string,
  client: Client,
  { card = buildApprovalCard(approval(), 'U1', 'T1'), team = 'T1' } = {},
) {
  const button = (card.blocks.find((b) => b.type === 'actions') as AnyBlock).elements.find(
    (b: AnyBlock) => b.action_id === actionId,
  );
  return {
    ack: sinon.stub().resolves(),
    body: {
      type: 'block_actions',
      user: { id: user },
      team: { id: team },
      trigger_id: 'trig-1',
      channel: { id: 'C1' },
      message: { ts: '100.2', thread_ts: '100.1', text: card.text, blocks: card.blocks },
      actions: [{ action_id: actionId, value: button.value }],
    },
    client,
    context: { teamId: team },
  };
}

function lastText(stub: sinon.SinonStub): string {
  const blocks = stub.lastCall.args[0].blocks as AnyBlock[];
  return blocks[blocks.length - 1].elements[0].text as string;
}

describe('slack-bot/approvalActions', () => {
  let resumeApproval: sinon.SinonStub;
  let handlers: ReturnType<typeof fakeApp>;

  beforeEach(() => {
    resetApprovalGuardForTests();
    resumeApproval = sinon.stub().resolves('allowed');
    handlers = fakeApp();
    registerApprovalActions(handlers.app, { resumeApproval });
  });

  const press = (actionId: string, user: string, client: Client, options?: Parameters<typeof click>[3]) => {
    const args = click(actionId, user, client, options);
    return handlers.actions.get(actionId)!(args).then(() => args);
  };

  describe('who may answer', () => {
    it('tells anyone but the requester to wait, and changes nothing', async () => {
      const client = fakeClient({ U1: OWNER, U2: 'other@example.com' });
      const args = await press('approval_allow', 'U2', client);

      expect(args.ack.calledOnce).to.equal(true);
      expect(client.chat.postEphemeral.firstCall.args[0]).to.deep.include({
        channel: 'C1',
        thread_ts: '100.1',
        user: 'U2',
        text: 'Only <@U1> can approve this',
      });
      expect(resumeApproval.called).to.equal(false);
      expect(client.chat.update.called).to.equal(false);
    });

    it('leaves an expired card alone when someone else clicks it', async () => {
      const client = fakeClient({ U1: OWNER, U2: 'other@example.com' });
      await press('approval_allow', 'U2', client, {
        card: buildApprovalCard(approval({ expiresAt: Date.now() - 1 }), 'U1', 'T1'),
      });
      expect(client.chat.update.called).to.equal(false);
      expect(client.chat.postEphemeral.firstCall.args[0].text).to.equal('Only <@U1> can approve this');
    });

    it('treats a clicker with no email as someone else', async () => {
      const client = fakeClient({ U1: OWNER, U2: undefined });
      await press('approval_allow', 'U2', client);
      expect(client.chat.postEphemeral.firstCall.args[0].text).to.equal('Only <@U1> can approve this');
      expect(resumeApproval.called).to.equal(false);
    });

    it('says something went wrong when the lookup fails', async () => {
      const client = fakeClient({ U1: OWNER, U2: new Error('ratelimited') });
      await press('approval_allow', 'U2', client);
      expect(client.chat.postEphemeral.firstCall.args[0].text).to.equal(WRONG);
      expect(resumeApproval.called).to.equal(false);
      expect(client.chat.update.called).to.equal(false);
    });

    it('turns away a click from another workspace', async () => {
      const client = fakeClient({ U1: OWNER });
      await press('approval_allow', 'U1', client, { team: 'T2' });
      expect(client.chat.postEphemeral.firstCall.args[0].text).to.equal('Only <@U1> can approve this');
      expect(resumeApproval.called).to.equal(false);
    });

    it('lets another Slack account with the requester email answer', async () => {
      const client = fakeClient({ U1: OWNER, U3: 'Owner@example.com' });
      await press('approval_allow', 'U3', client);
      expect(resumeApproval.calledOnce).to.equal(true);
    });
  });

  describe('the answer', () => {
    it('shows it is sending, resumes with allow, then shows the receipt', async () => {
      const client = fakeClient({ U1: OWNER });
      const args = await press('approval_allow', 'U1', client);

      expect(args.ack.calledBefore(resumeApproval)).to.equal(true);
      expect(resumeApproval.firstCall.args[0]).to.deep.equal({
        channel: 'C1',
        threadTs: '100.1',
        clickerSlackId: 'U1',
        approvalId: 'ap-1',
        argsHash: 'h1',
        decision: 'allow',
      });
      const sending = client.chat.update.firstCall.args[0];
      expect(sending).to.deep.include({ channel: 'C1', ts: '100.2' });
      // The call stays as the record; the status line and the buttons give way to the receipt.
      expect(sending.blocks.map((b: AnyBlock) => b.block_id ?? b.type)).to.deep.equal(['header', 'approval_meta', 'approval_status']);
      expect(sending.blocks[2].elements[0].text).to.equal('Sending your answer…');
      expect(client.chat.update.firstCall.calledBefore(resumeApproval.firstCall)).to.equal(true);
      expect(client.chat.update.calledTwice).to.equal(true);
      expect(lastText(client.chat.update)).to.equal('Approved by <@U1>');
    });

    it('keeps the call and its Details on the receipt, and drops the lines of a card posted before its blocks had ids', async () => {
      const card = buildApprovalCard(approval({ arguments: { summary: 'Fix login', cloudId: 'c-1' } }), 'U1', 'T1');
      // Slack names an unnamed block with a random id when it sends the message back.
      const legacy = { ...card, blocks: [...card.blocks, { type: 'context', block_id: 'x1Y2', elements: [{ type: 'mrkdwn', text: 'old' }] }] };
      const client = fakeClient({ U1: OWNER }, legacy as typeof card);
      await press('approval_allow', 'U1', client, { card: legacy as typeof card });
      expect(client.chat.update.lastCall.args[0].blocks.map((b: AnyBlock) => b.block_id ?? b.type)).to.deep.equal([
        'header', 'approval_document', 'approval_details', 'approval_status',
      ]);
    });

    it('shows "Expired" when the backend says so', async () => {
      resumeApproval.resolves('expired');
      const client = fakeClient({ U1: OWNER });
      await press('approval_deny', 'U1', client);
      expect(lastText(client.chat.update)).to.equal("Expired, so it didn't run");
    });

    it('says an approved call that a Block stopped did not run', async () => {
      resumeApproval.resolves('allowedNotRun');
      const client = fakeClient({ U1: OWNER });
      await press('approval_allow', 'U1', client);
      expect(lastText(client.chat.update)).to.equal("Approved by <@U1>, but it didn't run");
    });

    it('shows "Already answered", never "Approved by", for a card answered elsewhere', async () => {
      resumeApproval.resolves('answered');
      const client = fakeClient({ U1: OWNER });
      await press('approval_allow', 'U1', client);
      expect(lastText(client.chat.update)).to.equal('Already answered');
    });

    for (const failure of ['resolves', 'rejects'] as const) {
      it(`puts the buttons back and says so when the answer did not go through (${failure})`, async () => {
        if (failure === 'resolves') resumeApproval.resolves('failed');
        else resumeApproval.rejects(new Error('boom'));
        const client = fakeClient({ U1: OWNER });
        const args = await press('approval_allow', 'U1', client);

        expect(client.chat.update.lastCall.args[0].blocks).to.deep.equal(args.body.message.blocks);
        expect(client.chat.postEphemeral.firstCall.args[0]).to.deep.include({ user: 'U1', text: WRONG });

        resumeApproval.resolves('allowed');
        await press('approval_allow', 'U1', client);
        expect(resumeApproval.calledTwice).to.equal(true);
      });
    }

    it('runs one answer for a double click', async () => {
      let finish: (value: string) => void = () => undefined;
      resumeApproval.returns(new Promise((resolve) => { finish = resolve; }));
      const client = fakeClient({ U1: OWNER });
      const first = press('approval_allow', 'U1', client);
      const second = press('approval_deny', 'U1', client);
      await second;
      finish('allowed');
      await first;
      expect(resumeApproval.calledOnce).to.equal(true);
    });

    it('turns an expired card into "Expired" and runs nothing', async () => {
      const client = fakeClient({ U1: OWNER });
      await press('approval_allow', 'U1', client, {
        card: buildApprovalCard(approval({ expiresAt: Date.now() - 1 }), 'U1', 'T1'),
      });
      expect(resumeApproval.called).to.equal(false);
      expect(lastText(client.chat.update)).to.equal("Expired, so it didn't run");
    });
  });

  describe('change something else', () => {
    async function openModal(client: Client) {
      const args = await press('approval_change', 'U1', client);
      expect(args.ack.calledBefore(client.views.open)).to.equal(true);
      return client.views.open.firstCall.args[0];
    }

    function submit(view: AnyBlock, user: string, client: Client, text = '  Use OPS-2  ') {
      const ack = sinon.stub().resolves();
      const block = view.blocks[0];
      return handlers.views
        .get('approval_feedback')!({
          ack,
          body: { user: { id: user }, team: { id: 'T1' } },
          view: {
            private_metadata: view.private_metadata,
            state: { values: { [block.block_id]: { [block.element.action_id]: { value: text } } } },
          },
          client,
          context: { teamId: 'T1' },
        })
        .then(() => ack);
    }

    it('denies with the feedback typed in the modal', async () => {
      resumeApproval.resolves('declined');
      const client = fakeClient({ U1: OWNER });
      const opened = await openModal(client);
      expect(opened.trigger_id).to.equal('trig-1');
      expect(opened.view.callback_id).to.equal('approval_feedback');
      expect([opened.view.title.text, opened.view.submit.text]).to.deep.equal(['Suggest a change', 'Send suggestion']);
      expect(opened.view.blocks[0].element).to.deep.include({ multiline: true, max_length: 2000 });
      expect(resumeApproval.called).to.equal(false);

      const ack = await submit(opened.view, 'U1', client);

      expect(ack.calledOnce).to.equal(true);
      expect(resumeApproval.firstCall.args[0]).to.deep.include({
        channel: 'C1',
        threadTs: '100.1',
        decision: 'deny',
        feedback: 'Use OPS-2',
      });
      expect(client.chat.update.firstCall.args[0].ts).to.equal('100.2');
      expect(lastText(client.chat.update)).to.equal('Denied by <@U1>: Use OPS-2');
    });

    it('runs nothing for a submission by someone else', async () => {
      const client = fakeClient({ U1: OWNER, U2: 'other@example.com' });
      const opened = await openModal(client);
      await submit(opened.view, 'U2', client);
      expect(resumeApproval.called).to.equal(false);
      expect(client.chat.update.called).to.equal(false);
      expect(client.chat.postEphemeral.firstCall.args[0].text).to.equal('Only <@U1> can approve this');
    });

    it('says something went wrong when the modal cannot open', async () => {
      const client = fakeClient({ U1: OWNER });
      client.views.open.rejects(new Error('expired_trigger_id'));
      await press('approval_change', 'U1', client);
      expect(client.chat.postEphemeral.firstCall.args[0].text).to.equal(WRONG);
    });
  });

  it('reads the turn: the first settled answer decides', () => {
    expect(approvalTurnResult('allow', undefined)).to.equal('failed');
    expect(approvalTurnResult('allow', { answer: 'allowed', replayed: false })).to.equal('allowed');
    expect(approvalTurnResult('deny', { answer: 'declined', replayed: false })).to.equal('declined');
    expect(approvalTurnResult('allow', { answer: 'expired', replayed: false })).to.equal('expired');
    expect(approvalTurnResult('allow', { answer: 'allowed', replayed: true })).to.equal('answered');
    expect(approvalTurnResult('allow', { answer: 'declined', replayed: false })).to.equal('answered');
    // Allowed, but a Block set since the card was shown stopped it.
    expect(approvalTurnResult('allow', { answer: 'allowed', replayed: false, ended: 'blocked' })).to.equal('allowedNotRun');
    expect(approvalTurnResult('allow', { answer: 'allowed', replayed: false, ended: 'completed' })).to.equal('allowed');
    expect(approvalTurnResult('deny', { answer: 'declined', replayed: false, ended: 'blocked' })).to.equal('declined');
  });
});

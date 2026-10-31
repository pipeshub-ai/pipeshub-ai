import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import type { App } from '@slack/bolt';
import {
  registerApprovalActions,
  rememberApprovalCard,
  resetApprovalCardsForTests,
} from '../../../src/integrations/slack-bot/src/approvalActions';
import {
  buildApprovalCard,
  type ToolApprovalDetails,
} from '../../../src/integrations/slack-bot/src/utils/approval-card';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Handler = (args: any) => Promise<void>;

function fakeApp() {
  const actions = new Map<string, Handler>();
  const views = new Map<string, Handler>();
  const app = {
    action: (id: string, handler: Handler) => actions.set(id, handler),
    view: (id: string, handler: Handler) => views.set(id, handler),
  };
  return { app: app as unknown as App, actions, views };
}

function fakeClient(emails: Record<string, string>) {
  return {
    users: {
      info: sinon.stub().callsFake(async ({ user }: { user: string }) => ({
        user: { profile: { email: emails[user] } },
      })),
    },
    chat: { update: sinon.stub().resolves({}), postEphemeral: sinon.stub().resolves({}) },
    views: { open: sinon.stub().resolves({}) },
  };
}

const APPROVAL: ToolApprovalDetails = {
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
};

function remember(fields: Partial<ToolApprovalDetails> = {}) {
  const approval = { ...APPROVAL, ...fields };
  rememberApprovalCard(approval, buildApprovalCard(approval, 'U1'), {
    requesterSlackId: 'U1',
    requesterEmail: 'owner@example.com',
    channel: 'C1',
    threadTs: '100.1',
    cardTs: '100.2',
  });
}

function click(actionId: string, user: string, client: ReturnType<typeof fakeClient>) {
  return {
    ack: sinon.stub().resolves(),
    body: {
      type: 'block_actions',
      user: { id: user },
      trigger_id: 'trig-1',
      channel: { id: 'C1' },
      message: { ts: '100.2', blocks: [] },
      actions: [{ action_id: actionId, value: JSON.stringify({ approvalId: 'ap-1', argsHash: 'h1' }) }],
    },
    client,
    context: { teamId: 'T1' },
  };
}

describe('slack-bot/approvalActions', () => {
  let resumeApproval: sinon.SinonStub;
  let handlers: ReturnType<typeof fakeApp>;

  beforeEach(() => {
    resetApprovalCardsForTests();
    resumeApproval = sinon.stub().resolves();
    handlers = fakeApp();
    registerApprovalActions(handlers.app, { resumeApproval });
  });

  it('tells anyone but the requester to wait, and sends nothing', async () => {
    remember();
    const client = fakeClient({ U2: 'other@example.com' });
    const args = click('approval_allow', 'U2', client);
    await handlers.actions.get('approval_allow')!(args);

    expect(args.ack.calledOnce).to.equal(true);
    expect(client.chat.postEphemeral.calledOnce).to.equal(true);
    expect(client.chat.postEphemeral.firstCall.args[0]).to.deep.include({
      channel: 'C1',
      user: 'U2',
      text: 'Only <@U1> can approve this',
    });
    expect(resumeApproval.called).to.equal(false);
    expect(client.chat.update.called).to.equal(false);
  });

  it('lets another Slack account with the requester email answer', async () => {
    remember();
    const client = fakeClient({ U3: 'Owner@example.com' });
    await handlers.actions.get('approval_allow')!(click('approval_allow', 'U3', client));
    expect(resumeApproval.calledOnce).to.equal(true);
  });

  it('approves for the requester: resumes with allow and turns the card into a receipt', async () => {
    remember();
    const client = fakeClient({ U1: 'owner@example.com' });
    const args = click('approval_allow', 'U1', client);
    await handlers.actions.get('approval_allow')!(args);

    expect(args.ack.calledBefore(resumeApproval)).to.equal(true);
    expect(resumeApproval.calledOnce).to.equal(true);
    expect(resumeApproval.firstCall.args[0]).to.deep.equal({
      channel: 'C1',
      threadTs: '100.1',
      clickerSlackId: 'U1',
      approvalId: 'ap-1',
      argsHash: 'h1',
      decision: 'allow',
    });
    const update = client.chat.update.firstCall.args[0];
    expect(update).to.deep.include({ channel: 'C1', ts: '100.2' });
    const blocks = update.blocks as Array<{ type: string; elements?: Array<{ text: string }> }>;
    expect(blocks.map((b) => b.type)).to.deep.equal(['header', 'section', 'context']);
    expect(blocks[blocks.length - 1]!.elements![0]!.text).to.equal('Approved by <@U1>');
  });

  it('runs one answer for a double click', async () => {
    remember();
    const client = fakeClient({ U1: 'owner@example.com' });
    await Promise.all([
      handlers.actions.get('approval_allow')!(click('approval_allow', 'U1', client)),
      handlers.actions.get('approval_deny')!(click('approval_deny', 'U1', client)),
    ]);
    await handlers.actions.get('approval_allow')!(click('approval_allow', 'U1', client));
    expect(resumeApproval.calledOnce).to.equal(true);
    expect(client.chat.update.calledOnce).to.equal(true);
  });

  it('denies with feedback from the change something else modal', async () => {
    remember();
    const client = fakeClient({ U1: 'owner@example.com' });
    const args = click('approval_change', 'U1', client);
    await handlers.actions.get('approval_change')!(args);

    expect(args.ack.calledBefore(client.views.open)).to.equal(true);
    const view = client.views.open.firstCall.args[0].view;
    expect(client.views.open.firstCall.args[0].trigger_id).to.equal('trig-1');
    expect(view.callback_id).to.equal('approval_feedback');
    expect(view.blocks[0].element).to.deep.include({ multiline: true, max_length: 2000 });
    expect(resumeApproval.called).to.equal(false);

    const ack = sinon.stub().resolves();
    await handlers.views.get('approval_feedback')!({
      ack,
      body: { user: { id: 'U1' } },
      view: {
        private_metadata: view.private_metadata,
        state: { values: { [view.blocks[0].block_id]: { [view.blocks[0].element.action_id]: { value: '  Use OPS-2  ' } } } },
      },
      client,
      context: { teamId: 'T1' },
    });

    expect(ack.calledOnce).to.equal(true);
    expect(resumeApproval.firstCall.args[0]).to.deep.include({ decision: 'deny', feedback: 'Use OPS-2' });
    const blocks = client.chat.update.firstCall.args[0].blocks;
    expect(blocks[blocks.length - 1].elements[0].text).to.equal('Declined by <@U1>: Use OPS-2');
  });

  it('turns an expired card into "Expired" and runs nothing', async () => {
    remember({ expiresAt: Date.now() - 1 });
    const client = fakeClient({ U1: 'owner@example.com' });
    await handlers.actions.get('approval_allow')!(click('approval_allow', 'U1', client));
    expect(resumeApproval.called).to.equal(false);
    const blocks = client.chat.update.firstCall.args[0].blocks;
    expect(blocks[blocks.length - 1].elements[0].text).to.equal('Expired');
  });
});

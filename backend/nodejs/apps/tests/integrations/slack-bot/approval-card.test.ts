import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildApprovalCard,
  buildApprovalReceipt,
  type ToolApprovalDetails,
} from '../../../src/integrations/slack-bot/src/utils/approval-card';

const EXPIRES_AT = 1_793_000_000_000;

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
    expiresAt: EXPIRES_AT,
    arguments: { project: 'OPS', summary: 'Fix login' },
    toolPath: 'jira.create_issue',
    source: 'toolset',
    argsHash: 'h1',
    editable: true,
    ...fields,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyBlock = any;

function actionsOf(blocks: AnyBlock[]): AnyBlock[] {
  return blocks.find((b) => b.type === 'actions').elements;
}

describe('slack-bot/utils/approval-card', () => {
  it('builds a write card: header, argument lines, expiry and three buttons', () => {
    const { text, blocks } = buildApprovalCard(approval({ displayName: 'Create a Jira issue' }), 'U1') as {
      text: string;
      blocks: AnyBlock[];
    };
    expect(text).to.contain('Create a Jira issue on Jira');
    expect(blocks[0]).to.deep.include({ type: 'header' });
    expect(blocks[0].text.text).to.equal('Create a Jira issue on Jira');
    const section = blocks.find((b) => b.type === 'section');
    expect(section.text.text).to.equal('*project:* OPS\n*summary:* Fix login');
    const context = blocks.find((b) => b.type === 'context').elements[0].text as string;
    expect(context).to.contain(`<!date^${EXPIRES_AT / 1000}^`);
    expect(context).to.contain('<@U1>');
    expect(context).not.to.contain("Can't be undone");

    const buttons = actionsOf(blocks);
    expect(buttons.map((b: AnyBlock) => b.action_id)).to.deep.equal([
      'approval_allow',
      'approval_change',
      'approval_deny',
    ]);
    expect(buttons.map((b: AnyBlock) => b.text.text)).to.deep.equal(['Approve', 'Change something else', 'Deny']);
    expect(buttons[0].style).to.equal('primary');
    for (const button of buttons) {
      expect(JSON.parse(button.value)).to.deep.equal({ approvalId: 'ap-1', argsHash: 'h1' });
    }
  });

  it('marks a destructive card: danger approve and "Can\'t be undone"', () => {
    const { blocks } = buildApprovalCard(approval({ kind: 'destructive', argsSummary: 'Delete OPS-1' }), 'U1') as {
      blocks: AnyBlock[];
    };
    expect(blocks[0].text.text).to.equal('create_issue on Jira');
    expect(blocks.find((b) => b.type === 'section').text.text).to.equal('Delete OPS-1');
    expect(blocks.find((b) => b.type === 'context').elements[0].text).to.contain("Can't be undone");
    expect(actionsOf(blocks)[0].style).to.equal('danger');
  });

  it('shows at most five arguments, each value capped, and keeps them plain text', () => {
    const args: Record<string, unknown> = { a: 'x'.repeat(500), b: { n: 1 }, c: '<!channel>', d: 4, e: 5, f: 6 };
    const { blocks } = buildApprovalCard(approval({ arguments: args }), 'U1') as { blocks: AnyBlock[] };
    const lines = (blocks.find((b) => b.type === 'section').text.text as string).split('\n');
    expect(lines).to.have.length(5);
    expect(lines[0]!.length).to.be.at.most('*a:* '.length + 200);
    expect(lines[1]).to.equal('*b:* {"n":1}');
    expect(lines[2]).to.equal('*c:* &lt;!channel&gt;');
  });

  it('writes receipts', () => {
    const textOf = (blocks: AnyBlock[]) => blocks[0].elements[0].text;
    expect(textOf(buildApprovalReceipt('allowed', 'U1'))).to.equal('Approved by <@U1>');
    expect(textOf(buildApprovalReceipt('declined', 'U1'))).to.equal('Declined by <@U1>');
    expect(textOf(buildApprovalReceipt('declined', 'U1', 'use OPS-2'))).to.equal('Declined by <@U1>: use OPS-2');
    expect(textOf(buildApprovalReceipt('expired', 'U1'))).to.equal('Expired');
  });
});

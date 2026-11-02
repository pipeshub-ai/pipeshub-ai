import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildApprovalCard,
  buildApprovalReceipt,
  parseApprovalButtonValue,
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
    const { text, blocks } = buildApprovalCard(approval({ displayName: 'Created a Jira issue' }), 'U1', 'T1') as {
      text: string;
      blocks: AnyBlock[];
    };
    expect(text).to.contain('Create issue on Jira');
    expect(blocks[0]).to.deep.include({ type: 'header' });
    expect(blocks[0].text.text).to.equal('Create issue on Jira');
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
    const value = { approvalId: 'ap-1', argsHash: 'h1', requesterSlackId: 'U1', expiresAt: EXPIRES_AT, teamId: 'T1' };
    for (const button of buttons) {
      expect(JSON.parse(button.value)).to.deep.equal(value);
      expect(parseApprovalButtonValue(button.value)).to.deep.equal(value);
    }
  });

  it('marks a destructive card: danger approve and "Can\'t be undone"', () => {
    const { blocks } = buildApprovalCard(approval({ kind: 'destructive' }), 'U1', 'T1') as { blocks: AnyBlock[] };
    expect(blocks.find((b) => b.type === 'context' && b.elements[0].text.includes('Expires')).elements[0].text).to.contain(
      "Can't be undone",
    );
    expect(actionsOf(blocks)[0].style).to.equal('danger');
  });

  it("titles the card with the tool's title, else its name, never the past-tense display name", () => {
    const titleOf = (fields: Partial<ToolApprovalDetails>) =>
      (buildApprovalCard(approval(fields), 'U1', 'T1').blocks[0] as AnyBlock).text.text as string;
    expect(titleOf({ displayName: 'Created an issue' })).to.equal('Create issue on Jira');
    expect(titleOf({ toolTitle: null, displayName: 'Created an issue' })).to.equal('create_issue on Jira');
    expect(titleOf({ toolTitle: '  ' })).to.equal('create_issue on Jira');
  });

  it('shows the summary as a subtitle and still every argument', () => {
    const { blocks } = buildApprovalCard(approval({ argsSummary: 'Fix login in OPS' }), 'U1', 'T1') as {
      blocks: AnyBlock[];
    };
    expect(blocks[1]).to.deep.equal({ type: 'context', elements: [{ type: 'mrkdwn', text: 'Fix login in OPS' }] });
    expect(blocks.find((b) => b.type === 'section').text.text).to.equal('*project:* OPS\n*summary:* Fix login');
  });

  it('shows every argument that fits, keeps them plain, caps each value and says what it left out', () => {
    const args: Record<string, unknown> = { a: 'x'.repeat(500), b: { n: 1 }, c: '<!channel>', d: 4, e: 5, f: 6 };
    const { blocks } = buildApprovalCard(approval({ arguments: args }), 'U1', 'T1') as { blocks: AnyBlock[] };
    const lines = (blocks.find((b) => b.type === 'section').text.text as string).split('\n');
    expect(lines.slice(1, 6)).to.deep.equal(['*b:* {"n":1}', '*c:* &lt;!channel&gt;', '*d:* 4', '*e:* 5', '*f:* 6']);
    expect(lines[0]!.length).to.be.at.most('*a:* '.length + 200);
    expect(lines[6]).to.equal('_Open in PipesHub to see everything._');
  });

  it('cuts many long arguments at the section limit with a "+N more" line', () => {
    const args = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`field${String(i)}`, 'v'.repeat(300)]));
    const { blocks } = buildApprovalCard(approval({ arguments: args }), 'U1', 'T1') as { blocks: AnyBlock[] };
    const text = blocks.find((b) => b.type === 'section').text.text as string;
    expect(text.length).to.be.at.most(3000);
    const lines = text.split('\n');
    const shown = lines.length - 1;
    expect(shown).to.be.greaterThan(5);
    expect(lines.at(-1)).to.equal(`_+${String(40 - shown)} more. Open in PipesHub to see everything._`);
  });

  it('shows the preview, escaped and capped, when the arguments were too large to send', () => {
    const short = buildApprovalCard(
      approval({ arguments: null, argumentsPreview: '{"q": "<!here>"}' }),
      'U1',
      'T1',
    ) as { blocks: AnyBlock[] };
    expect(short.blocks.find((b: AnyBlock) => b.type === 'section').text.text).to.equal('{"q": "&lt;!here&gt;"}');
    const long = buildApprovalCard(approval({ arguments: null, argumentsPreview: 'y'.repeat(5000) }), 'U1', 'T1') as {
      blocks: AnyBlock[];
    };
    const text = long.blocks.find((b: AnyBlock) => b.type === 'section').text.text as string;
    expect(text.length).to.be.at.most(3000);
    expect(text.endsWith('\n_Open in PipesHub to see everything._')).to.equal(true);
  });

  it('keeps the notification text and a long summary plain, without cutting an entity in half', () => {
    const summary = `${'a'.repeat(2996)}&&&&`;
    const { text, blocks } = buildApprovalCard(
      approval({ toolTitle: '<!channel> & co', argsSummary: summary }),
      'U1',
      'T1',
    ) as { text: string; blocks: AnyBlock[] };
    expect(text).to.contain('&lt;!channel&gt; &amp; co on Jira');
    const shown = blocks[1].elements[0].text as string;
    expect(shown.length).to.be.at.most(3000);
    expect(shown.replace(/&amp;/g, '').includes('&')).to.equal(false);
  });

  it('reads back only a well-formed button value', () => {
    expect(parseApprovalButtonValue('not json')).to.equal(null);
    expect(parseApprovalButtonValue(JSON.stringify({ approvalId: 'ap-1', argsHash: 'h1' }))).to.equal(null);
  });

  it('writes receipts', () => {
    const textOf = (blocks: AnyBlock[]) => blocks[0].elements[0].text;
    expect(textOf(buildApprovalReceipt('allowed', 'U1'))).to.equal('Approved by <@U1>');
    expect(textOf(buildApprovalReceipt('allowedNotRun', 'U1'))).to.equal("Approved by <@U1>, but it didn't run");
    expect(textOf(buildApprovalReceipt('declined', 'U1'))).to.equal('Declined by <@U1>');
    expect(textOf(buildApprovalReceipt('declined', 'U1', 'use OPS-2'))).to.equal('Declined by <@U1>: use OPS-2');
    expect(textOf(buildApprovalReceipt('expired', 'U1'))).to.equal('Expired');
    expect(textOf(buildApprovalReceipt('answered', 'U1'))).to.equal('Already answered');
    expect(textOf(buildApprovalReceipt('sending', 'U1'))).to.equal('Sending your answer…');
  });
});

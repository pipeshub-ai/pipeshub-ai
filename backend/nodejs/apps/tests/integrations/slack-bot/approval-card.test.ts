import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildApprovalCard,
  buildApprovalReceipt,
  parseApprovalButtonValue,
  withoutWaitingLine,
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

const EXPIRES = `<!date^${EXPIRES_AT / 1000}^Expires at {time}|Expires at ${new Date(EXPIRES_AT).toUTCString()}>`;
const VALUE = JSON.stringify({ approvalId: 'ap-1', argsHash: 'h1', requesterSlackId: 'U1', expiresAt: EXPIRES_AT, teamId: 'T1' });
const actions = (approve: 'primary' | 'danger' = 'primary') => ({
  type: 'actions',
  block_id: 'approval_actions',
  elements: [
    { type: 'button', action_id: 'approval_allow', text: { type: 'plain_text', text: 'Approve' }, value: VALUE, style: approve },
    { type: 'button', action_id: 'approval_change', text: { type: 'plain_text', text: 'Suggest a change' }, value: VALUE },
    { type: 'button', action_id: 'approval_deny', text: { type: 'plain_text', text: 'Deny' }, value: VALUE },
  ],
});
const context = (block_id: string, ...texts: string[]) => ({
  type: 'context',
  block_id,
  elements: texts.map((text) => ({ type: 'mrkdwn', text })),
});

// The call from a live Slack card: Atlassian's MCP server, creating a Jira issue.
const JIRA: Partial<ToolApprovalDetails> = {
  serverName: 'Atlassian Rovo',
  toolName: 'createJiraIssue',
  toolTitle: 'Create issue',
  argsSummary: 'cloudId: "c7f37664-a798-44ef-a525-ecacfeb1087d", projectKey: "PP", issueTypeName: "Task", …',
  arguments: {
    cloudId: 'c7f37664-a798-44ef-a525-ecacfeb1087d',
    projectKey: 'PP',
    issueTypeName: 'Task',
    summary: 'Test Slack automation and tool approvals',
    description:
      'Test Slack automation workflows and verify tool approval behavior. Document test results, issues found, and any follow-up actions.',
  },
  inputSchema: {
    type: 'object',
    required: ['cloudId', 'projectKey', 'issueTypeName', 'summary'],
    properties: {
      cloudId: { type: 'string' },
      projectKey: { type: 'string' },
      issueTypeName: { type: 'string' },
      summary: { type: 'string' },
      description: { type: 'string' },
    },
  },
};

const EMAIL: Partial<ToolApprovalDetails> = {
  serverName: 'Gmail',
  toolName: 'send_email',
  toolTitle: 'Send email',
  arguments: { to: ['ana@example.com', 'ben@example.com'], cc: [], subject: 'Weekly update for the team', body: 'Hi all,\n\nThe update is attached.' },
  inputSchema: {
    type: 'object',
    required: ['to', 'subject', 'body'],
    properties: {
      to: { type: 'array', items: { type: 'string', format: 'email' } },
      cc: { type: 'array', items: { type: 'string', format: 'email' } },
      subject: { type: 'string' },
      body: { type: 'string' },
    },
  },
};

export const EXAMPLES = { JIRA, EMAIL };

describe('slack-bot/utils/approval-card', () => {
  it('reads the Jira call as its issue: the product in the header, the summary and description up front, the cloud id under Details', () => {
    const { text, blocks } = buildApprovalCard(approval(JIRA), 'U1', 'T1');
    expect(text).to.equal('Approval needed: Create issue in Jira');
    expect(blocks).to.deep.equal([
      { type: 'header', text: { type: 'plain_text', text: 'Create issue in Jira' } },
      context('approval_source', 'Atlassian Rovo'),
      {
        type: 'section',
        block_id: 'approval_document',
        text: {
          type: 'mrkdwn',
          text:
            '*Test Slack automation and tool approvals*\n' +
            'Test Slack automation workflows and verify tool approval behavior. Document test results, issues found, and any follow-up actions.',
        },
      },
      context('approval_meta', 'Project: PP · Issue type: Task'),
      context('approval_details', '*Details*', 'Cloud ID: c7f37664-a798-44ef-a525-ecacfeb1087d'),
      context('approval_status', `Waiting on <@U1> to answer · ${EXPIRES}`),
      actions(),
    ]);
  });

  it('reads an email as its message: who it goes to, the subject, the body; an empty cc says nothing', () => {
    const { text, blocks } = buildApprovalCard(approval(EMAIL), 'U1', 'T1');
    expect(text).to.equal('Approval needed: Send email in Gmail');
    expect(blocks).to.deep.equal([
      { type: 'header', text: { type: 'plain_text', text: 'Send email in Gmail' } },
      {
        type: 'section',
        block_id: 'approval_document',
        text: { type: 'mrkdwn', text: 'To: ana@example.com, ben@example.com\n*Weekly update for the team*\nHi all,\n\nThe update is attached.' },
      },
      context('approval_status', `Waiting on <@U1> to answer · ${EXPIRES}`),
      actions(),
    ]);
  });

  it('marks a destructive card: danger approve and "Can\'t be undone"', () => {
    const { blocks } = buildApprovalCard(approval({ kind: 'destructive' }), 'U1', 'T1') as { blocks: AnyBlock[] };
    expect(blocks.find((b) => b.block_id === 'approval_status').elements[0].text).to.equal(
      `Waiting on <@U1> to answer · ${EXPIRES} · Can't be undone`,
    );
    expect(actionsOf(blocks)[0].style).to.equal('danger');
  });

  it('every button carries the signed value the bot reads back', () => {
    for (const button of actionsOf(buildApprovalCard(approval(), 'U1', 'T1').blocks)) {
      expect(parseApprovalButtonValue(button.value)).to.deep.equal(JSON.parse(VALUE));
    }
  });

  it("titles the card with the tool's title, else its name, never the past-tense display name", () => {
    const titleOf = (fields: Partial<ToolApprovalDetails>) =>
      (buildApprovalCard(approval(fields), 'U1', 'T1').blocks[0] as AnyBlock).text.text as string;
    expect(titleOf({ displayName: 'Created an issue' })).to.equal('Create issue in Jira');
    expect(titleOf({ toolTitle: null, displayName: 'Created an issue' })).to.equal('Create issue in Jira');
    expect(titleOf({ toolTitle: '  ' })).to.equal('Create issue in Jira');
    // A tool that names its product isn't told where it is twice.
    expect(titleOf({ toolTitle: null, toolName: 'createConfluencePage', serverName: 'Atlassian' })).to.equal('Create Confluence Page');
  });

  it('labels by schema title first, then the name in sentence case, and keeps arguments the schema leaves out', () => {
    const { blocks } = buildApprovalCard(
      approval({
        arguments: { issueTypeName: 'Bug', priority_level: 'High', count: 2, notify: false, labels: ['a', 'b'] },
        inputSchema: { type: 'object', properties: { issueTypeName: { type: 'string', title: 'Work type' } } },
      }),
      'U1',
      'T1',
    ) as { blocks: AnyBlock[] };
    expect(blocks.find((b) => b.block_id === 'approval_meta').elements[0].text).to.equal(
      'Work type: Bug · Priority level: High · Count: 2 · Notify: No · Labels: a, b',
    );
  });

  it('puts ids and nested values under Details, and never drops them', () => {
    const { blocks } = buildApprovalCard(
      approval({ arguments: { title: 'Fix', site: 'c7f37664-a798-44ef-a525-ecacfeb1087d', accountId: 'abc', meta: { n: 1 } } }),
      'U1',
      'T1',
    ) as { blocks: AnyBlock[] };
    expect(blocks.find((b) => b.block_id === 'approval_details').elements.map((e: AnyBlock) => e.text)).to.deep.equal([
      '*Details*',
      'Site: c7f37664-a798-44ef-a525-ecacfeb1087d · Account ID: abc · Meta: {"n":1}',
    ]);
  });

  it('keeps tool text plain, cuts a long body and says so', () => {
    const { blocks } = buildApprovalCard(
      approval({ arguments: { title: '<!channel> *now*', description: 'x'.repeat(5000) } }),
      'U1',
      'T1',
    ) as { blocks: AnyBlock[] };
    const doc = blocks.find((b) => b.block_id === 'approval_document').text.text as string;
    expect(doc.startsWith('*&lt;!channel&gt; now*\n')).to.equal(true);
    expect(doc.length).to.be.at.most(3000);
    expect(doc.endsWith('...')).to.equal(true);
    expect(blocks.find((b) => b.block_id === 'approval_details').elements.map((e: AnyBlock) => e.text)).to.deep.equal([
      '_Open in PipesHub to see everything._',
    ]);
  });

  it('shows a Unix time under a time-like name as a Slack date', () => {
    const { blocks } = buildApprovalCard(approval({ arguments: { channel: 'C1', post_at: 1793000000 } }), 'U1', 'T1') as {
      blocks: AnyBlock[];
    };
    expect(blocks.find((b) => b.block_id === 'approval_meta').elements[0].text).to.equal(
      `Channel: C1 · Post at: <!date^1793000000^{date_short_pretty} {time}|${new Date(1793000000 * 1000).toUTCString()}>`,
    );
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

  it('keeps the notification text plain', () => {
    const { text } = buildApprovalCard(approval({ toolTitle: '<!channel> & co' }), 'U1', 'T1');
    expect(text).to.equal('Approval needed: &lt;!channel&gt; &amp; co in Jira');
  });

  it("drops the server's closing waiting line from the answer: the card says it", () => {
    expect(withoutWaitingLine('I will file it.\n\nWaiting for your approval to run createJiraIssue on Atlassian Rovo.')).to.equal('I will file it.\n\n');
    expect(withoutWaitingLine('Waiting for your approval to run create_issue on Jira.')).to.equal('');
    expect(withoutWaitingLine('Done.')).to.equal('Done.');
  });

  it('reads back only a well-formed button value', () => {
    expect(parseApprovalButtonValue('not json')).to.equal(null);
    expect(parseApprovalButtonValue(JSON.stringify({ approvalId: 'ap-1', argsHash: 'h1' }))).to.equal(null);
  });

  it('writes receipts in the status line the answer replaces', () => {
    expect(buildApprovalReceipt('allowed', 'U1')[0]).to.deep.include({ type: 'context', block_id: 'approval_status' });
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

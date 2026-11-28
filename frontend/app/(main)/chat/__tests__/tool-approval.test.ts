import { describe, expect, it } from 'vitest';
import {
  answeredToolApproval,
  answerWords,
  approvalArgumentLines,
  builtInToolKey,
  findApprovalStub,
  findToolApproval,
  hasApprovalCard,
  hasApprovedCall,
  isToolApprovalResumeQuery,
  settledToolApprovals,
  toolApprovalDetails,
  toolApprovalResumeQuery,
  waitingWords,
  waitsForApproval,
} from '../tool-approval';
import type { MessagePart } from '../types';

const WIRE = {
  approvalId: 'ap-1',
  instanceId: 'inst-1',
  serverName: 'Jira',
  toolName: 'create_issue',
  toolTitle: 'Create an issue',
  readOnly: false,
  expiresAt: 1_900_000_000_000,
  arguments: { title: 'Bug' },
};

function waiting(approval: unknown, overrides: Partial<MessagePart> = {}): MessagePart {
  return { type: 'tool_call', toolCallId: 'c', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval', approval: approval as MessagePart['approval'], ...overrides };
}

describe('toolApprovalDetails, a restricted card', () => {
  it('keeps its hosts, drops malformed ones, and never lets it set a rule', () => {
    const details = toolApprovalDetails({
      ...WIRE,
      canRemember: true,
      restricted: true,
      egress: 'publish',
      hosts: [{ host: 'xn--80ak6aa92e.com', unicode: 'аррӏе.com' }, { host: 'plain.example' }, { unicode: 'no-host' }, 'x', null],
    });
    expect(details).toMatchObject({
      restricted: true,
      canRemember: false,
      hosts: [
        { host: 'xn--80ak6aa92e.com', unicode: 'аррӏе.com' },
        { host: 'plain.example', unicode: 'plain.example' },
      ],
    });
  });

  it('is not restricted unless the server says so', () => {
    const details = toolApprovalDetails({ ...WIRE, restricted: 'yes', hosts: [{ host: 'a.example' }] });
    expect(details).not.toHaveProperty('restricted');
    expect(details).not.toHaveProperty('hosts');
  });
});

describe('toolApprovalDetails', () => {
  it('keeps a well-formed record', () => {
    expect(toolApprovalDetails(WIRE)).toEqual({ ...WIRE, kind: null, canRemember: true });
    expect(toolApprovalDetails({ ...WIRE, kind: 'destructive' })).toMatchObject({ kind: 'destructive' });
    expect(toolApprovalDetails({ ...WIRE, kind: 'wipe-everything' })).toMatchObject({ kind: null });
  });

  it.each([
    ['no approval id', { ...WIRE, approvalId: '' }],
    ['no server', { ...WIRE, serverName: undefined }],
    ['no expiry', { ...WIRE, expiresAt: '1900000000000' }],
    ['not an object', 'ap-1'],
    ['nothing', null],
  ])('drops a record with %s', (_label, value) => {
    expect(toolApprovalDetails(value)).toBeNull();
  });

  it('treats anything but true as false, and anything but an object as no arguments', () => {
    const details = toolApprovalDetails({ ...WIRE, readOnly: 1, arguments: ['x'] });
    expect(details).toMatchObject({ readOnly: false, arguments: null });
  });

  it("says whether \"Don't ask again\" can set a rule: not on a toolset's card, whose rules are its admin's", () => {
    expect(toolApprovalDetails({ ...WIRE, canRemember: false })).toMatchObject({ canRemember: false });
    // Cards saved before the flag was sent were MCP cards.
    expect(toolApprovalDetails({ ...WIRE, canRemember: 'no' })).toMatchObject({ canRemember: true });
  });

  it('keeps the preview of arguments too large to send', () => {
    const details = toolApprovalDetails({ ...WIRE, arguments: null, argumentsPreview: '{"body": "aaa…' });
    expect(details).toMatchObject({ arguments: null, argumentsPreview: '{"body": "aaa…' });
  });
});

describe('findToolApproval', () => {
  it('finds the waiting call at the top level', () => {
    expect(findToolApproval([{ type: 'text', content: 'hi' }, waiting(WIRE)])?.approvalId).toBe('ap-1');
  });

  it('finds one a sub-agent asked for', () => {
    const parts: MessagePart[] = [{ type: 'sub_agent', runId: 'child', parts: [waiting({ ...WIRE, approvalId: 'ap-child' })] }];
    expect(findToolApproval(parts)?.approvalId).toBe('ap-child');
  });

  it('ignores calls that are not waiting, or have nothing to show', () => {
    expect(findToolApproval([waiting(WIRE, { status: 'blocked' }), waiting(undefined)])).toBeNull();
    expect(findToolApproval(undefined)).toBeNull();
  });
});

describe('hasApprovedCall', () => {
  it('finds a call a person approved, at any depth', () => {
    expect(hasApprovedCall([{ type: 'text', content: 'x' }, { type: 'tool_call', toolCallId: 'a', approved: true }])).toBe(true);
    expect(hasApprovedCall([{ type: 'sub_agent', parts: [{ type: 'tool_call', toolCallId: 'a', approved: true }] }])).toBe(true);
    expect(hasApprovedCall([{ type: 'tool_call', toolCallId: 'b', status: 'completed' }])).toBe(false);
    expect(hasApprovedCall(undefined)).toBe(false);
  });
});

describe('answeredToolApproval', () => {
  it('finds a call an answered card settled, at any depth, whatever the answer', () => {
    expect(answeredToolApproval([{ type: 'tool_call', toolCallId: 'a', status: 'blocked', approvalAnswer: 'declined' }])).toBe(true);
    expect(answeredToolApproval([{ type: 'sub_agent', parts: [{ type: 'tool_call', toolCallId: 'a', approvalAnswer: 'expired' }] }])).toBe(true);
    expect(answeredToolApproval([waiting(WIRE)])).toBe(false);
    expect(answeredToolApproval(undefined)).toBe(false);
  });
});

describe('the answer query', () => {
  it('is the same prefix the server reads, and nothing else counts', () => {
    expect(toolApprovalResumeQuery('allow')).toBe('Tool approval: allow');
    expect(isToolApprovalResumeQuery(toolApprovalResumeQuery('deny'))).toBe(true);
    expect(isToolApprovalResumeQuery('  Tool approval: allow')).toBe(true);
    expect(isToolApprovalResumeQuery('Allow once: create_issue on Jira')).toBe(false);
    expect(isToolApprovalResumeQuery(undefined)).toBe(false);
  });
});

describe('approvalArgumentLines', () => {
  it('shows text as is, plain lists as a comma list, and cuts long ones', () => {
    expect(approvalArgumentLines({ title: 'Bug', labels: ['a', 'b'], n: 3, body: 'x'.repeat(400) })).toEqual([
      { name: 'title', value: 'Bug', cut: false },
      { name: 'labels', value: 'a, b', cut: false },
      { name: 'n', value: '3', cut: false },
      { name: 'body', value: `${'x'.repeat(300)}…`, cut: true },
    ]);
  });

  it('keeps list items apart and typed: a comma, a blank, a number or an empty list shows as JSON', () => {
    expect(approvalArgumentLines({ to: ['admins, owners'] })[0]!.value).toBe('["admins, owners"]');
    expect(approvalArgumentLines({ to: ['admins', 'owners'] })[0]!.value).toBe('admins, owners');
    expect(approvalArgumentLines({ to: ['a', ''] })[0]!.value).toBe('["a",""]');
    expect(approvalArgumentLines({ to: ['a', null] })[0]!.value).toBe('["a",null]');
    expect(approvalArgumentLines({ to: [] })[0]!.value).toBe('[]');
    expect(approvalArgumentLines({ to: [1, true] })[0]!.value).toBe('[1,true]');
    expect(approvalArgumentLines({ to: ['1', 'true'] })[0]!.value).toBe('1, true');
  });

  it('indents nested values as JSON', () => {
    const [line] = approvalArgumentLines({ columns: [{ name: 'Status', type: 'select' }] });
    expect(line!.value).toBe(JSON.stringify([{ name: 'Status', type: 'select' }], null, 2));
  });

  it('shows a Unix time under a time-like name as a local date', () => {
    const local = (ms: number) => new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    const lines = approvalArgumentLines({ post_at: 1793000000, sentAt: '1793000000000', count: 1793000000, channel: 'C1' });
    expect(lines).toEqual([
      { name: 'post_at', value: local(1793000000 * 1000), cut: false },
      { name: 'sentAt', value: local(1793000000000), cut: false },
      { name: 'count', value: '1793000000', cut: false },
      { name: 'channel', value: 'C1', cut: false },
    ]);
  });

  it('shows every value in full when asked', () => {
    expect(approvalArgumentLines({ body: 'x'.repeat(400) }, { full: true })).toEqual([{ name: 'body', value: 'x'.repeat(400), cut: false }]);
  });
});

describe('a built-in tool, which has no server', () => {
  const t = ((key: string) => `<${key}>`) as unknown as Parameters<typeof waitingWords>[1];
  const OPEN = 'chat.toolApproval.builtIn.openWebPage';

  it.each([
    ['dynamic__fetch_url', 'Open a web page', OPEN],
    // As a card saved before the server had words for it: the tool's name, or its label, where a server's name goes.
    ['dynamic__fetch_url', 'dynamic__fetch_url', OPEN],
    ['run_code', 'Run code', 'chat.toolApproval.builtIn.runCode'],
    ['run_code', 'Ran code', 'chat.toolApproval.builtIn.runCode'],
    ['install_packages', 'Install code packages', 'chat.toolApproval.builtIn.installPackages'],
    ['install_packages', 'Installed packages', 'chat.toolApproval.builtIn.installPackages'],
  ])('%s of "%s" has words of its own', (tool, server, key) => {
    expect(builtInToolKey(tool, server)).toBe(key);
  });

  it.each([
    ['run_code', 'E2B'],
    ['fetch_url', 'fetch_url'],
    ['fetch', 'fetch'],
    ['create_issue', 'Jira'],
    ['constructor', 'constructor'],
    ['dynamic__web_search', 'dynamic__web_search'],
  ])('a tool of a server keeps its names: %s of "%s"', (tool, server) => {
    expect(builtInToolKey(tool, server)).toBeUndefined();
  });

  it('is in words on a receipt, with no server, whatever its card holds', () => {
    const allowed = (approval: unknown) =>
      ({ type: 'tool_call', toolCallId: 'c1', toolName: 'dynamic__fetch_url', status: 'completed', approvalAnswer: 'allowed', approval }) as MessagePart;
    const old = { ...WIRE, instanceId: '', serverName: 'dynamic__fetch_url', toolName: 'dynamic__fetch_url', toolTitle: null };
    for (const approval of [old, { ...old, serverName: 'Open a web page', toolTitle: 'Open a web page' }, undefined]) {
      expect(settledToolApprovals([allowed(approval)])).toMatchObject([{ toolKey: OPEN, server: '', answer: 'allowed' }]);
    }
    expect(settledToolApprovals([{ ...allowed(WIRE), toolName: 'mcp_jira_create_issue' }])).toEqual([
      { key: 'c1', tool: 'Create an issue', server: 'Jira', answer: 'allowed' },
    ]);
  });

  it('is in words in the line an earlier server saved, and no other line changes', () => {
    expect(waitingWords('Waiting for your approval to run dynamic__fetch_url on dynamic__fetch_url.', t)).toBe(
      `Waiting for your approval to run “<${OPEN}>”.`,
    );
    for (const line of [
      'Waiting for your approval to run “Open a web page”.',
      'Waiting for your approval to run create_issue on Jira.',
      'Waiting for your approval to run run_code on E2B.',
      'Anything else',
      '',
    ]) {
      expect(waitingWords(line, t)).toBe(line);
    }
  });

  it("still leaves an answer's own words without the line", () => {
    const card = waiting({ ...WIRE, serverName: 'Open a web page', toolName: 'dynamic__fetch_url', expiresAt: 1 });
    const line = 'Waiting for your approval to run “Open a web page”.';
    expect(answerWords({ answer: `I will open it.\n\n${line}`, persistedParts: [card] })).toBe('I will open it.');
  });
});

describe('a card the fork settled as expired', () => {
  const forked = (): MessagePart[] => [
    { type: 'sub_agent', runId: 'child', parts: [waiting(WIRE, { status: 'blocked', approvalAnswer: 'expired', resultPreview: 'The approval request expired.' })] },
  ];

  it('is no longer a card to answer, and counts as answered', () => {
    expect(findToolApproval(forked())).toBeNull();
    expect(answeredToolApproval(forked())).toBe(true);
  });

  it('shows up as an expired receipt for its tool', () => {
    expect(settledToolApprovals(forked())).toEqual([
      { key: 'c', tool: 'Create an issue', server: 'Jira', answer: 'expired' },
    ]);
  });
});

describe('findApprovalStub', () => {
  const stub = (status: string, over: Record<string, unknown> = {}) =>
    ({ type: 'tool_call', toolCallId: 'c1', stub: true, status, approval: WIRE, ...over }) as unknown as MessagePart;

  it.each(['approved', 'not_run', 'denied', 'expired'] as const)('reads how a %s card of someone else stands', (status) => {
    expect(findApprovalStub([{ type: 'text', content: 'hi' }, stub(status)])).toEqual({ status, details: expect.objectContaining({ toolName: 'create_issue', serverName: 'Jira' }) });
  });

  it('is nothing for a card that still waits (the waiting card draws it), or a call that is not a stub', () => {
    expect(findApprovalStub([stub('awaiting_approval')])).toBeNull();
    expect(findApprovalStub([stub('approved', { stub: undefined })])).toBeNull();
    expect(findApprovalStub([stub('made_up')])).toBeNull();
  });
});

describe('settledToolApprovals, an approved call refused for lost access', () => {
  it('says why it did not run', () => {
    const refused = { type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'blocked', approvalAnswer: 'allowed', approvalReason: 'access_lost', approval: WIRE } as MessagePart;
    expect(settledToolApprovals([refused]).map((s) => s.answer)).toEqual(['accessLost']);
  });
});

describe("an answer's own words, when its run stopped on a card", () => {
  const LINE = 'Waiting for your approval to run Create an issue on Jira.';
  const OWN = 'I will file it under OPS.';
  const card = (overrides: Partial<MessagePart> = {}) => waiting(WIRE, overrides);
  const subAgent: MessagePart = { type: 'sub_agent', parts: [card()] } as MessagePart;
  const expired = waiting({ ...WIRE, expiresAt: 1 });
  const stub = card({ stub: true, status: 'approved' } as Partial<MessagePart>);
  const answered = card({ status: 'completed', approvalAnswer: 'allowed' });

  it.each([
    ['its card', [card()], true, true],
    ["a sub-agent's card", [subAgent], true, true],
    ['a card past its expiry', [expired], false, true],
    ['an answered card', [answered], false, true],
    ["someone else's card, answered", [stub], false, true],
    ['a question card', [{ type: 'tool_call', toolCallId: 'q', toolName: 'ask_user_question', status: 'awaiting_answer' } as unknown as MessagePart], false, false],
    ['no parts', undefined, false, false],
  ] as const)('%s', (_label, parts, waits, hasCard) => {
    expect(waitsForApproval(parts as MessagePart[] | undefined)).toBe(waits);
    expect(hasApprovalCard(parts as MessagePart[] | undefined)).toBe(hasCard);
  });

  it('has none while it waits, whatever it said before', () => {
    expect(answerWords({ answer: LINE, persistedParts: [card()] })).toBe('');
    expect(answerWords({ answer: `${OWN}\n\n${LINE}`, persistedParts: [card()] })).toBe('');
  });

  it('answered or expired, has what it said, and never the line that speaks to the asker alone', () => {
    for (const parts of [[answered], [expired], [stub]]) {
      expect(answerWords({ answer: LINE, persistedParts: parts })).toBe('');
      expect(answerWords({ answer: `${OWN}\n\n${LINE}`, persistedParts: parts })).toBe(OWN);
    }
  });

  it('keeps every word of an answer with no card', () => {
    expect(answerWords({ answer: `It said: ${LINE}` })).toBe(`It said: ${LINE}`);
    expect(answerWords({ answer: '' })).toBe('');
  });
});

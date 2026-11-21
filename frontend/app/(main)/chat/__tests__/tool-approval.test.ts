import { describe, expect, it } from 'vitest';
import {
  answeredToolApproval,
  approvalValueText,
  clipValue,
  findToolApproval,
  hasApprovedCall,
  isToolApprovalResumeQuery,
  settledToolApprovals,
  toolApprovalDetails,
  toolApprovalResumeQuery,
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

describe('approvalValueText', () => {
  const v = approvalValueText;
  it('shows text as is and plain lists as a comma list', () => {
    expect([v('title', 'Bug'), v('labels', ['a', 'b']), v('n', 3)]).toEqual(['Bug', 'a, b', '3']);
  });

  it('keeps list items apart and typed: a comma, a blank, a number or an empty list shows as JSON', () => {
    expect(v('to', ['admins, owners'])).toBe('["admins, owners"]');
    expect(v('to', ['admins', 'owners'])).toBe('admins, owners');
    expect(v('to', ['a', ''])).toBe('["a",""]');
    expect(v('to', ['a', null])).toBe('["a",null]');
    expect(v('to', [])).toBe('[]');
    expect(v('to', [1, true])).toBe('[1,true]');
    expect(v('to', ['1', 'true'])).toBe('1, true');
  });

  it('indents nested values as JSON', () => {
    expect(v('columns', [{ name: 'Status', type: 'select' }])).toBe(JSON.stringify([{ name: 'Status', type: 'select' }], null, 2));
  });

  it('shows a Unix time under a time-like name as a local date', () => {
    const local = (ms: number) => new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    expect([v('post_at', 1793000000), v('sentAt', '1793000000000'), v('count', 1793000000), v('channel', 'C1')]).toEqual([
      local(1793000000 * 1000), local(1793000000000), '1793000000', 'C1',
    ]);
  });

  it('cuts a long value unless shown in full', () => {
    expect(clipValue('x'.repeat(400), false)).toEqual({ text: `${'x'.repeat(300)}…`, cut: true });
    expect(clipValue('x'.repeat(400), true)).toEqual({ text: 'x'.repeat(400), cut: false });
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

describe('toolApprovalDetails edit fields', () => {
  const schema = { type: 'object', properties: { a: { type: 'string' } } };
  it('keeps the new fields', () => {
    const editErrors = [{ path: '/a', message: 'bad' }];
    expect(
      toolApprovalDetails({
        ...WIRE,
        toolPath: 'jira.create',
        source: 'toolset',
        inputSchema: schema,
        argsHash: 'h1',
        editable: true,
        argsSummary: 'sum',
        displayName: 'Create issue',
        editErrors,
      })
    ).toMatchObject({ toolPath: 'jira.create', source: 'toolset', inputSchema: schema, argsHash: 'h1', editable: true, argsSummary: 'sum', displayName: 'Create issue', editErrors });
  });
  it('drops malformed ones', () => {
    const d = toolApprovalDetails({
      ...WIRE,
      source: 'other',
      inputSchema: 'x',
      editable: 'yes',
      argsHash: 5,
      editErrors: [{ path: '/a', message: 'ok' }, { path: 1 }, 'x'],
    });
    expect(d).not.toHaveProperty('source');
    expect(d).not.toHaveProperty('inputSchema');
    expect(d).not.toHaveProperty('editable');
    expect(d).not.toHaveProperty('argsHash');
    expect(d?.editErrors).toEqual([{ path: '/a', message: 'ok' }]);
  });
});

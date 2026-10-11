import { describe, expect, it } from 'vitest';
import { approvalArgumentLines, findToolApproval, hasApprovedCall, toolApprovalDetails } from '../tool-approval';
import type { MessagePart } from '../types';

const WIRE = {
  approvalId: 'ap-1',
  instanceId: 'inst-1',
  serverName: 'Jira',
  toolName: 'create_issue',
  toolTitle: 'Create an issue',
  readOnly: false,
  canAlwaysAllow: true,
  expiresAt: 1_900_000_000_000,
  arguments: { title: 'Bug' },
};

function waiting(approval: unknown, overrides: Partial<MessagePart> = {}): MessagePart {
  return { type: 'tool_call', toolCallId: 'c', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval', approval: approval as MessagePart['approval'], ...overrides };
}

describe('toolApprovalDetails', () => {
  it('keeps a well-formed record', () => {
    expect(toolApprovalDetails(WIRE)).toEqual({ ...WIRE, kind: null, companyAlwaysAsk: false });
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
    const details = toolApprovalDetails({ ...WIRE, canAlwaysAllow: 'yes', readOnly: 1, arguments: ['x'] });
    expect(details).toMatchObject({ canAlwaysAllow: false, readOnly: false, arguments: null });
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

describe('approvalArgumentLines', () => {
  it('shows text as is, other values as JSON, and cuts long ones', () => {
    expect(approvalArgumentLines({ title: 'Bug', labels: ['a', 'b'], n: 3, body: 'x'.repeat(400) })).toEqual([
      { name: 'title', value: 'Bug', cut: false },
      { name: 'labels', value: '["a","b"]', cut: false },
      { name: 'n', value: '3', cut: false },
      { name: 'body', value: `${'x'.repeat(300)}…`, cut: true },
    ]);
  });

  it('shows every value in full when asked', () => {
    expect(approvalArgumentLines({ body: 'x'.repeat(400) }, { full: true })).toEqual([{ name: 'body', value: 'x'.repeat(400), cut: false }]);
  });
});

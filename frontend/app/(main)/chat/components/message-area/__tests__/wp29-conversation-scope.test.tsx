/**
 * AP-04 (C3.75): "Allow for this conversation" says in words what it covers (this destination only; anything else
 * still asks), and the receipt says what later calls run without asking.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import type { ApprovalReason, PendingApproval } from '../../../types';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const REASON: ApprovalReason = {
  rule: 'flow_external_write',
  sensitivity: 'private',
  sources: [],
  moreSources: 0,
  destination: '#sales',
  conversationGrant: true,
};

function approval(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    interactionId: 'ix.approve.t2.0',
    toolCallId: 'call-2',
    toolName: 'slack__send_message',
    args: JSON.stringify({ channel: 'sales', message: 'Q3 summary' }),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    reason: REASON,
    ...overrides,
  };
}

const card = (props: Partial<React.ComponentProps<typeof ApprovalCard>>) =>
  render(h(Theme, null, h(ApprovalCard, { approval: approval(), onDecide: vi.fn(), conversationChoice: true, ...props })));

describe('AP-04: the conversation grant names its scope', () => {
  it('AP-04: when offered, the card says the grant covers this destination only', () => {
    card({});
    expect(screen.getByTestId('approval-conversation-scope').textContent).toBe(
      'Allowing it for this conversation covers #sales only. Anything else still asks.',
    );
  });

  it('AP-04: no scope line when the grant is not offered', () => {
    card({ approval: approval({ reason: { ...REASON, conversationGrant: false } }) });
    expect(screen.queryByTestId('approval-conversation-scope')).toBeNull();
  });

  it('AP-04: the receipt says later calls to the destination run without asking', () => {
    card({ approval: approval({ status: 'approved', scope: 'conversation' }), onDecide: undefined });
    expect(screen.getByTestId('approval-conversation-scope').textContent).toBe(
      'Later calls to #sales in this conversation run without asking.',
    );
  });

  it('AP-04: an approval once carries no scope line', () => {
    card({ approval: approval({ status: 'approved' }), onDecide: undefined });
    expect(screen.queryByTestId('approval-conversation-scope')).toBeNull();
  });
});

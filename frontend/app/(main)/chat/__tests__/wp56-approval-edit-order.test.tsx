/**
 * WP-56 (AP-06, C3.75): the edit card follows the tool schema's property order, which the worker sends with its edit
 * offer (`fieldOrder`); the card has no schema of its own.
 *
 * WP56-ORDER-6 the reason keeps the order only with the offer, and only names it can read.
 * WP56-ORDER-7 the card lists the values in that order, not the call's.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { toApprovalReason } from '../approval-preview';
import { ApprovalCard } from '../components/message-area/approval-card';
import type { PendingApproval } from '../types';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const REASON = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };

describe('WP-56 the edit card follows the tool’s own order', () => {
  it('WP56-ORDER-6: the reason keeps the field order only with the edit offer, and only readable names', () => {
    expect(toApprovalReason({ ...REASON, editable: true, fieldOrder: ['to', 'subject', 'body'] })?.fieldOrder).toEqual([
      'to',
      'subject',
      'body',
    ]);
    expect(toApprovalReason({ ...REASON, fieldOrder: ['to'] })).not.toHaveProperty('fieldOrder');
    expect(toApprovalReason({ ...REASON, editable: true, fieldOrder: ['to', 3, '', 'body'] })?.fieldOrder).toEqual(['to', 'body']);
    expect(toApprovalReason({ ...REASON, editable: true, fieldOrder: 'to' })).not.toHaveProperty('fieldOrder');
  });

  it('WP56-ORDER-7: the card lists the values in the schema’s order, not the call’s', () => {
    const approval: PendingApproval = {
      interactionId: 'ix.approve.t1.0',
      toolCallId: 'call-1',
      toolName: 'gmail__send_email',
      args: JSON.stringify({ body: 'Hello all', subject: 'Q3 results', to: 'all-staff@acme.com' }),
      argsSummary: null,
      reason: { ...REASON, editable: true, fieldOrder: ['to', 'subject', 'body'] },
      status: 'pending',
      errorCode: null,
    };
    render(
      <Theme>
        <ApprovalCard approval={approval} onDecide={vi.fn()} editAllowed onApproveEdited={vi.fn()} />
      </Theme>,
    );
    fireEvent.click(screen.getByTestId('approval-edit'));
    const form = screen.getByTestId('approval-edit-form');
    const values = [...form.querySelectorAll('input,textarea')].map((el) => (el as HTMLInputElement).value);
    expect(values).toEqual(['all-staff@acme.com', 'Q3 results', 'Hello all']);
  });
});

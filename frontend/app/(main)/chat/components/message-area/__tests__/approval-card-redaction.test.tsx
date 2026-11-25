/**
 * QA-11 QA11-3 (FEC-09, APR-26): whatever card holds a call, no entry of the shared
 * token vector reaches the screen: the flow and engine cards, the plain card a rule
 * without an explanation gets (and the admin queue shows), its summary line, the
 * reason's destination and source titles, and the full text behind "Show all".
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { PREVIEW_CHARS } from '../../../approval-preview';
import type { ApprovalReason, PendingApproval } from '../../../types';
import { VECTOR_EXAMPLES, VECTOR_MASK } from '../../../__tests__/token-vector';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

function reasonFor(rule: string, secret: string): ApprovalReason {
  return {
    rule,
    sensitivity: 'private',
    sources: [{ title: `Runbook ${secret}`, recordId: 'rec-1' }],
    moreSources: 0,
    destination: `#ops ${secret}`,
    conversationGrant: true,
  };
}

function heldCall(secret: string, reason?: ApprovalReason): PendingApproval {
  return {
    interactionId: 'ix.approve.t9.0',
    toolCallId: 'call-9',
    toolName: 'slack__send_message',
    args: JSON.stringify({
      message: `${'x'.repeat(PREVIEW_CHARS)} ${secret}`,
      channel: 'ops',
      headers: { note: `use ${secret}` },
      recipients: ['a@example.com', secret],
    }),
    argsSummary: `Send ${secret}`,
    status: 'pending',
    errorCode: null,
    ...(reason ? { reason } : {}),
  };
}

/** The card as a person or a screen reader gets it: text, and every attribute. */
function onScreen(container: HTMLElement): string {
  const attributes = [...container.querySelectorAll('*')].flatMap((el) => [...el.attributes].map((a) => a.value));
  return [container.textContent ?? '', ...attributes].join('\n');
}

const CASES = VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const);

describe('QA-11 QA11-3: no card shows a secret from the vector', () => {
  it.each(CASES)('the plain card, pending and decided (the admin queue), masks %s', (_id, secret) => {
    for (const props of [{ onDecide: () => {} }, { authority: { approve: false, deny: true }, onDecide: () => {} }, {}]) {
      const { container, unmount } = render(h(Theme, null, h(ApprovalCard, { approval: heldCall(secret), ...props })));
      expect(onScreen(container)).not.toContain(secret);
      expect(screen.getByTestId('approval-preview').textContent).toContain(VECTOR_MASK);
      unmount();
    }
  });

  it.each(CASES)('the flow and engine cards mask %s, also behind "Show all"', (_id, secret) => {
    for (const rule of ['flow_external_write', 'engine_writes']) {
      const { container, unmount } = render(
        h(Theme, null, h(ApprovalCard, { approval: heldCall(secret, reasonFor(rule, secret)), onDecide: () => {}, conversationChoice: true })),
      );
      expect(onScreen(container)).not.toContain(secret);
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
      expect(onScreen(container)).not.toContain(secret);
      expect(container.textContent).toContain(VECTOR_MASK);
      unmount();
    }
  });
});

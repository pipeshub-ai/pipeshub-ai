/**
 * FLOWASK: a web search and a page fetch held after a private read can be allowed for the rest of the
 * conversation. The search names its provider ("DuckDuckGo") and the fetch its site ("pipeshub.com"); the card
 * says where the words go and offers the conversation-wide choice beside Allow once.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import type { ApprovalReason, PendingApproval } from '../../../types';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const reason = (destination: string): ApprovalReason => ({
  rule: 'flow_external_write',
  sensitivity: 'private',
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
  moreSources: 0,
  destination,
  conversationGrant: true,
});

function renderCard(toolName: string, args: Record<string, string>, destination: string) {
  const onDecide = vi.fn();
  const approval: PendingApproval = {
    interactionId: 'ix.approve.t3.0',
    toolCallId: 'call-3',
    toolName,
    args: JSON.stringify(args),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    reason: reason(destination),
  };
  render(h(Theme, null, h(ApprovalCard, { approval, onDecide, conversationChoice: true })));
  return onDecide;
}

describe('a web read can be allowed for the conversation (FLOWASK)', () => {
  it.each([
    ['dynamic__web_search', { query: 'pipeshub pricing' }, 'DuckDuckGo'],
    ['dynamic__fetch_url', { url: 'https://docs.pipeshub.com/introduction' }, 'pipeshub.com'],
  ])('%s names where its words go and offers the conversation-wide choice', (toolName, args, destination) => {
    const onDecide = renderCard(toolName, args, destination);
    expect(screen.getByTestId('approval-why').textContent).toContain(`This sends words to ${destination}.`);
    const allow = screen.getByRole('button', { name: new RegExp(`^Allow ${destination} for this conversation: `) });
    // WP-113: the button names the place it allows.
    expect(allow.textContent).toBe(`Allow ${destination} for this conversation`);
    fireEvent.click(allow);
    expect(onDecide.mock.calls).toEqual([['approved', 'conversation']]);
  });
});

/**
 * UX2 EA-10: an engine run's held write gets the same card as the durable
 * loop's: the action and where it goes, why it asks (engine runs ask before
 * a write that leaves the user's own space, UX4 EC-1), what would be sent with secrets
 * hidden, the sources when it read any, and "Allow for this conversation"
 * only when the run offered it.
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

const ENGINE: ApprovalReason = {
  rule: 'engine_writes',
  sensitivity: 'public',
  sources: [],
  moreSources: 0,
  destination: 'acme/web',
  conversationGrant: true,
};

function approval(reason: ApprovalReason | undefined): PendingApproval {
  return {
    interactionId: 'ix.approve.kabc',
    toolCallId: 'ix.approve.kabc',
    toolName: 'github__open_pull_request',
    args: JSON.stringify({ repo: 'acme/web', title: 'Fix the login page', body: 'Closes #12', token: 'ghp_abcdefghijklmnopqrstuvwxyz0123456789' }),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    ...(reason !== undefined ? { reason } : {}),
  };
}

function renderCard(reason: ApprovalReason | undefined, conversationChoice = true) {
  const onDecide = vi.fn();
  render(h(Theme, null, h(ApprovalCard, { approval: approval(reason), onDecide, conversationChoice })));
  return onDecide;
}

describe('ApprovalCard for an engine run (UX2 EA-10)', () => {
  it('names the action and the destination, and says why engine runs ask', () => {
    renderCard(ENGINE);
    expect(screen.getByRole('region', { name: 'Open pull request to acme/web in GitHub?' })).toBeTruthy();
    expect(screen.getByTestId('approval-why').textContent).toBe(
      'The coding agent asks before each change that would reach other people or systems outside your own space. Nothing is sent until you decide.',
    );
  });

  it('shows what would be sent with secrets hidden, and no sources when it read none', () => {
    renderCard(ENGINE);
    const preview = screen.getByTestId('approval-preview').textContent ?? '';
    expect(preview).toContain('Fix the login page');
    expect(preview).not.toContain('ghp_abcdefghijklmnopqrstuvwxyz0123456789');
    expect(screen.queryByTestId('approval-sources')).toBeNull();
  });

  it('names what it read earlier, when it read something', () => {
    renderCard({ ...ENGINE, sensitivity: 'private', sources: [{ title: 'Release checklist', recordId: 'rec-9' }] });
    const why = screen.getByTestId('approval-why').textContent ?? '';
    expect(why).toContain('outside your own space');
    expect(why).toContain('earlier in this conversation it read content');
    expect(screen.getByRole('link', { name: 'Release checklist' }).getAttribute('href')).toContain('rec-9');
  });

  it('offers "Allow for this conversation" only when the run offered it, and posts the scope', () => {
    const onDecide = renderCard(ENGINE);
    fireEvent.click(screen.getByRole('button', { name: /for this conversation: / }));
    expect(onDecide).toHaveBeenCalledWith('approved', 'conversation');
    cleanup();
    renderCard({ ...ENGINE, conversationGrant: false });
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
    expect(screen.getByRole('button', { name: /^Allow once: / })).toBeTruthy();
  });

  it('an approval without a reason (an older worker) keeps the plain card', () => {
    renderCard(undefined);
    expect(screen.getByRole('region', { name: 'Open pull request in GitHub?' }).textContent).toContain('What will be sent');
  });
});

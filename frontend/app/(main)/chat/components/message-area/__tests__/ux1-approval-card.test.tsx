/**
 * UX1 CA-1, CA-2, CA-4, CA-5: a call the flow guard holds. The card names the
 * action and where it goes, says why it asks, shows what would be sent with
 * secrets hidden, names the sources by title with links, and offers Deny,
 * Allow once and, only when offered, Allow for this conversation. Nothing is
 * chosen for the person.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
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
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }, { url: 'https://example.com/live' }],
  moreSources: 3,
  destination: '#sales',
  conversationGrant: true,
};

function approval(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    interactionId: 'ix.approve.t2.0',
    toolCallId: 'call-2',
    toolName: 'slack__send_message',
    args: JSON.stringify({ channel: 'sales', message: 'Contoso is the Q3 target', token: 'xoxb-1234567890-abcdef' }),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    reason: REASON,
    ...overrides,
  };
}

function renderCard(props: Partial<React.ComponentProps<typeof ApprovalCard>> = {}) {
  const onDecide = vi.fn();
  const view = render(
    h(Theme, null, h(ApprovalCard, { approval: approval(), onDecide, conversationChoice: true, ...props })),
  );
  return { ...view, onDecide };
}

describe('ApprovalCard for the flow guard (UX1)', () => {
  it('CA-1: the headline says the action, the destination and the app', () => {
    renderCard();
    expect(screen.getByRole('region', { name: 'Send message to #sales in Slack?' })).toBeTruthy();
  });

  it('CA-1: without a destination the headline names the action and the app', () => {
    renderCard({ approval: approval({ reason: { ...REASON, destination: undefined, conversationGrant: false } }) });
    expect(screen.getByRole('region', { name: 'Send message in Slack?' })).toBeTruthy();
  });

  it('CA-2: it says why it asks, and the region is described by it', () => {
    renderCard();
    const why = screen.getByTestId('approval-why');
    expect(why.textContent).toContain('read private content');
    // FLOWTRUST: a search asks too, and the card says that its words are what would leave.
    expect(why.textContent).toContain('it asks before it sends anything outside your organization');
    expect(why.textContent).toContain('Nothing is sent until you decide');
    expect(screen.getByRole('region').getAttribute('aria-describedby')).toBe(why.id);
  });

  it('CA-2: unknown content is named as such', () => {
    renderCard({ approval: approval({ reason: { ...REASON, sensitivity: 'unknown' } }) });
    const why = screen.getByTestId('approval-why').textContent;
    expect(why).toContain('connected app or an outside tool whose origin cannot be checked');
    expect(why).toContain('it asks before it sends anything outside your organization');
  });

  it('CA-3: what will be sent comes first, with secrets hidden, as plain text', () => {
    const { container } = renderCard({
      approval: approval({ args: JSON.stringify({ channel: 'sales', message: '<b>hi</b>', token: 'xoxb-1234567890-abcdef' }) }),
    });
    const preview = screen.getByTestId('approval-preview');
    const values = within(preview).getAllByTestId('approval-preview-value').map((v) => v.textContent);
    expect(values[0]).toBe('<b>hi</b>');
    expect(preview.textContent).not.toContain('xoxb-1234567890-abcdef');
    expect(preview.textContent).toContain('hidden here');
    expect(container.querySelector('b')).toBeNull();
  });

  it('CA-3: a long message is cut, with Show all', () => {
    renderCard({ approval: approval({ args: JSON.stringify({ message: 'y'.repeat(400) }) }) });
    const toggle = screen.getByRole('button', { name: 'Show all' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(screen.getByTestId('approval-preview-value').textContent).toBe('y'.repeat(400));
  });

  it('CA-4: sources are named by title and linked; never an id; the rest counted', () => {
    renderCard();
    const list = screen.getByTestId('approval-sources');
    const record = within(list).getByRole('link', { name: 'Q3 plan' });
    expect(record.getAttribute('href')).toBe('/record/rec-1/');
    const live = within(list).getByRole('link', { name: 'Untitled source' });
    expect(live.getAttribute('href')).toBe('https://example.com/live');
    expect(live.getAttribute('rel')).toBe('noopener noreferrer');
    expect(list.textContent).toContain('and 3 more');
    expect(list.textContent).not.toContain('rec-1');
  });

  it('CA-5: Deny, Allow for this conversation and Allow once, each labelled; nothing focused or chosen', () => {
    const { onDecide } = renderCard();
    const buttons = screen.getAllByRole('button').filter((b) => b.textContent !== 'Show all');
    expect(buttons.map((b) => b.textContent)).toEqual(['Deny', 'Allow #sales for this conversation', 'Allow once']);
    expect(buttons.every((b) => b.tagName === 'BUTTON' && b.tabIndex >= 0)).toBe(true);
    expect(document.activeElement).toBe(document.body);
    expect(onDecide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Allow #sales for this conversation: Send message' }));
    fireEvent.click(screen.getByRole('button', { name: 'Allow once: Send message' }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny Send message: nothing is sent' }));
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecide.mock.calls).toEqual([['approved', 'conversation'], ['approved'], ['denied']]);
  });

  it("R38-AUTHZ (ADR-GRANT-01 D9): an approver deciding another person's run is offered Allow once, never for the conversation", () => {
    renderCard({ authority: { approve: true, deny: true, edit: false } });
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
    expect(screen.getByRole('button', { name: 'Allow once: Send message' })).toBeTruthy();
    cleanup();
    renderCard({ authority: { approve: true, deny: true, edit: true } });
    expect(screen.getByRole('button', { name: 'Allow #sales for this conversation: Send message' })).toBeTruthy();
  });

  it('CA-5: no conversation choice when the run does not offer it, outside the chat, or for a viewer who may only deny', () => {
    renderCard({ approval: approval({ reason: { ...REASON, conversationGrant: false } }) });
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
    cleanup();
    renderCard({ conversationChoice: false });
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
    cleanup();
    renderCard({ authority: { approve: false, deny: true } });
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Deny']);
  });

  it('decided for the conversation, it says so; expired, it says nothing was sent', () => {
    renderCard({ approval: approval({ status: 'approved', scope: 'conversation' }), onDecide: undefined });
    // The badge, and the live region that announces it.
    expect(screen.getAllByText('Allowed for this conversation')).toHaveLength(2);
    cleanup();
    renderCard({ approval: approval({ status: 'expired' }) });
    expect(screen.getByText('Nobody decided in time, so nothing was sent.')).toBeTruthy();
  });

  it('a refused conversation grant is explained', () => {
    renderCard({ approval: approval({ errorCode: 'HTTP_AGENT_HARNESS_CONVERSATION_GRANT_NOT_OFFERED' }) });
    expect(screen.getByRole('alert').textContent).toContain('only be allowed once');
  });
});

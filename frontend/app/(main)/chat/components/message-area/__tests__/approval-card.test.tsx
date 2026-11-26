import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import type { PendingApproval } from '../../../types';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

function approval(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    interactionId: 'ix.approve.t1.0',
    toolCallId: 'call-1',
    toolName: 'slack__post_message',
    displayName: 'Post to Slack',
    args: '{"channel":"#eng","text":"<b>ship it</b> <img src=x onerror=alert(1)>"}',
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    ...overrides,
  };
}

function renderCard(props: Partial<React.ComponentProps<typeof ApprovalCard>> = {}) {
  const onDecide = vi.fn();
  const view = render(h(Theme, null, h(ApprovalCard, { approval: approval(), onDecide, ...props })));
  return { ...view, onDecide: props.onDecide ?? onDecide };
}

describe('ApprovalCard (P2.6c UI-1..UI-3)', () => {
  it('UI-1: a labelled region names the tool and shows its arguments as plain text (UXINT-3: labelled values)', () => {
    const { container } = renderCard();
    const region = screen.getByRole('region', { name: 'Post to Slack?' });
    expect(region).toBeTruthy();
    const args = screen.getByTestId('approval-preview');
    expect(args.textContent).toContain('Channel#eng');
    expect(args.textContent).toContain('<b>ship it</b>');
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
  });

  it('UI-1: arguments that are not JSON are shown exactly as journaled', () => {
    renderCard({ approval: approval({ args: 'not json {' }) });
    expect(screen.getByTestId('approval-preview').textContent).toBe('not json {');
  });

  it('UI-2: approve and deny are labelled buttons that send the decision', () => {
    const { onDecide } = renderCard();
    fireEvent.click(screen.getByRole('button', { name: 'Allow once: Post to Slack' }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny Post to Slack: nothing is sent' }));
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecide.mock.calls).toEqual([['approved'], ['denied']]);
  });

  it('UI-2: both buttons are reachable by keyboard (native buttons, in tab order)', () => {
    renderCard();
    const buttons = screen.getAllByRole('button');
    expect(buttons.map((b) => b.tagName)).toEqual(['BUTTON', 'BUTTON']);
    for (const button of buttons) {
      expect(button.getAttribute('tabindex')).not.toBe('-1');
      button.focus();
      expect(document.activeElement).toBe(button);
    }
  });

  it('UI-2: while sending, the buttons are disabled and the state is announced', () => {
    renderCard({ approval: approval({ status: 'sending' }) });
    for (const button of screen.getAllByRole('button')) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
    expect(screen.getAllByText('Sending your decision…').length).toBeGreaterThan(0);
  });

  it('UI-3: a decided card shows the decision and no buttons', () => {
    renderCard({ approval: approval({ status: 'approved' }) });
    expect(screen.queryAllByRole('button')).toEqual([]);
    expect(screen.getAllByText('Approved').length).toBeGreaterThan(0);
    cleanup();
    renderCard({ approval: approval({ status: 'closed' }) });
    expect(screen.getAllByText('Closed').length).toBeGreaterThan(0);
  });

  it('UI-3: a refusal says why and keeps the buttons', () => {
    renderCard({ approval: approval({ errorCode: 'HTTP_AGENT_HARNESS_APPROVER_REQUIRED' }) });
    expect(screen.getByRole('alert').textContent).toBe(
      'Only the person this task runs for, or an approver, can approve it.',
    );
    expect(screen.getAllByRole('button')).toHaveLength(2);
    cleanup();
    renderCard({ approval: approval({ errorCode: 'HTTP_SOMETHING_ELSE' }) });
    expect(screen.getByRole('alert').textContent).toBe('Your decision did not reach the server. Check your connection, then choose again.');
  });
});

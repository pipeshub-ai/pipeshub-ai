/**
 * UX3 UE-9, CA-5, SE-6: the unknown-effect card, a sub-agent's request in the
 * main thread, and the memory chip.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { useUserStore } from '@/lib/store/user-store';
import { ApprovalCard } from '../approval-card';
import { MemoryChip } from '../memory-chip';
import { UnknownEffectCard } from '../unknown-effect-card';
import type { MessagePart, PendingApproval } from '../../../types';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  useUserStore.setState({ profile: null } as never);
});

const effect = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: 'ix.unknown.t1.0',
  toolCallId: 'call-1',
  toolName: 'slack__send_message',
  displayName: 'Send message',
  args: '',
  argsSummary: '#sales',
  kind: 'unknown_effect',
  status: 'pending',
  errorCode: null,
  ...over,
});

const show = (el: React.ReactElement) => render(h(Theme, null, el));

describe('UnknownEffectCard (UE-9)', () => {
  it('asks whether it happened, says why it cannot tell, and answers either way', () => {
    const onDecide = vi.fn();
    show(h(UnknownEffectCard, { effect: effect(), onDecide }));
    const region = screen.getByRole('region', { name: 'Did this happen? Send message in Slack' });
    expect(region.textContent).toContain('Check Slack');
    fireEvent.click(screen.getByRole('button', { name: 'It happened: Send message' }));
    fireEvent.click(screen.getByRole('button', { name: "It didn't happen: Send message" }));
    expect(onDecide.mock.calls).toEqual([['answered'], ['denied']]);
  });

  it('the person answers it themselves: no retry link or admin note, for members and admins alike', () => {
    for (const isAdmin of [false, true]) {
      useUserStore.setState({ profile: { isAdmin } } as never);
      show(h(UnknownEffectCard, { effect: effect(), onDecide: vi.fn() }));
      expect(screen.queryByRole('link')).toBeNull();
      expect(screen.queryByText(/an admin can try it again/)).toBeNull();
      expect(screen.getByRole('button', { name: 'It happened: Send message' })).toBeTruthy();
      expect(screen.getByRole('button', { name: "It didn't happen: Send message" })).toBeTruthy();
      cleanup();
    }
  });

  it('once answered it says what was decided and offers nothing more', () => {
    show(h(UnknownEffectCard, { effect: effect({ status: 'answered' }), onDecide: vi.fn() }));
    expect(screen.getByText('Marked as done')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('a sub-agent’s request in the main thread (CA-5)', () => {
  it('names the sub-agent above the request', () => {
    show(
      h(ApprovalCard, {
        approval: effect({ kind: 'approval', args: '{"text":"hi"}', childRunId: 'child-1', childRole: 'research' }),
        onDecide: vi.fn(),
      }),
    );
    expect(screen.getByTestId('child-attribution').textContent).toContain('Sub-agent “research” wants to:');
  });

  it('an unknown effect of a sub-agent is attributed the same way', () => {
    show(h(UnknownEffectCard, { effect: effect({ childRunId: 'child-1', childRole: 'research' }), onDecide: vi.fn() }));
    expect(screen.getByTestId('child-attribution').textContent).toContain('research');
  });
});

describe('MemoryChip (SE-6)', () => {
  const remember = (status: MessagePart['status']): MessagePart => ({ type: 'tool_call', toolName: 'remember', status });

  it('a memory the agent kept is said in the answer, with a way to review it', () => {
    show(h(MemoryChip, { parts: [remember('completed')] }));
    expect(screen.getByTestId('memory-chip').textContent).toContain('Saved to your memories');
    expect(screen.getByRole('link', { name: 'Review' }).getAttribute('href')).toMatch(/^\/workspace\/memories\/?$/);
  });

  it('nothing when no memory was kept', () => {
    show(h(MemoryChip, { parts: [remember('failed'), { type: 'tool_call', toolName: 'search', status: 'completed' }] }));
    expect(screen.queryByTestId('memory-chip')).toBeNull();
  });
});

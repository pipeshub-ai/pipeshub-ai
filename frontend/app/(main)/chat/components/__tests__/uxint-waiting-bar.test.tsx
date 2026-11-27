/**
 * UXINT-2/4 (direction C): above the message box, a polite live region says when the agent needs the person, and
 * a small pill shows only while the active card is scrolled out of view; it brings the card back and focuses it.
 * The AG-UI `interaction_waiting` frame reaches the slot.
 */
import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { InteractionWaitingBar } from '../interaction-waiting-bar';
import { useChatStore } from '@/chat/store';
import { createAGUIEventHandler } from '@/chat/agui-event-handler';
import type { PendingApproval } from '@/chat/types';

const initialChat = useChatStore.getState();
const image = (i: number): PendingApproval => ({
  interactionId: `ix.approve.t7.${i}`,
  toolCallId: `t7.${i}`,
  toolName: 'image_generator__generate_image',
  args: '{}',
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  runId: 'run-w',
  afterTurns: 7,
  seq: 100 + i,
});

let report: ((visible: boolean, top: number) => void) | null = null;
class FakeObserver {
  constructor(private readonly cb: IntersectionObserverCallback) {
    report = (visible, top) =>
      this.cb([{ isIntersecting: visible, boundingClientRect: { top } } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  observe() {}
  disconnect() {}
}

function openSlot(patch: Record<string, unknown>) {
  const store = useChatStore.getState();
  const id = store.createSlot('conv-1');
  store.updateSlot(id, { isInitialized: true, isOwner: true, ...patch });
  store.setActiveSlot(id);
  return id;
}

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  vi.useFakeTimers();
  vi.stubGlobal('IntersectionObserver', FakeObserver);
  report = null;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the waiting bar (UXINT-2/4)', () => {
  it('UXB-1: announces the new call to action once, politely', () => {
    openSlot({ isStreaming: true, pendingApprovals: Array.from({ length: 7 }, (_, i) => image(i)) });
    render(<Theme><InteractionWaitingBar /></Theme>);
    const live = screen.getByTestId('interaction-announcer');
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.textContent).toBe('Agent needs your approval: Generate 7 images. 7 waiting for you.');
  });

  it('UXB-1b (WP-71): a question that ends in its own mark is not given a second one', () => {
    const questions = [{ id: 'q-dept', question: 'Which department are you in?', options: [] }];
    openSlot({
      isStreaming: true,
      pendingAskUserQuestion: { assistantMessageId: 'r', payload: { name: 'ask_user_question', questions }, answers: {}, status: 'pending' },
    });
    render(<Theme><InteractionWaitingBar /></Theme>);
    const said = screen.getByTestId('interaction-announcer').textContent ?? '';
    expect(said).toBe('Agent asks you: Which department are you in. 1 waiting for you.');
    expect(said).not.toMatch(/[?.!]\./);
  });

  it('UXB-2: no pill while the card is on screen; scrolled away, a pill brings it back and focuses it', () => {
    openSlot({ isStreaming: true, pendingApprovals: [image(0), { ...image(1), afterTurns: 8, toolName: 'coding_sandbox__run_code' }] });
    const card = document.createElement('div');
    card.setAttribute('data-interaction-active', 'true');
    const heading = document.createElement('div');
    heading.setAttribute('data-testid', 'interaction-heading');
    heading.tabIndex = -1;
    card.appendChild(heading);
    card.scrollIntoView = vi.fn();
    document.body.appendChild(card);
    render(<Theme><InteractionWaitingBar /></Theme>);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    act(() => report?.(true, 100));
    expect(screen.queryByTestId('interaction-pill')).toBeNull();
    act(() => report?.(false, -400));
    const pill = screen.getByTestId('interaction-pill');
    expect(pill.textContent).toContain('2 waiting for you · Go to first');
    expect(pill.textContent).toContain('arrow_upward');
    fireEvent.click(pill);
    expect(card.scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' });
    expect(document.activeElement).toBe(heading);
  });

  it('UXB-3: nothing waiting, nothing shown or said', () => {
    openSlot({ isStreaming: true, pendingApprovals: [{ ...image(0), status: 'approved' }] });
    render(<Theme><InteractionWaitingBar /></Theme>);
    expect(screen.getByTestId('interaction-announcer').textContent).toBe('');
    expect(screen.queryByTestId('interaction-pill')).toBeNull();
  });
});

describe('the interaction_waiting frame (UXINT-5)', () => {
  it('UXB-4: reaches the stream callbacks as a count and a paused flag', () => {
    const onInteractionWaiting = vi.fn();
    const handle = createAGUIEventHandler({ onInteractionWaiting });
    const frame = (value: unknown) => ({ event: 'CUSTOM', data: { type: 'CUSTOM', name: 'interaction_waiting', value, runId: 'run-w' } });
    handle(frame({ count: 2, paused: true }));
    handle(frame({ count: 'x' }));
    expect(onInteractionWaiting.mock.calls).toEqual([[{ count: 2, paused: true }], [{ count: 0, paused: false }]]);
  });
});

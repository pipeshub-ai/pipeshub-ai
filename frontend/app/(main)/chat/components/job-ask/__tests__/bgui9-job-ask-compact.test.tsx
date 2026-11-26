/**
 * BGUI-9: the floating Needs-you card is the design's compact card at every width (`needs-you-card.tsx`, shot 01b):
 * tile, one bold line, the action in plain words, who and where, and the actions that do it. Classic's whole card
 * opens from Details (in place on a desktop, a bottom sheet on a phone), and both answer through the same calls and
 * the same guard, so an ask is never answered twice and never allowed by itself.
 */
import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import i18n from 'i18next';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import type { MessagePart, PendingApproval, SSEEngineProgressEvent } from '@/chat/types';
import { useJobsStore } from '@/lib/harness-jobs/jobs-store';
import { EngineSignInApi } from '@/app/(main)/workspace/ai-accounts/engine-signin-api';
import { requestAskFocus } from '../../../hooks/use-interaction-focus';
import { JobAskFloat } from '../job-ask-float';
import { askOffer } from '../job-ask-offer';
import { useJobAskFloat, type JobAskItem } from '../job-asks';

vi.mock('@/lib/harness-jobs/jobs-store', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/harness-jobs/jobs-store')>();
  return { ...real, followJob: vi.fn(() => () => undefined) };
});

const initialChat = useChatStore.getState();
const initialJobs = useJobsStore.getState();
const t = i18n.t.bind(i18n);

const ask = (i: number, patch: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: `ix.approve.t3.${i}`,
  toolCallId: `t3.${i}`,
  toolName: 'github__create_pull_request',
  args: '{"title":"Fix PAY-1423","body":"Invoices in euros"}',
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  runId: 'run-root',
  childRunId: 'run-job',
  childEngine: 'claude_code',
  afterTurns: 3,
  seq: 10 + i,
  ...patch,
});

const jobPart: MessagePart = {
  type: 'sub_agent',
  runId: 'run-job',
  parentRunId: 'run-root',
  codeTaskGoal: 'Fix PAY-1423',
  status: 'running',
} as MessagePart;

function openSlot(patch: Record<string, unknown>) {
  const store = useChatStore.getState();
  const id = store.createSlot('conv-1');
  store.updateSlot(id, {
    isInitialized: true,
    isOwner: true,
    convId: 'conv-1',
    isStreaming: true,
    durableRun: { sessionId: 'conv-1', runId: 'run-root' },
    streamingParts: [jobPart],
    ...patch,
  });
  store.setActiveSlot(id);
  return id;
}

const ui = (node: React.ReactNode) => render(<Theme>{node}</Theme>);

function phone(on: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: on && query.includes('max-width'),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

/** Flushes the frame a focus move waits for. */
const frames = () => act(async () => new Promise((resolve) => setTimeout(resolve, 20)));

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useJobsStore.setState(initialJobs, true);
  useJobAskFloat.setState({ floated: new Set(), wanted: null });
  vi.spyOn(ChatApi, 'getDecisionAuthority').mockResolvedValue({ approve: true, deny: true, edit: false } as never);
  phone(false);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  requestAskFocus(null);
  document.body.innerHTML = '';
});

describe('what the compact card offers (BGUI-9, pure)', () => {
  const approval = (items: PendingApproval[]): JobAskItem => ({ key: 'k', kind: 'approval', runId: 'run-job', group: { key: 'k', items } });

  it('JC-1: an ordinary approval is decided here, named against the safe alternative', () => {
    const offer = askOffer(t, approval([ask(0)]), { approve: true, deny: true, edit: false });
    expect(offer.kind).toBe('decide');
    if (offer.kind !== 'decide') return;
    expect(offer.allow.text).toBe('Allow once');
    expect(offer.keep.text).toBe('Keep it out');
    expect(offer.allow.name.startsWith('Allow once: ')).toBe(true);
    expect(offer.keep.name.startsWith('Keep it out: ')).toBe(true);
    expect(offer.action).toMatch(/\?$/);
  });

  it('JC-2: a batch is allowed or kept out whole; a call already sending is not offered again', () => {
    const offer = askOffer(t, approval([ask(0), ask(1), ask(2, { status: 'sending' })]), undefined);
    expect(offer.kind).toBe('decide');
    if (offer.kind !== 'decide') return;
    expect(offer.allow.text).toBe('Allow all 3');
    expect(offer.keep.text).toBe('Keep them out');
    expect(offer.cards.map((c) => c.interactionId)).toEqual(['ix.approve.t3.0', 'ix.approve.t3.1']);
    expect(offer.sending).toBe(true);
  });

  it('JC-3: a sign-in, a connect, a plan, an unknown effect, or a viewer who may not both allow and deny opens the full card', () => {
    const signin: JobAskItem = { key: 's', kind: 'signin', runId: 'run-job', line: { attemptId: 'a', state: 'requested' } as never };
    expect(askOffer(t, signin, undefined)).toMatchObject({ kind: 'open', primary: 'Sign in' });
    const connect = ask(0, { kind: 'connect', connect: { item: 'Jira', itemKind: 'toolset', connectPath: '/x' } });
    expect(askOffer(t, approval([connect]), undefined)).toMatchObject({ kind: 'open', primary: 'Connect', action: 'Connect Jira to continue' });
    expect(askOffer(t, approval([ask(0, { toolName: 'submit_plan' })]), undefined)).toMatchObject({ kind: 'open', primary: 'Review' });
    expect(askOffer(t, approval([ask(0, { kind: 'unknown_effect' })]), undefined)).toMatchObject({ kind: 'open', primary: 'Review' });
    expect(askOffer(t, approval([ask(0)]), { approve: false, deny: true, edit: false }).kind).toBe('open');
    expect(askOffer(t, approval([ask(0)]), { approve: true, deny: false, edit: false }).kind).toBe('open');
  });
});

describe('the compact floating card (BGUI-9)', () => {
  it('JC-4: tile, one bold line, the action, who and where, and its actions; the full card is not drawn until asked', () => {
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    const card = screen.getByTestId('job-ask-float');
    expect(card.getAttribute('data-offer')).toBe('decide');
    expect(screen.getByTestId('job-ask-headline').textContent).toBe('Fix PAY-1423 is asking first');
    expect(screen.getByTestId('job-ask-action').textContent).toMatch(/\?$/);
    expect(screen.getByTestId('job-ask-where').textContent).toContain('Fix PAY-1423');
    expect(within(card).getByRole('button', { name: 'See activity' })).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'See the job' })).toBeTruthy();
    expect(within(card).getByRole('button', { name: /^Allow once: / })).toBeTruthy();
    expect(within(card).getByRole('button', { name: /^Keep it out: / })).toBeTruthy();
    expect(screen.queryByTestId('approval-card')).toBeNull();
    expect(card.textContent).not.toContain('What will be sent');
    // The region is named by what is asked and the action, so the ask can be found by its action's words.
    expect(card.getAttribute('aria-labelledby')).toBe('job-ask-float-heading job-ask-float-action');
  });

  it('JC-5: Allow once decides the slot’s own ask once, however many times it is pressed, and the card goes', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    const slot = openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    const allow = screen.getByRole('button', { name: /^Allow once: / });
    await act(async () => {
      fireEvent.click(allow);
      fireEvent.click(allow);
    });
    expect(decide).toHaveBeenCalledTimes(1);
    expect(decide.mock.calls[0][0]).toMatchObject({ runId: 'run-job' });
    expect(decide.mock.calls[0][2]).toBe('approved');
    expect(useChatStore.getState().slots[slot]?.pendingApprovals?.[0]?.status).toBe('approved');
    expect(screen.queryByTestId('job-ask-float')).toBeNull();
  });

  it('JC-6: Keep it out denies it; a batch is decided in one request', async () => {
    const one = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    const many = vi.spyOn(ChatApi, 'decideDurableInteractions').mockResolvedValue({ decided: {} } as never);
    const slot = openSlot({ pendingApprovals: [ask(0), ask(1)] });
    ui(<JobAskFloat />);
    expect(screen.getByRole('button', { name: /^Keep them out: / })).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Keep them out: / }));
    });
    expect(one).not.toHaveBeenCalled();
    expect(many).toHaveBeenCalledTimes(1);
    expect(many.mock.calls[0][1]).toEqual(['ix.approve.t3.0', 'ix.approve.t3.1']);
    expect(many.mock.calls[0][2]).toBe('denied');
    expect(useChatStore.getState().slots[slot]?.pendingApprovals?.every((a) => a.status === 'denied')).toBe(true);
  });

  it('JC-7: nothing is decided by itself: Enter on the card and a new ask landing send nothing', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    await frames();
    fireEvent.keyDown(screen.getByTestId('job-ask-float'), { key: 'Enter' });
    fireEvent.keyDown(screen.getByTestId('job-ask-headline'), { key: 'Enter' });
    expect(decide).not.toHaveBeenCalled();
  });

  it('JC-8: on a desktop Details opens the whole card in place, focused, with what will be sent; Escape folds it back', async () => {
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    const toggle = screen.getByRole('button', { name: 'Details' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    await act(async () => {
      fireEvent.click(toggle);
    });
    await frames();
    const details = screen.getByTestId('job-ask-details');
    expect(within(details).getByTestId('approval-card')).toBeTruthy();
    expect(details.textContent).toContain('What will be sent');
    expect(screen.getByRole('button', { name: 'Hide details' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: 'Hide details' }).getAttribute('aria-controls')).toBe(details.id);
    // One set of answers on screen: the compact buttons step aside for the whole card's.
    expect(screen.queryByTestId('job-ask-allow')).toBeNull();
    expect(document.activeElement).toBe(within(details).getByTestId('interaction-heading'));
    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' });
    await frames();
    expect(screen.queryByTestId('job-ask-details')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Details' }));
  });

  it('JC-9: the whole card in Details answers through the same guard: a second answer from the compact card is not sent', async () => {
    let release: (v: object) => void = () => undefined;
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockImplementation(() => new Promise((resolve) => (release = resolve)));
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    // The compact Allow once starts sending; the whole card, opened while it sends, offers no second answer.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Allow once: / }));
    });
    expect(screen.getByTestId('job-ask-allow').hasAttribute('disabled')).toBe(true);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    });
    const details = screen.getByTestId('job-ask-details');
    for (const b of within(details).queryAllByRole('button')) if (/Allow/.test(b.textContent ?? '')) fireEvent.click(b);
    await act(async () => release({}));
    expect(decide).toHaveBeenCalledTimes(1);
  });

  it('JC-10: on a phone the actions are 44 px targets under the text, and Details is a bottom sheet holding the whole card', async () => {
    phone(true);
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    for (const name of [/^Allow once: /, /^Keep it out: /, /^See the job$/, /^Details$/, /^See activity$/]) {
      expect((screen.getByRole('button', { name }) as HTMLElement).style.minHeight).toBe('44px');
    }
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    });
    const sheet = screen.getByRole('dialog', { name: 'Fix PAY-1423 is asking first' });
    expect(within(sheet).getByTestId('approval-card')).toBeTruthy();
    expect(sheet.textContent).toContain('What will be sent');
    // The sheet is not inside the card, so its Escape closes the sheet rather than leaving for the message box.
    expect(screen.getByTestId('job-ask-float').contains(sheet)).toBe(false);
    await act(async () => {
      fireEvent.click(within(sheet).getByRole('button', { name: 'Close' }));
    });
    await frames();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Details' }));
  });

  it('JC-11: an ask answered inside the sheet closes it, and the focus goes to what is asked next', async () => {
    phone(true);
    vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    openSlot({ pendingApprovals: [ask(0), ask(1, { afterTurns: 4, toolName: 'slack__post_message' })] });
    ui(<JobAskFloat />);
    expect(screen.getByTestId('job-ask-position').textContent).toBe('1 of 2');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    });
    const sheet = screen.getByRole('dialog');
    await act(async () => {
      fireEvent.click(within(sheet).getByRole('button', { name: /^Allow once/ }));
    });
    await frames();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByTestId('job-ask-position')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('job-ask-headline'));
  });

  it('JC-12: an engine sign-in needs a code typed, so Sign in opens the whole sign-in card', async () => {
    vi.spyOn(EngineSignInApi, 'attempt').mockResolvedValue({ state: 'waiting', link: null } as never);
    const frames0 = [{ kind: 'signin', data: { attemptId: 'att-1', state: 'requested' } }] as unknown as SSEEngineProgressEvent[];
    openSlot({ pendingApprovals: [] });
    useJobsStore.setState({ streams: { 'run-job': { frames: frames0, ended: false } as never } });
    ui(<JobAskFloat />);
    expect(screen.getByTestId('job-ask-headline').textContent).toBe('Fix PAY-1423 needs you to sign in');
    expect(screen.getByTestId('job-ask-action').textContent).toBe('Sign in to Claude Code');
    expect(screen.queryByTestId('engine-signin-card')).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByTestId('job-ask-open'));
    });
    expect(within(screen.getByTestId('job-ask-details')).getByTestId('engine-signin-card')).toBeTruthy();
  });

  it('JC-13: a Needs you link naming the ask focuses the compact card', async () => {
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    await act(async () => requestAskFocus('ix.approve.t3.0'));
    expect(document.activeElement).toBe(screen.getByTestId('job-ask-headline'));
  });

  it('JC-14: a paused job says so in its bold line, and the action stays in plain words', () => {
    openSlot({ pendingApprovals: [ask(0)], interactionWaiting: { paused: true } });
    ui(<JobAskFloat />);
    expect(screen.getByTestId('job-ask-headline').textContent).toBe('Fix PAY-1423 paused. Everything is saved.');
    expect(screen.getByTestId('job-ask-action').textContent).toMatch(/\?$/);
  });
});

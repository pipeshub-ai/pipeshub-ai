/**
 * BGUI-4: a background job's asks float over the message box (design `needs-you-card.tsx`, shot 01b). One answer
 * state sits behind the floating card and the transcript: the floating card answers through the chat slot's own
 * approvals, the transcript draws a pointer while the card holds the ask, and a second answer is never sent.
 */
import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import { decideApproval } from '@/chat/approvals';
import type { MessagePart, PendingApproval, SSEEngineProgressEvent } from '@/chat/types';
import { useJobsStore } from '@/lib/harness-jobs/jobs-store';
import { useActivityPanelStore } from '../../activity-panel/activity-panel-store';
import { InteractionWaitingBar } from '../../interaction-waiting-bar';
import { JobAskFloat } from '../job-ask-float';
import { FloatedAskPointer } from '../floated-ask-pointer';
import { floatKeys, floatsGroup, jobAsksOf, pointerKey, signInKey, useJobAskFloat } from '../job-asks';

vi.mock('@/lib/harness-jobs/jobs-store', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/harness-jobs/jobs-store')>();
  return { ...real, followJob: vi.fn(() => () => undefined) };
});

const initialChat = useChatStore.getState();
const initialJobs = useJobsStore.getState();

const ask = (i: number, patch: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: `ix.approve.t3.${i}`,
  toolCallId: `t3.${i}`,
  toolName: 'github__create_pull_request',
  args: '{"title":"Fix PAY-1423"}',
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

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useJobsStore.setState(initialJobs, true);
  useJobAskFloat.setState({ floated: new Set(), wanted: null });
  vi.spyOn(ChatApi, 'getDecisionAuthority').mockResolvedValue({ approve: true, deny: true, edit: false } as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('the job asks (BGUI-4, pure)', () => {
  it('JA-1: only a job’s open asks, while the run streams; the root run’s stay at their row', () => {
    const items = jobAsksOf({ isStreaming: true, pendingApprovals: [ask(0), ask(1, { childRunId: undefined }), ask(2, { status: 'approved' })], jobs: [] });
    expect(items.map((i) => i.key)).toEqual(['run-root:run-job:ix.approve.t3.0']);
    expect(jobAsksOf({ isStreaming: false, pendingApprovals: [ask(0)], jobs: [] })).toEqual([]);
  });

  it('JA-2: calls that waited together are one ask ("Allow all"), and an engine sign-in is its own', () => {
    const frames = [{ kind: 'signin', data: { attemptId: 'att-1', state: 'requested' } }] as unknown as SSEEngineProgressEvent[];
    const items = jobAsksOf({ isStreaming: true, pendingApprovals: [ask(0), ask(1)], jobs: [{ runId: 'run-job', frames }] });
    expect(items.map((i) => i.kind)).toEqual(['approval', 'signin']);
    expect(items[0].kind === 'approval' && items[0].group.items).toHaveLength(2);
    expect(floatKeys(items)).toEqual(['run-root:run-job:ix.approve.t3.0', 'run-root:run-job:ix.approve.t3.1', signInKey('run-job', 'att-1')]);
  });

  it('JA-3: the transcript knows a group is floated by any open call of it, and names it by its first open one', () => {
    const group = { key: 'g', items: [ask(0, { status: 'approved' }), ask(1)] };
    expect(floatsGroup(group, new Set(['run-root:run-job:ix.approve.t3.1']))).toBe(true);
    expect(floatsGroup(group, new Set(['run-root:run-job:ix.approve.t3.0']))).toBe(false);
    expect(pointerKey(group)).toBe('run-root:run-job:ix.approve.t3.1');
  });
});

describe('the floating card (BGUI-4)', () => {
  it('JA-4: says which job needs you and where, opens the job and its activity', () => {
    openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    const card = screen.getByTestId('job-ask-float');
    expect(card.getAttribute('role')).toBe('region');
    expect(screen.getByTestId('job-ask-headline').textContent).toBe('Fix PAY-1423 is asking first');
    expect(screen.getByTestId('job-ask-where').textContent).toContain('Fix PAY-1423');
    fireEvent.click(screen.getByRole('button', { name: 'See the job' }));
    expect(useActivityPanelStore.getState().run?.runId).toBe('run-job');
    fireEvent.click(screen.getByRole('button', { name: 'See activity' }));
    expect(useActivityPanelStore.getState().jobSection).toBe('activity');
    expect(useJobAskFloat.getState().floated.has('run-root:run-job:ix.approve.t3.0')).toBe(true);
  });

  it('JA-5: announces a new ask politely and does not take focus', () => {
    openSlot({ pendingApprovals: [ask(0)] });
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();
    ui(<JobAskFloat />);
    const live = screen.getByTestId('job-ask-announcer');
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.textContent).toMatch(/^Fix PAY-1423 needs you: /);
    expect(document.activeElement).toBe(outside);
  });

  it('JA-6: several asks read "1 of N", one at a time', () => {
    openSlot({ pendingApprovals: [ask(0), ask(1, { afterTurns: 4, toolName: 'slack__post_message' })] });
    ui(<JobAskFloat />);
    expect(screen.getByTestId('job-ask-position').textContent).toBe('1 of 2');
    fireEvent.click(screen.getByRole('button', { name: 'Next ask' }));
    expect(screen.getByTestId('job-ask-position').textContent).toBe('2 of 2');
    expect(screen.getByRole('button', { name: 'Next ask' })).toHaveProperty('disabled', true);
  });

  it('JA-7: answering here decides the slot’s own ask once; the same answer from the row is not sent again', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    const slot = openSlot({ pendingApprovals: [ask(0)] });
    ui(<JobAskFloat />);
    await act(async () => {
      const first = decideApproval(slot, ask(0), 'approved');
      const again = decideApproval(slot, ask(0), 'approved');
      await Promise.all([first, again]);
    });
    expect(decide).toHaveBeenCalledTimes(1);
    expect(decide.mock.calls[0][0]).toMatchObject({ runId: 'run-job' });
    expect(useChatStore.getState().slots[slot]?.pendingApprovals?.[0]?.status).toBe('approved');
    expect(screen.queryByTestId('job-ask-float')).toBeNull();
    expect(useJobAskFloat.getState().floated.size).toBe(0);
  });

  it('JA-8: a shared viewer sees no floating card', () => {
    openSlot({ pendingApprovals: [ask(0)], isOwner: false });
    ui(<JobAskFloat />);
    expect(screen.queryByTestId('job-ask-float')).toBeNull();
  });

  it('JA-9: the pointer in the transcript turns the card to its ask and focuses it', async () => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
    openSlot({ pendingApprovals: [ask(0), ask(1, { afterTurns: 4, toolName: 'slack__post_message' })] });
    ui(
      <>
        <JobAskFloat />
        <FloatedAskPointer askKey="run-root:run-job:ix.approve.t3.1" />
      </>,
    );
    expect(screen.getByTestId('job-ask-pointer').textContent).toContain('Answer it in the card above the message box');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Show it' }));
    });
    expect(screen.getByTestId('job-ask-position').textContent).toBe('2 of 2');
    expect(document.activeElement).toBe(screen.getByTestId('job-ask-headline'));
    vi.unstubAllGlobals();
  });

  it('JA-10: the page’s announcer leaves a floated ask to the card', () => {
    openSlot({ pendingApprovals: [ask(0)] });
    ui(
      <>
        <JobAskFloat />
        <InteractionWaitingBar />
      </>,
    );
    expect(screen.getByTestId('interaction-announcer').textContent).toBe('');
    expect(screen.getByTestId('job-ask-announcer').textContent).not.toBe('');
  });
});

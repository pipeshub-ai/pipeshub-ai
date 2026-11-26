/**
 * GA-05 (G-07): the end-of-run summary under a run's steps, in plain words
 * (no ids, no raw values), read only once the person opens the steps.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import i18n from 'i18next';
import '@/lib/__tests__/test-i18n';
import type { DurableAgentRunHandle, RunDigestView, RunTimelineView } from '@/chat/types';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, streamSSEGet: vi.fn() }));

import { ChatApi } from '@/chat/api';
import { ActivitySectionOpen } from '../message-area/agent-activity';
import { RunSummary, runSummaryLines } from '../message-area/run-summary';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };

const DIGEST: RunDigestView = {
  outcome: 'answered',
  actions: { total: 5, failed: 1, refused: 1, tools: [{ name: 'jira__create_issue', count: 5 }] },
  commands: { total: 0, failed: 0 },
  records: { read: 3, changed: 2 },
  artifacts: [{ name: 'report.pdf', recordId: 'rec-9' }, { name: 'a.csv' }, { name: 'b.csv' }, { name: 'c.csv' }],
  files: { added: 0, modified: 0, deleted: 0 },
  approvals: { asked: 2, approved: 1, denied: 1, unanswered: 0 },
  questions: { asked: 0, open: false },
  delegated: 0,
  cost: { usdMicros: 41_500, tokens: 12_300 },
  durationMs: 42_000,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('GA-05 run summary', () => {
  it('GA-05: says how the run ended and what it did, in plain words, with no ids', () => {
    const lines = runSummaryLines(DIGEST, i18n.t.bind(i18n));
    expect(lines).toEqual([
      'Answered',
      'Used 5 tools · 1 failed · 1 stopped',
      'Read 3 records · Made 2 changes',
      'Saved report.pdf, a.csv, b.csv, 1 more',
      'Asked 2 approvals · 1 approved · 1 denied',
      'Cost $0.04 · 12,300 tokens',
    ]);
    expect(lines.join(' ')).not.toMatch(/rec-9|jira__/);
  });

  it('GA-05: a failed, cut-short run with nothing else to say is one line', () => {
    const bare: RunDigestView = {
      ...DIGEST,
      outcome: 'failed',
      cutShortBy: 'budget',
      actions: { total: 0, failed: 0, refused: 0, tools: [] },
      records: { read: 0, changed: 0 },
      artifacts: [],
      approvals: { asked: 0, approved: 0, denied: 0, unanswered: 0 },
      cost: null,
    };
    expect(runSummaryLines(bare, i18n.t.bind(i18n))).toEqual(['Failed · Stopped early: budget reached']);
  });

  it('GA-05: reads the run only once its steps are opened, then shows the summary', async () => {
    const read = vi
      .spyOn(ChatApi, 'getRunTimeline')
      .mockResolvedValue({ runId: 'run-1', status: 'completed', digest: DIGEST } as unknown as RunTimelineView);
    const view = (open: boolean) => (
      <Theme>
        <ActivitySectionOpen.Provider value={open}>
          <RunSummary run={RUN} />
        </ActivitySectionOpen.Provider>
      </Theme>
    );
    const { rerender } = render(view(false));
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByTestId('run-summary')).toBeNull();
    rerender(view(true));
    await waitFor(() => expect(screen.getByTestId('run-summary')).toBeTruthy());
    expect(read).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Summary')).toBeTruthy();
    expect(screen.getByText('Read 3 records · Made 2 changes')).toBeTruthy();
  });

  it('GA-05: a run with no summary yet (still going, or an older server) shows nothing', async () => {
    vi.spyOn(ChatApi, 'getRunTimeline').mockResolvedValue({ runId: 'run-1', status: 'running' } as unknown as RunTimelineView);
    render(
      <Theme>
        <RunSummary run={RUN} />
      </Theme>,
    );
    await waitFor(() => expect(ChatApi.getRunTimeline).toHaveBeenCalled());
    expect(screen.queryByTestId('run-summary')).toBeNull();
  });
});

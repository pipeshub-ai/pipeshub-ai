import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { RunResultCardPayload } from '../../../types';
import type { WorkflowRunUpdate } from '@/lib/hooks/use-workflow-run-updates';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Interpolation options are passed as the second argument too, so only a
    // string is treated as a fallback; anything else falls back to the key.
    t: (key: string, fallback?: unknown) =>
      typeof fallback === 'string' ? fallback : key,
    i18n: { language: 'en-US' },
  }),
}));

vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href }, children),
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) =>
    React.createElement('span', { 'data-testid': 'icon', 'data-icon': name }),
}));

const answerRun = vi.fn(async () => ({ status: 'pending' }));

vi.mock('@/app/(main)/workflows/api', () => ({
  WorkflowsApi: {
    answerRun: (...args: unknown[]) => answerRun(...(args as [])),
  },
}));

const handlers = new Set<(update: WorkflowRunUpdate) => void>();

vi.mock('@/lib/hooks/use-workflow-run-updates', () => ({
  useWorkflowRunUpdates: (handler: (update: WorkflowRunUpdate) => void) => {
    handlers.add(handler);
  },
}));

const { WorkflowRunHeader } = await import('../workflow-run-header');

const h = React.createElement;

function emit(update: WorkflowRunUpdate) {
  act(() => {
    handlers.forEach((handler) => handler(update));
  });
}

function makePayload(overrides: Partial<RunResultCardPayload> = {}): RunResultCardPayload {
  return {
    workflowId: 'wf-1',
    runId: 'run-1',
    status: 'succeeded',
    outputSummary: 'All good.',
    redirectLink: '/workflows?workflowId=wf-1',
    ...overrides,
  } as RunResultCardPayload;
}

function renderHeader(payload: RunResultCardPayload) {
  return render(h(Theme, null, h(WorkflowRunHeader, { payload })));
}

beforeEach(() => {
  handlers.clear();
  answerRun.mockClear();
});
afterEach(() => cleanup());

describe('WorkflowRunHeader', () => {
  it('shows the workflow name and its terminal status', () => {
    renderHeader(makePayload({ workflowName: 'Weekly digest' }));

    expect(screen.getByText('Weekly digest')).toBeTruthy();
    expect(screen.getByText('succeeded')).toBeTruthy();
  });

  it('advances a dry run to succeeded on a socket update', () => {
    // A dry run renders before it has finished, so without the socket it
    // would sit at "pending" forever.
    renderHeader(makePayload({ status: 'pending', isDryRun: true }));
    expect(screen.getByText('pending')).toBeTruthy();

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'succeeded' });

    expect(screen.getByText('succeeded')).toBeTruthy();
    expect(screen.queryByText('pending')).toBeNull();
  });

  it('ignores an update for a different run', () => {
    renderHeader(makePayload({ status: 'pending' }));

    emit({ workflowId: 'wf-1', runId: 'some-other-run', status: 'failed' });

    expect(screen.getByText('pending')).toBeTruthy();
  });

  it('renders the failure reason outside the answer body', () => {
    renderHeader(makePayload({ status: 'failed', error: 'Tool jira__create_issue failed' }));

    expect(screen.getByText('Tool jira__create_issue failed')).toBeTruthy();
  });

  it('does not show a stale error once a run is reported succeeded', () => {
    renderHeader(makePayload({ status: 'failed', error: 'transient failure' }));

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'succeeded' });

    expect(screen.queryByText('transient failure')).toBeNull();
  });

  it('links to the workflow it ran', () => {
    const { container } = renderHeader(makePayload());

    const hrefs = Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('/workflows?workflowId=wf-1');
    expect(hrefs).toContain('/workflows?workflowId=wf-1&edit=true');
  });

  it('marks a dry run as such so a user does not read it as a real run', () => {
    renderHeader(makePayload({ isDryRun: true }));

    expect(screen.getByText('Dry run')).toBeTruthy();
  });

  describe('when the run is waiting on the user', () => {
    it('shows the question and answers it with an approval', async () => {
      // Without this the run reads as a finished message and the only way to
      // unblock it is to go find it in the workflows dashboard.
      renderHeader(
        makePayload({
          status: 'awaiting_input',
          suspensionKind: 'approval',
          outputSummary: 'Delete 42 stale Jira issues?',
        })
      );

      expect(screen.getByText('Delete 42 stale Jira issues?')).toBeTruthy();

      await act(async () => {
        screen.getByText('Approve').click();
      });

      expect(answerRun).toHaveBeenCalledWith('wf-1', 'run-1', 'yes');
    });

    it('offers nothing to click when it is waiting on an external event', () => {
      renderHeader(
        makePayload({
          status: 'awaiting_input',
          suspensionKind: 'wait_for_event',
          outputSummary: 'Waiting for github.pull_request.opened.',
        })
      );

      expect(screen.queryByText('Approve')).toBeNull();
      expect(screen.getByText('This run resumes when the event arrives.')).toBeTruthy();
    });

    it('takes a free-text answer for an agent question', async () => {
      renderHeader(
        makePayload({ status: 'awaiting_input', outputSummary: 'Which sprint?' })
      );

      const input = screen.getByPlaceholderText('Type your answer') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(input, { target: { value: 'Sprint 42' } });
      });
      await act(async () => {
        screen.getByText('Send').click();
      });

      expect(answerRun).toHaveBeenCalledWith('wf-1', 'run-1', 'Sprint 42');
    });
  });
});

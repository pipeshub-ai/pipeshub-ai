import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { WorkflowTriggersPanel } from '../components/workflow-triggers-panel';
import type { WorkflowTrigger } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: () => void }) => unknown) =>
    selector({ addToast: () => {} }),
}));

vi.mock('../api', () => ({
  WorkflowsApi: {
    createTrigger: vi.fn(),
    setTriggerEnabled: vi.fn(),
    deleteTrigger: vi.fn(),
  },
}));

const cronTrigger: WorkflowTrigger = {
  triggerId: 'trg-1',
  workflowId: 'wf-1',
  kind: 'cron',
  cronExpression: '0 9 * * *',
  misfirePolicy: 'skip',
  runCount: 0,
  enabled: true,
};

let api: typeof import('../api').WorkflowsApi;

// Radix's Select mounts a scroll area that measures itself, which jsdom has
// no implementation for.
class NoopResizeObserver {
  observe() {}

  unobserve() {}

  disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver;

beforeEach(async () => {
  ({ WorkflowsApi: api } = await import('../api'));
  vi.mocked(api.setTriggerEnabled).mockResolvedValue({ ...cronTrigger, enabled: false });
  vi.mocked(api.deleteTrigger).mockResolvedValue(undefined);
  vi.mocked(api.createTrigger).mockResolvedValue({ ...cronTrigger, triggerId: 'trg-2' });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPanel(triggers: WorkflowTrigger[], onChanged = vi.fn()) {
  render(
    <Theme>
      <WorkflowTriggersPanel workflowId="wf-1" triggers={triggers} onChanged={onChanged} />
    </Theme>
  );
  return onChanged;
}

describe('WorkflowTriggersPanel', () => {
  it('disables a trigger and refetches so the new next-run time is server-computed', async () => {
    const onChanged = renderPanel([cronTrigger]);

    fireEvent.click(screen.getByTitle('Disable'));

    await waitFor(() => {
      expect(api.setTriggerEnabled).toHaveBeenCalledWith('wf-1', 'trg-1', false);
    });
    expect(onChanged).toHaveBeenCalled();
  });

  it('deletes a trigger', async () => {
    renderPanel([cronTrigger]);

    fireEvent.click(screen.getByTitle('Delete'));

    await waitFor(() => {
      expect(api.deleteTrigger).toHaveBeenCalledWith('wf-1', 'trg-1');
    });
  });

  it('reveals the webhook secret exactly once, since it is never readable again', async () => {
    vi.mocked(api.createTrigger).mockResolvedValue({
      ...cronTrigger,
      triggerId: 'trg-3',
      kind: 'webhook',
      webhookId: 'hook-1',
      webhookSecret: 's3cret',
      webhookPath: '/api/v1/tasks/webhooks/hook-1',
    });
    renderPanel([]);

    fireEvent.click(screen.getByText('Add trigger'));
    // The kind select defaults to cron; the panel posts whatever is chosen,
    // and this asserts the reveal path rather than the select interaction.
    const form = screen.getByText('Add trigger', { selector: 'button' });
    fireEvent.click(form);

    await waitFor(() => {
      expect(screen.getByDisplayValue('s3cret')).toBeTruthy();
    });
  });

  it('says a workflow with no triggers only runs on demand', () => {
    renderPanel([]);

    expect(
      screen.getByText('No schedule configured — this workflow only runs on demand.')
    ).toBeTruthy();
  });
});

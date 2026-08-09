import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { Workflow } from '../types';
import type { WorkflowRunUpdate } from '@/lib/hooks/use-workflow-run-updates';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: () => true,
  selectIsProfileInitialized: () => true,
}));

vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: () => vi.fn(),
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: () => React.createElement('span'),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => React.createElement('span', null, 'Loading'),
}));

vi.mock('../components', () => ({
  WorkflowDetailView: () => React.createElement('div'),
}));

/** Renders every column's cell so the test can read what the page shows. */
vi.mock('@/app/(main)/workspace/components', () => ({
  EntityPageHeader: () => React.createElement('div'),
  EntityPagination: () => React.createElement('div'),
  EntityEmptyState: () => React.createElement('div'),
  EntityRowActionMenu: () => React.createElement('div'),
  ConfirmationDialog: () => React.createElement('div'),
  SelectDropdown: () => React.createElement('div'),
  EntityDataTable: ({
    columns,
    data,
    getItemId,
  }: {
    columns: { key: string; render: (item: Workflow) => React.ReactNode }[];
    data: Workflow[];
    getItemId: (item: Workflow) => string;
  }) =>
    React.createElement(
      'table',
      null,
      React.createElement(
        'tbody',
        null,
        data.map((item) =>
          React.createElement(
            'tr',
            { key: getItemId(item) },
            columns.map((column) =>
              React.createElement(
                'td',
                { key: column.key, 'data-testid': `${getItemId(item)}-${column.key}` },
                column.render(item)
              )
            )
          )
        )
      )
    ),
}));

const listWorkflows = vi.fn();
vi.mock('../api', () => ({
  WorkflowsApi: {
    listWorkflows: (...args: unknown[]) => listWorkflows(...args),
    deleteWorkflow: vi.fn(),
  },
}));

let emitRunUpdate: ((update: WorkflowRunUpdate) => void) | null = null;
vi.mock('@/lib/hooks/use-workflow-run-updates', () => ({
  useWorkflowRunUpdates: (handler: (update: WorkflowRunUpdate) => void) => {
    emitRunUpdate = handler;
  },
}));

const workflow: Workflow = {
  workflowId: 'wf-1',
  orgId: 'org-1',
  title: 'Weekly digest',
  description: 'posts the digest',
  status: 'active',
  executionKind: 'code',
  triggers: [],
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
} as unknown as Workflow;

beforeEach(() => {
  emitRunUpdate = null;
  listWorkflows.mockResolvedValue({ workflows: [workflow], total: 1 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderPage() {
  const { default: WorkflowsPage } = await import('../page');
  render(React.createElement(Theme, null, React.createElement(WorkflowsPage)));
  await waitFor(() => screen.getByTestId('wf-1-status'));
}

function emit(update: Partial<WorkflowRunUpdate>) {
  act(() => {
    emitRunUpdate?.(update as WorkflowRunUpdate);
  });
}

describe('WorkflowsPage status column', () => {
  it('shows the workflow lifecycle status', async () => {
    await renderPage();

    expect(screen.getByTestId('wf-1-status').textContent).toBe('active');
  });

  it('shows a live run status alongside it without replacing it', async () => {
    // A run being `failed` says nothing about whether the workflow itself is
    // active or paused; overwriting the lifecycle status here used to corrupt
    // the badge and the status filter until the next refetch.
    await renderPage();

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'failed' });

    await waitFor(() => {
      const cell = screen.getByTestId('wf-1-status').textContent ?? '';
      expect(cell).toContain('active');
      expect(cell).toContain('failed');
    });
  });

  it('renders underscored run statuses readably', async () => {
    await renderPage();

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'awaiting_input' });

    await waitFor(() =>
      expect(screen.getByTestId('wf-1-status').textContent).toContain('awaiting input')
    );
  });

  it('ignores an update with no workflow id', async () => {
    await renderPage();

    emit({ runId: 'run-1', status: 'failed' });

    expect(screen.getByTestId('wf-1-status').textContent).toBe('active');
  });

  it('keeps only the latest run status for a workflow', async () => {
    await renderPage();

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'running' });
    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'succeeded' });

    await waitFor(() => {
      const cell = screen.getByTestId('wf-1-status').textContent ?? '';
      expect(cell).toContain('succeeded');
      expect(cell).not.toContain('running');
    });
  });
});

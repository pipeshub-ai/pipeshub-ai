import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { WorkflowEditPanel } from '../components/workflow-edit-panel';
import type { WorkflowIR, WorkflowEditResult } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => React.createElement('span', null, 'Loading'),
}));

vi.mock('../components/workflow-graph', () => ({
  WorkflowGraph: ({ ir }: { ir: WorkflowIR }) =>
    React.createElement('div', { 'data-testid': 'workflow-graph', 'data-nodes': ir.nodes.length }),
}));
vi.mock('../components/workflow-editor', () => ({
  WorkflowEditor: ({ source }: { source: string }) =>
    React.createElement('pre', { 'data-testid': 'workflow-editor' }, source),
}));

vi.mock('../api', () => ({
  WorkflowsApi: {
    editWorkflow: vi.fn(),
    commitVersion: vi.fn(),
  },
}));

const EMPTY_IR: WorkflowIR = { nodes: [], edges: [], entry_node_id: null };

const mockEditResult: WorkflowEditResult = {
  source: 'def my_workflow(): pass',
  ir: {
    nodes: [
      {
        node_id: 'n1',
        kind: 'workflow',
        label: 'main',
        source_start: 1,
        source_end: 2,
        children: [],
        metadata: {},
      },
    ],
    edges: [],
    entry_node_id: 'n1',
  },
  previousSource: 'def old_workflow(): pass',
  baseVersionId: 'ver-1',
};

const mockVersion = {
  versionId: 'ver-2',
  versionNumber: 2,
  createdAt: '2026-01-01T00:00:00Z',
  createdByUserId: 'u-1',
  contentHash: 'abc',
};

let api: typeof import('../api').WorkflowsApi;

beforeEach(async () => {
  ({ WorkflowsApi: api } = await import('../api'));
  vi.mocked(api.editWorkflow).mockResolvedValue(mockEditResult);
  vi.mocked(api.commitVersion).mockResolvedValue(mockVersion as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const h = React.createElement;

function renderPanel(props: {
  onApply?: (r: WorkflowEditResult, v: unknown) => void;
  onDiscard?: () => void;
}) {
  return render(
    h(
      Theme,
      null,
      h(WorkflowEditPanel, {
        workflowId: 'wf-1',
        currentSource: 'def old_workflow(): pass',
        currentIR: EMPTY_IR,
        onApply: (props.onApply ?? vi.fn()) as never,
        onDiscard: props.onDiscard ?? vi.fn(),
      })
    )
  );
}

const instructionsBox = () => screen.getByRole('textbox');

async function generate(instructions = 'Add Slack step') {
  fireEvent.change(instructionsBox(), { target: { value: instructions } });
  fireEvent.click(screen.getByRole('button', { name: /Generate Changes/ }));
  await waitFor(() => screen.getByRole('button', { name: /Apply Changes/ }));
}

describe('WorkflowEditPanel', () => {
  it('requires instructions before generating', () => {
    renderPanel({});
    const btn = screen.getByRole('button', { name: /Generate Changes/ }) as HTMLButtonElement;
    expect(btn.disabled).toBeTruthy();

    fireEvent.change(instructionsBox(), {
      target: { value: 'Add a Slack notification step' },
    });
    expect(
      (screen.getByRole('button', { name: /Generate Changes/ }) as HTMLButtonElement).disabled
    ).toBeFalsy();
  });

  it('shows the before/after review after generating', async () => {
    renderPanel({});
    await generate();

    expect(screen.queryByText(/review the changes/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Discard/ })).toBeTruthy();
  });

  it('generating alone commits nothing', async () => {
    // The whole point of review-first: until Apply, the pinned version is
    // untouched, so Discard can actually undo the edit.
    renderPanel({});
    await generate();

    expect(vi.mocked(api.commitVersion)).not.toHaveBeenCalled();
  });

  it('Discard issues no commit and reports no new version', async () => {
    const onApply = vi.fn();
    const onDiscard = vi.fn();
    renderPanel({ onApply, onDiscard });
    await generate();

    fireEvent.click(screen.getByRole('button', { name: /Discard/ }));

    expect(vi.mocked(api.commitVersion)).not.toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
    expect(onDiscard).toHaveBeenCalledOnce();
  });

  it('Apply commits the reviewed source against the base version', async () => {
    const onApply = vi.fn();
    renderPanel({ onApply });
    await generate();

    fireEvent.click(screen.getByRole('button', { name: /Apply Changes/ }));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(vi.mocked(api.commitVersion)).toHaveBeenCalledWith(
      'wf-1',
      mockEditResult.source,
      'ver-1'
    );
    expect(onApply).toHaveBeenCalledWith(mockEditResult, mockVersion);
  });

  it('surfaces a generation failure', async () => {
    vi.mocked(api.editWorkflow).mockRejectedValueOnce(new Error('Verification failed'));

    renderPanel({});
    fireEvent.change(instructionsBox(), {
      target: { value: 'Do something bad' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Generate Changes/ }));

    await waitFor(() => screen.getByText('Verification failed'));
  });

  it('surfaces a commit conflict without calling onApply', async () => {
    // A concurrent edit moved the pin; the user has to reload rather than
    // silently overwrite the version that won.
    vi.mocked(api.commitVersion).mockRejectedValueOnce(
      new Error('Workflow moved to another version while this edit was in flight')
    );
    const onApply = vi.fn();
    renderPanel({ onApply });
    await generate();

    fireEvent.click(screen.getByRole('button', { name: /Apply Changes/ }));

    await waitFor(() => screen.getByText(/moved to another version/));
    expect(onApply).not.toHaveBeenCalled();
  });
});

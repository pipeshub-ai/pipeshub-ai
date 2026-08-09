import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { WorkflowStudio } from '../components/workflow-studio';
import type { WorkflowIR } from '../types';
import type { WorkflowVersionSummary } from '../api';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

vi.mock('../components/workflow-graph', () => ({
  WorkflowGraph: () => React.createElement('div', { 'data-testid': 'workflow-graph' }),
}));
vi.mock('../components/workflow-editor', () => ({
  WorkflowEditor: ({ source }: { source: string }) =>
    React.createElement('pre', { 'data-testid': 'workflow-editor' }, source),
}));
vi.mock('../components/workflow-edit-panel', () => ({
  WorkflowEditPanel: () => React.createElement('div', { 'data-testid': 'edit-panel' }),
}));
vi.mock('../api', () => ({
  WorkflowsApi: {
    commitVersion: vi.fn(),
    activateVersion: vi.fn(),
  },
}));

const EMPTY_IR: WorkflowIR = { nodes: [], edges: [], entry_node_id: null };

const IR_WITH_A_NODE: WorkflowIR = {
  nodes: [
    { node_id: 'n1', kind: 'workflow', label: 'main', source_start: 1, source_end: 2, children: [], metadata: {} },
  ],
  edges: [],
  entry_node_id: 'n1',
};

const version: WorkflowVersionSummary = {
  versionId: 'ver-1',
  workflowId: 'wf-1',
  versionNumber: 1,
  sdkVersion: '0.1.0',
  contentHash: 'abc1234',
  hasBundleRef: true,
  createdAt: '2026-01-01T00:00:00Z',
  createdByUserId: 'u-1',
  ir: IR_WITH_A_NODE,
  verifierVersion: 1,
  verifiedAt: '2026-01-01T00:00:00Z',
  needsRegeneration: false,
};

const h = React.createElement;

function renderStudio(overrides: Partial<React.ComponentProps<typeof WorkflowStudio>> = {}) {
  return render(
    h(
      Theme,
      null,
      h(WorkflowStudio, {
        workflowId: 'wf-1',
        source: '',
        ir: EMPTY_IR,
        versions: [],
        readOnly: true,
        ...overrides,
      })
    )
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkflowStudio partial rendering when source fails to load', () => {
  it('renders the graph tab by default when a version exists but its source did not load', () => {
    // BUG-5: the version's IR loaded fine (source_data.source fetch failed
    // separately) -- the graph must not be hidden behind an empty-source gate.
    renderStudio({ source: '', ir: IR_WITH_A_NODE, versions: [version], selectedVersionId: 'ver-1' });

    expect(screen.getByTestId('workflow-graph')).toBeTruthy();
    expect(screen.queryByText(/No code generated yet/)).toBeNull();
  });

  it('shows a distinct "source unavailable" message on the Code tab instead of a blank editor', () => {
    renderStudio({ source: '', ir: IR_WITH_A_NODE, versions: [version], selectedVersionId: 'ver-1' });

    fireEvent.click(screen.getByRole('button', { name: /Code/ }));

    expect(screen.getByText(/Source code could not be loaded/)).toBeTruthy();
    expect(screen.queryByTestId('workflow-editor')).toBeNull();
  });

  it('still shows "No code generated yet" when there truly is no version at all', () => {
    renderStudio({ source: '', ir: EMPTY_IR, versions: [] });

    expect(screen.getByText(/No code generated yet/)).toBeTruthy();
  });

  it('shows the code editor normally once source is actually available', () => {
    renderStudio({ source: 'print(1)', ir: IR_WITH_A_NODE, versions: [version], selectedVersionId: 'ver-1' });

    fireEvent.click(screen.getByRole('button', { name: /Code/ }));

    expect(screen.getByTestId('workflow-editor').textContent).toBe('print(1)');
    expect(screen.queryByText(/Source code could not be loaded/)).toBeNull();
  });
});

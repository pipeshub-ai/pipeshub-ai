import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { ErrorType } from '@/lib/api';
import { WorkflowDetailView } from '../components/workflow-detail-view';
import type { Workflow, WorkflowIR } from '../types';
import type { WorkflowVersionSummary } from '../api';

// A stable `t` reference across renders -- a fresh function every call would
// change every `useCallback` that depends on `t` (e.g. `fetchAll`), which in
// turn re-fires the effect that calls it on every render and desyncs
// `mockResolvedValueOnce`/`mockRejectedValueOnce` call counts in tests below.
function translate(_key: string, fallback: string, vars?: Record<string, unknown>) {
  if (!fallback) return _key;
  if (!vars) return fallback;
  return Object.entries(vars).reduce(
    (msg, [k, v]) => msg.replace(`{{${k}}}`, String(v)),
    fallback
  );
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: translate }),
}));

vi.mock('next/link', () => ({
  default: ({ children }: { children: React.ReactNode }) => React.createElement('a', null, children),
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: () => React.createElement('span'),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => React.createElement('span', null, 'Loading'),
}));

vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: () => void }) => unknown) =>
    selector({ addToast: () => {} }),
}));

vi.mock('../../workspace/components', () => ({
  ConfirmationDialog: () => React.createElement('div'),
}));

vi.mock('../components/workflow-studio', () => ({
  WorkflowStudio: () => React.createElement('div', { 'data-testid': 'workflow-studio' }),
}));
vi.mock('../components/workflow-definition-panel', () => ({
  WorkflowDefinitionPanel: () => React.createElement('div'),
}));
vi.mock('../components/run-inspector', () => ({
  RunInspector: () => React.createElement('div'),
}));
vi.mock('../components/workflow-triggers-panel', () => ({
  WorkflowTriggersPanel: () => React.createElement('div'),
}));

const listVersions = vi.fn();
const getVersionSource = vi.fn();
vi.mock('../api', () => ({
  WorkflowsApi: {
    getWorkflow: vi.fn(),
    listTriggers: vi.fn(),
    listRuns: vi.fn(),
    listVersions: (...args: unknown[]) => listVersions(...args),
    getVersionSource: (...args: unknown[]) => getVersionSource(...args),
    answerRun: vi.fn(),
    deleteWorkflow: vi.fn(),
  },
}));

let api: typeof import('../api').WorkflowsApi;

const EMPTY_IR: WorkflowIR = { nodes: [], edges: [], entry_node_id: null };

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    workflowId: 'wf-1',
    orgId: 'org-1',
    kind: 'agent_task',
    name: 'My Workflow',
    description: '',
    currentVersionId: null,
    triggers: [],
    status: 'active',
    requiredScopes: [],
    executionKind: 'agent_task',
    createdByUserId: 'u-1',
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function serverError(message: string) {
  return { type: ErrorType.SERVER_ERROR, message, statusCode: 503 };
}

function notFoundError(message = 'Not found') {
  return { type: ErrorType.NOT_FOUND, message, statusCode: 404 };
}

const h = React.createElement;

async function renderDetailView(workflow: Workflow) {
  ({ WorkflowsApi: api } = await import('../api'));
  vi.mocked(api.getWorkflow).mockResolvedValue(workflow);
  vi.mocked(api.listTriggers).mockResolvedValue({ triggers: [] });
  vi.mocked(api.listRuns).mockResolvedValue({ runs: [], total: 0, limit: 20, offset: 0, hasMore: false });

  render(
    h(Theme, null, h(WorkflowDetailView, { workflowId: 'wf-1', onBack: vi.fn() }))
  );
  await waitFor(() => screen.getByText('My Workflow'));
}

beforeEach(() => {
  listVersions.mockReset();
  getVersionSource.mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkflowDetailView version loading', () => {
  it('shows the Generate Code entry point when there simply are no versions yet (200, empty list)', async () => {
    listVersions.mockResolvedValue({ versions: [] });

    await renderDetailView(makeWorkflow());

    expect(await screen.findByText(/optionally generate code/i)).toBeTruthy();
    expect(screen.queryByText(/Could not load/)).toBeNull();
  });

  it('shows an error banner with Retry instead of Generate Code when the version store is unavailable', async () => {
    // BUG-1/BUG-4: a 503 from the version store must not be papered over as
    // "no code was ever generated."
    listVersions.mockRejectedValue(serverError('Version store unavailable for workflow wf-1'));

    await renderDetailView(makeWorkflow());

    expect(await screen.findByText(/Could not load this workflow's code/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy();
    expect(screen.queryByText(/optionally generate code/i)).toBeNull();
  });

  it('retrying re-fetches and can recover into the normal Generate Code state', async () => {
    listVersions.mockRejectedValueOnce(serverError('Version store unavailable for workflow wf-1'));
    listVersions.mockResolvedValueOnce({ versions: [] });

    await renderDetailView(makeWorkflow());
    await screen.findByRole('button', { name: /Retry/ });

    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));

    await waitFor(() => expect(screen.queryByText(/Could not load/)).toBeNull());
    expect(await screen.findByText(/optionally generate code/i)).toBeTruthy();
  });

  it('treats a 404 from listVersions as "no versions yet", not an error', async () => {
    listVersions.mockRejectedValue(notFoundError());

    await renderDetailView(makeWorkflow());

    expect(await screen.findByText(/optionally generate code/i)).toBeTruthy();
    expect(screen.queryByText(/Could not load/)).toBeNull();
  });

  it('renders WorkflowStudio once versions are present, even for an agent_task workflow', async () => {
    // BUG-2 downstream effect: a version whose pin failed is still listed,
    // so the studio (not "Generate Code") must show up for it.
    const version: WorkflowVersionSummary = {
      versionId: 'ver-1',
      workflowId: 'wf-1',
      versionNumber: 1,
      sdkVersion: '0.1.0',
      contentHash: 'abc1234',
      hasBundleRef: true,
      createdAt: '2026-01-01T00:00:00Z',
      createdByUserId: 'u-1',
      ir: EMPTY_IR,
      verifierVersion: 1,
      verifiedAt: '2026-01-01T00:00:00Z',
      needsRegeneration: false,
    };
    listVersions.mockResolvedValue({ versions: [version] });
    getVersionSource.mockResolvedValue({ versionId: 'ver-1', workflowId: 'wf-1', source: 'code' });

    await renderDetailView(makeWorkflow({ kind: 'agent_task', currentVersionId: null }));

    expect(await screen.findByTestId('workflow-studio')).toBeTruthy();
    expect(screen.queryByText(/optionally generate code/i)).toBeNull();
  });

  it('shows a regenerate banner when the active version predates the current verifier', async () => {
    // Phase 4: a version pinned before a fatal-at-runtime verifier rule
    // existed keeps running the old code indefinitely with no re-verify on
    // run -- this banner is the only signal a user gets that it happened.
    const staleVersion: WorkflowVersionSummary = {
      versionId: 'ver-1',
      workflowId: 'wf-1',
      versionNumber: 1,
      sdkVersion: '0.1.0',
      contentHash: 'abc1234',
      hasBundleRef: true,
      createdAt: '2026-01-01T00:00:00Z',
      createdByUserId: 'u-1',
      ir: EMPTY_IR,
      verifierVersion: 0,
      verifiedAt: null,
      needsRegeneration: true,
    };
    listVersions.mockResolvedValue({ versions: [staleVersion] });
    getVersionSource.mockResolvedValue({ versionId: 'ver-1', workflowId: 'wf-1', source: 'code' });

    await renderDetailView(makeWorkflow({ kind: 'code', currentVersionId: 'ver-1' }));

    expect(await screen.findByText(/generated before recent SDK safety checks/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Regenerate/ })).toBeTruthy();
  });

  it('does not show a regenerate banner when the active version is current', async () => {
    const currentVersion: WorkflowVersionSummary = {
      versionId: 'ver-1',
      workflowId: 'wf-1',
      versionNumber: 1,
      sdkVersion: '0.1.0',
      contentHash: 'abc1234',
      hasBundleRef: true,
      createdAt: '2026-01-01T00:00:00Z',
      createdByUserId: 'u-1',
      ir: EMPTY_IR,
      verifierVersion: 1,
      verifiedAt: '2026-01-01T00:00:00Z',
      needsRegeneration: false,
    };
    listVersions.mockResolvedValue({ versions: [currentVersion] });
    getVersionSource.mockResolvedValue({ versionId: 'ver-1', workflowId: 'wf-1', source: 'code' });

    await renderDetailView(makeWorkflow({ kind: 'code', currentVersionId: 'ver-1' }));

    await screen.findByTestId('workflow-studio');
    expect(screen.queryByText(/generated before recent SDK safety checks/i)).toBeNull();
  });
});

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

const h = vi.hoisted(() => ({
  get: vi.fn(),
  listConversations: vi.fn(),
  update: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/projects',
}));
vi.mock('@/chat/project-api', async (importActual) => ({
  ...(await importActual<typeof import('@/chat/project-api')>()),
  ProjectApi: { get: h.get, listConversations: h.listConversations, update: h.update },
}));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: vi.fn(async () => []) }));
vi.mock('@/chat/hooks/use-project-scope-hydration', () => ({ useProjectScopeHydration: () => {} }));
vi.mock('@/chat/components/chat-input', () => ({ ChatInput: () => null }));
vi.mock('@/app/components/share', () => ({ ShareSidebar: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status" /> }));
vi.mock('@/app/components/sidebar/sidebar-expand-button', () => ({ SidebarExpandButton: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => true }));
vi.mock('../settings-panel', () => ({
  // Stands in for the Tools card: toggles one tool the way the real card does.
  ProjectSettingsPanel: ({ onToolsChange }: { onToolsChange: (update: (tools: string[]) => string[]) => void }) => (
    <button type="button" onClick={() => onToolsChange((tools) => [...tools, 'jira.search'])}>Add Jira</button>
  ),
}));
vi.mock('../chat-defaults-card', () => ({ ChatDefaultsCard: () => null }));
vi.mock('../about-card', () => ({ AboutCard: () => null }));
vi.mock('../project-sharing-settings', () => ({ ProjectSharingSettings: () => null }));
vi.mock('@/chat/sidebar/dialogs', () => ({
  // Stands in for the rename dialog: reports a saved rename the way the real one does.
  EditProjectDialog: ({ project, onSaved }: { project: { name: string }; onSaved: (p: object) => void }) => (
    <button type="button" onClick={() => onSaved({ ...project, name: 'Renamed plan', role: undefined })}>Save rename</button>
  ),
  DeleteProjectDialog: () => null,
  LeaveProjectDialog: () => null,
}));

import { clearSessionCache } from '@/lib/api/session-cache';
import { useProjectDetail } from '@/chat/hooks/use-project-detail';
import { ProjectWorkspaceRedesigned } from '../project-workspace-redesigned';

const PROJECT = { _id: 'p1', name: 'Launch plan', color: '#f00', role: 'owner', tools: [], members: [], instructions: '' };

async function renderWorkspace() {
  render(<Theme><ProjectWorkspaceRedesigned projectId="p1" /></Theme>);
  await screen.findByRole('button', { name: 'Add Jira' });
}

/** What the project chat and the project sidebar read. */
const readCache = () => renderHook(() => useProjectDetail('p1').project);

beforeEach(() => {
  vi.clearAllMocks();
  clearSessionCache();
  h.get.mockResolvedValue(PROJECT);
  h.listConversations.mockResolvedValue({ conversations: [], pagination: { totalCount: 0 } });
});

afterEach(() => cleanup());

describe('project workspace and the shared project cache', () => {
  it('puts the loaded project in the cache, so a reader shows it at once', async () => {
    await renderWorkspace();

    const reader = readCache();

    expect(reader.result.current?.name).toBe('Launch plan');
  });

  it('shows a rename to the cache readers without a request, keeping the role', async () => {
    // The project sidebar is mounted next to the workspace.
    const reader = readCache();
    await renderWorkspace();
    await waitFor(() => expect(reader.result.current?.name).toBe('Launch plan'));
    const requests = h.get.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: 'Save rename' }));

    await waitFor(() => expect(reader.result.current?.name).toBe('Renamed plan'));
    expect(reader.result.current?.role).toBe('owner');
    expect(h.get).toHaveBeenCalledTimes(requests);
  });

  it('shows a tool toggle to the cache readers', async () => {
    h.update.mockResolvedValue({ ...PROJECT, tools: ['jira.search'] });
    const reader = readCache();
    await renderWorkspace();
    await waitFor(() => expect(reader.result.current?.name).toBe('Launch plan'));
    const requests = h.get.mock.calls.length;

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Add Jira' }));
    });

    await waitFor(() => expect(reader.result.current?.tools).toEqual(['jira.search']));
    expect(h.get).toHaveBeenCalledTimes(requests);
  });
});

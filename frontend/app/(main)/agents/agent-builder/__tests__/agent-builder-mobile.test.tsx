import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { agentDetail, installBrowserShims, knowledgeBase, model, renderInTheme, toolset } from './agent-builder-harness';

vi.setConfig({ testTimeout: 20_000 });

// A phone: narrow and touch-only.
vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => true,
  useIsBelowDesktop: () => true,
}));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => false }));

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/agents/edit',
}));

const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', () => ({
  useUserPermission: (key: string) => !permissions.denied.has(key),
  PermissionLockIcon: () => <span>Locked</span>,
  InheritedConfigNotice: () => null,
}));

// Nothing here may reach the network: every API module below is faked, and
// this catches anything that slips through.
vi.mock('@/lib/api/axios-instance', () => {
  const refuse = () => Promise.reject(new Error('Unexpected network call in a unit test'));
  const apiClient = { get: refuse, post: refuse, put: refuse, patch: refuse, delete: refuse };
  return { apiClient, default: apiClient };
});

const agentsApi = vi.hoisted(() => ({
  getAgent: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  getAllKnowledgeBasesForBuilder: vi.fn(),
  getAllKnowledgeHubAppNodes: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api')>()),
  AgentsApi: agentsApi,
}));

const fetchAvailableLlms = vi.hoisted(() => vi.fn());
vi.mock('@/chat/api', () => ({ ChatApi: { fetchAvailableLlms } }));

const toolsetsApi = vi.hoisted(() => ({
  getAllMyToolsets: vi.fn(),
  getAllAgentToolsets: vi.fn(),
  getToolsetRegistrySchema: vi.fn(),
  findAgentToolsetByInstanceId: vi.fn(),
  authenticateAgentToolset: vi.fn(),
  updateAgentToolsetCredentials: vi.fn(),
}));
vi.mock('@/app/(main)/toolsets/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/toolsets/api')>()),
  ToolsetsApi: toolsetsApi,
}));

vi.mock('@/app/(main)/workspace/skills/personal/api', () => ({
  SkillsApi: { listAssignableSkills: vi.fn(async () => []) },
}));
const mcpApi = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(async () => ({ instances: [] as unknown[] })),
  getAgentMcpServers: vi.fn(async () => ({ instances: [] as unknown[] })),
}));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/(main)/workspace/web-search/api', () => ({
  WebSearchApi: { getConfig: vi.fn(async () => ({ providers: [] })) },
}));
const getUsersByIds = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds } }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AgentBuilder } from '../agent-builder';

beforeEach(() => {
  installBrowserShims();
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false, ENABLE_SKILLS: false } });
  agentsApi.getAllKnowledgeBasesForBuilder.mockResolvedValue({ knowledgeBases: [knowledgeBase()] });
  agentsApi.getAllKnowledgeHubAppNodes.mockResolvedValue([]);
  fetchAvailableLlms.mockResolvedValue([model()]);
  toolsetsApi.getAllMyToolsets.mockResolvedValue({ toolsets: [toolset()] });
  toolsetsApi.getAllAgentToolsets.mockResolvedValue([]);
  getUsersByIds.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function canvasNode(label: string): HTMLElement | null {
  const nodes = Array.from(document.querySelectorAll<HTMLElement>('.react-flow__node'));
  return nodes.find((n) => n.getAttribute('data-id') !== 'agent-core-1' && n.textContent?.includes(label)) ?? null;
}

describe('the agent builder on a phone', () => {
  it('opens on the canvas, with the palette a tap away', async () => {
    renderInTheme(<AgentBuilder agentKey={null} />);
    await screen.findByText('Agent', { selector: 'span' });

    expect(screen.queryByPlaceholderText('Search nodes…')).toBeNull();
    expect(screen.getByRole('button', { name: 'Add to canvas' })).toBeTruthy();
  });

  it('adds a toolset by tapping it in the palette sheet, then closes the sheet', async () => {
    renderInTheme(<AgentBuilder agentKey={null} />);
    await screen.findByText('Agent', { selector: 'span' });

    fireEvent.click(screen.getByRole('button', { name: 'Add to canvas' }));
    const sheet = await screen.findByRole('dialog', { name: 'Palette' });
    const row = (await within(sheet).findByText('Team Jira')).closest('.agent-builder-sidebar-category-row') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Add to canvas' }));

    await waitFor(() => expect(canvasNode('Jira')).toBeTruthy());
    expect(screen.queryByRole('dialog', { name: 'Palette' })).toBeNull();
  });

  it('still opens the palette for a viewer who cannot edit, without offering to add', async () => {
    agentsApi.getAgent.mockResolvedValue({ agent: agentDetail({ can_edit: false }) });
    renderInTheme(<AgentBuilder agentKey="agent-1" />);

    const open = await screen.findByRole('button', { name: 'Palette' });
    expect(screen.queryByRole('button', { name: 'Add to canvas' })).toBeNull();
    fireEvent.click(open);
    expect(await screen.findByRole('dialog', { name: 'Palette' })).toBeTruthy();
  });
});

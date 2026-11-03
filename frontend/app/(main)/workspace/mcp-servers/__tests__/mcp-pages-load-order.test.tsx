import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { McpMyServerEntry } from '../types';

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
const mcpApi = vi.hoisted(() => ({ getMyMcpServers: vi.fn(), getCatalog: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import PersonalMcpServersPage from '../personal/page';

function server(name: string): McpMyServerEntry {
  return {
    _id: `inst-${name}`, orgId: 'org-1', createdBy: 'u-1', name, transport: 'streamable_http',
    authMode: 'none', useAdminAuth: false, args: [], requiredEnv: [], optionalEnv: [], scopes: [],
    isCustom: true, createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [],
  };
}

beforeEach(() => {
  installBrowserShims();
  useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true } } as never);
  mcpApi.getCatalog.mockResolvedValue({ templates: [], customStdioAllowed: false });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the personal MCP servers page', () => {
  it('keeps the newest list when an older load answers last', async () => {
    let answerFirst: (value: { instances: McpMyServerEntry[] }) => void = () => {};
    mcpApi.getMyMcpServers
      .mockReturnValueOnce(new Promise((resolve) => { answerFirst = resolve; }))
      .mockResolvedValueOnce({ instances: [server('Newer')] });

    renderInTheme(<PersonalMcpServersPage />);
    await waitFor(() => expect(mcpApi.getMyMcpServers).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Newer')).toBeTruthy();

    await act(async () => answerFirst({ instances: [server('Older')] }));

    expect(screen.getByText('Newer')).toBeTruthy();
    expect(screen.queryByText('Older')).toBeNull();
  });
});

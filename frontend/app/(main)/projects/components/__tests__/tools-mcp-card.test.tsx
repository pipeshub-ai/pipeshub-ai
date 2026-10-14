import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { buildCatalogMcpGroups } from '@/chat/tool-groups';

const catalog = vi.hoisted(() => ({ fetchToolCatalog: vi.fn() }));
vi.mock('@/chat/hooks/use-project-scope-hydration', () => catalog);
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ToolsMcpCard } from '../tools-mcp-card';

const mcpGroups = buildCatalogMcpGroups([
  {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'GitHub',
    typeId: 'github',
    transport: 'streamable_http',
    authMode: 'none',
    useAdminAuth: false,
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: false,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [
      { name: 'list_issues', namespacedName: 'mcp_github_list_issues', inputSchema: {} },
      { name: 'create_pr', namespacedName: 'mcp_github_create_pr', inputSchema: {} },
    ],
  },
]);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ToolsMcpCard', () => {
  it('selects a single tool of an MCP server', async () => {
    catalog.fetchToolCatalog.mockResolvedValue({ toolGroups: [], mcpGroups });
    const onChange = vi.fn();
    render(
      <Theme>
        <ToolsMcpCard selectedTools={[]} canEdit onChange={onChange} />
      </Theme>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    fireEvent.click(screen.getByText('create pr'));

    expect(onChange).toHaveBeenCalledWith(['mcp_github_create_pr']);
    expect(screen.getByText('list issues')).toBeTruthy();
  });
});

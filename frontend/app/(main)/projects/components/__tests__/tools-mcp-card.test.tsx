import React, { useMemo, useRef, useState } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { buildCatalogMcpGroups } from '@/chat/tool-groups';

const catalog = vi.hoisted(() => ({ fetchToolCatalog: vi.fn() }));
vi.mock('@/chat/hooks/use-project-scope-hydration', () => catalog);
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ToolsMcpCard, toolLabel } from '../tools-mcp-card';
import { createSerialSaver } from '../../utils/serial-saver';

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
      { name: 'list_issues', namespacedName: 'mcp_github_list_issues', inputSchema: {}, kind: 'read', description: 'Lists issues' },
      { name: 'create_pr', namespacedName: 'mcp_github_create_pr', inputSchema: {}, kind: 'write' },
      { name: 'delete_repo', namespacedName: 'mcp_github_delete_repo', inputSchema: {}, kind: 'destructive' },
    ],
  },
]);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('toolLabel', () => {
  it('turns a raw toolset name into words', () => {
    expect(toolLabel('slack.send_message', undefined)).toBe('Send message');
    expect(toolLabel('slack.getChannelHistory', undefined)).toBe('Get Channel History');
  });
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
    fireEvent.click(screen.getByText('Create pr'));

    expect(onChange).toHaveBeenCalledTimes(1);
    const update = onChange.mock.calls[0]![0] as (previous: string[]) => string[];
    expect(update([])).toEqual(['mcp_github_create_pr']);
    expect(screen.getByText('List issues')).toBeTruthy();
  });

  it('groups tools by what they do, shows descriptions, and marks destructive ones as needing approval', async () => {
    catalog.fetchToolCatalog.mockResolvedValue({ toolGroups: [], mcpGroups });
    render(
      <Theme>
        <ToolsMcpCard selectedTools={[]} canEdit onChange={vi.fn()} />
      </Theme>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    expect(screen.getByText('Reads data')).toBeTruthy();
    expect(screen.getByText('Makes changes')).toBeTruthy();
    expect(screen.getByText('Lists issues')).toBeTruthy();
    expect(screen.getByText('Needs approval')).toBeTruthy();
  });

  it('keeps every click when tools are toggled faster than the server answers', async () => {
    catalog.fetchToolCatalog.mockResolvedValue({ toolGroups: [], mcpGroups });
    const pending: Array<{ tools: string[]; resolve: () => void }> = [];
    const save = vi.fn(
      (tools: string[]) =>
        new Promise<string[]>((resolve) => {
          pending.push({ tools, resolve: () => resolve(tools) });
        }),
    );

    // Mirrors the workspace: local list in a ref, saves serialized, responses never rewind newer clicks.
    function Harness() {
      const [tools, setTools] = useState<string[]>([]);
      const toolsRef = useRef<string[]>([]);
      const saver = useMemo(
        () => createSerialSaver<string[], string[]>({ save, onSaved: (saved) => setTools(saved), onError: vi.fn() }),
        [],
      );
      return (
        <ToolsMcpCard
          selectedTools={tools}
          canEdit
          onChange={(update) => {
            toolsRef.current = update(toolsRef.current);
            setTools(toolsRef.current);
            void saver.push(toolsRef.current);
          }}
        />
      );
    }

    render(
      <Theme>
        <Harness />
      </Theme>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    for (const label of ['List issues', 'Create pr', 'Delete repo']) {
      fireEvent.click(screen.getByRole('checkbox', { name: label }));
    }
    expect(save).toHaveBeenCalledTimes(1);

    // The first (stale) response lands after the other clicks.
    await act(async () => pending[0]!.resolve());
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(new Set(pending[1]!.tools)).toEqual(
      new Set(['mcp_github_list_issues', 'mcp_github_create_pr', 'mcp_github_delete_repo']),
    );
    for (const label of ['List issues', 'Create pr', 'Delete repo']) {
      expect(screen.getByRole('checkbox', { name: label }).getAttribute('aria-checked')).toBe('true');
    }
    await act(async () => pending[1]!.resolve());
  });

  it("shows an MCP server's own logo", async () => {
    catalog.fetchToolCatalog.mockResolvedValue({ toolGroups: [], mcpGroups });
    render(
      <Theme>
        <ToolsMcpCard selectedTools={[]} canEdit onChange={vi.fn()} />
      </Theme>,
    );

    expect(await screen.findByRole('img', { name: 'github icon' })).toBeTruthy();
  });
});

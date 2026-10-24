import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import type { NodeTemplate } from '../types';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore } from '@/lib/store/user-store';
import {
  filterMcpServerTools,
  filterPaletteGroup,
  filterTemplatesBySearch,
  filterToolsetTools,
  matchesPaletteSearch,
} from '../sidebar-utils';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/sidebar-web-search-section', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/sidebar-web-search-section')>()),
  AgentBuilderWebSearchSection: () => null,
}));
const webSearch = vi.hoisted(() => ({ configured: [] as { provider: string }[] }));
vi.mock('../hooks/use-web-search-config', () => ({
  useWebSearchConfig: () => ({ configuredProviders: webSearch.configured, loading: false, error: null, reload: async () => {} }),
}));

import { AgentBuilderSidebar } from '../components/agent-builder-sidebar';

function toolset(overrides: Partial<BuilderSidebarToolset> = {}): BuilderSidebarToolset {
  return {
    name: 'gmail', normalized_name: 'gmail', displayName: 'Gmail', description: 'Read and send email',
    iconPath: '', category: 'app', toolCount: 2,
    tools: [
      { name: 'send_email', fullName: 'gmail.send_email', description: 'Send a message' },
      { name: 'search_emails', fullName: 'gmail.search_emails', description: 'Find messages' },
    ],
    isConfigured: true, isAuthenticated: true, isFromRegistry: false, instanceId: 'inst-gmail',
    instanceName: 'Team Gmail', toolsetType: 'gmail', scope: 'org', ...overrides,
  };
}

function mcpServer(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'mcp-1', name: 'Docs Server', typeId: 'docs', authMode: 'none', isAuthenticated: true,
    tools: [{ name: 'lookup_page', namespacedName: 'docs.lookup_page', description: 'Fetch a wiki page', inputSchema: {} }],
    ...overrides,
  } as McpMyServerEntry;
}

function template(overrides: Partial<NodeTemplate>): NodeTemplate {
  return { type: 'x', label: 'X', description: '', icon: '', defaultConfig: {}, category: 'llm', ...overrides };
}

const templates: NodeTemplate[] = [
  template({ type: 'llm-gpt', label: 'GPT-4o', description: 'openAI model', defaultConfig: { provider: 'openAI', modelName: 'gpt-4o' } }),
  template({ type: 'llm-sonnet', label: 'Claude Sonnet', description: 'anthropic model', defaultConfig: { provider: 'anthropic' } }),
  template({ type: 'kb-group', label: 'Collections', category: 'knowledge' }),
  template({ type: 'kb-1', label: 'Sales playbook', category: 'knowledge' }),
  template({ type: 'kb-2', label: 'Engineering wiki', category: 'knowledge' }),
  template({ type: 'skill-pdf', label: 'Pdf', description: 'Work with PDF files', category: 'skills', defaultConfig: { skillName: 'pdf' } }),
  template({ type: 'skill-xlsx', label: 'Xlsx', description: 'Spreadsheets', category: 'skills', defaultConfig: { skillName: 'xlsx' } }),
];

describe('palette search helpers', () => {
  it('matches case-insensitively, trims the query and treats an empty query as a match', () => {
    expect(matchesPaletteSearch('  GMAIL ', 'Team Gmail')).toBe(true);
    expect(matchesPaletteSearch('', 'anything')).toBe(true);
    expect(matchesPaletteSearch('drive', 'Gmail', undefined, null)).toBe(false);
  });

  it('shows every item when the query names the group, matching items otherwise, and null when nothing matches', () => {
    const items = ['Send', 'Search'];
    expect(filterPaletteGroup('mail', ['Gmail'], items, (i) => [i])).toEqual(items);
    expect(filterPaletteGroup('sea', ['Gmail'], items, (i) => [i])).toEqual(['Search']);
    expect(filterPaletteGroup('zzz', ['Gmail'], items, (i) => [i])).toBeNull();
    // A group that names the query still shows even without items.
    expect(filterPaletteGroup('gmail', ['Gmail'], [], () => [])).toEqual([]);
  });

  it('filters toolset tools by toolset name, tool name and tool description', () => {
    expect(filterToolsetTools(toolset(), 'gmail')).toHaveLength(2);
    expect(filterToolsetTools(toolset(), 'team')).toHaveLength(2);
    expect(filterToolsetTools(toolset(), 'send_')?.map((t) => t.name)).toEqual(['send_email']);
    expect(filterToolsetTools(toolset(), 'find')?.map((t) => t.name)).toEqual(['search_emails']);
    expect(filterToolsetTools(toolset(), 'drive')).toBeNull();
  });

  it('filters MCP servers by server name and tool name/description', () => {
    expect(filterMcpServerTools(mcpServer(), 'docs server')).toHaveLength(1);
    expect(filterMcpServerTools(mcpServer(), 'wiki')).toHaveLength(1);
    expect(filterMcpServerTools(mcpServer(), 'gmail')).toBeNull();
  });

  it('filters node templates without failing on missing descriptions', () => {
    const noDescription = template({ label: 'Plain', description: undefined as unknown as string });
    expect(filterTemplatesBySearch([noDescription], 'plain')).toEqual([noDescription]);
    expect(filterTemplatesBySearch(templates, 'SONNET').map((t) => t.type)).toEqual(['llm-sonnet']);
  });
});

function renderSidebar() {
  return renderInTheme(
    <AgentBuilderSidebar
      open
      width={320}
      loading={false}
      nodeTemplates={templates}
      availableSkills={[]}
      configuredConnectors={[]}
      toolsets={[toolset()]}
      activeToolsetTypeKeys={new Set()}
      toolsetMergeCheckNodes={[]}
      refreshToolsets={async () => {}}
      mcpServers={[mcpServer()]}
      mcpMergeCheckNodes={[]}
      refreshMcpServers={async () => {}}
      onNotify={vi.fn()}
      webSearchAttached={null}
    />,
  );
}

const typeQuery = (value: string) =>
  fireEvent.change(screen.getByPlaceholderText('Search nodes…'), { target: { value } });

beforeEach(() => {
  installBrowserShims();
  useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true, ENABLE_ACTIONS: true, ENABLE_SKILLS: true } });
  useUserStore.setState({ profile: { isAdmin: true } } as never);
  webSearch.configured = [];
});

afterEach(() => {
  cleanup();
});

describe('palette search box', () => {
  it('filters every section, opens the ones with matches and hides the rest', () => {
    renderSidebar();
    // AI models start collapsed; MCP server rows start collapsed.
    expect(screen.queryByText('GPT-4o')).toBeNull();
    expect(screen.queryByText('lookup_page')).toBeNull();

    typeQuery('sonnet');
    expect(screen.getByText('Claude Sonnet')).toBeTruthy();
    expect(screen.queryByText('GPT-4o')).toBeNull();
    expect(screen.queryByText('Knowledge')).toBeNull();
    expect(screen.queryByText('Skills')).toBeNull();

    typeQuery('wiki');
    expect(screen.getByText('Engineering wiki')).toBeTruthy();
    expect(screen.queryByText('Sales playbook')).toBeNull();
    // The MCP tool matches on its description, so its server opens.
    expect(screen.getByText('Lookup Page')).toBeTruthy();
    expect(screen.queryByText('Team Gmail')).toBeNull();

    typeQuery('send_');
    expect(screen.getByText('Team Gmail')).toBeTruthy();
    expect(screen.getByText('Send Email')).toBeTruthy();
    expect(screen.queryByText('Search Emails')).toBeNull();

    typeQuery('pdf');
    expect(screen.getByText('Pdf')).toBeTruthy();
    expect(screen.queryByText('Xlsx')).toBeNull();

    typeQuery('zzqxv');
    expect(screen.getByText('No results found')).toBeTruthy();
    expect(screen.queryByText('Tools')).toBeNull();
  });

  it('restores the palette when the query is cleared', () => {
    renderSidebar();
    typeQuery('pdf');
    typeQuery('');
    expect(screen.getByText('Xlsx')).toBeTruthy();
    expect(screen.getByText('Sales playbook')).toBeTruthy();
    expect(screen.getByText('Team Gmail')).toBeTruthy();
    expect(screen.queryByText('GPT-4o')).toBeNull();
    expect(screen.queryByText('No results found')).toBeNull();
  });

  it('counts only the web search providers this user can see', () => {
    // Serper needs setup: an admin sees it unconfigured, a member only once it is configured.
    renderSidebar();
    typeQuery('serper');
    expect(screen.queryByText('No results found')).toBeNull();
    cleanup();

    useUserStore.setState({ profile: { isAdmin: false } } as never);
    renderSidebar();
    typeQuery('serper');
    expect(screen.getByText('No results found')).toBeTruthy();
    cleanup();

    webSearch.configured = [{ provider: 'serper' }];
    renderSidebar();
    typeQuery('serper');
    expect(screen.queryByText('No results found')).toBeNull();
  });
});

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const api = vi.hoisted(() => ({
  getMyToolsets: vi.fn(),
  getAllMyToolsets: vi.fn(),
  findMyToolsetByInstanceId: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: api, MAX_TOOLSETS_LIST_LIMIT: 200 }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({
  McpServersApi: { getMyMcpServers: vi.fn().mockResolvedValue({ instances: [] }) },
}));
vi.mock('@/app/(main)/workspace/api-toolsets/api', () => ({
  ApiToolsetsApi: { listInstances: vi.fn().mockResolvedValue([]) },
}));
// The sources tab: shows what it was given and offers one pick.
vi.mock('../connectors-collections/collections-tab', () => ({
  CollectionsTab: ({
    selection,
    onSelectionChange,
  }: {
    selection: { apps: string[]; kb: string[] };
    onSelectionChange: (next: { apps: string[]; kb: string[] }) => void;
  }) => (
    <div>
      <p>Narrowed to: {[...selection.apps, ...selection.kb].join(', ') || 'nothing'}</p>
      <button type="button" onClick={() => onSelectionChange({ apps: [], kb: ['kb-handbook'] })}>
        Choose the handbook
      </button>
    </div>
  ),
}));

import { useChatStore } from '@/chat/store';
import { UniversalAgentResourcesPanel } from '../universal-agent-resources-panel';

const initial = useChatStore.getState();

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState(initial, true);
  useChatStore.setState({ settings: { ...initial.settings, filters: { apps: ['app-drive'], kb: [] } } });
  api.getMyToolsets.mockResolvedValue({ toolsets: [], hasNext: false });
  api.getAllMyToolsets.mockResolvedValue({ toolsets: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('UniversalAgentResourcesPanel — whose sources it narrows', () => {
  it('narrows the chat by default', () => {
    renderInTheme(<UniversalAgentResourcesPanel />);

    expect(screen.getByText('Narrowed to: app-drive')).toBeTruthy();
    fireEvent.click(screen.getByText('Choose the handbook'));

    expect(useChatStore.getState().settings.filters).toEqual({ apps: [], kb: ['kb-handbook'] });
  });

  it('narrows only the composer that passes its own sources, and resets those', () => {
    const onSelectionChange = vi.fn();
    renderInTheme(
      <UniversalAgentResourcesPanel selection={{ apps: [], kb: ['kb-team'] }} onSelectionChange={onSelectionChange} />,
    );

    expect(screen.getByText('Narrowed to: kb-team')).toBeTruthy();
    fireEvent.click(screen.getByText('Choose the handbook'));
    expect(onSelectionChange).toHaveBeenLastCalledWith({ apps: [], kb: ['kb-handbook'] });

    fireEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith({ apps: [], kb: [] });

    expect(useChatStore.getState().settings.filters).toEqual({ apps: ['app-drive'], kb: [] });
  });
});

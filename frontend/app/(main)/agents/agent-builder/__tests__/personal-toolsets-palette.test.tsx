import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/sidebar-web-search-section', () => ({ AgentBuilderWebSearchSection: () => null }));

import { AgentBuilderToolsetsSection } from '../components/sidebar-toolsets-section';

function toolset(overrides: Partial<BuilderSidebarToolset> = {}): BuilderSidebarToolset {
  return {
    name: 'jira', normalized_name: 'jira', displayName: 'Jira', description: '', iconPath: '', category: 'app',
    toolCount: 1, tools: [{ name: 'search_issues', fullName: 'jira.search_issues', description: '' }],
    isConfigured: true, isAuthenticated: true, isFromRegistry: false, instanceId: 'inst-mine',
    instanceName: 'My Jira', toolsetType: 'jira', authType: 'API_TOKEN', scope: 'personal', ...overrides,
  };
}

function renderPalette(toolsets: BuilderSidebarToolset[], agentShared: boolean) {
  const onNotify = vi.fn();
  renderInTheme(
    <AgentBuilderToolsetsSection
      toolsets={toolsets}
      loading={false}
      refreshToolsets={async () => {}}
      activeToolsetTypeKeys={new Set()}
      toolsetMergeCheckNodes={[]}
      isServiceAccount={false}
      agentKey={null}
      onNotify={onNotify}
      webSearchAttached={null}
      agentShared={agentShared}
    />,
  );
  const drag = (label: string) => {
    const row = screen.getByText(label).closest('[draggable]') as HTMLElement;
    fireEvent.dragStart(row, { dataTransfer: { setData: vi.fn(), effectAllowed: '' } });
  };
  return { onNotify, drag };
}

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
});

describe('a personal action in the agent builder', () => {
  it("can't go on an agent shared with the organization", () => {
    const { onNotify, drag } = renderPalette([toolset()], true);
    drag('My Jira');
    expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('My Jira is your personal action'));
  });

  it('goes on an agent of their own', () => {
    const { onNotify, drag } = renderPalette([toolset()], false);
    drag('My Jira');
    expect(onNotify).not.toHaveBeenCalled();
  });

  it("an organization's action goes on a shared agent", () => {
    const { onNotify, drag } = renderPalette([toolset({ scope: 'org', instanceName: 'Team Jira' })], true);
    drag('Team Jira');
    expect(onNotify).not.toHaveBeenCalled();
  });
});

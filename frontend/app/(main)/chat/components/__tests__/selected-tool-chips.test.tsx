import React from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/(main)/workspace/mcp-servers/components', () => ({ McpServerIcon: () => null }));

import { groupSelectedTools, removeSelectedToolGroup } from '../selected-tool-chips';
import { SelectedFiltersRow } from '../selected-filters-row';
import { useChatStore } from '@/chat/store';

const jira = {
  label: 'Jira',
  toolsetSlug: 'jira',
  instanceId: 'j1',
  fullNames: ['j1:jira.search_issues', 'j1:jira.create_issue'],
};
const mcp = {
  label: 'GitHub MCP',
  toolsetSlug: 'mcp',
  instanceId: 'm1',
  fullNames: ['m1:mcp_github_list_prs'],
  toolLabels: { 'm1:mcp_github_list_prs': 'list_prs' },
};

describe('composer tool chips', () => {
  beforeAll(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
  afterAll(() => vi.unstubAllGlobals());
  afterEach(() => {
    cleanup();
    useChatStore.getState().setUniversalAgentStreamTools(null);
  });

  it('groups picked tools per instance, keeping keys no list knows under their instance', () => {
    const groups = groupSelectedTools(['j1:jira.search_issues', 'm1:mcp_github_list_prs', 'x9:slack.post'], [jira, mcp]);
    expect(groups.map((g) => [g.instanceId, g.label, g.isMcp, g.tools.map((t) => t.label)])).toEqual([
      ['j1', 'Jira', false, ['jira.search_issues']],
      ['m1', 'GitHub MCP', true, ['list_prs']],
      ['x9', '', false, ['slack.post']],
    ]);
  });

  it('drops one instance and goes back to every tool when none are left', () => {
    const store = useChatStore.getState();
    store.setUniversalAgentStreamTools(['j1:jira.search_issues', 'm1:mcp_github_list_prs']);
    removeSelectedToolGroup('j1');
    expect(useChatStore.getState().universalAgentStreamTools).toEqual(['m1:mcp_github_list_prs']);
    removeSelectedToolGroup('m1');
    expect(useChatStore.getState().universalAgentStreamTools).toBeNull();
  });

  it('shows a chip per instance after the source pills, with × when the row is editable', () => {
    const onRemove = vi.fn();
    render(
      <Theme>
        <SelectedFiltersRow
          collections={[{ id: 'a1', name: 'Drive', kind: 'connector' as const }]}
          removable
          onRemove={() => {}}
          toolGroups={groupSelectedTools(['j1:jira.search_issues', 'j1:jira.create_issue'], [jira])}
          onRemoveToolGroup={onRemove}
        />
      </Theme>
    );
    expect(screen.getByText('Drive')).toBeTruthy();
    expect(screen.getByText('Jira · 2')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Remove the tools picked from Jira'));
    expect(onRemove).toHaveBeenCalledWith('j1');
  });
});

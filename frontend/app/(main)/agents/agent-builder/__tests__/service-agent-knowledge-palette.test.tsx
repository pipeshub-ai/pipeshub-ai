import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { NodeTemplate } from '../types';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore } from '@/lib/store/user-store';
import { serviceAgentKnowledgeOptions } from '../sidebar-utils';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/sidebar-web-search-section', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/sidebar-web-search-section')>()),
  AgentBuilderWebSearchSection: () => null,
}));
vi.mock('../hooks/use-web-search-config', () => ({
  useWebSearchConfig: () => ({ configuredProviders: [], loading: false, error: null, reload: async () => {} }),
}));

import { AgentBuilderSidebar } from '../components/agent-builder-sidebar';

const connector = (key: string, scope: 'personal' | 'team') => ({ _key: key, name: key, type: 'DRIVE', scope }) as Connector;
const connectors = [connector('my-drive', 'personal'), connector('team-drive', 'team')];
const collections = [{ id: 'kb-1' }, { id: 'kb-2' }];

describe('serviceAgentKnowledgeOptions', () => {
  it('leaves an agent a person runs as it is', () => {
    expect(serviceAgentKnowledgeOptions(connectors, collections, { isServiceAccount: false, bound: false })).toEqual({
      connectors,
      knowledgeBases: collections,
    });
  });

  it('offers a service agent no personal connector and no collection until it is bound', () => {
    const unbound = serviceAgentKnowledgeOptions(connectors, collections, { isServiceAccount: true, bound: false });
    expect(unbound.connectors.map((c) => c._key)).toEqual(['team-drive']);
    expect(unbound.knowledgeBases).toEqual([]);

    const bound = serviceAgentKnowledgeOptions(connectors, collections, { isServiceAccount: true, bound: true });
    expect(bound.connectors.map((c) => c._key)).toEqual(['team-drive']);
    expect(bound.knowledgeBases).toEqual(collections);
  });
});

function renderSidebar(collectionsNeedBinding: boolean) {
  const templates: NodeTemplate[] = [
    { type: 'kb-group', label: 'Collections', description: '', icon: '', defaultConfig: {}, category: 'knowledge' },
  ];
  return renderInTheme(
    <AgentBuilderSidebar
      open
      width={320}
      loading={false}
      nodeTemplates={templates}
      availableSkills={[]}
      configuredConnectors={[]}
      toolsets={[]}
      activeToolsetTypeKeys={new Set()}
      toolsetMergeCheckNodes={[]}
      refreshToolsets={async () => {}}
      mcpServers={[]}
      mcpMergeCheckNodes={[]}
      refreshMcpServers={async () => {}}
      onNotify={vi.fn()}
      webSearchAttached={null}
      isServiceAccount
      collectionsNeedBinding={collectionsNeedBinding}
    />,
  );
}

beforeEach(() => {
  installBrowserShims();
  useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true, ENABLE_ACTIONS: true, ENABLE_SKILLS: true } });
  useUserStore.setState({ profile: { isAdmin: true } } as never);
});

afterEach(() => {
  cleanup();
});

describe('collections in a service agent palette', () => {
  it('says why there are none while the agent is not bound', () => {
    renderSidebar(true);
    expect(
      screen.getByText('Collections can be added once this service agent is bound to a service account.'),
    ).toBeTruthy();
  });

  it('says there are none otherwise', () => {
    renderSidebar(false);
    expect(screen.getByText('No collections available')).toBeTruthy();
  });
});

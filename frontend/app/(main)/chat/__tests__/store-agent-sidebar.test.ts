import { beforeEach, describe, expect, it } from 'vitest';

import { useChatStore } from '@/chat/store';

/** The agent fields the chat page fills in from the agent detail. */
function pageOwnedAgentFields() {
  const s = useChatStore.getState();
  return {
    tools: s.agentToolCatalogFullNames,
    toolGroups: s.agentChatToolGroups,
    kbIds: s.agentChatKbIds,
    knowledgeDefaults: s.agentKnowledgeDefaults,
    scope: s.agentKnowledgeScope,
    name: s.agentContextDisplayName,
    createdBy: s.agentContextCreatedBy,
    access: s.agentContextAccess,
  };
}

describe('the agent sidebar and the agent fields the chat page owns', () => {
  beforeEach(() => {
    const store = useChatStore.getState();
    store.reset();
    store.hydrateAgentChatResources({
      toolCatalogFullNames: ['jira.search'],
      toolGroups: [{ label: 'Jira', fullNames: ['jira.search'], toolsetSlug: 'jira' }],
      mcpGroups: [],
      connectors: [],
      kbIds: ['kb-1'],
      knowledgeCollectionRows: [],
      knowledgeDefaults: { apps: [], kb: ['kb-1'] },
      deprecatedToolNames: [],
    });
    store.setAgentKnowledgeScope({ apps: [], kb: ['kb-1'] });
    store.setAgentContextDisplayName('Support Bot');
    store.setAgentContextCreatedBy('creator-1');
    store.setAgentContextAccess({ canEdit: true } as never);
  });

  it('leaves them alone when the sidebar mounts, switches agent or unmounts', () => {
    const before = pageOwnedAgentFields();
    const store = useChatStore.getState();

    store.setAgentSidebarAgentId('agent-7');
    expect(pageOwnedAgentFields()).toEqual(before);
    store.setAgentSidebarAgentId('agent-8');
    expect(pageOwnedAgentFields()).toEqual(before);
    store.setAgentSidebarAgentId(null);
    expect(pageOwnedAgentFields()).toEqual(before);
  });

  it("resets the sidebar's own state when it switches agent", () => {
    const store = useChatStore.getState();
    store.setAgentSidebarAgentId('agent-7');
    store.setAgentConversations([{ id: 'c1' } as never]);
    store.toggleAgentMoreChatsPanel();

    store.setAgentSidebarAgentId('agent-8');

    const s = useChatStore.getState();
    expect(s.agentSidebarAgentId).toBe('agent-8');
    expect(s.agentConversations).toEqual([]);
    expect(s.isAgentMoreChatsPanelOpen).toBe(false);
  });
});

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import testI18n, { en } from '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters } from '@/chat/types';

const api = vi.hoisted(() => ({ listCollectionsForChat: vi.fn(), getNodeChildren: vi.fn(), searchNodes: vi.fn() }));

vi.mock('@/chat/api', () => ({
  ChatApi: { listCollectionsForChat: (...args: unknown[]) => api.listCollectionsForChat(...args) },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: {
    getNodeChildren: (...args: unknown[]) => api.getNodeChildren(...args),
    searchNodes: (...args: unknown[]) => api.searchNodes(...args),
  },
}));
vi.mock('@/chat/hooks/use-main-chat-connector-default-hint', () => ({
  useMainChatConnectorDefaultHint: () => false,
}));

import { CollectionsTab } from '../collections-tab';

const EMPTY: ChatKnowledgeFilters = { apps: [], kb: [] };
const connector = (id: string, name: string, updatedAtTimestamp: number) => ({
  id,
  name,
  nodeType: 'app',
  hasChildren: true,
  origin: 'CONNECTOR',
  connector: 'Jira',
  updatedAtTimestamp,
});

function renderTab(appearance?: 'apps-panel') {
  return render(
    <Theme>
      <CollectionsTab selection={EMPTY} onSelectionChange={() => {}} filterMode="connectors" appearance={appearance} />
    </Theme>,
  );
}

/** The root row: the element that carries the row box around a checkbox. */
const rowOf = (name: string) => screen.getByRole('checkbox', { name }).closest('[role="presentation"]') as HTMLElement;

beforeEach(() => {
  useChatStore.setState({ collectionMetaCache: {}, collectionNamesCache: {} });
  const now = Date.now();
  // One root from today and one from last year, so a date grouping would show two headers.
  api.listCollectionsForChat.mockResolvedValue({
    knowledgeBases: [connector('jira', 'Jira', now), connector('drive', 'Drive', now - 400 * 24 * 3600 * 1000)],
    nextCursor: null,
  });
  api.getNodeChildren.mockResolvedValue({ items: [], pagination: { nextCursor: null } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Connectors tab in the apps panel', () => {
  it('lists every connected app under one "Your apps" header', async () => {
    renderTab('apps-panel');
    await screen.findByText('Jira');
    const header = en.chat.mentionYourApps;
    expect(testI18n.t('chat.mentionYourApps')).toBe(header);
    expect(screen.getAllByText(header)).toHaveLength(1);
    expect(screen.getByText('Drive')).toBeTruthy();
  });

  it('draws the root row as the apps panel row', async () => {
    renderTab('apps-panel');
    await screen.findByText('Jira');
    const row = rowOf('Jira');
    expect(row.style.height).toBe('var(--space-7)'); // 40px
    expect(row.style.borderRadius).toBe('var(--radius-2)');
    expect(row.style.backgroundColor).toBe('var(--surface-subtle)');
    expect(row.style.border).toBe('1px solid var(--border-subtle)');
    expect((screen.getByText('Jira') as HTMLElement).style.color).toBe('var(--text-secondary)');
  });

  it('keeps the chevron first in the row, and it says whether the app is open', async () => {
    renderTab('apps-panel');
    await screen.findByText('Jira');
    const row = rowOf('Jira');
    const chevron = screen.getByRole('button', { name: 'Show what is in Jira' });
    expect(row.firstElementChild).toBe(chevron);
    expect(chevron.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(chevron);
    expect(chevron.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(chevron);
    expect(chevron.getAttribute('aria-expanded')).toBe('false');
  });

  it('leaves other pickers on the default row and date groups', async () => {
    renderTab();
    await screen.findByText('Jira');
    expect(screen.queryByText(en.chat.mentionYourApps)).toBeNull();
    expect(rowOf('Jira').style.borderRadius).toBe('var(--radius-1)');
  });
});

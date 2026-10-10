import React, { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters } from '@/chat/types';

const api = vi.hoisted(() => ({ listCollectionsForChat: vi.fn(), getNodeChildren: vi.fn() }));

vi.mock('@/chat/api', () => ({
  ChatApi: { listCollectionsForChat: (...args: unknown[]) => api.listCollectionsForChat(...args) },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: { getNodeChildren: (...args: unknown[]) => api.getNodeChildren(...args) },
}));
vi.mock('@/chat/hooks/use-main-chat-connector-default-hint', () => ({
  useMainChatConnectorDefaultHint: () => false,
}));

import { CollectionsTab } from '../chat-panel/expansion-panels/connectors-collections/collections-tab';

const EMPTY: ChatKnowledgeFilters = { apps: [], kb: [] };

const root = (id: string, name: string, hasChildren: boolean, origin = 'COLLECTION') => ({
  id,
  name,
  nodeType: 'app',
  hasChildren,
  origin,
  connector: origin === 'COLLECTION' ? 'KB' : 'Jira',
});
const child = (id: string, name: string, nodeType: string, hasChildren = false) => ({ id, name, nodeType, hasChildren });
const page = (items: unknown[], nextCursor: string | null = null) => ({ items, pagination: { nextCursor } });

// GS-Alpha > A (folder) > a1.md, and GS-Alpha > root.md; Jira > PT (record group)
const CHILDREN: Record<string, ReturnType<typeof page>> = {
  alpha: page([child('folder-a', 'A', 'folder', true), child('root-md', 'root.md', 'record')]),
  'folder-a': page([child('a1-md', 'a1.md', 'record')]),
  jira: page([child('group-pt', 'PT', 'recordGroup', true)]),
};

const onSelectionChange = vi.fn<(next: ChatKnowledgeFilters) => void>();
const latest = () => onSelectionChange.mock.lastCall?.[0];

function Host({
  initial = EMPTY,
  filterMode,
}: {
  initial?: ChatKnowledgeFilters;
  filterMode?: 'connectors' | 'collections';
}) {
  const [selection, setSelection] = useState(initial);
  return (
    <Theme>
      <CollectionsTab
        filterMode={filterMode}
        selection={selection}
        onSelectionChange={(next) => {
          onSelectionChange(next);
          setSelection(next);
        }}
      />
    </Theme>
  );
}

const checkbox = (name: string) => screen.getByRole('checkbox', { name });
const expand = (name: string) => fireEvent.click(screen.getByRole('button', { name: `Show what is in ${name}` }));

beforeEach(() => {
  useChatStore.setState({ collectionMetaCache: {}, collectionNamesCache: {} });
  api.listCollectionsForChat.mockResolvedValue({
    knowledgeBases: [root('alpha', 'GS-Alpha', true), root('empty', 'GS-Empty', false), root('jira', 'Jira', true, 'CONNECTOR')],
    nextCursor: null,
  });
  api.getNodeChildren.mockImplementation(async (_type: string, id: string) => CHILDREN[id] ?? page([]));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Picker tree — expanding', () => {
  it('lists the apps first and loads nothing under them until one is expanded', async () => {
    render(<Host />);

    expect(await screen.findByText('GS-Alpha')).toBeTruthy();
    expect(screen.getByText('Jira')).toBeTruthy();
    expect(api.getNodeChildren).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Show what is in GS-Empty' })).toBeNull();
  });

  it('loads the children of an expanded node, folders and files alike, a page at a time', async () => {
    render(<Host />);
    await screen.findByText('GS-Alpha');

    expand('GS-Alpha');

    expect(await screen.findByText('root.md')).toBeTruthy();
    expect(api.getNodeChildren).toHaveBeenCalledWith('app', 'alpha', {
      onlyContainers: false,
      limit: 20,
      sortBy: 'name',
      sortOrder: 'asc',
    });
    expect(screen.queryByRole('button', { name: 'Show what is in root.md' })).toBeNull();

    expand('A');
    expect(await screen.findByText('a1.md')).toBeTruthy();
    expect(api.getNodeChildren).toHaveBeenLastCalledWith('folder', 'folder-a', expect.objectContaining({ limit: 20 }));
  });

  it('fetches the next page with the cursor the server issued', async () => {
    api.getNodeChildren.mockImplementation(async (_type: string, _id: string, params: { cursor?: string }) =>
      params.cursor === 'next-1'
        ? page([child('f2', 'second.md', 'record')])
        : page([child('f1', 'first.md', 'record')], 'next-1'),
    );
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('first.md');

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));

    expect(await screen.findByText('second.md')).toBeTruthy();
    expect(screen.getByText('first.md')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });

  it('keeps what was loaded when a node is collapsed and expanded again', async () => {
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('root.md');

    fireEvent.click(screen.getByRole('button', { name: 'Hide what is in GS-Alpha' }));
    expand('GS-Alpha');

    expect(api.getNodeChildren).toHaveBeenCalledTimes(1);
  });

  it('says so when a node has nothing under it, and offers a retry when loading fails', async () => {
    api.getNodeChildren.mockResolvedValueOnce(page([]));
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    expect(await screen.findByText('Nothing here')).toBeTruthy();

    api.getNodeChildren.mockRejectedValueOnce(new Error('boom'));
    expand('Jira');
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('PT')).toBeTruthy();
  });
});

describe('Picker tree — selecting', () => {
  it('sends an app or a collection under apps, a record group and a folder under their own keys', async () => {
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    expand('Jira');
    await screen.findByText('PT');
    await screen.findByText('A');

    fireEvent.click(checkbox('GS-Empty'));
    fireEvent.click(checkbox('PT'));
    fireEvent.click(checkbox('A'));

    expect(latest()).toEqual({ apps: ['empty'], kb: [], recordGroups: ['group-pt'], records: ['folder-a'] });
  });

  it('shows everything under a selected node as selected and locked, and what is above it as partly selected', async () => {
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('A');
    expand('A');
    await screen.findByText('a1.md');

    fireEvent.click(checkbox('A'));

    expect(checkbox('A').getAttribute('aria-checked')).toBe('true');
    expect(checkbox('a1.md').getAttribute('aria-checked')).toBe('true');
    expect((checkbox('a1.md') as HTMLButtonElement).disabled).toBe(true);
    expect(checkbox('GS-Alpha').getAttribute('aria-checked')).toBe('mixed');
    expect(checkbox('root.md').getAttribute('aria-checked')).toBe('false');

    fireEvent.click(checkbox('a1.md'));
    expect(latest()?.records).toEqual(['folder-a']);
  });

  it('replaces what was selected under a node when the node itself is selected', async () => {
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('A');
    fireEvent.click(checkbox('A'));
    fireEvent.click(checkbox('root.md'));
    expect(latest()?.records).toEqual(['folder-a', 'root-md']);

    fireEvent.click(checkbox('GS-Alpha'));

    expect(latest()).toEqual({ apps: ['alpha'], kb: [], recordGroups: [], records: [] });
    expect((checkbox('A') as HTMLButtonElement).disabled).toBe(true);
  });

  it('remembers the name of a selected node for its pill', async () => {
    render(<Host />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('A');

    fireEvent.click(checkbox('A'));

    const { collectionNamesCache, collectionMetaCache } = useChatStore.getState();
    expect(collectionNamesCache['folder-a']).toBe('A');
    expect(collectionMetaCache['folder-a']).toMatchObject({ nodeType: 'folder', ancestorIds: ['alpha'] });
  });

  it('shows a selection restored from an earlier message, and its parents once its row is on screen', async () => {
    render(<Host initial={{ apps: [], kb: [], records: ['folder-a'] }} />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('A');

    expect(checkbox('A').getAttribute('aria-checked')).toBe('true');
    await waitFor(() => expect(checkbox('GS-Alpha').getAttribute('aria-checked')).toBe('mixed'));
  });
});

describe('Picker tree — a selection this session did not pick from the tree', () => {
  // Restored from a message, handed over from All Records, or saved on an agent or a project.
  const crumbs = (...path: Array<[string, string, string]>) =>
    path.map(([id, name, nodeType]) => ({ id, name, nodeType }));

  beforeEach(() => {
    api.getNodeChildren.mockImplementation(async (_type: string, id: string, params: { include?: string }) =>
      params.include === 'breadcrumbs'
        ? { ...page([]), breadcrumbs: crumbs(['alpha', 'GS-Alpha', 'app'], ['folder-a', 'A', 'folder'], [id, 'Specs', 'folder']) }
        : (CHILDREN[id] ?? page([])),
    );
  });

  it('shows what is above it as partly selected without anything being opened', async () => {
    render(<Host initial={{ apps: [], kb: [], records: ['restored-1'] }} />);
    await screen.findByText('GS-Alpha');

    await waitFor(() => expect(checkbox('GS-Alpha').getAttribute('aria-checked')).toBe('mixed'));
    expect(api.getNodeChildren).toHaveBeenCalledWith('record', 'restored-1', {
      onlyContainers: false,
      limit: 1,
      include: 'breadcrumbs',
    });
    expect(useChatStore.getState().collectionMetaCache['restored-1']).toMatchObject({
      name: 'Specs',
      ancestorIds: ['alpha', 'folder-a'],
    });
  });

  it('is replaced when the collection above it is ticked', async () => {
    render(<Host initial={{ apps: [], kb: [], records: ['restored-2'] }} />);
    await screen.findByText('GS-Alpha');
    await waitFor(() => expect(checkbox('GS-Alpha').getAttribute('aria-checked')).toBe('mixed'));

    fireEvent.click(checkbox('GS-Alpha'));

    expect(latest()).toEqual({ apps: ['alpha'], kb: [], recordGroups: [], records: [] });
  });

  it('keeps a record selected alone ticked without locking what is under it', async () => {
    render(<Host initial={{ apps: [], kb: [], recordsExact: ['folder-a'] }} />);
    await screen.findByText('GS-Alpha');
    expand('GS-Alpha');
    await screen.findByText('A');
    expand('A');
    await screen.findByText('a1.md');

    expect(checkbox('A').getAttribute('aria-checked')).toBe('true');
    expect(checkbox('a1.md').getAttribute('aria-checked')).toBe('false');
    expect((checkbox('a1.md') as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('Picker tree — searching the list', () => {
  const search = (text: string) =>
    fireEvent.change(screen.getByRole('textbox'), { target: { value: text } });

  it('says so when no connector matches', async () => {
    render(<Host filterMode="connectors" />);
    await screen.findByText('Jira');

    search('no such connector');

    expect(screen.getByText('No connectors found')).toBeTruthy();
    expect(screen.queryByText('Jira')).toBeNull();
  });

  it('says so when no collection matches', async () => {
    render(<Host filterMode="collections" />);
    await screen.findByText('GS-Alpha');

    search('no such collection');

    expect(screen.getByText('No collections found')).toBeTruthy();
  });
});

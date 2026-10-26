import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';

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

import { MentionMenu, type MentionMenuHandle } from '../mention-menu';

const root = (id: string, name: string) => ({ id, name, nodeType: 'app', hasChildren: true, origin: 'CONNECTOR', connector: 'Jira' });
const child = (id: string, name: string, hasChildren = false) => ({ id, name, nodeType: hasChildren ? 'folder' : 'record', hasChildren });
const page = (items: unknown[], nextCursor: string | null = null) => ({ items, pagination: { nextCursor } });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function Menu({ query = '', onNoMatches, onPick = vi.fn(), handle }: { query?: string; onNoMatches?: () => void; onPick?: () => void; handle?: React.Ref<MentionMenuHandle> }) {
  return (
    <Theme>
      <MentionMenu
        ref={handle}
        query={query}
        onPick={onPick}
        onNoMatches={onNoMatches}
        onClearQuery={vi.fn()}
        maxHeight={400}
        listId="menu"
        onActiveChange={vi.fn()}
      />
    </Theme>
  );
}

const open = (name: string) => fireEvent.click(screen.getByRole('button', { name: `Show what is in ${name}` }));

// The full suite runs in parallel; under that load a search or level can take longer than the 1 s default.
const WAIT = { timeout: 5000 };
// A test waits several times, so it gets more than the 5 s default in all.
vi.setConfig({ testTimeout: 20000 });

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
});

beforeEach(() => {
  useChatStore.setState({ collectionMetaCache: {}, collectionNamesCache: {} });
  api.listCollectionsForChat.mockResolvedValue({
    knowledgeBases: [root('slow', 'Slow app'), root('fast', 'Fast app')],
    nextCursor: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('@ menu — levels', () => {
  it('shows the level it is on when an earlier level answers late', async () => {
    const slow = deferred<ReturnType<typeof page>>();
    api.getNodeChildren.mockImplementation((_type: string, id: string) =>
      id === 'slow' ? slow.promise : Promise.resolve(page([child('fast-1', 'Fast item')]))
    );
    render(<Menu />);
    await screen.findByText('Slow app', undefined, WAIT);
    open('Slow app');
    fireEvent.click(await screen.findByRole('button', { name: 'Back' }, WAIT));
    open('Fast app');
    expect(await screen.findByText('Fast item', undefined, WAIT)).toBeTruthy();
    await act(async () => slow.resolve(page([child('slow-1', 'Slow item')], 'cursor-of-slow')));
    expect(screen.getByText('Fast item')).toBeTruthy();
    expect(screen.queryByText('Slow item')).toBeNull();
  });

  it('never sends one level’s page cursor for another', async () => {
    const many = Array.from({ length: 20 }, (_, i) => child(`f-${i}`, `Item ${i}`));
    api.getNodeChildren.mockImplementation((_type: string, id: string) =>
      Promise.resolve(id === 'fast' ? page([child('sub', 'Sub folder', true), ...many], 'cursor-of-fast') : page([child('deep', 'Deep item')]))
    );
    render(<Menu />);
    await screen.findByText('Fast app', undefined, WAIT);
    open('Fast app');
    await screen.findByText('Sub folder', undefined, WAIT);
    open('Sub folder');
    await screen.findByText('Deep item', undefined, WAIT);
    for (const [, id, params] of api.getNodeChildren.mock.calls as Array<[string, string, { cursor?: string }]>) {
      if (params.cursor) expect(id).toBe('fast');
    }
  });
});

describe('@ menu — search', () => {
  it('shows what one search found when the other fails', async () => {
    api.searchNodes.mockImplementation(({ flattened }: { flattened: boolean }) =>
      flattened ? Promise.resolve(page([child('hit', 'Budget plan')])) : Promise.reject(new Error('400'))
    );
    render(<Menu query="budget" />);
    await waitFor(() => expect(screen.getByText('Budget plan')).toBeTruthy(), WAIT);
  });

  it('searches a query with spaces as typed and says when nothing matches', async () => {
    api.searchNodes.mockImplementation(({ q }: { q: string }) =>
      Promise.resolve(page(q === 'budget plan' ? [child('hit', 'Budget plan 2025')] : []))
    );
    const onNoMatches = vi.fn();
    const { rerender } = render(<Menu query="budget plan" onNoMatches={onNoMatches} />);
    await screen.findByText('Budget plan 2025', undefined, WAIT);
    expect(onNoMatches).not.toHaveBeenCalled();
    rerender(<Menu query="budget plan for" onNoMatches={onNoMatches} />);
    await waitFor(() => expect(onNoMatches).toHaveBeenCalled(), WAIT);
  });

  it('does not pick the previous query’s row while this one is still searching', async () => {
    const later = deferred<ReturnType<typeof page>>();
    api.searchNodes.mockImplementation(({ q }: { q: string }) =>
      q === 'q3' ? Promise.resolve(page([child('hit', 'Q3 plan')])) : later.promise
    );
    const onPick = vi.fn();
    const handle = React.createRef<MentionMenuHandle>();
    const { rerender } = render(<Menu query="q3" onPick={onPick} handle={handle} />);
    await screen.findByText('Q3 plan', undefined, WAIT);
    rerender(<Menu query="q3 report" onPick={onPick} handle={handle} />);
    const enter = { key: 'Enter', shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, nativeEvent: { isComposing: false }, preventDefault: vi.fn() };
    act(() => {
      handle.current?.keyDown(enter as unknown as React.KeyboardEvent<HTMLTextAreaElement>, true);
    });
    expect(onPick).not.toHaveBeenCalled();
    expect(enter.preventDefault).toHaveBeenCalled();
  });
  it('loads the next page of search results at the end of the list', async () => {
    const first = Array.from({ length: 20 }, (_, i) => child(`hit-${i}`, `Budget ${i}`));
    api.searchNodes.mockImplementation(({ flattened, cursor }: { flattened: boolean; cursor?: string }) => {
      if (!flattened) return Promise.resolve(page([]));
      return Promise.resolve(cursor === 'page-2' ? page([child('hit-20', 'Budget 20')]) : page(first, 'page-2'));
    });
    render(<Menu query="budget" />);
    expect(await screen.findByText('Budget 20', undefined, WAIT)).toBeTruthy();
    const next = (api.searchNodes.mock.calls as Array<[{ q: string; cursor?: string; flattened: boolean }]>).find(([p]) => p.cursor);
    expect(next?.[0]).toMatchObject({ q: 'budget', cursor: 'page-2', flattened: true });
  });

  it('drops a next page that answers after the query changed', async () => {
    const late = deferred<ReturnType<typeof page>>();
    api.searchNodes.mockImplementation(({ q, flattened, cursor }: { q: string; flattened: boolean; cursor?: string }) => {
      if (cursor) return late.promise;
      if (!flattened) return Promise.resolve(page([]));
      return Promise.resolve(q === 'budget' ? page([child('old', 'Budget old')], 'page-2') : page([child('new', 'Budget new')]));
    });
    const { rerender } = render(<Menu query="budget" />);
    await screen.findByText('Budget old', undefined, WAIT);
    await waitFor(() => expect(api.searchNodes.mock.calls.some(([p]) => (p as { cursor?: string }).cursor)).toBe(true), WAIT);
    rerender(<Menu query="budget new" />);
    await screen.findByText('Budget new', undefined, WAIT);
    await act(async () => late.resolve(page([child('stale', 'Budget stale')])));
    expect(screen.queryByText('Budget stale')).toBeNull();
  });
});

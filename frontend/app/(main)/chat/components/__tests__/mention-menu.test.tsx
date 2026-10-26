import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import { useAuthStore } from '@/lib/store/auth-store';
import { rememberRecentSources } from '@/chat/utils/recent-sources';
import type { MentionGroupView } from '@/chat/utils/mention-groups';

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

import { MentionMenu, openButtonSize, type MentionMenuHandle, type MentionPick } from '../mention-menu';

const root = (id: string, name: string) => ({ id, name, nodeType: 'app', hasChildren: true, origin: 'CONNECTOR', connector: 'Jira' });
const collection = (id: string, name: string) => ({ id, name, nodeType: 'kb', hasChildren: true, origin: 'COLLECTION', connector: 'KB' });
const child = (id: string, name: string, hasChildren = false) => ({ id, name, nodeType: hasChildren ? 'folder' : 'record', hasChildren });
const page = (items: unknown[], nextCursor: string | null = null) => ({ items, pagination: { nextCursor } });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

interface MenuProps {
  query?: string;
  onNoMatches?: () => void;
  onPick?: (pick: MentionPick) => void;
  handle?: React.Ref<MentionMenuHandle>;
  groups?: MentionGroupView[];
  onClearQuery?: () => void;
}

function Menu({ query = '', onNoMatches, onPick = vi.fn(), handle, groups, onClearQuery = vi.fn() }: MenuProps) {
  return (
    <Theme>
      <MentionMenu
        ref={handle}
        query={query}
        onPick={onPick}
        onNoMatches={onNoMatches}
        onClearQuery={onClearQuery}
        maxHeight={400}
        listId="menu"
        onActiveChange={vi.fn()}
        groups={groups}
        footer={null}
      />
    </Theme>
  );
}

const open = (name: string) => fireEvent.click(screen.getByRole('button', { name: `Show what is in ${name}` }));
const key = (k: string) => ({ key: k, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, nativeEvent: { isComposing: false }, preventDefault: vi.fn() });
const optionNames = () => screen.getAllByRole('option').map((o) => within(o).queryByTestId('mention-label')?.textContent ?? o.textContent);
const activeName = () => {
  const active = screen.getAllByRole('option').find((o) => o.getAttribute('aria-selected') === 'true');
  return active ? (within(active).queryByTestId('mention-label')?.textContent ?? active.textContent) : null;
};

// The full suite runs in parallel; under that load a search or level can take longer than the 1 s default.
const WAIT = { timeout: 5000 };
// A test waits several times, so it gets more than the 5 s default in all.
vi.setConfig({ testTimeout: 20000 });

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
});

beforeEach(() => {
  window.localStorage.clear();
  useChatStore.setState({ collectionMetaCache: {}, collectionNamesCache: {} });
  api.listCollectionsForChat.mockResolvedValue({
    knowledgeBases: [root('slow', 'Slow app'), root('fast', 'Fast app')],
    nextCursor: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useAuthStore.setState({ user: null });
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

  it('says where it is with a breadcrumb, and → opens and ← goes back by keyboard', async () => {
    api.getNodeChildren.mockImplementation((_type: string, id: string) =>
      Promise.resolve(id === 'fast' ? page([child('sub', 'Sub folder', true)]) : page([child('deep', 'Deep item')]))
    );
    const handle = React.createRef<MentionMenuHandle>();
    render(<Menu handle={handle} />);
    await screen.findByText('Slow app', undefined, WAIT);
    act(() => {
      handle.current?.keyDown(key('ArrowDown'), true);
    });
    expect(activeName()).toBe('Fast app');
    act(() => {
      handle.current?.keyDown(key('ArrowRight'), true);
    });
    await screen.findByText('Sub folder', undefined, WAIT);
    act(() => {
      handle.current?.keyDown(key('ArrowRight'), true);
    });
    await screen.findByText('Deep item', undefined, WAIT);
    expect(screen.getByTestId('mention-breadcrumb').textContent).toBe('Fast app / Sub folder');
    act(() => {
      handle.current?.keyDown(key('Backspace'), true);
    });
    expect(await screen.findByText('Sub folder', undefined, WAIT)).toBeTruthy();
    expect(screen.getByTestId('mention-breadcrumb').textContent).toBe('Fast app');
    act(() => {
      handle.current?.keyDown(key('ArrowLeft'), true);
    });
    expect(screen.queryByTestId('mention-breadcrumb')).toBeNull();
  });
});

describe('@ menu — the bare Sources group', () => {
  beforeEach(() => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [
        root('a1', 'Confluence'),
        root('a2', 'Drive'),
        root('a3', 'Jira'),
        root('a4', 'Slack'),
        collection('c1', 'Handbook'),
        collection('c2', 'Legal'),
        collection('c3', 'Plans'),
        collection('c4', 'Specs'),
      ],
      nextCursor: null,
    });
  });

  it('shows three apps and three collections under the apps panel’s names, then a way to every source', async () => {
    render(<Menu />);
    await screen.findByText('Confluence', undefined, WAIT);
    expect(screen.getByText('Your apps')).toBeTruthy();
    expect(screen.getByText('Collections')).toBeTruthy();
    expect(optionNames()).toEqual(['Confluence', 'Drive', 'Jira', 'Handbook', 'Legal', 'Plans', 'Browse all sources']);
    fireEvent.click(screen.getByRole('option', { name: /Browse all sources/ }));
    expect(await screen.findByText('Slack', undefined, WAIT)).toBeTruthy();
    expect(screen.getByText('Specs')).toBeTruthy();
    expect(screen.getByTestId('mention-breadcrumb').textContent).toBe('All sources');
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.queryByText('Slack')).toBeNull();
  });

  it('puts the sources picked last first, kept per person in this browser', async () => {
    useAuthStore.setState({ user: { id: 'u1' } as never });
    rememberRecentSources('u1', [{ id: 'a4', name: 'Slack', nodeType: 'app', connector: 'Slack' }]);
    render(<Menu />);
    await screen.findByText('Slack', undefined, WAIT);
    expect(optionNames().slice(0, 3)).toEqual(['Slack', 'Confluence', 'Drive']);
  });

  it('remembers what is picked, so it comes first next time', async () => {
    useAuthStore.setState({ user: { id: 'u1' } as never });
    const onPick = vi.fn();
    const first = render(<Menu onPick={onPick} />);
    await screen.findByText('Legal', undefined, WAIT);
    fireEvent.click(screen.getByRole('option', { name: /Legal/ }));
    expect(onPick).toHaveBeenCalledWith({ kind: 'source', node: expect.objectContaining({ id: 'c2', name: 'Legal', nodeType: 'app' }) });
    first.unmount();
    render(<Menu />);
    await screen.findByText('Legal', undefined, WAIT);
    expect(optionNames().slice(3, 6)).toEqual(['Legal', 'Handbook', 'Plans']);
  });

  it('filters what is loaded on the first character, without asking the server', async () => {
    render(<Menu query="j" />);
    await screen.findByText('Jira', undefined, WAIT);
    expect(optionNames()).toEqual(['Jira', 'Browse all sources']);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(api.searchNodes).not.toHaveBeenCalled();
  });
});

describe('@ menu — keyboard', () => {
  it('wraps with the arrows, and Tab picks as Enter does', async () => {
    const onPick = vi.fn();
    const handle = React.createRef<MentionMenuHandle>();
    render(<Menu handle={handle} onPick={onPick} />);
    await screen.findByText('Slow app', undefined, WAIT);
    act(() => {
      handle.current?.keyDown(key('ArrowUp'), true);
    });
    expect(activeName()).toBe('Browse all sources');
    act(() => {
      handle.current?.keyDown(key('ArrowDown'), true);
    });
    expect(activeName()).toBe('Slow app');
    const tab = key('Tab');
    act(() => {
      expect(handle.current?.keyDown(tab, true)).toBe(true);
    });
    expect(tab.preventDefault).toHaveBeenCalled();
    expect(onPick).toHaveBeenCalledWith({ kind: 'source', node: expect.objectContaining({ id: 'slow' }) });
  });

  it('leaves Enter to the composer once nothing matches, so it sends the message', async () => {
    api.searchNodes.mockResolvedValue(page([]));
    const handle = React.createRef<MentionMenuHandle>();
    render(<Menu handle={handle} query="zzz" />);
    await screen.findByText('No matches', undefined, WAIT);
    const enter = key('Enter');
    let used: boolean | undefined;
    act(() => {
      used = handle.current?.keyDown(enter, true);
    });
    expect(used).toBe(false);
    expect(enter.preventDefault).not.toHaveBeenCalled();
  });
});

describe('@ menu — the host’s groups', () => {
  const person = (id: string, label: string, recentAt?: number) => ({ key: `user:${id}`, label, value: { id }, ...(recentAt ? { recentAt } : {}) });
  const people = (items: ReturnType<typeof person>[], limit = 5): MentionGroupView => ({ key: 'people', label: 'People', items, limit, status: 'ready' });

  it('shows Sources only when the host adds none', async () => {
    render(<Menu />);
    await screen.findByText('Slow app', undefined, WAIT);
    expect(screen.getAllByRole('group')).toHaveLength(2);
    expect(screen.getByText('Your apps')).toBeTruthy();
    expect(screen.queryByText('People')).toBeNull();
  });

  it('shows a group’s first rows and its "N more", which opens the whole group in the same box with Back', async () => {
    const onPick = vi.fn();
    const all = ['Ana', 'Ben', 'Cy', 'Dee', 'Eve', 'Fay', 'Gus'].map((name, i) => person(`p${i}`, name));
    render(<Menu groups={[people(all)]} onPick={onPick} />);
    await screen.findByText('Slow app', undefined, WAIT);
    expect(optionNames().slice(0, 6)).toEqual(['Ana', 'Ben', 'Cy', 'Dee', 'Eve', '2 more']);
    fireEvent.click(screen.getByRole('option', { name: /2 more/ }));
    expect(optionNames()).toEqual(['Ana', 'Ben', 'Cy', 'Dee', 'Eve', 'Fay', 'Gus']);
    expect(screen.getByTestId('mention-breadcrumb').textContent).toBe('People');
    fireEvent.click(screen.getByRole('option', { name: /Gus/ }));
    expect(onPick).toHaveBeenCalledWith({ kind: 'item', item: expect.objectContaining({ key: 'user:p6' }) });
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText('2 more')).toBeTruthy();
  });

  it('puts the host’s groups before Sources, and hides an empty one', async () => {
    render(<Menu groups={[people([person('p1', 'Ana')]), { key: 'agents', label: 'Agents', items: [], limit: 3, status: 'ready' }]} />);
    await screen.findByText('Slow app', undefined, WAIT);
    expect(optionNames()[0]).toBe('Ana');
    expect(screen.queryByText('Agents')).toBeNull();
  });

  it('says a group is loading, and that one failed with a Retry, while the others show', async () => {
    const onRetry = vi.fn();
    render(
      <Menu
        groups={[
          { key: 'people', label: 'People', items: [], limit: 5, status: 'error', onRetry },
          { key: 'agents', label: 'Agents', items: [], limit: 3, status: 'loading' },
        ]}
      />,
    );
    await screen.findByText('Slow app', undefined, WAIT);
    expect(screen.getByTestId('mention-group-loading-agents')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalled();
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

  it('says the search failed, with a Retry that asks again', async () => {
    api.searchNodes.mockRejectedValue(new Error('500'));
    render(<Menu query="budget" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }, WAIT));
    api.searchNodes.mockResolvedValue(page([child('hit', 'Budget plan')]));
    expect(await screen.findByText('Budget plan', undefined, WAIT)).toBeTruthy();
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
    const enter = key('Enter');
    act(() => {
      handle.current?.keyDown(enter, true);
    });
    expect(onPick).not.toHaveBeenCalled();
    expect(enter.preventDefault).toHaveBeenCalled();
  });

  it('shows five matches under Sources, the rest behind Show more, which pages on at the end of the list', async () => {
    const first = Array.from({ length: 20 }, (_, i) => child(`hit-${i}`, `Budget ${i}`));
    api.searchNodes.mockImplementation(({ flattened, cursor }: { flattened: boolean; cursor?: string }) => {
      if (!flattened) return Promise.resolve(page([]));
      return Promise.resolve(cursor === 'page-2' ? page([child('hit-20', 'Budget 20')]) : page(first, 'page-2'));
    });
    render(<Menu query="budget" />);
    await screen.findByText('Budget 0', undefined, WAIT);
    expect(screen.getByText('Sources')).toBeTruthy();
    expect(optionNames()).toEqual(['Budget 0', 'Budget 1', 'Budget 2', 'Budget 3', 'Budget 4', 'Show more']);
    fireEvent.click(screen.getByRole('option', { name: /Show more/ }));
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
    fireEvent.click(await screen.findByRole('option', { name: /Show more/ }, WAIT));
    await waitFor(() => expect(api.searchNodes.mock.calls.some(([p]) => (p as { cursor?: string }).cursor)).toBe(true), WAIT);
    rerender(<Menu query="budget new" />);
    await screen.findByText('Budget new', undefined, WAIT);
    await act(async () => late.resolve(page([child('stale', 'Budget stale')])));
    expect(screen.queryByText('Budget stale')).toBeNull();
  });
});

describe('@ menu — the drill-in arrow', () => {
  it('is a 40 px target in a 44 px phone row and a 24 px icon on a desktop row', () => {
    expect(openButtonSize(44)).toBe(40);
    expect(openButtonSize(36)).toBe(24);
  });
});

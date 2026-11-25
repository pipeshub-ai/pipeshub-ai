import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import en from '@/lib/i18n/locales/en-US.json';
import { SearchBox, type SearchBoxApi } from '../components/search-box';

vi.mock('../api', () => ({ GraphApi: {} }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

// Radix Select measures its trigger; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const repos = [{ repo: 'acme/shop', source: 'GitHub', connector_id: 'c1', graph_status: 'ready' as const }];
const hit = { file_path: 'src/auth.py', qualified_name: 'function:parse_token', kind: 'function' };

function setup(over: Partial<SearchBoxApi> = {}, props: Partial<React.ComponentProps<typeof SearchBox>> = {}) {
  const api: SearchBoxApi = { repos: vi.fn(async () => repos), search: vi.fn(async () => [hit]), ...over };
  const onPick = vi.fn();
  render(
    <Theme>
      <SearchBox api={api} onPick={onPick} includeTests={false} debounceMs={0} {...props} />
    </Theme>,
  );
  return { api, onPick };
}

const type = async (value: string) => {
  const box = screen.getByRole('combobox', { name: en.graph.ui.search.label });
  await act(async () => {
    fireEvent.change(box, { target: { value } });
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 5));
  });
};

describe('SearchBox', () => {
  it('chooses the only repository and searches it, then opens the pick as an address', async () => {
    const { api, onPick } = setup();
    await act(async () => undefined);
    await type('parse');
    expect(api.search).toHaveBeenCalledWith({ repo: 'acme/shop', pattern: 'parse', include_tests: false });
    fireEvent.click(await screen.findByRole('option', { name: /parse_token/ }));
    expect(onPick).toHaveBeenCalledWith({ repo: 'acme/shop', filePath: 'src/auth.py', qualifiedName: 'function:parse_token' });
  });

  it('works from the keyboard: arrows move, Enter picks', async () => {
    const two = [hit, { file_path: 'src/b.py', qualified_name: 'function:parse_x' }];
    const { onPick } = setup({ search: vi.fn(async () => two) });
    await act(async () => undefined);
    await type('parse');
    const box = screen.getByRole('combobox', { name: en.graph.ui.search.label });
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ qualifiedName: 'function:parse_x' }));
  });

  it('does not search for one character, and passes the tests toggle on', async () => {
    const { api } = setup({}, { includeTests: true });
    await act(async () => undefined);
    await type('p');
    expect(api.search).not.toHaveBeenCalled();
    await type('pa');
    expect(api.search).toHaveBeenCalledWith(expect.objectContaining({ include_tests: true }));
  });

  it('says "no matches" for an empty answer and an error for a failed one, never the same words', async () => {
    setup({ search: vi.fn(async () => []) });
    await act(async () => undefined);
    await type('zzz');
    expect(screen.getByText(en.graph.ui.search.noMatches)).toBeTruthy();
    cleanup();
    setup({ search: vi.fn(async () => Promise.reject(new Error('503'))) });
    await act(async () => undefined);
    await type('zzz');
    expect(screen.getByText(en.graph.error.unavailable)).toBeTruthy();
    expect(screen.queryByText(en.graph.ui.search.noMatches)).toBeNull();
  });

  it('drops the answer to an older query', async () => {
    let release!: (m: typeof hit[]) => void;
    const slow = new Promise<typeof hit[]>((resolve) => (release = resolve));
    let n = 0;
    setup({ search: vi.fn(async () => (++n === 1 ? slow : [{ ...hit, qualified_name: 'function:new_one' }])) });
    await act(async () => undefined);
    await type('first');
    await type('second');
    await act(async () => release([{ ...hit, qualified_name: 'function:stale_one' }]));
    expect(screen.queryByText('stale_one')).toBeNull();
    expect(screen.getByText('new_one')).toBeTruthy();
  });

  it('says there are no repositories to search, and that the list failed, in different words', async () => {
    setup({ repos: vi.fn(async () => []) });
    expect(await screen.findByText(en.graph.ui.search.noRepos)).toBeTruthy();
    cleanup();
    setup({ repos: vi.fn(async () => Promise.reject(new Error('x'))) });
    expect(await screen.findByText(en.graph.error.unavailable)).toBeTruthy();
  });

  it('starts in the repository a link named', async () => {
    const { api } = setup({ repos: vi.fn(async () => [...repos, { ...repos[0], repo: 'acme/admin' }]) }, { initialRepo: 'acme/admin' });
    await act(async () => undefined);
    await type('login');
    expect(api.search).toHaveBeenCalledWith(expect.objectContaining({ repo: 'acme/admin' }));
  });
});

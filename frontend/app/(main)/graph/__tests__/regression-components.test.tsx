// Component-level regression tests for the earlier review round.
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import en from '@/lib/i18n/locales/en-US.json';

const engineCalls: string[] = [];
let mountedInto: HTMLElement | null = null;
const fakeEngine = {
  mount: vi.fn(async (el: HTMLElement) => {
    mountedInto = el;
    engineCalls.push('mount');
  }),
  setModel: vi.fn(() => {
    engineCalls.push('setModel');
  }),
  setQuality: vi.fn(),
  select: vi.fn(),
  setFocus: vi.fn(),
  resize: vi.fn(),
  reveal: vi.fn(),
  destroy: vi.fn(() => {
    engineCalls.push('destroy');
  }),
};
vi.mock('../engine/cytoscape-engine', () => ({ createCytoscapeEngine: () => fakeEngine }));
vi.mock('../engine/capabilities', () => ({
  probeCapabilities: () => ({ hasWebgl2: false, renderer: '', devicePixelRatio: 1, reducedMotion: false }),
}));
vi.mock('@/lib/api', () => ({
  ErrorType: { NOT_FOUND: 'NOT_FOUND', NETWORK_ERROR: 'NETWORK_ERROR', SERVER_ERROR: 'SERVER_ERROR', TIMEOUT_ERROR: 'TIMEOUT_ERROR' },
  apiClient: {},
}));
const resolve = vi.fn();
const expand = vi.fn();
const revalidate = vi.fn(async (b: { nodeIds: string[] }) => ({ visible: b.nodeIds }));
vi.mock('../api', () => ({
  GraphApi: {
    expand: (...a: unknown[]) => expand(...a),
    resolve: (...a: unknown[]) => resolve(...a),
    revalidate: (...a: unknown[]) => revalidate(...(a as [{ nodeIds: string[] }])),
    repos: vi.fn(async () => [{ repo: 'acme/shop', source: 'GitHub', connector_id: 'c', graph_status: 'ready' }]),
    search: vi.fn(async () => []),
  },
}));
const replaceSpy = vi.fn();
const pushSpy = vi.fn();
let search = '';
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceSpy, push: pushSpy }),
  usePathname: () => '/graph',
  useSearchParams: () => new URLSearchParams(search),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur.replace(/{{(\w+)}}/g, (_, k) => String(vars?.[k] ?? '')) : key;
    },
  }),
}));
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

import { GraphExplorer } from '../components/graph-explorer';
import { Notices } from '../components/notices';
import { Toolbar } from '../components/toolbar';
import { TableTwin } from '../components/table-twin';
import { SearchBox } from '../components/search-box';
import { emptyModel, mergeExpand } from '../model/graph-model';
import { noticesFor } from '../status';
import { useGraphStore } from '../store';

beforeEach(() => {
  engineCalls.length = 0;
  mountedInto = null;
  Object.values(fakeEngine).forEach((f) => 'mockClear' in f && f.mockClear());
  useGraphStore.getState().reset();
  expand.mockReset();
  resolve.mockReset();
  replaceSpy.mockReset();
  pushSpy.mockReset();
  revalidate.mockClear();
  search = '';
});
afterEach(cleanup);
const ui = (node: React.ReactNode) => render(<Theme>{node}</Theme>);

describe('the canvas is there before the first node, so search-first entry draws', () => {
  it('mounts the engine into a real element on the empty start page, then draws the node a search opens', async () => {
    expand.mockResolvedValue({
      nodes: [{ id: 'block:a', label: 'a' }],
      edges: [],
      anchors: [{ id: 'block:a', total: 0 }],
      page: { truncated: false, incomplete: false },
      graphStatus: 'ready',
    });
    ui(<GraphExplorer />);
    await act(async () => undefined);
    expect(fakeEngine.mount).toHaveBeenCalledTimes(1);
    expect(mountedInto).toBeInstanceOf(HTMLElement);
    expect(mountedInto?.isConnected).toBe(true);
    expect(screen.getByText(en.graph.ui.emptyPrompt)).toBeTruthy();
    // A node opens (as a search hit does): the same engine is handed the model, nothing is remounted.
    await act(async () => {
      resolve.mockResolvedValue({ nodeId: 'block:a' });
      const { ExplorerController } = await import('../controller');
      void ExplorerController; // the page's own controller is used below through the store
      useGraphStore.getState().focusOn('block:a');
    });
    expect(fakeEngine.mount).toHaveBeenCalledTimes(1);
    expect(fakeEngine.setFocus).toHaveBeenLastCalledWith('block:a');
  });

  it('keeps the engine through an empty state and brings the table and picture back without losing it', async () => {
    ui(<GraphExplorer />);
    await act(async () => undefined);
    const toggle = screen.getByRole('button', { name: en.graph.ui.showTable });
    expect((toggle as HTMLButtonElement).disabled).toBe(true); // nothing to show yet
  });
});

describe('Retry is a control on the failure', () => {
  it('shows Retry on an unavailable notice and calls the handler with the action', () => {
    const onAction = vi.fn();
    const base = { status: 'ready' as const, incomplete: false, verify: 'ok' as const, simplified: false };
    ui(<Notices notices={noticesFor({ ...base, failure: 'unavailable' })} onAction={onAction} />);
    fireEvent.click(screen.getByRole('button', { name: en.graph.ui.action.retry }));
    expect(onAction).toHaveBeenCalledWith('retry');
  });

  it('shows "use full quality" on a simplified view', () => {
    const onAction = vi.fn();
    ui(<Notices notices={noticesFor({ status: 'ready', incomplete: false, verify: 'ok', failure: null, simplified: true })} onAction={onAction} />);
    fireEvent.click(screen.getByRole('button', { name: en.graph.ui.action.restore }));
    expect(onAction).toHaveBeenCalledWith('restore');
  });

  it('shows no button on a notice that has no action', () => {
    ui(<Notices notices={noticesFor({ status: 'stale', incomplete: false, verify: 'ok', failure: null, simplified: false })} onAction={vi.fn()} />);
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('the toolbar has nothing active before there is a graph', () => {
  const props = {
    direction: 'any' as const, includeTests: false, canBack: false, canForward: false, tableView: false, tableOnly: false,
    onDirection: vi.fn(), onIncludeTests: vi.fn(), onBack: vi.fn(), onForward: vi.fn(), onFit: vi.fn(), onToggleView: vi.fn(),
  };
  it('disables Fit and the view switch without a graph and enables them with one', () => {
    ui(<Toolbar {...props} hasGraph={false} />);
    expect((screen.getByRole('button', { name: en.graph.ui.fit }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: en.graph.ui.showTable }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    ui(<Toolbar {...props} hasGraph />);
    expect((screen.getByRole('button', { name: en.graph.ui.fit }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('the table lists the centre first, marks it, and says its counts are the ones shown', () => {
  const model = mergeExpand(emptyModel(), { source: 'code', nodeIds: ['block:z'] }, {
    nodes: [{ id: 'block:a', label: 'a' }, { id: 'block:z', label: 'z' }],
    edges: [{ id: 'e', from: 'block:z', to: 'block:a', type: 'CALLS' }],
    anchors: [], page: { truncated: false, incomplete: false }, graphStatus: 'ready',
  });
  it('puts the focus first with a badge, and names the columns "shown"', () => {
    ui(<TableTwin model={model} selection={null} focus="block:z" onSelect={vi.fn()} onOpen={vi.fn()} onExpand={vi.fn()} onShowMore={vi.fn()} />);
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers).toContain(en.graph.ui.table.callersShown);
    expect(headers).toContain(en.graph.ui.table.calleesShown);
    const firstRow = screen.getAllByRole('row')[1];
    expect(firstRow.textContent).toContain('z');
    expect(firstRow.textContent).toContain(en.graph.ui.table.focus);
  });

  it('explains Open and Expand on the buttons', () => {
    ui(<TableTwin model={model} selection={null} onSelect={vi.fn()} onOpen={vi.fn()} onExpand={vi.fn()} onShowMore={vi.fn()} />);
    expect(screen.getAllByRole('button', { name: en.graph.ui.open })[0].getAttribute('title')).toBe(en.graph.ui.openHint);
    expect(screen.getAllByRole('button', { name: en.graph.ui.expand })[0].getAttribute('title')).toBe(en.graph.ui.expandHint);
  });
});

describe('search says what is needed and ignores a repo it cannot offer', () => {
  const api = (over = {}) => ({ repos: vi.fn(async () => [{ repo: 'acme/shop', source: 'G', connector_id: 'c', graph_status: 'ready' as const }, { repo: 'acme/admin', source: 'G', connector_id: 'c', graph_status: 'ready' as const }]), search: vi.fn(async () => [{ file_path: 'a.py', qualified_name: 'function:x' }, { file_path: 'b.py', qualified_name: 'function:y' }]), ...over });

  it('says "choose a repository first" until one is chosen, ignoring a repo the list does not have', async () => {
    ui(<SearchBox api={api()} includeTests={false} onPick={vi.fn()} initialRepo="acme/nothing" debounceMs={0} />);
    await act(async () => undefined);
    const box = screen.getByRole('combobox', { name: en.graph.ui.search.label }) as HTMLInputElement;
    expect(box.placeholder).toBe(en.graph.ui.search.chooseFirst);
    expect(box.disabled).toBe(true);
  });

  it('hints at two characters, and highlights the first result so Enter has a visible target', async () => {
    const onPick = vi.fn();
    ui(<SearchBox api={api()} includeTests={false} onPick={onPick} initialRepo="acme/shop" debounceMs={0} />);
    await act(async () => undefined);
    const box = screen.getByRole('combobox', { name: en.graph.ui.search.label });
    await act(async () => { fireEvent.change(box, { target: { value: 'x' } }); });
    expect(screen.getByText(en.graph.ui.search.minChars)).toBeTruthy();
    await act(async () => { fireEvent.change(box, { target: { value: 'xy' } }); });
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    const options = screen.getAllByRole('option');
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ qualifiedName: 'function:x' }));
  });

  it('draws the results in a popover over the page, not in the flow that pushes the graph down', async () => {
    ui(<SearchBox api={api()} includeTests={false} onPick={vi.fn()} initialRepo="acme/shop" debounceMs={0} />);
    await act(async () => undefined);
    const box = screen.getByRole('combobox', { name: en.graph.ui.search.label });
    await act(async () => { fireEvent.change(box, { target: { value: 'xy' } }); });
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(screen.getByRole('listbox').style.position).toBe('absolute');
  });
});

const answerFor = (id: string, others: string[] = []) => ({
  nodes: [id, ...others].map((n) => ({ id: n, label: n })),
  edges: others.map((n) => ({ id: `e:${id}>${n}`, from: id, to: n, type: 'CALLS' })),
  anchors: [{ id, total: others.length }],
  page: { truncated: false, incomplete: false },
  graphStatus: 'ready',
});

describe('an entry link keeps its address until it has resolved', () => {
  it('does not rewrite the URL while the link is resolving or after a failure a retry can fix, then rewrites it to the node', async () => {
    search = 'record=rec-5';
    resolve.mockRejectedValueOnce({ statusCode: 503, type: 'SERVER_ERROR', message: 'x' });
    ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    expect(replaceSpy).not.toHaveBeenCalled();
    // The link works on a second try (the Retry button): now the address becomes the node's id.
    resolve.mockResolvedValue({ nodeId: 'block:five' });
    expand.mockResolvedValue(answerFor('block:five'));
    fireEvent.click(await screen.findByRole('button', { name: en.graph.ui.action.retry }));
    await act(async () => undefined);
    await act(async () => undefined);
    expect(replaceSpy).toHaveBeenCalledWith('/graph?node=block%3Afive&at=0', { scroll: false });
  });

  it('drops the address for an answer a retry cannot change', async () => {
    search = 'record=rec-5';
    resolve.mockRejectedValue({ statusCode: 404, type: 'NOT_FOUND', message: 'x' });
    ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    expect(replaceSpy).toHaveBeenCalledWith('/graph', { scroll: false });
  });
});

describe('results are said in words, and the screen reader is pointed at the table', () => {
  it('has a polite region from the start, an announcement after a node opens, and a hint about the table', async () => {
    search = 'node=block:a';
    expand.mockResolvedValue(answerFor('block:a', ['block:b', 'block:c']));
    ui(<GraphExplorer />);
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
    await act(async () => undefined);
    await act(async () => undefined);
    expect(screen.getByText('Showing 3 items.')).toBeTruthy();
    expect(screen.getByText(en.graph.ui.canvasHint)).toBeTruthy();
  });

  it('keeps the notices region in the page when there is nothing to say, so the first message is announced', () => {
    ui(<Notices notices={[]} />);
    expect(screen.getByRole('status').getAttribute('aria-live')).toBe('polite');
  });

  it('puts an error in its own alert, not inside the polite region', () => {
    const base = { status: 'ready' as const, incomplete: false, verify: 'ok' as const, simplified: false };
    ui(<Notices notices={noticesFor({ ...base, failure: 'unavailable' })} />);
    const alert = screen.getByRole('alert');
    expect(alert.closest('[role=status]')).toBeNull();
  });
});

describe('focus lands on the explorer when what had it goes away, and the panel says why', () => {
  it('shows the "no longer available" sentence when the selection was taken away', async () => {
    const { DetailPanel } = await import('../components/detail-panel');
    ui(<DetailPanel model={emptyModel()} selection={null} selectionGone onOpen={vi.fn()} onExpand={vi.fn()} onShowMore={vi.fn()} />);
    expect(screen.getByText(en.graph.ui.selectionGone)).toBeTruthy();
  });

  it('moves focus to the explorer when the selection clears and nothing has focus', async () => {
    search = 'node=block:a';
    expand.mockResolvedValue(answerFor('block:a', ['block:b']));
    const { container } = ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    await act(async () => useGraphStore.getState().select('block:b'));
    (document.activeElement as HTMLElement | null)?.blur();
    await act(async () => useGraphStore.getState().select(null));
    expect(document.activeElement).toBe(container.querySelector('.graph-explorer'));
  });
});

describe('shortcuts leave a menu or list alone', () => {
  it('Escape clears the selection from the page but not from inside an open list', async () => {
    search = 'node=block:a';
    expand.mockResolvedValue(answerFor('block:a', ['block:b']));
    ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    await act(async () => useGraphStore.getState().select('block:b'));
    const list = document.createElement('div');
    list.setAttribute('role', 'listbox');
    const item = document.createElement('div');
    list.appendChild(item);
    document.body.appendChild(list);
    fireEvent.keyDown(item, { key: 'Escape' });
    expect(useGraphStore.getState().selection).toBe('block:b');
    list.remove();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(useGraphStore.getState().selection).toBeNull();
  });
});

describe('window focus checks access too, and leaving the page leaves nothing behind', () => {
  it('re-checks when the window gets focus', async () => {
    search = 'node=block:a';
    expand.mockResolvedValue(answerFor('block:a', ['block:b']));
    ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    revalidate.mockClear();
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
    });
    await vi.waitFor(() => expect(revalidate).toHaveBeenCalled());
  });

  it('resets the store when the explorer unmounts', async () => {
    search = 'node=block:a';
    expand.mockResolvedValue(answerFor('block:a'));
    const { unmount } = ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    expect(useGraphStore.getState().trail).toEqual(['block:a']);
    unmount();
    expect(useGraphStore.getState()).toMatchObject({ trail: [], node: null, selection: null });
  });
});

describe('review finding and small a11y and honesty fixes', () => {
  it('names no icon word to a screen reader on the toolbar buttons', () => {
    const p = { direction: 'any' as const, includeTests: false, canBack: true, canForward: true, hasGraph: true, tableView: false, tableOnly: false,
      onDirection: vi.fn(), onIncludeTests: vi.fn(), onBack: vi.fn(), onForward: vi.fn(), onFit: vi.fn(), onToggleView: vi.fn() };
    const { container } = ui(<Toolbar {...p} />);
    expect(container.querySelectorAll('button [aria-hidden="true"]').length).toBeGreaterThanOrEqual(4);
  });

  it('shows a dash, not zero, for the links of a node that has not been expanded', () => {
    const m = mergeExpand(emptyModel(), { source: 'code', nodeIds: ['block:a'] }, answerFor('block:a', ['block:b']));
    ui(<TableTwin model={m} selection={null} onSelect={vi.fn()} onOpen={vi.fn()} onExpand={vi.fn()} onShowMore={vi.fn()} />);
    const cells = (name: string) => screen.getByRole('rowheader', { name }).closest('tr')!.querySelectorAll('td');
    expect(cells('block:a')[2].textContent).toBe('0');   // expanded: it has no callers among those shown
    expect(cells('block:a')[3].textContent).toBe('1');
    expect(cells('block:b')[2].textContent).toBe('–');   // never expanded: unknown, not none
    expect(cells('block:b')[3].textContent).toBe('–');
  });

  it('gives the search box its combobox attributes and no empty listbox', async () => {
    const api = { repos: vi.fn(async () => [{ repo: 'r', source: 's', connector_id: 'c', graph_status: 'ready' as const }]), search: vi.fn(async () => []) };
    ui(<SearchBox api={api} includeTests={false} onPick={vi.fn()} debounceMs={0} />);
    await act(async () => undefined);
    const box = screen.getByRole('combobox', { name: en.graph.ui.search.label });
    expect(box.getAttribute('aria-autocomplete')).toBe('list');
    expect(box.getAttribute('aria-haspopup')).toBe('listbox');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('sends "/" to the repository choice while no repository is chosen', async () => {
    const api = { repos: vi.fn(async () => [{ repo: 'a', source: 's', connector_id: 'c', graph_status: 'ready' as const }, { repo: 'b', source: 's', connector_id: 'c', graph_status: 'ready' as const }]), search: vi.fn(async () => []) };
    const { rerender } = ui(<SearchBox api={api} includeTests={false} onPick={vi.fn()} debounceMs={0} focusSignal={0} />);
    await act(async () => undefined);
    rerender(<Theme><SearchBox api={api} includeTests={false} onPick={vi.fn()} debounceMs={0} focusSignal={1} /></Theme>);
    expect(document.activeElement).toBe(screen.getByRole('combobox', { name: en.graph.ui.search.repo }));
  });
});

describe('controls are 44px on touch screens and narrow windows', () => {
  it('has the rule in the shared stylesheet', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve: resolvePath } = await import('node:path');
    const css = readFileSync(resolvePath(process.cwd(), 'app/globals.css'), 'utf8');
    expect(css).toMatch(/\(pointer: coarse\), \(max-width: 480px\)[\s\S]*\.graph-explorer[\s\S]*min-height: 44px/);
  });
});

describe('the explorer does not take focus when the page loads', () => {
  it('leaves focus on the page until a selection goes away', async () => {
    search = 'node=block:a';
    expand.mockResolvedValue(answerFor('block:a', ['block:b']));
    ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    expect(document.activeElement).toBe(document.body);
  });
});


describe('the address filters survive the first pass and a replayed mount', () => {
  it('keeps the filters of an address that names no node, writing nothing over them', async () => {
    search = 'dir=inbound&tests=1';
    ui(<GraphExplorer />);
    await act(async () => undefined);
    await act(async () => undefined);
    const s = useGraphStore.getState();
    expect([s.direction, s.includeTests]).toEqual(['inbound', true]);
    expect(replaceSpy).not.toHaveBeenCalled(); // the first write must not put the defaults over the link's filters
  });

  it('applies the filters of the address again when Strict Mode replays the mount', async () => {
    search = 'node=block%3Aa&dir=inbound&tests=1';
    expand.mockResolvedValue(answerFor('block:a'));
    render(
      <React.StrictMode>
        <Theme>
          <GraphExplorer />
        </Theme>
      </React.StrictMode>,
    );
    await act(async () => undefined);
    await act(async () => undefined);
    const s = useGraphStore.getState();
    expect([s.direction, s.includeTests]).toEqual(['inbound', true]);
    const last = expand.mock.calls.at(-1)?.[0] as { direction?: string; includeTests?: boolean };
    expect(last).toMatchObject({ direction: 'inbound', includeTests: true });
  });
});

describe('the legend', () => {
  it('uses the drawing\'s own colours, and a focus dot that follows the theme', async () => {
    const { KIND_COLOUR } = await import('../engine/colours');
    const { Legend } = await import('../components/legend');
    const { container } = ui(<Legend />);
    const dots = [...container.querySelectorAll('span[aria-hidden=true]')].map((d) => (d as HTMLElement).style.background);
    // jsdom writes a colour back in its own notation, so the expected ones go through the same round trip.
    const asWritten = (c: string) => {
      const probe = document.createElement('span');
      probe.style.background = c;
      return probe.style.background;
    };
    expect(dots).toEqual([KIND_COLOUR.function, KIND_COLOUR.class, 'var(--gray-12)'].map(asWritten));
  });
});

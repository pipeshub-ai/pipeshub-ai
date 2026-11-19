import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import en from '@/lib/i18n/locales/en-US.json';
import { DetailPanel } from '../components/detail-panel';
import { Notices } from '../components/notices';
import { TableTwin } from '../components/table-twin';
import { Toolbar } from '../components/toolbar';
import { emptyModel, mergeExpand } from '../model/graph-model';
import { noticesFor } from '../status';

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

afterEach(cleanup);
const ui = (node: React.ReactNode) => render(<Theme>{node}</Theme>);

const model = mergeExpand(
  emptyModel(),
  { source: 'code', nodeIds: ['block:main'] },
  {
    nodes: [
      { id: 'block:main', label: 'main', kind: 'function', file: 'src/a.py', line: 3 },
      { id: 'block:lib', label: 'lib', kind: 'class', file: 'src/b.py' },
    ],
    edges: [{ id: 'e:1', from: 'block:main', to: 'block:lib', type: 'CALLS' }],
    anchors: [{ id: 'block:main', total: 1 }],
    page: { truncated: false, incomplete: false },
    graphStatus: 'ready',
  },
);

describe('TableTwin', () => {
  const handlers = () => ({ onSelect: vi.fn(), onOpen: vi.fn(), onExpand: vi.fn(), onShowMore: vi.fn() });

  it('lists every node with its drawn links and the same actions as the canvas', () => {
    const h = handlers();
    ui(<TableTwin model={model} selection={null} {...h} />);
    const rows = screen.getAllByRole('row');
    expect(rows).toHaveLength(3);
    const main = within(screen.getByRole('rowheader', { name: 'main' }).closest('tr')!);
    expect(main.getAllByRole('cell').map((c) => c.textContent)).toContain('function');
    fireEvent.click(main.getByRole('button', { name: 'Open' }));
    fireEvent.click(main.getByRole('button', { name: 'Expand' }));
    expect(h.onOpen).toHaveBeenCalledWith('block:main');
    expect(h.onExpand).toHaveBeenCalledWith('block:main');
  });

  it('offers Show more only on a node that has more', () => {
    const h = handlers();
    const more = { ...model, more: { 'block:main': { group: ['block:main'], cursor: 'c' } } };
    ui(<TableTwin model={more} selection={null} {...h} />);
    const buttons = screen.getAllByRole('button', { name: 'Show more' });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    expect(h.onShowMore).toHaveBeenCalledWith('block:main');
  });
});

describe('TableTwin paging', () => {
  it('draws 200 rows and adds 200 more on request, so the table lists everything without drawing it all at once', async () => {
    const { TABLE_PAGE } = await import('../components/table-twin');
    const nodes = Object.fromEntries(Array.from({ length: 450 }, (_, i) => [`block:n${String(i).padStart(3, '0')}`, { id: `block:n${String(i).padStart(3, '0')}`, label: `n${String(i).padStart(3, '0')}` }]));
    const big = { ...model, nodes, edges: {}, more: {} };
    ui(<TableTwin model={big} selection={null} onSelect={vi.fn()} onOpen={vi.fn()} onExpand={vi.fn()} onShowMore={vi.fn()} />);
    expect(screen.getAllByRole('row')).toHaveLength(TABLE_PAGE + 1);
    fireEvent.click(screen.getByRole('button', { name: en.graph.ui.table.moreRows }));
    expect(screen.getAllByRole('row')).toHaveLength(2 * TABLE_PAGE + 1);
    fireEvent.click(screen.getByRole('button', { name: en.graph.ui.table.moreRows }));
    expect(screen.getAllByRole('row')).toHaveLength(451);
    expect(screen.queryByRole('button', { name: en.graph.ui.table.moreRows })).toBeNull();
  }, 30_000);
});

describe('DetailPanel', () => {
  const actions = { onOpen: vi.fn(), onExpand: vi.fn(), onShowMore: vi.fn() };

  it('says nothing is selected when the selected node is gone', () => {
    ui(<DetailPanel model={model} selection="block:revoked" {...actions} />);
    expect(screen.getByText(en.graph.ui.nothingSelected)).toBeTruthy();
    expect(screen.queryByText('revoked')).toBeNull();
  });

  it('shows an exact total, and none while the read is truncated', () => {
    ui(<DetailPanel model={model} selection="block:main" {...actions} />);
    expect(screen.getByText('Links: 1')).toBeTruthy();
    cleanup();
    ui(<DetailPanel model={{ ...model, totals: {} }} selection="block:main" {...actions} />);
    expect(screen.queryByText(/Links:/)).toBeNull();
  });
});

describe('Notices', () => {
  it('renders nothing when there is nothing to say', () => {
    const { container } = ui(<Notices notices={[]} />);
    expect(container.textContent).toBe('');
  });

  it('announces an error as an alert and reads the rest in order', () => {
    ui(
      <Notices
        notices={noticesFor({ status: 'stale', incomplete: true, verify: 'ok', failure: 'unavailable', simplified: false })}
      />,
    );
    expect(screen.getByRole('alert').textContent).toContain(en.graph.error.unavailable);
    expect(screen.getByText(en.graph.status.stale)).toBeTruthy();
    expect(screen.getByText(en.graph.incomplete)).toBeTruthy();
  });
});

describe('Toolbar', () => {
  const props = () => ({
    direction: 'any' as const,
    includeTests: false,
    canBack: false,
    canForward: true,
    hasGraph: true,
    tableView: false,
    tableOnly: false,
    onDirection: vi.fn(),
    onIncludeTests: vi.fn(),
    onBack: vi.fn(),
    onForward: vi.fn(),
    onFit: vi.fn(),
    onToggleView: vi.fn(),
  });

  it('disables back with no history and wires the controls', () => {
    const p = props();
    ui(<Toolbar {...p} />);
    expect((screen.getByRole('button', { name: 'Back' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show as table' }));
    fireEvent.click(screen.getByRole('button', { name: 'Fit to view' }));
    expect(p.onForward).toHaveBeenCalled();
    expect(p.onToggleView).toHaveBeenCalled();
    expect(p.onFit).toHaveBeenCalled();
  });

  it('has no way to the canvas when the table is the only view', () => {
    ui(<Toolbar {...props()} tableOnly />);
    expect(screen.queryByRole('button', { name: 'Show as table' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Fit to view' })).toBeNull();
  });
});

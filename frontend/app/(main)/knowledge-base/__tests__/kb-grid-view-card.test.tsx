import React from 'react';
import { act } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { KbGridView } from '../components/kb-grid-view';
import { KbListView } from '../components/kb-list-view';
import type { KnowledgeHubNode } from '../types';
import { hubNode, installBrowserShims, openRowMenu, renderInTheme } from './kb-page-harness';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => true }));

beforeAll(installBrowserShims);
afterEach(cleanup);

const record = hubNode({
  id: 'r1',
  name: 'Runbook.pdf',
  indexingStatus: 'FAILED',
  reason: 'Parser crashed',
  sizeInBytes: 2048,
  updatedAt: 1700000000000,
  previewRenderable: true,
  parentId: 'g1',
  parent: { id: 'g1', nodeType: 'recordGroup', name: 'Platform' },
});

const handlers = {
  onRename: vi.fn(async () => {}),
  onReindex: vi.fn(),
  onReplace: vi.fn(),
  onMove: vi.fn(),
  onDelete: vi.fn(),
  onDownload: vi.fn(),
};

function renderGrid(items: KnowledgeHubNode[], extra: Partial<React.ComponentProps<typeof KbGridView>> = {}) {
  renderInTheme(
    <KbGridView items={items} selectedItems={new Set()} onSelectItem={() => {}} onItemClick={() => {}} {...handlers} {...extra} />,
  );
}

async function openCardMenu(name: string) {
  const trigger = screen.getByRole('button', { name: `More options for ${name}` });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  return screen.getByRole('menu');
}

const labels = (menu: HTMLElement) => within(menu).getAllByRole('menuitem').map((item) => item.textContent?.trim());

describe('grid card', () => {
  it('offers the same menu actions as the list row', async () => {
    renderGrid([record]);
    const gridActions = labels(await openCardMenu('Runbook.pdf'));
    cleanup();
    renderInTheme(
      <KbListView
        items={[record]}
        selectedItems={new Set()}
        allSelected={false}
        sort={{ field: 'name', order: 'asc' }}
        onSelectAll={() => {}}
        onSelectItem={() => {}}
        onItemClick={() => {}}
        onSort={() => {}}
        {...handlers}
      />,
    );
    expect(gridActions).toEqual(labels(await openRowMenu('Runbook.pdf')));
    expect(gridActions.some((label) => label?.endsWith('Download'))).toBe(true);
  });

  it('says what its indexing status is (the reason stays in the tooltip), and shows size and last update', () => {
    renderGrid([record]);
    const card = screen.getByRole('gridcell', { name: 'Runbook.pdf' });
    expect(within(card).getByText('Failed')).toBeTruthy();
    expect(within(card).getByText(/2\.0 KB · \d+ \w+ 2023/)).toBeTruthy();
  });

  it('previews and opens the parent without opening the card', () => {
    const onPreview = vi.fn();
    const onGoToParent = vi.fn();
    const onItemClick = vi.fn();
    renderGrid([record], { onPreview, onGoToParent, onItemClick });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.click(screen.getByRole('button', { name: 'Go to parent' }));
    expect(onPreview).toHaveBeenCalledWith(record);
    expect(onGoToParent).toHaveBeenCalledWith(record);
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it('shows every checkbox once something is selected, and offers select all', () => {
    const onSelectAll = vi.fn();
    const other = hubNode({ id: 'r2', name: 'Notes.txt' });
    renderGrid([record, other], { selectedItems: new Set(['r1']), onSelectAll });
    expect(screen.getByRole('checkbox', { name: 'Select Notes.txt' }).parentElement?.style.visibility).toBe('visible');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }));
    expect(onSelectAll).toHaveBeenCalled();
  });
});

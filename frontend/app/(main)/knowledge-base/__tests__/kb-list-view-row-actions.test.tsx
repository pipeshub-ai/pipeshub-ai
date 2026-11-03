import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { KbListView, getKbItemStatusLabel } from '../components/kb-list-view';
import type { TFunction } from 'i18next';
import type { KnowledgeHubNode } from '../types';
import { hubNode, installBrowserShims, openRowMenu, renderInTheme, row } from './kb-page-harness';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));

const SORT = { field: 'name', order: 'asc' } as const;

function renderRows(items: KnowledgeHubNode[], handlers: Partial<React.ComponentProps<typeof KbListView>> = {}) {
  const onItemClick = vi.fn();
  renderInTheme(
    <KbListView
      items={items}
      selectedItems={new Set()}
      allSelected={false}
      sort={SORT}
      onSelectAll={() => {}}
      onSelectItem={() => {}}
      onItemClick={onItemClick}
      onSort={() => {}}
      {...handlers}
    />,
  );
  return { onItemClick };
}

beforeAll(installBrowserShims);
afterEach(cleanup);

describe('list row actions', () => {
  const page = hubNode({
    id: 'r1',
    name: 'Runbook',
    parentId: 'g1',
    parent: { id: 'g1', nodeType: 'recordGroup', name: 'Platform' },
    previewRenderable: true,
  });

  it('opens the parent of a search result, without opening the row', () => {
    const onGoToParent = vi.fn();
    const { onItemClick } = renderRows([page], { onGoToParent });

    fireEvent.click(within(row('Runbook')).getByRole('button', { name: 'Go to parent' }));

    expect(onGoToParent).toHaveBeenCalledWith(page);
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it('offers no parent outside search results, for an app, or without the parent in the response', () => {
    renderRows([page]);
    expect(screen.queryByRole('button', { name: 'Go to parent' })).toBeNull();
  });

  it.each([
    ['an app', hubNode({ id: 'a1', name: 'Drive', nodeType: 'app', parent: { id: 'x', nodeType: 'app' } })],
    ['a row whose parent was not sent', hubNode({ id: 'r2', name: 'Orphan', parentId: 'g9' })],
  ])('offers no parent for %s', (_label, node) => {
    renderRows([node], { onGoToParent: vi.fn() });
    expect(screen.queryByRole('button', { name: 'Go to parent' })).toBeNull();
  });

  it('previews a record that can be previewed', () => {
    const onPreview = vi.fn();
    const { onItemClick } = renderRows([page], { onPreview });

    fireEvent.click(within(row('Runbook')).getByRole('button', { name: 'Preview' }));

    expect(onPreview).toHaveBeenCalledWith(page);
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it.each([
    ['a record that cannot be previewed', hubNode({ id: 'r3', name: 'Archive', previewRenderable: false })],
    ['a folder', hubNode({ id: 'f1', name: 'Docs', nodeType: 'folder', hasChildren: true })],
  ])('has no preview button for %s', (_label, node) => {
    renderRows([node], { onPreview: vi.fn() });
    expect(screen.queryByRole('button', { name: 'Preview' })).toBeNull();
  });
});

describe('row actions with no collection open (a search at the Collections root)', () => {
  const handlers = { onRename: vi.fn(), onMove: vi.fn(), onDelete: vi.fn(), hasCollectionContext: false };
  const menuItems = async (name: string) =>
    within(await openRowMenu(name)).getAllByRole('menuitem').map((item) => item.textContent?.trim());

  it('offers no rename, move or delete for a folder, which all need its collection', async () => {
    renderRows([hubNode({ id: 'f1', name: 'Docs', nodeType: 'folder', hasChildren: true })], handlers);
    const items = await menuItems('Docs');
    expect(items.some((label) => /Rename|Move|Delete/.test(label ?? ''))).toBe(false);
  });

  it('still renames and deletes a file, but does not move it', async () => {
    renderRows([hubNode({ id: 'r1', name: 'notes.txt' })], handlers);
    const items = (await menuItems('notes.txt')).join(' ');
    expect(items).toMatch(/Rename/);
    expect(items).toMatch(/Delete/);
    expect(items).not.toMatch(/Move/);
  });

  it('keeps every action for a folder inside an open collection', async () => {
    renderRows([hubNode({ id: 'f1', name: 'Docs', nodeType: 'folder', hasChildren: true })], {
      ...handlers,
      hasCollectionContext: true,
    });
    const items = (await menuItems('Docs')).join(' ');
    expect(items).toMatch(/Rename/);
    expect(items).toMatch(/Move/);
    expect(items).toMatch(/Delete/);
  });
});

describe('renaming a folder from its row menu', () => {
  it('puts the cursor in the name field with the name selected, so typing and Enter rename it', async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    const { onItemClick } = renderRows(
      [hubNode({ id: 'f1', name: 'Docs', nodeType: 'folder', hasChildren: true })],
      { onRename, hasCollectionContext: true },
    );

    const menu = await openRowMenu('Docs');
    await act(async () => {
      fireEvent.click(within(menu).getByRole('menuitem', { name: /Rename/ }));
    });

    const field = await waitFor(() => {
      const active = document.activeElement as HTMLInputElement | null;
      expect(active?.tagName).toBe('INPUT');
      return active!;
    });
    expect(field.value).toBe('Docs');
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, 4]);

    fireEvent.change(field, { target: { value: 'Specs' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    await waitFor(() => expect(onRename).toHaveBeenCalledWith(expect.objectContaining({ id: 'f1' }), 'Specs'));
    expect(onItemClick).not.toHaveBeenCalled();
  });
});

describe('the status of a file with nothing to index', () => {
  const t = ((key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : key)) as unknown as TFunction;

  it('says a 0-byte file is empty and why nothing was indexed', () => {
    const file = hubNode({ id: 'r9', name: 'blank.txt', indexingStatus: 'EMPTY', sizeInBytes: 0 });
    expect(getKbItemStatusLabel(file, t)).toBe('Empty file — nothing to index (0 bytes)');
  });

  it('says a file with bytes but no text had nothing to index', () => {
    const file = hubNode({ id: 'r8', name: 'scan.pdf', indexingStatus: 'EMPTY', sizeInBytes: 2048 });
    expect(getKbItemStatusLabel(file, t)).toBe('Empty — no text was found to index');
  });

  it('keeps the short label short', () => {
    const file = hubNode({ id: 'r9', name: 'blank.txt', indexingStatus: 'EMPTY', sizeInBytes: 0 });
    expect(getKbItemStatusLabel(file, t, false)).toBe('kb.indexingStatus.empty');
  });
});

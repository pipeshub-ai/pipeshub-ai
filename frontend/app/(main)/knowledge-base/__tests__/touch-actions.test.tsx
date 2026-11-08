import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { FolderTreeItem } from '../sidebar/section-element/folder-tree-item';
import { KbGridView } from '../components/kb-grid-view';
import { hubNode, installBrowserShims, renderInTheme } from './kb-page-harness';

const canHover = vi.hoisted(() => ({ value: true }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => canHover.value }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true, useIsBelowDesktop: () => true }));

beforeAll(installBrowserShims);
beforeEach(() => {
  canHover.value = true;
});
afterEach(cleanup);

function renderTreeItem() {
  renderInTheme(
    <FolderTreeItem
      node={
        {
          id: 'f1',
          name: 'Docs',
          children: [],
          isExpanded: false,
          depth: 0,
          parentId: null,
          hasChildren: false,
          nodeType: 'folder',
          permission: { role: 'OWNER', canEdit: true, canDelete: true },
        } as never
      }
      isSelected={false}
      onSelect={() => {}}
      onToggle={() => {}}
      expandedFolders={{}}
      onRename={async () => {}}
      onDelete={() => {}}
    />,
  );
}

function renderGrid() {
  renderInTheme(
    <KbGridView
      items={[hubNode({ id: 'r1', name: 'Runbook' })]}
      selectedItems={new Set()}
      onSelectItem={() => {}}
      onItemClick={() => {}}
    />,
  );
}

describe('folder tree meatball', () => {
  it('is hidden until hover on a mouse device', () => {
    renderTreeItem();
    expect(screen.queryAllByRole('button').filter((b) => b.querySelector('[data-icon="more_horiz"], .material-icons-outlined'))).toHaveLength(0);
  });

  it('is rendered without hover on a touch device', () => {
    renderTreeItem();
    const before = screen.getAllByRole('button').length;
    cleanup();
    canHover.value = false;
    renderTreeItem();
    expect(screen.getAllByRole('button').length).toBe(before + 1);
  });
});

describe('grid card checkbox', () => {
  it('is hidden until hover on a mouse device', () => {
    renderGrid();
    expect(screen.getByRole('checkbox', { hidden: true }).parentElement?.style.visibility).toBe('hidden');
  });

  it('is visible without hover on a touch device', () => {
    canHover.value = false;
    renderGrid();
    expect(screen.getByRole('checkbox').parentElement?.style.visibility).toBe('visible');
  });
});

import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import axe from 'axe-core';
import { KbListView, TableHeaderCell, type KbListExtraColumns } from '../components/kb-list-view';
import { hubNode, installBrowserShims, renderInTheme } from './kb-page-harness';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));

// jsdom computes no colours, and the list is mounted without the app's landmarks.
const AXE_OPTIONS: axe.RunOptions = { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } };

const OWNER_COLUMN: KbListExtraColumns = {
  header: <TableHeaderCell label="Owner" width="90px" />,
  cell: () => <span>Alice</span>,
};

function renderList() {
  renderInTheme(
    <KbListView
      items={[hubNode({ id: 'r1', name: 'Budget.xlsx' })]}
      selectedItems={new Set()}
      allSelected={false}
      sort={{ field: 'name', order: 'asc' }}
      onSelectAll={() => {}}
      onSelectItem={() => {}}
      onItemClick={() => {}}
      onSort={() => {}}
      extraColumns={OWNER_COLUMN}
    />,
  );
}

beforeAll(installBrowserShims);
afterEach(cleanup);

describe('records list with an extra column', () => {
  it("shows the extra column's header as one column header, not one nested in another", () => {
    renderList();

    expect(screen.getAllByRole('columnheader', { name: 'Owner' })).toHaveLength(1);
    expect(screen.getAllByRole('columnheader').filter((h) => h.querySelector('[role="columnheader"]'))).toEqual([]);
  });

  it('has no axe violations', async () => {
    renderList();
    await screen.findByRole('row', { name: 'Budget.xlsx' });

    const result = await axe.run(document.body, AXE_OPTIONS);
    expect(result.violations.map((v) => ({ rule: v.id, nodes: v.nodes.map((n) => n.target.join(' ')) }))).toEqual([]);
  });
});

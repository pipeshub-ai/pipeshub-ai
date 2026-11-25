/**
 * PHONETABLES: at phone width EntityDataTable is a list of cards, so no column is cut off.
 * PT-1 every field is present with its label; PT-2 selection, row click and the action menu still work, and a
 * row checkbox is named by the name alone (the avatar initials are hidden);
 * PT-3 loading with no rows is an empty list; PT-4 desktop keeps the row layout.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { EntityDataTable, type ColumnConfig } from '../entity-data-table';
import { AvatarCell } from '../avatar-cell';

type Row = { id: string; name: string; role: string; joined: string };

const rows: Row[] = [
  { id: 'u1', name: 'Ada Lovelace', role: 'Admin', joined: '1 Jan 2026' },
  { id: 'u2', name: 'Alan Turing', role: 'Member', joined: '2 Feb 2026' },
];
const columns: ColumnConfig<Row>[] = [
  { key: 'name', label: 'User', minWidth: '220px', render: (r) => <AvatarCell name={r.name} /> },
  { key: 'role', label: 'Role', width: '112px', render: (r) => <span>{r.role}</span> },
  { key: 'joined', label: 'Date Joined', width: '130px', render: (r) => <span>{r.joined}</span> },
];

function mockWidth(mobile: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: mobile,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

function renderTable(props: Partial<React.ComponentProps<typeof EntityDataTable<Row>>> = {}) {
  const onSelectionChange = vi.fn();
  const onRowClick = vi.fn();
  const onAction = vi.fn();
  render(
    <Theme>
      <EntityDataTable<Row>
        columns={columns}
        data={rows}
        getItemId={(r) => r.id}
        selectedIds={new Set()}
        onSelectionChange={onSelectionChange}
        onRowClick={onRowClick}
        renderRowActions={(r) => (
          <button type="button" aria-label={`Actions for ${r.name}`} onClick={() => onAction(r.id)} />
        )}
        ariaLabel="Users"
        {...props}
      />
    </Theme>
  );
  return { onSelectionChange, onRowClick, onAction };
}

afterEach(cleanup);

describe('EntityDataTable at phone width', () => {
  it('PT-1 lists each row as a card with every field and its label', () => {
    mockWidth(true);
    renderTable();
    const list = screen.getByRole('list', { name: 'Users' });
    const cards = within(list).getAllByRole('listitem');
    expect(cards).toHaveLength(2);
    expect(screen.queryByRole('row')).toBeNull();
    const first = within(cards[0]);
    expect(first.getByText('Ada Lovelace')).toBeTruthy();
    expect(first.getAllByRole('definition').map((d) => d.textContent)).toEqual(['Admin', '1 Jan 2026']);
    expect(first.getAllByRole('term').map((d) => d.textContent)).toEqual(['Role', 'Date Joined']);
  });

  it('PT-2 selection, row click and the action menu still work', () => {
    mockWidth(true);
    const { onSelectionChange, onRowClick, onAction } = renderTable();
    // Exact name: AvatarCell's initials ("AT") must not be read before it.
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alan Turing' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith(new Set(['u2']));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith(new Set(['u1', 'u2']));
    expect(onRowClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Actions for Ada Lovelace' }));
    expect(onAction).toHaveBeenCalledWith('u1');
    expect(onRowClick).not.toHaveBeenCalled();

    const card = screen.getAllByRole('listitem')[1];
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(onRowClick).toHaveBeenCalledWith(rows[1]);
    fireEvent.click(screen.getByText('Ada Lovelace'));
    expect(onRowClick).toHaveBeenLastCalledWith(rows[0]);
  });

  it('PT-3 loading with no rows renders an empty list and an unchecked select-all', () => {
    mockWidth(true);
    renderTable({ data: [], isLoading: true });
    expect(screen.getByRole('list', { name: 'Users' }).children).toHaveLength(0);
    expect(screen.getByRole('checkbox', { name: 'Select all' }).getAttribute('data-state')).toBe('unchecked');
  });

  it('PT-4 desktop keeps the row layout', () => {
    mockWidth(false);
    renderTable();
    expect(screen.queryByRole('list')).toBeNull();
    const tableRows = screen.getAllByRole('row');
    expect(tableRows).toHaveLength(2);
    expect(screen.getByText('Date Joined')).toBeTruthy();
    expect(within(tableRows[0]).getByText('1 Jan 2026')).toBeTruthy();
  });
});

/**
 * AUDITUI: the shared entity components gained optional parts for read-only
 * lists. Their defaults are unchanged (checkboxes, search, CTA); turned off
 * they render nothing in place; the cursor footer pages without a total.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { EntityDataTable, type ColumnConfig } from '../entity-data-table';
import { EntityPageHeader } from '../entity-page-header';
import { EntityEmptyState } from '../entity-empty-state';
import { CursorPagination } from '../cursor-pagination';

type Row = { id: string; name: string };
const rows: Row[] = [
  { id: 'a', name: 'Alpha' },
  { id: 'b', name: 'Beta' },
];
const columns: ColumnConfig<Row>[] = [{ key: 'name', label: 'Name', render: (r) => <span>{r.name}</span> }];

function mockWidth(mobile: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: mobile,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

afterEach(cleanup);

describe('EntityDataTable selectable and highlighted rows', () => {
  for (const mobile of [false, true]) {
    it(`keeps checkboxes by default and drops them with selectable={false} (${mobile ? 'phone' : 'desktop'})`, () => {
      mockWidth(mobile);
      render(
        <Theme>
          <EntityDataTable<Row> columns={columns} data={rows} getItemId={(r) => r.id} selectedIds={new Set()} onSelectionChange={vi.fn()} />
        </Theme>,
      );
      expect(screen.getAllByRole('checkbox').length).toBe(3);
      cleanup();

      const onRowClick = vi.fn();
      render(
        <Theme>
          <EntityDataTable<Row>
            columns={columns}
            data={rows}
            getItemId={(r) => r.id}
            selectable={false}
            onRowClick={onRowClick}
            isRowHighlighted={(r) => r.id === 'b'}
          />
        </Theme>,
      );
      expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
      fireEvent.click(screen.getByText('Beta'));
      expect(onRowClick).toHaveBeenCalledWith(rows[1]);
      const row = screen.getByText('Beta').closest(mobile ? '[role="listitem"]' : '[role="row"]') as HTMLElement;
      expect(row.style.boxShadow).toContain('var(--accent-9)');
    });
  }
});

describe('EntityPageHeader and EntityEmptyState optional parts', () => {
  it('show search and CTA by default, and neither without their handlers', () => {
    render(
      <Theme>
        <EntityPageHeader title="T" subtitle="S" searchValue="" onSearchChange={vi.fn()} ctaLabel="Invite" ctaIcon="add" onCtaClick={vi.fn()} />
      </Theme>,
    );
    expect(screen.getByRole('textbox')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Invite/ })).toBeTruthy();
    cleanup();

    render(
      <Theme>
        <EntityPageHeader title="T" subtitle="S" />
        <EntityEmptyState icon="download" title="Nothing" description="Yet" />
      </Theme>,
    );
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows a badge beside the title only when given one', () => {
    render(
      <Theme>
        <EntityPageHeader title="Runs" subtitle="S" badge={<span data-testid="title-badge">Beta</span>} />
      </Theme>,
    );
    const heading = screen.getByRole('heading', { name: 'Runs' });
    expect(heading.parentElement?.contains(screen.getByTestId('title-badge'))).toBe(true);
    cleanup();

    render(
      <Theme>
        <EntityPageHeader title="Runs" subtitle="S" />
      </Theme>,
    );
    expect(screen.queryByTestId('title-badge')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Runs' })).toBeTruthy();
  });
});

describe('CursorPagination', () => {
  it('pages by Newer and Older, each only when there is one', () => {
    mockWidth(false);
    const onNewer = vi.fn();
    const onOlder = vi.fn();
    render(
      <Theme>
        <CursorPagination from={26} count={25} hasNewer hasOlder={false} onNewer={onNewer} onOlder={onOlder} />
      </Theme>,
    );
    expect(screen.getByText('Showing 26–50')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Newer/ }));
    expect(onNewer).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Older/ }).hasAttribute('disabled')).toBe(true);
  });
});

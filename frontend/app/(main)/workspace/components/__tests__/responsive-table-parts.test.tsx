import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

const mobile = vi.hoisted(() => ({ value: true, belowDesktop: true }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({
  useIsMobile: () => mobile.value,
  useIsBelowDesktop: () => mobile.belowDesktop,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { EntityDataTable } from '../entity-data-table';
import { SettingsRow } from '../settings-row';

afterEach(() => cleanup());

describe('EntityDataTable', () => {
  const columns = [
    { key: 'a', label: 'ColA', width: '200px', render: () => 'a' },
    { key: 'b', label: 'ColB', width: '100px', hideOnMobile: true, render: () => 'b' },
  ];
  const renderTable = () =>
    render(
      <Theme>
        <EntityDataTable
          columns={columns}
          data={[{ id: '1' }]}
          getItemId={(i) => i.id}
          selectedIds={new Set()}
          onSelectionChange={() => {}}
        />
      </Theme>,
    );

  it('lists rows on phones: first column leads, the rest as labelled values, no sideways scroll', () => {
    mobile.value = true;
    mobile.belowDesktop = true;
    render(
      <Theme>
        <EntityDataTable
          columns={[
            { key: 'name', label: 'Name', render: () => 'Ada' },
            { key: 'role', label: 'Role', width: '112px', render: () => 'Admin' },
            { key: 'joined', label: 'Joined', hideOnMobile: true, render: () => 'May' },
          ]}
          data={[{ id: '1' }]}
          getItemId={(i) => i.id}
          selectedIds={new Set()}
          onSelectionChange={() => {}}
          renderRowActions={() => <button type="button">Row menu</button>}
        />
      </Theme>,
    );
    expect(screen.getByTestId('entity-data-table').style.overflowX).toBe('');
    expect(screen.getByText('Ada')).toBeTruthy();
    expect(screen.getByText('Role')).toBeTruthy();
    expect(screen.getByText('Admin')).toBeTruthy();
    expect(screen.queryByText('Joined')).toBeNull();
    expect(screen.getByRole('button', { name: 'Row menu' }).parentElement?.style.position).not.toBe('sticky');
    expect(screen.getByRole('checkbox', { name: 'Select all' })).toBeTruthy();
  });

  it('announces a refresh on phones while rows stay on screen', () => {
    mobile.value = true;
    mobile.belowDesktop = true;
    render(
      <Theme>
        <EntityDataTable
          columns={columns}
          data={[{ id: '1' }]}
          getItemId={(i) => i.id}
          selectedIds={new Set()}
          onSelectionChange={() => {}}
          isLoading
        />
      </Theme>,
    );
    expect(screen.getByLabelText('Refreshing...').getAttribute('aria-live')).toBe('polite');
    expect(screen.getByText('a')).toBeTruthy();
  });

  it('keeps the row actions in view while the table scrolls sideways on tablets', () => {
    mobile.value = false;
    mobile.belowDesktop = true;
    render(
      <Theme>
        <EntityDataTable
          columns={columns}
          data={[{ id: '1' }]}
          getItemId={(i) => i.id}
          selectedIds={new Set()}
          onSelectionChange={() => {}}
          renderRowActions={() => <button type="button">Row menu</button>}
        />
      </Theme>,
    );
    const cell = screen.getByRole('button', { name: 'Row menu' }).parentElement as HTMLElement;
    expect(cell.style.position).toBe('sticky');
    expect(cell.style.right).toBe('0px');
    // The sideways scroller must also be the vertical one, or sticky has nothing to stick to.
    expect(screen.getByTestId('entity-data-table').style.overflowY).toBe('auto');
  });

  it('keeps every column on desktop', () => {
    mobile.value = false;
    mobile.belowDesktop = false;
    renderTable();
    expect(screen.getByText('ColB')).toBeTruthy();
  });
});

describe('SettingsRow', () => {
  it('stacks at the smallest breakpoint', () => {
    const { container } = render(
      <Theme>
        <SettingsRow label="L">
          <input />
        </SettingsRow>
      </Theme>,
    );
    expect(container.querySelector('.rt-r-fd-column')).toBeTruthy();
  });
});

import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const slot = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  KbSelectionActions: (props: Record<string, unknown>) => {
    slot.props = props;
    return <span data-testid="slot" />;
  },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));

import { SelectionActionBar } from '../components/selection-action-bar';
import { UploadDataSidebar } from '../components/dialogs/upload-data-sidebar';

afterEach(() => {
  cleanup();
  slot.props = null;
});

describe('SelectionActionBar slot', () => {
  const base = { selectedCount: 1, onDeselectAll: () => {}, onChat: () => {} };

  it('passes the selected items, view mode and change callback to the slot', () => {
    const items = [{ id: 'a' }] as never[];
    const onChanged = vi.fn();
    render(
      <Theme>
        <SelectionActionBar {...base} selectedItems={items} onChanged={onChanged} pageViewMode="all-records" />
      </Theme>,
    );
    expect(screen.getByTestId('slot')).toBeTruthy();
    expect(slot.props).toEqual({ selectedItems: items, pageViewMode: 'all-records', onChanged });
  });

  it('works when the caller gives neither selected items nor a callback', () => {
    render(
      <Theme>
        <SelectionActionBar {...base} />
      </Theme>,
    );
    expect(slot.props).toEqual({ selectedItems: [], pageViewMode: 'collections', onChanged: undefined });
  });
});

describe('UploadDataSidebar extras', () => {
  const props = { open: true, onOpenChange: () => {}, onSave: () => {} };

  it('renders the extras it is given', () => {
    render(
      <Theme>
        <UploadDataSidebar {...props} extras={<p>extra content</p>} />
      </Theme>,
    );
    expect(screen.getByText('extra content')).toBeTruthy();
  });

  it('renders no extras area without them', () => {
    render(
      <Theme>
        <UploadDataSidebar {...props} />
      </Theme>,
    );
    expect(screen.queryByText('extra content')).toBeNull();
    expect(screen.getByTestId('upload-sidebar-save')).toBeTruthy();
  });
});

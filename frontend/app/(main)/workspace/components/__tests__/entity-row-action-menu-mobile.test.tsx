import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true, useIsBelowDesktop: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { EntityRowActionMenu } from '../entity-row-action-menu';

afterEach(() => cleanup());

describe('EntityRowActionMenu on phones', () => {
  it('opens the actions in a bottom sheet without triggering the row', () => {
    const onRow = vi.fn();
    const onRemove = vi.fn();
    render(
      <Theme>
        <div onClick={onRow}>
          <EntityRowActionMenu actions={[{ icon: 'delete', label: 'Remove', onClick: onRemove }]} />
        </div>
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More options' }));
    const sheet = screen.getByRole('dialog', { name: 'More options' });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove' }));
    expect(onRemove).toHaveBeenCalledOnce();
    expect(onRow).not.toHaveBeenCalled();
    expect(sheet.isConnected).toBe(false);
  });
});

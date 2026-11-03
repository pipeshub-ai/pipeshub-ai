import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true, useIsBelowDesktop: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/config', () => ({ PermissionLockIcon: () => null }));

import { ItemActionMenu } from '../item-action-menu';
import { SelectionActionBar } from '../selection-action-bar';

afterEach(() => cleanup());

describe('ItemActionMenu', () => {
  it('does not pin the trigger to 24px when a bigger size is requested', () => {
    render(
      <Theme>
        <ItemActionMenu triggerSize="3" triggerLabel="More" actions={[{ icon: 'x', label: 'X', onClick: () => {} }]} />
      </Theme>,
    );
    expect(screen.getByLabelText('More').style.width).not.toBe('24px');
  });
});

describe('SelectionActionBar on mobile', () => {
  it('labels every icon-only button', () => {
    render(
      <Theme>
        <SelectionActionBar selectedCount={2} onDeselectAll={() => {}} onChat={() => {}} onReindex={() => {}} onDelete={() => {}} />
      </Theme>,
    );
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    for (const b of buttons) expect(b.getAttribute('aria-label')).toBeTruthy();
  });
});

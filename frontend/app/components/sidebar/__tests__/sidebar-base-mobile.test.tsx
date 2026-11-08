import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { SidebarBase } from '../sidebar-base';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

afterEach(() => cleanup());

function drawer(onMobileClose: () => void, mobileOpen = true) {
  return (
    <Theme>
      <SidebarBase header={<span>Header</span>} isMobile mobileOpen={mobileOpen} onMobileClose={onMobileClose}>
        <span>Nav items</span>
      </SidebarBase>
    </Theme>
  );
}

describe('SidebarBase mobile drawer', () => {
  it('is a modal dialog that closes on Escape and focuses its close button', () => {
    const onClose = vi.fn();
    render(drawer(onClose));

    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: en.sidebar.close }));

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('returns focus to the opener when it closes', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();

    const { rerender } = render(drawer(vi.fn()));
    rerender(drawer(vi.fn(), false));
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('registers itself as a mobile sidebar while mounted on mobile', () => {
    useMobileSidebarStore.setState({ registeredCount: 0 });
    const { unmount } = render(drawer(vi.fn(), false));
    expect(useMobileSidebarStore.getState().registeredCount).toBe(1);
    unmount();
    expect(useMobileSidebarStore.getState().registeredCount).toBe(0);
  });

  it('ignores an Escape that a menu inside the drawer already handled', () => {
    const onClose = vi.fn();
    render(drawer(onClose));

    const handled = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
    handled.preventDefault();
    document.dispatchEvent(handled);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('focuses the first control inside the dialog when it opens on a secondary panel', () => {
    render(
      <Theme>
        <SidebarBase
          header={<span>Header</span>}
          isMobile
          mobileOpen
          onMobileClose={vi.fn()}
          secondaryPanel={<button>Back</button>}
        >
          <span>Nav items</span>
        </SidebarBase>
      </Theme>,
    );
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Back' }));
  });
});

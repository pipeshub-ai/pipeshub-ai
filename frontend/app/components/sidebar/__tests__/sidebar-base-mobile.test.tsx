import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { SidebarBase } from '../sidebar-base';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name, size, color }: { name: string; size?: number; color?: string }) => (
    <i data-icon={name} data-size={size} data-color={color} />
  ),
}));

afterEach(() => cleanup());

function drawer(onMobileClose: () => void, mobileOpen = true, children: React.ReactNode = <span>Nav items</span>) {
  return (
    <Theme>
      <SidebarBase header={<span>Header</span>} isMobile mobileOpen={mobileOpen} onMobileClose={onMobileClose}>
        {children}
      </SidebarBase>
    </Theme>
  );
}

describe('SidebarBase navigation drawer', () => {
  it('is one modal dialog named Navigation, with focus on its close button', () => {
    render(drawer(vi.fn()));

    const dialog = screen.getByRole('dialog', { name: en.sidebar.navigation });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: en.sidebar.close }));
  });

  it('closes on Escape and from its close button', () => {
    const onClose = vi.fn();
    render(drawer(onClose));

    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: en.sidebar.close }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('its Close shows the panel glyph, as the desktop Hide sidebar does: left_panel_close, 16, secondary text', () => {
    render(drawer(vi.fn()));
    const icon = screen.getByRole('button', { name: en.sidebar.close }).querySelector('[data-icon]') as HTMLElement;
    expect(icon.dataset.icon).toBe('left_panel_close');
    expect(icon.dataset.size).toBe('16');
    expect(icon.dataset.color).toBe('var(--text-secondary)');
    expect(document.querySelector('[data-icon="close"]')).toBeNull();
    const button = screen.getByRole('button', { name: en.sidebar.close });
    expect(button.style.width).toBe('var(--space-6)');
    expect(button.style.height).toBe('var(--space-6)');
    expect(button.style.boxSizing).toBe('border-box');
  });

  it('gives the close button a touch-sized hit area', () => {
    render(drawer(vi.fn()));
    expect(screen.getByRole('button', { name: en.sidebar.close }).classList.contains('touch-hit')).toBe(true);
  });

  it('returns focus to the opener when it closes', async () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();

    const { rerender } = render(drawer(vi.fn()));
    rerender(drawer(vi.fn(), false));
    await waitFor(() => expect(document.activeElement).toBe(opener));
    opener.remove();
  });

  it('keeps the page behind out of the accessibility tree while open', () => {
    const page = document.createElement('main');
    document.body.appendChild(page);
    render(drawer(vi.fn()));
    expect(page.getAttribute('aria-hidden')).toBe('true');
    page.remove();
  });

  it('registers itself as a mobile sidebar while mounted in the drawer layout', () => {
    useMobileSidebarStore.setState({ registeredCount: 0 });
    const { unmount } = render(drawer(vi.fn(), false));
    expect(useMobileSidebarStore.getState().registeredCount).toBe(1);
    unmount();
    expect(useMobileSidebarStore.getState().registeredCount).toBe(0);
  });

  it('lets Escape in an inline rename field cancel the rename, not close the drawer', () => {
    const onClose = vi.fn();
    render(
      drawer(
        onClose,
        true,
        <div data-inline-rename="">
          <input aria-label="New name" />
        </div>,
      ),
    );
    const field = screen.getByRole('textbox', { name: 'New name' });
    field.focus();
    fireEvent.keyDown(field, { key: 'Escape' });
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

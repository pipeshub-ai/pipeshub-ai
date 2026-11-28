import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { NotificationFilterMenu } from '../notification-filter-menu';

// jsdom has no ResizeObserver; Radix's popper measures with one.
vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });

afterEach(() => cleanup());

function renderMenu(onChange = vi.fn()) {
  render(
    <Theme>
      <NotificationFilterMenu value="all" onChange={onChange} />
    </Theme>,
  );
  return { trigger: screen.getByRole('button', { name: en.notifications.filter }), onChange };
}

/** Opens the menu from the keyboard, as Radix expects in jsdom. */
async function open(trigger: HTMLElement) {
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'Enter' });
  return screen.findByRole('menu');
}

describe('NotificationFilterMenu focus', () => {
  it('returns focus to the Filter trigger after a filter is chosen', async () => {
    const { trigger, onChange } = renderMenu();
    await open(trigger);
    fireEvent.click(screen.getByRole('menuitem', { name: en.notifications.filterArchived }));
    expect(onChange).toHaveBeenCalledWith('archived');
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it('returns focus to the Filter trigger after Escape', async () => {
    const { trigger } = renderMenu();
    const menu = await open(trigger);
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});

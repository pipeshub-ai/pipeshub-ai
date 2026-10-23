import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Dialog } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

// The real icon renders its ligature name as text; that text must stay out of the accessible name.
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span>{name}</span> }));

import { RoleDropdownMenu } from '../role-dropdown-menu';

beforeEach(() => installBrowserShims());
afterEach(cleanup);

/** Opens the menu the way a keyboard user does: focus the trigger, press Enter. */
const openWithKeyboard = () => {
  const trigger = screen.getByRole('button', { name: /Can view|Can edit|Full Access/ });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'Enter' });
  return trigger;
};

describe('RoleDropdownMenu', () => {
  it('offers owner, edit and view by default', async () => {
    renderInTheme(<RoleDropdownMenu role="READER" onRoleChange={vi.fn()} />);
    openWithKeyboard();
    expect(await screen.findByText('Full Access')).toBeTruthy();
    expect(screen.getByText('Can edit')).toBeTruthy();
  });

  it('offers only the roles an entity allows, and reports the pick', async () => {
    const onRoleChange = vi.fn();
    renderInTheme(<RoleDropdownMenu role="READER" roles={['WRITER', 'READER']} onRoleChange={onRoleChange} />);
    openWithKeyboard();
    expect(screen.queryByText('Full Access')).toBeNull();
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /Can edit/ }));
    expect(onRoleChange).toHaveBeenCalledWith('WRITER');
  });

  it('names the trigger by its level only, and every menu row is at least 44px tall', async () => {
    renderInTheme(<RoleDropdownMenu role="READER" roles={['WRITER', 'READER']} onRoleChange={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Can view' })).toBeTruthy();
    openWithKeyboard();
    for (const row of [...(await screen.findAllByRole('menuitemradio')), screen.getByRole('menuitem', { name: 'Remove' })]) {
      expect(parseInt((row as HTMLElement).style.minHeight, 10)).toBeGreaterThanOrEqual(44);
    }
  });

  it('names the menu by its level only, not by the icon text', async () => {
    renderInTheme(<RoleDropdownMenu role="WRITER" roles={['WRITER', 'READER']} onRoleChange={vi.fn()} />);
    openWithKeyboard();
    expect(await screen.findByRole('menu', { name: 'Can edit' })).toBeTruthy();
    expect(screen.queryByRole('menu', { name: /expand_more/ })).toBeNull();
  });

  it('keeps the highlighted option at 4.5:1 or better (theme tokens, computed from the shipped CSS)', () => {
    const css = fs.readFileSync(path.join(process.cwd(), 'node_modules/@radix-ui/themes/styles.css'), 'utf8')
    const token = (name: string) => new RegExp(`^\\s*--${name}:\\s*(#[0-9a-fA-F]+);`, 'm').exec(css)![1]!
    const rgba = (hex: string): [number, number, number, number] => {
      const h = hex.slice(1)
      return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).concat(h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1) as [number, number, number, number]
    }
    const over = (fg: number[], bg: number[]) => [0, 1, 2].map((i) => fg[i]! * fg[3]! + bg[i]! * (1 - fg[3]!))
    const lum = (c: number[]) => {
      const f = (v: number) => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4)
      return 0.2126 * f(c[0]!) + 0.7152 * f(c[1]!) + 0.0722 * f(c[2]!)
    }
    const ratio = (a: number[], b: number[]) => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
      return (hi! + 0.05) / (lo! + 0.05)
    }
    const panel = rgba(token('olive-2'))
    const onJade = over(rgba(token('jade-a4')), panel)
    const onRed = over(rgba(token('red-a4')), panel)
    expect(ratio(rgba(token('olive-12')), onJade)).toBeGreaterThanOrEqual(4.5)
    expect(ratio(rgba(token('olive-11')), onJade)).toBeGreaterThanOrEqual(4.5)
    expect(ratio(rgba(token('red-12')), onRed)).toBeGreaterThanOrEqual(4.5)
  });

  describe('inside the Share dialog (which traps focus and closes on Escape)', () => {
    const renderInDialog = (onRoleChange = vi.fn(), onDialogOpenChange = vi.fn()) =>
      renderInTheme(
        <Dialog.Root open onOpenChange={onDialogOpenChange}>
          <Dialog.Content>
            <Dialog.Title>Share</Dialog.Title>
            <RoleDropdownMenu role="READER" roles={['WRITER', 'READER']} onRoleChange={onRoleChange} />
          </Dialog.Content>
        </Dialog.Root>,
      );

    it('moves focus into the menu on open, and Arrow keys then Enter pick a level', async () => {
      const onRoleChange = vi.fn();
      renderInDialog(onRoleChange);
      openWithKeyboard();

      const items = await screen.findAllByRole('menuitemradio');
      expect(screen.getByRole('menu')).toBeTruthy();
      await waitFor(() => expect(items.includes(document.activeElement as HTMLElement)).toBe(true));
      const active = document.activeElement as HTMLElement;
      fireEvent.keyDown(active, { key: 'ArrowDown' });
      // Radix moves focus on the next tick.
      await waitFor(() => {
        expect(items.includes(document.activeElement as HTMLElement)).toBe(true);
        expect(document.activeElement).not.toBe(active);
      });
      fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Enter' });
      await waitFor(() => expect(onRoleChange).toHaveBeenCalledTimes(1));
    });

    it('Escape closes only the menu, not the dialog', async () => {
      const onDialogOpenChange = vi.fn();
      renderInDialog(vi.fn(), onDialogOpenChange);
      openWithKeyboard();
      const items = await screen.findAllByRole('menuitemradio');
      await waitFor(() => expect(items.includes(document.activeElement as HTMLElement)).toBe(true));

      fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' });

      await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
      expect(onDialogOpenChange).not.toHaveBeenCalledWith(false);
      expect(screen.getByRole('dialog')).toBeTruthy();
    });
  });
});

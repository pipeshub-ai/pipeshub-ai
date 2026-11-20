/**
 * A menu item's glyph is decoration: the item is named and found by type-ahead by its words, the glyph sits first in
 * the secondary text colour, and a red or disabled item draws it in its own colour. The real MaterialIcon renders, so
 * the glyph's ligature text is in the item as it is in the browser.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DropdownMenu, Theme } from '@radix-ui/themes';
import { IconMenuItem } from '../icon-menu-item';

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

function Menu() {
  return (
    <Theme>
      <DropdownMenu.Root defaultOpen modal={false}>
        <DropdownMenu.Trigger>
          <button type="button">Actions</button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content>
          <IconMenuItem icon="drive_file_rename_outline">Rename</IconMenuItem>
          <IconMenuItem icon="block" disabled>
            Blocked
          </IconMenuItem>
          <IconMenuItem icon="delete" color="red">
            Delete
          </IconMenuItem>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </Theme>
  );
}

const item = (name: string) => screen.getByRole('menuitem', { name });
const glyph = (name: string) => item(name).querySelector<HTMLElement>('.material-icons-outlined')!;

afterEach(cleanup);

describe('IconMenuItem', () => {
  it('leads with its glyph, hidden from screen readers, so the item is named by its words alone', () => {
    render(<Menu />);
    expect(glyph('Rename').textContent).toBe('drive_file_rename_outline');
    expect(item('Rename').firstElementChild?.getAttribute('aria-hidden')).toBe('true');
    expect(item('Rename').firstElementChild?.contains(glyph('Rename'))).toBe(true);
    expect(glyph('Rename').style.fontSize).toBe('16px');
  });

  it('draws the glyph in the secondary text colour, and in the item colour when it is red or disabled', () => {
    render(<Menu />);
    expect(glyph('Rename').style.color).toBe('var(--text-secondary)');
    expect(glyph('Delete').style.color).toBe('inherit');
    expect(glyph('Blocked').style.color).toBe('inherit');
  });

  it('keeps type-ahead on the words: "r" from Delete lands on Rename, whose glyph is spelled drive_file_rename_outline', async () => {
    render(<Menu />);
    act(() => item('Delete').focus());
    fireEvent.keyDown(item('Delete'), { key: 'r' });
    await waitFor(() => expect(document.activeElement).toBe(item('Rename')));
  });
});

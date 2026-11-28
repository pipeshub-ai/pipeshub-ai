import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} /> }));

const canHover = vi.hoisted(() => ({ value: true }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => canHover.value }));

import { ChatItemMenu } from '../chat-item-menu';

async function openMenu() {
  const trigger = screen.getByRole('button');
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
}

function renderMenu(props: Partial<React.ComponentProps<typeof ChatItemMenu>> = {}) {
  const handlers = { onRename: vi.fn(), onArchive: vi.fn(), onDelete: vi.fn() };
  const onRowClick = vi.fn();
  renderInTheme(
    <div onClick={onRowClick}>
      <ChatItemMenu title="Q3 plan" isParentHovered {...handlers} {...props} />
    </div>,
  );
  return { ...handlers, onRowClick };
}

beforeEach(() => {
  canHover.value = true;
  installBrowserShims();
});
afterEach(() => cleanup());

describe('chat row menu', () => {
  it('lists Export between Rename and Archive when export is available', async () => {
    renderMenu({ onExport: vi.fn() });

    await openMenu();

    expect(screen.getAllByRole('menuitem').map((el) => el.textContent)).toEqual(['Rename', 'Export', 'Archive', 'Delete']);
  });

  it('leaves Export out when the row cannot export', async () => {
    renderMenu();

    await openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Export' })).toBeNull();
    expect(screen.getAllByRole('menuitem')).toHaveLength(3);
  });

  it('lists Pin right after Rename when the chat can be pinned', async () => {
    renderMenu({ onTogglePin: vi.fn(), onExport: vi.fn() });

    await openMenu();

    expect(screen.getAllByRole('menuitem').map((el) => el.textContent)).toEqual([
      'Rename',
      'Pin',
      'Export',
      'Archive',
      'Delete',
    ]);
  });

  it('says Unpin instead of Pin for a chat that is already pinned', async () => {
    renderMenu({ onTogglePin: vi.fn(), isPinned: true });

    await openMenu();

    expect(screen.getByRole('menuitem', { name: 'Unpin' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Pin' })).toBeNull();
  });

  it('leaves Pin out when the row cannot be pinned', async () => {
    renderMenu();

    await openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Pin' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Unpin' })).toBeNull();
  });

  it('pins without opening the chat behind the menu or running another action', async () => {
    const onTogglePin = vi.fn();
    const { onRename, onArchive, onDelete, onRowClick } = renderMenu({ onTogglePin });

    await openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pin' }));

    expect(onTogglePin).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
    expect(onRename).not.toHaveBeenCalled();
    expect(onArchive).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('runs the export without opening the chat behind the menu', async () => {
    const onExport = vi.fn();
    const { onRename, onArchive, onDelete, onRowClick } = renderMenu({ onExport });

    await openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export' }));

    expect(onExport).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
    expect(onRename).not.toHaveBeenCalled();
    expect(onArchive).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });
});

/** Each item's words and the glyph it leads with. */
const glyphs = () =>
  screen.getAllByRole('menuitem').map((el) => [el.textContent, el.querySelector('[data-icon]')?.getAttribute('data-icon')]);

describe('chat row menu glyphs', () => {
  it('leads every item with the glyph its action has in every menu', async () => {
    renderMenu({ onTogglePin: vi.fn(), onExport: vi.fn(), onMoveToProject: vi.fn(), onRemoveFromProject: vi.fn() });

    await openMenu();

    expect(glyphs()).toEqual([
      ['Rename', 'drive_file_rename_outline'],
      ['Pin', 'keep'],
      ['Export', 'download'],
      ['Move to project', 'drive_file_move'],
      ['Remove from project', 'folder_off'],
      ['Archive', 'archive'],
      ['Delete', 'delete'],
    ]);
  });

  it('crosses the pin out for Unpin', async () => {
    renderMenu({ onTogglePin: vi.fn(), isPinned: true });

    await openMenu();

    expect(glyphs()).toContainEqual(['Unpin', 'keep_off']);
  });
});

describe('chat row menu layout', () => {
  it('is named for the chat it acts on', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: 'Actions for Q3 plan' })).toBeTruthy();
  });

  it('puts the project items before Archive, and one line before Delete, which comes last', async () => {
    renderMenu({ onTogglePin: vi.fn(), onExport: vi.fn(), onMoveToProject: vi.fn(), onRemoveFromProject: vi.fn() });

    await openMenu();

    expect(screen.getAllByRole('menuitem').map((el) => el.textContent)).toEqual([
      'Rename',
      'Pin',
      'Export',
      'Move to project',
      'Remove from project',
      'Archive',
      'Delete',
    ]);
    const separators = screen.getAllByRole('separator');
    expect(separators).toHaveLength(1);
    expect(separators[0].nextElementSibling?.textContent).toBe('Delete');
  });

  it("lines its list up with the trigger's right edge", async () => {
    renderMenu();
    await openMenu();
    expect(screen.getByRole('menu').getAttribute('data-align')).toBe('end');
  });
});

describe('chat row menu on touch', () => {
  it('fades the trigger out on a hover device until the row is hovered, keeping its place', () => {
    renderMenu({ isParentHovered: false });
    expect(screen.getByRole('button', { name: 'Actions for Q3 plan' }).style.opacity).toBe('0');
  });

  it('keeps the trigger visible when the row cannot be hovered', () => {
    canHover.value = false;
    renderMenu({ isParentHovered: false });
    expect(screen.getByRole('button').style.opacity).toBe('1');
  });
});

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

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
      <ChatItemMenu isParentHovered {...handlers} {...props} />
    </div>,
  );
  return { ...handlers, onRowClick };
}

beforeEach(() => installBrowserShims());
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

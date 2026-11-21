/**
 * Collection menus keep an action the caller's group permissions refuse, locked,
 * and lock each one on the permission its route checks.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { TFunction } from 'i18next';
import type { MenuAction } from '../item-action-menu';

vi.mock('@/config', () => ({
  PermissionLockIcon: () => <span data-testid="lock">Locked</span>,
  useUserPermission: () => true,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { getKbItemMenuActions } from '../kb-list-view';
import { ItemActionMenu } from '../item-action-menu';
import { isReindexPermissionLocked } from '../../hooks/use-collection-group-permissions';

afterEach(cleanup);

// Radix menus measure themselves; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const t = ((key: string) => key) as unknown as TFunction;
const noop = () => {};
const handlers = {
  onOpen: noop,
  onStartRename: noop,
  onRename: async () => {},
  onReindex: noop,
  onReplace: noop,
  onMove: noop,
  onDelete: noop,
};
const node = (overrides: Record<string, unknown>) =>
  ({
    id: 'n1',
    name: 'Node',
    parentId: null,
    origin: 'COLLECTION',
    connector: 'KB',
    hasChildren: true,
    permission: { role: 'OWNER', canEdit: true, canDelete: true },
    sharingStatus: 'private',
    indexingStatus: 'COMPLETED',
    ...overrides,
  }) as any;
const collection = node({ id: 'kb1', nodeType: 'app' });
const folder = node({ id: 'f1', nodeType: 'folder' });

const actionsOf = (item: unknown, groupPermissions?: { canEdit: boolean; canDelete: boolean; canManageSync: boolean }) =>
  (getKbItemMenuActions(item as any, t, { ...handlers, groupPermissions }).filter(Boolean) as MenuAction[]);
const byLabel = (actions: MenuAction[], label: string) => actions.find((a) => a.label === label);
const reindexActions = (actions: MenuAction[]) =>
  actions.filter((a) => !['kb.rowActions.open', 'menu.rename', 'action.replace', 'action.move', 'action.delete'].includes(a.label));

describe('collection item menu locks', () => {
  it('locks rename and delete when edit and delete are off', () => {
    const actions = actionsOf(collection, { canEdit: false, canDelete: false, canManageSync: true });
    expect(byLabel(actions, 'menu.rename')?.permissionLocked).toBe(true);
    expect(byLabel(actions, 'action.delete')?.permissionLocked).toBe(true);
  });

  it('locks a collection reindex on connector sync, not on edit', () => {
    const syncOff = reindexActions(actionsOf(collection, { canEdit: true, canDelete: true, canManageSync: false }));
    const editOff = reindexActions(actionsOf(collection, { canEdit: false, canDelete: true, canManageSync: true }));
    expect(syncOff.length).toBeGreaterThan(0);
    expect(syncOff.every((a) => a.permissionLocked)).toBe(true);
    expect(editOff.every((a) => !a.permissionLocked)).toBe(true);
  });

  it('locks a folder reindex and move on edit', () => {
    const actions = actionsOf(folder, { canEdit: false, canDelete: true, canManageSync: true });
    const reindex = reindexActions(actions);
    expect(reindex.length).toBeGreaterThan(0);
    expect(reindex.every((a) => a.permissionLocked)).toBe(true);
    expect(byLabel(actions, 'action.move')?.permissionLocked).toBe(true);
    expect(byLabel(actions, 'action.delete')?.permissionLocked).toBe(false);
  });

  it('locks nothing when every permission is on, or none is given', () => {
    for (const actions of [actionsOf(folder, { canEdit: true, canDelete: true, canManageSync: true }), actionsOf(folder)]) {
      expect(actions.some((a) => a.permissionLocked)).toBe(false);
    }
  });

  it('isReindexPermissionLocked follows the route each node uses', () => {
    const perms = { canEdit: false, canDelete: true, canManageSync: true };
    expect(isReindexPermissionLocked({ nodeType: 'app', connector: 'KB' }, perms)).toBe(false);
    expect(isReindexPermissionLocked({ nodeType: 'folder' }, perms)).toBe(true);
    expect(isReindexPermissionLocked({ nodeType: 'app', connector: 'KB' }, { ...perms, canManageSync: false })).toBe(true);
    expect(isReindexPermissionLocked({ nodeType: 'folder' }, undefined)).toBe(false);
  });
});

describe('ItemActionMenu', () => {
  it('renders a locked action disabled, with the lock, and does not run it', () => {
    const onClick = vi.fn();
    render(
      <Theme>
        <ItemActionMenu
          open
          onOpenChange={noop}
          actions={[{ icon: 'edit', label: 'Rename', onClick, permissionLocked: true }, { icon: 'open', label: 'Open', onClick: noop }]}
        />
      </Theme>,
    );
    const locked = screen.getByRole('menuitem', { name: /Rename/ });
    expect(locked.hasAttribute('data-disabled')).toBe(true);
    expect(locked.textContent).toContain('Locked');
    locked.click();
    expect(onClick).not.toHaveBeenCalled();
    expect(screen.getByRole('menuitem', { name: /Open/ }).hasAttribute('data-disabled')).toBe(false);
  });
});

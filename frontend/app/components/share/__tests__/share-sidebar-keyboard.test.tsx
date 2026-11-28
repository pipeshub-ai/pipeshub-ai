/**
 * The share drawer by keyboard and screen reader: a suggested person is a checkbox reached by Tab and picked with Space
 * or Enter, each member's level button says whose level it is, and closing the drawer gives focus back to what opened it.
 */
import React, { useState } from 'react';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/config', async () => ({ useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: 'me' } }) }));

import { ShareSidebar } from '../share-sidebar';
import type { ShareAdapter } from '../types';

const SUGGESTED = [{ id: 'u3', name: 'Sam Suggested', email: 's@x.com', isInOrg: true }];
const MEMBERS = [
  { id: 'u1', type: 'user', name: 'Mina Member', email: 'm@x.com', role: 'READER' },
  { id: 'u2', type: 'user', name: 'Noor Member', email: 'n@x.com', role: 'READER' },
];

const adapter = (): ShareAdapter => ({
  entityType: 'conversation',
  entityId: 'c1',
  sidebarTitle: 'Share Chat',
  supportsRoles: true,
  supportsTeams: false,
  roleOptions: {
    roles: ['WRITER', 'READER'],
    labels: {
      OWNER: { label: 'Full Access', description: '' },
      WRITER: { label: 'Can edit', description: 'edit' },
      READER: { label: 'Can view', description: 'view' },
    },
  },
  getSharedMembers: vi.fn().mockResolvedValue(MEMBERS),
  share: vi.fn() as never,
  removeMember: vi.fn(),
  getSharingUsersPaginated: vi.fn().mockResolvedValue({ users: SUGGESTED, totalCount: 1 }),
});

/** A page with a Share button that opens the drawer, as the chat page's does. */
function Page() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open share
      </button>
      <ShareSidebar open={open} onOpenChange={setOpen} adapter={adapter()} />
    </>
  );
}

beforeEach(() => installBrowserShims());
afterEach(cleanup);

describe('the share drawer by keyboard and screen reader', () => {
  it('a suggested person is a checkbox in the tab order, picked with Space or Enter', async () => {
    for (const key of [' ', 'Enter']) {
      const view = renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter()} />);

      const row = await screen.findByRole('checkbox', { name: /Sam Suggested/ });
      expect(row.tabIndex, key).toBe(0);
      expect(row.getAttribute('aria-checked')).toBe('false');
      fireEvent.keyDown(row, { key });
      // Picked: the person leaves the suggestions for the field, where a button takes them out again.
      const remove = await screen.findByRole('button', { name: 'Remove s@x.com' });
      expect(screen.queryByRole('checkbox', { name: /Sam Suggested/ })).toBeNull();
      fireEvent.click(remove);
      expect(await screen.findByRole('checkbox', { name: /Sam Suggested/ })).toBeTruthy();
      view.unmount();
    }
  });

  it("each member's level button says whose level it is", async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter()} />);

    expect(await screen.findByRole('button', { name: 'Can view, Mina Member' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Can view, Noor Member' })).toBeTruthy();
    const result = await axe.run(document.body, { runOnly: ['nested-interactive', 'button-name', 'aria-allowed-attr', 'aria-required-attr'] });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it('closing it with Escape gives focus back to the button that opened it', async () => {
    renderInTheme(<Page />);
    const opener = screen.getByRole('button', { name: 'Open share' });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole('checkbox', { name: /Sam Suggested/ });
    expect(document.activeElement).not.toBe(opener);

    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });
});

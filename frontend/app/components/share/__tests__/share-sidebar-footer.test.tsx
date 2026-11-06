import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('@/config', async () => ({ useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: 'me' } }) }));
const apiMock = vi.hoisted(() => ({
  listUserTeams: vi.fn(),
}));
vi.mock('../api', () => ({ ShareCommonApi: apiMock }));

import { ShareSidebar } from '../share-sidebar';
import type { ShareAdapter } from '../types';

function adapter(overrides: Partial<ShareAdapter> = {}): ShareAdapter {
  return {
    entityType: 'kb',
    entityId: 'kb1',
    sidebarTitle: 'Share collection',
    supportsRoles: true,
    supportsTeams: true,
    getSharedMembers: vi.fn().mockResolvedValue([]),
    share: vi.fn(),
    removeMember: vi.fn().mockResolvedValue({ removed: true }),
    getSharingUsersPaginated: vi.fn().mockResolvedValue({ users: [], totalCount: 0 }),
    ...overrides,
  };
}

beforeEach(() => {
  installBrowserShims();
  vi.clearAllMocks();
  apiMock.listUserTeams.mockResolvedValue([{ id: 't1', name: 'Design team', memberCount: 3 }]);
});
afterEach(cleanup);

describe('share sidebar footer', () => {
  it('has one outline gray secondary and one unpainted primary', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter()} />);
    await screen.findByText('Design team');

    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const footer = cancel.parentElement as HTMLElement;
    const buttons = within(footer).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Cancel', 'Share']);
    expect(cancel.className).toContain('rt-variant-outline');
    expect(cancel.getAttribute('data-accent-color')).toBe('gray');
    const share = buttons[1]!;
    expect(share.className).toContain('rt-variant-solid');
    expect(share.getAttribute('style') ?? '').not.toContain('background');
  });

  it('offers "Create a new team" once, on the suggested teams row', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter()} />);
    await screen.findByText('Design team');

    const create = screen.getAllByRole('button', { name: 'Create a new team' });
    expect(create).toHaveLength(1);
    expect(create[0]!.className).toContain('rt-variant-ghost');
    const row = create[0]!.parentElement as HTMLElement;
    expect(within(row).getByText('Suggested teams')).toBeTruthy();

    fireEvent.click(create[0]!);
    expect(await screen.findByRole('button', { name: 'Back' })).toBeTruthy();
  });

  it('asks before removing access, with an outline gray Cancel beside the act', async () => {
    renderInTheme(
      <ShareSidebar
        open
        onOpenChange={() => undefined}
        adapter={adapter({
          supportsTeams: false,
          confirmBeforeRemove: true,
          getSharedMembers: vi.fn().mockResolvedValue([
            { id: 'u2', type: 'user', name: 'Alex Doe', role: 'READER', isOwner: false, isCurrentUser: false },
          ]),
        })}
      />,
    );
    await screen.findByText('Alex Doe');
    const trigger = screen.getAllByRole('button').find((b) => b.getAttribute('aria-haspopup') === 'menu') as HTMLElement;
    fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
    const dialog = await screen.findByRole('alertdialog');
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
    expect(cancel.className).toContain('rt-variant-outline');
    expect(cancel.getAttribute('data-accent-color')).toBe('gray');
  });

  it('names the close button', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ supportsTeams: false })} />);
    expect(await screen.findByRole('button', { name: 'Close' })).toBeTruthy();
  });
});

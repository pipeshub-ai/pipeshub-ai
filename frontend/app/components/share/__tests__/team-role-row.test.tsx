import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (typeof cur !== 'string') return key;
      return cur.replace(/\{\{(\w+)\}\}/g, (_, k) => vars?.[k] ?? '');
    },
  }),
}));

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient }));
vi.mock('@/config', () => ({
  useAuthStore: Object.assign(
    (selector: (s: { user: { id: string } }) => unknown) => selector({ user: { id: 'me' } }),
    { getState: () => ({ user: { id: 'me' } }) },
  ),
}));
vi.mock('@/lib/store/user-store', () => ({ useUserStore: { getState: () => ({ profile: { userId: 'me' } }) } }));
vi.mock('@/app/components/share/utils', () => ({
  fetchShareUsersPaginated: vi.fn().mockResolvedValue({ users: [], totalCount: 0 }),
}));
vi.mock('@/app/components/share/api', () => ({
  ShareCommonApi: { getAllUsers: vi.fn().mockResolvedValue([]), listUserTeams: vi.fn().mockResolvedValue([]) },
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
vi.mock('@/app/(main)/workspace/components/avatar-cell', () => ({ AvatarCell: ({ name }: { name: string }) => <span>{name}</span> }));

import { createKBShareAdapter } from '@/app/(main)/knowledge-base/share-adapter';
import { ShareSidebar } from '../share-sidebar';
import { ShareableRow } from '../shareable-row';

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('team row role', () => {
  it('shows the granted role instead of the no-roles notice', () => {
    render(
      <Theme>
        <ShareableRow type="team" name="Eng" role="WRITER" showRoleDropdown teamRoleEditable />
      </Theme>,
    );
    expect(screen.getByRole('button', { name: new RegExp(en.shareSidebar.roles.writer.label) })).toBeTruthy();
    expect(screen.queryByText(en.shareSidebar.team)).toBeNull();
  });

  it('keeps the no-roles notice for entities whose teams carry no role', () => {
    render(
      <Theme>
        <ShareableRow type="team" name="Eng" role="WRITER" showRoleDropdown />
      </Theme>,
    );
    expect(screen.getByRole('button', { name: new RegExp(en.shareSidebar.team) })).toBeTruthy();
  });

  it('offers only edit, comment and view and reports the picked role', () => {
    const onRoleChange = vi.fn();
    render(
      <Theme>
        <ShareableRow type="team" name="Eng" role="READER" showRoleDropdown teamRoleEditable onRoleChange={onRoleChange} />
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.shareSidebar.roles.reader.label) }));
    expect(screen.queryByText(en.shareSidebar.roles.owner.label)).toBeNull();
    fireEvent.click(screen.getByText(en.shareSidebar.roles.commenter.label));
    expect(onRoleChange).toHaveBeenCalledWith('COMMENTER');
  });
});

describe('KB share sidebar team role', () => {
  it('lists the team with its granted role and updates it through the permissions endpoint', async () => {
    apiClient.get.mockResolvedValue({
      data: { permissions: [{ type: 'TEAM', id: 't1', name: 'Eng', role: 'WRITER' }] },
    });
    apiClient.put.mockResolvedValue({ data: {} });

    render(
      <Theme>
        <ShareSidebar open onOpenChange={() => {}} adapter={createKBShareAdapter('kb-1')} />
      </Theme>,
    );

    const trigger = await screen.findByRole('button', { name: new RegExp(en.shareSidebar.roles.writer.label) });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText(en.shareSidebar.roles.reader.label));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    expect(apiClient.put.mock.calls[0][0]).toBe('/api/v1/knowledgeBase/kb-1/permissions');
    expect(apiClient.put.mock.calls[0][1]).toEqual({ userIds: [], teamIds: ['t1'], role: 'READER' });
    await screen.findByRole('button', { name: new RegExp(en.shareSidebar.roles.reader.label) });
  });
});

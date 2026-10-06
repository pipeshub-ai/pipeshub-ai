import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { buildShareSubmission, toTeamShareRole, type ShareSelection } from '../types';
import { ShareSearchInput } from '../share-search-input';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient }));
vi.mock('@/config', () => ({ useAuthStore: { getState: () => ({ user: { id: 'me' } }) } }));
vi.mock('@/lib/store/user-store', () => ({ useUserStore: { getState: () => ({ profile: { userId: 'me' } }) } }));
vi.mock('@/app/components/share/utils', () => ({ fetchShareUsersPaginated: vi.fn() }));

import { createKBShareAdapter } from '@/app/(main)/knowledge-base/share-adapter';

const user = (id: string): ShareSelection => ({ type: 'user', id, name: id });
const team = (id: string, role?: ShareSelection['role']): ShareSelection => ({ type: 'team', id, name: id, role });

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('buildShareSubmission', () => {
  it('gives users the sidebar role and each team its own role', () => {
    const submission = buildShareSubmission(
      [user('u1'), team('t1', 'WRITER'), team('t2', 'COMMENTER')],
      'OWNER',
    );
    expect(submission.principals).toEqual([
      { principalType: 'user', principalId: 'u1', role: 'OWNER' },
      { principalType: 'team', principalId: 't1', role: 'WRITER' },
      { principalType: 'team', principalId: 't2', role: 'COMMENTER' },
    ]);
  });

  it('never leaves OWNER on a team, even when the sidebar role is OWNER and none was picked', () => {
    const submission = buildShareSubmission([team('t1'), team('t2', 'OWNER')], 'OWNER');
    expect(submission.principals.map((p) => p.role)).toEqual(['READER', 'READER']);
  });

  it('toTeamShareRole clamps OWNER and undefined to READER', () => {
    expect(toTeamShareRole('OWNER')).toBe('READER');
    expect(toTeamShareRole(undefined)).toBe('READER');
    expect(toTeamShareRole('COMMENTER')).toBe('COMMENTER');
  });
});

describe('KB share adapter', () => {
  it('posts principals with per-principal roles and no legacy single role', async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    const adapter = createKBShareAdapter('kb-1');
    await adapter.share(buildShareSubmission([user('u1'), team('t1', 'WRITER')], 'OWNER'));

    expect(apiClient.post).toHaveBeenCalledTimes(1);
    const [url, body] = apiClient.post.mock.calls[0];
    expect(url).toBe('/api/v1/knowledgeBase/kb-1/permissions');
    expect(body).toEqual({
      principals: [
        { principalType: 'user', principalId: 'u1', role: 'OWNER' },
        { principalType: 'team', principalId: 't1', role: 'WRITER' },
      ],
    });
    expect(body).not.toHaveProperty('role');
    expect(body).not.toHaveProperty('teamIds');
  });
});

describe('team chip role picker', () => {
  function renderInput(selections: ShareSelection[], onTeamRoleChange = vi.fn()) {
    render(
      <Theme>
        <ShareSearchInput
          selections={selections}
          searchQuery=""
          selectedRole="OWNER"
          supportsRoles
          onSearchChange={() => {}}
          onRemoveSelection={() => {}}
          onRoleChange={() => {}}
          onTeamRoleChange={onTeamRoleChange}
        />
      </Theme>,
    );
    return onTeamRoleChange;
  }

  it('offers only edit, comment and view for a team and never OWNER', () => {
    const onTeamRoleChange = renderInput([team('t1', 'READER')]);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.shareSidebar.roles.reader.label) }));

    expect(screen.getAllByText(en.shareSidebar.roles.writer.label).length).toBeGreaterThan(0);
    expect(screen.getAllByText(en.shareSidebar.roles.commenter.label).length).toBeGreaterThan(0);
    expect(screen.queryByText(en.shareSidebar.roles.owner.label)).toBeNull();

    fireEvent.click(screen.getByText(en.shareSidebar.roles.writer.label));
    expect(onTeamRoleChange).toHaveBeenCalledWith('t1', 'WRITER');
  });

  it('shows no user role picker when only teams are selected', () => {
    renderInput([team('t1')]);
    expect(screen.queryByText(en.shareSidebar.roles.owner.label)).toBeNull();
    // the team chip's own picker, plus its remove button: no separate user picker
    expect(screen.getAllByRole('button').filter((b) => b.getAttribute('aria-label') !== 'Remove')).toHaveLength(1);
  });
});

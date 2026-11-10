import { beforeEach, describe, expect, it, vi } from 'vitest';

const projectApi = vi.hoisted(() => ({
  listMembers: vi.fn(),
  upsertMembers: vi.fn(),
  removeMember: vi.fn(),
}));
const shareApi = vi.hoisted(() => ({ getUsersByIds: vi.fn(), listUserTeams: vi.fn() }));
const teamsApi = vi.hoisted(() => ({ getTeam: vi.fn() }));

vi.mock('../project-api', () => ({ ProjectApi: projectApi }));
vi.mock('@/app/components/share/api', () => ({ ShareCommonApi: shareApi }));
vi.mock('@/app/(main)/workspace/teams/api', () => ({ TeamsApi: teamsApi }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: {} }));
vi.mock('@/lib/api', () => ({ apiClient: {} }));
vi.mock('@/config', () => ({ useAuthStore: { getState: () => ({ user: null }) } }));
vi.mock('@/lib/store/user-store', () => ({ useUserStore: { getState: () => ({ profile: { userId: 'owner' } }) } }));

import { createProjectShareAdapter } from '../share-adapter';
import type { ProjectDetail } from '../project-types';

const project = { _id: 'p1', userId: 'owner', members: [] } as unknown as ProjectDetail;

beforeEach(() => {
  vi.clearAllMocks();
  shareApi.getUsersByIds.mockResolvedValue([{ id: 'owner', name: 'Olive Owner', isInOrg: true }]);
  shareApi.listUserTeams.mockResolvedValue([{ id: 'team-1', name: 'Design', memberCount: 4 }]);
});

describe('createProjectShareAdapter', () => {
  it('offers only the two project roles and supports teams', () => {
    const adapter = createProjectShareAdapter(project);
    expect(adapter.roleOptions?.roles).toEqual(['WRITER', 'READER']);
    expect(adapter.supportsTeams).toBe(true);
    expect(adapter.teamsHaveRoles).toBe(true);
    expect(adapter.confirmBeforeRemove).toBe(true);
  });

  it('lists the owner, teams and users, using the names the members API returns', async () => {
    projectApi.listMembers.mockResolvedValue([
      { principalType: 'user', principalId: 'u1', role: 'editor', name: 'Ed Editor', email: 'ed@x.com' },
      { principalType: 'team', principalId: 'team-1', role: 'viewer' },
      { principalType: 'team', principalId: 'team-2', role: 'editor' },
    ]);
    teamsApi.getTeam.mockResolvedValue({ id: 'team-2', name: 'Support', memberCount: 2 });

    const rows = await createProjectShareAdapter(project).getSharedMembers();

    expect(shareApi.getUsersByIds).toHaveBeenCalledWith(['owner']);
    expect(rows.map((r) => [r.type, r.id, r.name, r.role])).toEqual([
      ['user', 'owner', 'Olive Owner', 'OWNER'],
      ['team', 'team-1', 'Design', 'READER'],
      ['team', 'team-2', 'Support', 'WRITER'],
      ['user', 'u1', 'Ed Editor', 'WRITER'],
    ]);
  });

  it('saves the picked role for users and teams alike', async () => {
    const adapter = createProjectShareAdapter(project);
    await adapter.share({ userIds: ['u1'], teamIds: ['team-1'], role: 'WRITER' });
    expect(projectApi.upsertMembers).toHaveBeenCalledWith('p1', [
      { principalId: 'u1', principalType: 'user', role: 'editor' },
      { principalId: 'team-1', principalType: 'team', role: 'editor' },
    ]);
  });

  it('never saves OWNER as a member role', async () => {
    await createProjectShareAdapter(project).updateRole!('u1', 'user', 'OWNER');
    expect(projectApi.upsertMembers).toHaveBeenCalledWith('p1', [
      { principalId: 'u1', principalType: 'user', role: 'editor' },
    ]);
  });

  it('removes a team by its principal type and passes on removed:false', async () => {
    projectApi.removeMember.mockResolvedValue({ members: [], removed: false });
    const result = await createProjectShareAdapter(project).removeMember('team-1', 'team');
    expect(projectApi.removeMember).toHaveBeenCalledWith('p1', 'team-1', 'team');
    expect(result).toEqual({ removed: false });
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
vi.mock('@/lib/api', () => ({
  apiClient: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
    put: (...args: unknown[]) => put(...args),
    delete: vi.fn(),
  },
}));

import { TeamsApi, unwrapTeam } from '../api';

const team = { id: 't1', name: 'QA Team' };

describe('unwrapTeam', () => {
  it('reads the team from each shape the team service answers with', () => {
    expect(unwrapTeam({ status: 'success', message: 'Team created successfully', data: team })).toEqual(team);
    expect(unwrapTeam({ status: 'success', team })).toEqual(team);
    expect(unwrapTeam(team)).toEqual(team);
  });
});

describe('TeamsApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    put.mockReset();
  });

  it('createTeam returns the created team, not the envelope', async () => {
    post.mockResolvedValue({ data: { status: 'success', message: 'Team created successfully', data: team } });

    const created = await TeamsApi.createTeam({ name: 'QA Team' });

    expect(created.name).toBe('QA Team');
    // the create form shows the server's reason itself
    expect(post.mock.calls[0][2]).toEqual({ suppressErrorToast: true });
  });

  it('updateTeam and getTeam unwrap { team }', async () => {
    put.mockResolvedValue({ data: { status: 'success', team } });
    get.mockResolvedValue({ data: { status: 'success', team } });

    expect(await TeamsApi.updateTeam('t1', { name: 'QA Team' })).toEqual(team);
    expect(await TeamsApi.getTeam('t1')).toEqual(team);
  });

  it('getTeam does not toast a 404 for a team the caller is not in', async () => {
    get.mockResolvedValue({ data: { team } });
    await TeamsApi.getTeam('t1');

    const { suppressErrorToast } = get.mock.calls[0][1];
    expect(suppressErrorToast({ statusCode: 404 })).toBe(true);
    expect(suppressErrorToast({ statusCode: 500 })).toBe(false);
  });
});

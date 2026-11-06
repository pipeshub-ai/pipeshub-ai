import { beforeEach, describe, expect, it, vi } from 'vitest';

const post = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const { UsersApi } = await import('../api');

beforeEach(() => {
  post.mockReset();
});

describe('UsersApi.checkInviteStatus', () => {
  it('posts the addresses without a toast on failure and maps members and pending invites', async () => {
    post.mockResolvedValue({
      data: {
        existing: [
          { email: 'Dave@Lab.test', status: 'member' },
          { email: 'pat@lab.test', status: 'invited' },
          { email: 'odd@lab.test', status: 'something-new' },
          { status: 'member' },
        ],
      },
    });

    const result = await UsersApi.checkInviteStatus(['dave@lab.test', 'pat@lab.test']);

    expect(post).toHaveBeenCalledWith(
      '/api/v1/users/bulk/invite/check',
      { emails: ['dave@lab.test', 'pat@lab.test'] },
      { suppressErrorToast: true },
    );
    expect(result).toEqual({ 'dave@lab.test': 'member', 'pat@lab.test': 'invited' });
  });

  it('sends at most 500 addresses per request', async () => {
    post.mockResolvedValue({ data: { existing: [] } });
    const emails = Array.from({ length: 1100 }, (_, i) => `u${i}@lab.test`);

    await UsersApi.checkInviteStatus(emails);

    expect(post.mock.calls.map((call) => (call[1] as { emails: string[] }).emails.length)).toEqual([500, 500, 100]);
  });
});

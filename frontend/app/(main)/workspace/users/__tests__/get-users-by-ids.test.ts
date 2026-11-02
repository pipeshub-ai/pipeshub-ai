import { beforeEach, describe, expect, it, vi } from 'vitest';

const post = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const { UsersApi } = await import('../api');
const { ShareCommonApi } = await import('@/app/components/share/api');
const { toLookupUserIds } = await import('@/lib/utils/user-ids');

const ID_A = '507f1f77bcf86cd799439011';
const ID_B = '507f1f77bcf86cd799439012';

beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ data: [] });
});

describe('toLookupUserIds', () => {
  it('drops blank and missing ids and duplicates', () => {
    expect(toLookupUserIds(['', ID_A, '  ', null, undefined, ID_A, ` ${ID_B} `])).toEqual([ID_A, ID_B]);
  });
});

describe.each([
  ['UsersApi.getUsersByIds', (ids: string[]) => UsersApi.getUsersByIds(ids)],
  ['ShareCommonApi.getUsersByIds', (ids: string[]) => ShareCommonApi.getUsersByIds(ids)],
])('%s', (_name, lookup) => {
  it('skips the request when the only id is empty', async () => {
    await expect(lookup([''])).resolves.toEqual([]);
    expect(post).not.toHaveBeenCalled();
  });

  it('sends only the non-empty ids', async () => {
    await lookup(['', ID_A, ID_A]);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1]).toEqual({ userIds: [ID_A] });
  });
});

describe('UsersApi.inviteUsers', () => {
  it('passes on the invited and already-member counts', async () => {
    post.mockResolvedValue({
      data: { queued: true, invited: 2, reinvited: 1, restored: 0, alreadyActive: 3 },
    });
    await expect(UsersApi.inviteUsers(['a@x.com'])).resolves.toEqual({
      queued: true,
      invited: 2,
      reinvited: 1,
      restored: 0,
      alreadyActive: 3,
    });
  });

  it('leaves the counts out when the server does not send them', async () => {
    post.mockResolvedValue({ data: { queued: true } });
    await expect(UsersApi.inviteUsers(['a@x.com'])).resolves.toEqual({
      queued: true,
      invited: undefined,
      reinvited: undefined,
      restored: undefined,
      alreadyActive: undefined,
    });
  });
});

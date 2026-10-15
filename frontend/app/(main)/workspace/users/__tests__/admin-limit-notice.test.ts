import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  apiClient: { get, post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const { UsersApi } = await import('../api');
const { getAdminLimitNotice } = await import('../components/admin-limit-notice');
const { isAdminLimitReached } = await import('../use-admin-limit-status');

const ENFORCEMENT = '2027-01-01T00:00:00.000Z';
const overLimit = {
  adminCount: 3,
  maxAdmins: 1,
  overLimit: true,
  enforcementDate: ENFORCEMENT,
  retainedAdminEmail: 'owner@a.test',
};

describe('getAdminLimitNotice', () => {
  const before = new Date('2026-12-31T23:59:59.999Z');
  const after = new Date(ENFORCEMENT);

  it('warns an admin of an over-limit org before the enforcement date', () => {
    expect(getAdminLimitNotice(overLimit, true, before)).toEqual({
      kind: 'upcoming',
      adminCount: 3,
      enforcementDate: new Date(ENFORCEMENT),
      retainedAdminEmail: 'owner@a.test',
    });
  });

  it('switches to the enforced message from the enforcement date', () => {
    expect(getAdminLimitNotice(overLimit, true, after)?.kind).toBe('due');
  });

  it('shows nothing to members or while the role is unknown', () => {
    expect(getAdminLimitNotice(overLimit, false, before)).toBeNull();
    expect(getAdminLimitNotice(overLimit, null, before)).toBeNull();
  });

  it('shows nothing to an org within the limit or before the status loads', () => {
    expect(getAdminLimitNotice({ ...overLimit, adminCount: 1, overLimit: false }, true, before)).toBeNull();
    expect(getAdminLimitNotice(null, true, before)).toBeNull();
  });

  it('shows nothing when the server sends an unreadable date', () => {
    expect(getAdminLimitNotice({ ...overLimit, enforcementDate: 'soon' }, true, before)).toBeNull();
  });
});

describe('UsersApi.getAdminLimitStatus', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('reads the admin limit without an error toast', async () => {
    get.mockResolvedValue({ data: overLimit });

    await expect(UsersApi.getAdminLimitStatus()).resolves.toEqual(overLimit);
    expect(get).toHaveBeenCalledWith('/api/v1/users/admin-limit', { suppressErrorToast: true });
  });
});

describe('isAdminLimitReached', () => {
  it('is reached when the org already has as many admins as it may', () => {
    expect(isAdminLimitReached({ ...overLimit, adminCount: 1, overLimit: false })).toBe(true);
    expect(isAdminLimitReached(overLimit)).toBe(true);
  });

  it('is not reached below the limit, with no limit, or before the status loads', () => {
    expect(isAdminLimitReached({ ...overLimit, adminCount: 0, overLimit: false })).toBe(false);
    expect(isAdminLimitReached({ ...overLimit, maxAdmins: null, overLimit: false })).toBe(false);
    expect(isAdminLimitReached(null)).toBe(false);
  });
});

describe('UsersApi.transferAdmin', () => {
  beforeEach(() => {
    post.mockReset();
  });

  it('posts to the transfer endpoint for the new admin without an error toast', async () => {
    post.mockResolvedValue({ data: {} });

    await UsersApi.transferAdmin('507f1f77bcf86cd799439011');

    expect(post).toHaveBeenCalledWith(
      '/api/v1/users/507f1f77bcf86cd799439011/transfer-admin',
      undefined,
      { suppressErrorToast: true },
    );
  });
});

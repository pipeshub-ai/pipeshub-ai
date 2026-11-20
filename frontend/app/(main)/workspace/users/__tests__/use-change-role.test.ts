import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { TFunction } from 'i18next';

const updateUser = vi.hoisted(() => vi.fn());
vi.mock('../../profile/api', () => ({ ProfileApi: { updateUser } }));

import { useChangeRole } from '../use-change-role';
import type { User } from '../types';

const t = ((key: string, opts?: { defaultValue?: string } | string) =>
  typeof opts === 'string' ? opts : (opts?.defaultValue ?? key)) as unknown as TFunction;

const user = { userId: 'u1', name: 'Dave', email: 'dave@lab.test', role: 'Member' } as unknown as User;

function setup() {
  const addToast = vi.fn();
  const onChanged = vi.fn();
  const { result } = renderHook(() => useChangeRole({ addToast, t, onChanged }));
  return { change: result.current, addToast, onChanged };
}

beforeEach(() => {
  updateUser.mockReset();
  updateUser.mockResolvedValue(undefined);
});

describe('useChangeRole', () => {
  it('changes the role and offers Undo in the success toast', async () => {
    const { change, addToast, onChanged } = setup();

    await change(user, 'Admin');

    expect(updateUser).toHaveBeenCalledWith('u1', { role: 'admin' }, { suppressErrorToast: true });
    const toast = addToast.mock.calls[0][0];
    expect(toast).toMatchObject({ variant: 'success', title: 'Role updated', action: { label: 'Undo' } });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('puts the previous role back through the same call when Undo is pressed', async () => {
    const { change, addToast, onChanged } = setup();
    await change(user, 'Admin');
    updateUser.mockClear();

    addToast.mock.calls[0][0].action.onClick();

    await vi.waitFor(() => expect(addToast).toHaveBeenCalledTimes(2));
    expect(updateUser).toHaveBeenCalledWith('u1', { role: 'member' }, { suppressErrorToast: true });
    expect(addToast.mock.calls[1][0]).toMatchObject({ variant: 'success', title: 'Role restored' });
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it('restores an admin to admin, not to member', async () => {
    const { change, addToast } = setup();
    await change({ ...user, role: 'Admin' } as User, 'Member');
    updateUser.mockClear();

    addToast.mock.calls[0][0].action.onClick();

    await vi.waitFor(() => expect(updateUser).toHaveBeenCalled());
    expect(updateUser).toHaveBeenCalledWith('u1', { role: 'admin' }, { suppressErrorToast: true });
  });

  it('says so when Undo fails, and keeps the list fresh', async () => {
    const { change, addToast, onChanged } = setup();
    await change(user, 'Admin');
    updateUser.mockRejectedValueOnce(new Error('nope'));

    addToast.mock.calls[0][0].action.onClick();

    await vi.waitFor(() => expect(addToast).toHaveBeenCalledTimes(2));
    expect(addToast.mock.calls[1][0]).toMatchObject({ variant: 'error', title: 'Could not restore the role' });
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it('does nothing when the role is unchanged, and offers no Undo for a failed change', async () => {
    const { change, addToast } = setup();
    await change(user, 'Member');
    expect(updateUser).not.toHaveBeenCalled();

    updateUser.mockRejectedValueOnce(new Error('boom'));
    await expect(change(user, 'Admin')).rejects.toThrow('boom');
    expect(addToast.mock.calls[0][0]).toMatchObject({ variant: 'error' });
    expect(addToast.mock.calls[0][0].action).toBeUndefined();
  });
});

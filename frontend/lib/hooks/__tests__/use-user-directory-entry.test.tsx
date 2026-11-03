import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { clearSessionCache } from '@/lib/api/session-cache';
import type { User } from '@/app/(main)/workspace/users/types';

const getUsersByIds = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds } }));

import { useUserDirectoryEntry } from '../use-user-directory-entry';

const A = '507f1f77bcf86cd799439011';
const B = '507f1f77bcf86cd799439012';
const C = '507f1f77bcf86cd799439013';

function user(id: string, name = `User ${id.slice(-2)}`): User {
  return { id, userId: id, name, email: `${id}@acme.test`, hasLoggedIn: true, isActive: true };
}

/** Answers every lookup with the users it knows, the way the server leaves unknown ids out. */
function serverWith(...known: User[]) {
  getUsersByIds.mockImplementation(async (ids: string[]) => known.filter((u) => ids.includes(u.userId)));
}

function Name({ id }: { id: string | null | undefined }) {
  const entry = useUserDirectoryEntry(id);
  return <span data-testid={`name-${id}`}>{entry?.name ?? '—'}</span>;
}

function requestedIds(): string[][] {
  return getUsersByIds.mock.calls.map(([ids]) => ids as string[]);
}

beforeEach(() => {
  clearSessionCache();
  getUsersByIds.mockReset();
  serverWith(user(A, 'Ada Lovelace'), user(B, 'Brian Kernighan'), user(C, 'Carol Shaw'));
});
afterEach(() => cleanup());

describe('useUserDirectoryEntry: batching', () => {
  it('asks once for a user shown on several cards in one render', async () => {
    render(<>{[1, 2, 3].map((n) => <Name key={n} id={A} />)}</>);

    await waitFor(() => expect(screen.getAllByText('Ada Lovelace')).toHaveLength(3));
    expect(requestedIds()).toEqual([[A]]);
  });

  it('asks for different users rendered together in one request', async () => {
    render(<><Name id={A} /><Name id={B} /><Name id={C} /></>);

    await waitFor(() => expect(screen.getByTestId(`name-${C}`).textContent).toBe('Carol Shaw'));
    expect(requestedIds()).toEqual([[A, B, C]]);
  });

  it('sends a repeated id once and answers every card that asked for it', async () => {
    render(<><Name id={A} /><Name id={B} /><Name id={A} /></>);

    await waitFor(() => expect(screen.getAllByText('Ada Lovelace')).toHaveLength(2));
    expect(screen.getByText('Brian Kernighan')).toBeTruthy();
    expect(requestedIds()).toEqual([[A, B]]);
  });

  it('asks again, for the new id only, when a card appears in a later tick', async () => {
    const view = render(<Name id={A} />);
    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());

    view.rerender(<><Name id={A} /><Name id={B} /></>);

    await waitFor(() => expect(screen.getByText('Brian Kernighan')).toBeTruthy());
    expect(requestedIds()).toEqual([[A], [B]]);
  });

  it('does not ask again for a user it already knows, after a remount', async () => {
    const first = render(<Name id={A} />);
    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    first.unmount();

    render(<Name id={A} />);

    expect(screen.getByText('Ada Lovelace')).toBeTruthy();
    expect(requestedIds()).toEqual([[A]]);
  });

  it('remembers that an id is unknown instead of asking for it again', async () => {
    const unknown = '507f1f77bcf86cd7994390ff';
    const first = render(<><Name id={A} /><Name id={unknown} /></>);
    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    expect(screen.getByTestId(`name-${unknown}`).textContent).toBe('—');
    first.unmount();

    render(<Name id={unknown} />);
    await act(async () => {});

    expect(requestedIds()).toEqual([[A, unknown]]);
  });
});

describe('useUserDirectoryEntry: ids it should not send', () => {
  it.each([
    ['blank', ''],
    ['missing', null],
    ['too short', '507f1f77bcf86cd79943901'],
    ['not hex', 'creator-1'],
  ])('a %s id makes no request', async (_label, id) => {
    render(<><Name id={id} /><Name id={A} /></>);

    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    expect(requestedIds()).toEqual([[A]]);
  });

  it('looks an upper-case or padded id up in the form the server answers with', async () => {
    const { result } = renderHook(() => useUserDirectoryEntry(` ${A.toUpperCase()} `));

    await waitFor(() => expect(result.current?.name).toBe('Ada Lovelace'));
    expect(requestedIds()).toEqual([[A]]);
  });

  it('asks without an error toast: the card shows its own fallback', async () => {
    renderHook(() => useUserDirectoryEntry(A));

    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledWith([A], { suppressErrorToast: true }));
  });
});

describe('useUserDirectoryEntry: failures', () => {
  it('leaves every card in a failed request empty, and the next visit asks again', async () => {
    getUsersByIds.mockRejectedValueOnce(new Error('offline'));
    const first = render(<><Name id={A} /><Name id={B} /></>);
    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledTimes(1));
    await act(async () => {});
    expect(screen.getByTestId(`name-${A}`).textContent).toBe('—');
    expect(screen.getByTestId(`name-${B}`).textContent).toBe('—');
    first.unmount();

    render(<Name id={A} />);

    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    expect(requestedIds()).toEqual([[A, B], [A]]);
  });

  it('settles every card when reading the answer fails, and the next visit asks again', async () => {
    getUsersByIds.mockResolvedValueOnce(null);
    const first = render(<><Name id={A} /><Name id={B} /></>);
    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledTimes(1));
    await act(async () => {});
    first.unmount();

    render(<Name id={A} />);

    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    expect(requestedIds()).toEqual([[A, B], [A]]);
  });

  it('ignores a row without an id and still answers the others', async () => {
    getUsersByIds.mockResolvedValueOnce([{ name: 'No id' }, user(B, 'Brian Kernighan')]);
    render(<><Name id={A} /><Name id={B} /></>);

    await waitFor(() => expect(screen.getByText('Brian Kernighan')).toBeTruthy());
    expect(screen.getByTestId(`name-${A}`).textContent).toBe('—');
  });

  it('a lookup that never answers does not block the next session', async () => {
    getUsersByIds.mockImplementationOnce(() => new Promise(() => {}));
    const first = render(<Name id={A} />);
    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledTimes(1));
    first.unmount();

    act(() => clearSessionCache());
    render(<Name id={A} />);

    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    expect(requestedIds()).toEqual([[A], [A]]);
  });

  it('a failed batch does not touch users from another batch', async () => {
    const view = render(<Name id={A} />);
    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    getUsersByIds.mockRejectedValueOnce(new Error('offline'));

    view.rerender(<><Name id={A} /><Name id={B} /></>);
    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledTimes(2));
    await act(async () => {});

    expect(screen.getByText('Ada Lovelace')).toBeTruthy();
    expect(screen.getByTestId(`name-${B}`).textContent).toBe('—');
  });
});

describe('useUserDirectoryEntry: freshness', () => {
  it('after a user edit, a card that was not on screen reads the new name on its next mount', async () => {
    const first = render(<Name id={A} />);
    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    first.unmount();

    serverWith(user(A, 'Ada King'));
    revalidateSwrNamespace(SWR_NS.userDirectory);
    render(<Name id={A} />);

    await waitFor(() => expect(screen.getByText('Ada King')).toBeTruthy());
    expect(requestedIds()).toEqual([[A], [A]]);
  });

  it('after a user edit, the cards on screen refetch together', async () => {
    render(<><Name id={A} /><Name id={B} /></>);
    await waitFor(() => expect(screen.getByText('Brian Kernighan')).toBeTruthy());

    serverWith(user(A, 'Ada King'), user(B, 'Brian K'));
    act(() => revalidateSwrNamespace(SWR_NS.userDirectory));

    await waitFor(() => expect(screen.getByText('Brian K')).toBeTruthy());
    expect(screen.getByText('Ada King')).toBeTruthy();
    expect(requestedIds()).toEqual([[A, B], [A, B]]);
  });

  it('forgets every user at sign-out, so the next session asks again', async () => {
    const first = render(<Name id={A} />);
    await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeTruthy());
    first.unmount();

    clearSessionCache();
    render(<Name id={A} />);

    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledTimes(2));
  });

  it('does not show a name that arrives after sign-out', async () => {
    let answer: (users: User[]) => void = () => {};
    getUsersByIds.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
    const { result } = renderHook(() => useUserDirectoryEntry(A));
    await waitFor(() => expect(getUsersByIds).toHaveBeenCalledTimes(1));

    act(() => clearSessionCache());
    await act(async () => answer([user(A, 'Ada Lovelace')]));

    expect(result.current).toBeNull();
  });
});

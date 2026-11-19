import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type React from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  getUser: vi.fn(),
  getAvatar: vi.fn(),
  updateUser: vi.fn(),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  getPasswordStatus: vi.fn(),
}));
const addToast = vi.hoisted(() => vi.fn());

vi.mock('../../api', () => ({ ProfileApi: api }));
vi.mock('@/lib/utils/jwt', () => ({
  getUserIdFromToken: () => 'user-1',
  getUserEmailFromToken: () => 'user@example.com',
}));
vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (select: (s: { addToast: typeof addToast }) => unknown) => select({ addToast }),
}));
vi.mock('@/config', () => ({ logoutFromWorkspaceMenu: vi.fn() }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { useProfilePage } from '../use-profile-page';
import { useProfileStore } from '../../store';
import { useUserStore, type UserProfile } from '@/lib/store/user-store';
import { clearSessionCache } from '@/lib/api/session-cache';

function signedIn(patch: Partial<UserProfile> = {}) {
  useUserStore.setState({
    isInitialized: true,
    profile: {
      userId: 'user-1',
      firstName: null,
      lastName: null,
      fullName: 'A User',
      email: 'user@example.com',
      isAdmin: false,
      avatarUrl: 'data:image/png;base64,current',
      hasLoggedIn: true,
      designation: 'Engineer',
      ...patch,
    },
  });
}

function fileChange(): React.ChangeEvent<HTMLInputElement> {
  const file = new File(['x'], 'me.png', { type: 'image/png' });
  return { target: { files: [file], value: 'C:\\fakepath\\me.png' } } as unknown as React.ChangeEvent<HTMLInputElement>;
}

async function renderPage() {
  const view = renderHook(() => useProfilePage());
  await waitFor(() => expect(view.result.current.isLoading).toBe(false));
  return view;
}

beforeEach(() => {
  clearSessionCache();
  useUserStore.getState().clearProfile();
  useProfileStore.getState().reset();
  api.getPasswordStatus.mockResolvedValue({ hasPassword: true });
  api.updateUser.mockResolvedValue(undefined);
  URL.createObjectURL = vi.fn(() => 'blob:preview');
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('useProfilePage reads the signed-in user from the store', () => {
  it('makes no users/:id or users/dp request and fills the form from the store', async () => {
    signedIn({ isAdmin: true });
    const { result } = await renderPage();

    expect(result.current.form).toEqual({ fullName: 'A User', designation: 'Engineer' });
    expect(result.current.role).toBe('Admin');
    expect(result.current.avatarUrl).toBe('data:image/png;base64,current');
    expect(api.getUser).not.toHaveBeenCalled();
    expect(api.getAvatar).not.toHaveBeenCalled();
  });

  it('waits for the user store to finish loading', async () => {
    useUserStore.setState({ isInitialized: false, profile: null });
    const { result } = renderHook(() => useProfilePage());
    expect(result.current.isLoading).toBe(true);

    act(() => signedIn());
    await waitFor(() => expect(result.current.form.fullName).toBe('A User'));
    expect(result.current.isLoading).toBe(false);
  });

  it('keeps edits in progress when the store changes for another reason', async () => {
    signedIn();
    const { result } = await renderPage();
    act(() => result.current.setField('fullName', 'Typing'));

    act(() => useUserStore.getState().setAvatarUrl('data:image/png;base64,other'));

    expect(result.current.form.fullName).toBe('Typing');
  });

  it('fills the form again for a different signed-in user', async () => {
    signedIn();
    const { result } = await renderPage();

    act(() => signedIn({ userId: 'user-2', fullName: 'B User', designation: null }));

    await waitFor(() => expect(result.current.form).toEqual({ fullName: 'B User', designation: '' }));
  });
});

describe('useProfilePage when the sign-in profile read failed', () => {
  it('fetches users/:id once and fills the form and the store', async () => {
    signedIn({ fullName: null, designation: undefined });
    api.getUser.mockResolvedValue({ fullName: 'A User', designation: 'Engineer' });
    const { result } = await renderPage();

    expect(api.getUser).toHaveBeenCalledTimes(1);
    expect(result.current.form).toEqual({ fullName: 'A User', designation: 'Engineer' });
    expect(useUserStore.getState().profile?.designation).toBe('Engineer');
  });

  it('also takes the role from that read, so an admin is not shown as a member', async () => {
    signedIn({ fullName: null, designation: undefined, isAdmin: null });
    api.getUser.mockResolvedValue({ fullName: 'A User', designation: '', role: 'admin' });
    const { result } = await renderPage();

    await waitFor(() => expect(result.current.role).toBe('Admin'));
    expect(useUserStore.getState().profile?.isAdmin).toBe(true);
  });

  it('shows the load error and never offers Save when that read fails too', async () => {
    signedIn({ fullName: null, designation: undefined });
    api.getUser.mockRejectedValue(new Error('500'));
    const { result } = await renderPage();

    act(() => result.current.setField('fullName', 'Typed name'));

    expect(addToast).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'error', title: 'workspace.profile.toasts.loadError' }),
    );
    expect(result.current.isFormDirty).toBe(false);

    // A keyboard user reaching the hidden Save still sends nothing.
    await act(async () => {
      await result.current.handleSave();
    });
    expect(api.updateUser).not.toHaveBeenCalled();
  });

  it('makes no request when the designation is known to be empty', async () => {
    signedIn({ designation: null });
    const { result } = await renderPage();

    expect(api.getUser).not.toHaveBeenCalled();
    expect(result.current.form.designation).toBe('');
  });
});

describe('useProfilePage writes back to the store', () => {
  it('saving sends the name and designation (ProfileApi.updateUser updates the store)', async () => {
    signedIn();
    const { result } = await renderPage();
    act(() => {
      result.current.setField('fullName', 'New Name');
      result.current.setField('designation', 'Lead');
    });
    expect(result.current.isFormDirty).toBe(true);

    await act(async () => {
      await result.current.handleSave();
    });

    expect(api.updateUser).toHaveBeenCalledWith('user-1', { fullName: 'New Name', designation: 'Lead' });
    expect(result.current.isFormDirty).toBe(false);
  });

  it('a failed upload after an unprocessed one clears the picture instead of showing a revoked preview', async () => {
    signedIn({ avatarUrl: 'blob:earlier-preview' });
    api.uploadAvatar.mockRejectedValue(new Error('too large'));
    const { result } = await renderPage();

    await act(async () => {
      await result.current.handleAvatarChange(fileChange());
    });

    expect(result.current.avatarUrl).toBeNull();
  });

  it('an avatar upload shows the preview, then the processed picture, in the store', async () => {
    signedIn();
    let finish: (url: string) => void = () => undefined;
    api.uploadAvatar.mockReturnValue(new Promise<string>((resolve) => { finish = resolve; }));
    const { result } = await renderPage();

    let upload: Promise<void> = Promise.resolve();
    act(() => {
      upload = result.current.handleAvatarChange(fileChange());
    });
    expect(useUserStore.getState().profile?.avatarUrl).toBe('blob:preview');

    await act(async () => {
      finish('data:image/png;base64,processed');
      await upload;
    });
    expect(useUserStore.getState().profile?.avatarUrl).toBe('data:image/png;base64,processed');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });

  it('keeps the current picture and shows an error when the upload fails', async () => {
    signedIn();
    api.uploadAvatar.mockRejectedValue(new Error('too large'));
    const { result } = await renderPage();

    await act(async () => {
      await result.current.handleAvatarChange(fileChange());
    });

    expect(result.current.avatarUrl).toBe('data:image/png;base64,current');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
    expect(addToast).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'error', title: 'workspace.profile.toasts.avatarUploadError' }),
    );
  });

  it('deleting the avatar clears it in the store', async () => {
    signedIn();
    api.deleteAvatar.mockResolvedValue(undefined);
    const { result } = await renderPage();

    await act(async () => {
      await result.current.handleAvatarDelete();
    });

    expect(useUserStore.getState().profile?.avatarUrl).toBeNull();
  });
});

describe('useProfilePage password status', () => {
  it('is fetched once per session', async () => {
    signedIn();
    const first = await renderPage();
    await waitFor(() => expect(first.result.current.hasPassword).toBe(true));
    first.unmount();

    const second = await renderPage();
    expect(second.result.current.hasPassword).toBe(true);
    expect(api.getPasswordStatus).toHaveBeenCalledTimes(1);
  });

  it('stays unknown on failure and Retry asks again', async () => {
    signedIn();
    api.getPasswordStatus.mockRejectedValueOnce(new Error('500'));
    const { result } = await renderPage();
    await waitFor(() => expect(result.current.passwordStatusFailed).toBe(true));
    expect(result.current.hasPassword).toBeNull();

    act(() => result.current.retryPasswordStatus());

    await waitFor(() => expect(result.current.hasPassword).toBe(true));
    expect(result.current.passwordStatusFailed).toBe(false);
  });

  it('is fetched again after sign-out', async () => {
    signedIn();
    const first = await renderPage();
    await waitFor(() => expect(first.result.current.hasPassword).toBe(true));
    first.unmount();

    clearSessionCache();
    signedIn();
    const second = await renderPage();
    await waitFor(() => expect(second.result.current.hasPassword).toBe(true));
    expect(api.getPasswordStatus).toHaveBeenCalledTimes(2);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import type React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  getUser: vi.fn(),
  getAvatar: vi.fn(),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  getPasswordStatus: vi.fn(() => Promise.resolve({ hasPassword: true })),
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

function fileChange(): React.ChangeEvent<HTMLInputElement> {
  const file = new File(['x'], 'me.png', { type: 'image/png' });
  return { target: { files: [file], value: 'C:\\fakepath\\me.png' } } as unknown as React.ChangeEvent<HTMLInputElement>;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('useProfilePage avatar upload', () => {
  it('keeps the current picture and shows an error when the upload fails', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
    api.getUser.mockResolvedValue({ fullName: 'A User', role: 'member' });
    api.getAvatar.mockResolvedValue('blob:current');
    api.uploadAvatar.mockRejectedValue(new Error('too large'));

    const { result } = renderHook(() => useProfilePage());
    await waitFor(() => expect(result.current.avatarUrl).toBe('blob:current'));

    await act(async () => {
      await result.current.handleAvatarChange(fileChange());
    });

    expect(result.current.avatarUrl).toBe('blob:current');
    expect(addToast).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'error', title: 'workspace.profile.toasts.avatarUploadError' }),
    );
  });
});

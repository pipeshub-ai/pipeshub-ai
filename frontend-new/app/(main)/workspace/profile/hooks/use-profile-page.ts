'use client';

import React, { useRef, useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useToastStore } from '@/lib/store/toast-store';
import { useAuthStore } from '@/lib/store/auth-store';
import { useProfileStore, isProfileFormDirty } from '../store';
import { ProfileApi } from '../api';
import { getUserIdFromToken, getUserEmailFromToken } from '@/lib/utils/jwt';
import { isProcessedError } from '@/lib/api';
import { getUserGroupsForProfile } from '../../users/api';
import { GROUP_TYPES, USER_ROLES } from '../../constants';

// ========================================
// Hook
// ========================================

export function useProfilePage() {
  const addToast = useToastStore((s) => s.addToast);
  const logout = useAuthStore((s) => s.logout);
  const router = useRouter();
  const avatarInputRef = useRef<HTMLInputElement>(null);

  // ── Local state ──────────────────────────────────────────────
  const [userId, setUserId] = useState<string | null>(null);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const [changeEmailOpen, setChangeEmailOpen] = useState(false);
  const [email, setEmail] = useState<string>('');
  const [emailLoading, setEmailLoading] = useState(false);
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const [groups, setGroups] = useState<Array<{ name: string; type: string }>>([]);
  const [role, setRole] = useState<string>(USER_ROLES.MEMBER);

  // ── Store ─────────────────────────────────────────────────────
  const form = useProfileStore((s) => s.form);
  const errors = useProfileStore((s) => s.errors);
  const discardDialogOpen = useProfileStore((s) => s.discardDialogOpen);
  const isLoading = useProfileStore((s) => s.isLoading);

  const setField = useProfileStore((s) => s.setField);
  const setForm = useProfileStore((s) => s.setForm);
  const markSaved = useProfileStore((s) => s.markSaved);
  const setErrors = useProfileStore((s) => s.setErrors);
  const discardChanges = useProfileStore((s) => s.discardChanges);
  const setDiscardDialogOpen = useProfileStore((s) => s.setDiscardDialogOpen);
  const setLoading = useProfileStore((s) => s.setLoading);
  /** Subscribe to form + savedForm so Zustand re-renders after markSaved (savedForm-only updates). */
  const isFormDirty = useProfileStore((s) =>
    isProfileFormDirty(s.form, s.savedForm),
  );

  // ── Load profile on mount ─────────────────────────────────────
  useEffect(() => {
    const fetchProfile = async () => {
      const uid = getUserIdFromToken();
      setUserId(uid);

      // Email from JWT (fast), refreshed via API below
      setEmailLoading(true);
      const emailFromToken = getUserEmailFromToken();
      if (emailFromToken) {
        setEmail(emailFromToken);
        setEmailLoading(false);
      }

      if (!uid) {
        setLoading(false);
        return;
      }

      try {
        const [userData, avatarObjectUrl] = await Promise.all([
          ProfileApi.getUser(uid),
          ProfileApi.getAvatar(),
        ]);

        setForm({
          fullName: userData.fullName ?? '',
          designation: userData.designation ?? '',
        });

        if (avatarObjectUrl) setAvatarUrl(avatarObjectUrl);

        setLoading(false);

        // Fetch groups + derive role from group membership (best-effort, non-blocking)
        getUserGroupsForProfile(uid).then((allGroups) => {
          // Exclude system groups (admin, everyone) from the badge display
          const displayGroups = allGroups.filter(
            (g) => g.type !== GROUP_TYPES.EVERYONE
          );
          setGroups(displayGroups);
          // Role is derived from group membership: admin group → Admin
          const isAdmin = allGroups.some((g) => g.type === GROUP_TYPES.ADMIN);
          setRole(isAdmin ? USER_ROLES.ADMIN : USER_ROLES.MEMBER);
        });
      } catch {
        addToast({
          variant: 'error',
          title: 'Failed to load profile',
          description: 'Could not fetch your profile details',
        });
        setLoading(false);
      }
    };

    fetchProfile();
  }, []);

  // ── Form handlers ─────────────────────────────────────────────

  const validate = useCallback((): boolean => {
    const newErrors: { fullName?: string } = {};
    if (!form.fullName.trim()) {
      newErrors.fullName = 'Full name is required';
    } else if (/[<>]/.test(form.fullName)) {
      newErrors.fullName = 'Full name cannot contain HTML tags';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [form.fullName, setErrors]);

  const handleSave = useCallback(async () => {
    if (!validate() || !userId) return;
    try {
      await ProfileApi.updateUser(userId, {
        fullName: form.fullName,
        designation: form.designation,
      });
      markSaved();
      addToast({
        variant: 'success',
        title: 'Profile saved',
        description: 'Your profile details have been updated',
      });
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to save',
        description: 'Could not update your profile',
      });
    }
  }, [form, userId, validate, markSaved, addToast]);

  // ── Password change ───────────────────────────────────────────

  const handlePasswordChangeSuccess = useCallback(() => {
    addToast({
      variant: 'success',
      title: 'Password has been updated',
      description: "We'll log you out. Please login again...",
      duration: 4000,
    });
    // Give the user a moment to read the toast, then log out
    setTimeout(() => {
      logout();
      router.push('/login');
    }, 1500);
  }, [addToast, logout, router]);
  // ── Email change ───────────────────────────────────────────

  const handleEmailVerificationSent = useCallback(() => {
    addToast({
      variant: 'success',
      title: 'Email Verification Link Sent',
      description: 'You will be logged out when you verify the email', 
      duration: 5000,
    });
  }, [addToast]);
  // ── Discard handlers ─────────────────────────────────────────

  const handleDiscard = useCallback(() => {
    setDiscardDialogOpen(true);
  }, [setDiscardDialogOpen]);

  const handleDiscardConfirm = useCallback(() => {
    discardChanges();
    addToast({
      variant: 'success',
      title: 'Discarded edits',
      description: 'Your profile has been reset',
    });
  }, [discardChanges, addToast]);

  // ── Avatar upload ─────────────────────────────────────────────

  // Avatar uploads immediately (not part of the Save form)
  const handleAvatarChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file || !userId) return;
      e.target.value = '';

      setAvatarUploading(true);
      const previewUrl = URL.createObjectURL(file);
      setAvatarUrl(previewUrl);

      try {
        const processedUrl = await ProfileApi.uploadAvatar(file);
        URL.revokeObjectURL(previewUrl);
        if (processedUrl) setAvatarUrl(processedUrl);
        addToast({
          variant: 'success',
          title: 'Profile picture saved',
          description: 'Your profile picture has been added',
        });
      } catch (err: unknown) {
        URL.revokeObjectURL(previewUrl);
        setAvatarUrl(null);
        const errMessage = isProcessedError(err) ? err.message : undefined;
        addToast({
          variant: 'error',
          title: 'Upload failed',
          description: errMessage || 'Could not upload profile picture',
        });
      } finally {
        setAvatarUploading(false);
      }
    },
    [userId, addToast]
  );

  // ── Avatar delete ──────────────────────────────────────────────

  const handleAvatarDelete = useCallback(async () => {
    if (!userId) return;
    setAvatarUploading(true);
    try {
      await ProfileApi.deleteAvatar();
      setAvatarUrl(null);
      addToast({
        variant: 'success',
        title: 'Profile picture removed',
        description: 'Your profile picture has been removed',
      });
    } catch (err: unknown) {
      const errMessage = isProcessedError(err) ? err.message : undefined;
      addToast({
        variant: 'error',
        title: 'Remove failed',
        description: errMessage || 'Could not remove profile picture',
      });
    } finally {
      setAvatarUploading(false);
    }
  }, [userId, addToast]);

  // ── Computed ──────────────────────────────────────────────────

  const avatarInitial = form.fullName
    ? form.fullName.charAt(0).toUpperCase()
    : email
      ? email.charAt(0).toUpperCase()
      : 'U';

  return {
    // Refs
    avatarInputRef,
    // State
    userId,
    changePasswordOpen,
    setChangePasswordOpen,
    changeEmailOpen,
    setChangeEmailOpen,
    email,
    emailLoading,
    avatarUrl,
    avatarUploading,
    avatarInitial,
    groups,
    role,
    // Store state
    form,
    errors,
    discardDialogOpen,
    isLoading,
    setField,
    setErrors,
    setDiscardDialogOpen,
    isFormDirty,
    // Handlers
    handleSave,
    handlePasswordChangeSuccess,
    handleEmailVerificationSent,
    handleDiscard,
    handleDiscardConfirm,
    handleAvatarChange,
    handleAvatarDelete,
  };
}

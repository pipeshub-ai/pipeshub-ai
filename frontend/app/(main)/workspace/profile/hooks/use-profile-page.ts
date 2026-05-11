'use client';

import React, { useRef, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { useToastStore } from '@/lib/store/toast-store';
import { useAuthStore } from '@/lib/store/auth-store';
import { useProfileStore, isProfileFormDirty } from '../store';
import { ProfileApi } from '../api';
import { getUserIdFromToken, getUserEmailFromToken } from '@/lib/utils/jwt';
import { isProcessedError } from '@/lib/api';
import { getUserGroupsForProfile } from '../../users/api';
import { USER_ROLES } from '../../constants';
import { GroupType } from '../../groups/types';

// ========================================
// Hook
// ========================================

export function useProfilePage() {
  const { t } = useTranslation();
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

      // Get email from JWT
      setEmailLoading(true);
      const emailFromToken = getUserEmailFromToken();
      if (emailFromToken) {
        setEmail(emailFromToken);
      }
      setEmailLoading(false);

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
            (g) => g.type !== GroupType.EVERYONE
          );
          setGroups(displayGroups);
          // Role is derived from group membership: admin group → Admin
          const isAdmin = allGroups.some((g) => g.type === GroupType.ADMIN);
          setRole(isAdmin ? USER_ROLES.ADMIN : USER_ROLES.MEMBER);
        });
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.profile.toasts.loadError'),
          description: t('workspace.profile.toasts.loadErrorDescription'),
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
      newErrors.fullName = t('form.required');
    } else if (/[<>]/.test(form.fullName)) {
      newErrors.fullName = t('workspace.profile.errors.nameHtml');
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
        title: t('workspace.profile.toasts.saveSuccess'),
        description: t('workspace.profile.toasts.saveSuccessDescription'),
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.profile.toasts.saveError'),
        description: t('workspace.profile.toasts.saveErrorDescription'),
      });
    }
  }, [form, userId, validate, markSaved, addToast]);

  // ── Password change ───────────────────────────────────────────

  const handlePasswordChangeSuccess = useCallback(() => {
    addToast({
      variant: 'success',
      title: t('workspace.profile.toasts.passwordUpdated'),
      description: t('workspace.profile.toasts.passwordUpdatedDescription'),
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
      title: t('workspace.profile.toasts.emailVerificationSent'),
      description: t('workspace.profile.toasts.emailVerificationSentDescription'),
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
      title: t('workspace.profile.toasts.discardSuccess'),
      description: t('workspace.profile.toasts.discardSuccessDescription'),
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
          title: t('workspace.profile.toasts.avatarSaved'),
          description: t('workspace.profile.toasts.avatarSavedDescription'),
        });
      } catch (err: unknown) {
        URL.revokeObjectURL(previewUrl);
        setAvatarUrl(null);
        const errMessage = isProcessedError(err) ? err.message : undefined;
        addToast({
          variant: 'error',
          title: t('workspace.profile.toasts.avatarUploadError'),
          description: errMessage || t('workspace.profile.toasts.avatarUploadErrorDescription'),
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
        title: t('workspace.profile.toasts.avatarRemoved'),
        description: t('workspace.profile.toasts.avatarRemovedDescription'),
      });
    } catch (err: unknown) {
      const errMessage = isProcessedError(err) ? err.message : undefined;
      addToast({
        variant: 'error',
        title: t('workspace.profile.toasts.avatarRemoveError'),
        description: errMessage || t('workspace.profile.toasts.avatarRemoveErrorDescription'),
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

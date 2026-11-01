'use client';

import React, { useRef, useCallback, useEffect, useState } from 'react';
import useSWR from 'swr';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectProfile, selectIsProfileInitialized } from '@/lib/store/user-store';
import { logoutFromWorkspaceMenu } from '@/config';
import { useProfileStore, isProfileFormDirty } from '../store';
import { ProfileApi } from '../api';
import { getUserIdFromToken, getUserEmailFromToken } from '@/lib/utils/jwt';
import { isProcessedError } from '@/lib/api';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { USER_ROLES } from '../../constants';

const PASSWORD_STATUS_KEY = [SWR_NS.profile, 'password-status'] as const;

// ========================================
// Hook
// ========================================

export function useProfilePage() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const avatarInputRef = useRef<HTMLInputElement>(null);

  // ── Signed-in user (read once per session by the profile initializer) ──
  const profile = useUserStore(selectProfile);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const updateUserProfile = useUserStore((s) => s.updateProfile);
  const setStoreAvatarUrl = useUserStore((s) => s.setAvatarUrl);
  const avatarUrl = profile?.avatarUrl ?? null;
  const role = profile?.isAdmin ? USER_ROLES.ADMIN : USER_ROLES.MEMBER;

  // ── Local state ──────────────────────────────────────────────
  const [userId] = useState<string | null>(() => getUserIdFromToken());
  const [email] = useState<string>(() => getUserEmailFromToken() ?? '');
  /** False until the form holds the server's name and designation; Save stays hidden until then. */
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const [settingPassword, setSettingPassword] = useState(false);
  const [setPasswordDialogOpen, setSetPasswordDialogOpen] = useState(false);

  // Read once per session: both password flows end in sign-out. Stays unknown on failure:
  // guessing "has a password" would send a passwordless user into a form that asks for a
  // current password they do not have.
  const passwordStatus = useSWR(
    PASSWORD_STATUS_KEY,
    () => ProfileApi.getPasswordStatus(),
    SWR_POLICY.sessionStable,
  );
  const hasPassword = passwordStatus.data?.hasPassword ?? null;
  const passwordStatusFailed = Boolean(passwordStatus.error) && !passwordStatus.isValidating;
  const { mutate: mutatePasswordStatus } = passwordStatus;
  const retryPasswordStatus = useCallback(() => {
    void mutatePasswordStatus();
  }, [mutatePasswordStatus]);
  const [changeEmailOpen, setChangeEmailOpen] = useState(false);
  const [avatarUploading, setAvatarUploading] = useState(false);

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
  const isFormEdited = useProfileStore((s) =>
    isProfileFormDirty(s.form, s.savedForm),
  );
  // Saving before the server's values are in would overwrite the stored designation with ''.
  const isFormDirty = profileLoaded && isFormEdited;

  // ── Fill the form once per signed-in user ─────────────────────
  useEffect(() => {
    if (!isProfileInitialized) return;
    setProfileLoaded(false);
    const known = useUserStore.getState().profile;
    if (known && known.designation !== undefined) {
      setForm({ fullName: known.fullName ?? '', designation: known.designation ?? '' });
      setProfileLoaded(true);
      setLoading(false);
      return;
    }
    // The sign-in read of users/:id failed, so name and designation are unknown: ask once more.
    if (!userId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    ProfileApi.getUser(userId)
      .then((userData) => {
        if (cancelled) return;
        const fullName = userData.fullName ?? '';
        const designation = userData.designation ?? '';
        setForm({ fullName, designation });
        updateUserProfile({ fullName: fullName || null, designation: userData.designation ?? null });
        setProfileLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        addToast({
          variant: 'error',
          title: t('workspace.profile.toasts.loadError'),
          description: t('workspace.profile.toasts.loadErrorDescription'),
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isProfileInitialized, profile?.userId]);

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
      updateUserProfile({ fullName: form.fullName, designation: form.designation });
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
  }, [form, userId, validate, markSaved, updateUserProfile, addToast]);

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
      logoutFromWorkspaceMenu();
    }, 1500);
  }, [addToast]);
  const handleSetPasswordClick = useCallback(() => {
    setSetPasswordDialogOpen(true);
  }, []);

  const handleSetPasswordConfirm = useCallback(async () => {
    setSettingPassword(true);
    try {
      await ProfileApi.sendPasswordSetupEmail();
      setSetPasswordDialogOpen(false);
      addToast({
        variant: 'success',
        title: t('workspace.profile.toasts.passwordSetupEmailSent'),
        description: t('workspace.profile.toasts.passwordSetupEmailSentDescription'),
        duration: 4000,
      });
      // Log out only once the mail is on its way, so the user is never locked out without a link.
      setTimeout(() => {
        logoutFromWorkspaceMenu();
      }, 1500);
    } catch {
      setSetPasswordDialogOpen(false);
      addToast({
        variant: 'error',
        title: t('workspace.profile.toasts.passwordSetupEmailError'),
        description: t('workspace.profile.toasts.passwordSetupEmailErrorDescription'),
      });
    } finally {
      setSettingPassword(false);
    }
  }, [addToast, t]);

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
      const previousUrl = avatarUrl;
      // The store revokes a replaced blob: URL, so the preview is freed when it is swapped out.
      setStoreAvatarUrl(URL.createObjectURL(file));

      try {
        const processedUrl = await ProfileApi.uploadAvatar(file);
        if (processedUrl) setStoreAvatarUrl(processedUrl);
        addToast({
          variant: 'success',
          title: t('workspace.profile.toasts.avatarSaved'),
          description: t('workspace.profile.toasts.avatarSavedDescription'),
        });
      } catch (err: unknown) {
        setStoreAvatarUrl(previousUrl);
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
    [userId, avatarUrl, setStoreAvatarUrl, addToast, t]
  );

  // ── Avatar delete ──────────────────────────────────────────────

  const handleAvatarDelete = useCallback(async () => {
    if (!userId) return;
    setAvatarUploading(true);
    try {
      await ProfileApi.deleteAvatar();
      setStoreAvatarUrl(null);
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
  }, [userId, setStoreAvatarUrl, addToast]);

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
    hasPassword,
    passwordStatusFailed,
    retryPasswordStatus,
    settingPassword,
    setPasswordDialogOpen,
    setSetPasswordDialogOpen,
    handleSetPasswordClick,
    handleSetPasswordConfirm,
    setChangePasswordOpen,
    changeEmailOpen,
    setChangeEmailOpen,
    email,
    emailLoading: false,
    avatarUrl,
    avatarUploading,
    avatarInitial,
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

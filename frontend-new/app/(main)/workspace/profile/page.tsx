'use client';

import React from 'react';
import { Box, Flex, Text, Heading } from '@radix-ui/themes';
import { ConfirmationDialog, SettingsSaveBar } from '../components';
import {
  ChangePasswordDialog,
  ChangeEmailDialog,
  GeneralSection,
  RolesPermissionsSection,
  PasswordSecuritySection,
} from './components';
import { useProfilePage } from './hooks/use-profile-page';

// ========================================
// Main Page
// ========================================

export default function ProfilePage() {
  const {
    avatarInputRef,
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
    form,
    errors,
    discardDialogOpen,
    isLoading,
    setField,
    setErrors,
    setDiscardDialogOpen,
    isDirty,
    handleSave,
    handlePasswordChangeSuccess,
    handleEmailVerificationSent,
    handleDiscard,
    handleDiscardConfirm,
    handleAvatarChange,
  } = useProfilePage();

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <Text size="2" style={{ color: 'var(--gray-9)' }}>Loading...</Text>
      </Flex>
    );
  }

  return (
    <Box style={{ height: '100%', overflowY: 'auto', position: 'relative' }}>
      {/* Hidden avatar file input */}
      <input
        ref={avatarInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={handleAvatarChange}
      />

      {/* Page content */}
      <Box style={{ padding: '64px 100px' }}>

        {/* ── Page header ── */}
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Profile
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-10)', marginTop: 'var(--space-1)', display: 'block' }}>
            Manage all your personal profile details here
          </Text>
        </Box>

        {/* ── General section ── */}
        <Box style={{ marginBottom: 'var(--space-5)' }}>
          <GeneralSection
            avatarUrl={avatarUrl}
            avatarInitial={avatarInitial}
            avatarUploading={avatarUploading}
            onEditAvatarClick={() => avatarInputRef.current?.click()}
            fullName={form.fullName}
            fullNameError={errors.fullName}
            onFullNameChange={(value) => {
              setField('fullName', value);
              if (errors.fullName) setErrors({});
            }}
            designation={form.designation}
            onDesignationChange={(value) => setField('designation', value)}
            email={email}
            emailLoading={emailLoading}
            onEditEmailClick={() => setChangeEmailOpen(true)}
          />
        </Box>

        {/* ── Roles & Permissions section ── */}
        <Box style={{ marginBottom: 'var(--space-5)' }}>
          <RolesPermissionsSection role={role} groups={groups} />
        </Box>

        {/* ── Password & Security section ── */}
        {/* Extra bottom padding so save bar doesn't overlap last section */}
        <Box style={{ marginBottom: 80 }}>
          <PasswordSecuritySection onChangePasswordClick={() => setChangePasswordOpen(true)} />
        </Box>

      </Box>

      {/* ── Change Password Dialog ── */}
      <ChangePasswordDialog
        open={changePasswordOpen}
        onOpenChange={setChangePasswordOpen}
        onSuccess={handlePasswordChangeSuccess}
      />

      {/* ── Change Email Dialog ── */}
      <ChangeEmailDialog
        open={changeEmailOpen}
        onOpenChange={setChangeEmailOpen}
        userId={userId}
        onSuccess={handleEmailVerificationSent}
      />

      {/* ── Discard Confirmation Dialog ── */}
      <ConfirmationDialog
        open={discardDialogOpen}
        onOpenChange={setDiscardDialogOpen}
        title="Discard changes?"
        message="If you discard, your edits won't be saved"
        confirmLabel="Discard"
        cancelLabel="Continue Editing"
        confirmVariant="danger"
        onConfirm={handleDiscardConfirm}
      />

      {/* ── Settings Save Bar (visible when form has unsaved changes) ── */}
      <SettingsSaveBar
        visible={isDirty()}
        onDiscard={handleDiscard}
        onSave={handleSave}
      />
    </Box>
  );
}

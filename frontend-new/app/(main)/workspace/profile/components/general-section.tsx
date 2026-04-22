'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, TextField, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AvatarUploadWidget } from '../../components';
import { SettingsSection } from './settings-section';
import { SettingsRow } from './settings-row';

// ========================================
// Types
// ========================================

export interface GeneralSectionProps {
  avatarUrl: string | null;
  avatarInitial: string;
  avatarUploading: boolean;
  onEditAvatarClick: () => void;
  onDeleteAvatarClick: () => void;
  fullName: string;
  fullNameError?: string;
  onFullNameChange: (value: string) => void;
  designation: string;
  onDesignationChange: (value: string) => void;
  email: string;
  emailLoading: boolean;
  /** Opens the Change Email dialog */
  onEditEmailClick: () => void;
}

// ========================================
// Component
// ========================================

export function GeneralSection({
  avatarUrl,
  avatarInitial,
  avatarUploading,
  onEditAvatarClick,
  onDeleteAvatarClick,
  fullName,
  fullNameError,
  onFullNameChange,
  designation,
  onDesignationChange,
  email,
  emailLoading,
  onEditEmailClick,
}: GeneralSectionProps) {
  const { t } = useTranslation();
  return (
    <SettingsSection title={t('workspace.profile.general.title')}>

      {/* Your Display Picture */}
      <SettingsRow
        label={t('workspace.profile.general.avatarLabel')}
        description={t('workspace.profile.general.avatarDescription')}
      >
        <AvatarUploadWidget
          src={avatarUrl}
          initial={avatarInitial}
          uploading={avatarUploading}
          onEditClick={onEditAvatarClick}
          onDeleteClick={onDeleteAvatarClick}
          triggerAriaLabel={t('workspace.profile.general.editAvatarAria')}
        />
      </SettingsRow>

      {/* Full Name */}
      <SettingsRow label={t('workspace.profile.general.fullName')} description={t('workspace.profile.general.fullNameDescription')}>
        <Flex direction="column" gap="1">
          <TextField.Root
            placeholder={t('workspace.profile.general.fullNamePlaceholder')}
            value={fullName}
            onChange={(e) => onFullNameChange(e.target.value)}
            color={fullNameError ? 'red' : undefined}
          />
          {fullNameError && (
            <Text size="1" style={{ color: 'var(--red-a11)' }}>
              {fullNameError}
            </Text>
          )}
        </Flex>
      </SettingsRow>

      {/* Designation */}
      <SettingsRow label={t('workspace.profile.general.designation')} description={t('workspace.profile.general.designationDescription')}>
        <TextField.Root
          placeholder={t('workspace.profile.general.designationPlaceholder')}
          value={designation}
          onChange={(e) => onDesignationChange(e.target.value)}
        />
      </SettingsRow>

      {/* Company Email — read-only; Change Email logic ready but UI not implemented */}
      <SettingsRow
        label={t('workspace.profile.general.companyEmail')}
        description={t('workspace.profile.general.companyEmailDescription')}
      >
        <TextField.Root
          value={emailLoading ? t('common.loading') : email}
          readOnly
          style={{ color: 'var(--gray-10)' }}
        >
          <TextField.Slot side="right">
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={onEditEmailClick}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="edit" size={14} color="var(--gray-9)" />
            </IconButton>
          </TextField.Slot>
        </TextField.Root>
      </SettingsRow>

    </SettingsSection>
  );
}

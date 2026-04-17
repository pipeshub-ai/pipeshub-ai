'use client';

import React from 'react';
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
  return (
    <SettingsSection title="General">

      {/* Your Display Picture */}
      <SettingsRow
        label="Your Display Picture"
        description="Recommended size is 256px by 256px"
      >
        <AvatarUploadWidget
          src={avatarUrl}
          initial={avatarInitial}
          uploading={avatarUploading}
          onEditClick={onEditAvatarClick}
          onDeleteClick={onDeleteAvatarClick}
        />
      </SettingsRow>

      {/* Full Name */}
      <SettingsRow label="Full Name" description="Please write your full name">
        <Flex direction="column" gap="1">
          <TextField.Root
            placeholder="eg: John Doe"
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
      <SettingsRow label="Designation" description="Your job role in the company">
        <TextField.Root
          placeholder="eg: Co-Founder"
          value={designation}
          onChange={(e) => onDesignationChange(e.target.value)}
        />
      </SettingsRow>

      {/* Company Email — read-only; Change Email logic ready but UI not implemented */}
      <SettingsRow
        label="Company Email"
        description="Primary work email from the company"
      >
        <TextField.Root
          value={emailLoading ? 'Loading…' : email}
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

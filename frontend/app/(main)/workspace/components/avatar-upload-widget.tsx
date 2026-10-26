'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Avatar, IconButton, DropdownMenu } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface AvatarUploadWidgetProps {
  /** URL of the current image (null = no image) */
  src: string | null;
  /** Fallback initial character shown when no image is present */
  initial: string;
  /** Whether an upload is currently in progress */
  uploading?: boolean;
  /** Called when the user wants to upload / change the image */
  onEditClick: () => void;
  /** Called when the user wants to remove the image (dropdown shown only when defined AND src is set) */
  onDeleteClick?: () => void;
  /** Accessible name for the trigger button (covers both the plain-edit and dropdown-menu states) */
  triggerAriaLabel?: string;
}

/**
 * AvatarUploadWidget — small thumbnail (image or initial) + edit icon button.
 *
 * When an image is present and `onDeleteClick` is provided, the edit button
 * opens a dropdown with "Change" and "Remove" options. Otherwise it triggers
 * the file picker directly.
 *
 * Used by both the General settings page (company logo) and the Profile
 * settings page (user profile picture).
 */
export function AvatarUploadWidget({
  src,
  initial,
  uploading = false,
  onEditClick,
  onDeleteClick,
  triggerAriaLabel,
}: AvatarUploadWidgetProps) {
  const { t } = useTranslation();
  const showDropdown = !!src && !!onDeleteClick;

  return (
    <Flex align="center" justify="end" gap="2">
      <Avatar
        size="2"
        variant="soft"
        src={src ?? undefined}
        fallback={uploading ? '…' : initial}
        style={{ flexShrink: 0, borderRadius: 'var(--radius-2)' }}
      />

      {showDropdown ? (
        <DropdownMenu.Root>
          <DropdownMenu.Trigger>
            <IconButton
              variant="soft"
              color="gray"
              size="2"
              className="touch-hit"
              disabled={uploading}
              aria-label={triggerAriaLabel}
              style={{ cursor: uploading ? 'wait' : 'pointer' }}
            >
              <MaterialIcon name="edit" size={16} color="var(--gray-11)" />
            </IconButton>
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end" sideOffset={4}>
            <DropdownMenu.Item onClick={onEditClick}>
              <Flex align="center" gap="2">
                <MaterialIcon name="upload" size={14} color="var(--gray-11)" />
                {t('action.upload')}
              </Flex>
            </DropdownMenu.Item>
            <DropdownMenu.Item color="red" onClick={onDeleteClick}>
              <Flex align="center" gap="2">
                <MaterialIcon name="delete" size={14} color="var(--red-11)" />
                {t('action.remove')}
              </Flex>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      ) : (
        <IconButton
          variant="soft"
          color="gray"
          size="2"
          className="touch-hit"
          onClick={onEditClick}
          disabled={uploading}
          aria-label={triggerAriaLabel}
          style={{ cursor: uploading ? 'wait' : 'pointer' }}
        >
          <MaterialIcon name="edit" size={16} color="var(--gray-11)" />
        </IconButton>
      )}
    </Flex>
  );
}

'use client';

import React from 'react';
import { Flex, Avatar, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface AvatarUploadWidgetProps {
  /** URL of the current image (null = no image) */
  src: string | null;
  /** Fallback initial character shown when no image is present */
  initial: string;
  /** Whether an upload is currently in progress */
  uploading?: boolean;
  /** Called when the edit/upload button is clicked */
  onEditClick: () => void;
}

/**
 * AvatarUploadWidget — small thumbnail (image or initial) + edit icon button.
 *
 * Uses Radix's Avatar for the thumbnail (same as AvatarCell) so image
 * loading, fallback initials, and theming are handled consistently.
 *
 * Used by both the General settings page (company logo) and the Profile
 * settings page (user profile picture).
 */
export function AvatarUploadWidget({
  src,
  initial,
  uploading = false,
  onEditClick,
}: AvatarUploadWidgetProps) {
  return (
    <Flex align="center" justify="end" gap="2">
      <Avatar
        size="2"
        variant="soft"
        src={src ?? undefined}
        fallback={uploading ? '…' : initial}
        style={{ flexShrink: 0, borderRadius: 'var(--radius-2)' }}
      />

      <IconButton
        variant="soft"
        color="gray"
        size="2"
        onClick={onEditClick}
        disabled={uploading}
        style={{ cursor: uploading ? 'wait' : 'pointer' }}
      >
        <MaterialIcon name="edit" size={16} color="var(--gray-11)" />
      </IconButton>
    </Flex>
  );
}


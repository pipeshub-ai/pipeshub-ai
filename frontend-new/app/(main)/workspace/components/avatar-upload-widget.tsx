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
  /** Called when the user wants to upload / change the image */
  onEditClick: () => void;
  /** Called when the user wants to remove the image (button shown only when defined AND src is set) */
  onDeleteClick?: () => void;
}

export function AvatarUploadWidget({
  src,
  initial,
  uploading = false,
  onEditClick,
  onDeleteClick,
}: AvatarUploadWidgetProps) {
  const showDelete = !!src && !!onDeleteClick;

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

      {showDelete && (
        <IconButton
          variant="soft"
          color="red"
          size="2"
          onClick={onDeleteClick}
          disabled={uploading}
          style={{ cursor: uploading ? 'wait' : 'pointer' }}
        >
          <MaterialIcon name="delete" size={16} color="var(--red-11)" />
        </IconButton>
      )}
    </Flex>
  );
}

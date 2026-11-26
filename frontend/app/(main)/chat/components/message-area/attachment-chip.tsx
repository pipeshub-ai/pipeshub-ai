'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { FileIcon } from '@/app/components/ui/file-icon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import type { AttachmentRef } from '../../types';
import { isPastedTextAttachment } from '../../utils/paste-attachment';

interface AttachmentChipProps {
  attachment: AttachmentRef;
  onOpen: (attachment: AttachmentRef) => void;
}

const CHIP_STYLE: React.CSSProperties = {
  flexShrink: 0,
  padding: 'var(--space-1) var(--space-2)',
  borderRadius: 'var(--radius-1)',
  maxWidth: '200px',
};

const NAME_STYLE: React.CSSProperties = {
  color: 'var(--slate-11)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

/** A file sent with a message. One erased with another conversation (QA-15G) is shown as removed and opens nothing. */
export function AttachmentChip({ attachment, onOpen }: AttachmentChipProps) {
  const { t } = useTranslation();
  const isPastedText = isPastedTextAttachment(attachment);
  const name = isPastedText
    ? t('chat.attachments.pastedText', { defaultValue: 'Pasted text' })
    : attachment.recordName;

  if (attachment.removed) {
    const explanation = t('chat.attachments.removedTitle', { name });
    return (
      <Flex
        data-testid="attachment-removed-chip"
        align="center"
        gap="1"
        title={explanation}
        aria-label={explanation}
        style={{ ...CHIP_STYLE, maxWidth: '260px', border: '1px dashed var(--slate-6)', backgroundColor: 'var(--slate-a2)' }}
      >
        <MaterialIcon name="link_off" size={14} color="var(--slate-11)" />
        <Text size="1" style={{ ...NAME_STYLE, color: 'var(--slate-11)', textDecoration: 'line-through' }}>
          {name}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}>
          {t('chat.attachments.removed')}
        </Text>
      </Flex>
    );
  }

  const background = isPastedText ? 'var(--accent-a2)' : 'var(--olive-a2)';
  const hoverBackground = isPastedText ? 'var(--accent-a4)' : 'var(--olive-a4)';
  return (
    <Flex
      align="center"
      gap="1"
      role="button"
      tabIndex={0}
      title={name}
      onClick={() => onOpen(attachment)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          // Space would otherwise scroll the transcript.
          e.preventDefault();
          onOpen(attachment);
        }
      }}
      style={{
        ...CHIP_STYLE,
        backgroundColor: background,
        border: isPastedText ? '1px dashed var(--accent-a7)' : '1px solid var(--olive-3)',
        cursor: 'pointer',
        transition: 'background-color 0.15s',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.backgroundColor = hoverBackground;
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.backgroundColor = background;
      }}
    >
      {isPastedText ? (
        <MaterialIcon name="content_paste" size={14} color="var(--accent-11)" />
      ) : (
        <FileIcon
          extension={getMimeTypeExtension(attachment.mimeType) || attachment.extension || undefined}
          filename={attachment.recordName}
          size={14}
          fallbackIcon="insert_drive_file"
        />
      )}
      <Text size="1" style={NAME_STYLE}>
        {name}
      </Text>
    </Flex>
  );
}

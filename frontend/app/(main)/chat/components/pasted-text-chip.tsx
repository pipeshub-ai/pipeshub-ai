'use client';

import React from 'react';
import { Box, Card, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { formatPasteCount } from '../utils/paste-attachment';
import type { UploadedFile } from '../types';
import {
  ATTACHMENT_CHIP_HEIGHT,
  ATTACHMENT_CHIP_MAX_WIDTH,
  AttachmentRemoveButton,
  useRevealOnHoverOrFocus,
} from './selected-collections';

interface PastedTextChipProps {
  file: UploadedFile;
  onPreview: () => void;
  onRemove: () => void;
  onRetry: () => void;
}

/**
 * Composer chip for a large clipboard paste turned into a text attachment (`file.source === 'paste-text'`):
 * the same 28px chip as a selected source, with the paste's size, opening the reader on click.
 */
export function PastedTextChip({ file, onPreview, onRemove, onRetry }: PastedTextChipProps) {
  const { t } = useTranslation();
  const { hovered, revealed, handlers } = useRevealOnHoverOrFocus();
  const label = t('chat.attachments.pastedText', { defaultValue: 'Pasted text' });
  const metaLabel =
    file.pasteCharCount !== undefined && file.pasteLineCount !== undefined
      ? t('chat.attachments.pasteMeta', {
          defaultValue: '{{count}} lines · {{chars}} chars',
          defaultValue_one: '{{count}} line · {{chars}} chars',
          chars: formatPasteCount(file.pasteCharCount),
          count: file.pasteLineCount,
        })
      : undefined;
  const isError = file.status === 'error';
  const meta = isError
    ? file.errorMessage || t('chat.attachments.uploadFailed', { defaultValue: 'Upload failed' })
    : metaLabel;
  const accessibleName = meta
    ? t('chat.attachments.pasteChipLabel', { defaultValue: '{{label}}, {{meta}}. Open to read.', label, meta })
    : label;

  return (
    <Box
      {...handlers}
      style={{ position: 'relative', flexShrink: 0, maxWidth: ATTACHMENT_CHIP_MAX_WIDTH }}
    >
      <Flex align="center" gap="1">
        <Card asChild variant="ghost">
          <button
            type="button"
            aria-label={accessibleName}
            onClick={onPreview}
            disabled={isError}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              boxSizing: 'border-box',
              height: ATTACHMENT_CHIP_HEIGHT,
              maxWidth: '100%',
              minWidth: 0,
              margin: 0,
              paddingBlock: 0,
              paddingLeft: 'calc(var(--space-1) * 1.5)',
              paddingRight: 'calc(var(--space-2) + var(--space-1) / 2)',
              cursor: isError ? 'default' : 'pointer',
              borderRadius: 'var(--radius-4)',
              border: `1px solid ${isError ? 'var(--red-7)' : hovered ? 'var(--accent-a7)' : 'var(--border-strong)'}`,
              background: 'var(--surface-raised)',
              boxShadow: hovered && !isError ? 'var(--shadow-2)' : 'none',
              transition:
                'border-color var(--motion-micro) var(--motion-ease), box-shadow var(--motion-micro) var(--motion-ease)',
            }}
          >
            <Flex
              align="center"
              justify="center"
              aria-hidden
              style={{
                width: 'calc(var(--space-4) + var(--space-1) / 2)',
                height: 'calc(var(--space-4) + var(--space-1) / 2)',
                borderRadius: 'var(--radius-2)',
                background: 'var(--fill-muted)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="content_paste" size={12} color="var(--text-secondary)" />
            </Flex>
            <Text
              size="1"
              weight="medium"
              style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
            >
              {label}
            </Text>
            {meta && (
              <Text
                size="1"
                style={{
                  color: isError ? 'var(--red-11)' : 'var(--text-secondary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  minWidth: 0,
                }}
              >
                {meta}
              </Text>
            )}
            {file.status === 'uploading' && (
              <Spinner
                size={14}
                thickness={1.5}
                color="var(--text-secondary)"
                ariaLabel={t('chat.attachments.uploadingNamed', { defaultValue: 'Uploading pasted text' })}
              />
            )}
          </button>
        </Card>
        {isError && (
          <Tooltip content={t('chat.attachments.retry', { defaultValue: 'Retry upload' })} side="top">
            <IconButton
              variant="ghost"
              color="red"
              size="1"
              className="touch-hit"
              onClick={onRetry}
              // A 24px box (44px on touch screens via touch-hit), not Radix's trimmed ghost size.
              style={{
                margin: 0,
                flexShrink: 0,
                width: 'var(--space-5)',
                height: 'var(--space-5)',
                minWidth: 'var(--space-5)',
                boxSizing: 'border-box',
                padding: 0,
              }}
              aria-label={t('chat.attachments.retry', { defaultValue: 'Retry upload' })}
            >
              <MaterialIcon name="refresh" size={14} color="var(--red-11)" />
            </IconButton>
          </Tooltip>
        )}
      </Flex>
      <AttachmentRemoveButton
        label={t('chat.attachments.removePastedText', { defaultValue: 'Remove pasted text' })}
        onRemove={onRemove}
        visible={revealed}
      />
    </Box>
  );
}

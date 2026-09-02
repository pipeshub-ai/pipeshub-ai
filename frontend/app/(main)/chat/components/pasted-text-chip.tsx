'use client';

import React from 'react';
import { Box, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { formatPasteCount } from '../utils/paste-attachment';
import type { UploadedFile } from '../types';

const BUTTON_RESET = {
  appearance: 'none',
  background: 'none',
  border: 0,
  padding: 0,
  margin: 0,
  font: 'inherit',
  color: 'inherit',
  textAlign: 'left',
  width: '100%',
} as const;

interface PastedTextChipProps {
  file: UploadedFile;
  onPreview: () => void;
  onShowInTextField: () => void;
  onRemove: () => void;
  onRetry: () => void;
}

/**
 * Composer chip for a large clipboard paste auto-converted to a text
 * attachment (`file.source === 'paste-text'`) — visually distinct from a
 * regular file chip (dashed accent border, text-excerpt label, paste icon)
 * and exposes "Show in text field" alongside the usual preview/remove/retry.
 */
export function PastedTextChip({ file, onPreview, onShowInTextField, onRemove, onRetry }: PastedTextChipProps) {
  const { t } = useTranslation();
  const metaLabel =
    file.pasteCharCount !== undefined && file.pasteLineCount !== undefined
      ? t('chat.attachments.pasteMeta', {
          defaultValue: '{{chars}} chars · {{count}} lines',
          defaultValue_one: '{{chars}} chars · {{count}} line',
          chars: formatPasteCount(file.pasteCharCount),
          count: file.pasteLineCount,
        })
      : undefined;

  return (
    <Box
      style={{
        flexShrink: 0,
        width: '196px',
        padding: 'var(--space-2)',
        backgroundColor: 'var(--accent-a2)',
        border:
          file.status === 'error' ? '1px solid var(--red-7)' : '1px dashed var(--accent-a7)',
        borderRadius: 'var(--radius-1)',
      }}
    >
      <Flex direction="column" gap="2">
        <Flex align="center" justify="between">
          <Flex align="center" gap="1" style={{ overflow: 'hidden' }}>
            <MaterialIcon name="content_paste" size={16} color="var(--accent-9)" />
            <Text
              size="1"
              style={{ color: 'var(--accent-11)', textTransform: 'uppercase', letterSpacing: '0.02em' }}
            >
              {t('chat.attachments.pastedText', { defaultValue: 'Pasted text' })}
            </Text>
          </Flex>
          {file.status === 'uploading' ? (
            <Tooltip content={t('chat.attachments.uploading', { defaultValue: 'Uploading…' })} side="top">
              <Box
                aria-label={t('chat.attachments.uploadingNamed', { defaultValue: 'Uploading pasted text' })}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20, flexShrink: 0 }}
              >
                <Spinner size={14} thickness={1.5} color="var(--slate-11)" />
              </Box>
            </Tooltip>
          ) : (
            <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
              {file.status === 'error' && (
                <Tooltip content={t('chat.attachments.retry', { defaultValue: 'Retry upload' })} side="top">
                  <IconButton
                    variant="ghost"
                    size="1"
                    onClick={onRetry}
                    style={{ margin: 0, flexShrink: 0 }}
                    aria-label={t('chat.attachments.retry', { defaultValue: 'Retry upload' })}
                  >
                    <MaterialIcon name="refresh" size={ICON_SIZES.SECONDARY} color="var(--red-11)" />
                  </IconButton>
                </Tooltip>
              )}
              <IconButton
                variant="ghost"
                size="1"
                onClick={onRemove}
                style={{ margin: 0, flexShrink: 0 }}
                aria-label={t('chat.attachments.removePastedText', { defaultValue: 'Remove pasted text' })}
              >
                <MaterialIcon name="close" size={ICON_SIZES.SECONDARY} color="var(--slate-11)" />
              </IconButton>
            </Flex>
          )}
        </Flex>

        <Flex direction="column" gap="1" asChild>
          <button
            type="button"
            onClick={onPreview}
            disabled={file.status === 'error'}
            style={{
              ...BUTTON_RESET,
              minWidth: 0,
              cursor: file.status === 'error' ? 'default' : 'pointer',
            }}
          >
            <Text
              size="1"
              weight="medium"
              style={{
                color: 'var(--slate-12)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {file.pastePreview || file.name}
            </Text>
            <Text
              size="1"
              style={{
                color: file.status === 'error' ? 'var(--red-11)' : 'var(--slate-10)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {file.status === 'error'
                ? file.errorMessage || t('chat.attachments.uploadFailed', { defaultValue: 'Upload failed' })
                : metaLabel}
            </Text>
          </button>
        </Flex>

        {file.status === 'uploaded' && (
          <Flex align="center" gap="1" asChild>
            <button
              type="button"
              onClick={onShowInTextField}
              style={{ ...BUTTON_RESET, cursor: 'pointer' }}
              aria-label={t('chat.attachments.showInTextField', { defaultValue: 'Show in text field' })}
            >
              <MaterialIcon name="text_fields" size={12} color="var(--accent-10)" />
              <Text size="1" style={{ color: 'var(--accent-10)' }}>
                {t('chat.attachments.showInTextField', { defaultValue: 'Show in text field' })}
              </Text>
            </button>
          </Flex>
        )}
      </Flex>
    </Box>
  );
}

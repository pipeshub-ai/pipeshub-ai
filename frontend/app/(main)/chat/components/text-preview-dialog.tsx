'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Box, Dialog, Flex, IconButton, Spinner, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';

const COPY_FEEDBACK_MS = 2000;

interface TextPreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** Lazily invoked once when the dialog first opens; the result is cached for the component's lifetime. */
  loadText: () => Promise<string>;
  /** When provided, shows a "Show in text field" action that hands the loaded text back to the caller. */
  onShowInTextField?: (text: string) => void;
}

/**
 * Read-only monospace viewer for a pasted-text (or uploaded .txt) attachment.
 * Used both pre-send (composer chip preview, with `onShowInTextField`) and
 * post-send (sent-message attachment chip, read-only).
 */
export function TextPreviewDialog({
  open,
  onOpenChange,
  title,
  loadText,
  onShowInTextField,
}: TextPreviewDialogProps) {
  const { t } = useTranslation();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState<boolean | null>(null);

  useEffect(() => {
    if (!open || text !== null || loading) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    loadText()
      .then((result) => {
        if (!cancelled) setText(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            (err as { message?: string })?.message ??
              t('chat.attachments.previewLoadFailed', { defaultValue: 'Failed to load content' }),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const handleCopy = useCallback(() => {
    if (text === null) return;
    navigator.clipboard
      .writeText(text)
      .then(() => setCopyFeedback(true))
      .catch(() => setCopyFeedback(false));
    setTimeout(() => setCopyFeedback(null), COPY_FEEDBACK_MS);
  }, [text]);

  const handleShowInTextField = useCallback(() => {
    if (text === null || !onShowInTextField) return;
    onShowInTextField(text);
    onOpenChange(false);
  }, [text, onShowInTextField, onOpenChange]);

  const lines = text !== null ? text.split('\n') : [];

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(28, 32, 36, 0.5)',
            zIndex: 999,
            cursor: 'pointer',
          }}
          onClick={() => onOpenChange(false)}
        />
      )}

      <Dialog.Content
        style={{
          maxWidth: '760px',
          width: '92vw',
          maxHeight: '80vh',
          padding: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          zIndex: 1000,
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{title}</Dialog.Title>
        </VisuallyHidden>

        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-3) var(--space-4)',
            borderBottom: '1px solid var(--slate-6)',
            flexShrink: 0,
          }}
        >
          <Flex align="center" gap="2" style={{ overflow: 'hidden' }}>
            <MaterialIcon name="content_paste" size={ICON_SIZES.PRIMARY} color="var(--slate-9)" />
            <Text
              size="2"
              weight="medium"
              style={{
                color: 'var(--slate-11)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {title}
            </Text>
          </Flex>

          <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
            {onShowInTextField && (
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                title={t('chat.attachments.showInTextField', { defaultValue: 'Show in text field' })}
                aria-label={t('chat.attachments.showInTextField', { defaultValue: 'Show in text field' })}
                disabled={text === null}
                style={{ cursor: text === null ? 'default' : 'pointer' }}
                onClick={handleShowInTextField}
              >
                <MaterialIcon name="text_fields" size={ICON_SIZES.SECONDARY} />
              </IconButton>
            )}
            <IconButton
              size="1"
              variant="ghost"
              color={copyFeedback === true ? 'green' : copyFeedback === false ? 'red' : 'gray'}
              title={t('chat.attachments.copyText', { defaultValue: 'Copy' })}
              aria-label={t('chat.attachments.copyText', { defaultValue: 'Copy' })}
              disabled={text === null}
              style={{ cursor: text === null ? 'default' : 'pointer' }}
              onClick={handleCopy}
            >
              <MaterialIcon
                name={copyFeedback === true ? 'check' : copyFeedback === false ? 'close' : 'content_copy'}
                size={ICON_SIZES.SECONDARY}
              />
            </IconButton>
            <Dialog.Close>
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                aria-label={t('chat.attachments.closePreview', { defaultValue: 'Close' })}
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon name="close" size={ICON_SIZES.SECONDARY} />
              </IconButton>
            </Dialog.Close>
          </Flex>
        </Flex>

        {/* Body */}
        <Box style={{ flex: 1, minHeight: 0, overflow: 'auto', backgroundColor: 'var(--slate-2)' }}>
          {loading && (
            <Flex align="center" justify="center" gap="2" style={{ padding: 'var(--space-7)' }}>
              <Spinner size="2" />
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                {t('chat.attachments.loadingPreview', { defaultValue: 'Loading…' })}
              </Text>
            </Flex>
          )}

          {!loading && error && (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-7)' }}>
              <Text size="1" style={{ color: 'var(--red-11)' }}>
                {error}
              </Text>
            </Flex>
          )}

          {!loading && !error && text !== null && (
            <Flex style={{ fontFamily: 'var(--code-font-family, monospace)', fontSize: 'var(--font-size-1)' }}>
              <pre
                aria-hidden="true"
                style={{
                  margin: 0,
                  padding: 'var(--space-3) var(--space-2)',
                  color: 'var(--slate-8)',
                  textAlign: 'right',
                  userSelect: 'none',
                  borderRight: '1px solid var(--slate-5)',
                  lineHeight: 1.6,
                }}
              >
                {lines.map((_, i) => i + 1).join('\n')}
              </pre>
              <pre
                style={{
                  margin: 0,
                  padding: 'var(--space-3) var(--space-3)',
                  color: 'var(--slate-12)',
                  overflowX: 'auto',
                  flex: 1,
                  lineHeight: 1.6,
                  whiteSpace: 'pre',
                }}
              >
                {text}
              </pre>
            </Flex>
          )}
        </Box>
      </Dialog.Content>
    </Dialog.Root>
  );
}

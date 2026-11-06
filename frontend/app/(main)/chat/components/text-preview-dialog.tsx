'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Dialog, Flex, IconButton, Spinner, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { formatPasteCount } from '../utils/paste-attachment';

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
 * Read-only reader for a pasted-text (or uploaded .txt) attachment: the whole text, wrapped and monospaced,
 * with copy and close. Used both pre-send (composer chip, with `onShowInTextField`) and post-send
 * (sent-message attachment chip, read-only). Closing hands focus back to whatever opened it.
 */
export function TextPreviewDialog({
  open,
  onOpenChange,
  title,
  loadText,
  onShowInTextField,
}: TextPreviewDialogProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState<boolean | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The element that had focus when the reader opened (its chip): focus goes back there on close.
  const openerRef = useRef<HTMLElement | null>(null);
  if (open && openerRef.current === null && typeof document !== 'undefined') {
    const active = document.activeElement;
    openerRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
  }

  useEffect(() => {
    // Closing while a load is in flight cancels it, so `loading` has to be
    // released here — the cancelled promise's `finally` deliberately skips it,
    // and a stale `true` would make the guard below refuse the next load.
    if (!open) {
      setLoading(false);
      return;
    }
    if (text !== null || loading) return;
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
    // Scheduled per outcome rather than up front: a writeText that settles
    // after COPY_FEEDBACK_MS would otherwise set an icon with no timer left to
    // clear it, stranding the check/cross until the next copy.
    const showFeedback = (ok: boolean) => {
      setCopyFeedback(ok);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopyFeedback(null), COPY_FEEDBACK_MS);
    };
    // Undefined outside a secure context, which on-prem installs served over
    // plain HTTP hit. Reading .writeText off it would throw synchronously,
    // escaping the click handler instead of reaching the red failure state.
    const { clipboard } = navigator;
    if (clipboard) {
      clipboard
        .writeText(text)
        .then(() => showFeedback(true))
        .catch(() => showFeedback(false));
    } else {
      showFeedback(false);
    }
  }, [text]);

  useEffect(
    () => () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    },
    [],
  );

  const handleShowInTextField = useCallback(() => {
    if (text === null || !onShowInTextField) return;
    onShowInTextField(text);
    onOpenChange(false);
  }, [text, onShowInTextField, onOpenChange]);

  const meta =
    text !== null
      ? t('chat.attachments.pasteMeta', {
          defaultValue: '{{count}} lines · {{chars}} chars',
          defaultValue_one: '{{count}} line · {{chars}} chars',
          count: text.split('\n').length,
          chars: formatPasteCount(text.length),
        })
      : '';
  const copyLabel =
    copyFeedback === true
      ? t('chat.attachments.copied', { defaultValue: 'Copied' })
      : t('chat.attachments.copyText', { defaultValue: 'Copy to clipboard' });
  // frontend/CLAUDE.md: touch targets are at least 44px on mobile.
  const touchTarget = isMobile ? { minWidth: 44, minHeight: 44 } : null;
  const headerButtonStyle = { margin: 0, ...touchTarget };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        onCloseAutoFocus={(event) => {
          const opener = openerRef.current;
          openerRef.current = null;
          if (opener?.isConnected) {
            event.preventDefault();
            opener.focus();
          }
        }}
        style={{
          maxWidth: 'calc(var(--space-9) * 11 + var(--space-4))',
          padding: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--surface-raised)',
        }}
      >
        {/* Header: the paste's name and size, then its actions. */}
        <Flex
          align="center"
          justify="between"
          gap="2"
          style={{
            padding: 'calc(var(--space-3) + var(--space-1) / 2) calc(var(--space-4) + var(--space-1) / 2)',
            borderBottom: '1px solid var(--border-subtle)',
            flexShrink: 0,
          }}
        >
          <Flex direction="column" style={{ minWidth: 0 }}>
            <Dialog.Title
              size="3"
              weight="bold"
              mb="0"
              // 16/24: the body line height, not the tighter heading one.
              style={{
                color: 'var(--text-primary)',
                lineHeight: 'var(--line-height-3)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {title}
            </Dialog.Title>
            <Dialog.Description size="1" style={{ color: 'var(--text-secondary)' }}>
              {meta}
            </Dialog.Description>
          </Flex>

          <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
            {onShowInTextField && (
              <IconButton
                size="2"
                variant="ghost"
                color="gray"
                aria-label={t('chat.attachments.showInTextField', { defaultValue: 'Show in text field' })}
                disabled={text === null}
                style={headerButtonStyle}
                onClick={handleShowInTextField}
              >
                <MaterialIcon name="text_fields" size={16} color={text === null ? undefined : 'var(--text-secondary)'} />
              </IconButton>
            )}
            <IconButton
              size="2"
              variant="ghost"
              color="gray"
              aria-label={copyLabel}
              disabled={text === null}
              style={headerButtonStyle}
              onClick={handleCopy}
            >
              <MaterialIcon
                name={copyFeedback === true ? 'check' : copyFeedback === false ? 'close' : 'content_copy'}
                size={16}
                color={
                  copyFeedback === true
                    ? 'var(--emerald-a11)'
                    : copyFeedback === false
                      ? 'var(--red-11)'
                      : text === null
                        ? undefined
                        : 'var(--text-secondary)'
                }
              />
            </IconButton>
            <Dialog.Close>
              <IconButton
                size="2"
                variant="ghost"
                color="gray"
                aria-label={t('chat.attachments.closePreview', { defaultValue: 'Close' })}
                style={headerButtonStyle}
              >
                <MaterialIcon name="close" size={18} color="var(--text-secondary)" />
              </IconButton>
            </Dialog.Close>
          </Flex>
        </Flex>
        {/* Announces the copy result once, politely. */}
        <VisuallyHidden>
          <span aria-live="polite">
            {copyFeedback === true
              ? t('chat.attachments.copied', { defaultValue: 'Copied' })
              : copyFeedback === false
                ? t('chat.attachments.copyFailed', { defaultValue: "Couldn't copy the text" })
                : ''}
          </span>
        </VisuallyHidden>

        {/* Body */}
        <Box
          style={{
            maxHeight: '62vh',
            minHeight: 0,
            overflowY: 'auto',
            padding: 'var(--space-4) calc(var(--space-4) + var(--space-1) / 2) calc(var(--space-5) - var(--space-1))',
          }}
        >
          {loading && (
            <Flex align="center" justify="center" gap="2" style={{ padding: 'var(--space-7)' }}>
              <Spinner size="2" />
              <Text size="1" style={{ color: 'var(--text-secondary)' }}>
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
            <pre
              style={{
                margin: 0,
                fontFamily: 'var(--code-font-family, monospace)',
                fontSize: 'calc(var(--font-size-1) + var(--space-1) / 4)',
                lineHeight: 'var(--line-height-2)',
                color: 'var(--text-primary)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {text}
            </pre>
          )}
        </Box>
      </Dialog.Content>
    </Dialog.Root>
  );
}

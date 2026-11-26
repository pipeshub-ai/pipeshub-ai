'use client';

import React, { useEffect, useId, useState } from 'react';
import { Callout, Flex, Text, TextArea } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ConfirmationDialog } from '@/workspace/components/confirmation-dialog';

/** The API keeps at most this many characters of a version's note. */
export const VERSION_NOTE_MAX = 500;

/**
 * Confirms a publish or a rollback (OWD-14) and takes the optional note kept on the version it makes. The note
 * starts empty each time the dialog opens.
 */
export function VersionNoteDialog({
  open,
  onOpenChange,
  title,
  body,
  warning,
  noteLabel,
  confirmLabel,
  busyLabel,
  busy,
  error,
  onConfirm,
  container,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  body: string;
  /** Shown above the note, e.g. that unpublished changes will be discarded. */
  warning?: string | null;
  noteLabel: string;
  confirmLabel: string;
  busyLabel: string;
  busy: boolean;
  error?: string | null;
  onConfirm: (note: string) => void;
  container?: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [note, setNote] = useState('');

  useEffect(() => {
    if (open) setNote('');
  }, [open]);

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      confirmLabel={confirmLabel}
      confirmLoadingLabel={busyLabel}
      confirmVariant="primary"
      isLoading={busy}
      onConfirm={() => onConfirm(note)}
      container={container}
      message={
        <Flex direction="column" gap="3">
          <Text size="2">{body}</Text>
          {warning ? (
            <Callout.Root color="amber" variant="surface" size="1">
              <Callout.Text weight="medium">{warning}</Callout.Text>
            </Callout.Root>
          ) : null}
          <Flex direction="column" gap="1">
            <Text as="label" htmlFor={`${id}-note`} size="1" weight="medium" color="gray">
              {noteLabel}
            </Text>
            <TextArea
              id={`${id}-note`}
              value={note}
              maxLength={VERSION_NOTE_MAX}
              placeholder={t('agentVersions.notePlaceholder')}
              onChange={(e) => setNote(e.target.value)}
              disabled={busy}
              rows={3}
            />
          </Flex>
          {error ? (
            <Callout.Root color="red" variant="surface" size="1" role="alert">
              <Callout.Text>{error}</Callout.Text>
            </Callout.Root>
          ) : null}
        </Flex>
      }
    />
  );
}

'use client';

import React, { useId, useState } from 'react';
import { Button, Dialog, Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { matchesConfirmationKeyword } from '@/lib/utils/validators';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { DialogHeading } from './dialog-heading';
import { LoadingButton } from './loading-button';
import { touchTarget } from './responsive';

export interface TypedDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void> | void;
  /** The visible question, which is also the dialog's accessible name. */
  title: string;
  /** What will be deleted; the dialog's accessible description. */
  description: React.ReactNode;
  /** A line under the description, in the same block. */
  note?: React.ReactNode;
  /** Shown between the description and the typed confirmation (a warning). */
  children?: React.ReactNode;
  /** The destructive button's text; "Delete" when omitted. */
  confirmLabel?: string;
  isDeleting?: boolean;
}

/** A delete that the person confirms by typing the delete keyword (chats, collections, folders, files). */
export function TypedDeleteDialog({
  open,
  onOpenChange,
  onConfirm,
  title,
  description,
  note,
  children,
  confirmLabel,
  isDeleting = false,
}: TypedDeleteDialogProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const confirmId = useId();
  const [confirmText, setConfirmText] = useState('');
  const deleteKeyword = t('dialog.deleteKeyword', { defaultValue: 'DELETE' });
  const isConfirmed = matchesConfirmationKeyword(confirmText, deleteKeyword);
  const buttonSize = isMobile ? '3' : '2';

  const handleOpenChange = (next: boolean) => {
    if (isDeleting) return;
    if (!next) setConfirmText('');
    onOpenChange(next);
  };

  const handleConfirm = async () => {
    if (isConfirmed && !isDeleting) await onConfirm();
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content style={{ maxWidth: '37.5rem', width: '100%', padding: 'var(--space-5)' }}>
        <Flex direction="column" gap="4">
          <DialogHeading title={title} description={description} note={note} />

          {children}

          <Flex direction="column" gap="2">
            <Text as="label" htmlFor={confirmId} size="2" weight="medium" style={{ color: 'var(--olive-11)' }}>
              {t('dialog.typeDeleteToConfirm', { keyword: deleteKeyword })}
            </Text>
            <TextField.Root
              id={confirmId}
              placeholder={deleteKeyword}
              value={confirmText}
              autoComplete="off"
              onChange={(e) => setConfirmText(e.target.value)}
              style={isConfirmed ? { borderColor: 'var(--accent-8)' } : undefined}
            />
          </Flex>

          <Flex gap="2" justify="end">
            <Button
              variant="outline"
              color="gray"
              size={buttonSize}
              onClick={() => handleOpenChange(false)}
              disabled={isDeleting}
            >
              {t('action.cancel')}
            </Button>
            <LoadingButton
              color="red"
              size={buttonSize}
              style={touchTarget(isMobile)}
              onClick={handleConfirm}
              disabled={!isConfirmed}
              loading={isDeleting}
              loadingLabel={t('action.deleting')}
            >
              {confirmLabel ?? t('action.delete')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

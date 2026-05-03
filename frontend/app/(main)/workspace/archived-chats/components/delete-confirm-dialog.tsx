'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmationDialog } from '@/workspace/components';

interface DeleteConfirmDialogProps {
  open: boolean;
  isLoading: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

/**
 * Permanent-delete confirmation dialog for archived chats.
 * Matches Figma spec: "Are you sure? / This chat will be permanently deleted..."
 */
export function DeleteConfirmDialog({
  open,
  isLoading,
  onOpenChange,
  onConfirm,
}: DeleteConfirmDialogProps) {
  const { t } = useTranslation();

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('dialog.areYouSure')}
      message={t('workspace.archivedChats.deleteConfirmMessage')}
      confirmLabel={t('action.delete')}
      cancelLabel={t('action.cancel')}
      confirmVariant="danger"
      isLoading={isLoading}
      onConfirm={onConfirm}
    />
  );
}

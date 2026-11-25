'use client';

import { useTranslation } from 'react-i18next';
import { TypedDeleteDialog } from '@/app/components/ui/typed-delete-dialog';

interface BulkDeleteConfirmationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  itemCount: number;
  isDeleting?: boolean;
}

/** Deletes the items selected in the Collections list. */
export function BulkDeleteConfirmationDialog({
  open,
  onOpenChange,
  onConfirm,
  itemCount,
  isDeleting = false,
}: BulkDeleteConfirmationDialogProps) {
  const { t } = useTranslation();
  return (
    <TypedDeleteDialog
      open={open}
      onOpenChange={onOpenChange}
      onConfirm={onConfirm}
      title={t('dialog.bulkDeleteTitle', { count: itemCount })}
      description={t('dialog.bulkDeleteBody')}
      confirmLabel={t('dialog.bulkDeleteConfirm', { count: itemCount })}
      isDeleting={isDeleting}
    />
  );
}

'use client';

import { Callout } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { TypedDeleteDialog } from '@/app/components/ui/typed-delete-dialog';

interface DeleteConfirmationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  itemName: string;
  itemType: 'KB' | 'folder' | 'record';
  warningMessage?: string;
  isDeleting?: boolean;
}

const TITLE_KEY = {
  KB: 'dialog.deleteCollectionTitle',
  folder: 'dialog.deleteFolderTitle',
  record: 'dialog.deleteFileTitle',
} as const;

const BODY_KEY = {
  KB: 'dialog.deleteCollectionBody',
  folder: 'dialog.deleteFolderBody',
  record: 'dialog.deleteFileBody',
} as const;

/** Deletes one collection, folder or file (the Collections list, its sidebar and the record view). */
export function DeleteConfirmationDialog({
  open,
  onOpenChange,
  onConfirm,
  itemName,
  itemType,
  warningMessage,
  isDeleting = false,
}: DeleteConfirmationDialogProps) {
  const { t } = useTranslation();
  return (
    <TypedDeleteDialog
      open={open}
      onOpenChange={onOpenChange}
      onConfirm={onConfirm}
      title={t(TITLE_KEY[itemType])}
      description={t(BODY_KEY[itemType], { name: itemName })}
      isDeleting={isDeleting}
    >
      {warningMessage && (
        <Callout.Root color="amber" size="1">
          <Callout.Icon>
            <MaterialIcon name="warning_amber" size={16} />
          </Callout.Icon>
          <Callout.Text size="1">{warningMessage}</Callout.Text>
        </Callout.Root>
      )}
    </TypedDeleteDialog>
  );
}

'use client';

// TODO - extract to a reusable confirmation dialog component if the use case arises elsewhere
import { useState } from 'react';
import { Dialog, Flex, Text, TextField, Button, Box, Callout, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

interface DeleteConfirmationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  itemName: string;
  itemType: 'KB' | 'folder' | 'record';
  warningMessage?: string;
  isDeleting?: boolean;
}

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
  const [confirmText, setConfirmText] = useState('');
  const isConfirmed = confirmText === 'DELETE';

  const handleConfirm = async () => {
    if (isConfirmed && !isDeleting) {
      await onConfirm();
    }
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setConfirmText(''); // Reset on close
    }
    onOpenChange(newOpen);
  };

  const getItemTypeLabel = () => {
    switch (itemType) {
      case 'KB':
        return t('itemType.knowledgeBase') + ' will be deleted';
      case 'folder':
        return t('itemType.folder') + ' will be deleted from the collection';
      case 'record':
        return t('itemType.file') + ' will be deleted from the collection';
      default:
        return t('itemType.file');
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      {/* Dark overlay matching conventions */}
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(28, 32, 36, 0.1)',
            zIndex: 999,
            cursor: 'pointer',
          }}
          onClick={() => !isDeleting && onOpenChange(false)}
        />
      )}
      <Dialog.Content
        style={{
          maxWidth: '37.5rem',
          padding: 'var(--space-5)',
          zIndex: 1000,
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>Delete Confirmation</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Text size="5" weight="bold">
              {t('dialog.areYouSure')}
            </Text>
              <Text size="2" weight="bold" style={{ color: 'var(--slate-12)', marginRight: '4px' }}>&apos;{itemName}&apos;<Text size="2" style={{ color: 'var(--slate-11)' }}> {getItemTypeLabel()} </Text></Text>
          </Flex>

          {warningMessage && (
            <Callout.Root color="amber" size="1">
              <Callout.Icon>
                <MaterialIcon name="warning_amber" size={16} />
              </Callout.Icon>
              <Callout.Text size="1">
                {warningMessage}
              </Callout.Text>
            </Callout.Root>
          )}

          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--olive-11)' }}>
              {t('dialog.typeDeleteToConfirm')}
            </Text>
            <TextField.Root
              placeholder=""
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              style={{
                ...(isConfirmed ? { borderColor: 'var(--accent-8)' } : {}),
              }}
            />
          </Flex>

          <Flex gap="2" justify="end">
            <Button
              variant="outline"
              color="gray"
              onClick={() => onOpenChange(false)}
              disabled={isDeleting}
            >
              {t('action.cancel')}
            </Button>
            <Button
              color="red"
              onClick={handleConfirm}
              disabled={!isConfirmed || isDeleting}
            >
              {isDeleting ? t('action.deleting') : t('action.delete')}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

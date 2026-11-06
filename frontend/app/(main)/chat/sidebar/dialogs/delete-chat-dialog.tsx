'use client';

import { useId, useState } from 'react';
import { Dialog, Flex, Text, TextField, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { matchesConfirmationKeyword } from '@/lib/utils/validators';
import { LoadingButton } from '@/app/components/ui/loading-button';

interface DeleteChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  chatTitle: string;
  isDeleting?: boolean;
}

/**
 * Delete confirmation dialog for a chat conversation.
 *
 * Requires typing "DELETE" to confirm. Matches Figma spec (node 2497:31109)
 * and follows the same pattern as knowledge-base DeleteConfirmationDialog.
 * The visible heading names the dialog and the explanation describes it, so a screen
 * reader announces what the sighted reader sees.
 */
export function DeleteChatDialog({
  open,
  onOpenChange,
  onConfirm,
  chatTitle,
  isDeleting = false,
}: DeleteChatDialogProps) {
  const { t } = useTranslation();
  const [confirmText, setConfirmText] = useState('');
  const deleteKeyword = t('dialog.deleteKeyword', { defaultValue: 'DELETE' });
  const isConfirmed = matchesConfirmationKeyword(confirmText, deleteKeyword);
  const confirmFieldId = useId();
  const descriptionId = useId();

  const handleConfirm = async () => {
    if (isConfirmed && !isDeleting) {
      await onConfirm();
    }
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setConfirmText('');
    }
    onOpenChange(newOpen);
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content
        aria-describedby={descriptionId}
        onInteractOutside={(e) => {
          if (isDeleting) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (isDeleting) e.preventDefault();
        }}
        style={{
          maxWidth: '37.5rem',
          width: '100%',
          padding: 'var(--space-5)',
        }}
      >
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Dialog.Title size="5" weight="bold" mb="0">
              {t('chat.deleteChatConfirm')}
            </Dialog.Title>
            {/* Both sentences describe the dialog; a Dialog.Description holds only one paragraph. */}
            <Flex id={descriptionId} direction="column" gap="1">
              <Text size="2" style={{ color: 'var(--text-secondary)' }}>
                {t('chat.deleteChatDescription')}{' '}
                <Text size="2" weight="bold" style={{ color: 'var(--text-primary)' }}>
                  &apos;{chatTitle}&apos;
                </Text>{' '}
                {t('chat.deleteChatWarning')}
              </Text>
              <Text size="2" style={{ color: 'var(--text-secondary)' }}>
                {t('chat.deleteChatArtifactsNote')}
              </Text>
            </Flex>
          </Flex>

          <Flex direction="column" gap="2">
            <Text as="label" htmlFor={confirmFieldId} size="2" weight="medium" style={{ color: 'var(--text-secondary)' }}>
              {t('dialog.typeDeleteToConfirm', { keyword: deleteKeyword })}
            </Text>
            <TextField.Root
              id={confirmFieldId}
              placeholder={deleteKeyword}
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
            <LoadingButton
              color="red"
              onClick={handleConfirm}
              disabled={!isConfirmed}
              loading={isDeleting}
              loadingLabel={t('action.deleting')}
            >
              {t('action.delete')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

'use client';

import { useState } from 'react';
import { Dialog, Flex, Text, TextField, Button, Box, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

interface DeleteAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  agentName: string;
  isDeleting?: boolean;
}

/**
 * Delete confirmation for an agent — same “type DELETE” pattern as {@link DeleteChatDialog}.
 */
export function DeleteAgentDialog({
  open,
  onOpenChange,
  onConfirm,
  agentName,
  isDeleting = false,
}: DeleteAgentDialogProps) {
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
      setConfirmText('');
    }
    onOpenChange(newOpen);
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(28, 32, 36, 0.5)',
            zIndex: 999,
            cursor: 'pointer',
          }}
          onClick={() => !isDeleting && onOpenChange(false)}
        />
      )}
      <Dialog.Content
        style={{
          maxWidth: '37.5rem',
          width: '100%',
          padding: 'var(--space-5)',
          zIndex: 1000,
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{t('chat.deleteAgentConfirm')}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Text size="5" weight="bold">
              {t('chat.deleteAgentConfirm')}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t('chat.deleteAgentDescription')}{' '}
              <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
                &apos;{agentName}&apos;
              </Text>{' '}
              {t('chat.deleteAgentWarning')}
            </Text>
          </Flex>

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
            <Button color="red" onClick={handleConfirm} disabled={!isConfirmed || isDeleting}>
              {isDeleting ? t('action.deleting') : t('action.delete')}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

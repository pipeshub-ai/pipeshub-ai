'use client';

import { Dialog, Flex, Text, Button, Box, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';

interface ArchiveChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  chatTitle: string;
  isArchiving?: boolean;
}

/**
 * Archive confirmation dialog for a chat conversation.
 *
 * Simple confirm/cancel dialog. Matches Figma spec (node 2503:28557).
 */
export function ArchiveChatDialog({
  open,
  onOpenChange,
  onConfirm,
  chatTitle,
  isArchiving = false,
}: ArchiveChatDialogProps) {
  const { t } = useTranslation();

  const handleConfirm = async () => {
    if (!isArchiving) {
      await onConfirm();
    }
  };

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
          onClick={() => !isArchiving && onOpenChange(false)}
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
          <Dialog.Title>Archive Confirmation</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Text size="5" weight="bold">
              {t('chat.archiveChatConfirm')}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t('chat.archiveChatDescription')}{' '}
              <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
                &apos;{chatTitle}&apos;
              </Text>{' '}
              {t('chat.archiveChatWarning')}
            </Text>
          </Flex>

          <Flex gap="2" justify="end">
            <Button
              variant="outline"
              color="gray"
              onClick={() => onOpenChange(false)}
              disabled={isArchiving}
            >
              {t('action.cancel')}
            </Button>
            <LoadingButton
              color="jade"
              onClick={handleConfirm}
              loading={isArchiving}
              loadingLabel={t('chat.archiving')}
            >
              {t('chat.archive')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

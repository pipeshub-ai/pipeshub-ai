'use client';

import { Dialog, Flex, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';

interface DeleteProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  isDeleting?: boolean;
}

/**
 * Confirmation dialog for deleting a project. Unlike `DeleteChatDialog`,
 * this doesn't require typing a keyword — deleting a project only unlinks
 * its chats (`ProjectService.softDelete`), it never deletes chat data.
 */
export function DeleteProjectDialog({
  open,
  onOpenChange,
  onConfirm,
  isDeleting = false,
}: DeleteProjectDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !isDeleting && onOpenChange(v)}>
      <Dialog.Content style={{ maxWidth: '26rem', width: '100%', padding: 'var(--space-5)' }}>
        <VisuallyHidden>
          <Dialog.Title>{t('chat.projects.deleteDialog.title')}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Text size="4" weight="bold" style={{ color: 'var(--olive-12)' }}>
            {t('chat.projects.deleteDialog.title')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('chat.projects.deleteDialog.description')}
          </Text>
          <Flex gap="2" justify="end">
            <LoadingButton
              type="button"
              variant="soft"
              color="gray"
              size="2"
              onClick={() => onOpenChange(false)}
              disabled={isDeleting}
            >
              {t('action.cancel')}
            </LoadingButton>
            <LoadingButton
              type="button"
              color="red"
              size="2"
              onClick={() => void onConfirm()}
              loading={isDeleting}
              loadingLabel={t('chat.projects.deleteDialog.deleting')}
            >
              {t('chat.projects.deleteDialog.confirm')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

'use client';

import { Dialog, Flex, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';

interface DeleteProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  isDeleting?: boolean;
  /** Shown under the title so the owner can see which project goes. */
  projectName?: string;
}

/**
 * Confirmation dialog for deleting a project. Its chats are unlinked, not
 * deleted, but its uploaded files (the hidden linked Collection) go with it
 * and every member loses access — the copy says so.
 */
export function DeleteProjectDialog({
  open,
  onOpenChange,
  onConfirm,
  isDeleting = false,
  projectName,
}: DeleteProjectDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !isDeleting && onOpenChange(v)}>
      <Dialog.Content style={{ maxWidth: '26rem', width: '100%', padding: 'var(--space-5)' }}>
        <VisuallyHidden>
          <Dialog.Title>{t('chat.projects.deleteDialog.title')}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Text size="4" weight="bold" style={{ color: 'var(--olive-12)' }}>
              {t('chat.projects.deleteDialog.title')}
            </Text>
            {projectName ? (
              <Text size="2" weight="medium" style={{ color: 'var(--olive-12)', overflowWrap: 'anywhere' }}>
                {projectName}
              </Text>
            ) : null}
          </Flex>
          <Text as="p" size="2" style={{ color: 'var(--slate-11)' }}>
            {t('chat.projects.deleteDialog.description')}
          </Text>
          <ul style={{ margin: 0, paddingLeft: 'var(--space-5)', color: 'var(--slate-11)' }}>
            <li>
              <Text size="2">{t('chat.projects.deleteDialog.filesDeleted')}</Text>
            </li>
            <li>
              <Text size="2">{t('chat.projects.deleteDialog.membersLoseAccess')}</Text>
            </li>
            <li>
              <Text size="2">{t('chat.projects.deleteDialog.chatsKept')}</Text>
            </li>
          </ul>
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

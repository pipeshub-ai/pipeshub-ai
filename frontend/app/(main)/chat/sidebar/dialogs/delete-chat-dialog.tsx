'use client';

import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { TypedDeleteDialog } from '@/app/components/ui/typed-delete-dialog';

interface DeleteChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  chatTitle: string;
  isDeleting?: boolean;
}

/** Deletes a chat conversation once the person types the delete keyword. */
export function DeleteChatDialog({
  open,
  onOpenChange,
  onConfirm,
  chatTitle,
  isDeleting = false,
}: DeleteChatDialogProps) {
  const { t } = useTranslation();
  return (
    <TypedDeleteDialog
      open={open}
      onOpenChange={onOpenChange}
      onConfirm={onConfirm}
      title={t('chat.deleteChatConfirm')}
      description={
        <>
          {t('chat.deleteChatDescription')}{' '}
          <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
            &apos;{chatTitle}&apos;
          </Text>{' '}
          {t('chat.deleteChatWarning')}
        </>
      }
      note={
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t('chat.deleteChatArtifactsNote')}
        </Text>
      }
      isDeleting={isDeleting}
    />
  );
}

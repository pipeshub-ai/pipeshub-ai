'use client';

import { Dialog, Flex, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';

interface LeaveChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
  isLeaving?: boolean;
}

/** Confirm step before a non-owner gives up their access to a shared chat. */
export function LeaveChatDialog({ open, onOpenChange, onConfirm, isLeaving = false }: LeaveChatDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !isLeaving && onOpenChange(next)}>
      <Dialog.Content style={{ maxWidth: '30rem', width: '100%', padding: 'var(--space-5)', zIndex: 1000 }}>
        <Flex direction="column" gap="4">
          <Flex direction="column" gap="1">
            <Dialog.Title size="5" mb="0">
              {t('chat.collab.sidebar.leaveTitle')}
            </Dialog.Title>
            <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
              {t('chat.collab.sidebar.leaveDescription')}
            </Dialog.Description>
          </Flex>
          <Flex gap="2" justify="end">
            <Button variant="outline" color="gray" onClick={() => onOpenChange(false)} disabled={isLeaving}>
              {t('action.cancel')}
            </Button>
            <LoadingButton
              color="red"
              onClick={() => void onConfirm()}
              loading={isLeaving}
              loadingLabel={t('chat.collab.sidebar.leaving')}
            >
              {t('chat.collab.sidebar.leave')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

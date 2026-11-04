'use client';

import { AlertDialog, Button, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LoadingButton } from '@/app/components/ui/loading-button';

interface LeaveProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  onConfirm: () => Promise<void>;
  isLeaving?: boolean;
}

/** Confirms a member giving up their own access to a project. */
export function LeaveProjectDialog({ open, onOpenChange, projectName, onConfirm, isLeaving = false }: LeaveProjectDialogProps) {
  const { t } = useTranslation();
  return (
    <AlertDialog.Root open={open} onOpenChange={(next) => !isLeaving && onOpenChange(next)}>
      <AlertDialog.Content style={{ maxWidth: '26rem' }}>
        <AlertDialog.Title size="4">{t('chat.projects.leaveDialog.title', { name: projectName })}</AlertDialog.Title>
        <AlertDialog.Description size="2">{t('chat.projects.leaveDialog.description')}</AlertDialog.Description>
        <Flex gap="2" justify="end" mt="4">
          <AlertDialog.Cancel>
            <Button variant="soft" color="gray" disabled={isLeaving}>
              {t('action.cancel')}
            </Button>
          </AlertDialog.Cancel>
          <LoadingButton
            color="red"
            onClick={() => void onConfirm()}
            loading={isLeaving}
            loadingLabel={t('chat.projects.leaveDialog.leaving')}
          >
            {t('chat.projects.leaveProject')}
          </LoadingButton>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
}

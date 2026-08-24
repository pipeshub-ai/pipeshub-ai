'use client';

import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { getCodeRepoConnectorsUrl } from '../../utils/reindex-label';

export interface ReindexBlockedDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connector?: string;
  nodeType?: string;
}

/** Shown instead of reindexing a GitHub / GitLab file or folder; points the user at a connector sync. */
export function ReindexBlockedDialog({
  open,
  onOpenChange,
  connector,
  nodeType,
}: ReindexBlockedDialogProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const isFolder = nodeType === 'folder';

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t(isFolder ? 'reindexBlocked.folderTitle' : 'reindexBlocked.title', {
        defaultValue: isFolder
          ? "This folder can't be reindexed on its own"
          : "This record can't be reindexed on its own",
      })}
      message={t(isFolder ? 'reindexBlocked.folderMessage' : 'reindexBlocked.message', {
        defaultValue: isFolder
          ? 'GitHub and GitLab files form one code graph for the whole repo. Reindexing a folder would update only those files and put them ahead of the rest, breaking cross-file links. Run a connector sync instead — it updates all changed files together.'
          : 'GitHub and GitLab files form one code graph for the whole repo. Reindexing a single file would put it ahead of the rest and break cross-file links. Run a connector sync instead — it updates all changed files together.',
      })}
      confirmLabel={t('reindexBlocked.goToConnectors', { defaultValue: 'Go to connectors' })}
      cancelLabel={t('common.cancel', { defaultValue: 'Cancel' })}
      confirmVariant="primary"
      onConfirm={() => {
        onOpenChange(false);
        router.push(getCodeRepoConnectorsUrl(connector));
      }}
    />
  );
}

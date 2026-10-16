'use client';

import { useTranslation } from 'react-i18next';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';

export interface ReindexBlockedDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connector?: string;
  nodeType?: string;
}

/**
 * Shown instead of reindexing a GitHub / GitLab file or folder. Points the user
 * at a repository reindex: a connector sync skips files that did not change.
 */
export function ReindexBlockedDialog({ open, onOpenChange, nodeType }: ReindexBlockedDialogProps) {
  const { t } = useTranslation();
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
          ? 'GitHub and GitLab files form one code graph for the whole repo. Reindexing a folder would update only those files and put them ahead of the rest, breaking cross-file links. To refresh it, reindex the whole repository from the knowledge hub — that re-indexes every file together. A connector sync only picks up files that changed.'
          : 'GitHub and GitLab files form one code graph for the whole repo. Reindexing a single file would put it ahead of the rest and break cross-file links. To refresh it, reindex the whole repository from the knowledge hub — that re-indexes every file together. A connector sync only picks up files that changed.',
      })}
      confirmLabel={t('common.ok', { defaultValue: 'OK' })}
      cancelLabel={t('common.close', { defaultValue: 'Close' })}
      confirmVariant="primary"
      onConfirm={() => onOpenChange(false)}
    />
  );
}

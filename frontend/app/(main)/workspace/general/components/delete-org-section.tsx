'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { DestructiveTypedConfirmationDialog } from '../../components/destructive-typed-confirmation-dialog';
import { logoutAndRedirect } from '@/config';
import { toast } from '@/lib/store/toast-store';
import { ORG_DELETE_NAME_MISMATCH, reasonCodeOf } from '@/lib/org-deletion';
import { OrgApi } from '../api';

export interface DeleteOrgSectionProps {
  /** The name people know the org by (display name, else registered name). */
  orgName: string;
  /** What the admin types: the registered name (the server checks the same). */
  confirmName: string;
}

/**
 * General › Delete organization: the typed-name delete. The organization and
 * its data are deleted at once and everyone is signed out; nothing can bring
 * it back, so the dialog says so before the name is typed.
 */
export function DeleteOrgSection({ orgName, confirmName }: DeleteOrgSectionProps) {
  const { t } = useTranslation();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const confirmDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await OrgApi.deleteOrg(confirmName);
      logoutAndRedirect();
    } catch (error) {
      setDeleting(false);
      toast.error(t('orgDeletion.dialog.failed'), {
        description:
          reasonCodeOf(error) === ORG_DELETE_NAME_MISMATCH ? t('orgDeletion.dialog.nameMismatch') : undefined,
      });
    }
  }, [confirmName, t]);

  return (
    <Flex
      direction="column"
      gap="4"
      data-testid="delete-org-section"
      style={{
        border: '1px solid var(--red-7)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        background: 'var(--olive-2)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('orgDeletion.section.title')}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 300, lineHeight: '16px' }}>
          {t('orgDeletion.section.descriptionNow', { org: orgName })}
        </Text>
      </Flex>
      <Box style={{ height: 1, backgroundColor: 'var(--red-6)', width: '100%' }} />

      <Flex
        direction={{ initial: 'column', sm: 'row' }}
        align={{ initial: 'start', sm: 'center' }}
        justify="between"
        gap="3"
        style={{ width: '100%' }}
      >
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
            {t('orgDeletion.section.deleteLabel', { org: orgName })}
          </Text>
          <Text
            size="1"
            style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300 }}
          >
            {t('orgDeletion.section.deleteNow')}
          </Text>
        </Box>
        <Button
          variant="outline"
          color="red"
          onClick={() => setDialogOpen(true)}
          disabled={confirmName === ''}
          style={{ minHeight: 36 }}
        >
          <MaterialIcon name="delete_forever" size={16} />
          {t('orgDeletion.section.deleteButton')}
        </Button>
      </Flex>

      <DestructiveTypedConfirmationDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        heading={t('orgDeletion.dialog.heading', { org: orgName })}
        headingIcon="warning"
        body={
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t('orgDeletion.dialog.deletedNow', { org: orgName })}
          </Text>
        }
        confirmationKeyword={confirmName}
        confirmInputLabel={t('orgDeletion.dialog.typeToConfirm', { name: confirmName })}
        mismatchHint={t('orgDeletion.dialog.typedMismatch')}
        primaryButtonText={t('orgDeletion.dialog.confirm', { org: orgName })}
        cancelLabel={t('orgDeletion.dialog.keep', { org: orgName })}
        confirmLoadingLabel={t('orgDeletion.dialog.deleting')}
        confirmIcon="delete_forever"
        onConfirm={() => void confirmDelete()}
        isLoading={deleting}
        phoneSheet
        maxWidth="32rem"
      />
    </Flex>
  );
}

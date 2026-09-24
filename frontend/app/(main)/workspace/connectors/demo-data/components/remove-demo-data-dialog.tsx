'use client';

import React, { useEffect, useState } from 'react';
import { AlertDialog, Button, Checkbox, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { isAxiosError } from 'axios';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectUserEmail } from '@/lib/store/user-store';
import { extractApiErrorMessage, getUserFacingErrorMessage, processError } from '@/lib/api/api-error';
import { useConnectorsStore } from '../../store';
import { useDemoDataStore } from '../store';
import { findSampleAccounts, removeDemoData, type SampleAccount } from '../remove-demo-data';

interface RemoveDemoDataDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Demo connector instances to delete. */
  connectorIds: string[];
}

function describeError(error: unknown, fallback: string): string {
  if (isAxiosError(error)) {
    const fromBody = extractApiErrorMessage(error.response?.data);
    return getUserFacingErrorMessage(fromBody ? { message: fromBody } : processError(error), fallback);
  }
  return getUserFacingErrorMessage(error, fallback);
}

/**
 * Confirms removing the Acme Corp demo data: the Demo connector with its
 * records, groups and permissions, and optionally the sample accounts.
 */
export function RemoveDemoDataDialog({ open, onOpenChange, connectorIds }: RemoveDemoDataDialogProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const myEmail = useUserStore(selectUserEmail);
  const [accounts, setAccounts] = useState<SampleAccount[] | null>(null);
  const [deleteAccounts, setDeleteAccounts] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setAccounts(null);
    setDeleteAccounts(true);
    findSampleAccounts(myEmail)
      .then((found) => {
        if (!cancelled) setAccounts(found);
      })
      .catch(() => {
        // Not offering to delete them is safe; they stay under Workspace → Users.
        if (!cancelled) setAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, myEmail]);

  const handleConfirm = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const chosen = deleteAccounts && accounts ? accounts : [];
      const { failedAccounts } = await removeDemoData(connectorIds, chosen);
      const connectorsStore = useConnectorsStore.getState();
      connectorIds.forEach((id) => connectorsStore.removeConnectorInstance(id));
      useDemoDataStore.getState().reset();
      onOpenChange(false);
      if (failedAccounts.length > 0) {
        addToast({
          variant: 'warning',
          title: t('demoData.removeDialog.partialTitle'),
          description: t('demoData.removeDialog.partialDescription', {
            emails: failedAccounts.map((a) => a.email).join(', '),
          }),
        });
      } else {
        addToast({
          variant: 'success',
          title: t('demoData.removeDialog.successTitle'),
          description: chosen.length > 0 ? t('demoData.removeDialog.successAccounts') : undefined,
          duration: 4000,
        });
      }
    } catch (error: unknown) {
      addToast({
        variant: 'error',
        title: t('demoData.removeDialog.errorTitle'),
        description: describeError(error, t('demoData.removeDialog.errorDescription')),
      });
    } finally {
      setBusy(false);
    }
  };

  const accountNames = (accounts ?? []).map((a) => a.name || a.email).join(', ');

  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next && busy) return;
        onOpenChange(next);
      }}
    >
      <AlertDialog.Content style={{ maxWidth: 480 }}>
        <AlertDialog.Title>{t('demoData.removeDialog.title')}</AlertDialog.Title>
        <AlertDialog.Description size="2">{t('demoData.removeDialog.description')}</AlertDialog.Description>

        {accounts && accounts.length > 0 && (
          <Flex direction="column" gap="1" mt="4">
            <Text as="label" size="2">
              <Flex gap="2" align="start">
                <Checkbox
                  id="remove-demo-data-accounts"
                  checked={deleteAccounts}
                  disabled={busy}
                  onCheckedChange={(v) => setDeleteAccounts(v === true)}
                  style={{ marginTop: 2 }}
                />
                {t('demoData.removeDialog.deleteAccounts', { names: accountNames })}
              </Flex>
            </Text>
            <Text size="1" color="gray" style={{ paddingLeft: 'var(--space-5)' }}>
              {t('demoData.removeDialog.deleteAccountsHint')}
            </Text>
          </Flex>
        )}

        <Flex gap="3" justify="end" mt="5">
          <AlertDialog.Cancel>
            <Button variant="soft" color="gray" disabled={busy}>
              {t('demoData.removeDialog.cancel')}
            </Button>
          </AlertDialog.Cancel>
          <Button color="red" loading={busy} disabled={accounts === null} onClick={() => void handleConfirm()}>
            {t('demoData.removeDialog.confirm')}
          </Button>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
}

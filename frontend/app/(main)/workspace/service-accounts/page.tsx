'use client';

import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { Badge, Button, Flex, Heading, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useUserStore, selectIsProfileInitialized } from '@/lib/store/user-store';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EntityEmptyState, ConfirmationDialog } from '../components';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceAccountsApi } from './api';
import type { ServiceAccount } from './types';
import { CreateServiceAccountPanel } from './components/create-service-account-panel';
import { ServiceAccountTokensPanel } from './components/service-account-tokens-panel';

function ServiceAccountsPageContent() {
  const { t } = useTranslation();
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const addToast = useToastStore((s) => s.addToast);

  const [accounts, setAccounts] = useState<ServiceAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createPanelOpen, setCreatePanelOpen] = useState(false);
  const [tokensFor, setTokensFor] = useState<ServiceAccount | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ServiceAccount | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  // Disabling is asked about first, because it stops every token the account
  // already holds, immediately. Enabling is harmless and stays one click.
  const [disableTarget, setDisableTarget] = useState<ServiceAccount | null>(null);
  const [isDisabling, setIsDisabling] = useState(false);

  const fetchAccounts = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await ServiceAccountsApi.list();
      setAccounts(data.serviceAccounts ?? []);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : t('workspace.serviceAccounts.errorGeneric')
      );
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!isProfileInitialized) return;
    void fetchAccounts();
  }, [isProfileInitialized, fetchAccounts]);

  const toggleDisabled = useCallback(
    async (account: ServiceAccount) => {
      setIsDisabling(true);
      try {
        await ServiceAccountsApi.update(account.id, {
          isDisabled: !account.isDisabled,
        });
        addToast({
          variant: 'success',
          title: account.isDisabled
            ? t('workspace.serviceAccounts.enabledTitle')
            : t('workspace.serviceAccounts.disabledTitle'),
          description: account.isDisabled
            ? undefined
            : t('workspace.serviceAccounts.disabledBody'),
          duration: 4000,
        });
        setDisableTarget(null);
        void fetchAccounts();
      } catch (err: unknown) {
        addToast({
          variant: 'error',
          title: t('workspace.serviceAccounts.updateErrorTitle'),
          description: err instanceof Error ? err.message : undefined,
          duration: 4000,
        });
      } finally {
        setIsDisabling(false);
      }
    },
    [addToast, t, fetchAccounts]
  );

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await ServiceAccountsApi.remove(deleteTarget.id);
      addToast({
        variant: 'success',
        title: t('workspace.serviceAccounts.deleteSuccessTitle'),
        duration: 3000,
      });
      setDeleteTarget(null);
      void fetchAccounts();
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.deleteErrorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 4000,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, addToast, t, fetchAccounts]);

  if (!isProfileInitialized) return null;

  const pagePaddingX = 'clamp(var(--space-4), 4vw, 40px)';
  const isEmpty = !isLoading && !error && accounts.length === 0;

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingLeft: pagePaddingX,
        paddingRight: pagePaddingX,
        boxSizing: 'border-box',
        minWidth: 0,
      }}
    >
      <Flex
        justify="between"
        align="end"
        style={{ paddingTop: '64px', paddingBottom: 'var(--space-4)' }}
      >
        <Flex direction="column" gap="1">
          <Heading size="6" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.serviceAccounts.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.serviceAccounts.subtitle')}
          </Text>
        </Flex>
        <Button
          size="2"
          style={{ cursor: 'pointer' }}
          onClick={() => setCreatePanelOpen(true)}
        >
          <MaterialIcon name="add" size={16} color="white" />
          {t('workspace.serviceAccounts.newAccount')}
        </Button>
      </Flex>

      <Flex direction="column" style={{ flex: 1, minHeight: 0, width: '100%' }}>
        {isLoading && (
          <Flex align="center" justify="center" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <LottieLoader variant="loader" size={48} showLabel />
          </Flex>
        )}

        {!isLoading && error && (
          <Flex
            direction="column"
            align="center"
            justify="center"
            gap="3"
            style={{ flex: 1, padding: 'var(--space-6)' }}
          >
            <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center' }}>
              {error}
            </Text>
            <Button size="2" variant="soft" onClick={() => void fetchAccounts()}>
              {t('workspace.serviceAccounts.retry')}
            </Button>
          </Flex>
        )}

        {isEmpty && (
          <Flex direction="column" style={{ flex: 1, minHeight: 'min(60vh, 560px)', width: '100%' }}>
            <EntityEmptyState
              icon="precision_manufacturing"
              title={t('workspace.serviceAccounts.emptyTitle')}
              description={t('workspace.serviceAccounts.emptyDescription')}
              ctaLabel={t('workspace.serviceAccounts.newAccount')}
              ctaIcon="add"
              onCtaClick={() => setCreatePanelOpen(true)}
            />
          </Flex>
        )}

        {!isLoading && !error && !isEmpty && (
          <Flex
            direction="column"
            style={{
              flex: 1,
              minHeight: 0,
              overflow: 'auto',
              paddingTop: 'var(--space-2)',
              paddingBottom: 'var(--space-4)',
            }}
          >
            <Flex
              direction="column"
              style={{
                border: '1px solid var(--olive-3)',
                borderRadius: 'var(--radius-3)',
                overflow: 'hidden',
              }}
            >
              <Flex
                style={{
                  padding: 'var(--space-3) var(--space-4)',
                  backgroundColor: 'var(--olive-2)',
                  borderBottom: '1px solid var(--olive-3)',
                }}
              >
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 2 }}>
                  {t('workspace.serviceAccounts.columnName')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 3 }}>
                  {t('workspace.serviceAccounts.columnDescription')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                  {t('workspace.serviceAccounts.columnStatus')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', width: 120, textAlign: 'right' }}>
                  {' '}
                </Text>
              </Flex>

              {accounts.map((account) => (
                <Flex
                  key={account.id}
                  align="center"
                  style={{
                    padding: 'var(--space-3) var(--space-4)',
                    borderBottom: '1px solid var(--olive-3)',
                  }}
                >
                  <Flex direction="column" gap="1" style={{ flex: 2, minWidth: 0 }}>
                    <Text size="2" weight="medium">
                      {account.fullName}
                    </Text>
                    <Text
                      size="1"
                      style={{
                        color: 'var(--slate-10)',
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      }}
                    >
                      {account.slug}
                    </Text>
                  </Flex>
                  <Text size="1" style={{ color: 'var(--slate-11)', flex: 3 }}>
                    {account.description ?? '—'}
                  </Text>
                  <Flex style={{ flex: 1 }}>
                    <Badge color={account.isDisabled ? 'gray' : 'green'} size="1">
                      {account.isDisabled
                        ? t('workspace.serviceAccounts.statusDisabled')
                        : t('workspace.serviceAccounts.statusActive')}
                    </Badge>
                  </Flex>
                  <Flex gap="1" justify="end" style={{ width: 120 }}>
                    <IconButton
                      size="2"
                      variant="ghost"
                      style={{ cursor: 'pointer' }}
                      aria-label={t('workspace.serviceAccounts.manageTokens')}
                      onClick={() => setTokensFor(account)}
                    >
                      <MaterialIcon name="key" size={16} />
                    </IconButton>
                    <IconButton
                      size="2"
                      variant="ghost"
                      style={{ cursor: 'pointer' }}
                      aria-label={
                        account.isDisabled
                          ? t('workspace.serviceAccounts.enable')
                          : t('workspace.serviceAccounts.disable')
                      }
                      onClick={() => {
                        if (account.isDisabled) {
                          void toggleDisabled(account);
                        } else {
                          setDisableTarget(account);
                        }
                      }}
                    >
                      <MaterialIcon
                        name={account.isDisabled ? 'play_arrow' : 'pause'}
                        size={16}
                      />
                    </IconButton>
                    <IconButton
                      size="2"
                      variant="ghost"
                      color="red"
                      style={{ cursor: 'pointer' }}
                      aria-label={t('workspace.serviceAccounts.delete')}
                      onClick={() => setDeleteTarget(account)}
                    >
                      <MaterialIcon name="delete" size={16} />
                    </IconButton>
                  </Flex>
                </Flex>
              ))}
            </Flex>
          </Flex>
        )}
      </Flex>

      <CreateServiceAccountPanel
        open={createPanelOpen}
        onOpenChange={setCreatePanelOpen}
        onCreated={() => void fetchAccounts()}
      />

      <ServiceAccountTokensPanel
        open={tokensFor !== null}
        onOpenChange={(open) => {
          if (!open) setTokensFor(null);
        }}
        account={tokensFor}
      />

      <ConfirmationDialog
        open={disableTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDisableTarget(null);
        }}
        title={t('workspace.serviceAccounts.disableTitle')}
        message={t('workspace.serviceAccounts.disableDescription', {
          name: disableTarget?.fullName ?? '',
        })}
        confirmLabel={t('workspace.serviceAccounts.disableConfirm')}
        cancelLabel={t('common.cancel')}
        confirmVariant="danger"
        isLoading={isDisabling}
        onConfirm={() => {
          if (disableTarget) void toggleDisabled(disableTarget);
        }}
      />

      <ConfirmationDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title={t('workspace.serviceAccounts.deleteTitle')}
        message={t('workspace.serviceAccounts.deleteDescription', {
          name: deleteTarget?.fullName ?? '',
        })}
        confirmLabel={t('workspace.serviceAccounts.delete')}
        cancelLabel={t('common.cancel')}
        confirmVariant="danger"
        isLoading={isDeleting}
        onConfirm={() => void handleDelete()}
      />
    </Flex>
  );
}

export default function ServiceAccountsPage() {
  return (
    <Suspense fallback={null}>
      <ServiceAccountsPageContent />
    </Suspense>
  );
}

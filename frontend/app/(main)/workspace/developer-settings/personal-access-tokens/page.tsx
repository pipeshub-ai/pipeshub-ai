'use client';

import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { Button, Flex, Heading, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useUserStore, selectIsProfileInitialized } from '@/lib/store/user-store';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EntityEmptyState, ConfirmationDialog } from '../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { PatApi } from './api';
import type { PatListItem } from './types';
import { CreatePatPanel } from './components/create-pat-panel';

function formatDate(value: string, locale?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

// The backend stores "never expires" as a ~100-year expiry (OAuthAccessToken.expiresAt
// is required and TTL-indexed, so there's no literal null option). Anything decades out
// is that sentinel, not a real expiry a human picked.
const NEVER_EXPIRES_THRESHOLD_YEARS = 50;

function formatExpiry(value: string, locale: string | undefined, neverLabel: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const yearsOut = (date.getTime() - Date.now()) / (365.25 * 24 * 60 * 60 * 1000);
  if (yearsOut > NEVER_EXPIRES_THRESHOLD_YEARS) return neverLabel;
  return formatDate(value, locale);
}

function PersonalAccessTokensPageContent() {
  const { t, i18n } = useTranslation();
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const addToast = useToastStore((s) => s.addToast);

  const [tokens, setTokens] = useState<PatListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createPanelOpen, setCreatePanelOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<PatListItem | null>(null);
  const [isRevoking, setIsRevoking] = useState(false);

  const fetchTokens = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await PatApi.listTokens();
      setTokens(data.tokens ?? []);
    } catch (err: unknown) {
      const message =
        err instanceof Error
          ? err.message
          : t('workspace.personalAccessTokens.errorGeneric');
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!isProfileInitialized) return;
    void fetchTokens();
  }, [isProfileInitialized, fetchTokens]);

  const handleRevoke = useCallback(async () => {
    if (!revokeTarget) return;
    setIsRevoking(true);
    try {
      await PatApi.revokeToken(revokeTarget.id);
      addToast({
        variant: 'success',
        title: t('workspace.personalAccessTokens.revoke.successTitle'),
        duration: 3000,
      });
      setRevokeTarget(null);
      void fetchTokens();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.personalAccessTokens.revoke.errorTitle'),
        duration: 4000,
      });
    } finally {
      setIsRevoking(false);
    }
  }, [revokeTarget, addToast, t, fetchTokens]);

  if (!isProfileInitialized) {
    return null;
  }

  const pagePaddingX = 'clamp(var(--space-4), 4vw, 40px)';
  const isEmpty = !isLoading && !error && tokens.length === 0;

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
            {t('workspace.personalAccessTokens.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.personalAccessTokens.subtitle')}
          </Text>
        </Flex>
        <Button
          size="2"
          style={{ cursor: 'pointer' }}
          onClick={() => setCreatePanelOpen(true)}
        >
          <MaterialIcon name="add" size={16} color="white" />
          {t('workspace.personalAccessTokens.newToken')}
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
            <Button size="2" variant="soft" onClick={() => void fetchTokens()}>
              {t('workspace.personalAccessTokens.retry')}
            </Button>
          </Flex>
        )}

        {!isLoading && !error && isEmpty && (
          <Flex
            direction="column"
            style={{ flex: 1, minHeight: 'min(60vh, 560px)', width: '100%' }}
          >
            <EntityEmptyState
              icon="vpn_key"
              title={t('workspace.personalAccessTokens.emptyTitle')}
              description={t('workspace.personalAccessTokens.emptyDescription')}
              ctaLabel={t('workspace.personalAccessTokens.newToken')}
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
                  {t('workspace.personalAccessTokens.columnName')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 2 }}>
                  {t('workspace.personalAccessTokens.columnScopes')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                  {t('workspace.personalAccessTokens.columnCreated')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                  {t('workspace.personalAccessTokens.columnExpires')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                  {t('workspace.personalAccessTokens.columnLastUsed')}
                </Text>
                <Text
                  size="1"
                  weight="medium"
                  style={{ color: 'var(--slate-11)', width: 40, textAlign: 'right' }}
                >
                  {' '}
                </Text>
              </Flex>

              {tokens.map((token) => (
                <Flex
                  key={token.id}
                  align="center"
                  style={{
                    padding: 'var(--space-3) var(--space-4)',
                    borderBottom: '1px solid var(--olive-3)',
                  }}
                >
                  <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', flex: 2 }}>
                    {token.name}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)', flex: 2 }}>
                    {token.scopes.length === 1
                      ? token.scopes[0]
                      : t('workspace.personalAccessTokens.scopeCount', {
                          count: token.scopes.length,
                        })}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)', flex: 1 }}>
                    {formatDate(token.createdAt, i18n.language)}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)', flex: 1 }}>
                    {formatExpiry(
                      token.expiresAt,
                      i18n.language,
                      t('workspace.personalAccessTokens.neverExpires'),
                    )}
                  </Text>
                  <Text size="2" style={{ color: 'var(--slate-11)', flex: 1 }}>
                    {token.lastUsedAt
                      ? formatDate(token.lastUsedAt, i18n.language)
                      : t('workspace.personalAccessTokens.neverUsed')}
                  </Text>
                  <Flex style={{ width: 40, justifyContent: 'flex-end' }}>
                    <IconButton
                      type="button"
                      variant="ghost"
                      color="red"
                      size="2"
                      aria-label={t('workspace.personalAccessTokens.revokeAria', {
                        name: token.name,
                      })}
                      onClick={() => setRevokeTarget(token)}
                      style={{ cursor: 'pointer' }}
                    >
                      <MaterialIcon name="delete" size={18} color="var(--red-11)" />
                    </IconButton>
                  </Flex>
                </Flex>
              ))}
            </Flex>
          </Flex>
        )}
      </Flex>

      <CreatePatPanel
        open={createPanelOpen}
        onOpenChange={setCreatePanelOpen}
        onCreated={() => void fetchTokens()}
      />

      <ConfirmationDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
        title={t('workspace.personalAccessTokens.revoke.confirmTitle')}
        message={t('workspace.personalAccessTokens.revoke.confirmMessage', {
          name: revokeTarget?.name ?? '',
        })}
        confirmLabel={t('workspace.personalAccessTokens.revoke.confirmButton')}
        confirmVariant="danger"
        isLoading={isRevoking}
        confirmLoadingLabel={t('workspace.personalAccessTokens.revoke.confirmLoading')}
        onConfirm={() => void handleRevoke()}
      />
    </Flex>
  );
}

export default function PersonalAccessTokensPage() {
  return (
    <Suspense fallback={null}>
      <PersonalAccessTokensPageContent />
    </Suspense>
  );
}

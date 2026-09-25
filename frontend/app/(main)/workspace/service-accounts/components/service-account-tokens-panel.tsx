'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Box,
  Button,
  Checkbox,
  Flex,
  IconButton,
  Text,
  TextField,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { CheckedState } from '@radix-ui/react-checkbox';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConfirmationDialog, FormField, WorkspaceRightPanel } from '../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceTokensApi } from '../api';
import type { ServiceAccount, ServiceToken } from '../types';

/**
 * A token within this many days of expiry is flagged, so the rotation happens
 * because someone noticed rather than because an integration broke.
 */
const EXPIRING_SOON_DAYS = 14;

const EXPIRY_OPTIONS = [30, 90, 365];
const DEFAULT_EXPIRY_DAYS = 90;

const CREDENTIAL_MONO: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  wordBreak: 'break-all',
};

function daysUntil(iso: string): number {
  const ms = new Date(iso).getTime() - Date.now();
  return Math.floor(ms / (24 * 60 * 60 * 1000));
}

function formatDate(value: string | undefined, locale?: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export interface ServiceAccountTokensPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  account: ServiceAccount | null;
}

/**
 * The tokens one service account holds, and the way to rotate them.
 *
 * Rotation works by overlap rather than by replacement: mint the new token,
 * move the integration across while both work, then revoke the old one. There
 * is no "rotate" button that does all three, because the middle step happens
 * somewhere this screen cannot see, and a button that revoked the old token
 * immediately would break the integration it was meant to protect.
 */
export function ServiceAccountTokensPanel({
  open,
  onOpenChange,
  account,
}: ServiceAccountTokensPanelProps) {
  const { t, i18n } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [tokens, setTokens] = useState<ServiceToken[]>([]);
  const [availableScopes, setAvailableScopes] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const [isMinting, setIsMinting] = useState(false);
  const [showMintForm, setShowMintForm] = useState(false);
  const [tokenName, setTokenName] = useState('');
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [expiryDays, setExpiryDays] = useState<number>(DEFAULT_EXPIRY_DAYS);
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<ServiceToken | null>(null);
  const [isRevoking, setIsRevoking] = useState(false);

  const refresh = useCallback(async () => {
    if (!account) return;
    setIsLoading(true);
    try {
      const [tokenList, scopeList] = await Promise.all([
        ServiceTokensApi.list(account.id),
        ServiceTokensApi.getScopes(),
      ]);
      setTokens(tokenList.tokens ?? []);
      setAvailableScopes(scopeList.scopes ?? []);
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.tokens.loadErrorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 4000,
      });
    } finally {
      setIsLoading(false);
    }
  }, [account, addToast, t]);

  useEffect(() => {
    if (!open) {
      setShowMintForm(false);
      setIssuedToken(null);
      setTokenName('');
      setSelectedScopes([]);
      setExpiryDays(DEFAULT_EXPIRY_DAYS);
      setCopied(false);
      setRevokeTarget(null);
      return;
    }
    void refresh();
  }, [open, refresh]);

  const toggleScope = useCallback((scope: string, checked: CheckedState) => {
    setSelectedScopes((current) =>
      checked === true
        ? [...current, scope]
        : current.filter((s) => s !== scope)
    );
  }, []);

  const handleMint = useCallback(async () => {
    if (!account) return;
    setIsMinting(true);
    try {
      const result = await ServiceTokensApi.create({
        serviceAccountId: account.id,
        name: tokenName.trim(),
        scopes: selectedScopes,
        expiryDays,
      });
      setIssuedToken(result.token.accessToken);
      setShowMintForm(false);
      setTokenName('');
      setSelectedScopes([]);
      void refresh();
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.tokens.mintErrorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 5000,
      });
    } finally {
      setIsMinting(false);
    }
  }, [account, tokenName, selectedScopes, expiryDays, refresh, addToast, t]);

  const handleRevoke = useCallback(
    async (token: ServiceToken) => {
      if (!account) return;
      setIsRevoking(true);
      try {
        await ServiceTokensApi.revoke(token.id, account.id);
        addToast({
          variant: 'success',
          title: t('workspace.serviceAccounts.tokens.revokeSuccessTitle'),
          duration: 3000,
        });
        setRevokeTarget(null);
        void refresh();
      } catch (err: unknown) {
        addToast({
          variant: 'error',
          title: t('workspace.serviceAccounts.tokens.revokeErrorTitle'),
          description: err instanceof Error ? err.message : undefined,
          duration: 4000,
        });
      } finally {
        setIsRevoking(false);
      }
    },
    [account, addToast, t, refresh]
  );

  const copyToken = useCallback(async () => {
    if (!issuedToken) return;
    try {
      await navigator.clipboard.writeText(issuedToken);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can be unavailable; the token is on screen to copy by hand.
    }
  }, [issuedToken]);

  const canMint = tokenName.trim().length > 0 && selectedScopes.length > 0;

  const expiringSoon = useMemo(
    () => tokens.filter((token) => daysUntil(token.expiresAt) <= EXPIRING_SOON_DAYS),
    [tokens]
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={account ? account.fullName : t('workspace.serviceAccounts.tokens.title')}
      icon={<MaterialIcon name="key" size={18} />}
      hideFooter
    >
      <Flex direction="column" gap="4">
        {account && (
          <Text size="1" style={{ color: 'var(--slate-10)', ...CREDENTIAL_MONO }}>
            {account.email}
          </Text>
        )}

        {issuedToken && (
          <Flex
            direction="column"
            gap="2"
            style={{
              backgroundColor: 'var(--amber-2)',
              border: '1px solid var(--amber-6)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-3)',
            }}
          >
            <Text size="2" weight="medium" style={{ color: 'var(--amber-11)' }}>
              {t('workspace.serviceAccounts.tokens.copyNowTitle')}
            </Text>
            <Text size="1" style={{ color: 'var(--amber-11)' }}>
              {t('workspace.serviceAccounts.tokens.copyNowBody')}
            </Text>
            <Flex align="center" gap="2">
              <Box
                style={{
                  ...CREDENTIAL_MONO,
                  flex: 1,
                  fontSize: 'var(--font-size-1)',
                  backgroundColor: 'var(--color-panel-solid)',
                  border: '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-2)',
                  padding: 'var(--space-2)',
                }}
              >
                {issuedToken}
              </Box>
              <IconButton
                size="2"
                variant="soft"
                style={{ cursor: 'pointer' }}
                onClick={() => void copyToken()}
                aria-label={t('workspace.serviceAccounts.tokens.copy')}
              >
                <MaterialIcon name={copied ? 'check' : 'content_copy'} size={16} />
              </IconButton>
            </Flex>
          </Flex>
        )}

        {expiringSoon.length > 0 && !issuedToken && (
          <Flex
            gap="2"
            style={{
              backgroundColor: 'var(--amber-2)',
              border: '1px solid var(--amber-6)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-3)',
            }}
          >
            <MaterialIcon name="schedule" size={16} color="var(--amber-11)" />
            <Text size="1" style={{ color: 'var(--amber-11)' }}>
              {t('workspace.serviceAccounts.tokens.expiringSoon', {
                count: expiringSoon.length,
              })}
            </Text>
          </Flex>
        )}

        {!showMintForm && (
          <Button
            size="2"
            variant="soft"
            style={{ cursor: 'pointer', alignSelf: 'flex-start' }}
            onClick={() => {
              setIssuedToken(null);
              setShowMintForm(true);
            }}
            disabled={account?.isDisabled === true}
          >
            <MaterialIcon name="add" size={16} />
            {t('workspace.serviceAccounts.tokens.newToken')}
          </Button>
        )}

        {account?.isDisabled === true && (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('workspace.serviceAccounts.tokens.disabledNote')}
          </Text>
        )}

        {showMintForm && (
          <Flex
            direction="column"
            gap="3"
            style={{
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-3)',
            }}
          >
            <FormField label={t('workspace.serviceAccounts.tokens.nameLabel')} required>
              <TextField.Root
                value={tokenName}
                placeholder={t('workspace.serviceAccounts.tokens.namePlaceholder')}
                onChange={(e) => setTokenName(e.target.value)}
              />
            </FormField>

            <FormField label={t('workspace.serviceAccounts.tokens.scopesLabel')} required>
              <Text size="1" style={{ color: 'var(--slate-10)' }}>
                {t('workspace.serviceAccounts.tokens.scopesHelp')}
              </Text>
              <Flex direction="column" gap="1" style={{ marginTop: 'var(--space-2)' }}>
                {availableScopes.map((scope) => (
                  <Flex key={scope} align="center" gap="2" asChild>
                    <label style={{ cursor: 'pointer' }}>
                      <Checkbox
                        checked={selectedScopes.includes(scope)}
                        onCheckedChange={(checked) => toggleScope(scope, checked)}
                      />
                      <Text size="1" style={CREDENTIAL_MONO}>
                        {scope}
                      </Text>
                    </label>
                  </Flex>
                ))}
              </Flex>
            </FormField>

            <FormField label={t('workspace.serviceAccounts.tokens.expiryLabel')}>
              <Flex gap="2">
                {EXPIRY_OPTIONS.map((days) => (
                  <Button
                    key={days}
                    size="1"
                    variant={expiryDays === days ? 'solid' : 'soft'}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setExpiryDays(days)}
                  >
                    {t('workspace.serviceAccounts.tokens.expiryDays', { count: days })}
                  </Button>
                ))}
              </Flex>
            </FormField>

            <Flex gap="2" justify="end">
              <Button
                size="2"
                variant="soft"
                style={{ cursor: 'pointer' }}
                onClick={() => setShowMintForm(false)}
              >
                {t('common.cancel')}
              </Button>
              <Button
                size="2"
                style={{ cursor: 'pointer' }}
                disabled={!canMint}
                loading={isMinting}
                onClick={() => void handleMint()}
              >
                {t('workspace.serviceAccounts.tokens.mint')}
              </Button>
            </Flex>
          </Flex>
        )}

        <Flex direction="column" gap="2">
          <Text size="2" weight="medium">
            {t('workspace.serviceAccounts.tokens.existing')}
          </Text>

          {isLoading && (
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.tokens.loading')}
            </Text>
          )}

          {!isLoading && tokens.length === 0 && (
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.tokens.none')}
            </Text>
          )}

          {tokens.map((token) => {
            const remaining = daysUntil(token.expiresAt);
            const soon = remaining <= EXPIRING_SOON_DAYS;
            const lastUsed = formatDate(token.lastUsedAt, i18n.language);
            return (
              <Flex
                key={token.id}
                align="center"
                justify="between"
                gap="3"
                style={{
                  border: '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-2)',
                  padding: 'var(--space-3)',
                }}
              >
                <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                  <Flex align="center" gap="2">
                    <Text size="2" weight="medium">
                      {token.name}
                    </Text>
                    {soon && (
                      <Badge color="amber" size="1">
                        {remaining < 0
                          ? t('workspace.serviceAccounts.tokens.expired')
                          : t('workspace.serviceAccounts.tokens.expiresInDays', {
                              count: remaining,
                            })}
                      </Badge>
                    )}
                  </Flex>
                  <Text size="1" style={{ color: 'var(--slate-10)', ...CREDENTIAL_MONO }}>
                    {token.scopes.join(', ')}
                  </Text>
                  <Text size="1" style={{ color: 'var(--slate-10)' }}>
                    {lastUsed
                      ? t('workspace.serviceAccounts.tokens.lastUsed', { date: lastUsed })
                      : t('workspace.serviceAccounts.tokens.neverUsed')}
                    {' · '}
                    {t('workspace.serviceAccounts.tokens.expiresOn', {
                      date: formatDate(token.expiresAt, i18n.language) ?? '',
                    })}
                  </Text>
                </Flex>
                <IconButton
                  size="2"
                  variant="ghost"
                  color="red"
                  style={{ cursor: 'pointer' }}
                  aria-label={t('workspace.serviceAccounts.tokens.revoke')}
                  onClick={() => setRevokeTarget(token)}
                >
                  <MaterialIcon name="delete" size={16} />
                </IconButton>
              </Flex>
            );
          })}
        </Flex>

        <Flex
          gap="2"
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3)',
          }}
        >
          <MaterialIcon name="autorenew" size={16} color="var(--slate-10)" />
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.serviceAccounts.tokens.rotationHelp')}
          </Text>
        </Flex>
      </Flex>

      <ConfirmationDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
        title={t('workspace.serviceAccounts.tokens.revokeTitle')}
        message={t('workspace.serviceAccounts.tokens.revokeDescription', {
          name: revokeTarget?.name ?? '',
        })}
        confirmLabel={t('workspace.serviceAccounts.tokens.revoke')}
        cancelLabel={t('common.cancel')}
        confirmVariant="danger"
        isLoading={isRevoking}
        onConfirm={() => {
          if (revokeTarget) void handleRevoke(revokeTarget);
        }}
      />
    </WorkspaceRightPanel>
  );
}

'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Box,
  Button,
  Flex,
  IconButton,
  Text,
  TextField,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConfirmationDialog, FormField, WorkspaceRightPanel } from '../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { DelegationsApi } from '../api';
import type { Delegation, DelegationEvent, ServiceAccount } from '../types';

const MONO: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  wordBreak: 'break-all',
};

/** How many log entries the panel shows. Enough to spot a pattern. */
const EVENT_LIMIT = 50;

function formatWhen(value: string, locale?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export interface ServiceAccountDelegationsPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  account: ServiceAccount | null;
}

/**
 * Who a service account may act as, and the record of every time it tried.
 *
 * The two halves belong on one screen on purpose. The allow-list is a
 * promise about what can happen; the log is what did happen. Reviewing either
 * alone is how a delegation that nobody uses stays granted for years, or a
 * credential quietly probes for people it may not reach.
 */
export function ServiceAccountDelegationsPanel({
  open,
  onOpenChange,
  account,
}: ServiceAccountDelegationsPanelProps) {
  const { t, i18n } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [delegations, setDelegations] = useState<Delegation[]>([]);
  const [events, setEvents] = useState<DelegationEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [subject, setSubject] = useState('');
  const [reason, setReason] = useState('');
  const [isGranting, setIsGranting] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<Delegation | null>(null);
  const [isRevoking, setIsRevoking] = useState(false);

  const accountId = account?.id ?? null;

  const refresh = useCallback(async () => {
    if (!accountId) return;
    setIsLoading(true);
    try {
      const [list, log] = await Promise.all([
        DelegationsApi.list(accountId),
        DelegationsApi.events(accountId, EVENT_LIMIT),
      ]);
      setDelegations(list.delegations ?? []);
      setEvents(log.events ?? []);
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.delegations.loadErrorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 4000,
      });
    } finally {
      setIsLoading(false);
    }
  }, [accountId, addToast, t]);

  useEffect(() => {
    if (!open || !accountId) return;
    setSubject('');
    setReason('');
    void refresh();
  }, [open, accountId, refresh]);

  const grant = useCallback(async () => {
    if (!accountId || subject.trim() === '') return;
    setIsGranting(true);
    try {
      await DelegationsApi.grant({
        serviceAccountId: accountId,
        subject: subject.trim(),
        reason: reason.trim() === '' ? undefined : reason.trim(),
      });
      addToast({
        variant: 'success',
        title: t('workspace.serviceAccounts.delegations.grantSuccessTitle'),
        duration: 4000,
      });
      setSubject('');
      setReason('');
      void refresh();
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.delegations.grantErrorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 6000,
      });
    } finally {
      setIsGranting(false);
    }
  }, [accountId, subject, reason, addToast, t, refresh]);

  const revoke = useCallback(async () => {
    if (!accountId || !revokeTarget) return;
    setIsRevoking(true);
    try {
      await DelegationsApi.revoke(revokeTarget.subjectUserId, accountId);
      addToast({
        variant: 'success',
        title: t('workspace.serviceAccounts.delegations.revokeSuccessTitle'),
        duration: 4000,
      });
      setRevokeTarget(null);
      void refresh();
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workspace.serviceAccounts.delegations.revokeErrorTitle'),
        description: err instanceof Error ? err.message : undefined,
        duration: 4000,
      });
    } finally {
      setIsRevoking(false);
    }
  }, [accountId, revokeTarget, addToast, t, refresh]);

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={
        account
          ? account.fullName
          : t('workspace.serviceAccounts.delegations.title')
      }
      icon={<MaterialIcon name="switch_account" size={18} />}
      hideFooter
    >
      <Flex direction="column" gap="4">
        {account && (
          <Text size="1" style={{ color: 'var(--slate-10)', ...MONO }}>
            {account.email}
          </Text>
        )}

        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.serviceAccounts.delegations.explainer')}
        </Text>

        {/* Grant */}
        <Flex
          direction="column"
          gap="3"
          style={{
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3)',
          }}
        >
          <Text size="2" weight="medium">
            {t('workspace.serviceAccounts.delegations.addTitle')}
          </Text>
          <FormField
            label={t('workspace.serviceAccounts.delegations.subjectLabel')}
            required
          >
            <TextField.Root
              value={subject}
              placeholder={t(
                'workspace.serviceAccounts.delegations.subjectPlaceholder'
              )}
              onChange={(e) => setSubject(e.target.value)}
            />
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.delegations.subjectHelp')}
            </Text>
          </FormField>
          <FormField
            label={t('workspace.serviceAccounts.delegations.reasonLabel')}
            optional
          >
            <TextField.Root
              value={reason}
              placeholder={t(
                'workspace.serviceAccounts.delegations.reasonPlaceholder'
              )}
              onChange={(e) => setReason(e.target.value)}
            />
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.delegations.reasonHelp')}
            </Text>
          </FormField>
          <Flex justify="end">
            <Button
              size="2"
              disabled={subject.trim() === '' || isGranting}
              onClick={() => void grant()}
              style={{ cursor: 'pointer' }}
            >
              {t('workspace.serviceAccounts.delegations.addConfirm')}
            </Button>
          </Flex>
        </Flex>

        {/* Allow-list */}
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium">
            {t('workspace.serviceAccounts.delegations.listTitle')}
          </Text>
          {isLoading && delegations.length === 0 ? (
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.delegations.loading')}
            </Text>
          ) : delegations.length === 0 ? (
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.delegations.none')}
            </Text>
          ) : (
            delegations.map((delegation) => (
              <Flex
                key={delegation.id}
                align="center"
                gap="2"
                style={{
                  border: '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-2)',
                  padding: 'var(--space-2) var(--space-3)',
                }}
              >
                <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
                  <Flex align="center" gap="2">
                    <Text size="2">
                      {delegation.subjectFullName ??
                        delegation.subjectEmail ??
                        delegation.subjectUserId}
                    </Text>
                    {delegation.inactive && (
                      <Badge color="amber" size="1">
                        {t('workspace.serviceAccounts.delegations.inactive')}
                      </Badge>
                    )}
                  </Flex>
                  {delegation.subjectEmail && (
                    <Text size="1" style={{ color: 'var(--slate-10)', ...MONO }}>
                      {delegation.subjectEmail}
                    </Text>
                  )}
                  {delegation.inactiveReason && (
                    <Text size="1" style={{ color: 'var(--amber-11)' }}>
                      {delegation.inactiveReason}
                    </Text>
                  )}
                  {delegation.reason && (
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {delegation.reason}
                    </Text>
                  )}
                </Flex>
                <IconButton
                  size="2"
                  variant="ghost"
                  color="red"
                  style={{ cursor: 'pointer' }}
                  aria-label={t('workspace.serviceAccounts.delegations.revoke')}
                  onClick={() => setRevokeTarget(delegation)}
                >
                  <MaterialIcon name="delete" size={16} />
                </IconButton>
              </Flex>
            ))
          )}
        </Flex>

        {/* The log */}
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium">
            {t('workspace.serviceAccounts.delegations.logTitle')}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.serviceAccounts.delegations.logHelp')}
          </Text>
          {events.length === 0 ? (
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.serviceAccounts.delegations.logNone')}
            </Text>
          ) : (
            <Box>
              {events.map((event) => (
                <Flex
                  key={event.id}
                  align="center"
                  gap="2"
                  style={{
                    padding: 'var(--space-2) 0',
                    borderBottom: '1px solid var(--olive-3)',
                  }}
                >
                  <Badge
                    color={event.outcome === 'issued' ? 'green' : 'red'}
                    size="1"
                  >
                    {event.outcome === 'issued'
                      ? t('workspace.serviceAccounts.delegations.outcomeIssued')
                      : t('workspace.serviceAccounts.delegations.outcomeRefused')}
                  </Badge>
                  <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
                    <Text size="1">
                      {event.subjectEmail ?? event.requestedSubject}
                    </Text>
                    {event.refusedReason && (
                      <Text size="1" style={{ color: 'var(--slate-10)', ...MONO }}>
                        {event.refusedReason}
                      </Text>
                    )}
                  </Flex>
                  <Text size="1" style={{ color: 'var(--slate-10)' }}>
                    {formatWhen(event.createdAt, i18n.language)}
                  </Text>
                </Flex>
              ))}
            </Box>
          )}
        </Flex>
      </Flex>

      <ConfirmationDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={t('workspace.serviceAccounts.delegations.revokeTitle')}
        message={t('workspace.serviceAccounts.delegations.revokeDescription', {
          subject:
            revokeTarget?.subjectEmail ?? revokeTarget?.subjectUserId ?? '',
        })}
        confirmLabel={t('workspace.serviceAccounts.delegations.revoke')}
        cancelLabel={t('common.cancel')}
        confirmVariant="danger"
        isLoading={isRevoking}
        onConfirm={() => void revoke()}
      />
    </WorkspaceRightPanel>
  );
}

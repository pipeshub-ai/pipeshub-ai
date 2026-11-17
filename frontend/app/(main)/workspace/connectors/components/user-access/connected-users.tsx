'use client';

import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { ConnectorsApi } from '../../api';
import type {
  UserAccessInviteResult,
  UserAccessInviteSkipReason,
  UserAccessRoster,
  UserAccessRosterPerson,
  UserAccessRosterStatus,
} from '../../types';

/** Summary order, which is also the server's sort order. */
const STATUS_ORDER: UserAccessRosterStatus[] = [
  'connected',
  'needs_reconnect',
  'invited',
  'not_invited',
  'opted_out',
  'no_account',
];

const STATUS_COLOR: Record<UserAccessRosterStatus, React.ComponentProps<typeof Badge>['color']> = {
  connected: 'jade',
  needs_reconnect: 'red',
  invited: 'blue',
  not_invited: 'gray',
  opted_out: 'amber',
  no_account: 'gray',
};

// A background send ({all: true}) is still going when it answers; read the roster again after this.
const QUEUED_RELOAD_MS = 5000;

// `{all: true}` skips anyone sent something within this long (server: MIN_SEND_GAP_MS).
const RECENT_SEND_MS = 24 * 60 * 60 * 1000;

/** Who "Invite all" reaches: a PipesHub account, not connected, not opted out, not sent anything in the last day. */
const isInvitable = (p: UserAccessRosterPerson) =>
  Boolean(p.userId) &&
  p.status !== 'connected' &&
  p.status !== 'opted_out' &&
  !(p.invite?.lastSentAt && Date.now() - p.invite.lastSentAt < RECENT_SEND_MS);

/**
 * Admin roster of a team connector in per-user mode: who connected, who still has to, and
 * buttons to ask them. Renders nothing for a connector in workspace mode (the roster answers 404).
 */
export function ConnectedUsers({
  connectorId,
  container,
}: {
  connectorId: string;
  /** Portal host from the drawer, so the confirmation stacks above it. */
  container?: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const headingId = useId();
  const addToast = useToastStore((s) => s.addToast);
  // null: not loaded, or not a per-user connector.
  const [roster, setRoster] = useState<UserAccessRoster | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // 'all' or the userId being invited.
  const [inviting, setInviting] = useState<string | null>(null);
  const [removing, setRemoving] = useState<UserAccessRosterPerson | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => setRoster(null), [connectorId]);
  useEffect(() => () => {
    if (reloadTimer.current) clearTimeout(reloadTimer.current);
  }, []);

  const toastFor = useCallback(
    (result: UserAccessInviteResult) => {
      if (result.queued) {
        addToast({ variant: 'success', title: t('workspace.connectors.userAccess.admin.queued', { count: result.sent }) });
        return;
      }
      if (result.sent === 0) {
        const reasons = [...new Set((result.skipped ?? []).map((s) => s.reason))] as UserAccessInviteSkipReason[];
        addToast({
          variant: 'warning',
          title: t('workspace.connectors.userAccess.admin.nothingSent'),
          description: reasons.map((r) => t(`workspace.connectors.userAccess.admin.skipReason.${r}`)).join(' ') || undefined,
        });
        return;
      }
      const notEmailed = result.emailed === null ? 0 : result.sent - result.emailed;
      addToast({
        variant: 'success',
        title: t('workspace.connectors.userAccess.admin.sent', { count: result.sent }),
        description:
          notEmailed > 0 ? t('workspace.connectors.userAccess.admin.notEmailed', { count: notEmailed }) : undefined,
      });
    },
    [addToast, t]
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await ConnectorsApi.getUserAccessRoster(connectorId);
        if (!cancelled) setRoster(next);
      } catch {
        // 404 means workspace mode; the interceptor already reported anything else.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connectorId, reloadKey]);

  const invite = useCallback(
    async (key: string, target: { all: true } | { userIds: string[] }) => {
      setInviting(key);
      try {
        const result = await ConnectorsApi.inviteUserAccess(connectorId, target);
        toastFor(result);
        setReloadKey((k) => k + 1);
        if (result.queued) {
          if (reloadTimer.current) clearTimeout(reloadTimer.current);
          reloadTimer.current = setTimeout(() => setReloadKey((k) => k + 1), QUEUED_RELOAD_MS);
        }
      } catch {
        // The interceptor shows the server's reason.
      } finally {
        setInviting(null);
      }
    },
    [connectorId, toastFor]
  );

  const handleRemove = useCallback(async () => {
    if (!removing?.userId) return;
    setRemoveBusy(true);
    try {
      await ConnectorsApi.removeUserAccessConnection(connectorId, removing.userId);
      setRemoving(null);
      setReloadKey((k) => k + 1);
      addToast({ variant: 'success', title: t('workspace.connectors.userAccess.admin.removed') });
    } catch {
      // The interceptor shows the server's reason.
    } finally {
      setRemoveBusy(false);
    }
  }, [connectorId, removing, addToast, t]);

  if (roster === null) return null;

  const invitableCount = roster.people.filter(isInvitable).length;
  const statusLabel = (s: UserAccessRosterStatus) => t(`workspace.connectors.userAccess.admin.status.${s}`);
  const summary = STATUS_ORDER.filter((s) => (roster.counts?.[s] ?? 0) > 0)
    .map((s) => t('workspace.connectors.userAccess.admin.countItem', { label: statusLabel(s), count: roster.counts[s] }))
    .join(' · ');

  const detail = (p: UserAccessRosterPerson): string | null => {
    if (p.status === 'no_account') return t('workspace.connectors.userAccess.admin.noAccountHint');
    if (p.connection && p.status === 'connected') {
      return t('workspace.connectors.userAccess.admin.connectedAt', {
        time: formatRelativeTime(p.connection.connectedAt),
      });
    }
    if (p.invite?.lastSentAt) {
      return t('workspace.connectors.userAccess.admin.lastSent', { time: formatRelativeTime(p.invite.lastSentAt) });
    }
    return null;
  };

  return (
    <Box asChild>
      <section aria-labelledby={headingId}>
        <Flex direction="column" gap="3">
          <Flex direction="column" gap="1">
            <Heading as="h3" id={headingId} size="3">
              {t('workspace.connectors.userAccess.admin.title')}
            </Heading>
            <Text size="2" color="gray">
              {t('workspace.connectors.userAccess.admin.description')}
            </Text>
            {summary ? (
              <Text size="2" weight="medium">
                {summary}
              </Text>
            ) : null}
            <Text size="1" color="gray">
              {roster.membersUpdatedAt
                ? t('workspace.connectors.userAccess.admin.membersUpdated', {
                    time: formatRelativeTime(roster.membersUpdatedAt),
                  })
                : t('workspace.connectors.userAccess.admin.membersPending')}
            </Text>
          </Flex>
          <Flex>
            <LoadingButton
              size="2"
              style={{ minHeight: 44 }}
              disabled={invitableCount === 0 || inviting !== null}
              loading={inviting === 'all'}
              onClick={() => void invite('all', { all: true })}
            >
              {t('workspace.connectors.userAccess.admin.inviteAll', { count: invitableCount })}
            </LoadingButton>
          </Flex>
          <Flex asChild direction="column" gap="3" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            <ul>
              {roster.people.map((p) => {
                const line = detail(p);
                const who = p.name || p.email;
                const firstInvite = !p.invite || p.invite.sendCount === 0;
                return (
                  <li key={p.userId ?? p.sourceUserId ?? p.email}>
                    <Flex align="center" justify="between" gap="3" wrap="wrap">
                      <Flex direction="column" gap="1" style={{ minWidth: 0, flex: '1 1 200px' }}>
                        <Flex align="center" gap="2" wrap="wrap">
                          <Text size="2" weight="medium" style={{ overflowWrap: 'anywhere' }}>
                            {p.name || p.email}
                          </Text>
                          <Badge color={STATUS_COLOR[p.status]} variant="soft">
                            {statusLabel(p.status)}
                          </Badge>
                        </Flex>
                        {p.name ? (
                          <Text size="1" color="gray" style={{ overflowWrap: 'anywhere' }}>
                            {p.email}
                          </Text>
                        ) : null}
                        {line ? (
                          <Text size="1" color="gray">
                            {line}
                          </Text>
                        ) : null}
                      </Flex>
                      <Flex gap="2" style={{ flexShrink: 0 }}>
                        {/* Opted out: no action; the badge already says why. */}
                        {p.userId && p.status !== 'connected' && p.status !== 'opted_out' ? (
                          <LoadingButton
                            size="2"
                            variant="soft"
                            style={{ minHeight: 44 }}
                            disabled={inviting !== null}
                            loading={inviting === p.userId}
                            onClick={() => void invite(p.userId as string, { userIds: [p.userId as string] })}
                            aria-label={
                              firstInvite
                                ? t('workspace.connectors.userAccess.admin.inviteWho', { name: who })
                                : t('workspace.connectors.userAccess.admin.remindWho', { name: who })
                            }
                          >
                            {firstInvite
                              ? t('workspace.connectors.userAccess.admin.invite')
                              : t('workspace.connectors.userAccess.admin.remind')}
                          </LoadingButton>
                        ) : null}
                        {p.userId && p.connection ? (
                          <Button
                            size="2"
                            variant="soft"
                            color="red"
                            style={{ minHeight: 44 }}
                            onClick={() => setRemoving(p)}
                            aria-label={t('workspace.connectors.userAccess.admin.removeWho', { name: who })}
                          >
                            {t('workspace.connectors.userAccess.admin.remove')}
                          </Button>
                        ) : null}
                      </Flex>
                    </Flex>
                  </li>
                );
              })}
            </ul>
          </Flex>
        </Flex>
        <ConfirmationDialog
          container={container}
          open={removing !== null}
          onOpenChange={(open) => !open && !removeBusy && setRemoving(null)}
          title={t('workspace.connectors.userAccess.admin.removeTitle', { email: removing?.email ?? '' })}
          message={t('workspace.connectors.userAccess.admin.removeMessage')}
          confirmLabel={t('workspace.connectors.userAccess.admin.remove')}
          confirmLoadingLabel={t('workspace.connectors.userAccess.admin.removing')}
          isLoading={removeBusy}
          onConfirm={() => void handleRemove()}
        />
      </section>
    </Box>
  );
}

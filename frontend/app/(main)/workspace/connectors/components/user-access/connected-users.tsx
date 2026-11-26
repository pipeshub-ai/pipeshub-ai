'use client';

import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Heading, Text, TextField } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { usePaginatedList } from '@/app/(main)/workspace/hooks/use-paginated-list';
import { ConnectorsApi } from '../../api';
import { ConnectAccountAction } from './connect-account';
import type {
  UserAccessInviteResult,
  UserAccessInviteSkipReason,
  UserAccessRoster,
  UserAccessRosterPerson,
  UserAccessRosterStatus,
} from '../../types';

const STATUS_COLOR: Record<UserAccessRosterStatus, React.ComponentProps<typeof Badge>['color']> = {
  connected: 'jade',
  needs_reconnect: 'red',
  invited: 'blue',
  not_invited: 'gray',
  opted_out: 'amber',
  no_account: 'gray',
};

const PAGE_SIZE = 20;
const SCROLL_THRESHOLD_PX = 40;

// A background send ({all: true}) is still going when it answers; read the roster again at these times.
const QUEUED_RELOADS_MS = [5_000, 15_000, 30_000, 60_000];

/** People "Invite everyone" can reach (the server also skips anyone sent something in the last day). */
const invitableCount = (counts: UserAccessRoster['counts']) =>
  (counts.not_invited ?? 0) + (counts.invited ?? 0) + (counts.needs_reconnect ?? 0);

const personKey = (p: UserAccessRosterPerson) => p.userId ?? p.sourceUserId ?? p.email;

/**
 * Admin view of a team connector in per-user mode: the admin's own connection, how many people
 * connected, and one search box that opens a paged list of people to invite, remind or remove,
 * with "Invite everyone" at its top. Renders nothing in workspace mode (the roster answers 404).
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
  const listId = useId();
  const addToast = useToastStore((s) => s.addToast);
  // Counts and member-list date; null: not loaded, or not a per-user connector.
  const [summary, setSummary] = useState<Pick<UserAccessRoster, 'counts' | 'membersUpdatedAt'> | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [open, setOpen] = useState(false);
  // 'all' or the userId being invited.
  const [inviting, setInviting] = useState<string | null>(null);
  const [removing, setRemoving] = useState<UserAccessRosterPerson | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const reloadTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const pickerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSummary(null);
    setOpen(false);
  }, [connectorId]);
  useEffect(() => () => reloadTimers.current.forEach(clearTimeout), []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const roster = await ConnectorsApi.getUserAccessRoster(connectorId, { limit: 1 });
        if (!cancelled) setSummary({ counts: roster.counts, membersUpdatedAt: roster.membersUpdatedAt });
      } catch {
        // 404 means workspace mode; the interceptor already reported anything else.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connectorId, reloadKey]);

  const fetcher = useCallback(
    async (q: string | undefined, page: number, limit: number) => {
      const roster = await ConnectorsApi.getUserAccessRoster(connectorId, { q, offset: (page - 1) * limit, limit });
      setSummary({ counts: roster.counts, membersUpdatedAt: roster.membersUpdatedAt });
      return { items: roster.people, totalCount: roster.total };
    },
    [connectorId]
  );
  const people = usePaginatedList<UserAccessRosterPerson>({
    fetcher,
    limit: PAGE_SIZE,
    enabled: open && summary !== null,
  });
  const { search, setSearch, reset } = people;
  // Reopening starts from the first page with an empty search (the hook's initial fetch ignores `search`).
  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  // Close the list on a click outside it (a dialog it opened is outside too, but keeps it open).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (pickerRef.current?.contains(target) || target?.closest?.('.rt-BaseDialogOverlay')) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const reload = useCallback(() => {
    setReloadKey((k) => k + 1);
    setSearch(search); // refetch the open list, same search, from the first page
  }, [search, setSearch]);

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

  const invite = useCallback(
    async (key: string, target: { all: true } | { userIds: string[] }) => {
      setInviting(key);
      try {
        const result = await ConnectorsApi.inviteUserAccess(connectorId, target);
        toastFor(result);
        reload();
        if (result.queued) {
          reloadTimers.current.forEach(clearTimeout);
          reloadTimers.current = QUEUED_RELOADS_MS.map((ms) => setTimeout(reload, ms));
        }
      } catch {
        // The interceptor shows the server's reason.
      } finally {
        setInviting(null);
      }
    },
    [connectorId, toastFor, reload]
  );

  const handleRemove = useCallback(async () => {
    if (!removing?.userId) return;
    setRemoveBusy(true);
    try {
      await ConnectorsApi.removeUserAccessConnection(connectorId, removing.userId);
      setRemoving(null);
      reload();
      addToast({ variant: 'success', title: t('workspace.connectors.userAccess.admin.removed') });
    } catch {
      // The interceptor shows the server's reason.
    } finally {
      setRemoveBusy(false);
    }
  }, [connectorId, removing, reload, addToast, t]);

  if (summary === null) return null;

  const { counts } = summary;
  const everyone = Object.values(counts).reduce((a, b) => a + b, 0);
  const canInviteAll = invitableCount(counts);
  const statusLabel = (s: UserAccessRosterStatus) => t(`workspace.connectors.userAccess.admin.status.${s}`);

  const detail = (p: UserAccessRosterPerson): string | null => {
    if (p.status === 'no_account') return t('workspace.connectors.userAccess.admin.noAccountHint');
    if (p.connection && p.status === 'connected') {
      return t('workspace.connectors.userAccess.admin.connectedAt', { time: formatRelativeTime(p.connection.connectedAt) });
    }
    if (p.invite?.lastSentAt) {
      return t('workspace.connectors.userAccess.admin.lastSent', { time: formatRelativeTime(p.invite.lastSentAt) });
    }
    return null;
  };

  const row = (p: UserAccessRosterPerson) => {
    const who = p.name || p.email;
    const line = detail(p);
    const firstInvite = !p.invite || p.invite.sendCount === 0;
    return (
      <li key={personKey(p)} style={{ padding: 'var(--space-2) var(--space-3)' }}>
        <Flex align="center" justify="between" gap="3">
          <Flex direction="column" style={{ minWidth: 0, flex: 1 }}>
            <Flex align="center" gap="2" wrap="wrap">
              <Text size="2" weight="medium" style={{ overflowWrap: 'anywhere' }}>
                {who}
              </Text>
              <Badge color={STATUS_COLOR[p.status]} variant="soft" size="1">
                {statusLabel(p.status)}
              </Badge>
            </Flex>
            <Text size="1" color="gray" style={{ overflowWrap: 'anywhere' }}>
              {[p.name ? p.email : null, line].filter(Boolean).join(' · ')}
            </Text>
          </Flex>
          {/* Opted out or no account: no action; the badge and line say why. */}
          {p.userId && p.status !== 'connected' && p.status !== 'opted_out' ? (
            <LoadingButton
              size="1"
              variant="soft"
              disabled={inviting !== null}
              loading={inviting === p.userId}
              onClick={() => void invite(p.userId as string, { userIds: [p.userId as string] })}
              aria-label={
                firstInvite
                  ? t('workspace.connectors.userAccess.admin.inviteWho', { name: who })
                  : t('workspace.connectors.userAccess.admin.remindWho', { name: who })
              }
            >
              {firstInvite ? t('workspace.connectors.userAccess.admin.invite') : t('workspace.connectors.userAccess.admin.remind')}
            </LoadingButton>
          ) : null}
          {p.userId && p.connection ? (
            <Button
              size="1"
              variant="soft"
              color="red"
              onClick={() => setRemoving(p)}
              aria-label={t('workspace.connectors.userAccess.admin.removeWho', { name: who })}
            >
              {t('workspace.connectors.userAccess.admin.remove')}
            </Button>
          ) : null}
        </Flex>
      </li>
    );
  };

  const onScroll = () => {
    const el = listRef.current;
    if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD_PX) people.loadMore();
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
          </Flex>

          <Flex align="center" justify="between" gap="3" wrap="wrap">
            <Text size="2" weight="medium">
              {t('workspace.connectors.userAccess.admin.yourAccount')}
            </Text>
            <ConnectAccountAction connectorId={connectorId} />
          </Flex>

          <Flex direction="column" gap="1">
            <Text size="2" weight="medium">
              {t('workspace.connectors.userAccess.admin.connectedOf', { connected: counts.connected ?? 0, total: everyone })}
              {counts.needs_reconnect
                ? ` · ${t('workspace.connectors.userAccess.admin.needReconnect', { count: counts.needs_reconnect })}`
                : ''}
            </Text>
            <Text size="1" color="gray">
              {summary.membersUpdatedAt
                ? t('workspace.connectors.userAccess.admin.membersUpdated', { time: formatRelativeTime(summary.membersUpdatedAt) })
                : t('workspace.connectors.userAccess.admin.membersPending')}
            </Text>
          </Flex>

          <Box ref={pickerRef} onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
            <TextField.Root
              size="2"
              role="combobox"
              aria-expanded={open}
              aria-controls={listId}
              aria-label={t('workspace.connectors.userAccess.admin.search')}
              placeholder={t('workspace.connectors.userAccess.admin.search')}
              value={search}
              onFocus={() => setOpen(true)}
              onClick={() => setOpen(true)}
              onChange={(e) => {
                setOpen(true);
                setSearch(e.target.value);
              }}
            >
              <TextField.Slot>
                <MaterialIcon name="person_search" size={16} color="var(--slate-11)" />
              </TextField.Slot>
            </TextField.Root>
            {open ? (
              <Box
                id={listId}
                mt="1"
                style={{
                  border: '1px solid var(--olive-4)',
                  borderRadius: 'var(--radius-3)',
                  background: 'var(--color-panel-solid)',
                  overflow: 'hidden',
                }}
              >
                {!search.trim() ? (
                  <Flex
                    align="center"
                    justify="between"
                    gap="3"
                    style={{ padding: 'var(--space-2) var(--space-3)', borderBottom: '1px solid var(--olive-4)' }}
                  >
                    <Text size="2" color="gray">
                      {t('workspace.connectors.userAccess.admin.inviteEveryoneHint')}
                    </Text>
                    <LoadingButton
                      size="1"
                      disabled={canInviteAll === 0 || inviting !== null}
                      loading={inviting === 'all'}
                      onClick={() => void invite('all', { all: true })}
                    >
                      {t('workspace.connectors.userAccess.admin.inviteEveryone', { count: canInviteAll })}
                    </LoadingButton>
                  </Flex>
                ) : null}
                <Box ref={listRef} onScroll={onScroll} style={{ maxHeight: 320, overflowY: 'auto' }}>
                  {people.isLoading && people.items.length === 0 ? (
                    <Flex justify="center" p="3">
                      <Spinner size={16} />
                    </Flex>
                  ) : people.items.length === 0 ? (
                    <Text as="p" size="2" color="gray" style={{ padding: 'var(--space-3)' }}>
                      {search.trim()
                        ? t('workspace.connectors.userAccess.admin.noMatches', { q: search.trim() })
                        : t('workspace.connectors.userAccess.admin.nobody')}
                    </Text>
                  ) : (
                    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>{people.items.map(row)}</ul>
                  )}
                  {people.isLoadingMore ? (
                    <Flex justify="center" p="2">
                      <Spinner size={12} />
                    </Flex>
                  ) : null}
                </Box>
              </Box>
            ) : null}
          </Box>
        </Flex>
        <ConfirmationDialog
          container={container}
          open={removing !== null}
          onOpenChange={(o) => !o && !removeBusy && setRemoving(null)}
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

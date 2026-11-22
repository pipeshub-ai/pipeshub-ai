'use client';

import { useId, useState } from 'react';
import useSWR from 'swr';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Heading, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { selectIsAdmin, useUserStore } from '@/lib/store/user-store';
import { ConnectorsApi } from '../../api';
import type { ConnectorInstance, PermissionStatus } from '../../types';

const MAX_WARNINGS = 5;
const REFRESH_MS = 30_000;

/** The server sends codes; each is worded by one of these keys under `attention.`, and anything else by `other`. */
const ATTENTION_KEY: Record<string, string> = {
  pending_unreadable: 'unreadable',
  needs_full_pass: 'rebuild',
  open_write_expired: 'unfinished',
  open_write_unleased: 'unfinished',
  open_writes_unreadable: 'unfinished',
  writes_unreadable: 'unfinished',
  stale_points: 'stale',
  sweep_failing: 'sweep',
};

/** The icon font renders its name as text, so it is hidden from screen readers: the sentence beside it carries the meaning. */
function Icon({ name, color }: { name: string; color: string }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex' }}>
      <MaterialIcon name={name} size={16} color={color} />
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Flex direction="column" gap="1">
      <Text size="1" weight="medium" style={{ color: 'var(--gray-11)' }}>{label}</Text>
      {children}
    </Flex>
  );
}

/** Whole minutes as the largest clean unit, so the sentence reads "within 6 hours", not "360 minutes". */
function useSpan() {
  const { t } = useTranslation();
  return (minutes: number) => {
    if (minutes >= 1440 && minutes % 1440 === 0) return t('workspace.connectors.access.span.days', { count: minutes / 1440 });
    if (minutes >= 60 && minutes % 60 === 0) return t('workspace.connectors.access.span.hours', { count: minutes / 60 });
    return t('workspace.connectors.access.span.minutes', { count: Math.max(1, Math.round(minutes)) });
  };
}

/** Admin-only card on a connector's Overview: how fast permission changes reach PipesHub, and what is waiting or needs a person. */
export function PermissionFreshness({
  instance,
  onSyncNow,
  syncBusy = false,
}: {
  instance: ConnectorInstance;
  onSyncNow?: () => void;
  syncBusy?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const span = useSpan();
  const uid = useId();
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const id = isAdmin && instance.scope === 'team' ? instance._key : undefined;
  const { data, error, isLoading, mutate } = useSWR<PermissionStatus>(
    id ? ['connector-permission-status', id] : null,
    () => ConnectorsApi.getPermissionStatus(id as string),
    { refreshInterval: REFRESH_MS }
  );
  const [showAll, setShowAll] = useState(false);

  if (!id) return null;
  const k = (key: string, o?: Record<string, unknown>) => t(`workspace.connectors.access.${key}`, o);
  const when = (iso: string) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso));
  const headingId = `${uid}-h`;

  const body = () => {
    if (isLoading) {
      return <Text size="2" role="status" style={{ color: 'var(--gray-11)' }}>{k('loading')}</Text>;
    }
    if (error || !data) {
      return (
        <Flex align="center" gap="3" wrap="wrap" role="alert">
          <Text size="2" color="red">{k('loadError')}</Text>
          <Button size="2" variant="soft" style={{ minHeight: 44 }} onClick={() => void mutate()}>
            {k('retry')}
          </Button>
        </Flex>
      );
    }
    const { lastPermissionSync: last, pending, warnings, attention, state } = data;
    const refresh =
      state === 'running' ? 'running'
        : state === 'never' ? 'never'
          : last.outcome === 'failed' ? 'failed'
            : last.outcome === 'stopped' || last.outcome === 'skipped' ? 'incomplete'
              : 'succeeded';
    const shown = showAll ? warnings : warnings.slice(0, MAX_WARNINGS);
    const problems = attention.length + warnings.length;

    return (
      <Flex direction="column" gap="4">
        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Text size="2">
            {data.reachWithinMinutes
              ? k('reach', { connector: instance.name, interval: span(data.reachWithinMinutes) })
              : k('reachUnknown', { connector: instance.name })}
          </Text>
          {onSyncNow && (
            <Button size="2" variant="soft" style={{ minHeight: 44 }} onClick={onSyncNow} disabled={syncBusy} aria-busy={syncBusy}>
              {k('syncNow')}
            </Button>
          )}
        </Flex>

        <Row label={k('refresh.label')}>
          <Flex gap="2" align="start">
            {(refresh === 'failed' || refresh === 'incomplete') && <Icon name="error" color="var(--red-11)" />}
            <Text size="2" color={refresh === 'failed' ? 'red' : undefined}>
              {k(`refresh.${refresh}`, { when: last.finishedAt ? when(last.finishedAt) : '' })}
            </Text>
          </Flex>
        </Row>

        <Row label={k('pending.label')}>
          {pending.unreadable ? (
            <Text size="2">{k('pending.unknown')}</Text>
          ) : state === 'overflow' ? (
            <Text size="2">{k('pending.overflow')}</Text>
          ) : pending.roots === 0 ? (
            <Text size="2">{k('pending.none')}</Text>
          ) : (
            <Text size="2">{k('pending.some', { count: pending.roots })}</Text>
          )}
        </Row>

        <Row label={k('attention.label', { count: problems })}>
          {problems === 0 ? (
            <Text size="2">{k('attention.none')}</Text>
          ) : (
            <Flex asChild direction="column" gap="2" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              <ul>
                {attention.map((code) => (
                  <li key={code}>
                    <Flex gap="2" align="start">
                      <Icon name="warning" color="var(--amber-11)" />
                      <Text size="2">{k(`attention.${ATTENTION_KEY[code] ?? 'other'}`)}</Text>
                    </Flex>
                  </li>
                ))}
                {shown.map((w, i) => (
                  <li key={`${w.code}-${i}`}>
                    <Flex gap="2" align="start">
                      <Icon name="warning" color="var(--amber-11)" />
                      <Text size="2">
                        {w.container && w.items ? k('attention.warning', { items: w.items, container: w.container }) : k('attention.warningNoContainer')}
                        {w.fix?.permission && w.fix?.vendor && ` ${k('attention.fix', { permission: w.fix.permission, vendor: w.fix.vendor })}`}
                      </Text>
                    </Flex>
                  </li>
                ))}
              </ul>
            </Flex>
          )}
          {warnings.length > MAX_WARNINGS && !showAll && (
            <Button size="1" variant="ghost" style={{ alignSelf: 'start', minHeight: 44 }} onClick={() => setShowAll(true)}>
              {k('attention.showAll', { count: warnings.length - MAX_WARNINGS })}
            </Button>
          )}
        </Row>
      </Flex>
    );
  };

  return (
    <Box asChild>
      <section aria-labelledby={headingId} data-testid="permission-freshness">
        <Flex direction="column" gap="3">
          <Heading as="h3" id={headingId} size="3">{k('title')}</Heading>
          {body()}
        </Flex>
      </section>
    </Box>
  );
}

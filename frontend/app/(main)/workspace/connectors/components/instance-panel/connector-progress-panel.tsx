'use client';

import { Box, Button, Flex, Grid, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { formatRelativeTime } from '@/lib/utils/formatters';
import type { IndexingStatus } from '@/app/(main)/knowledge-base/types';
import { ConnectorProgressLine } from '../connector-progress-line';
import {
  errorAction,
  errorTitle,
  etaLabel,
  unitLabel,
  type ConnectorProgress,
  type ReasonGroup,
  type SyncRun,
} from '../../utils/connector-progress';

const K = 'workspace.connectors.progress';

// Failures and missing models need someone; the rest is how the content is.
const NEEDS_ATTENTION = new Set(['FAILED', 'ENABLE_MULTIMODAL_MODELS']);

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Flex direction="column" gap="1">
      <Text size="1" style={{ color: 'var(--gray-10)' }}>{label}</Text>
      <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </Text>
    </Flex>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Flex direction="column" gap="3">
      <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', textTransform: 'uppercase', letterSpacing: '0.04px' }}>
        {title}
      </Text>
      {children}
    </Flex>
  );
}

function durationLabel(t: TFunction, run: SyncRun): string | null {
  if (!run.finishedAt) return null;
  const minutes = Math.round((run.finishedAt - run.startedAt) / 60000);
  return minutes < 1 ? t(`${K}.duration.underMinute`) : t(`${K}.duration.minutes`, { n: minutes });
}

function RunRow({ run, source }: { run: SyncRun; source: string }) {
  const { t } = useTranslation();
  const icon =
    run.outcome === 'succeeded' ? { name: 'check_circle', color: 'var(--jade-11)' }
      : run.outcome === 'failed' ? { name: 'error', color: 'var(--red-11)' }
        : { name: 'remove_circle_outline', color: 'var(--gray-10)' };
  const duration = durationLabel(t, run);
  const changed = run.counts.new + run.counts.changed;
  return (
    <Flex gap="2" align="start" style={{ padding: 'var(--space-2) 0', borderTop: '1px solid var(--gray-a3)' }}>
      <MaterialIcon name={icon.name} size={16} color={icon.color} />
      <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
        <Text size="2" style={{ color: 'var(--gray-12)' }}>
          {t(`${K}.history.line`, {
            kind: t(run.full ? `${K}.history.full` : `${K}.history.quick`),
            trigger: t(`${K}.trigger.${run.trigger}`),
            time: formatRelativeTime(run.startedAt),
          })}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          {[
            run.outcome ? t(`workspace.connectors.overview.lastRun.outcome.${run.outcome}`) : null,
            duration,
            t(`${K}.history.counts`, { found: run.counts.found, changed, removed: run.counts.removed }),
            run.outcome === 'failed' ? errorTitle(t, run.error?.code, source) : null,
          ].filter(Boolean).join(' · ')}
        </Text>
      </Flex>
    </Flex>
  );
}

function ReasonRow({
  group,
  onView,
}: {
  group: ReasonGroup;
  onView?: (statuses: IndexingStatus[]) => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex justify="between" align="start" gap="3" style={{ padding: 'var(--space-2) 0', borderTop: '1px solid var(--gray-a3)' }}>
      <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t(`${K}.status.${group.indexingStatus}`, { defaultValue: group.indexingStatus })} · {group.count.toLocaleString()}
        </Text>
        {group.reason ? (
          <Text size="1" style={{ color: 'var(--gray-11)' }}>{group.reason}</Text>
        ) : null}
      </Flex>
      {onView ? (
        <Button
          size="1"
          variant="ghost"
          onClick={() => onView([group.indexingStatus as IndexingStatus])}
          style={{ flexShrink: 0 }}
        >
          {t(`${K}.viewRecords`)}
        </Button>
      ) : null}
    </Flex>
  );
}

/**
 * The two stages of a connector — reading from the source, making it searchable —
 * plus why the last sync failed, why records are not searchable, and the last runs.
 */
export function ConnectorProgressPanel({
  view,
  source,
  byReason,
  onViewRecords,
}: {
  view: ConnectorProgress;
  source: string;
  byReason?: ReasonGroup[];
  onViewRecords?: (statuses: IndexingStatus[]) => void;
}) {
  const { t } = useTranslation();
  const { run, indexing, lastRun, recentRuns = [] } = view;
  const failedRun = !run && lastRun?.outcome === 'failed' ? lastRun : null;
  const failure = failedRun ? (failedRun.error ?? view.lastError) : null;
  const attention = (byReason ?? []).filter((g) => NEEDS_ATTENTION.has(g.indexingStatus));
  const byChoice = (byReason ?? []).filter((g) => !NEEDS_ATTENTION.has(g.indexingStatus));
  const backlog = indexing.waiting + indexing.inProgress;

  return (
    <Flex direction="column" gap="5">
      <ConnectorProgressLine view={view} source={source} />

      {failedRun ? (
        <Box style={{ padding: 'var(--space-3)', borderRadius: 'var(--radius-3)', backgroundColor: 'var(--red-a2)', border: '1px solid var(--red-a5)' }}>
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--red-11)' }}>{errorTitle(t, failure?.code, source)}</Text>
            <Text size="2" style={{ color: 'var(--gray-12)' }}>{errorAction(t, failure?.code, source)}</Text>
            {failure?.detail ? (
              <Text size="1" style={{ color: 'var(--gray-10)', wordBreak: 'break-word' }}>
                {t(`${K}.error.detail`, { detail: failure.detail })}
              </Text>
            ) : null}
          </Flex>
        </Box>
      ) : null}

      {run ? (
        <Section title={t(`${K}.stage.reading`, { source })}>
          <Grid columns={{ initial: '2', sm: '4' }} gap="3">
            <Stat label={t(`${K}.count.found`)} value={run.counts.found} />
            <Stat label={t(`${K}.count.changed`)} value={run.counts.new + run.counts.changed} />
            <Stat label={t(`${K}.count.unchanged`)} value={run.counts.unchanged} />
            <Stat label={t(`${K}.count.removed`)} value={run.counts.removed} />
            {run.unit ? (
              <Stat
                label={unitLabel(t, run.unit.kind)}
                value={run.unit.total ? `${run.unit.done.toLocaleString()} / ${run.unit.total.toLocaleString()}` : run.unit.done}
              />
            ) : null}
            {run.counts.filtered ? <Stat label={t(`${K}.count.filtered`)} value={run.counts.filtered} /> : null}
            {run.counts.containers ? <Stat label={t(`${K}.count.containers`)} value={run.counts.containers} /> : null}
          </Grid>
          {run.approximate ? <Text size="1" style={{ color: 'var(--gray-10)' }}>{t(`${K}.approximate`)}</Text> : null}
        </Section>
      ) : null}

      {backlog > 0 || indexing.failed > 0 ? (
        <Section title={t(`${K}.stage.searchable`)}>
          <Grid columns={{ initial: '2', sm: '4' }} gap="3">
            <Stat label={t(`${K}.count.waiting`)} value={indexing.waiting} />
            <Stat label={t(`${K}.count.inProgress`)} value={indexing.inProgress} />
            <Stat label={t(`${K}.count.failed`)} value={indexing.failed} />
            <Stat
              label={t(`${K}.count.eta`)}
              value={indexing.etaSeconds != null ? etaLabel(t, indexing.etaSeconds) : t(`${K}.eta.unknown`)}
            />
          </Grid>
          {indexing.waitingInLine ? (
            <Text size="1" style={{ color: 'var(--gray-11)' }}>{t(`${K}.indexing.inLineHelp`)}</Text>
          ) : null}
        </Section>
      ) : null}

      {attention.length ? (
        <Section title={t(`${K}.attention.title`)}>
          <Flex direction="column">
            {attention.map((g) => <ReasonRow key={`${g.indexingStatus}:${g.reason}`} group={g} onView={onViewRecords} />)}
          </Flex>
        </Section>
      ) : null}

      {byChoice.length ? (
        <Section title={t(`${K}.notIndexed.title`)}>
          <Flex direction="column">
            {byChoice.map((g) => <ReasonRow key={`${g.indexingStatus}:${g.reason}`} group={g} onView={onViewRecords} />)}
          </Flex>
        </Section>
      ) : null}

      {recentRuns.length ? (
        <Section title={t(`${K}.history.title`)}>
          <Flex direction="column">
            {recentRuns.map((r) => <RunRow key={r.runId} run={r} source={source} />)}
          </Flex>
        </Section>
      ) : null}
    </Flex>
  );
}

'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Heading, Tabs, Text } from '@radix-ui/themes';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useFeatureFlagGuard } from '@/lib/hooks/use-feature-flag-guard';
import { selectAgentHarnessPaused, selectScheduledRunsEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { LoadState } from '../../ai-accounts/components/load-state';
import { ApprovalsDirectory } from '../../approvals/directory';
import { SettingsSection } from '../../components/settings-section';
import { SchedulesOrgTab } from '@/config';
import { useSchedulesStore } from '../store';
import type { ScheduleScope } from '../types';
import { CreateScheduleDialog, type AgentOption } from './create-schedule-dialog';
import { ScheduleRow } from './schedule-row';
import { WebhookSecretDialog, type WebhookCredential } from './webhook-secret-dialog';

function ScheduleList({ scope }: { scope: ScheduleScope }) {
  const { t } = useTranslation();
  const load = useSchedulesStore((s) => s[scope]);
  const reload = useSchedulesStore((s) => s.load);
  const act = useSchedulesStore((s) => s.act);
  const pin = useSchedulesStore((s) => s.pin);

  useEffect(() => {
    void reload(scope);
  }, [reload, scope]);

  return (
    <LoadState load={load} unavailableKey="schedules.unavailable" onRetry={() => void reload(scope)}>
      {(schedules) =>
        schedules.length === 0 ? (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('schedules.empty')}
          </Text>
        ) : (
          <Flex direction="column" gap="3">
            {schedules.map((schedule) => (
              <ScheduleRow
                key={schedule.id}
                schedule={schedule}
                orgView={false}
                agentName={
                  schedule.agentKey ? (schedule.agentName ?? schedule.agentKey) : t('schedules.assistant')
                }
                onAction={(action) => act(scope, schedule.id, action)}
                onPin={(agentVersion) => pin(scope, schedule.id, agentVersion)}
              />
            ))}
          </Flex>
        )
      }
    </LoadState>
  );
}

/**
 * "Schedules" (P8a): a person's own scheduled and webhook-triggered agent
 * runs, and, for an admin, any tab a build adds beside them (`SchedulesOrgTab`).
 * Behind Labs `ENABLE_SCHEDULED_RUNS`.
 */
export function SchedulesPage() {
  const { t } = useTranslation();
  const { ready } = useFeatureFlagGuard(selectScheduledRunsEnabled);
  const isAdmin = useUserStore(selectIsAdmin);
  const paused = useFeatureFlagsStore(selectAgentHarnessPaused);
  const create = useSchedulesStore((s) => s.create);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<WebhookCredential | null>(null);
  const [agents, setAgents] = useState<AgentOption[]>([]);

  useEffect(() => {
    if (!creating) return;
    void ApprovalsDirectory.searchAgents('')
      .then(setAgents)
      .catch(() => setAgents([]));
  }, [creating]);

  const header = useMemo(
    () => (
      <Box style={{ marginBottom: 'var(--space-6)' }}>
        <Flex align="center" gap="2">
          <Heading as="h1" size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('schedules.title')}
          </Heading>
          <BetaBadge />
        </Flex>
        <Text size="2" as="p" style={{ color: 'var(--slate-11)', marginTop: 'var(--space-1)' }}>
          {t('schedules.subtitle')}
        </Text>
        {paused && (
          <Text size="2" as="p" role="note" data-testid="schedules-paused" style={{ color: 'var(--amber-11)', marginTop: 'var(--space-2)' }}>
            {t('schedules.harnessPaused')}
          </Text>
        )}
      </Box>
    ),
    [t, paused],
  );

  if (!ready) return null;

  const mine = (
    <SettingsSection
      title={t('schedules.mineTitle')}
      description={t('schedules.mineDescription')}
      rightAction={
        <Button type="button" onClick={() => setCreating(true)}>
          {t('schedules.new')}
        </Button>
      }
    >
      <ScheduleList scope="mine" />
    </SettingsSection>
  );

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <Box style={{ padding: '64px var(--workspace-gutter, 100px)', paddingBottom: 80, maxWidth: 1100 }}>
        {header}
        {isAdmin && SchedulesOrgTab !== null ? (
          <Tabs.Root defaultValue="mine">
            <Tabs.List aria-label={t('schedules.title')}>
              <Tabs.Trigger value="mine">{t('schedules.tabs.mine')}</Tabs.Trigger>
              <Tabs.Trigger value="org">{t('schedules.tabs.org')}</Tabs.Trigger>
            </Tabs.List>
            <Box pt="4">
              <Tabs.Content value="mine">{mine}</Tabs.Content>
              <Tabs.Content value="org">
                <SchedulesOrgTab />
              </Tabs.Content>
            </Box>
          </Tabs.Root>
        ) : (
          mine
        )}
      </Box>

      <CreateScheduleDialog
        open={creating}
        onOpenChange={setCreating}
        agents={agents}
        onSubmit={async (input) => {
          const created = await create(input);
          setCreating(false);
          if (created.webhookSecret) {
            setSecret({ scheduleId: created.schedule.id, kind: 'signature', value: created.webhookSecret });
          } else if (created.webhookToken) {
            setSecret({ scheduleId: created.schedule.id, kind: 'token', value: created.webhookToken });
          }
        }}
      />
      <WebhookSecretDialog secret={secret} onClose={() => setSecret(null)} />
    </Box>
  );
}

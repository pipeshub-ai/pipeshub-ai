'use client';

import { useCallback, useEffect, useState } from 'react';
import useSWR from 'swr';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Switch, Text } from '@radix-ui/themes';
import { SWR_POLICY } from '@/lib/api/swr-cache';
import { SettingsSection } from '../../components';
import type { GeneralPageSettings } from '../general-page-settings';
import { METRICS_KEY, MetricsApi } from './api';

const NO_ORG_FIELDS: Readonly<Record<string, unknown>> = {};

/** The metrics-collection setting (admins only: pass `enabled` false for others). */
export function useOrgMetrics(enabled: boolean) {
  return useSWR(enabled ? METRICS_KEY : null, () => MetricsApi.getMetricsCollection(), SWR_POLICY.sessionStable);
}

/**
 * The Data Collection switch of the admin General page: it joins the page's form, so it is
 * saved and discarded with it. `active` is false for anyone who is not an admin: the setting
 * is not theirs to read, so it is not asked for.
 */
export function useGeneralDataCollection(active: boolean): GeneralPageSettings {
  const { t } = useTranslation();
  const { data: metrics, error, isValidating, mutate } = useOrgMetrics(active);
  const [seeded, setSeeded] = useState(false);
  const [checked, setChecked] = useState(false);
  const [saved, setSaved] = useState(false);

  // Seeded once per visit: a later refresh (after Save) must not replace what is being edited.
  useEffect(() => {
    if (seeded || !metrics) return;
    const on = metrics.enableMetricCollection === 'true';
    setChecked(on);
    setSaved(on);
    setSeeded(true);
  }, [seeded, metrics]);

  // Shown only while telemetry can run on this deployment, so whatever is collected can always
  // be seen and switched off. A server that does not say shows it; hidden until the setting loads.
  const telemetryEnabled = Boolean(metrics) && metrics?.telemetryEnabled !== false;

  const save = useCallback(async () => {
    if (checked !== saved) await MetricsApi.toggleMetricsCollection(checked);
    setSaved(checked);
  }, [checked, saved]);
  const discard = useCallback(() => setChecked(saved), [saved]);
  const retry = useCallback(() => {
    void mutate();
  }, [mutate]);

  const section = telemetryEnabled ? (
    <SettingsSection
      title={t('workspace.general.dataCollection')}
      rightAction={
        <Flex align="center" gap="2">
          <Switch checked={checked} onCheckedChange={setChecked} size="1" />
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.general.enableDataCollection')}
          </Text>
        </Flex>
      }
    >
      <Box style={{ padding: 'var(--space-4) var(--space-5)' }}>
        <Text size="2" style={{ color: 'var(--slate-11)', display: 'block', marginBottom: 'var(--space-3)' }}>
          {t('workspace.general.dataCollectionDescription')}
        </Text>
        <Flex direction="column" gap="2" style={{ paddingLeft: 'var(--space-1)' }}>
          {[
            t('workspace.general.dataCollectionReason1'),
            t('workspace.general.dataCollectionReason2'),
            t('workspace.general.dataCollectionReason3'),
            t('workspace.general.dataCollectionReason4'),
            t('workspace.general.dataCollectionReason5'),
          ].map((item) => (
            <Flex key={item} align="start" gap="2">
              <Box
                style={{
                  width: 'var(--space-1)',
                  height: 'var(--space-1)',
                  borderRadius: '50%',
                  backgroundColor: 'var(--slate-9)',
                  marginTop: 'var(--space-2)',
                  flexShrink: 0,
                }}
              />
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {item}
              </Text>
            </Flex>
          ))}
        </Flex>
      </Box>
    </SettingsSection>
  ) : null;

  return {
    ready: !active || Boolean(metrics),
    failed: active && Boolean(error) && !metrics && !isValidating,
    retry,
    dirty: checked !== saved,
    // The organization update's handler never read the switch, so nothing rides on it.
    orgFields: NO_ORG_FIELDS,
    save,
    discard,
    section,
  };
}

'use client';

/**
 * Everything the General page does for Data Collection. `data-collection.stub.tsx`
 * takes this file's place in a build without telemetry (scripts/telemetry-stub.mjs),
 * so keep the two files' exports the same and keep this code out of other files.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import useSWR, { mutate } from 'swr';
import { Box, Flex, Text, Switch } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { SettingsSection } from '../components';
import { useGeneralStore } from './store';

const METRICS_BASE_URL = '/api/v1/configurationManager/metricsCollection';
const METRICS_URL = `${METRICS_BASE_URL}/toggle`;
const METRICS_KEY = [SWR_NS.org, 'metrics'] as const;

export interface MetricsConfig {
  enableMetricCollection: string; // "true" | "false" (string from API)
}

export const MetricsApi = {
  /** GET /api/v1/configurationManager/metricsCollection */
  async getMetricsCollection(): Promise<MetricsConfig> {
    const { data } = await apiClient.get<MetricsConfig>(METRICS_BASE_URL);
    return data;
  },

  /** PUT /api/v1/configurationManager/metricsCollection/toggle */
  async toggleMetricsCollection(enabled: boolean): Promise<void> {
    await apiClient.put(METRICS_URL, { enableMetricCollection: enabled });
    void mutate(METRICS_KEY);
  },
};

/** The metrics-collection setting (admins only: pass `enabled` false for others). */
function useOrgMetrics(enabled: boolean) {
  return useSWR(enabled ? METRICS_KEY : null, () => MetricsApi.getMetricsCollection(), SWR_POLICY.sessionStable);
}

function useAdminMetrics() {
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  return useOrgMetrics(isProfileInitialized && isAdmin === true);
}

/** What the General page needs to fill and save the setting. */
export function useDataCollection() {
  const { data, error, isValidating, mutate: reread } = useAdminMetrics();
  return {
    /** The page waits for this before it fills an admin's form. */
    loaded: Boolean(data),
    error: error as unknown,
    validating: isValidating,
    reread: reread as () => unknown,
    value: data?.enableMetricCollection === 'true',
    save: saveDataCollection,
  };
}

async function saveDataCollection(next: boolean, saved: boolean): Promise<void> {
  if (next !== saved) await MetricsApi.toggleMetricsCollection(next);
}

export function DataCollectionSection() {
  const { t } = useTranslation();
  const checked = useGeneralStore((s) => s.form.optIn);
  const setField = useGeneralStore((s) => s.setField);

  return (
    <Box style={{ marginBottom: 'var(--space-5)' }}>
      <SettingsSection
        title={t('workspace.general.dataCollection')}
        rightAction={
          <Flex align="center" gap="2">
            <Switch
              checked={checked}
              onCheckedChange={(next) => setField('optIn', next)}
              size="1"
            />
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.general.enableDataCollection')}
            </Text>
          </Flex>
        }
      >
        <Box style={{ padding: 'var(--space-4) var(--space-5)' }}>
          <Text
            size="2"
            style={{ color: 'var(--slate-11)', display: 'block', marginBottom: 'var(--space-3)' }}
          >
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
    </Box>
  );
}

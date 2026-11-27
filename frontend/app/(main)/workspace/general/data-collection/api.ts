import { mutate } from 'swr';
import { apiClient } from '@/lib/api';
import { SWR_NS } from '@/lib/api/swr-cache';

const METRICS_BASE_URL = '/api/v1/configurationManager/metricsCollection';
const METRICS_URL = `${METRICS_BASE_URL}/toggle`;

export interface MetricsConfig {
  enableMetricCollection: string; // "true" | "false" (string from API)
  /** False when the deployment runs no telemetry; the General page then hides Data Collection. */
  telemetryEnabled?: boolean;
}

/** SWR key of the metrics-collection setting; it is cleared with the rest of the org's reads. */
export const METRICS_KEY = [SWR_NS.org, 'metrics'] as const;

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

import { mutate } from 'swr';
import { apiClient, sharedGet } from '@/lib/api';
import { SWR_NS } from '@/lib/api/swr-cache';
import { blobToDataUrl } from '@/lib/utils/blob-to-data-url';

const BASE_URL = '/api/v1/org';
const METRICS_BASE_URL = '/api/v1/configurationManager/metricsCollection';
const METRICS_URL = `${METRICS_BASE_URL}/toggle`;

// ========================================
// Metrics types
// ========================================

export interface MetricsConfig {
  enableMetricCollection: string; // "true" | "false" (string from API)
}

// ========================================
// Types (matching API response)
// ========================================

export interface OrgAddress {
  addressLine1: string;
  city: string;
  state: string;
  postCode: string;
  country: string;
  _id?: string;
}

export interface OrgResponse {
  _id: string;
  registeredName: string;
  shortName: string;
  domain: string;
  contactEmail: string;
  accountType: string;
  permanentAddress: OrgAddress;
  onBoardingStatus: string;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
  slug: string;
  __v?: number;
  isTenantOrg?: boolean;
  parentOrgId?: string | null;
}

export interface UpdateOrgPayload {
  registeredName: string;
  shortName: string;
  contactEmail: string;
  permanentAddress: Partial<OrgAddress>;
  dataCollectionConsent: boolean;
}

/** SWR keys of the org reads, shared by the sidebar footer and the General page. */
export const ORG_KEYS = {
  details: [SWR_NS.org, 'details'],
  logo: [SWR_NS.org, 'logo'],
  metrics: [SWR_NS.org, 'metrics'],
} as const;

// ========================================
// API functions
// ========================================

export const OrgApi = {
  /** GET /api/v1/org — fetch current org details */
  async getOrg(): Promise<OrgResponse> {
    // The rail's footer reads the same org as the app starts: one request between them.
    return sharedGet<OrgResponse>(BASE_URL);
  },

  /** GET /api/v1/org/logo — download logo and return a data URL (null when the org has no logo, i.e. 204); throws when the read fails. */
  async getLogoUrl(): Promise<string | null> {
    // A failed read throws, so the cache asks again instead of keeping "no logo" for the session.
    const response = await apiClient.get<Blob>(`${BASE_URL}/logo`, {
      responseType: 'blob',
    });
    if (response.status === 204) {
      return null;
    }
    return response.data instanceof Blob ? await blobToDataUrl(response.data) : null;
  },

  /** PUT /api/v1/org/ — update org fields, then refetch the cached org (the response is an envelope). */
  async updateOrg(payload: UpdateOrgPayload): Promise<void> {
    await apiClient.put(`${BASE_URL}/`, payload);
    void mutate(ORG_KEYS.details);
  },

  /** PUT /api/v1/org/logo — upload org logo (multipart form); returns the stored logo, now also cached. */
  async uploadLogo(file: File): Promise<string | null> {
    const formData = new FormData();
    formData.append('file', file);
    await apiClient.put(`${BASE_URL}/logo`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    try {
      return await mutate(ORG_KEYS.logo, OrgApi.getLogoUrl(), { revalidate: false });
    } catch {
      // The upload worked; only the read-back failed. Mark the cached logo stale so it is read again.
      void mutate(ORG_KEYS.logo);
      return null;
    }
  },

  /** DELETE /api/v1/org/logo — remove org logo */
  async deleteLogo(): Promise<void> {
    await apiClient.delete(`${BASE_URL}/logo`);
    void mutate(ORG_KEYS.logo, null, { revalidate: false });
  },
};

export const MetricsApi = {
  /** GET /api/v1/configurationManager/metricsCollection */
  async getMetricsCollection(): Promise<MetricsConfig> {
    const { data } = await apiClient.get<MetricsConfig>(METRICS_BASE_URL);
    return data;
  },

  /** PUT /api/v1/configurationManager/metricsCollection/toggle */
  async toggleMetricsCollection(enabled: boolean): Promise<void> {
    await apiClient.put(METRICS_URL, { enableMetricCollection: enabled });
    void mutate(ORG_KEYS.metrics);
  },
};

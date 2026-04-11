import { apiClient } from '@/lib/api';

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
}

export interface UpdateOrgPayload {
  registeredName: string;
  shortName: string;
  contactEmail: string;
  permanentAddress: Partial<OrgAddress>;
  dataCollectionConsent: boolean;
}

// ========================================
// API functions
// ========================================

export const OrgApi = {
  /** GET /api/v1/org — fetch current org details */
  async getOrg(): Promise<OrgResponse> {
    const { data } = await apiClient.get<OrgResponse>(BASE_URL);
    return data;
  },

  /** GET /api/v1/org/logo — download logo and return a local blob URL */
  async getLogoUrl(): Promise<string | null> {
    try {
      const response = await apiClient.get<Blob>(`${BASE_URL}/logo`, {
        responseType: 'blob',
      });
      return URL.createObjectURL(response.data);
    } catch {
      return null;
    }
  },

  /** PUT /api/v1/org/ — update org fields */
  async updateOrg(payload: UpdateOrgPayload): Promise<OrgResponse> {
    const { data } = await apiClient.put<OrgResponse>(`${BASE_URL}/`, payload);
    return data;
  },

  /** PUT /api/v1/org/logo — upload org logo (multipart form) */
  async uploadLogo(file: File): Promise<void> {
    const formData = new FormData();
    formData.append('file', file);
    await apiClient.put(`${BASE_URL}/logo`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
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
  },
};

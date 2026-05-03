import { apiClient } from '@/lib/api';

const BASE = '/api/v1/configurationManager/platform';
const SETTINGS_URL = `${BASE}/settings`;
const AVAILABLE_FLAGS_URL = `${BASE}/feature-flags/available`;

const BYTES_PER_MB = 1024 * 1024;

// ========================================
// Types
// ========================================

/** Shape returned / accepted by the settings endpoint */
export interface PlatformSettingsPayload {
  fileUploadMaxSizeBytes: number;
  featureFlags: Record<string, boolean>;
}

/** A single available feature flag descriptor */
export interface AvailableFlag {
  key: string;
  label: string;
  description: string;
  defaultEnabled: boolean;
}

export interface AvailableFlagsResponse {
  flags: AvailableFlag[];
}

// ========================================
// Helpers
// ========================================

export const bytesToMb = (bytes: number): number => Math.round(bytes / BYTES_PER_MB);
export const mbToBytes = (mb: number): number => mb * BYTES_PER_MB;

// ========================================
// API
// ========================================

export const LabsApi = {
  /** GET /platform/settings — returns raw bytes */
  async getSettings(): Promise<PlatformSettingsPayload> {
    const { data } = await apiClient.get<PlatformSettingsPayload>(SETTINGS_URL);
    return data;
  },

  /** POST /platform/settings — accepts raw bytes */
  async saveSettings(payload: PlatformSettingsPayload): Promise<void> {
    await apiClient.post(SETTINGS_URL, payload, { suppressErrorToast: true });
  },

  /** GET /platform/feature-flags/available — list of toggleable flags */
  async getAvailableFlags(): Promise<AvailableFlag[]> {
    const { data } = await apiClient.get<AvailableFlagsResponse>(AVAILABLE_FLAGS_URL);
    return Array.isArray(data?.flags) ? data.flags : [];
  },
};

import { apiClient } from '@/lib/api';

const BASE = '/api/v1/configurationManager/platform';
const SETTINGS_URL = `${BASE}/settings`;
const AVAILABLE_FLAGS_URL = `${BASE}/feature-flags/available`;

const BYTES_PER_MB = 1024 * 1024;

// ========================================
// Types
// ========================================

/** The admin's answer to the one-time notice about durable agent chat (UX1). */
export type DurableDefaultChoice = 'adopted' | 'kept' | 'dismissed';
export type DurableDefaultNotice = 'pending' | DurableDefaultChoice;

/** Shape returned by the settings endpoint */
export interface PlatformSettingsPayload {
  fileUploadMaxSizeBytes: number;
  /** Every flag's effective value. */
  featureFlags: Record<string, boolean>;
  /** Only the flags an admin changed; the rest follow their release default. */
  featureFlagOverrides?: Record<string, boolean>;
  notices?: { durableAgentDefault?: DurableDefaultNotice };
}

/** What a save sends: only what changed. A flag set to its release default follows that default again. */
export interface PlatformSettingsChanges {
  fileUploadMaxSizeBytes?: number;
  featureFlags?: Record<string, boolean>;
  notices?: { durableAgentDefault: DurableDefaultChoice };
  /** What the page showed for each setting sent (UX2): the server refuses a save over someone else's newer choice. */
  expected?: { featureFlags?: Record<string, boolean>; fileUploadMaxSizeBytes?: number };
}

/** The server's reason code when someone else changed a setting this save decides (UX2). */
export const LABS_CHANGED = 'labs_changed';

/** A save refused because another admin changed what it decides since the page loaded. */
export function isLabsChanged(error: unknown): boolean {
  const failure = error as {
    statusCode?: number;
    originalError?: { response?: { data?: { error?: { reasonCode?: unknown } } } };
  } | null;
  return (
    failure?.statusCode === 409 &&
    failure.originalError?.response?.data?.error?.reasonCode === LABS_CHANGED
  );
}

/** A single available feature flag descriptor */
export interface AvailableFlag {
  key: string;
  label: string;
  description: string;
  defaultEnabled: boolean;
  /** Labs shows these only after "Show advanced feature flags" is opened. */
  advanced?: boolean;
  /** A switch of the agent harness, which is in beta; listed only where the install turned the harness on. */
  requiresAgentHarness?: boolean;
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

  /** POST /platform/settings — records only what is sent */
  async saveSettings(changes: PlatformSettingsChanges): Promise<void> {
    await apiClient.post(SETTINGS_URL, changes, { suppressErrorToast: true });
  },

  /** GET /platform/feature-flags/available — list of toggleable flags */
  async getAvailableFlags(): Promise<AvailableFlag[]> {
    const { data } = await apiClient.get<AvailableFlagsResponse>(AVAILABLE_FLAGS_URL);
    return Array.isArray(data?.flags) ? data.flags : [];
  },
};

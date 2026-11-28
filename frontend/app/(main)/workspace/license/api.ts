import { isAxiosError } from 'axios';
import { apiClient } from '@/lib/api';
import type { ProcessedError } from '@/lib/api';
import type { InstallResult, LicenseStatusView } from './types';

const LICENSE_URL = '/api/v1/license';

/**
 * Where keys are issued. A build may point it elsewhere with NEXT_PUBLIC_PROCUREMENT_URL.
 * The procurement app is built separately; it reads the query parameters `procurementLink` sends.
 */
export const PROCUREMENT_BASE_URL =
  process.env.NEXT_PUBLIC_PROCUREMENT_URL || 'https://procurement.pipeshub.com';

export const PROCUREMENT_PATHS = {
  /** A 14-day trial key. */
  trial: '/trial',
  /** The one 14-day extension of the upgrade grace an install may ask for. */
  extension: '/extension',
} as const;

/**
 * A procurement link carrying what the install should send: `url`, the web address users open
 * (a key is issued for that address), and for an extension `graceEndsAt`, when the grace ends.
 */
export function procurementLink(
  path: (typeof PROCUREMENT_PATHS)[keyof typeof PROCUREMENT_PATHS],
  params: Record<string, string | null | undefined>,
): string {
  const link = new URL(path, PROCUREMENT_BASE_URL);
  for (const [key, value] of Object.entries(params)) {
    if (value) link.searchParams.set(key, value);
  }
  return link.toString();
}

export const LicenseApi = {
  /** GET /api/v1/license — admin only. */
  async getStatus(): Promise<LicenseStatusView> {
    const { data } = await apiClient.get<LicenseStatusView>(LICENSE_URL);
    return data;
  },

  /** POST /api/v1/license — installs a key file, given as its parsed JSON. */
  async install(license: unknown): Promise<InstallResult> {
    const { data } = await apiClient.post<InstallResult>(
      LICENSE_URL,
      { license },
      { suppressErrorToast: true },
    );
    return data;
  },
};

/** The backend's reason for refusing a key (`LICENSE_BAD_SIGNATURE` and so on), or null. */
export function licenseErrorReason(error: unknown): string | null {
  const original = (error as ProcessedError | null)?.originalError;
  if (!isAxiosError(original)) return null;
  const code = (original.response?.data as { error?: { code?: unknown } } | undefined)?.error?.code;
  return typeof code === 'string' ? code.replace(/^HTTP_/, '') : null;
}

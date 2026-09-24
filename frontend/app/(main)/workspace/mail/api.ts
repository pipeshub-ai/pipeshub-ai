import { apiClient } from '@/lib/api';
import type { SmtpConfig } from './types';

// ============================================================
// Base URL
// ============================================================

const SMTP_URL = '/api/v1/configurationManager/smtpConfig';
/** Boolean-only status — unlike `SMTP_URL`, not admin-gated (see cm_routes.ts). */
const SMTP_STATUS_URL = '/api/v1/configurationManager/smtpConfig/status';

// ============================================================
// SMTP API
// ============================================================

export const SmtpApi = {
  /**
   * GET /api/v1/configurationManager/smtpConfig
   * Returns the current SMTP configuration, or an empty object if not yet set.
   */
  async getSmtpConfig(): Promise<SmtpConfig | null> {
    try {
      const { data } = await apiClient.get<SmtpConfig>(SMTP_URL);
      if (!data || (!data.host && !data.inherited)) return null;
      return data;
    } catch {
      return null;
    }
  },

  /**
   * POST /api/v1/configurationManager/smtpConfig
   * Creates or updates the SMTP configuration.
   */
  async saveSmtpConfig(payload: SmtpConfig): Promise<void> {
    await apiClient.post(SMTP_URL, payload);
  },

  /**
   * GET /api/v1/configurationManager/smtpConfig/status
   * Convenience: returns true if SMTP is configured. Unlike `getSmtpConfig`,
   * this doesn't require admin — safe for any authenticated user (e.g. a
   * member deciding whether the Invite button should be enabled).
   */
  async isConfigured(): Promise<boolean> {
    try {
      const { data } = await apiClient.get<{ configured: boolean }>(
        SMTP_STATUS_URL,
        { suppressErrorToast: true }
      );
      return !!data?.configured;
    } catch {
      return false;
    }
  },
};

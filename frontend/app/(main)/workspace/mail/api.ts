import { apiClient } from '@/lib/api';
import type { SmtpConfig } from './types';

// ============================================================
// Base URL
// ============================================================

const SMTP_URL = '/api/v1/configurationManager/smtpConfig';

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
      // API returns {} when not configured
      if (!data || !data.host) return null;
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
   * Convenience: returns true if SMTP is configured (has at least host + fromEmail).
   */
  async isConfigured(): Promise<boolean> {
    const config = await SmtpApi.getSmtpConfig();
    return !!(config?.host && config?.fromEmail);
  },
};

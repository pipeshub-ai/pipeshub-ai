import type { APIRequestContext } from '@playwright/test';

const SMTP_URL = '/api/v1/configurationManager/smtpConfig';

/**
 * Make sure the org has SMTP configured; inviting users is disabled until it is.
 *
 * When SMTP_HOST / SMTP_PORT are set (CI points these at Mailpit) it always
 * writes them, so a config left by an earlier run against another stack
 * cannot silently swallow the invite. They are dialled by the app server, not
 * the browser: against a containerised app use the Mailpit service name (for
 * example `mailpit` / `1025`), not a port published on this machine.
 * Returns false only when SMTP isn't configured and those variables don't say
 * how, so the caller can skip instead of failing on a disabled button.
 */
export async function ensureSmtpConfigured(api: APIRequestContext): Promise<boolean> {
  const status = await api.get(`${SMTP_URL}/status`);
  if (!status.ok()) {
    // An auth or server error is not "SMTP is off"; reporting it as that would
    // skip the invite tests instead of failing them.
    throw new Error(`Reading SMTP status failed: HTTP ${status.status()} ${await status.text()}`);
  }
  const host = process.env.SMTP_HOST;
  const port = process.env.SMTP_PORT;
  if (!host || !port) return Boolean((await status.json())?.configured);

  const username = process.env.SMTP_USERNAME ?? '';
  const response = await api.post(SMTP_URL, {
    data: {
      host,
      port: Number(port),
      username,
      password: process.env.SMTP_PASSWORD ?? '',
      fromEmail: process.env.SMTP_FROM_EMAIL || username || 'no-reply@example.com',
    },
  });
  if (!response.ok()) {
    throw new Error(`Configuring SMTP failed: HTTP ${response.status()} ${await response.text()}`);
  }
  return true;
}

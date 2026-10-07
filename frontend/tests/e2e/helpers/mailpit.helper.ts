import { request } from '@playwright/test';

/**
 * Reads mail caught by Mailpit, the SMTP sink the integration stack runs.
 * CI publishes its web API on the runner at port 8025; override with MAILPIT_URL.
 */
const MAILPIT_URL = (process.env.MAILPIT_URL || 'http://localhost:8025').replace(/\/$/, '');

type MailpitSummary = { ID: string; Subject: string; Created: string };

/**
 * Wait for the newest message addressed to `email` and return its text and HTML bodies.
 * Throws with what was (or wasn't) found, so a missing email reads as the failure it is.
 */
export async function waitForEmailTo(email: string, timeoutMs = 60_000): Promise<string> {
  const api = await request.newContext({ baseURL: MAILPIT_URL });
  try {
    const deadline = Date.now() + timeoutMs;
    let lastError = '';
    while (Date.now() < deadline) {
      const search = await api
        .get('/api/v1/search', { params: { query: `to:"${email}"` } })
        .catch((e: Error) => e);
      if (search instanceof Error) {
        lastError = `Mailpit unreachable at ${MAILPIT_URL}: ${search.message}`;
      } else if (!search.ok()) {
        lastError = `Mailpit search failed: HTTP ${search.status()}`;
      } else {
        const messages: MailpitSummary[] = (await search.json()).messages ?? [];
        if (messages.length > 0) {
          const newest = messages.sort((a, b) => b.Created.localeCompare(a.Created))[0];
          const message = await api.get(`/api/v1/message/${newest.ID}`);
          if (!message.ok()) throw new Error(`Mailpit message ${newest.ID}: HTTP ${message.status()}`);
          // Invites are sent as HTML only, with the link on a button; keep both parts.
          const body = (await message.json()) as { Text?: string; HTML?: string };
          return `${body.Text ?? ''}\n${body.HTML ?? ''}`;
        }
        lastError = `no email to ${email} yet`;
      }
      await new Promise((r) => setTimeout(r, 2_000));
    }
    throw new Error(`No email reached ${email} within ${timeoutMs / 1000}s (${lastError})`);
  } finally {
    await api.dispose();
  }
}

/** The account-setup path from an invite email, e.g. `/reset-password#token=…`. */
export function inviteLinkPath(emailText: string): string {
  const match = emailText.match(/\/reset-password#token=[A-Za-z0-9._-]+/);
  if (!match) throw new Error(`The invite email has no account-setup link:\n${emailText.slice(0, 500)}`);
  return match[0];
}

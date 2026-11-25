import { describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { streamHttpError } from '@/lib/api/stream-errors';
import { admissionRefusalText, DurableAdmissionError } from '../harness-errors';

/**
 * QA-AUTO F1: the cap on answers running at once (an admin's "Runs at once") refused a second chat with the words of
 * the per-minute rate limit ("You've sent a lot of messages in a short time. Wait a minute"), which waiting a minute
 * does not fix. The refusal's `reasonCode` names the quota, and each has its own words.
 */
describe('QA-AUTO F1 the active-runs refusal says what to do', () => {
  it('words the person cap, the workspace cap and the rate limit apart', async () => {
    await i18n.changeLanguage('en-US');
    const user = admissionRefusalText('HTTP_AGENT_HARNESS_QUOTA_EXCEEDED', 'active_runs_user');
    const org = admissionRefusalText('HTTP_AGENT_HARNESS_QUOTA_EXCEEDED', 'active_runs_org');
    const rate = admissionRefusalText('HTTP_AGENT_HARNESS_QUOTA_EXCEEDED', 'admission_rate_user');
    expect(user).toBe(i18n.t('chatStream.admission.quotaActiveUser'));
    expect(user).toMatch(/finish|stop/i);
    expect(user).not.toMatch(/minute/i);
    expect(org).toBe(i18n.t('chatStream.admission.quotaActiveOrg'));
    expect(rate).toBe(i18n.t('chatStream.admission.quota'));
    // An older server sends no reason: the rate words, as before.
    expect(admissionRefusalText('HTTP_AGENT_HARNESS_QUOTA_EXCEEDED')).toBe(rate);
    expect(new Set([user, org, rate]).size).toBe(3);
  });

  it('carries the reason from the 429 body to the error the chat shows', async () => {
    await i18n.changeLanguage('en-US');
    const response = new Response(
      JSON.stringify({ error: { code: 'HTTP_AGENT_HARNESS_QUOTA_EXCEEDED', message: 'Too many agent runs are active for this user', reasonCode: 'active_runs_user' } }),
      { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '5' } },
    );
    const refused = await streamHttpError(response);
    expect(refused.reasonCode).toBe('active_runs_user');
    const shown = new DurableAdmissionError(refused.code ?? null, refused.reasonCode ?? null);
    expect(shown.message).toBe(i18n.t('chatStream.admission.quotaActiveUser'));
  });
});

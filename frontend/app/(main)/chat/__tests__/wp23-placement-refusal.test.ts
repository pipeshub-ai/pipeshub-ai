import { describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { admissionRefusalText } from '../harness-errors';

/**
 * RES-12 (OWD-12, WP-23): a run this deployment may not place for the org says so plainly, with what the person
 * can do, and never the generic failure or where the org's runs are placed instead.
 */
describe('WP-23 placement refusal', () => {
  it('names the server and the admin, in the reader language, under both prefixes Node sends', async () => {
    await i18n.changeLanguage('en-US');
    const text = admissionRefusalText('HTTP_AGENT_HARNESS_RUN_PLACEMENT_REFUSED');
    expect(text).toBe(i18n.t('chatStream.admission.placement'));
    expect(text).toContain('server');
    expect(text).toContain('admin');
    expect(text).not.toMatch(/region/i);
    expect(admissionRefusalText('RUN_PLACEMENT_REFUSED')).toBe(text);
    expect(text).not.toBe(i18n.t('chatStream.admission.failed'));
    await i18n.changeLanguage('de-DE');
    expect(admissionRefusalText('RUN_PLACEMENT_REFUSED')).not.toBe(text);
    await i18n.changeLanguage('en-US');
  });
});

/**
 * WP-64: a chat request refused because today's loop would serve it without its gates (the agent runtime is turned
 * off, or the conversation's last task asked a question under a dry run, plan approval or checks) reads in the
 * reader's language and says who can fix it; any other refusal keeps its own words.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { StreamError } from '@/lib/api/stream-errors';
import { admissionRefusalText, refusedTurnText } from '../harness-errors';

const WORDS = "This agent's safety settings need the agent runtime, which is turned off for this workspace. Ask an admin to turn it on.";

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('WP-64 GATES_NEED_RUNTIME', () => {
  it('WP64-FE-11: the refused stream reads in plain words; another refusal keeps its own', () => {
    expect(refusedTurnText(new StreamError('server words', 409, 'HTTP_AGENT_HARNESS_GATES_NEED_RUNTIME'))).toBe(WORDS);
    expect(refusedTurnText(new StreamError('busy', 503, 'HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE'))).toBeNull();
    expect(refusedTurnText(new Error('network'))).toBeNull();
    expect(admissionRefusalText('HTTP_AGENT_HARNESS_GATES_NEED_RUNTIME')).toBe(WORDS);
  });

  it('WP64-FE-12: another language reads its own words', async () => {
    await i18n.changeLanguage('de-DE');
    expect(refusedTurnText({ code: 'HTTP_AGENT_HARNESS_GATES_NEED_RUNTIME' })).toMatch(/Agent-Laufzeit/);
    await i18n.changeLanguage('en-US');
  });
});

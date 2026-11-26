/** WP-85 (ADR-SEC-01, SEC-SC-01): an image no trusted key signed ends a run with words that say to ask the admin. */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { nextActionKind, runErrorText } from '../harness-errors';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('WP-85: image_untrusted', () => {
  it('reads as plain words and offers asking the admin', () => {
    expect(runErrorText('image_untrusted')).toBe(
      "This agent's sandbox image isn't signed by a key this server trusts, so nothing was started. Ask your admin.",
    );
    expect(nextActionKind('run', 'image_untrusted')).toBe('ask_admin');
  });
});

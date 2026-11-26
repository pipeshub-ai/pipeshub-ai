/**
 * R31GVISO-FU-3: a run its sandbox's memory limit ended (`out_of_memory`; under gVisor the whole sandbox ends) reads
 * "ran out of memory (limit N)" in every locale, from the live RUN_ERROR's details or the saved outcome, so a reload
 * says what the stream said. Without a limit the sentence stands alone; the next step is to change the request.
 */
import { afterAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { memoryLimitLabel, memoryLimitOf, nextActionKind, runErrorText } from '../harness-errors';
import { runFailureWords } from '../run-failure-words';
import { runOutcome } from '../run-outcome';

const LOCALES = ['en-US', 'en-IN', 'cs-CZ', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

describe('R32-OOM: an out-of-memory end names its limit in the reader’s language', () => {
  const start = i18n.language;
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it('OOM-UI-1: every locale names the limit from the details, and stands alone without one', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const named = runErrorText('out_of_memory', null, { memoryLimitMb: 4096 });
      expect(named, locale).toContain('4 GB');
      expect(named, locale).not.toMatch(/\{\{/);
      const bare = runErrorText('out_of_memory');
      expect(bare, locale).not.toBeNull();
      expect(bare, locale).not.toContain('GB');
    }
    await i18n.changeLanguage('en-US');
    expect(runErrorText('out_of_memory', null, { memoryLimitMb: 4096 })).toBe(
      "This task ran out of memory (limit 4 GB) and was stopped. Send a smaller task, or ask your admin about the coding engine's memory.",
    );
  });

  it('OOM-UI-2: the live frame’s details and the saved outcome read the same words', async () => {
    await i18n.changeLanguage('en-US');
    const live = runFailureWords('out_of_memory', null, { details: { memoryLimitMb: 1536 } });
    const saved = runFailureWords(
      'out_of_memory',
      runOutcome({ kind: 'failed', reason: 'out_of_memory', action: 'retry', sources: 0, kept: false, opening: false, memoryLimitMb: 1536 }),
    );
    expect(live).toContain('(limit 1536 MB)');
    expect(saved).toBe(live);
  });

  it('OOM-UI-3: a malformed limit is not one; the next step is to change the request', () => {
    expect(memoryLimitOf({ memoryLimitMb: 0 })).toBeNull();
    expect(memoryLimitOf({ memoryLimitMb: '4096' })).toBeNull();
    expect(memoryLimitOf(null)).toBeNull();
    expect(memoryLimitLabel(512)).toBe('512 MB');
    expect(nextActionKind('run', 'out_of_memory')).toBe('change_request');
    expect(runOutcome({ kind: 'failed', action: 'retry', sources: 0, kept: false, opening: false, memoryLimitMb: -1 }))
      .not.toHaveProperty('memoryLimitMb');
  });
});

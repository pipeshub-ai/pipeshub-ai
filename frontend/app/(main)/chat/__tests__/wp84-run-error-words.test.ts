/**
 * WP84-WORDS: two run endings the web app showed in the worker's or Node's English now have words in every locale.
 * `mcp_tool_withdrawn` (WP-79) names the servers from the frame's `servers` (or the saved message's `errorServers`),
 * never an id; a run the AI model provider ended (`provider_unavailable`, `rate_limited`, WP-77) says it is the
 * provider and to wait, then try again.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { oneClickStep, runErrorText } from '../harness-errors';

const LOCALES = ['en-US', 'en-IN', 'cs-CZ', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const ROOT = join(__dirname, '..', '..', '..', '..', '..');
const english = (key: string): string =>
  (JSON.parse(readFileSync(join(ROOT, 'frontend', 'lib', 'i18n', 'locales', 'en-US.json'), 'utf8')) as {
    chatStream: { runError: Record<string, string> };
  }).chatStream.runError[key] ?? '';
const VECTOR = JSON.parse(
  readFileSync(join(ROOT, 'contracts', 'harness', 'vectors', 'run-error-codes.json'), 'utf8'),
) as { codes: string[] };

describe('WP84-WORDS: run endings worded in the reader’s language', () => {
  const start = i18n.language;
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it('WP84-WORDS-7: a withdrawn tool names its server in every locale, never the worker’s English', async () => {
    const worker = "Atlassian no longer offers a tool this task was using, so the task stopped.";
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const one = runErrorText('mcp_tool_withdrawn', ['Atlassian']);
      expect(one, locale).toContain('Atlassian');
      expect(one, locale).not.toMatch(/\{\{|mcp@|inst-/);
      if (!locale.startsWith('en')) expect(one, locale).not.toContain(worker);
      const two = runErrorText('mcp_tool_withdrawn', ['Atlassian', 'GitHub']);
      expect(two, locale).toContain('Atlassian');
      expect(two, locale).toContain('GitHub');
      const none = runErrorText('mcp_tool_withdrawn');
      expect(none, locale).not.toBeNull();
      expect(none, locale).not.toMatch(/\{\{/);
    }
  });

  it('WP84-WORDS-8: in English the list reads naturally and the verb agrees', async () => {
    await i18n.changeLanguage('en-US');
    expect(runErrorText('mcp_tool_withdrawn', ['Atlassian'])).toMatch(/^Atlassian no longer offers a tool/);
    expect(runErrorText('mcp_tool_withdrawn', ['Atlassian', 'GitHub'])).toMatch(/^Atlassian and GitHub no longer offer a tool/);
    expect(runErrorText('mcp_tool_withdrawn', ['A', 'B', 'C'])).toMatch(/^A, B, and C no longer offer a tool/);
  });

  it('WP84-WORDS-9: a provider ending names the provider and waiting, in every locale', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      for (const code of ['provider_unavailable', 'rate_limited']) {
        const text = runErrorText(code);
        expect(text, `${locale} ${code}`).not.toBeNull();
        expect(text, `${locale} ${code}`).not.toMatch(/\{\{/);
      }
    }
    await i18n.changeLanguage('en-US');
    expect(runErrorText('provider_unavailable')).toContain('AI model provider');
    expect(runErrorText('rate_limited')).toContain('limiting requests');
  });

  it('WP84-WORDS-10: the codes are in the run-error vocabulary with their next step', () => {
    for (const code of ['mcp_tool_withdrawn', 'provider_unavailable', 'rate_limited']) {
      expect(VECTOR.codes, code).toContain(code);
    }
    expect(oneClickStep('run', 'mcp_tool_withdrawn')).toBe('retry');
    expect(oneClickStep('run', 'provider_unavailable')).toBe('wait_then_retry');
    expect(oneClickStep('run', 'rate_limited')).toBe('wait_then_retry');
    expect(english('mcpToolWithdrawnUnnamed')).toMatch(/connected app/);
  });
});

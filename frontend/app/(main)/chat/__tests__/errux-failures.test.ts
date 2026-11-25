import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, describe, expect, it, vi } from 'vitest';
import { i18n } from '@/lib/i18n';
import { TOOL_ERROR_CODES } from '@/lib/contracts/harness/generated/core/enums';
import { ANSWER_ERROR_CODES } from '@/lib/contracts/harness/generated/ext/pipeshub/enums';
import type { SSEEvent } from '@/lib/api';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import { answerErrorText, providerWaitText, runErrorText, toolFailureText } from '../harness-errors';
import type { MessagePart } from '../types';

/** EU-7, EU-8, EU-11 (ERR-UX): failures and the provider's wait in the reader's words, in every locale. */

const LOCALES = ['de-DE', 'en-IN', 'en-US', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const DETAIL = 'ConnectError: All connection attempts failed';

const leaves = (locale: string, path: string): string[] => {
  let node: unknown = JSON.parse(
    readFileSync(join(__dirname, '..', '..', '..', '..', 'lib', 'i18n', 'locales', `${locale}.json`), 'utf8'),
  );
  for (const key of path.split('.')) node = (node as Record<string, unknown>)[key];
  return Object.values(node as Record<string, string>);
};

const words = (template: string): RegExp =>
  new RegExp(`^${template.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\{\\\{\w+\\\}\\\}/g, '.+')}$`);

const inEveryLocale = async (check: (locale: string) => void): Promise<void> => {
  for (const locale of LOCALES) {
    await i18n.changeLanguage(locale);
    check(locale);
  }
};

describe('ERR-UX: failures in the reader\'s words', () => {
  const start = i18n.language;
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it.each(TOOL_ERROR_CODES)('EU-7: tool failure %s is worded in every locale, naming the tool', async (code) => {
    await inEveryLocale((locale) => {
      const text = toolFailureText({ errorCode: code }, 'Slack');
      expect(text, `${locale} ${code}`).toContain('Slack');
      expect(leaves(locale, 'chatStream.toolFailure').some((t) => words(t).test(text)), `${locale} ${code}`).toBe(true);
    });
  });

  it('EU-7: a write whose outcome is definite says nothing was sent; a failure without a code reads failed', async () => {
    await i18n.changeLanguage('en-US');
    expect(toolFailureText({ errorCode: 'unavailable', nothingSent: true }, 'Slack')).toBe(
      'Slack could not be reached. Nothing was sent.',
    );
    expect(toolFailureText({}, 'Slack')).toBe('Slack failed.');
    expect(toolFailureText({ errorCode: DETAIL }, 'Slack')).toBe('Slack failed.');
  });

  it.each(ANSWER_ERROR_CODES)('EU-8: error answer %s is worded in every locale', async (code) => {
    await inEveryLocale((locale) => {
      const text = answerErrorText(code);
      expect(text, `${locale} ${code}`).not.toBeNull();
      expect(leaves(locale, 'chatStream.answerError'), `${locale} ${code}`).toContain(text);
    });
  });

  it('EU-23: a run whose MCP servers could not be listed is worded in every locale', async () => {
    await inEveryLocale((locale) => {
      const text = runErrorText('mcp_unavailable');
      expect(text, locale).not.toBeNull();
      expect(leaves(locale, 'chatStream.runError'), locale).toContain(text);
    });
  });

  it('EU-8: an unknown code is not worded', () => {
    expect(answerErrorText('provider says: invalid_request_error')).toBeNull();
    expect(answerErrorText(undefined)).toBeNull();
  });

  it('EU-11: the provider wait counts down from the reader\'s clock', async () => {
    const now = Date.parse('2026-10-10T12:00:00.000Z');
    await inEveryLocale((locale) => {
      const text = providerWaitText('2026-10-10T12:00:20.000Z', now);
      expect(text, locale).toContain('20');
      expect(providerWaitText('2026-10-10T11:59:00.000Z', now), locale).toBe(i18n.t('chatStream.providerWaitNow'));
      expect(providerWaitText(null, now), locale).toBe(i18n.t('chatStream.providerWaitNow'));
    });
  });
});

const frame = (type: string, fields: Record<string, unknown> = {}): SSEEvent => ({ event: type, data: { type, ...fields } });

const callbacks = () => {
  const spies = { onStatus: vi.fn(), onChunk: vi.fn(), onAnswerFinal: vi.fn(), onParts: vi.fn() };
  return { spies, callbacks: spies as unknown as StreamMessageCallbacks };
};

describe('ERR-UX: the live stream', () => {
  it('EU-7: a failed TOOL_CALL_RESULT keeps its code, never its detail', async () => {
    const { spies, callbacks: cb } = callbacks();
    const handle = createAGUIEventHandler(cb);
    handle(frame('TOOL_CALL_START', { toolCallId: 'c1', toolCallName: 'slack__send_message' }));
    handle(frame('TOOL_CALL_RESULT', { toolCallId: 'c1', status: 'failed', content: '', errorCode: 'unavailable', nothingSent: true }));
    const parts = spies.onParts.mock.lastCall?.[0] as MessagePart[];
    const tool = parts.find((p) => p.toolCallId === 'c1');
    expect(tool).toMatchObject({ status: 'failed', errorCode: 'unavailable', nothingSent: true, resultPreview: '' });
  });

  it('EU-8: a final error answer is replaced by the reader\'s words for its code', async () => {
    await i18n.changeLanguage('de-DE');
    const { spies, callbacks: cb } = callbacks();
    const handle = createAGUIEventHandler(cb);
    handle(frame('STATE_SNAPSHOT', {
      snapshot: { final: true, answer: 'The AI model took too long to answer. Please try again.', answerMatchType: 'Error', errorCode: 'timeout' },
    }));
    expect(spies.onChunk).toHaveBeenLastCalledWith({ chunk: '', accumulated: i18n.t('chatStream.answerError.timeout'), citations: [] });
    expect(spies.onAnswerFinal).toHaveBeenCalled();
    await i18n.changeLanguage('en-US');
  });

  it('EU-11: waiting_provider shows the wait from the injected clock', async () => {
    await i18n.changeLanguage('en-US');
    const { spies, callbacks: cb } = callbacks();
    const handle = createAGUIEventHandler(cb, undefined, { now: () => Date.parse('2026-10-10T12:00:00.000Z') });
    handle(frame('STATE_SNAPSHOT', { snapshot: { status: 'waiting_provider', retry_at: '2026-10-10T12:00:20.000Z' } }));
    expect(spies.onStatus).toHaveBeenLastCalledWith({
      status: 'waiting_provider',
      message: 'Waiting for the AI model (busy or too many requests), retrying in 20 s…',
    });
  });

  it('QA31F: waiting_sandbox says the run waits for a free sandbox', async () => {
    await i18n.changeLanguage('en-US');
    const { spies, callbacks: cb } = callbacks();
    const handle = createAGUIEventHandler(cb);
    handle(frame('STATE_SNAPSHOT', { snapshot: { status: 'waiting_sandbox', retry_at: '2026-10-10T12:00:20.000Z' } }));
    expect(spies.onStatus).toHaveBeenLastCalledWith({
      status: 'waiting_sandbox',
      message: 'Waiting for a free sandbox…',
    });
  });

  it('CH-09: waiting_store says the run carries on once file storage is back', async () => {
    await i18n.changeLanguage('en-US');
    const { spies, callbacks: cb } = callbacks();
    const handle = createAGUIEventHandler(cb);
    handle(frame('STATE_SNAPSHOT', { snapshot: { status: 'waiting_store', retry_at: '2026-10-10T12:00:20.000Z' } }));
    expect(spies.onStatus).toHaveBeenLastCalledWith({
      status: 'waiting_store',
      message: 'File storage is not responding; the task will carry on once it is back…',
    });
  });
});

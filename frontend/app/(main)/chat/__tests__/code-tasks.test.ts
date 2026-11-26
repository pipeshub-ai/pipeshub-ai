/**
 * Terminal text, artifact cards updated in place (AR-1), stage 1's retired copy (SR-29), and the AG-UI handler
 * folding engine frames into the answer's engine part while routing artifact cards (AH-1, CTL-4).
 */
import { describe, expect, it, vi } from 'vitest';
import { appendTerminal, plainTerminalText, withArtifactCard } from '../code-tasks';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { ChatArtifact, MessagePart, SSEEngineProgressEvent } from '../types';
import deDE from '@/lib/i18n/locales/de-DE.json';
import enIN from '@/lib/i18n/locales/en-IN.json';
import enUS from '@/lib/i18n/locales/en-US.json';
import esES from '@/lib/i18n/locales/es-ES.json';
import hiIN from '@/lib/i18n/locales/hi-IN.json';
import koKR from '@/lib/i18n/locales/ko-KR.json';
import zhCN from '@/lib/i18n/locales/zh-CN.json';
import zhSG from '@/lib/i18n/locales/zh-SG.json';
import zhTW from '@/lib/i18n/locales/zh-TW.json';

function frame(kind: string, text = '', data: Record<string, unknown> = {}): SSEEngineProgressEvent {
  return { tool: 'code_task', taskId: 'ct-1', kind, text, data };
}

describe('terminal text', () => {
  it('drops escape sequences and control characters, and keeps the newest output within both bounds', () => {
    expect(plainTerminalText('\u001b[31mred\u001b[0m \u001b]0;title\u0007ok\r\n')).toBe('red ok\n');
    expect(plainTerminalText('a\u0000b\u0007c\td')).toBe('abc\td');
    const long = appendTerminal('', 'x'.repeat(110), { chars: 100, lines: 50 });
    expect(long).toEqual({ terminal: 'x'.repeat(100), truncated: true });
    const lines = Array.from({ length: 60 }, (_, i) => `line ${i}`).join('\n');
    const many = appendTerminal('', `${lines}\n`, { chars: 10_000, lines: 50 });
    expect(many.terminal.split('\n')).toHaveLength(51);
    expect(many.terminal.endsWith('line 59\n')).toBe(true);
  });

  it('PF-17: the line bound keeps exactly the newest lines, at every edge (as splitting the whole text did)', () => {
    const reference = (text: string, lines: number) => {
      const all = text.split('\n');
      return all.length > lines + 1 ? { terminal: all.slice(all.length - lines - 1).join('\n'), truncated: true } : { terminal: text, truncated: false };
    };
    const cases = ['', '\n', '\n\n\n', 'a', 'a\n', '\na', '\n\na\nb', 'a\nb\nc\n', '\n\n\n\n\nend'];
    for (let seed = 0; seed < 200; seed += 1) {
      cases.push(Array.from({ length: seed % 17 }, (_, i) => ((seed * 31 + i * 7) % 5 === 0 ? '' : `l${i}`)).join('\n'));
    }
    for (const text of cases) {
      for (const lines of [0, 1, 2, 3, 5]) {
        expect(appendTerminal('', text, { chars: 1_000, lines }), JSON.stringify({ text, lines })).toEqual(reference(text, lines));
      }
    }
  });
});

describe('artifact cards update in place (AR-1)', () => {
  const card = (id: string, version = 1): ChatArtifact => ({
    id,
    fileName: `${id}.csv`,
    mimeType: 'text/csv',
    sizeBytes: 1,
    downloadUrl: '',
    artifactType: 'SPREADSHEET',
    version,
  });

  it('adds a new card, replaces one with the same id where it stands, and removes a discarded one', () => {
    let cards = withArtifactCard([], card('a'), 'draft');
    cards = withArtifactCard(cards, card('b'), undefined);
    expect(cards.map((c) => [c.id, c.lifecycle])).toEqual([
      ['a', 'draft'],
      ['b', undefined],
    ]);
    cards = withArtifactCard(cards, card('a', 2), 'final');
    expect(cards.map((c) => [c.id, c.version, c.lifecycle])).toEqual([
      ['a', 2, undefined],
      ['b', 1, undefined],
    ]);
    expect(withArtifactCard(cards, card('b'), 'discarded').map((c) => c.id)).toEqual(['a']);
    expect(withArtifactCard(cards, card('zzz'), 'discarded')).toBe(cards);
  });
});

describe('stage 1\'s retired copy (SR-29)', () => {
  const locales = { deDE, enIN, enUS, esES, hiIN, koKR, zhCN, zhSG, zhTW };
  const lookup = (catalogue: unknown, key: string): unknown =>
    key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], catalogue);

  it.each(['busy', 'grantTooLarge', 'unavailable'])('SR-29: stage 1\'s reason.%s copy is gone from every locale', (key) => {
    for (const [name, catalogue] of Object.entries(locales)) {
      expect(lookup(catalogue, `codeTaskCard.reason.${key}`), name).toBeUndefined();
      expect(lookup(catalogue, 'codeTaskCard.status.refused'), name).toBeUndefined();
    }
  });
});

describe('the AG-UI handler (AH-1, CTL-4)', () => {
  it('folds engine frames into the engine part, ignores a malformed one, and keeps an artifact card\'s lifecycle', () => {
    let parts: MessagePart[] = [];
    const onArtifact = vi.fn();
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = next), onArtifact });
    const value = frame('command', 'ls');
    handle({ event: 'CUSTOM', data: { type: 'CUSTOM', name: 'engine_progress', value } });
    handle({ event: 'CUSTOM', data: { type: 'CUSTOM', name: 'engine_progress', value: 'not an object' } });
    handle({
      event: 'STATE_DELTA',
      data: {
        type: 'STATE_DELTA',
        delta: [{ op: 'add', path: '/artifacts/-', value: { artifactId: 'a', fileName: 'a.csv', lifecycle: 'draft' } }],
      },
    });
    expect(parts.filter((part) => part.type === 'engine_task')).toEqual([{ type: 'engine_task', taskId: 'ct-1', frames: [value] }]);
    expect(onArtifact.mock.calls[0]?.[0]).toMatchObject({ artifactId: 'a', lifecycle: 'draft' });
  });
});

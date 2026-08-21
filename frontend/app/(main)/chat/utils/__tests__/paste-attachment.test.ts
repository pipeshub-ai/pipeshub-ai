import { describe, it, expect } from 'vitest';
import {
  isLargePaste,
  createPastedTextFile,
  generatePastePreview,
  isPastedTextAttachment,
  formatPasteMeta,
  DEFAULT_PASTE_ATTACHMENT_CONFIG,
  type PasteAttachmentConfig,
} from '../paste-attachment';

/**
 * jsdom's `File`/`Blob` polyfill doesn't implement `.text()` (unlike real
 * browsers), so tests read content via `FileReader` instead. Production
 * code (`chat-input.tsx`, `text-preview-dialog.tsx`) still uses `.text()`
 * directly since it only ever runs in real browsers.
 */
function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

describe('isLargePaste', () => {
  it('is false for empty text', () => {
    expect(isLargePaste('')).toBe(false);
  });

  it('is false for short single-line text', () => {
    expect(isLargePaste('hello world')).toBe(false);
  });

  it('is true when character count exceeds the threshold', () => {
    const text = 'x'.repeat(DEFAULT_PASTE_ATTACHMENT_CONFIG.characterThreshold + 1);
    expect(isLargePaste(text)).toBe(true);
  });

  it('is false exactly at the character threshold (exclusive)', () => {
    const text = 'x'.repeat(DEFAULT_PASTE_ATTACHMENT_CONFIG.characterThreshold);
    expect(isLargePaste(text)).toBe(false);
  });

  it('is true when line count exceeds the threshold even under the character threshold', () => {
    const lines = Array.from({ length: DEFAULT_PASTE_ATTACHMENT_CONFIG.lineThreshold + 1 }, (_, i) => `item ${i}`);
    const text = lines.join('\n');
    expect(text.length).toBeLessThan(DEFAULT_PASTE_ATTACHMENT_CONFIG.characterThreshold);
    expect(isLargePaste(text)).toBe(true);
  });

  it('is false exactly at the line threshold (exclusive)', () => {
    const lines = Array.from({ length: DEFAULT_PASTE_ATTACHMENT_CONFIG.lineThreshold }, (_, i) => `item ${i}`);
    expect(isLargePaste(lines.join('\n'))).toBe(false);
  });

  it('respects a custom config', () => {
    const config: PasteAttachmentConfig = {
      characterThreshold: 10,
      lineThreshold: 2,
      maxPasteSize: 1024,
      fileNamePrefix: 'pasted-text-',
    };
    expect(isLargePaste('short', config)).toBe(false);
    expect(isLargePaste('this is definitely long', config)).toBe(true);
    expect(isLargePaste('a\nb\nc', config)).toBe(true);
  });
});

describe('generatePastePreview', () => {
  it('returns the trimmed first non-blank line', () => {
    expect(generatePastePreview('  First line  \nSecond line')).toBe('First line');
  });

  it('skips leading blank lines', () => {
    expect(generatePastePreview('\n\n   \nActual content\nmore')).toBe('Actual content');
  });

  it('falls back to a flattened view when every line is blank', () => {
    expect(generatePastePreview('   \n  \n ')).toBe('');
  });

  it('truncates long first lines with an ellipsis', () => {
    const longLine = 'a'.repeat(250);
    const preview = generatePastePreview(longLine, 200);
    expect(preview.length).toBe(201);
    expect(preview.endsWith('…')).toBe(true);
  });

  it('does not truncate a first line under the max length', () => {
    expect(generatePastePreview('short line', 200)).toBe('short line');
  });
});

describe('createPastedTextFile', () => {
  it('creates a text/plain File with the pasted content', async () => {
    const file = createPastedTextFile('hello world');
    expect(file.type).toBe('text/plain');
    expect(await readFileText(file)).toBe('hello world');
  });

  it('names the file with the pasted-text-<timestamp>.txt convention', () => {
    const fixedDate = new Date(2026, 0, 15, 9, 5, 3); // 2026-01-15 09:05:03
    const file = createPastedTextFile('content', DEFAULT_PASTE_ATTACHMENT_CONFIG, fixedDate);
    expect(file.name).toBe('pasted-text-2026-01-15-09-05-03.txt');
  });

  it('truncates content exceeding maxPasteSize and appends a notice', async () => {
    const config: PasteAttachmentConfig = { ...DEFAULT_PASTE_ATTACHMENT_CONFIG, maxPasteSize: 10 };
    const file = createPastedTextFile('0123456789ABCDEF', config);
    const text = await readFileText(file);
    expect(text.startsWith('0123456789')).toBe(true);
    expect(text).toContain('truncated');
  });

  it('does not append a truncation notice under the size limit', async () => {
    const file = createPastedTextFile('small content');
    expect(await readFileText(file)).toBe('small content');
  });
});

describe('isPastedTextAttachment', () => {
  it('is true for a composer file tagged with source paste-text', () => {
    expect(isPastedTextAttachment({ source: 'paste-text', name: 'whatever.txt' })).toBe(true);
  });

  it('is false for a composer file tagged with a different source', () => {
    expect(isPastedTextAttachment({ source: 'upload', name: 'pasted-text-2026-01-01-00-00-00.txt' })).toBe(false);
  });

  it('detects a server AttachmentRef by filename convention + text mime', () => {
    expect(
      isPastedTextAttachment({ recordName: 'pasted-text-2026-01-15-09-05-03.txt', mimeType: 'text/plain' }),
    ).toBe(true);
  });

  it('rejects a filename match with a non-text mime type', () => {
    expect(
      isPastedTextAttachment({ recordName: 'pasted-text-2026-01-15-09-05-03.txt', mimeType: 'application/pdf' }),
    ).toBe(false);
  });

  it('rejects a regular uploaded .txt file that does not match the naming convention', () => {
    expect(isPastedTextAttachment({ recordName: 'notes.txt', mimeType: 'text/plain' })).toBe(false);
  });

  it('rejects an empty input', () => {
    expect(isPastedTextAttachment({})).toBe(false);
  });
});

describe('formatPasteMeta', () => {
  it('formats small counts verbatim', () => {
    expect(formatPasteMeta(120, 5)).toBe('120 chars · 5 lines');
  });

  it('formats a single line without pluralizing', () => {
    expect(formatPasteMeta(50, 1)).toBe('50 chars · 1 line');
  });

  it('abbreviates large character counts with a "k" suffix', () => {
    expect(formatPasteMeta(12500, 40)).toBe('12.5k chars · 40 lines');
  });
});

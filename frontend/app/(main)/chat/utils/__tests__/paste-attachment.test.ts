import { describe, it, expect } from 'vitest';
import {
  isLargePaste,
  createPastedTextFile,
  generatePastePreview,
  isPastedTextAttachment,
  formatPasteCount,
  DEFAULT_PASTE_ATTACHMENT_CONFIG,
  type PasteAttachmentConfig,
} from '../paste-attachment';

/**
 * jsdom's `File`/`Blob` polyfill doesn't implement `.text()` (unlike real
 * browsers), so tests read content via `FileReader` instead. Production
 * code (`chat-input.tsx`, `text-preview-dialog.tsx`) still uses `.text()`
 * directly since it only ever runs in real browsers.
 */
/** Mirrors DEFAULT_PASTE_ATTACHMENT_CONFIG.truncationNotice. */
const TRUNCATION_NOTICE_TEXT =
  '\n\n[Content truncated \u2014 pasted text exceeded the maximum attachment size.]';

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

  // The notice is charged to the same budget, so a usable maxPasteSize has to
  // exceed it; these use `NOTICE_BYTES + n` to leave exactly n bytes of content.
  const NOTICE_BYTES = new TextEncoder().encode(TRUNCATION_NOTICE_TEXT).length;
  const budgetFor = (contentBytes: number) => NOTICE_BYTES + contentBytes;

  it('truncates content exceeding maxPasteSize and appends a notice', async () => {
    const config: PasteAttachmentConfig = {
      ...DEFAULT_PASTE_ATTACHMENT_CONFIG,
      maxPasteSize: budgetFor(15),
    };
    const file = createPastedTextFile('0123456789'.repeat(20), config);
    const text = await readFileText(file);
    expect(text.startsWith('012345678901234')).toBe(true);
    expect(text).toContain('truncated');
    expect(new TextEncoder().encode(text).length).toBeLessThanOrEqual(config.maxPasteSize);
  });

  it('keeps the whole file within maxPasteSize, notice included', async () => {
    const config: PasteAttachmentConfig = {
      ...DEFAULT_PASTE_ATTACHMENT_CONFIG,
      maxPasteSize: budgetFor(40),
    };
    const file = createPastedTextFile('x'.repeat(5000), config);
    const text = await readFileText(file);
    expect(text).toContain('truncated');
    expect(new TextEncoder().encode(text).length).toBeLessThanOrEqual(config.maxPasteSize);
    expect(file.size).toBeLessThanOrEqual(config.maxPasteSize);
  });

  it('measures the budget in UTF-8 bytes, not UTF-16 code units', async () => {
    // Each CJK char is 3 UTF-8 bytes but 1 code unit; a code-unit budget would
    // keep 12 of them (36 bytes) instead of the 4 that fit.
    const config: PasteAttachmentConfig = {
      ...DEFAULT_PASTE_ATTACHMENT_CONFIG,
      maxPasteSize: budgetFor(12),
    };
    const file = createPastedTextFile('\u3042'.repeat(40), config);
    const text = await readFileText(file);
    expect(text.replace(TRUNCATION_NOTICE_TEXT, '')).toBe('\u3042'.repeat(4));
    expect(new TextEncoder().encode(text).length).toBeLessThanOrEqual(config.maxPasteSize);
  });

  it('does not split a surrogate pair when truncating', async () => {
    // 6 bytes of content budget lands mid-emoji (4 bytes each); the partial one
    // is dropped rather than emitted as a replacement character.
    const config: PasteAttachmentConfig = {
      ...DEFAULT_PASTE_ATTACHMENT_CONFIG,
      maxPasteSize: budgetFor(6),
    };
    const file = createPastedTextFile('\u{1F600}'.repeat(30), config);
    const text = await readFileText(file);
    const body = text.replace(TRUNCATION_NOTICE_TEXT, '');
    expect(body).toBe('\u{1F600}');
    expect(body).not.toContain('\uFFFD');
    expect(new TextEncoder().encode(text).length).toBeLessThanOrEqual(config.maxPasteSize);
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

  it('is false for a pasted image/file, which UploadedFileSource tags as plain "paste"', () => {
    expect(isPastedTextAttachment({ source: 'paste', name: 'screenshot.png', type: 'image/png' })).toBe(false);
  });

  it('detects a persisted AttachmentRef carrying the wire source paste-text', () => {
    expect(
      isPastedTextAttachment({
        source: 'paste-text',
        recordName: 'pasted-text-2026-01-15-09-05-03.txt',
        mimeType: 'text/plain',
      }),
    ).toBe(true);
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

describe('formatPasteCount', () => {
  it('formats small counts verbatim', () => {
    expect(formatPasteCount(120)).toBe('120');
  });

  it('abbreviates large counts with a "k" suffix', () => {
    expect(formatPasteCount(12500)).toBe('12.5k');
  });

  it('abbreviates at exactly the 1000 boundary', () => {
    expect(formatPasteCount(1000)).toBe('1.0k');
  });
});

import {
  CHAT_PASTE_CHARACTER_THRESHOLD,
  CHAT_PASTE_FILENAME_PREFIX,
  CHAT_PASTE_LINE_THRESHOLD,
  CHAT_PASTE_MAX_SIZE_BYTES,
} from '../constants';

/**
 * Config for the large-paste → attachment conversion. Defaults mirror
 * ChatGPT (character threshold) and Coder's Agents chat (line threshold) —
 * see the plan's "Industry Analysis" section for sourcing.
 */
export interface PasteAttachmentConfig {
  characterThreshold: number;
  lineThreshold: number;
  maxPasteSize: number;
  fileNamePrefix: string;
}

export const DEFAULT_PASTE_ATTACHMENT_CONFIG: PasteAttachmentConfig = {
  characterThreshold: CHAT_PASTE_CHARACTER_THRESHOLD,
  lineThreshold: CHAT_PASTE_LINE_THRESHOLD,
  maxPasteSize: CHAT_PASTE_MAX_SIZE_BYTES,
  fileNamePrefix: CHAT_PASTE_FILENAME_PREFIX,
};

/** Notice appended to the file body when a paste exceeds `maxPasteSize`. */
const TRUNCATION_NOTICE = '\n\n[Content truncated — pasted text exceeded the maximum attachment size.]';

function countLines(text: string): number {
  if (text.length === 0) return 0;
  return text.split('\n').length;
}

/**
 * True when `text` is large enough to warrant collapsing into an attachment
 * chip instead of inserting it inline into the textarea — either dimension
 * (character count OR line count) crossing its threshold is sufficient, so
 * a long list of short lines is caught even under the character threshold.
 */
export function isLargePaste(
  text: string,
  config: PasteAttachmentConfig = DEFAULT_PASTE_ATTACHMENT_CONFIG,
): boolean {
  if (!text) return false;
  return text.length > config.characterThreshold || countLines(text) > config.lineThreshold;
}

/**
 * Short, single-line excerpt for the chip label. Prefers the first
 * non-blank line (trimmed); falls back to a whitespace-collapsed view of
 * the whole text when the paste starts with blank lines.
 */
export function generatePastePreview(text: string, maxLength = 200): string {
  const truncate = (s: string) => (s.length > maxLength ? `${s.slice(0, maxLength)}…` : s);

  const firstLine = text.split('\n').find((line) => line.trim().length > 0);
  if (firstLine) return truncate(firstLine.trim());

  const flattened = text.replace(/\s+/g, ' ').trim();
  return truncate(flattened);
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** `pasted-text-YYYY-MM-DD-HH-MM-SS.txt` — also the pattern `isPastedTextAttachment` matches on. */
function formatPasteTimestamp(date: Date): string {
  return [
    date.getFullYear(),
    pad2(date.getMonth() + 1),
    pad2(date.getDate()),
    pad2(date.getHours()),
    pad2(date.getMinutes()),
    pad2(date.getSeconds()),
  ].join('-');
}

/**
 * Builds the synthetic `text/plain` File uploaded through the existing
 * attachment pipeline. Content is truncated (with a visible notice) rather
 * than rejected outright so oversized pastes still degrade gracefully.
 */
export function createPastedTextFile(
  text: string,
  config: PasteAttachmentConfig = DEFAULT_PASTE_ATTACHMENT_CONFIG,
  now: Date = new Date(),
): File {
  const overBudget = text.length > config.maxPasteSize;
  const content = overBudget
    ? text.slice(0, config.maxPasteSize) + TRUNCATION_NOTICE
    : text;
  const filename = `${config.fileNamePrefix}${formatPasteTimestamp(now)}.txt`;
  return new File([content], filename, { type: 'text/plain' });
}

/** Matches the exact filename convention `createPastedTextFile` generates. */
const PASTE_FILENAME_PATTERN = /^pasted-text-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.txt$/i;

/**
 * Detects a pasted-text attachment from either shape we encounter it in:
 *  - a composer-local `UploadedFile` (checked via the `source` tag we set
 *    ourselves at paste time — fast and definitive), or
 *  - a server `AttachmentRef` / persisted `ConversationMessage.attachments`
 *    entry (no reliable origin field yet — falls back to the filename
 *    convention + text-like MIME check, the same defense-in-depth pattern
 *    used server-side for LLM-context inlining).
 */
export function isPastedTextAttachment(input: {
  source?: string;
  name?: string;
  recordName?: string;
  mimeType?: string;
  type?: string;
}): boolean {
  if (input.source === 'paste-text') return true;
  // A composer-local `UploadedFile` always carries an explicit `source` — trust
  // it outright rather than falling through to the filename heuristic below,
  // which exists only for server shapes that never track origin at all.
  if (input.source !== undefined) return false;

  const name = input.name ?? input.recordName ?? '';
  if (!PASTE_FILENAME_PATTERN.test(name)) return false;

  const mime = input.mimeType ?? input.type ?? '';
  return mime === '' || mime === 'text/plain' || mime.startsWith('text/');
}

/** Compact "N chars · M lines" label for the chip subtitle. */
export function formatPasteMeta(charCount: number, lineCount: number): string {
  const chars = charCount >= 1000 ? `${(charCount / 1000).toFixed(1)}k chars` : `${charCount} chars`;
  const lines = `${lineCount} ${lineCount === 1 ? 'line' : 'lines'}`;
  return `${chars} · ${lines}`;
}

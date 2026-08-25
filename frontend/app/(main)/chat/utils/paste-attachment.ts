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
  /** Appended to the body when a paste is truncated. Injected so the UI layer
   *  supplies a translated string; this file holds no user-facing copy. */
  truncationNotice: string;
}

/** English fallback for `PasteAttachmentConfig.truncationNotice`; callers that
 *  render to a user pass a translated string instead. */
const DEFAULT_TRUNCATION_NOTICE =
  '\n\n[Content truncated — pasted text exceeded the maximum attachment size.]';

export const DEFAULT_PASTE_ATTACHMENT_CONFIG: PasteAttachmentConfig = {
  characterThreshold: CHAT_PASTE_CHARACTER_THRESHOLD,
  lineThreshold: CHAT_PASTE_LINE_THRESHOLD,
  maxPasteSize: CHAT_PASTE_MAX_SIZE_BYTES,
  fileNamePrefix: CHAT_PASTE_FILENAME_PREFIX,
  truncationNotice: DEFAULT_TRUNCATION_NOTICE,
};

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
 * Trims a UTF-8 buffer to at most `maxBytes` without splitting a multi-byte
 * sequence. Slicing the source string instead would measure UTF-16 code units
 * against a byte budget (3x off for CJK) and could cut a surrogate pair in
 * half, which encodes back out as a replacement character.
 */
function truncateUtf8(bytes: Uint8Array, maxBytes: number): string {
  let end = Math.min(maxBytes, bytes.length);
  while (end > 0 && (bytes[end] & 0xc0) === 0x80) end -= 1;
  return new TextDecoder().decode(bytes.subarray(0, end));
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
  const encoder = new TextEncoder();
  const bytes = encoder.encode(text);
  const overBudget = bytes.length > config.maxPasteSize;
  // The notice is part of the file, so it comes out of the same budget —
  // `maxPasteSize` bounds the File, not just the text kept from the paste. A
  // budget smaller than the notice itself is the one case that can't hold.
  const noticeBytes = encoder.encode(config.truncationNotice).length;
  const contentBudget = Math.max(0, config.maxPasteSize - noticeBytes);
  const content = overBudget
    ? truncateUtf8(bytes, contentBudget) + config.truncationNotice
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

/**
 * Abbreviates a count for the chip subtitle (`12500` -> `12.5k`). Returns the
 * number only — the surrounding "chars"/"lines" wording is translated by the
 * caller, so no user-facing copy lives in this module.
 */
export function formatPasteCount(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : `${count}`;
}

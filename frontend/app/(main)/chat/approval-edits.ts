/**
 * AP-06 edit before approving: which of a held call's values a person may change on the card, and what they
 * changed. Pure, so the card, its receipt and their tests share one rule. Only top-level text, numbers and yes/no
 * values are editable; lists, nested values and anything the card masks as a secret stay as the call has them.
 */

import { previewPayload, redactText, type PreviewField } from './approval-preview';
import type { PendingApproval } from './types';

export type EditKind = 'text' | 'number' | 'boolean' | 'fixed';

export interface EditField {
  key: string;
  label: string;
  kind: EditKind;
  /** The value as the call has it, as the editor shows it ('' for a fixed field: it shows `preview`). */
  original: string;
  /** Long text gets a text area. */
  multiline: boolean;
  /** How a fixed field reads (already masked). */
  preview: PreviewField | null;
}

/** What the person typed per key, as text; a number or yes/no is parsed back when sent. */
export type EditDraft = Record<string, string>;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

function parseObject(args: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(args);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

const MULTILINE_AT = 80;

/**
 * The call's values as the editor lists them, in the tool's argument order: `order` (the tool schema's property
 * order, which the worker sends with its edit offer, WP-56) first, then the rest in the call's own order (WP-54: the
 * read-only card's content-first order put Body before To on a mail). Null when the arguments are not a JSON object
 * (nothing to edit field by field).
 */
export function editFields(args: string, order: readonly string[] = []): EditField[] | null {
  const parsed = parseObject(args);
  if (parsed === null) return null;
  const previews = new Map(previewPayload(args, Number.MAX_SAFE_INTEGER).map((field) => [field.key, field]));
  const own = Object.keys(parsed);
  const rank = (key: string) => (order.includes(key) ? order.indexOf(key) : order.length + own.indexOf(key));
  const keys = [...own].sort((a, b) => rank(a) - rank(b));
  return keys.map((key) => {
    const value = parsed[key];
    const preview = previews.get(key) ?? null;
    const label = preview?.label ?? key;
    // A value the card masks is never put in an input: the person would see the mask, or the secret.
    const masked = preview?.redacted === true || redactText(key).text !== key;
    if (!masked && typeof value === 'string') {
      return { key, label, kind: 'text', original: value, multiline: value.length > MULTILINE_AT || value.includes('\n'), preview };
    }
    if (!masked && typeof value === 'number' && Number.isFinite(value)) {
      return { key, label, kind: 'number', original: String(value), multiline: false, preview };
    }
    if (!masked && typeof value === 'boolean') {
      return { key, label, kind: 'boolean', original: String(value), multiline: false, preview };
    }
    return { key, label, kind: 'fixed', original: '', multiline: false, preview };
  });
}

/** The keys whose draft differs from the call's own value; a number that does not parse counts as unchanged. */
export function changedKeys(fields: readonly EditField[], draft: EditDraft): string[] {
  return fields
    .filter((field) => field.kind !== 'fixed' && draft[field.key] !== undefined && draft[field.key] !== field.original)
    .filter((field) => field.kind !== 'number' || (Number.isFinite(Number(draft[field.key])) && draft[field.key].trim() !== ''))
    .map((field) => field.key);
}

/** A number field holds something that is not a number: the card says so and will not approve. */
export function invalidKeys(fields: readonly EditField[], draft: EditDraft): string[] {
  return fields
    .filter((field) => field.kind === 'number' && draft[field.key] !== undefined)
    .filter((field) => draft[field.key].trim() === '' || !Number.isFinite(Number(draft[field.key])))
    .map((field) => field.key);
}

/** The arguments the person approves: the call's own, with each changed value in its own type. */
export function editedArguments(args: string, fields: readonly EditField[], draft: EditDraft): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(parseObject(args) ?? {}) };
  for (const key of changedKeys(fields, draft)) {
    const field = fields.find((each) => each.key === key);
    const text = draft[key];
    if (field?.kind === 'number') out[key] = Number(text);
    else if (field?.kind === 'boolean') out[key] = text === 'true';
    else out[key] = text;
  }
  return out;
}

export interface EditDiffLine {
  key: string;
  label: string;
  before: string;
  after: string;
}

/**
 * What the person changed, for the receipt: one old/new pair per top-level value that differs, masked as the card
 * masks values. Empty when nothing differs or either side cannot be read.
 */
export function editDiff(args: string, editedArgs: string): EditDiffLine[] {
  const before = parseObject(args);
  const after = parseObject(editedArgs);
  if (before === null || after === null) return [];
  const old = new Map(previewPayload(args, Number.MAX_SAFE_INTEGER).map((field) => [field.key, field]));
  const now = previewPayload(editedArgs, Number.MAX_SAFE_INTEGER);
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const lines: EditDiffLine[] = [];
  for (const key of keys) {
    if (JSON.stringify(before[key]) === JSON.stringify(after[key])) continue;
    const shown = redactText(key).text;
    const was = old.get(shown);
    const is = now.find((field) => field.key === shown);
    lines.push({
      key: shown,
      label: is?.label ?? was?.label ?? shown,
      before: was?.full ?? '',
      after: is?.full ?? '',
    });
  }
  return lines;
}

/**
 * The call as it ran: when the person's edit changed the value the reason names as the destination (an address, a
 * channel), the destination is the new one, so the receipt's title says where it went.
 */
export function editedDestination<T extends Pick<PendingApproval, 'args' | 'reason' | 'editedArgs' | 'status'>>(approval: T): T {
  const destination = approval.reason?.destination;
  if (approval.status !== 'approved' || !approval.editedArgs || !destination) return approval;
  const moved = editDiff(approval.args, approval.editedArgs).find((line) => line.before === destination && line.after);
  return moved && approval.reason ? { ...approval, reason: { ...approval.reason, destination: moved.after } } : approval;
}

import type { MessagePart } from './types';

/**
 * ADR-RUN-03 D5 (R32REPO-FU-1): what the run, or a job it handed work to, tried and could not do, as Node sends it
 * on the answer's final part: from the journal, never a model's words. `line` is the English the answer text starts
 * with (after the not-found line), for a surface with no UI; the chat words the items in the reader's language and
 * leaves the line out.
 */
export const COULD_NOT_DO_KINDS = [
  'repository.declined',
  'repository.refused',
  'repository.not_allowed',
  'repository.failed',
  'repository.not_cloned',
  'attachment.not_accessible',
  'attachment.unreadable',
  'attachment.too_large',
] as const;
export type CouldNotDoKind = (typeof COULD_NOT_DO_KINDS)[number];

export interface CouldNotDoItem {
  kind: CouldNotDoKind;
  /** The repository it could not read (host and path), when the journal names it. */
  repository?: string;
  /** How many things it names none of the item stands for: repositories, or files a job may not name (R46FIX-FU-2). */
  count: number;
  /** The attached file it could not read, by its name (the attachment kinds, R33JGAPS-FU-6). */
  file?: string;
}

export interface CouldNotDo {
  items: CouldNotDoItem[];
  line: string;
}

/** The locale key of each kind's sentence; literal, so `i18n:keys` checks each one. */
export const COULD_NOT_DO_KEYS: Record<CouldNotDoKind, string> = {
  'repository.declined': 'chat.couldNotDo.declined',
  'repository.refused': 'chat.couldNotDo.refused',
  'repository.not_allowed': 'chat.couldNotDo.notAllowed',
  'repository.failed': 'chat.couldNotDo.failed',
  'repository.not_cloned': 'chat.couldNotDo.notCloned',
  'attachment.not_accessible': 'chat.couldNotDo.attachmentNotAccessible',
  'attachment.unreadable': 'chat.couldNotDo.attachmentUnreadable',
  'attachment.too_large': 'chat.couldNotDo.attachmentTooLarge',
};

/** Whether the item names an attached file rather than a repository. */
export const isAttachmentItem = (item: CouldNotDoItem): boolean => item.kind.startsWith('attachment.');

function item(value: unknown): CouldNotDoItem | null {
  if (value === null || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  if (!(COULD_NOT_DO_KINDS as readonly unknown[]).includes(raw.kind)) return null;
  const repository = typeof raw.repository === 'string' && raw.repository !== '' ? raw.repository : undefined;
  const count = typeof raw.count === 'number' && Number.isInteger(raw.count) && raw.count > 1 ? raw.count : 1;
  const file = typeof raw.file === 'string' && raw.file !== '' ? raw.file : undefined;
  return {
    kind: raw.kind as CouldNotDoKind,
    ...(repository !== undefined ? { repository } : {}),
    count,
    ...(file !== undefined ? { file } : {}),
  };
}

function couldNotDo(value: unknown): CouldNotDo | null {
  if (value === null || typeof value !== 'object') return null;
  const { items, line } = value as Record<string, unknown>;
  if (typeof line !== 'string' || !line || !Array.isArray(items)) return null;
  const kept = items.map(item).filter((i): i is CouldNotDoItem => i !== null);
  return kept.length > 0 ? { items: kept, line } : null;
}

/** The answer's "could not do" fact: the saved message's own, else its final text part's (a shared viewer keeps only
 *  that part). */
export function couldNotDoOf(parts: MessagePart[] | undefined, own?: unknown): CouldNotDo | null {
  return couldNotDo(own) ?? couldNotDo(parts?.find((part) => part.type === 'text' && part.isFinal === true)?.limits);
}

/** The answer without an English line it starts with, which the chat words itself; as it is when it does not. */
export function withoutLeadLine(answer: string, line: string): string {
  const lead = answer.trimStart();
  return lead.startsWith(line) ? lead.slice(line.length).replace(/^\s+/, '') : answer;
}

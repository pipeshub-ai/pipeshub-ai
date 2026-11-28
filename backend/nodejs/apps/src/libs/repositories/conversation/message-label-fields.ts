import type { MessageLabelRow } from './types';

/**
 * What `listMessageLabels` reads of a stored message: the fields the reader check uses, no more. A turn reads this
 * for every answer of its chat, so sources, tool output, reasoning, files and feedback stay in the store. The text
 * and steps (`content`, `parts`) are read: a label is relied on only while its hash matches them. Who asked
 * (`requestedBy`, `authorUserId`) is read: without it a row reads as its chat owner's. The card a question answers
 * (`answersCard`) is read: a share counts a person's message as words every member reads only when it answers none.
 */
export const MESSAGE_LABEL_FIELDS = [
  '_id',
  'sessionId',
  'orgId',
  'seq',
  'messageType',
  'content',
  'parts',
  'labelVer',
  'provenance',
  'labelHistory',
  'requestedBy',
  'authorUserId',
  'runId',
  'respondingAgentKey',
  'sharedFrom',
  'removedFrom',
  'again',
  'consent',
  'consentTo',
  'createdAt',
  'removedAt',
  'answersCard',
] as const satisfies ReadonlyArray<keyof MessageLabelRow>;

type Holds<T extends true> = T;

/** Fails to compile when the row type gains a field this list does not read: the check would get it as absent. */
export type MessageLabelFieldsCoverTheRow = Holds<
  [
    Exclude<keyof MessageLabelRow, (typeof MESSAGE_LABEL_FIELDS)[number]>,
  ] extends [never]
    ? true
    : false
>;

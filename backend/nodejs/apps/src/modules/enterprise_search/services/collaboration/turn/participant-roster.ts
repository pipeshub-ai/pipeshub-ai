import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { IMessage } from '../../../types/conversation.interfaces';

/** The AI backend's roster cap (`CollaborationContext.participants`). */
export const MAX_PARTICIPANTS = 50;
export const NAME_FALLBACK = 'Participant';
const NAME_MAX_LENGTH = 64;

export interface CollaborationParticipant {
  ref: string;
  displayName: string;
  isCurrentSender: boolean;
}

/** The `collaboration` block of the AI request; no user ids, only refs and names. */
export interface CollaborationPayload {
  participants: CollaborationParticipant[];
  currentSenderRef: string;
}

/** Maps a stored author to the ref the AI backend knows them by; legacy rows (no author) belong to the owner. */
export interface AuthorRefs {
  readonly ownerId: string;
  readonly refs: ReadonlyMap<string, string>;
}

export interface TurnRoster {
  readonly collaboration: CollaborationPayload;
  readonly authors: AuthorRefs;
}

// Same rules as the AI backend's `sanitize_display_name` (collaboration/history.py); both run
// tests/fixtures/collaboration/display-names.json. Whitespace is Python's `str.isspace()` set.
// eslint-disable-next-line no-control-regex -- matching those characters is the point
const SPACE = /[\t\n\v\f\r\x1c-\x1f\x85\u2028\u2029\p{Zs}]/u;
const OTHER = /\p{C}/u;
const STRIPPED = /[[\]<>]/gu;
const EMAIL_LIKE = /\S+@\S+/u;

/** A name safe to put in a prompt label: no brackets, control or bidi characters, no address, bounded. */
export const sanitizeDisplayName = (raw: string): string => {
  const cleaned = Array.from(raw.normalize('NFKC'))
    .map((ch) => (SPACE.test(ch) ? ' ' : OTHER.test(ch) ? '' : ch))
    .join('')
    .replace(STRIPPED, '')
    .replace(/ +/gu, ' ')
    .trim();
  if (cleaned === '' || EMAIL_LIKE.test(cleaned)) return NAME_FALLBACK;
  return Array.from(cleaned).slice(0, NAME_MAX_LENGTH).join('').trim();
};

const authorOf = (msg: IMessage, ownerId: string): string =>
  (msg.authorUserId as { toString(): string } | undefined)?.toString() ??
  ownerId;

const firstSeqOf = (msg: IMessage, fallback: number): number =>
  (msg as { seq?: number }).seq ?? fallback;

/**
 * Users in tiers, each in the order of its first row: authors of a `user_query`, authors of a
 * `note`, people only mentioned (in the history, then in the current message), and the sender with
 * no turn yet. A note or a mention never moves someone who has a question ahead of them.
 * Equal positions (which `seq` should rule out) go to the owner.
 */
const orderedAuthors = (
  history: readonly IMessage[],
  ownerId: string,
  senderId: string,
  mentionedNow: readonly string[],
): string[] => {
  const tiers: Array<Map<string, number>> = [new Map(), new Map(), new Map()];
  const note = (tier: Map<string, number>, id: string, seq: number): void => {
    if (!tier.has(id)) tier.set(id, seq);
  };
  for (const [index, msg] of history.entries()) {
    const seq = firstSeqOf(msg, index);
    if (msg.messageType === 'user_query')
      note(tiers[0] as Map<string, number>, authorOf(msg, ownerId), seq);
    else if (msg.messageType === 'note')
      note(tiers[1] as Map<string, number>, authorOf(msg, ownerId), seq);
    if (msg.messageType === 'user_query' || msg.messageType === 'note') {
      for (const m of msg.mentions ?? [])
        if (m.type === 'user') note(tiers[2] as Map<string, number>, m.id, seq);
    }
  }
  mentionedNow.forEach((id) =>
    note(tiers[2] as Map<string, number>, id, Infinity),
  );
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const tier of tiers) {
    [...tier]
      .sort(([idA, seqA], [idB, seqB]) => {
        if (seqA !== seqB) return seqA < seqB ? -1 : 1;
        return Number(idB === ownerId) - Number(idA === ownerId);
      })
      .forEach(([id]) => {
        if (seen.has(id)) return;
        seen.add(id);
        ordered.push(id);
      });
  }
  if (!seen.has(senderId)) ordered.push(senderId);
  return ordered;
};

/**
 * Assigns `participant_1..n` to the people of a chat. Past `MAX_PARTICIPANTS` the oldest 49 keep
 * their refs and the last slot goes to the sender when they have none; every other author past the
 * cap is unlabelled (their rows carry no `authorRef`, so the AI backend treats them as other people).
 * Fewer than two people yields no roster: the AI backend rejects it and the chat reads as solo.
 */
export const assignRefs = (
  history: readonly IMessage[],
  ownerId: string,
  senderId: string,
  mentionedNow: readonly string[] = [],
): ReadonlyMap<string, string> => {
  const order = orderedAuthors(history, ownerId, senderId, mentionedNow);
  const kept = order.slice(0, MAX_PARTICIPANTS);
  if (!kept.includes(senderId)) kept[MAX_PARTICIPANTS - 1] = senderId;
  return new Map(kept.map((id, i) => [id, `participant_${String(i + 1)}`]));
};

export async function buildTurnRoster(input: {
  users: IUserDirectory;
  orgId: string;
  ownerId: string;
  senderId: string;
  history: readonly IMessage[];
  /** Users the message being answered mentions; they get refs so the AI backend can name them. */
  mentionedNow?: readonly string[];
}): Promise<TurnRoster | undefined> {
  const { users, orgId, ownerId, senderId, history, mentionedNow } = input;
  const refs = assignRefs(history, ownerId, senderId, mentionedNow);
  if (refs.size < 2) return undefined;
  const names = await users.displayNames(orgId, [...refs.keys()], {
    emailFallback: false,
  });
  const participants = [...refs].map(([id, ref]) => ({
    ref,
    displayName: sanitizeDisplayName(names.get(id) ?? ''),
    isCurrentSender: id === senderId,
  }));
  return {
    collaboration: {
      participants,
      currentSenderRef: refs.get(senderId) as string,
    },
    authors: { ownerId, refs },
  };
}

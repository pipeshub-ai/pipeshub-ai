/**
 * The answer-share writes of the conversation repository on MongoDB: a member's ask added to an answer, asks closed, a
 * share written, a share ended. Each is one atomic update, guarded by what must still hold (the answer's version, the
 * share it was read with and the people it keeps from it, the asks the same person already made) rather than by the
 * row's change stamp, so concurrent asks never refuse each other or the asker's own share, dismiss or stop. Each gives
 * the row a fresh change stamp, as the row writer (`modules/enterprise_search/utils/utils.ts`) does for every row it
 * writes, so the chat's feed sees the change; the row-writer inventory lists this file beside it.
 */
import mongoose from 'mongoose';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import {
  ChatSessionMessage,
  NOT_REMOVED,
} from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import { NotFoundError } from '../../errors';
import { sessionOf } from '../../mongo';
import type { Tx } from '../../uow';
import type {
  AnswerConsentChange,
  AnswerConsentDto,
  AnswerConsentEntryDto,
  AnswerRowRef,
  ClosedShareRequestState,
  EndedAnswerShare,
  NewAnswerShare,
  ShareRequestDto,
} from '../types';

const oid = (id: string): mongoose.Types.ObjectId =>
  new mongoose.Types.ObjectId(id);

interface StoredConsent {
  by: mongoose.Types.ObjectId;
  at: Date;
  ver: string;
  scope: AnswerConsentDto['scope'];
  how?: AnswerConsentDto['how'];
  agents?: string[];
  on?: Array<{ row: string; ver: string }>;
}

interface StoredConsentEntry {
  userId: mongoose.Types.ObjectId;
  memberSince: string;
}

interface StoredShareRequest {
  by: mongoose.Types.ObjectId;
  ver: string;
  state: string;
  memberSince?: string;
}

/**
 * Asks for one version that are still open, of some people only when `by` is given, and made in another membership than
 * `notSince` only when that is.
 */
interface OpenAsks {
  ver: string;
  by?: readonly string[];
  notSince?: string | null;
}

/** The answer row itself: of its chat and org, live, and an answer or a failed answer. */
const rowFilter = (row: AnswerRowRef): Record<string, unknown> => ({
  _id: oid(row.messageId),
  sessionId: oid(row.sessionId),
  orgId: oid(row.orgId),
  messageType: { $in: ['bot_response', 'error'] },
  ...NOT_REMOVED,
});

/** The answer at the version it was read at; a row from before labels has none. */
const atVersion = (labelVer: string | undefined): Record<string, unknown> => ({
  labelVer: labelVer ?? null,
});

const openAsk = (asks: OpenAsks): Record<string, unknown> => ({
  state: 'open',
  ver: asks.ver,
  ...(asks.by && { by: { $in: asks.by.map(oid) } }),
  ...(asks.notSince !== undefined && {
    memberSince: {
      $exists: true,
      ...(asks.notSince !== null && { $ne: asks.notSince }),
    },
  }),
});

/** The same condition as an array filter on the element named `ask`. */
const openAskFilter = (asks: OpenAsks): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(openAsk(asks)).map(([field, condition]) => [
      `ask.${field}`,
      condition,
    ]),
  );

/** Who asked, among the asks `asks` names, in a row as it was just before an update closed them. */
const askersIn = (
  stored: readonly StoredShareRequest[] | undefined,
  asks: OpenAsks,
): string[] =>
  (stored ?? []).flatMap((r) =>
    r.state === 'open' &&
    r.ver === asks.ver &&
    (!asks.by || asks.by.includes(String(r.by))) &&
    (asks.notSince === undefined ||
      (r.memberSince !== undefined && r.memberSince !== asks.notSince))
      ? [String(r.by)]
      : [],
  );

/** The share exactly as it was read: none, or the same asker, time, version and scope. */
const shareIs = (expected: AnswerConsentDto | null): Record<string, unknown> =>
  expected === null
    ? { consent: null }
    : {
        'consent.by': oid(expected.by),
        'consent.at': expected.at,
        'consent.ver': expected.ver,
        'consent.scope': expected.scope,
      };

const consentDtoOf = (c: StoredConsent): AnswerConsentDto => ({
  by: String(c.by),
  at: c.at,
  ver: c.ver,
  scope: c.scope,
  ...(c.how !== undefined && { how: c.how }),
  ...(c.agents !== undefined && { agents: [...c.agents] }),
  ...(c.on !== undefined && {
    on: c.on.map(({ row, ver }) => ({ row, ver })),
  }),
});

const entryDtoOf = (e: StoredConsentEntry): AnswerConsentEntryDto => ({
  userId: String(e.userId),
  memberSince: e.memberSince,
});

/**
 * Takes the next change stamp from the chat's `rev`, before the row is written, as the row writer's `reserveChangeRevs`
 * does (in the same transaction, when there is one). This module cannot import the row writer: it imports this
 * repository.
 */
const reserveChangeRev = async (
  sessionId: string,
  tx?: Tx,
): Promise<number> => {
  const chat = await ChatSession.findOneAndUpdate(
    { _id: oid(sessionId) },
    { $inc: { rev: 1 } },
    { returnDocument: 'after', projection: { rev: 1 }, session: sessionOf(tx) },
  ).lean<{ rev: number }>();
  if (!chat) {
    throw new NotFoundError('Chat session not found');
  }
  return chat.rev;
};

export async function addShareRequest(
  row: AnswerRowRef,
  labelVer: string | undefined,
  request: ShareRequestDto,
  share: AnswerConsentDto | null,
  tx?: Tx,
): Promise<boolean> {
  const by = oid(request.by);
  const theirs = { by, ver: request.ver };
  const changeRev = await reserveChangeRev(row.sessionId, tx);
  const result = await ChatSessionMessage.updateOne(
    {
      ...rowFilter(row),
      ...atVersion(labelVer),
      // One waiting ask per person per version, held in the write so asks arriving together, a double click included,
      // add one: no ask of theirs open, and either none at all, or the share the caller found (none, or one that no
      // longer covers them) unchanged since. A dismissed, answered or void ask leaves them free to ask again.
      $nor: [
        {
          shareRequests: {
            $elemMatch: { ...theirs, state: 'open' },
          },
        },
      ],
      $or: [
        { $nor: [{ shareRequests: { $elemMatch: theirs } }] },
        shareIs(share),
      ],
    },
    { $push: { shareRequests: { ...request, by } }, $set: { changeRev } },
    { session: sessionOf(tx), runValidators: true },
  );
  return result.matchedCount > 0;
}

export async function closeShareRequests(
  row: AnswerRowRef,
  asks: OpenAsks & { state: ClosedShareRequestState },
  tx?: Tx,
): Promise<string[]> {
  const changeRev = await reserveChangeRev(row.sessionId, tx);
  const before = await ChatSessionMessage.findOneAndUpdate(
    { ...rowFilter(row), shareRequests: { $elemMatch: openAsk(asks) } },
    { $set: { 'shareRequests.$[ask].state': asks.state, changeRev } },
    {
      arrayFilters: [openAskFilter(asks)],
      returnDocument: 'before',
      session: sessionOf(tx),
      runValidators: true,
    },
  )
    .select('+shareRequests')
    .lean<{ shareRequests?: StoredShareRequest[] }>();
  return askersIn(before?.shareRequests, asks);
}

export async function setAnswerConsent(
  row: AnswerRowRef,
  share: NewAnswerShare,
  tx?: Tx,
): Promise<string[] | null> {
  const { consent, consentTo, labelVer, expected } = share;
  const covered: OpenAsks = {
    ver: consent.ver,
    ...(share.covers !== 'everyone' && { by: share.covers }),
  };
  // The people it names beside those it adds come from the share it read. A stop for one of them changes no field of
  // that share, so each must still be named: else this write would name again someone just stopped.
  const adds = share.covers === 'everyone' ? undefined : share.covers;
  const kept =
    consentTo && adds ? consentTo.filter((e) => !adds.includes(e.userId)) : [];
  const guard = {
    ...rowFilter(row),
    ...atVersion(labelVer),
    ...shareIs(expected),
    ...(kept.length > 0 && {
      $and: kept.map((e) => ({
        consentTo: {
          $elemMatch: { userId: oid(e.userId), memberSince: e.memberSince },
        },
      })),
    }),
  };
  const written = {
    consent: { ...consent, by: oid(consent.by) },
    ...(consentTo && {
      consentTo: consentTo.map((e) => ({
        userId: oid(e.userId),
        memberSince: e.memberSince,
      })),
    }),
  };
  // A share with everyone covers everyone: whom an earlier share named goes.
  const unset = consentTo ? undefined : { consentTo: 1 };
  const changeRev = await reserveChangeRev(row.sessionId, tx);
  const options = {
    returnDocument: 'before' as const,
    session: sessionOf(tx),
    runValidators: true,
  };
  // The asks it answers change where they are; `$[ask]` needs the array, which an answer's first ask creates.
  const withAsks = await ChatSessionMessage.findOneAndUpdate(
    { ...guard, shareRequests: { $exists: true } },
    {
      $set: { ...written, changeRev, 'shareRequests.$[ask].state': 'shared' },
      ...(unset && { $unset: unset }),
    },
    { ...options, arrayFilters: [openAskFilter(covered)] },
  )
    .select('+shareRequests')
    .lean<{ shareRequests?: StoredShareRequest[] }>();
  if (withAsks) {
    return askersIn(withAsks.shareRequests, covered);
  }
  const withoutAsks = await ChatSessionMessage.findOneAndUpdate(
    { ...guard, shareRequests: { $exists: false } },
    { $set: { ...written, changeRev }, ...(unset && { $unset: unset }) },
    options,
  ).lean<Record<string, unknown>>();
  return withoutAsks ? [] : null;
}

export async function changeAnswerConsent(
  row: AnswerRowRef,
  change: AnswerConsentChange,
  tx?: Tx,
): Promise<boolean> {
  const { expected, people } = change;
  const ids = people.map(oid);
  const update = !change.stop
    ? { $pull: { 'consent.except': { $in: ids } } }
    : expected.scope === 'people'
      ? { $pull: { consentTo: { userId: { $in: ids } } } }
      : { $addToSet: { 'consent.except': { $each: ids } } };
  const changeRev = await reserveChangeRev(row.sessionId, tx);
  const result = await ChatSessionMessage.updateOne(
    { ...rowFilter(row), ...atVersion(change.labelVer), ...shareIs(expected) },
    { ...update, $set: { changeRev } },
    { session: sessionOf(tx), runValidators: true },
  );
  return result.matchedCount > 0;
}

export async function clearAnswerConsent(
  row: AnswerRowRef,
  onlyBy: string | undefined,
  tx?: Tx,
): Promise<EndedAnswerShare | null> {
  const changeRev = await reserveChangeRev(row.sessionId, tx);
  const before = await ChatSessionMessage.findOneAndUpdate(
    {
      ...rowFilter(row),
      consent: { $ne: null },
      ...(onlyBy !== undefined && { 'consent.by': oid(onlyBy) }),
    },
    { $unset: { consent: 1, consentTo: 1 }, $set: { changeRev } },
    { returnDocument: 'before', session: sessionOf(tx) },
  )
    .select('+consentTo')
    .lean<{
      consent?: StoredConsent | null;
      consentTo?: StoredConsentEntry[];
    }>();
  if (!before?.consent) {
    return null;
  }
  return {
    consent: consentDtoOf(before.consent),
    consentTo: (before.consentTo ?? []).map(entryDtoOf),
  };
}

export async function clearArchivedConsents(
  row: AnswerRowRef,
  only: { by?: string; ver?: string } = {},
  tx?: Tx,
): Promise<AnswerConsentDto[]> {
  const shared = {
    consent: { $type: 'object' },
    ...(only.by !== undefined && { 'consent.by': oid(only.by) }),
    ...(only.ver !== undefined && { ver: only.ver }),
  };
  const filter = { ...rowFilter(row), labelHistory: { $elemMatch: shared } };
  // Nothing archived is shared, as on every answer never shared at an earlier version: no stamp is taken for nothing.
  if ((await ChatSessionMessage.countDocuments(filter)) === 0) return [];
  const changeRev = await reserveChangeRev(row.sessionId, tx);
  const before = await ChatSessionMessage.findOneAndUpdate(
    filter,
    {
      $unset: {
        'labelHistory.$[entry].consent': 1,
        'labelHistory.$[entry].consentTo': 1,
      },
      $set: { changeRev },
    },
    {
      arrayFilters: [
        Object.fromEntries(
          Object.entries(shared).map(([field, condition]) => [
            `entry.${field}`,
            condition,
          ]),
        ),
      ],
      returnDocument: 'before',
      session: sessionOf(tx),
    },
  )
    .select('+labelHistory')
    .lean<{
      labelHistory?: Array<{ ver?: string; consent?: StoredConsent | null }>;
    }>();
  return (before?.labelHistory ?? []).flatMap((entry) =>
    entry.consent &&
    (only.by === undefined || String(entry.consent.by) === only.by) &&
    (only.ver === undefined || entry.ver === only.ver)
      ? [consentDtoOf(entry.consent)]
      : [],
  );
}

/**
 * Slack thread links (`slack_conversations` -> `integrations.slack_threads`). The table keeps no document id: a row is keyed by
 * (organisation, thread, bot, email), and the organisation is that of the conversation the link continues. So:
 *
 *  - the key map (`carry/keymap.ts`) records which row each document became, in the unit that writes it: the follower removes the
 *    row of a deleted link (R45MIGR-FU-4), and a rollback removes the document of a link removed in PostgreSQL;
 *  - `orgId` is copied as `org_id` (R45MIGR-FU-3: a rollback writes it back). A link that never stored one is read as holding its
 *    conversation's organisation (a listed transform, counted); one that names another organisation is refused by the importer.
 */
import type { Db } from 'mongodb';
import { ObjectId } from 'mongodb';
import type { Tx as KernelTx } from '../../libs/db';
import type { KeyEntry } from './carry/keymap';
import { rowKey } from './carry/keymap';
import type { KeyedRows } from './mapping/types';

/** A conversation id as the chats table keeps it (lower-case hex), or null. */
const conversationKey = (v: unknown): string | null => {
  const s = v === undefined || v === null ? '' : String(v);
  return /^[0-9a-f]{24}$/i.test(s) ? s.toLowerCase() : null;
};

/** A marker the verifier's read sets on a document whose `orgId` it filled in; the listed transform removes and counts it. */
export const ORG_FILLED = '__orgFromConversation';

/** The verifier's read: a link without `orgId` is given its conversation's organisation (from MongoDB, as the copy found it). */
export async function fillSlackThreadOrgs(
  db: Db,
  raws: unknown[],
): Promise<void> {
  const want = new Map<string, Array<Record<string, unknown>>>();
  for (const r of raws as Array<Record<string, unknown>>) {
    if (r.orgId !== undefined && r.orgId !== null) continue;
    const c = conversationKey(r.conversationId);
    if (!c) continue;
    const list = want.get(c) ?? [];
    list.push(r);
    want.set(c, list);
  }
  if (!want.size) return;
  for await (const s of db
    .collection('chatSessions')
    .find(
      { _id: { $in: [...want.keys()].map((i) => new ObjectId(i)) } },
      { projection: { orgId: 1 } },
    ))
    for (const r of want.get(String(s._id).toLowerCase()) ?? []) {
      r.orgId = String(s.orgId).toLowerCase();
      r[ORG_FILLED] = true;
    }
}

/** The rows the documents became: their conversation's organisation (read from the copied chats) and the thread, bot and email. */
export const SLACK_THREAD_KEYS: KeyedRows = {
  columns: ['thread_id', 'bot_id', 'email'],
  async entries(
    k: KernelTx,
    docs: Array<Record<string, unknown>>,
  ): Promise<KeyEntry[]> {
    const conv = [
      ...new Set(
        docs
          .map((d) => conversationKey(d.conversationId))
          .filter((x): x is string => x !== null),
      ),
    ];
    if (!conv.length) return [];
    const { rows } = await k.query<{ id: string; org_id: string }>(
      '/* pipeshub:migrate.slack_thread_orgs */ SELECT id, org_id FROM conversation.chat_sessions WHERE id = ANY($1::text[])',
      [conv],
    );
    const orgOf = new Map(rows.map((r) => [r.id, r.org_id]));
    const out: KeyEntry[] = [];
    for (const d of docs) {
      const org = orgOf.get(conversationKey(d.conversationId) ?? '');
      if (
        !org ||
        typeof d.threadId !== 'string' ||
        typeof d.botId !== 'string' ||
        typeof d.email !== 'string'
      )
        continue;
      out.push({
        orgId: org,
        sourceId: String(d._id).toLowerCase(),
        oid: true,
        rowKey: rowKey([d.threadId, d.botId, d.email]),
      });
    }
    return out;
  },
};

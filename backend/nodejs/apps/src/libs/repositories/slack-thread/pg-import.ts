/**
 * Documents of the `slack_conversations` collection as rows of `integrations.slack_threads`. A Slack link names no organisation:
 * it is the organisation of the assistant conversation it continues, found among the copied chat sessions. A link whose
 * conversation is not there has no organisation to stamp and is reported (the link would find nothing in the application either).
 */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

export async function importSlackThreadDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const ids = [
    ...new Set(
      docs
        .map((d) => idText(d.conversationId))
        .filter((x): x is string => x !== null),
    ),
  ];
  const orgOf = new Map<string, string>();
  if (ids.length) {
    const { rows } = await k.query<{ id: string; org_id: string }>(
      '/* pipeshub:integrations.import_thread_orgs */ SELECT id, org_id FROM conversation.chat_sessions WHERE id = ANY($1::text[])',
      [ids],
    );
    for (const r of rows) orgOf.set(r.id, r.org_id);
  }
  const rows: Row[] = [];
  for (const d of docs) {
    const org = orgOf.get(idText(d.conversationId) ?? '');
    if (
      !org ||
      typeof d.threadId !== 'string' ||
      typeof d.botId !== 'string' ||
      typeof d.email !== 'string' ||
      typeof d.conversationId !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'Slack link whose conversation was not copied (no organisation to stamp)',
      });
      continue;
    }
    rows.push({
      org_id: org,
      thread_id: d.threadId,
      bot_id: d.botId,
      email: d.email,
      conversation_id: d.conversationId,
      version: typeof d.__v === 'number' ? d.__v : 0,
      created_at: d.createdAt,
      updated_at: d.updatedAt ?? d.createdAt,
    });
  }
  await writePlan(k, [{ table: 'integrations.slack_threads', rows }], stats);
  return stats;
}

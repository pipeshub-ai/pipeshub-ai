import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { EMPTY_LEDGER } from '../../../../modules/agent-harness/domain/session-import';
import { sessionOf } from '../../mongo';
import { docStore } from '../../store-select';
import type { Tx } from '../../uow';

/**
 * The agent harness's own fields on a chat session that the conversation
 * repository does not carry: the executor pin (`harnessVersion`) with its
 * import ledger, and the erasure marker (E7B), on the MongoDB session row.
 * Under `DOC_STORE=postgres` they are columns of the conversation repository's
 * session row (`./pg.ts`; callers use `./index.ts`), so these answer nothing there.
 */

export type HarnessVersion = 'legacy' | 'durable-v1';

const onMongo = (): boolean => docStore() === 'mongo';

/** What a conversation read adds to the repository's row: the executor pin, and the notes a fork keeps (FORKFIX). */
export interface HarnessSessionView {
  harnessVersion?: HarnessVersion;
  metadata?: unknown;
}

/** The harness's view of a session; empty when the session is not in MongoDB. */
export async function harnessSessionView(
  sessionId: string,
  orgId: string,
  tx?: Tx,
): Promise<HarnessSessionView> {
  if (!onMongo()) return {};
  const query = ChatSession.findOne({ _id: sessionId, orgId }).select(
    '+harnessVersion metadata',
  );
  const session = sessionOf(tx);
  const row = (await (
    session ? query.session(session) : query
  ).lean()) as HarnessSessionView | null;
  return { harnessVersion: row?.harnessVersion, metadata: row?.metadata };
}

/** The executor a session is pinned to, if any. */
export async function harnessVersionOf(
  sessionId: string,
  orgId: string,
  tx?: Tx,
): Promise<HarnessVersion | undefined> {
  return (await harnessSessionView(sessionId, orgId, tx)).harnessVersion;
}

/**
 * A fork keeps its original's executor; its first durable run imports the
 * copied messages (an empty ledger).
 */
export async function pinForkExecutor(
  forkId: string,
  orgId: string,
  sourceVersion: HarnessVersion | undefined,
  tx?: Tx,
): Promise<void> {
  if (!onMongo() || sourceVersion !== 'durable-v1') return;
  await ChatSession.updateOne(
    { _id: forkId, orgId },
    {
      $set: {
        harnessVersion: 'durable-v1',
        harnessLedger: { ...EMPTY_LEDGER },
      },
    },
    { session: sessionOf(tx) },
  );
}

/**
 * Written with a delete that enqueues erasure, in its unit of work, so the
 * sweeper still finds a delete whose enqueue was lost (E7B).
 */
export async function markErasureRequested(
  sessionId: string,
  orgId: string,
  at: number,
  tx?: Tx,
): Promise<void> {
  if (!onMongo()) return;
  await ChatSession.updateOne(
    { _id: sessionId, orgId },
    { $set: { erasureRequestedAt: at } },
    { session: sessionOf(tx) },
  );
}

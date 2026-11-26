import { ConnectionStates, type ClientSession } from 'mongoose';
import type {
  DeploymentRevision,
  HeadRevisions,
  Heads,
} from '../../../../modules/control-plane/ports/head-revisions';
import type { CpTx } from '../../../../modules/control-plane/ports/transaction';
import { CpOrgHeadModel } from '../org-heads/schema/org-heads.schema';

/** The deployment's row in `cpOrgHeads`; `$` is in no org id, so it is never an org's. */
export const DEPLOYMENT_HEAD_ID = '$deployment';

/** What the head reads ask of mongoose's connection. */
/** `readyState` is a plain number on the connections this watches (and their fakes). */
const CONNECTED: number = ConnectionStates.connected;

export interface WatchedConnection {
  readonly readyState: number;
  once(event: 'connected', listener: () => void): unknown;
  off(event: 'connected', listener: () => void): unknown;
}

/** Long enough for a driver that just saw its store return; far below mongoose's buffer timeout. */
export const RECONNECT_GRACE_MS = 1_000;

/**
 * Resolves once `connection` is connected, waiting at most `graceMs` for a
 * reconnection; rejects otherwise. A query on a closed connection would wait
 * out mongoose's buffer timeout, and callers have a fallback; but a read that
 * arrives in the moment between the store returning and the driver noticing
 * must not be refused for it (found on the fault stack, IX-DD-2b under `on`).
 */
export function connectedWithin(
  connection: WatchedConnection,
  graceMs: number = RECONNECT_GRACE_MS,
): Promise<void> {
  if (connection.readyState === CONNECTED) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const connected = (): void => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      connection.off('connected', connected);
      reject(new Error('MongoDB is not connected'));
    }, graceMs);
    connection.once('connected', connected);
  });
}

/** Point reads of head rows (`_id` lookups, about a millisecond on the admission's Mongo). */
export class MongoHeadRevisions implements HeadRevisions {
  async head(orgId: string): Promise<number> {
    await connectedWithin(CpOrgHeadModel.db);
    const row = await CpOrgHeadModel.findOne(
      { _id: orgId },
      { revision: 1 },
    ).lean();
    return row?.revision ?? 0;
  }

  /** `session`: read inside that transaction, as an admission reads them where it writes its run. */
  async heads(orgId: string, session?: ClientSession): Promise<Heads> {
    await connectedWithin(CpOrgHeadModel.db);
    const rows = await CpOrgHeadModel.find(
      { _id: { $in: [orgId, DEPLOYMENT_HEAD_ID] } },
      { revision: 1 },
    )
      .session(session ?? null)
      .lean();
    const revision = (id: string): number =>
      rows.find((row) => row._id === id)?.revision ?? 0;
    return { org: revision(orgId), deployment: revision(DEPLOYMENT_HEAD_ID) };
  }
}

/** The deployment's revision row, moved in the writing org's transaction. */
export class MongoDeploymentRevision implements DeploymentRevision {
  move(tx: CpTx): Promise<void> {
    return tx.enlist(async () => {
      await CpOrgHeadModel.updateOne(
        { _id: DEPLOYMENT_HEAD_ID },
        { $inc: { revision: 1 }, $set: { updatedAt: new Date(), lease: null } },
        { upsert: true, session: tx.session as ClientSession | undefined },
      );
    });
  }
}

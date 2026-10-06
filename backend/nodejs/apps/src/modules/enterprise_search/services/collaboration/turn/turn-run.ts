import mongoose, { ClientSession, Types } from 'mongoose';
import { isReplicaSet } from '../../../../../libs/utils/replica-set';
import { IMessage } from '../../../types/conversation.interfaces';
import { fencedWrite } from '../leases/fenced-write';
import { LeaseHandle } from '../leases/lease.types';

/**
 * What a turn's terminal writes need to know. `lease` is absent with the flag off, where nothing is
 * fenced; the authorship fields are written either way.
 */
export interface TurnRun {
  readonly lease?: LeaseHandle;
  readonly requestedBy?: Types.ObjectId;
  /** The `user_query` row this turn answers. */
  readonly inReplyTo?: Types.ObjectId;
}

/** Stamps a row a turn produces (`bot_response`, `error`, `tool_call`) with who asked, what it answers and which run wrote it. */
export const stampTurnRow = (message: IMessage, run?: TurnRun): IMessage =>
  run === undefined
    ? message
    : {
        ...message,
        ...(run.requestedBy && { requestedBy: run.requestedBy }),
        ...(run.inReplyTo && { inReplyTo: run.inReplyTo }),
        ...(run.lease && { runId: run.lease.runId }),
      };

/** Runs a turn write fenced on the lease, or plainly on `dbSession` when the turn holds none. */
export const turnWrite = <T>(
  run: TurnRun | undefined,
  dbSession: ClientSession | null | undefined,
  fn: (dbSession: ClientSession | null) => Promise<T>,
): Promise<T> =>
  run?.lease ? fencedWrite(run.lease, fn) : fn(dbSession ?? null);

/** One short transaction on a replica set, a plain call on a standalone server. */
export const inShortTransaction = async <T>(
  work: (session: ClientSession | null) => Promise<T>,
): Promise<T> => {
  if (!isReplicaSet()) return work(null);
  const session = await mongoose.startSession();
  try {
    let result: T | undefined;
    await session.withTransaction(async () => {
      result = await work(session);
    });
    return result as T;
  } finally {
    await session.endSession();
  }
};

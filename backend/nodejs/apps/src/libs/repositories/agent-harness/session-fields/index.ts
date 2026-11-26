import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import {
  harnessSessionView as mongoHarnessSessionView,
  markErasureRequested as mongoMarkErasureRequested,
  pinForkExecutor as mongoPinForkExecutor,
  type HarnessSessionView,
  type HarnessVersion,
} from './mongo';
import {
  pgHarnessSessionView,
  pgMarkErasureRequested,
  pgPinForkExecutor,
} from './pg';

export type { HarnessSessionView, HarnessVersion } from './mongo';

interface SessionFields {
  view(sessionId: string, orgId: string, tx?: Tx): Promise<HarnessSessionView>;
  pinFork(
    forkId: string,
    orgId: string,
    sourceVersion: HarnessVersion | undefined,
    tx?: Tx,
  ): Promise<void>;
  markErasure(
    sessionId: string,
    orgId: string,
    at: number,
    tx?: Tx,
  ): Promise<void>;
}

const fields = (): SessionFields =>
  pickHarnessStore<SessionFields>('agent-harness/session-fields', {
    mongo: () => ({
      view: mongoHarnessSessionView,
      pinFork: mongoPinForkExecutor,
      markErasure: mongoMarkErasureRequested,
    }),
    postgres: () => ({
      view: pgHarnessSessionView,
      pinFork: pgPinForkExecutor,
      markErasure: pgMarkErasureRequested,
    }),
  });

/** The harness's view of a session: its executor pin, and the notes a fork keeps (FORKFIX). */
export const harnessSessionView = (
  sessionId: string,
  orgId: string,
  tx?: Tx,
): Promise<HarnessSessionView> => fields().view(sessionId, orgId, tx);

/** The executor a session is pinned to, if any. */
export const harnessVersionOf = async (
  sessionId: string,
  orgId: string,
  tx?: Tx,
): Promise<HarnessVersion | undefined> =>
  (await harnessSessionView(sessionId, orgId, tx)).harnessVersion;

/** A fork keeps its original's executor; its first durable run imports the copied messages (an empty ledger). */
export const pinForkExecutor = (
  forkId: string,
  orgId: string,
  sourceVersion: HarnessVersion | undefined,
  tx?: Tx,
): Promise<void> => fields().pinFork(forkId, orgId, sourceVersion, tx);

/** Written with a delete that enqueues erasure, in its unit of work, so the sweeper still finds a lost enqueue (E7B). */
export const markErasureRequested = (
  sessionId: string,
  orgId: string,
  at: number,
  tx?: Tx,
): Promise<void> => fields().markErasure(sessionId, orgId, at, tx);

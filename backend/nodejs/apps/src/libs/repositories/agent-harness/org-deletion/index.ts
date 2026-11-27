import type { StoreSession } from '../durable-transaction';
import { pickHarnessStore } from '../stores';
import * as mongo from './mongo';
import { PgOrgDeletionData } from './pg';
import type { OrgDeletionData } from './types';

export type { OrgDeletionData };

const MONGO: OrgDeletionData = {
  earliestActiveAdmin: mongo.earliestActiveAdmin,
  setAccountDisabled: mongo.setAccountDisabled,
  ownedConversations: mongo.ownedConversations,
  transferConversations: mongo.transferConversations,
  markConversationsErased: mongo.markConversationsErased,
};

let pg: PgOrgDeletionData | undefined;

/** A person's deletion's data on the selected document store. */
export const orgDeletionData = (): OrgDeletionData =>
  pickHarnessStore<OrgDeletionData>('agent-harness/org-deletion', {
    mongo: () => MONGO,
    postgres: () => (pg ??= new PgOrgDeletionData()),
  });

export const earliestActiveAdmin = (
  orgId: string,
  except: string,
): Promise<string | null> =>
  orgDeletionData().earliestActiveAdmin(orgId, except);
export const setAccountDisabled = (
  orgId: string,
  userId: string,
  disabled: boolean,
): Promise<void> =>
  orgDeletionData().setAccountDisabled(orgId, userId, disabled);
export const ownedConversations = (
  orgId: string,
  userId: string,
): Promise<Array<{ sessionId: string; shared: boolean }>> =>
  orgDeletionData().ownedConversations(orgId, userId);
export const transferConversations = (
  orgId: string,
  fromUserId: string,
  sessionIds: readonly string[],
  toUserId: string,
  tx: StoreSession,
): Promise<void> =>
  orgDeletionData().transferConversations(
    orgId,
    fromUserId,
    sessionIds,
    toUserId,
    tx,
  );
export const markConversationsErased = (
  orgId: string,
  sessionIds: readonly string[],
  now: number,
  tx: StoreSession,
): Promise<void> =>
  orgDeletionData().markConversationsErased(orgId, sessionIds, now, tx);

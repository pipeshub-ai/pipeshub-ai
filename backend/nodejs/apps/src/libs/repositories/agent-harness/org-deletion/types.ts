import type { Tx } from '../../uow';

/**
 * The reads and writes a person's deletion (CLEANUP-G2) and deactivation make on the platform's own user and
 * conversation data. Ids arrive as strings; a malformed one finds nothing.
 */
export interface OrgDeletionData {
  /** The org's earliest active admin other than `except`, by account age then id; null when there is none. */
  earliestActiveAdmin(orgId: string, except: string): Promise<string | null>;
  /** A member's account turned off (deactivation) or back on. */
  setAccountDisabled(
    orgId: string,
    userId: string,
    disabled: boolean,
  ): Promise<void>;
  /** A user's own live conversations; `shared` when someone else holds a share of it. */
  ownedConversations(
    orgId: string,
    userId: string,
  ): Promise<Array<{ sessionId: string; shared: boolean }>>;
  /** The user's conversations among `sessionIds` become `to`'s (owner and initiator), and `to` no longer holds a share of them, in `tx`. */
  transferConversations(
    orgId: string,
    fromUserId: string,
    sessionIds: readonly string[],
    toUserId: string,
    tx: Tx,
  ): Promise<void>;
  /** The conversations are marked deleted, their erasure asked for at `now`, in `tx`. */
  markConversationsErased(
    orgId: string,
    sessionIds: readonly string[],
    now: number,
    tx: Tx,
  ): Promise<void>;
}

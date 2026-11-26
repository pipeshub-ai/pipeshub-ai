import { getConversationRepository } from '../../conversation';
import type { Tx } from '../../uow';
import { getUserRepository, type UserDto } from '../../user';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';
import { orgUnit } from '../pg-documents/pg';
import type { OrgDeletionData } from './types';

const HEX24 = /^[0-9a-f]{24}$/;

const isLiveAdmin = (user: UserDto): boolean =>
  user.role === 'admin' && user.kind !== 'service';

/**
 * What a person's deletion and deactivation read and write on PostgreSQL: main's user and conversation data through
 * their repositories. A call runs as the org its caller bound (`inBoundOrgUnit`).
 */
export class PgOrgDeletionData implements OrgDeletionData {
  async earliestActiveAdmin(
    orgId: string,
    except: string,
  ): Promise<string | null> {
    if (!HEX24.test(orgId)) return null;
    const users = await inBoundOrgUnit(orgId, () =>
      getUserRepository().listAllLive(orgId),
    );
    const time = (u: UserDto): number =>
      u.createdAt === undefined ? 0 : new Date(u.createdAt).getTime();
    const [first] = users
      .filter(
        (u) => isLiveAdmin(u) && u.isDisabled !== true && u._id !== except,
      )
      .sort((a, b) => time(a) - time(b) || a._id.localeCompare(b._id));
    return first?._id ?? null;
  }

  async setAccountDisabled(
    orgId: string,
    userId: string,
    disabled: boolean,
  ): Promise<void> {
    await inBoundOrgUnit(orgId, () =>
      getUserRepository().setAccountsDisabled(orgId, [userId], disabled),
    );
  }

  ownedConversations(
    orgId: string,
    userId: string,
  ): Promise<Array<{ sessionId: string; shared: boolean }>> {
    return inBoundOrgUnit(orgId, () =>
      getConversationRepository().listOwnedForHandover(orgId, userId),
    );
  }

  async transferConversations(
    orgId: string,
    fromUserId: string,
    sessionIds: readonly string[],
    toUserId: string,
    tx: Tx,
  ): Promise<void> {
    await orgUnit(tx, orgId, { write: true }, () =>
      getConversationRepository().transferOwners(
        orgId,
        sessionIds,
        fromUserId,
        toUserId,
        tx,
      ),
    );
  }

  async markConversationsErased(
    orgId: string,
    sessionIds: readonly string[],
    now: number,
    tx: Tx,
  ): Promise<void> {
    await orgUnit(tx, orgId, { write: true }, () =>
      getConversationRepository().markDeletedForErasure(
        orgId,
        sessionIds,
        now,
        tx,
      ),
    );
  }
}

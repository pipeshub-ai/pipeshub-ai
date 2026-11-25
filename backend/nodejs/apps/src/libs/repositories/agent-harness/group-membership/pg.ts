import type { ActorGroupMembership } from '../../../../modules/agent-harness/ports/group-access';
import { getGroupRepository } from '../../group';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

const HEX24 = /^[0-9a-f]{24}$/;

/**
 * The org's user groups (G-41) on PostgreSQL, through the group repository: membership read live at every call, only
 * the org's own live groups count.
 */
export class PgGroupMembership implements ActorGroupMembership {
  async memberOfAny(
    orgId: string,
    userId: string,
    groupIds: readonly string[],
  ): Promise<boolean> {
    const wanted = new Set(groupIds.filter((id) => HEX24.test(id)));
    if (wanted.size === 0 || !HEX24.test(orgId) || !HEX24.test(userId))
      return false;
    const groups = await inBoundOrgUnit(orgId, () =>
      getGroupRepository().groupsOfUser(orgId, userId),
    );
    return groups.some((group) => wanted.has(group._id));
  }

  /** The ids that are not live groups of the org (deleted, another org's, never existed), in the order given. */
  async unknownGroups(
    orgId: string,
    groupIds: readonly string[],
  ): Promise<string[]> {
    if (!HEX24.test(orgId)) return [...groupIds];
    const repository = getGroupRepository();
    const known = new Set<string>();
    await inBoundOrgUnit(orgId, async () => {
      for (const id of new Set(groupIds.filter((g) => HEX24.test(g)))) {
        if ((await repository.getLive(orgId, id)) !== null) known.add(id);
      }
    });
    return groupIds.filter((id) => !known.has(id));
  }

  /** The names of the org's live groups among `groupIds`, in one read (ADR-UI-03); any other id is absent. */
  async groupNames(
    orgId: string,
    groupIds: readonly string[],
  ): Promise<Map<string, string>> {
    const ids = [...new Set(groupIds.filter((id) => HEX24.test(id)))];
    if (ids.length === 0 || !HEX24.test(orgId)) return new Map();
    const found = await inBoundOrgUnit(orgId, () =>
      getGroupRepository().listLiveByIds(orgId, ids),
    );
    return new Map(found.map((group) => [group._id, group.name]));
  }
}

import mongoose from 'mongoose';
import type { ActorGroupMembership } from '../../../../modules/agent-harness/ports/group-access';
import { UserGroups } from '../../../../modules/user_management/schema/userGroup.schema';

const objectIds = (ids: readonly string[]): string[] =>
  ids.filter((id) => mongoose.isValidObjectId(id));

/**
 * The org's user groups (G-41) as the harness reads them: membership live from
 * `UserGroups` at every call (a removal is seen by the next admission), and the
 * ids an access section may not name. Only the org's own, live groups count;
 * the IdP or SCIM sync that writes them is the groups' source.
 */
export class HarnessGroupMembership implements ActorGroupMembership {
  async memberOfAny(
    orgId: string,
    userId: string,
    groupIds: readonly string[],
  ): Promise<boolean> {
    const groups = objectIds(groupIds);
    if (
      groups.length === 0 ||
      !mongoose.isValidObjectId(orgId) ||
      !mongoose.isValidObjectId(userId)
    ) {
      return false;
    }
    const member = await UserGroups.exists({
      _id: { $in: groups },
      orgId,
      isDeleted: { $ne: true },
      users: userId,
    });
    return member !== null;
  }

  /** The ids that are not live groups of the org (deleted, another org's, never existed), in the order given. */
  async unknownGroups(
    orgId: string,
    groupIds: readonly string[],
  ): Promise<string[]> {
    if (!mongoose.isValidObjectId(orgId)) return [...groupIds];
    const found = await UserGroups.find(
      { _id: { $in: objectIds(groupIds) }, orgId, isDeleted: { $ne: true } },
      { _id: 1 },
    ).lean();
    const known = new Set(found.map((doc) => String(doc._id)));
    return groupIds.filter((id) => !known.has(id));
  }

  /** The names of the org's live groups among `groupIds`, in one query (ADR-UI-03); any other id is absent. */
  async groupNames(
    orgId: string,
    groupIds: readonly string[],
  ): Promise<Map<string, string>> {
    const ids = [...new Set(objectIds(groupIds))];
    if (ids.length === 0 || !mongoose.isValidObjectId(orgId)) return new Map();
    const found = await UserGroups.find(
      { _id: { $in: ids }, orgId, isDeleted: { $ne: true } },
      { _id: 1, name: 1 },
    ).lean<Array<{ _id: unknown; name?: unknown }>>();
    return new Map(
      found.flatMap((doc) =>
        typeof doc.name === 'string'
          ? [[String(doc._id), doc.name] as const]
          : [],
      ),
    );
  }
}

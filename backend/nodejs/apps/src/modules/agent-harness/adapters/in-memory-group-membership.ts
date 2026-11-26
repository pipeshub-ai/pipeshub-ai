import type { ActorGroupMembership } from '../ports/group-access';

/** The org's user groups in memory (tests, the in-memory backend); `down` makes every read throw. */
export class InMemoryGroupMembership implements ActorGroupMembership {
  down = false;
  private readonly members = new Map<string, Set<string>>();

  add(orgId: string, groupId: string, userId: string): void {
    const key = `${orgId}|${groupId}`;
    const users = this.members.get(key) ?? new Set<string>();
    users.add(userId);
    this.members.set(key, users);
  }

  remove(orgId: string, groupId: string, userId: string): void {
    this.members.get(`${orgId}|${groupId}`)?.delete(userId);
  }

  memberOfAny(
    orgId: string,
    userId: string,
    groupIds: readonly string[],
  ): Promise<boolean> {
    if (this.down) return Promise.reject(new Error('groups unreadable'));
    return Promise.resolve(
      groupIds.some(
        (groupId) =>
          this.members.get(`${orgId}|${groupId}`)?.has(userId) === true,
      ),
    );
  }
}

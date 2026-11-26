/**
 * Names for the ids the approvals pages show, from the org's existing user,
 * group and agent APIs. Lookups that fail leave the id as is: a name is a
 * convenience, the id is what the server decides on.
 */
import { AgentsApi } from '../../agents/api';
import { GroupsApi } from '../groups/api';
import { UsersApi } from '../users/api';
import type { DirectoryOption } from './types';

const LIMIT = 20;

export const ApprovalsDirectory = {
  async searchGroups(search: string): Promise<DirectoryOption[]> {
    const { groups } = await GroupsApi.listGroups({ page: 1, limit: LIMIT, ...(search ? { search } : {}) });
    return groups.map((group) => ({ id: group._id, name: group.name }));
  },

  async searchUsers(search: string): Promise<DirectoryOption[]> {
    const { users } = await UsersApi.listUsers(
      { page: 1, limit: LIMIT, ...(search ? { search } : {}) },
      { suppressErrorToast: true },
    );
    return users.map((user) => ({
      id: user.userId,
      name: user.name || user.email || user.userId,
      ...(user.email ? { detail: user.email } : {}),
    }));
  },

  async searchAgents(search: string): Promise<DirectoryOption[]> {
    const { agents } = await AgentsApi.getAgents({ page: 1, limit: LIMIT, ...(search ? { search } : {}) });
    return agents.map((agent) => ({ id: agent._key || agent.id, name: agent.name }));
  },

  /**
   * Display names for stored people and groups, keyed by id. People are one batched request; agents and groups cost one
   * request each, so a list that holds them reads their names from its own answer instead (ADR-UI-03).
   */
  async names(set: { users: string[]; groups: string[] }, agentKeys: string[] = []): Promise<Record<string, string>> {
    const out: Record<string, string> = {};
    const settle = async (work: () => Promise<void>) => {
      try {
        await work();
      } catch {
        // The id stays shown as is.
      }
    };
    await Promise.all([
      settle(async () => {
        if (set.users.length === 0) return;
        for (const user of await UsersApi.getUsersByIds(set.users)) {
          out[user.userId] = user.name || user.email || user.userId;
        }
      }),
      ...set.groups.map((id) =>
        settle(async () => {
          out[id] = (await GroupsApi.getGroup(id)).name;
        }),
      ),
      ...agentKeys.map((key) =>
        settle(async () => {
          const { agent } = await AgentsApi.getAgent(key);
          if (agent?.name) out[key] = agent.name;
        }),
      ),
    ]);
    return out;
  },
};

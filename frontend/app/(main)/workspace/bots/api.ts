import { apiClient } from '@/lib/api';
import { REVEAL_PARAMS } from '@/lib/hooks/use-secret-reveal-available';
import type { SlackBotConfig, SlackBotConfigPayload, AgentOption } from './types';

const SLACK_BOT_BASE_URL = '/api/v1/configurationManager/slack-bot';
const AGENTS_PAGE_SIZE = 200;
const MAX_AGENT_PAGES = 25;

export const BotsApi = {
  // ── Slack Bot CRUD ──

  async getSlackBotConfigs(): Promise<SlackBotConfig[]> {
    const { data } = await apiClient.get(SLACK_BOT_BASE_URL);
    return data?.configs || [];
  },

  /** One bot's stored credentials instead of placeholders. Only where the deployment allows it. */
  async revealSlackBotConfig(configId: string): Promise<SlackBotConfig | null> {
    const { data } = await apiClient.get(SLACK_BOT_BASE_URL, { params: REVEAL_PARAMS });
    const configs: SlackBotConfig[] = data?.configs || [];
    return configs.find((c) => c.id === configId) ?? null;
  },

  async createSlackBotConfig(payload: SlackBotConfigPayload): Promise<SlackBotConfig> {
    const { data } = await apiClient.post(SLACK_BOT_BASE_URL, payload);
    return data?.config;
  },

  async updateSlackBotConfig(
    configId: string,
    payload: SlackBotConfigPayload
  ): Promise<SlackBotConfig> {
    const { data } = await apiClient.put(`${SLACK_BOT_BASE_URL}/${configId}`, payload);
    return data?.config;
  },

  async deleteSlackBotConfig(configId: string): Promise<void> {
    await apiClient.delete(`${SLACK_BOT_BASE_URL}/${configId}`);
  },

  // ── Agents ──

  /** Every agent the caller can see (`complete` says the whole list arrived). The endpoint pages at 20 by default, which would hide a linked agent from the picker. */
  async getAgents(): Promise<{ agents: AgentOption[]; complete: boolean }> {
    const options: AgentOption[] = [];
    for (let page = 1; page <= MAX_AGENT_PAGES; page += 1) {
      const { data } = await apiClient.get('/api/v1/agents', {
        params: { page, limit: AGENTS_PAGE_SIZE },
      });
      const agents: { _key: string; name?: string }[] = data?.agents || [];
      options.push(
        ...agents.map((agent) => ({ id: agent._key, name: agent.name || agent._key })),
      );
      const hasNext: unknown = data?.pagination?.hasNext;
      // Only an explicit `false` says this was the last page; a response without usable pagination
      // metadata is not known to be the whole list.
      if (hasNext === false) return { agents: options, complete: true };
      if (hasNext !== true) return { agents: options, complete: false };
    }
    // Stopped at the cap with more pages left: a bot's agent missing from this list is not known to be deleted.
    return { agents: options, complete: false };
  },
};

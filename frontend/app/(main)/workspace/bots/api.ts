import { apiClient } from '@/lib/api';
import type { SlackBotConfig, SlackBotConfigPayload, AgentOption } from './types';

const SLACK_BOT_BASE_URL = '/api/v1/configurationManager/slack-bot';

export const BotsApi = {
  // ── Slack Bot CRUD ──

  async getSlackBotConfigs(): Promise<SlackBotConfig[]> {
    const { data } = await apiClient.get(SLACK_BOT_BASE_URL);
    return data?.configs || [];
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

  async getAgents(): Promise<AgentOption[]> {
    const { data } = await apiClient.get('/api/v1/agents');
    const agents = data?.agents || [];
    return agents.map((agent: { _key: string; name?: string }) => ({
      id: agent._key,
      name: agent.name || agent._key,
    }));
  },
};

import axios from 'src/utils/axios';

export type SlackBotConfig = {
  id: string;
  name: string;
  botToken: string;
  signingSecret: string;
  agentId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type SlackBotConfigPayload = {
  name: string;
  botToken: string;
  signingSecret: string;
  agentId?: string | null;
};

export type AgentOption = {
  id: string;
  name: string;
};

const BASE_URL = '/api/v1/configurationManager/slack-bot';

export const slackBotConfigService = {
  async getConfigs(): Promise<SlackBotConfig[]> {
    const response = await axios.get(BASE_URL);
    return response.data?.configs || [];
  },

  async createConfig(payload: SlackBotConfigPayload): Promise<SlackBotConfig> {
    const response = await axios.post(BASE_URL, payload);
    return response.data?.config;
  },

  async updateConfig(configId: string, payload: SlackBotConfigPayload): Promise<SlackBotConfig> {
    const response = await axios.put(`${BASE_URL}/${configId}`, payload);
    return response.data?.config;
  },

  async deleteConfig(configId: string): Promise<void> {
    await axios.delete(`${BASE_URL}/${configId}`);
  },

  async getAgents(): Promise<AgentOption[]> {
    const response = await axios.get('/api/v1/agents');
    const agents = response.data?.agents || [];
    return agents.map((agent: { _key: string; name?: string }) => ({
      id: agent._key,
      name: agent.name || agent._key,
    }));
  },
};

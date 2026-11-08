import { apiClient } from '@/lib/api';

const BASE = '/api/v1/configurationManager/mcp/access';

/** What the workspace allows on its `/mcp` endpoint. */
export interface McpAccessPolicy {
  enabled: boolean;
  allowWriteTools: boolean;
  disabledTools: string[];
}

export interface McpAccessTool {
  name: string;
  title: string;
  /** Left out while write tools are not allowed. */
  writes: boolean;
}

export const McpAccessApi = {
  async getPolicy(): Promise<McpAccessPolicy> {
    const { data } = await apiClient.get<McpAccessPolicy>(BASE);
    return data;
  },

  async savePolicy(policy: McpAccessPolicy): Promise<McpAccessPolicy> {
    const { data } = await apiClient.put<McpAccessPolicy>(BASE, policy, { suppressErrorToast: true });
    return data;
  },

  async getTools(): Promise<McpAccessTool[]> {
    const { data } = await apiClient.get<{ tools?: McpAccessTool[] }>(`${BASE}/tools`);
    return Array.isArray(data?.tools) ? data.tools : [];
  },
};

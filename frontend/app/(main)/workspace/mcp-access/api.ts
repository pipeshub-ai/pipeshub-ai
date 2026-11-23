import { apiClient } from '@/lib/api';

const BASE = '/api/v1/configurationManager/mcp/access';

/** What the workspace allows on its `/mcp` endpoint. */
export interface McpAccessPolicy {
  enabled: boolean;
  allowWriteTools: boolean;
  disabledTools: string[];
}

/** One argument of a tool, as the server flattens it from the tool's input schema. */
export interface McpAccessToolArgument {
  name: string;
  /** `string`, `integer`, `enum`, `<type>[]` for a list, `a|b` for a choice of types, … */
  type: string;
  required: boolean;
  description?: string;
  /** The allowed values, for an enum or a list of one. */
  values?: string[];
}

/** What a tool declares it does (MCP tool annotations); an undeclared hint is false. */
export interface McpAccessToolHints {
  readOnly: boolean;
  destructive: boolean;
  idempotent: boolean;
  openWorld: boolean;
}

export interface McpAccessTool {
  name: string;
  title: string;
  /** Left out while write tools are not allowed. */
  writes: boolean;
  /** What the AI model reads before it picks the tool. Absent from a server older than this page. */
  description?: string;
  arguments?: McpAccessToolArgument[];
  hints?: McpAccessToolHints;
  /** OAuth scopes an app or access token needs to call the tool. */
  scopes?: string[];
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

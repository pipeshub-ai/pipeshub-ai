import { apiClient } from '@/lib/api';

const BASE = '/api/v1/agent-activity';

export type AgentTokenKind = 'pat' | 'oauth' | 'delegated';
export type StatusClass = '2xx' | '4xx' | '5xx';

/** One call made with an access token, or the successful GETs of one minute rolled up (`count` > 1). */
export interface AgentCallEvent {
  id: string;
  userId: string;
  tokenId: string;
  tokenKind: AgentTokenKind;
  oauthClientId?: string;
  userAgent?: string;
  channel?: string;
  method: string;
  /** Route template with `:name` placeholders. */
  route: string;
  mcpTool?: string;
  statusCode: number;
  durationMs: number;
  scopeDenied: boolean;
  requestId?: string;
  count: number;
  createdAt: string;
}

export interface AgentActivityFilter {
  tokenKind?: AgentTokenKind;
  status?: StatusClass;
  cursor?: string;
}

export interface AgentActivityPage {
  items: AgentCallEvent[];
  nextCursor?: string;
}

export const AgentActivityApi = {
  /** GET /api/v1/agent-activity, newest first. */
  async list(filter: AgentActivityFilter): Promise<AgentActivityPage> {
    // The page shows the failure itself, so the client's toast would say it twice.
    const { data } = await apiClient.get<AgentActivityPage>(BASE, { params: filter, suppressErrorToast: true });
    return { items: Array.isArray(data?.items) ? data.items : [], nextCursor: data?.nextCursor };
  },
};

/**
 * Agent call repository: the record of calls made with bearer tokens, and the admin read of it.
 * Rows leave as plain objects with string ids.
 */
/** How long a row is kept: the same as the delegation log, and for the same reason; this is the local record, not evidence. */
export const AGENT_CALL_EVENT_RETENTION_DAYS = 30;

export type AgentTokenKind = 'pat' | 'oauth' | 'delegated';
export type StatusClass = '2xx' | '4xx' | '5xx';

export interface NewAgentCall {
  orgId: string;
  userId: string;
  tokenId: string;
  tokenKind: AgentTokenKind;
  oauthClientId?: string;
  userAgent?: string;
  channel?: string;
  method: string;
  route: string;
  mcpTool?: string;
  statusCode: number;
  durationMs: number;
  scopeDenied: boolean;
  requestId?: string;
  /** Successful GETs: fold into the row of the same token, method, route and UTC minute, adding one to its count. */
  rollup: boolean;
}

export interface AgentCallDto {
  _id: string;
  orgId: string;
  userId: string;
  tokenId: string;
  tokenKind: AgentTokenKind;
  oauthClientId?: string;
  userAgent?: string;
  channel?: string;
  method: string;
  route: string;
  mcpTool?: string;
  statusCode: number;
  durationMs: number;
  scopeDenied: boolean;
  requestId?: string;
  count: number;
  createdAt: Date;
}

export interface AgentCallFilter {
  from?: Date;
  to?: Date;
  userId?: string;
  tokenId?: string;
  tokenKind?: AgentTokenKind;
  /** The user-agent product token. */
  client?: string;
  statusClass?: StatusClass;
  /** Rows strictly older than this (createdAt, then id). */
  before?: { createdAt: Date; id: string };
}

export interface AgentCallRepository {
  record(call: NewAgentCall): Promise<void>;
  /** Newest first. */
  list(
    orgId: string,
    filter: AgentCallFilter,
    limit: number,
  ): Promise<AgentCallDto[]>;
}

export const STATUS_RANGES: Record<StatusClass, [number, number]> = {
  '2xx': [200, 300],
  '4xx': [400, 500],
  '5xx': [500, 600],
};

/** The start of the UTC minute, the key of a rolled-up GET row. */
export const minuteOf = (d: Date): Date =>
  new Date(Math.floor(d.getTime() / 60000) * 60000);

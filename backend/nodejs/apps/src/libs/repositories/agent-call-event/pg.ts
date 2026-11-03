/**
 * PostgreSQL implementation of the agent call repository (`DOC_STORE=postgres`). Every statement runs in the caller's org unit;
 * a row of another org is invisible. A rolled-up GET and a plain row share one INSERT: only a row with a `bucket` can conflict.
 */
import { stripNul } from '../nul';
import { Params, assertOrg, inUnit, newObjectId, oidOf } from '../pg-util';
import {
  STATUS_RANGES,
  minuteOf,
  type AgentCallDto,
  type AgentCallFilter,
  type AgentCallRepository,
  type AgentTokenKind,
  type NewAgentCall,
} from './types';

interface Rec {
  id: string;
  org_id: string;
  user_id: string;
  token_id: string;
  token_kind: AgentTokenKind;
  oauth_client_id: string | null;
  user_agent: string | null;
  channel: string | null;
  method: string;
  route: string;
  mcp_tool: string | null;
  status_code: number;
  duration_ms: number;
  scope_denied: boolean;
  request_id: string | null;
  count: number;
  created_at: Date;
}

const COLS =
  'id, org_id, user_id, token_id, token_kind, oauth_client_id, user_agent, channel, method, route, mcp_tool, status_code, duration_ms, scope_denied, request_id, count, created_at';

const dto = (r: Rec): AgentCallDto => ({
  _id: r.id,
  orgId: r.org_id,
  userId: r.user_id,
  tokenId: r.token_id,
  tokenKind: r.token_kind,
  ...(r.oauth_client_id !== null && { oauthClientId: r.oauth_client_id }),
  ...(r.user_agent !== null && { userAgent: r.user_agent }),
  ...(r.channel !== null && { channel: r.channel }),
  method: r.method,
  route: r.route,
  ...(r.mcp_tool !== null && { mcpTool: r.mcp_tool }),
  statusCode: r.status_code,
  durationMs: r.duration_ms,
  scopeDenied: r.scope_denied,
  ...(r.request_id !== null && { requestId: r.request_id }),
  count: r.count,
  createdAt: r.created_at,
});

export class PgAgentCallRepository implements AgentCallRepository {
  async record(call: NewAgentCall): Promise<void> {
    const c = stripNul(call, 'agent-call-event', 'record');
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, c.orgId);
      const now = new Date();
      await k.query(
        `/* pipeshub:oauth.agent_call_record */ INSERT INTO oauth.agent_call_events (org_id, id, user_id, token_id, token_kind, oauth_client_id, user_agent, channel, method, route, mcp_tool, status_code, duration_ms, scope_denied, request_id, bucket, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
           ON CONFLICT (org_id, token_id, method, route, bucket) WHERE bucket IS NOT NULL
           DO UPDATE SET count = oauth.agent_call_events.count + 1`,
        [
          c.orgId,
          newObjectId(),
          c.userId,
          c.tokenId,
          c.tokenKind,
          c.oauthClientId ?? null,
          c.userAgent ?? null,
          c.channel ?? null,
          c.method,
          c.route,
          c.mcpTool ?? null,
          c.statusCode,
          c.durationMs,
          c.scopeDenied,
          c.requestId ?? null,
          c.rollup ? minuteOf(now) : null,
          now,
        ],
      );
    });
  }

  async list(
    orgId: string,
    filter: AgentCallFilter,
    limit: number,
  ): Promise<AgentCallDto[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const where = [`org_id = ${p.add(orgId)}`];
      if (filter.from) where.push(`created_at >= ${p.add(filter.from)}`);
      if (filter.to) where.push(`created_at <= ${p.add(filter.to)}`);
      // an id that is not an id matches nothing
      if (filter.userId)
        where.push(`user_id = ${p.add(oidOf(filter.userId) ?? '')}`);
      if (filter.tokenId) where.push(`token_id = ${p.add(filter.tokenId)}`);
      if (filter.tokenKind)
        where.push(`token_kind = ${p.add(filter.tokenKind)}`);
      if (filter.client) where.push(`user_agent = ${p.add(filter.client)}`);
      if (filter.statusClass) {
        const [lo, hi] = STATUS_RANGES[filter.statusClass];
        where.push(
          `status_code >= ${p.add(lo)} AND status_code < ${p.add(hi)}`,
        );
      }
      if (filter.before) {
        where.push(
          `(created_at, id) < (${p.add(filter.before.createdAt)}, ${p.add(oidOf(filter.before.id) ?? '')})`,
        );
      }
      const { rows } = await k.query<Rec>(
        `/* pipeshub:oauth.agent_call_list */ SELECT ${COLS} FROM oauth.agent_call_events WHERE ${where.join(' AND ')}
           ORDER BY created_at DESC, id DESC LIMIT ${p.add(limit)}`,
        p.values,
      );
      return rows.map(dto);
    });
  }
}

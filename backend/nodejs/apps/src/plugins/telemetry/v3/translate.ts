/**
 * What usage telemetry v3 carries, as a translation of what this plug-in
 * already records for the legacy endpoints.
 *
 * v3 sends only the signals the usage contract names, and nothing that says
 * who: every label and prop not listed here is left out. A metric's values
 * are added up over the labels that are left out, so a per-organisation
 * series becomes one count for the install.
 *
 * Left out of v3, though the legacy endpoints still carry them:
 * - metric labels `org`, `domain`, `service`, `kb`, `mimetype`;
 * - event props `orgId`, `userId`, `email`, `domain`, `fullName`,
 *   `user_agent`, `channel` (of `org_created`), `referrer_domain`,
 *   `auth_type`, `oauth_client_id`, `client_id`, `transport`, and anything
 *   else not in `EVENTS` below;
 * - every metric not in `METRICS` below (HTTP request counts and durations,
 *   `pipeshub_metric_collection_enabled`, the `pipeshub_db_*`, `docstore_*`
 *   and consumer metrics): the contract calls them operational;
 * - every event not in `EVENTS` below (`pat_created`, `pat_access_changed`,
 *   `oauth_token_issued`, `mcp_tool_called`, ...).
 */
import type { BufferedEvent } from '../event-buffer';

export type V3MetricType = 'counter' | 'gauge';

/** One series of the legacy registry, as the backend reports it. */
export interface MetricSample {
  name: string;
  type: V3MetricType;
  labels: Record<string, string>;
  value: number;
}

/** One v3 series with its cumulative (counter) or current (gauge) value. */
export interface V3Series {
  name: string;
  type: V3MetricType;
  labels: Record<string, string>;
  value: number;
}

export interface V3Event {
  name: string;
  ts: string;
  attributes: Record<string, string | number | boolean>;
}

interface MetricRule {
  to: string;
  type: V3MetricType;
  /** Legacy label -> v3 label. Labels not named are dropped and summed over. */
  labels: Record<string, string>;
  /** Labels the v3 series always has. */
  constant?: Record<string, string>;
}

export const METRICS: Readonly<Record<string, MetricRule>> = {
  pipeshub_install_info: {
    to: 'pipeshub.usage.install.info',
    type: 'gauge',
    labels: {
      graph_db: 'graph_db',
      vector_db: 'vector_db',
      message_broker: 'message_broker',
      kv_store: 'kv_store',
    },
  },
  pipeshub_orgs_total: {
    to: 'pipeshub.usage.orgs',
    type: 'gauge',
    labels: { account_type: 'account_type' },
  },
  pipeshub_org_users: {
    to: 'pipeshub.usage.users',
    type: 'gauge',
    labels: {},
    constant: { user_state: 'total' },
  },
  pipeshub_org_auth_methods: {
    to: 'pipeshub.usage.auth_methods',
    type: 'gauge',
    labels: { method: 'auth_method' },
  },
  pipeshub_connector_active: {
    to: 'pipeshub.usage.connectors.active',
    type: 'gauge',
    labels: { connector: 'connector_type' },
  },
  pipeshub_usage_records: {
    to: 'pipeshub.usage.records',
    type: 'gauge',
    labels: {
      connector_type: 'connector_type',
      indexing_status: 'indexing_status',
    },
  },
  pipeshub_activity_total: {
    to: 'pipeshub.usage.activity',
    type: 'counter',
    labels: {
      activity: 'activity',
      status: 'status',
      connector: 'connector_type',
    },
  },
  pipeshub_usage_sdk_requests_total: {
    to: 'pipeshub.usage.sdk.requests',
    type: 'counter',
    labels: {
      channel: 'channel',
      sdk_language: 'sdk_language',
      sdk_version: 'sdk_version',
      outcome: 'outcome',
    },
  },
};

type AttributeType = 'string' | 'integer' | 'boolean';

interface EventRule {
  to: string;
  /** Legacy prop -> [v3 attribute, type]. Props not named are dropped. */
  attributes: Record<string, [string, AttributeType]>;
}

export const EVENTS: Readonly<Record<string, EventRule>> = {
  login: {
    to: 'user.logged_in',
    attributes: {
      auth_method: ['auth_method', 'string'],
      first_login: ['first_login', 'boolean'],
    },
  },
  signup: { to: 'user.signed_up', attributes: {} },
  org_created: { to: 'org.created', attributes: {} },
  connector_added: {
    to: 'connector.added',
    attributes: {
      connector: ['connector_type', 'string'],
      scope: ['connector_scope', 'string'],
    },
  },
  search_performed: {
    to: 'search.performed',
    attributes: {
      search_type: ['search_type', 'string'],
      status_code: ['status_code', 'integer'],
      num_queries: ['num_queries', 'integer'],
    },
  },
  chat_session_started: {
    to: 'chat.session.started',
    attributes: {
      mode: ['mode', 'string'],
      search_type: ['search_type', 'string'],
    },
  },
  agent_run: {
    to: 'agent.run.started',
    attributes: {
      has_tools: ['has_tools', 'boolean'],
      streaming: ['streaming', 'boolean'],
    },
  },
  'sdk.client.seen': {
    to: 'sdk.client.seen',
    attributes: {
      channel: ['channel', 'string'],
      sdk_language: ['sdk_language', 'string'],
      sdk_version: ['sdk_version', 'string'],
      mcp_server_version: ['mcp_server_version', 'string'],
    },
  },
  mcp_connected: {
    to: 'mcp.client.connected',
    attributes: {
      client_name: ['mcp_client_name', 'string'],
      client_version: ['mcp_client_version', 'string'],
    },
  },
};

/** Sign-in methods the product spells differently from the contract. */
const AUTH_METHODS: Readonly<Record<string, string>> = {
  azureAd: 'azure_ad',
  samlSso: 'saml',
};

/** Labels whose values the product writes in another case than the contract. */
const LOWER_CASED = new Set(['indexing_status', 'connector_type']);

export const MAX_POINTS_PER_BATCH = 5000;

function labelValue(label: string, raw: string): string {
  if (label === 'auth_method') return AUTH_METHODS[raw] ?? raw;
  if (!LOWER_CASED.has(label)) return raw;
  const cleaned = raw.toLowerCase().replace(/[^a-z0-9_.-]/g, '_');
  return cleaned === '' ? 'unknown' : cleaned;
}

export function seriesKey(series: Pick<V3Series, 'name' | 'labels'>): string {
  const labels = Object.keys(series.labels)
    .sort()
    .map((k) => `${k}=${JSON.stringify(series.labels[k])}`);
  return `${series.name}{${labels.join(',')}}`;
}

/**
 * The v3 series for a registry snapshot: cumulative values for counters,
 * current values for gauges, each added up over the labels v3 leaves out.
 */
export function translateMetrics(samples: MetricSample[]): V3Series[] {
  const out = new Map<string, V3Series>();
  for (const sample of samples) {
    const rule = METRICS[sample.name];
    if (rule === undefined) continue;
    if (rule.type !== sample.type) continue;
    if (!Number.isFinite(sample.value) || sample.value < 0) continue;
    const labels: Record<string, string> = { ...(rule.constant ?? {}) };
    for (const [from, to] of Object.entries(rule.labels)) {
      const raw = sample.labels[from];
      if (raw !== undefined && raw !== '') labels[to] = labelValue(to, raw);
    }
    const series: V3Series = {
      name: rule.to,
      type: rule.type,
      labels,
      value: sample.value,
    };
    const key = seriesKey(series);
    const existing = out.get(key);
    if (existing === undefined) out.set(key, series);
    else existing.value += sample.value;
  }
  return [...out.values()];
}

function attributeValue(
  type: AttributeType,
  raw: unknown,
): string | number | boolean | undefined {
  if (raw === undefined || raw === null) return undefined;
  if (type === 'boolean') {
    if (typeof raw === 'boolean') return raw;
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    return undefined;
  }
  if (type === 'integer') {
    const n = typeof raw === 'number' ? raw : Number(raw);
    return Number.isInteger(n) ? n : undefined;
  }
  return typeof raw === 'string' && raw !== '' ? raw : undefined;
}

/** The v3 form of a buffered event, or undefined when v3 does not carry it. */
export function translateEvent(event: BufferedEvent): V3Event | undefined {
  const rule = EVENTS[event.event];
  if (rule === undefined) return undefined;
  const attributes: Record<string, string | number | boolean> = {};
  for (const [from, [to, type]] of Object.entries(rule.attributes)) {
    const value = attributeValue(type, event.props?.[from]);
    if (value === undefined) continue;
    attributes[to] = typeof value === 'string' ? labelValue(to, value) : value;
  }
  return { name: rule.to, ts: event.timestamp, attributes };
}

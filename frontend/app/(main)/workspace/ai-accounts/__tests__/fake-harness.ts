/**
 * A fake of the Node model-account and model-access routes for component
 * tests, behind the mocked `apiClient`. It answers as the routes do:
 * metadata only, 400 naming the field (never the value), 404 for a missing
 * account, and whatever failure a test sets for a path.
 */
import { vi } from 'vitest';

type Json = Record<string, unknown>;

export interface FakeFailure {
  statusCode: number;
  message: string;
}

export interface FakeState {
  cloud: Json[];
  access: Json;
  engine: Json;
  /** ENGPICK: the coding-engine view the routes answer. */
  coding: { engines: Json[]; orgDefault: string | null; choice: string; withoutGvisor?: boolean; gvisorSyscallFilter?: boolean; autoRoute?: boolean };
  /** `METHOD path` → the failure that call answers with. */
  fail: Record<string, FakeFailure>;
  calls: Array<{ method: string; url: string; body?: unknown }>;
  /** The org policy revision each settings route answers as its `ETag` (QA-9). */
  revisions: { access: number; engine: number };
  /** The `If-Match` each save sent, in order (null when it sent none). */
  ifMatch: Array<string | null>;
  /** The control plane's org layer (CPD-11): its sections, revision and history; `off` answers as an undeployed API. */
  cp: {
    off: boolean;
    revision: number;
    layer: Record<string, Json | undefined>;
    history: Array<{ revision: number; layer: Record<string, Json | undefined>; by: string; at: number }>;
    audit: Json[];
  };
  /** Holds a GET until released (the loading state). */
  hold?: Promise<void>;
  /** SIGNIN: the org's switch, the person's record and the open sign-in as the worker would answer it. */
  signin: {
    org: boolean;
    enabled: boolean;
    email: string | null;
    plan: string | null;
    signedInAt: string | null;
    disconnectedAt: string | null;
    serviceAccount: boolean;
    /** How long a sign-in lasts on the deployment (SIGNIN-5). */
    keep: { idleMinutes: number; maxHours: number };
    /** The open attempt the relay answers for; null: none (404). */
    attempt: { state: string; link: string | null } | null;
    /** What a pasted code gets. */
    code: Json;
    /** SIGNGAP: a live worker carries a vendor sign-in (the sign-in add-on is on). */
    carried: boolean;
  };
}

export const BASE = '/api/v1/agent-harness';

export const rule = (over: Json = {}): Json => ({
  id: 'openai-api-key-user',
  provider: 'openai',
  accountKind: 'user_api_key',
  route: 'gateway',
  engines: null,
  status: 'allowed',
  consumer: false,
  source: 'https://openai.com/policies',
  built: true,
  outcome: 'allowed',
  ...over,
});

export const RULES: Json[] = [
  rule({ id: 'openai-api-key-org', accountKind: 'org_api_key' }),
  rule(),
  rule({ id: 'azureopenai-cloud-org', provider: 'azureopenai', accountKind: 'org_cloud' }),
  rule({ id: 'anthropic-api-key-user', provider: 'anthropic' }),
  rule({ id: 'gemini-api-key-user', provider: 'gemini' }),
  rule({ id: 'bedrock-cloud-org', provider: 'bedrock', accountKind: 'org_cloud' }),
  rule({
    id: 'openai-codex-chatgpt-auth-tokens',
    accountKind: 'user_plan_sandbox_login',
    route: 'vendor_sandbox_login',
    engines: ['codex'],
    status: 'prohibited',
    consumer: true,
    built: false,
    outcome: 'vendor_prohibited',
  }),
  rule({
    id: 'anthropic-claude-plan-claude-code',
    provider: 'anthropic',
    accountKind: 'user_plan_sandbox_login',
    route: 'vendor_sandbox_login',
    engines: ['claude_code'],
    consumer: true,
    built: false,
    outcome: 'org_policy_refused',
  }),
  rule({
    id: 'anthropic-platform-paid-claude-code',
    provider: 'anthropic',
    accountKind: 'org_api_key',
    engines: ['claude_code'],
    outcome: 'offering_not_permitted',
  }),
];

/** A release engine's default profile, as Node's `RELEASE_ENGINE_PROFILES` answers it. */
export const releaseProfile = (kind: string, version: string): Json => ({
  engine: { kind, version, binaryDigest: `sha256:${'0'.repeat(64)}` },
  mode: 'build',
  isolationMin: 'docker',
  egress: 'none',
  tools: { maxTools: null, effects: ['execute', 'read', 'workspace', 'write'] },
  limits: { maxSteps: 50, maxWallS: 1800, maxTokens: null, maxUsd: null },
  subAgents: { enabled: false, maxDepth: 1, maxConcurrent: 1 },
  reasoningEffort: null,
  approvals: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' },
  skills: null,
  accountKinds: ['org_api_key', 'org_cloud', 'user_api_key'],
});

/** The first dimension a ceiling widens over the release default (E4a's rule, for the dimensions the fake knows). */
export function wideningOf(ceiling: Json | null): string | null {
  if (ceiling === null) return null;
  const limits = (ceiling.limits ?? {}) as Record<string, number>;
  if (limits.maxSteps !== undefined && limits.maxSteps > 50) return 'limits.maxSteps';
  if (limits.maxWallS !== undefined && limits.maxWallS > 1800) return 'limits.maxWallS';
  const sub = ceiling.subAgents as { enabled?: boolean } | undefined;
  if (sub?.enabled === true) return 'subAgents.enabled';
  const approvals = (ceiling.approvals ?? {}) as Record<string, string>;
  for (const cls of ['write', 'execute']) {
    if (approvals[cls] === 'allow') return `approvals.${cls}`;
  }
  return null;
}

export function freshState(): FakeState {
  return {
    cloud: [],
    access: {
      version: '2026-10-02.1',
      offering: 'managed',
      policy: { disabledRules: [], consumerPlans: false },
      rules: RULES.map((r) => ({ ...r })),
    },
    engine: {
      ceiling: null,
      defaults: [releaseProfile('opencode', '1.18.34'), releaseProfile('codex', '0.160.0')],
    },
    coding: {
      engines: [
        { kind: 'opencode', available: true },
        { kind: 'codex', available: true },
        { kind: 'claude_code', available: false, reason: { code: 'OFFERING_NOT_PERMITTED' } },
      ],
      orgDefault: 'codex',
      choice: 'org_default',
      autoRoute: true,
    },
    fail: {},
    calls: [],
    revisions: { access: 1, engine: 1 },
    ifMatch: [],
    cp: { off: false, revision: 0, layer: {}, history: [], audit: [] },
    signin: {
      org: false,
      enabled: false,
      email: null,
      plan: null,
      signedInAt: null,
      disconnectedAt: null,
      serviceAccount: false,
      keep: { idleMinutes: 30, maxHours: 12 },
      attempt: null,
      code: { state: 'signed_in', retry: false, plan: 'team', email: 'ada@example.com' },
      carried: true,
    },
  };
}

/** The person's view as Node answers it (`EngineSignInService.view`). */
export function signInView(state: FakeState): Json {
  const s = state.signin;
  const reason = !s.carried
    ? 'engine_signin_unavailable'
    : !s.org
      ? 'engine_signin_off'
      : s.serviceAccount
        ? 'account_requires_interactive'
        : undefined;
  const view: Json = {
    state:
      reason !== undefined
        ? 'not_allowed'
        : s.enabled && s.email !== null
          ? 'connected'
          : s.disconnectedAt !== null && !s.enabled
            ? 'disconnected'
            : 'available',
    enabled: reason === undefined && s.enabled,
    consumerPlans: (state.access.policy as Json).consumerPlans === true,
    keep: state.signin.keep,
  };
  if (reason !== undefined) view.reason = reason;
  if (s.email !== null) view.email = s.email;
  if (s.plan !== null) {
    view.plan = s.plan;
    view.workPlan = s.plan === 'team' || s.plan === 'enterprise';
  }
  if (s.signedInAt !== null) view.signedInAt = s.signedInAt;
  if (s.disconnectedAt !== null) view.disconnectedAt = s.disconnectedAt;
  return { claude: view, chatgpt: { state: 'waiting_vendor' } };
}

const CP = '/api/v1/control-plane';
const CEILINGS: Record<string, Json> = {
  budgets: { 'budgets.maxTurns': 200, 'budgets.maxCostUsd': 50, 'budgets.maxTokens': 1000000, 'budgets.activeTimeS': 7200 },
  models: {},
};
const OPTIONS: Record<string, Json> = {
  budgets: {},
  models: { 'models.routes': ['anthropic:claude', 'openai:gpt-5'] },
};

/** The admin API's refusal body (D1), as `apiClient` rejects with it. */
const cpRefuse = (status: number, code: string, message: string, extra: Json = {}) => ({
  type: 'ERR',
  statusCode: status,
  message,
  originalError: { response: { status, data: { error: { code, message }, problem: { code, status, message, ...extra } } } },
});

const NOW = '2026-10-04T09:00:00.000Z';
const LATER = '2026-10-04T10:00:00.000Z';

const lastFour = (value: string): string => (value.length >= 16 ? value.slice(-4) : '');

const refuse = (statusCode: number, message: string) => ({ type: 'ERR', statusCode, message });

/** Recomputes each row's outcome from the stored policy, as Node's `rowOutcome` does. */
function withOutcomes(access: Json): Json {
  const policy = access.policy as { disabledRules: string[]; consumerPlans: boolean };
  return {
    ...access,
    rules: (access.rules as Json[]).map((r) => {
      let outcome = 'allowed';
      if (r.status === 'prohibited') outcome = 'vendor_prohibited';
      else if (r.status === 'pending_vendor') outcome = 'vendor_pending';
      else if (r.outcome === 'offering_not_permitted') outcome = 'offering_not_permitted';
      else if (policy.disabledRules.includes(r.id as string)) outcome = 'org_policy_refused';
      else if (r.consumer && !policy.consumerPlans) outcome = 'org_policy_refused';
      return { ...r, outcome };
    }),
  };
}

export function installFake(
  client: { get: unknown; put: unknown; post: unknown; delete: unknown },
  state: FakeState,
): void {
  const controlPlane = async (method: string, path: string, body: unknown, config: unknown): Promise<{ data: unknown }> => {
    const cp = state.cp;
    const failure = state.fail[`${method} ${CP}${path.split('?')[0]}`];
    if (failure) throw cpRefuse(failure.statusCode, 'CP_UNAVAILABLE', failure.message);
    if (cp.off) {
      throw cpRefuse(503, 'CP_UNAVAILABLE', 'The control plane is not configured on this deployment', {
        remediation: { action: 'not_configured', actor: 'operator' },
      });
    }
    const section = /^\/policy\/org\/current\/(budgets|models|access)/.exec(path)?.[1];
    const view = (name: string) => ({
      section: name,
      value: cp.layer[name] ?? null,
      revision: cp.revision,
      ceilings: CEILINGS[name],
      options: OPTIONS[name],
      locked: [],
    });
    const write = (name: string, value: Json | null) => {
      const header = (config as { headers?: Record<string, string> } | undefined)?.headers?.['If-Match'] ?? null;
      state.ifMatch.push(header);
      if (header !== `"${String(cp.revision)}"`) {
        throw cpRefuse(409, 'CP_REVISION_CONFLICT', `the policy is at revision ${String(cp.revision)}`);
      }
      const turns = (value as { maxTurns?: number } | null)?.maxTurns;
      if (typeof turns === 'number' && turns > 200) {
        throw cpRefuse(422, 'CP_WIDENING', 'budgets.maxTurns: above the release ceiling of 200', {
          dimension: 'budgets.maxTurns',
          layer: 'org',
        });
      }
      cp.revision += 1;
      cp.layer = { ...cp.layer, [name]: value ?? undefined };
      cp.history = [{ revision: cp.revision, layer: { ...cp.layer }, by: 'user:u-admin', at: Date.UTC(2026, 9, 16, 10) }, ...cp.history];
      cp.audit = [
        { seq: cp.revision, at: '2026-10-16T10:12:00.000Z', fields: { actor: 'user:u-admin', objectType: 'policy-section', objectId: name, changed: `${name}`, revisionFrom: cp.revision - 1, revisionTo: cp.revision } },
        ...cp.audit,
      ];
      return { data: view(name) };
    };
    if (section && method === 'GET' && path.includes('/history')) {
      return { data: { revisions: cp.history.map((h) => ({ revision: h.revision, value: h.layer[section] ?? null, updated: { at: h.at, by: h.by } })) } };
    }
    if (section && method === 'POST' && path.endsWith('/rollback')) {
      const target = cp.history.find((h) => h.revision === (body as { revision: number }).revision);
      if (!target) throw cpRefuse(404, 'CP_NOT_FOUND', 'No such policy revision');
      return write(section, target.layer[section] ?? null);
    }
    if (section && method === 'GET') return { data: view(section) };
    if (section && method === 'PUT') return write(section, (body as { value: Json | null }).value);
    if (method === 'GET' && path.startsWith('/audit')) {
      const ids = decodeURIComponent(/objectId=([^&]*)/.exec(path)?.[1] ?? '').split(',');
      return { data: { records: cp.audit.filter((r) => ids.includes(String((r.fields as Json).objectId))), next: null } };
    }
    throw cpRefuse(404, 'CP_NOT_FOUND', `no route ${method} ${path}`);
  };
  const tagged = (data: unknown, section: 'access' | 'engine') => ({
    data,
    headers: { etag: `"${String(state.revisions[section])}"` },
  });
  /** As the facades answer a save from a stale revision: 409, nothing stored. */
  const precondition = (section: 'access' | 'engine', config: unknown): void => {
    const header = (config as { headers?: Record<string, string> } | undefined)?.headers?.['If-Match'] ?? null;
    state.ifMatch.push(header);
    if (header !== null && header !== `"${String(state.revisions[section])}"`) {
      throw refuse(409, 'the policy is at a newer revision');
    }
    state.revisions[section] += 1;
  };
  const answer = async (method: string, url: string, body?: unknown, config?: unknown): Promise<{ data: unknown }> => {
    state.calls.push({ method, url, body });
    if (url.startsWith(CP)) return controlPlane(method, url.slice(CP.length), body, config);
    // G-41: what the group access section reads besides its section: the org's chat models and the admin's groups.
    if (method === 'GET' && url === '/api/v1/configurationManager/ai-models/llm') return { data: { models: [] } };
    if (method === 'GET' && url.startsWith('/api/v1/userGroups/users/')) return { data: [] };
    // WP-90: what the Bot accounts card reads besides its section: the org's agents and apps (none here).
    if (method === 'GET' && url === '/api/v1/agents') return { data: { agents: [], pagination: {} } };
    if (method === 'GET' && url === '/api/v1/toolsets/my-toolsets') return { data: { toolsets: [], pagination: { hasNext: false } } };
    // C411-FU-1: the traces card reads what the workers report (no worker has said content is traced).
    if (method === 'GET' && url === '/api/v1/agent-harness/admin/worker-settings') return { data: { traceContent: 'off' } };
    const path = url.slice(BASE.length);
    const failure = state.fail[`${method} ${path}`];
    if (method === 'GET' && state.hold) await state.hold;
    if (failure) throw refuse(failure.statusCode, failure.message);

    // WP-23: the org's data region, as Node answers before the org's first agent run.
    if (method === 'GET' && path === '/model-accounts/access') return { data: withOutcomes(state.access) };
    if (method === 'GET' && path === '/admin/model-access') return tagged(withOutcomes(state.access), 'access');
    if (method === 'GET' && path === '/admin/model-accounts') return { data: { accounts: state.cloud } };
    if (method === 'POST' && path === '/admin/model-accounts') {
      const { provider, label, credentials } = body as { provider: string; label: string | null; credentials: Record<string, string> };
      const secret = credentials.apiKey ?? credentials.secretAccessKey ?? credentials.serviceAccountJson ?? '';
      const id = `c${state.cloud.length + 1}`;
      const view = {
        id,
        accountRef: { kind: 'org_cloud', accountId: `cloud/${id}` },
        provider,
        label,
        last4: lastFour(secret),
        version: 1,
        createdAt: NOW,
        rotatedAt: NOW,
        validation: 'unvalidated',
      };
      state.cloud = [...state.cloud, view];
      return { data: view };
    }
    const cloud = /^\/admin\/model-accounts\/([^/]+)$/.exec(path);
    if (cloud && method === 'PUT') {
      const existing = state.cloud.find((a) => a.id === cloud[1]);
      if (!existing) throw refuse(404, 'Model account not found');
      const { credentials, label } = body as { credentials: Record<string, string>; label: string | null };
      const secret = credentials.apiKey ?? credentials.secretAccessKey ?? credentials.serviceAccountJson ?? '';
      const view = { ...existing, label, last4: lastFour(secret), version: (existing.version as number) + 1, rotatedAt: LATER };
      state.cloud = state.cloud.map((a) => (a.id === cloud[1] ? view : a));
      return { data: view };
    }
    if (cloud && method === 'DELETE') {
      if (!state.cloud.some((a) => a.id === cloud[1])) throw refuse(404, 'Model account not found');
      state.cloud = state.cloud.filter((a) => a.id !== cloud[1]);
      return { data: '' };
    }
    if (method === 'PUT' && path === '/admin/model-access') {
      const policy = body as { disabledRules: string[]; consumerPlans: boolean };
      const ids = new Set((state.access.rules as Json[]).map((r) => r.id));
      if (policy.disabledRules.some((id) => !ids.has(id))) {
        throw refuse(400, 'disabledRules: only rows of this release can be switched off');
      }
      precondition('access', config);
      state.access = { ...state.access, policy };
      return tagged(withOutcomes(state.access), 'access');
    }
    if (method === 'GET' && path === '/model-accounts/coding-engine') return { data: state.coding };
    if (method === 'PUT' && (path === '/model-accounts/coding-engine' || path === '/admin/coding-engine')) {
      const mine = path.startsWith('/model-accounts');
      const autoRoute = (body as { autoRoute?: boolean }).autoRoute;
      if (!mine && typeof autoRoute === 'boolean') {
        state.coding = { ...state.coding, autoRoute };
        return { data: state.coding };
      }
      const engine = mine ? (body as { choice: string }).choice : (body as { defaultEngine: string | null }).defaultEngine;
      const listed = state.coding.engines.find((e) => e.kind === engine);
      // As Node refuses an engine the org cannot use now: the vendor rule's code and sentence.
      if (listed !== undefined && listed.available !== true) {
        throw refuse(403, 'This offering may not use this account and route');
      }
      state.coding = mine ? { ...state.coding, choice: engine ?? 'org_default' } : { ...state.coding, orgDefault: engine };
      return { data: state.coding };
    }
    if (method === 'GET' && path === '/engine-accounts/me') return { data: signInView(state) };
    if (method === 'PUT' && path === '/engine-accounts/me/claude') {
      const enabled = (body as { enabled: boolean }).enabled;
      if (enabled && !state.signin.carried) throw refuse(409, 'Personal Claude accounts need the sign-in add-on on this server');
      if (enabled && (!state.signin.org || state.signin.serviceAccount)) throw refuse(403, 'Not allowed');
      if (!enabled && state.signin.enabled) {
        state.signin = { ...state.signin, enabled: false, email: null, plan: null, signedInAt: null, disconnectedAt: LATER };
      } else state.signin = { ...state.signin, enabled };
      return { data: signInView(state) };
    }
    if (method === 'DELETE' && path === '/engine-accounts/me/claude') {
      state.signin = { ...state.signin, enabled: false, email: null, plan: null, signedInAt: null, disconnectedAt: LATER };
      return { data: signInView(state) };
    }
    const attempt = /^\/engine-accounts\/signin\/([^/]+)(\/code)?$/.exec(path);
    if (attempt) {
      if (state.signin.attempt === null) throw refuse(404, 'No such sign-in');
      if (method === 'GET') {
        return {
          data: {
            attemptId: decodeURIComponent(attempt[1]),
            runId: 'run-1',
            sessionId: 'ses-1',
            state: state.signin.attempt.state,
            ...(state.signin.attempt.link !== null ? { link: state.signin.attempt.link } : {}),
            expiresAt: LATER,
          },
        };
      }
      if (method === 'POST' && attempt[2]) return { data: state.signin.code };
      if (method === 'DELETE') {
        state.signin = { ...state.signin, attempt: { ...state.signin.attempt, state: 'cancelled' } };
        return { data: '' };
      }
    }
    if (method === 'GET' && path === '/admin/engine-accounts') {
      const s = state.signin;
      const signedIn = s.org && s.enabled && s.signedInAt !== null ? ['u-me'] : [];
      return { data: { claude: s.org, available: s.carried, signedIn, keep: s.keep } };
    }
    if (method === 'PUT' && path === '/admin/engine-accounts') {
      const claude = (body as { claude: boolean }).claude;
      if (claude && !state.signin.carried) throw refuse(409, 'Personal Claude accounts need the sign-in add-on on this server');
      state.signin = { ...state.signin, org: claude };
      return { data: { claude: state.signin.org } };
    }
    if (method === 'GET' && path === '/admin/engine-profile') return tagged(state.engine, 'engine');
    if (method === 'PUT' && path === '/admin/engine-profile') {
      const ceiling = (body as { ceiling: Json | null }).ceiling;
      const widening = wideningOf(ceiling);
      if (widening !== null) throw refuse(400, `The org engine profile asks for more than allowed: ${widening}`);
      precondition('engine', config);
      state.engine = { ...state.engine, ceiling };
      return tagged(state.engine, 'engine');
    }
    throw refuse(404, `no route ${method} ${path}`);
  };

  vi.mocked(client.get as (url: string) => Promise<unknown>).mockImplementation((url: string) => answer('GET', url));
  vi.mocked(client.delete as (url: string) => Promise<unknown>).mockImplementation((url: string) => answer('DELETE', url));
  vi.mocked(client.put as (url: string, body: unknown, config?: unknown) => Promise<unknown>).mockImplementation(
    (url: string, body: unknown, config?: unknown) => answer('PUT', url, body, config),
  );
  vi.mocked(client.post as (url: string, body: unknown, config?: unknown) => Promise<unknown>).mockImplementation(
    (url: string, body: unknown, config?: unknown) => answer('POST', url, body, config),
  );
}

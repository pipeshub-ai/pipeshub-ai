import { readFileSync } from 'node:fs';

export type ActorName =
  | 'admin'
  | 'owner'
  | 'write_recipient'
  | 'read_recipient'
  | 'project_viewer'
  | 'project_editor'
  | 'project_team_member'
  | 'team_writer'
  | 'team_reader'
  | 'stranger'
  | 'other_org'
  | 'disabled';

export interface Actor {
  name: string;
  userId: string;
  orgId: string;
  email: string;
  role: string;
  /** Session JWT signed with the secret the stack's Node API verifies with. */
  token: string;
}

export interface StackState {
  /** Node API as the host sees it (127.0.0.1). */
  nodeUrl: string;
  controlUrl: string;
  roster: Record<ActorName, Actor>;
}

export type Reply =
  | { kind: 'held_stream'; gate: string; text?: string; runId?: string }
  | { kind: 'ask_user_question'; toolData?: Record<string, unknown>; runId?: string }
  | { kind: 'agent_draft'; draft: Record<string, unknown>; text?: string; runId?: string }
  | { kind: 'stream_answer'; text?: string; runId?: string; answerMatchType?: string }
  | { kind: 'run_error'; message?: string }
  | { kind: 'reply'; text?: string; body?: unknown; status?: number };

export interface FakeRequest {
  route: string;
  method: string;
  path: string;
  userId: string | null;
  body: unknown;
  at: number;
}

export const FRONTEND_ORIGIN = process.env.BASE_URL || 'http://localhost:3001';
export const AI_ROUTES = ['chat', 'chat_stream', 'agent_chat', 'agent_chat_stream'];

let cached: StackState | undefined;

/** The state `run.sh` wrote (`PCC_STACK_STATE`): where the Node API and the control API listen, and the seeded users. */
export function stackState(): StackState {
  if (!cached) {
    const file = process.env.PCC_STACK_STATE;
    if (!file) throw new Error('PCC_STACK_STATE is not set; run these specs through tests/e2e/collab-chats/run.sh');
    cached = JSON.parse(readFileSync(file, 'utf8')) as StackState;
  }
  return cached;
}

/** The Node API as the browser reaches it. Must match the host the frontend was built with (CORS and the baked base URL). */
export function browserNodeUrl(): string {
  return stackState().nodeUrl.replace('127.0.0.1', 'localhost');
}

async function control<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${stackState().controlUrl}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`control ${method} ${path} -> ${res.status}: ${text}`);
  return JSON.parse(text) as T;
}

/** Scripts the fake of the Python services. Python is not real in this lane: every model reply is one of these. */
export const fake = {
  reset: () => control<{ ok: true }>('POST', '/reset'),
  script: (route: string, ...replies: Reply[]) => control<{ queued: number }>('POST', '/script', { route, replies }),
  openGate: (name: string) => control<{ open: true }>('POST', `/gate/${name}/open`),
  gateReached: async (name: string) => (await control<{ reached: boolean }>('GET', `/gate/${name}/reached`)).reached,
  mark: async () => (await control<{ mark: number }>('GET', '/mark')).mark,
  requests: async (routes: string[] = [], since = 0) =>
    (await control<{ requests: FakeRequest[] }>('GET', `/requests?route=${routes.join(',')}&since=${since}`)).requests,
  setFlag: (enabled: boolean, key?: string) => control<{ enabled: boolean }>('POST', '/flag', { enabled, key }),
  /** A team in the fake connectors service, in the owner's org. `members` maps roster keys to WRITER/READER/OWNER. */
  addTeam: (name: string, members: Record<string, 'WRITER' | 'READER' | 'OWNER'>, teamId?: string) =>
    control<{ teamId: string }>('POST', '/team', { name, members, teamId }),
  /** A chat seeded in Mongo (one question and answer, or none with `empty`), shared with roster users and teams. */
  seedChat: (spec: { key: string; owner?: string; kind?: 'chat' | 'agent'; users?: Record<string, 'read' | 'write'>; teams?: Record<string, 'read' | 'write'>; empty?: boolean }) =>
    control<{ sessionId: string }>('POST', '/seed-chat', spec),
  /** Forgets every user's `tipsSeen`, so coachmarks show again. */
  resetTips: () => control<{ ok: true }>('POST', '/tips/reset'),
  /** A user of their own in the roster's org: sharing is rate limited per sharer, and the owner status is remembered per user. */
  freshActor: (label: string) => control<Actor>('POST', '/actor', { label }),
  setDisabled: (userId: string, disabled: boolean) => control<{ disabled: boolean }>('POST', '/user/disabled', { userId, disabled }),
  /** A membership change made outside Node: only the connectors fake hears of it. */
  changeTeamMember: (teamId: string, userId: string, remove: boolean, role: 'READER' | 'WRITER' = 'READER') =>
    control<{ members: number }>('POST', '/team/member', { teamId, userId, remove, role }),
  /** Redis keys of the access decision and team-id caches with their remaining TTL in seconds. */
  cache: async (pattern?: string) => (await control<{ keys: Record<string, number> }>('GET', `/cache${pattern ? `?pattern=${encodeURIComponent(pattern)}` : ''}`)).keys,
  deleteCache: (...patterns: string[]) => control<{ deleted: number }>('POST', '/cache/delete', { patterns }),
  serviceToken: async (orgId: string) => (await control<{ token: string }>('POST', '/service-token', { orgId })).token,
};

export async function waitFor<T>(
  what: string,
  probe: () => Promise<T | false | undefined | null>,
  timeoutMs = 20_000,
  intervalMs = 200,
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const got = await probe();
    if (got) return got;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export interface ApiResult<T = any> {
  status: number;
  body: T;
  headers: Record<string, string>;
}

/** The real Node API called as an actor, for seeding and for the forced attempts a UI cannot make. */
export class NodeApi {
  /** Chats this client created, so the fixture can remove them when the test ends. */
  readonly created: string[] = [];

  constructor(private readonly actor: Actor) {}

  async call<T = any>(method: string, path: string, body?: unknown): Promise<ApiResult<T>> {
    const res = await fetch(`${stackState().nodeUrl}${path}`, {
      method,
      headers: { authorization: `Bearer ${this.actor.token}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let parsed: any = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      /* not JSON */
    }
    return { status: res.status, body: parsed, headers: Object.fromEntries(res.headers) };
  }

  async createChat(query: string): Promise<string> {
    const r = await this.call('POST', '/api/v1/conversations/create', { query, chatMode: 'quick' });
    if (r.status !== 201) throw new Error(`create chat: ${r.status} ${JSON.stringify(r.body)}`);
    this.created.push(r.body.conversation._id);
    return r.body.conversation._id;
  }

  /**
   * A chat with one finished turn, started the way the UI does (the streamed create route). The fake answers with
   * whatever is scripted for `chat_stream`, "Fake answer" by default.
   */
  async startChat(query: string): Promise<string> {
    const res = await fetch(`${stackState().nodeUrl}/api/v1/conversations/stream`, {
      method: 'POST',
      headers: { authorization: `Bearer ${this.actor.token}`, 'content-type': 'application/json' },
      body: JSON.stringify({ query, chatMode: 'internal_search' }),
    });
    const text = await res.text();
    if (res.status !== 200) throw new Error(`start chat: ${res.status} ${text.slice(0, 300)}`);
    const id = /"conversation":\{"_id":"([0-9a-f]{24})"/.exec(text)?.[1] ?? /"_id":"([0-9a-f]{24})"/.exec(text)?.[1] ?? /"conversationId":"([0-9a-f]{24})"/.exec(text)?.[1];
    if (!id) throw new Error(`start chat: no conversation id in the stream: ${text.slice(0, 400)}`);
    this.created.push(id);
    return id;
  }

  /** Share at `write` or `read` through the PH-06 collaborators route. */
  share(conversationId: string, recipient: Actor, level: 'read' | 'write', note?: string) {
    return this.call('PUT', `/api/v1/conversations/${conversationId}/collaborators`, {
      collaborators: [{ principalType: 'user', principalId: recipient.userId, accessLevel: level }],
      ...(note ? { note } : {}),
    });
  }

  revoke(conversationId: string, recipient: Actor) {
    return this.call('DELETE', `/api/v1/conversations/${conversationId}/collaborators/${recipient.userId}?principalType=user`);
  }

  getChat(conversationId: string) {
    return this.call('GET', `/api/v1/conversations/${conversationId}`);
  }

  /** Deletes every chat this client created. Failures are ignored: the lane resets its collections between tests anyway. */
  async deleteCreated(): Promise<void> {
    await Promise.all(this.created.splice(0).map((id) => this.call('DELETE', `/api/v1/conversations/${id}`).catch(() => undefined)));
  }
}

/**
 * Node's internal chat-content check, called the way Python's `NodePdpClient` calls it (a service token with the
 * `authz:check` scope). Returns whether `subject` may read the file or artifact.
 */
export async function pdpAllows(
  subject: Actor,
  resource: { type: 'chatAttachment' | 'chatArtifact'; recordId: string; ownerUserId: string; conversationId?: string; runId?: string; kind?: Record<string, unknown> },
): Promise<boolean> {
  const token = await fake.serviceToken(subject.orgId);
  const res = await fetch(`${stackState().nodeUrl}/api/v1/authz/internal/check`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ userId: subject.userId, orgId: subject.orgId, action: 'read', resource }),
  });
  if (res.status !== 200) throw new Error(`pdp check: ${res.status} ${await res.text()}`);
  return ((await res.json()) as { allow: boolean }).allow;
}

/**
 * An agent's environment for its coding-engine work (C20.18, WP-100), set in the builder's "Environment" section and
 * versioned with the agent: setup commands, non-secret settings, folders the setup fills; or a repository's
 * devcontainer.json pasted in. Members an edition adds are kept as loaded (`EnvironmentDraft.extra`). The shape is the run spec's `x.pipeshub.environment`; the
 * server checks it again on save and the worker once more before it runs.
 */
export interface AgentEnvironment {
  environmentId: string;
  level: 'agent' | 'repository';
  setup?: string;
  env?: Record<string, string>;
  cachePaths?: string[];
  devcontainer?: string;
}

/** The members this build edits; any other is an edition's, kept as loaded and saved back unchanged. */
const OWN_MEMBERS: ReadonlySet<string> = new Set(['environmentId', 'level', 'setup', 'env', 'cachePaths', 'devcontainer']);

export const MAX_SETTINGS = 64;
export const MAX_CACHE_FOLDERS = 16;
const ENVIRONMENT_ID = 'agent';
const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
// The worker's own rules (harness_core.engines.environments.check_env).
const RESERVED = new Set(['PATH', 'HOME', 'USER', 'SHELL', 'PWD', 'TMPDIR', 'NO_PROXY', 'no_proxy', 'NODE_USE_ENV_PROXY']);
const RESERVED_PREFIX = /^(PXH_|PIPESHUB_|GIT_|HTTP_|HTTPS_|http_|https_|SSL_|OPENAI_|ANTHROPIC_)/;
const RESERVED_SUFFIX = /(_CA_BUNDLE|_CA_CERTS|_PROXY)$/;
const SECRET_NAME = /(TOKEN|SECRET|PASSW(OR)?D|PASSPHRASE|CREDENTIAL|API_?KEY|PRIVATE_?KEY|AUTH)/i;

/** A problem with what the author typed, as a locale key and the name it is about. */
export interface EnvironmentProblem {
  key: string;
  name?: string;
}

/** `NAME=value` lines as settings, or the first line that is not one. */
export function settingsOf(text: string): { env: Record<string, string> } | { problem: EnvironmentProblem } {
  const env: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (line === '') continue;
    const at = line.indexOf('=');
    const name = at > 0 ? line.slice(0, at).trim() : line;
    if (at <= 0 || !NAME.test(name)) return { problem: { key: 'agentBuilder.environment.problem.setting', name } };
    if (RESERVED.has(name) || RESERVED_PREFIX.test(name) || RESERVED_SUFFIX.test(name)) {
      return { problem: { key: 'agentBuilder.environment.problem.reserved', name } };
    }
    if (SECRET_NAME.test(name)) return { problem: { key: 'agentBuilder.environment.problem.secret', name } };
    env[name] = line.slice(at + 1).trim();
  }
  if (Object.keys(env).length > MAX_SETTINGS) return { problem: { key: 'agentBuilder.environment.problem.tooMany' } };
  return { env };
}

export const settingsText = (env: Record<string, string> | undefined): string =>
  Object.entries(env ?? {})
    .map(([name, value]) => `${name}=${value}`)
    .join('\n');

const lines = (text: string): string[] =>
  text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');

/** What the section holds while the author edits it. */
export interface EnvironmentDraft {
  mode: 'steps' | 'devcontainer';
  setup: string;
  settings: string;
  folders: string;
  devcontainer: string;
  /** The environment's members this build does not edit (an edition's section edits them). */
  extra: Readonly<Record<string, unknown>>;
}

export function draftOf(environment: AgentEnvironment | null): EnvironmentDraft {
  return {
    mode: environment?.level === 'repository' ? 'devcontainer' : 'steps',
    setup: environment?.setup ?? '',
    settings: settingsText(environment?.env),
    folders: (environment?.cachePaths ?? []).join('\n'),
    devcontainer: environment?.devcontainer ?? '',
    extra: Object.fromEntries(Object.entries(environment ?? {}).filter(([member]) => !OWN_MEMBERS.has(member))),
  };
}

/** The draft as the environment it saves (`null`: nothing set), or why it cannot be saved yet. */
export function environmentOf(draft: EnvironmentDraft): { environment: AgentEnvironment | null } | { problem: EnvironmentProblem } {
  const folders = lines(draft.folders);
  if (folders.length > MAX_CACHE_FOLDERS || folders.some((folder) => !folder.startsWith('/') || /\s/.test(folder))) {
    return { problem: { key: 'agentBuilder.environment.problem.folder' } };
  }
  const extra = Object.fromEntries(Object.entries(draft.extra).filter(([member]) => !OWN_MEMBERS.has(member)));
  const shared = {
    ...extra,
    ...(folders.length > 0 ? { cachePaths: folders } : {}),
  };
  if (draft.mode === 'devcontainer') {
    const document = draft.devcontainer.trim();
    if (document === '') return { environment: null };
    try {
      const parsed: unknown = JSON.parse(document);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object');
    } catch {
      return { problem: { key: 'agentBuilder.environment.problem.devcontainer' } };
    }
    return { environment: { environmentId: ENVIRONMENT_ID, level: 'repository', devcontainer: document, ...shared } };
  }
  const settings = settingsOf(draft.settings);
  if ('problem' in settings) return settings;
  const setup = draft.setup.trim();
  if (setup === '' && Object.keys(settings.env).length === 0 && Object.keys(extra).length === 0) return { environment: null };
  return {
    environment: {
      environmentId: ENVIRONMENT_ID,
      level: 'agent',
      ...(setup !== '' ? { setup } : {}),
      ...(Object.keys(settings.env).length > 0 ? { env: settings.env } : {}),
      ...shared,
    },
  };
}

/** The agent's stored environment, read defensively (an older agent has none). */
export function agentEnvironmentOf(value: unknown): AgentEnvironment | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const stored = value as Record<string, unknown>;
  if (stored['level'] !== 'agent' && stored['level'] !== 'repository') return null;
  return stored as unknown as AgentEnvironment;
}

/**
 * `environment` as the save sends it: the edited one; `{}` when the author cleared the environment of an agent that
 * had one (so the server clears it); nothing for an agent that never had one.
 */
export function agentEnvironmentPayload(edited: unknown, stored: unknown): { environment?: AgentEnvironment | Record<string, never> } {
  const environment = agentEnvironmentOf(edited);
  if (environment) return { environment };
  return agentEnvironmentOf(stored) ? { environment: {} } : {};
}

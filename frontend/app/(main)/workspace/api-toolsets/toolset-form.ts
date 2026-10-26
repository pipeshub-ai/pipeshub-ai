import type {
  ApiAuthScheme,
  ApiCompiledSpec,
  ApiOperationSummary,
  ApiToolset,
  ApiToolsetAuthType,
  ApiToolsetConfig,
  ApiToolsetCredentials,
  ApiToolsetPayload,
} from './types';

/** The backend always accepts this scheme name, whatever the spec declares. */
export const NO_AUTH_SCHEME: ApiAuthScheme = { name: 'none', type: 'none', description: '', scopes: {} };

export const DEFAULT_TIMEOUT_SECONDS = 30;
export const MAX_TIMEOUT_SECONDS = 120;

export interface ToolsetForm {
  name: string;
  description: string;
  baseUrl: string;
  authSchemeName: string;
  scopes: string[];
  useSharedCredential: boolean;
  timeoutSeconds: number;
  oauthClientId: string;
  oauthClientSecret: string;
  operations: string[];
}

export type CredentialField = keyof ApiToolsetCredentials;

const CREDENTIAL_FIELDS: Record<ApiToolsetAuthType, CredentialField[]> = {
  none: [],
  api_key: ['apiKey'],
  http_bearer: ['token'],
  http_basic: ['username', 'password'],
  oauth2_client_credentials: ['clientId', 'clientSecret'],
  // Connected by signing in, not by typing a secret.
  oauth2_authorization_code: [],
};

export function credentialFields(authType: ApiToolsetAuthType): CredentialField[] {
  return CREDENTIAL_FIELDS[authType] ?? [];
}

export const isSecretField = (field: CredentialField) => field !== 'username' && field !== 'clientId';

export const usesSignIn = (authType: ApiToolsetAuthType) => authType === 'oauth2_authorization_code';

export const isOAuth2 = (authType: ApiToolsetAuthType) =>
  authType === 'oauth2_client_credentials' || authType === 'oauth2_authorization_code';

/** Client credentials act as the app, not a person, so the backend keeps one shared credential. */
export const forcesSharedCredential = (authType: ApiToolsetAuthType) => authType === 'oauth2_client_credentials';

/**
 * Who connects a toolset, from the viewer's side:
 * `none` needs no credential, `own` is the viewer's own, `shared` is the admin managing the one
 * shared credential, and `managed` is a member using that shared credential.
 */
export type ConnectionMode = 'none' | 'own' | 'shared' | 'managed';

export function connectionMode(toolset: Pick<ApiToolset, 'authType' | 'useSharedCredential'>, isAdmin: boolean): ConnectionMode {
  if (toolset.authType === 'none') return 'none';
  if (!toolset.useSharedCredential) return 'own';
  return isAdmin ? 'shared' : 'managed';
}

/** What a card says about the viewer's access: `open` needs no credential at all. */
export type ConnectionStatus = 'connected' | 'open' | 'notConnected';

export function connectionStatus(toolset: Pick<ApiToolset, 'authType' | 'isConnected'>): ConnectionStatus {
  if (toolset.authType === 'none') return 'open';
  return toolset.isConnected ? 'connected' : 'notConnected';
}

/** The i18n key of a card's button: connect when the viewer can and hasn't, else look at the details. */
export function connectionCta(toolset: Pick<ApiToolset, 'authType' | 'useSharedCredential' | 'isConnected'>, isAdmin: boolean): string {
  const mode = connectionMode(toolset, isAdmin);
  if (mode === 'own' || mode === 'shared') {
    return toolset.isConnected ? 'workspace.apiToolsets.cta.manageConnection' : 'workspace.apiToolsets.cta.connect';
  }
  return 'workspace.apiToolsets.cta.details';
}

export function kindCounts(operations: Pick<ApiOperationSummary, 'kind'>[]): Record<ApiOperationSummary['kind'], number> {
  const counts = { read: 0, write: 0, destructive: 0 };
  for (const op of operations) counts[op.kind] += 1;
  return counts;
}

/** A service-account agent runs as nobody, so it can only use toolsets that don't need a member's credential. */
export const usableByServiceAccount = (toolset: Pick<ApiToolset, 'authType' | 'useSharedCredential'>) =>
  toolset.authType === 'none' || toolset.useSharedCredential;

/** Selected operations the spec says need credentials, which fail when the toolset sends none. */
export const operationsNeedingAuth = (
  operations: ApiOperationSummary[],
  selected: string[],
  authType: ApiToolsetAuthType,
): ApiOperationSummary[] =>
  authType === 'none' ? operations.filter((op) => op.requiresAuth && selected.includes(op.toolName)) : [];

/** Reads are safe to offer an agent by default; writes and deletes are opt-in. */
export const defaultOperationSelection = (operations: ApiOperationSummary[], authType: ApiToolsetAuthType) =>
  operations
    .filter((op) => op.kind === 'read' && !(authType === 'none' && op.requiresAuth))
    .map((op) => op.toolName);

/** The selection with one operation added or removed, kept in the spec's order. */
export function toggleOperation(
  operations: ApiOperationSummary[],
  selected: string[],
  toolName: string,
  checked: boolean,
): string[] {
  const next = new Set(selected);
  if (checked) next.add(toolName);
  else next.delete(toolName);
  return operations.map((op) => op.toolName).filter((name) => next.has(name));
}

export const authSchemeOptions = (spec: ApiCompiledSpec): ApiAuthScheme[] => [NO_AUTH_SCHEME, ...spec.authSchemes];

/** The spec's first secured scheme is what most APIs expect; `none` when it declares none. */
export function formFromSpec(spec: ApiCompiledSpec): ToolsetForm {
  return {
    name: spec.title,
    description: '',
    baseUrl: spec.servers[0]?.url ?? '',
    authSchemeName: spec.authSchemes[0]?.name ?? NO_AUTH_SCHEME.name,
    scopes: [],
    useSharedCredential: spec.authSchemes[0] ? forcesSharedCredential(spec.authSchemes[0].type) : false,
    timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
    oauthClientId: '',
    oauthClientSecret: '',
    operations: defaultOperationSelection(spec.operations, spec.authSchemes[0]?.type ?? 'none'),
  };
}

export function formFromConfig(config: ApiToolsetConfig): ToolsetForm {
  return {
    name: config.name,
    description: config.description,
    baseUrl: config.baseUrl,
    authSchemeName: config.authScheme.name,
    scopes: config.scopes,
    useSharedCredential: config.useSharedCredential,
    timeoutSeconds: config.timeoutSeconds,
    oauthClientId: config.oauthClientId ?? '',
    oauthClientSecret: '',
    operations: config.operations.map((op) => op.toolName),
  };
}

/** A re-uploaded spec keeps the operations already chosen that it still offers, and the settings typed so far. */
export function rebaseFormOnSpec(form: ToolsetForm, spec: ApiCompiledSpec): ToolsetForm {
  const offered = new Set(spec.operations.map((op) => op.toolName));
  const scheme = spec.authSchemes.find((s) => s.name === form.authSchemeName);
  // "No authentication" is offered whatever the spec says, so it survives a new spec.
  const keepsNoAuth = form.authSchemeName === NO_AUTH_SCHEME.name;
  return {
    ...form,
    authSchemeName: scheme || keepsNoAuth ? form.authSchemeName : formFromSpec(spec).authSchemeName,
    scopes: scheme ? form.scopes.filter((s) => s in scheme.scopes) : [],
    operations: form.operations.filter((name) => offered.has(name)),
  };
}

/** Choosing a scheme resets what only made sense for the previous one. */
export function withAuthScheme(form: ToolsetForm, scheme: ApiAuthScheme): ToolsetForm {
  return {
    ...form,
    authSchemeName: scheme.name,
    scopes: [],
    useSharedCredential: forcesSharedCredential(scheme.type) || (scheme.type !== 'none' && form.useSharedCredential),
  };
}

export type FormProblem = 'name' | 'baseUrl' | 'operations' | 'timeout' | 'oauthClientId';

export function formProblems(form: ToolsetForm, authType: ApiToolsetAuthType): FormProblem[] {
  const problems: FormProblem[] = [];
  if (!form.name.trim()) problems.push('name');
  if (!form.baseUrl.trim()) problems.push('baseUrl');
  if (form.operations.length === 0) problems.push('operations');
  if (!(form.timeoutSeconds >= 1 && form.timeoutSeconds <= MAX_TIMEOUT_SECONDS)) problems.push('timeout');
  if (usesSignIn(authType) && !form.oauthClientId.trim()) problems.push('oauthClientId');
  return problems;
}

/** `spec` is left out on an edit that keeps the stored one. */
export function buildPayload(form: ToolsetForm, authType: ApiToolsetAuthType, spec?: string): ApiToolsetPayload {
  const signIn = usesSignIn(authType);
  const secret = form.oauthClientSecret.trim();
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    ...(spec !== undefined ? { spec } : {}),
    baseUrl: form.baseUrl.trim(),
    authSchemeName: form.authSchemeName,
    operations: form.operations,
    scopes: isOAuth2(authType) ? form.scopes : [],
    useSharedCredential: authType !== 'none' && (forcesSharedCredential(authType) || form.useSharedCredential),
    timeoutSeconds: form.timeoutSeconds,
    ...(signIn ? { oauthClientId: form.oauthClientId.trim() } : {}),
    ...(signIn && secret ? { oauthClientSecret: secret } : {}),
  };
}

/** The credential body for the toolset's auth type: only its fields, trimmed, none blank. */
export function buildCredentials(
  authType: ApiToolsetAuthType,
  values: Partial<Record<CredentialField, string>>,
): ApiToolsetCredentials | null {
  const fields = credentialFields(authType);
  const body: ApiToolsetCredentials = {};
  for (const field of fields) {
    const raw = values[field] ?? '';
    if (!raw.trim()) return null;
    // A password may legitimately start or end with a space; pasted keys and tokens often carry a stray newline.
    body[field] = field === 'password' ? raw : raw.trim();
  }
  return fields.length > 0 ? body : null;
}

/** Which API actions a catalog tab shows. Constants, so a page can pass one without memoizing. */
export type ApiActionFilter = (toolset: ApiToolset) => boolean;

export const API_ACTION_FILTERS = {
  all: () => true,
  none: () => false,
  connected: (toolset) => toolset.isConnected,
  notConnected: (toolset) => !toolset.isConnected,
} satisfies Record<string, ApiActionFilter>;

/** The catalog search, over what a card shows. */
export function matchesSearch(toolset: Pick<ApiToolset, 'name' | 'description' | 'specTitle'>, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || [toolset.name, toolset.description, toolset.specTitle].some((text) => text.toLowerCase().includes(q));
}

/** True for an address that, seen from PipesHub's server, is the server itself; the backend refuses those. */
export function isLoopbackUrl(url: string): boolean {
  try {
    const host = new URL(url.trim()).hostname.replace(/^\[|\]$/g, '');
    return host === 'localhost' || host.endsWith('.localhost') || host.startsWith('127.') || host === '::1' || host === '0.0.0.0';
  } catch {
    return false;
  }
}

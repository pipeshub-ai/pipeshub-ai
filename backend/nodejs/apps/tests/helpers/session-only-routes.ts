import type { Router } from 'express';
import { requireSessionAuth } from '../../src/libs/middlewares/require-session-auth.middleware';

// Some writes decide how someone proves who they are, or where account-recovery
// messages go: sign-in settings, the public URLs that sign-in and reset links are
// built from, the mail transport, a user's own sign-in address, who becomes admin.
// Others mint or hold credentials, point stored credentials at another host, or
// change the organization's structure or org-wide security settings.
// Those take an interactive user session; a PAT, an OAuth access token or a
// service token is refused, whatever scopes it holds.
//
// This walks the built routers, mounted sub-routers and router-level middleware
// included, and checks the handler chain of every route in that class.

export interface RouteChain {
  id: string;
  method: string;
  path: string;
  handlers: unknown[];
  /** Index of the route's own first handler; the ones before it are router-level middleware. */
  ownStart: number;
}

interface Layer {
  route?: {
    path: string;
    methods: Record<string, boolean>;
    stack: Array<{ handle: unknown; method?: string }>;
  };
  name?: string;
  handle: unknown;
  regexp?: RegExp & { fast_slash?: boolean };
  keys?: Array<{ name: string | number }>;
}

/**
 * The path a router is mounted at, read back from the layer's regular expression:
 * plain segments and `:param` segments. A mount it cannot read (an array of paths,
 * a pattern) throws, so a sub-router is never scanned under the wrong path.
 */
export const mountPrefix = (layer: Layer): string => {
  if (!layer.regexp || layer.regexp.fast_slash) return '';
  const names = (layer.keys ?? []).map((k) => String(k.name));
  const source = layer.regexp.source;
  const unreadable = () =>
    new Error(`cannot read the mount path of a router layer: ${source}`);
  if (!source.startsWith('^') || !source.endsWith('\\/?(?=\\/|$)'))
    throw unreadable();
  let body = source.slice(1, source.length - '\\/?(?=\\/|$)'.length);
  let i = 0;
  body = body.split('(?:\\/([^/]+?))').join('\uE000');
  if (!/^(?:\\\/[A-Za-z0-9_-]+|\uE000)+$/.test(body)) throw unreadable();
  return body
    .replace(/\uE000/g, () => `/:${names[i++] ?? 'param'}`)
    .replace(/\\\//g, '/');
};

/**
 * Every route with its full path and the handlers that run before and in it:
 * middleware mounted ahead of it on the router (or on a parent router) comes
 * first, then the route's own handlers.
 */
export function routeChains(
  router: Router,
  prefix = '',
  inherited: unknown[] = [],
): RouteChain[] {
  const out: RouteChain[] = [];
  const ahead: unknown[] = [...inherited];
  for (const layer of (router as unknown as { stack: Layer[] }).stack) {
    if (layer.route) {
      for (const method of Object.keys(layer.route.methods)) {
        const path = `${prefix}${layer.route.path}`;
        // One route() can carry several methods; Express runs only this method's handlers and `all()` ones.
        const handlers = [
          ...ahead,
          ...layer.route.stack
            .filter((s) => s.method === undefined || s.method === method)
            .map((s) => s.handle),
        ];
        out.push({
          id: `${method.toUpperCase()} ${path}`,
          method: method.toUpperCase(),
          path,
          handlers,
          ownStart: ahead.length,
        });
      }
    } else if (
      layer.name === 'router' &&
      Array.isArray((layer.handle as { stack?: unknown[] })?.stack)
    ) {
      out.push(
        ...routeChains(
          layer.handle as Router,
          `${prefix}${mountPrefix(layer)}`,
          ahead,
        ),
      );
    } else if (
      typeof layer.handle === 'function' &&
      mountPrefix(layer) === ''
    ) {
      ahead.push(layer.handle);
    }
  }
  return out;
}

export interface AllowlistSpec {
  allowed: Record<string, true | string[]>;
  emptyAllowed?: string[];
}

export interface IdentityControlSpec {
  /** Every non-read route at or under one of these paths. */
  prefixes: string[];
  /** Routes that are session-only as a whole, as `METHOD /path`. */
  routes: string[];
  /** Routes that are session-only only when the body sets one of these fields. */
  fieldRoutes: Record<string, string[]>;
  /**
   * Routes where a token may send only these keys. The whole allowlist is compared, nested
   * lists and the keys that may be present but empty included, so loosening any of it fails here.
   */
  allowlistRoutes?: Record<string, AllowlistSpec>;
  /** Every non-read route whose path matches one of these. */
  patterns?: RegExp[];
  /** Every non-read route of the router. */
  everyWrite?: boolean;
  /**
   * The router authenticates in router-level middleware followed by other router-level
   * middleware (a rate limiter, an admin check). The guard then has to be the route's
   * own first handler, ahead of its scope check.
   */
  routerLevelAuth?: boolean;
}

export const EMPTY_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: [],
  fieldRoutes: {},
};

export const mergeSpecs = (
  ...specs: IdentityControlSpec[]
): IdentityControlSpec => ({
  prefixes: specs.flatMap((s) => s.prefixes),
  routes: specs.flatMap((s) => s.routes),
  fieldRoutes: Object.assign({}, ...specs.map((s) => s.fieldRoutes)),
  allowlistRoutes: Object.assign(
    {},
    ...specs.map((s) => s.allowlistRoutes ?? {}),
  ),
  patterns: specs.flatMap((s) => s.patterns ?? []),
  everyWrite: specs.some((s) => s.everyWrite),
  routerLevelAuth: specs.some((s) => s.routerLevelAuth),
});

// Shared configuration router.
export const CONFIG_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [
    '/authConfig',
    '/frontendPublicUrl',
    '/connectorPublicUrl',
    '/smtpConfig',
    '/storageConfig',
    '/slack-bot',
    '/platform/settings',
    '/prompts/system',
    '/mcp/access',
    '/metricsCollection/serverUrl',
    '/aiModelsConfig',
  ],
  // Any write under /ai-models (providers, connections, credentials, roles, defaults,
  // identity forwarding) except the read-only identity preview.
  patterns: [/^\/ai-models\/(?!identity\/preview$)/i],
  routes: [
    'POST /authConfig/microsoft',
    'POST /authConfig/google',
    'POST /frontendPublicUrl',
    'POST /slack-bot/identity-links/requests/inspect',
    'POST /slack-bot/identity-links/requests/decide',
    'DELETE /slack-bot/identity-links/:teamId/:slackUserId',
    'POST /connectorPublicUrl',
    'POST /smtpConfig',
    'POST /storageConfig',
    'POST /slack-bot',
    'PUT /slack-bot/:configId',
    'DELETE /slack-bot/:configId',
    'POST /platform/settings',
    'PUT /prompts/system',
    'PUT /mcp/access',
    'PATCH /metricsCollection/serverUrl',
    'POST /aiModelsConfig',
    'PUT /ai-models/roles',
    'POST /ai-models/discover',
    'POST /ai-models/providers/batch',
    'PUT /ai-models/connections/:connectionId/credentials',
    'PUT /ai-models/connections/:connectionId/identity-forwarding',
    'PUT /ai-models/identity-policy',
    'POST /ai-models/providers',
    'POST /ai-models/prepare-model',
    'PUT /ai-models/providers/:modelType/:modelKey',
    'DELETE /ai-models/providers/:modelType/:modelKey',
    'PUT /ai-models/default/:modelType/:modelKey',
    'POST /web-search/providers',
    'PUT /web-search/providers/:providerKey',
  ],
  fieldRoutes: {},
};

// Shared connectors router: what a token may send when it changes or creates a connector.
// Everything else (`auth`, `baseUrl`, `oauthConfigId`, any key added later) is session-only,
// even when empty, except the empty `auth` of the connection check and the empty `config.auth`
// of create that the SDK examples and mcp-server send.
export const CONNECTORS_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: [],
  fieldRoutes: {},
  allowlistRoutes: {
    'PUT /:connectorId/config': { allowed: { sync: true, filters: true } },
    'PUT /:connectorId/config/auth': { allowed: {} },
    'POST /registry/:connectorType/test-connection': {
      allowed: { authType: true, connectorId: true },
      emptyAllowed: ['auth'],
    },
    'POST /': {
      allowed: {
        connectorType: true,
        instanceName: true,
        scope: true,
        authType: true,
        config: ['sync', 'filters'],
      },
      emptyAllowed: ['config.auth'],
    },
  },
};

// Shared connector OAuth config router: an OAuth app holds a client secret and the URLs a
// grant is sent to, and connectors link to it; creating, changing or deleting one is session-only.
export const OAUTH_CONFIG_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: [
    'POST /:connectorType',
    'PUT /:connectorType/:configId',
    'DELETE /:connectorType/:configId',
  ],
  fieldRoutes: {},
};

// Shared toolsets router: credentials, OAuth app links and hosts of a toolset instance.
export const TOOLSETS_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: [
    'PUT /oauth-configs/:toolsetType/:oauthConfigId',
    'DELETE /oauth-configs/:toolsetType/:oauthConfigId',
  ],
  fieldRoutes: {},
  allowlistRoutes: {
    'POST /instances': {
      allowed: {
        instanceName: true,
        toolsetType: true,
        authType: true,
        scope: true,
      },
    },
    'PUT /instances/:instanceId': { allowed: { instanceName: true } },
    'POST /': { allowed: { name: true, displayName: true, type: true } },
    'POST /:toolsetId/config': { allowed: {} },
    'PUT /:toolsetId/config': { allowed: {} },
  },
};

// Shared service token router: minting one; listing and revoking stay open.
export const SERVICE_TOKEN_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: ['POST /'],
  fieldRoutes: {},
  routerLevelAuth: true,
};

// Shared service account router: creating or changing one; listing and removing stay open.
export const SERVICE_ACCOUNT_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: ['POST /', 'PATCH /:id'],
  fieldRoutes: {},
  routerLevelAuth: true,
};

// Shared crawling manager router.
export const CRAWLING_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: ['DELETE /schedule/all'],
  fieldRoutes: {},
};

// Shared org router.
export const ORG_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  routes: ['DELETE /'],
  fieldRoutes: {},
};

// Shared user router.
export const USER_ROUTER_SPEC: IdentityControlSpec = {
  prefixes: [],
  // Any route about a user's email address, whatever it is called.
  patterns: [/^\/:[^/]+\/email/i],
  routes: ['PATCH /:id/email', 'PUT /:id/unblock'],
  fieldRoutes: {
    'PUT /:id': ['email', 'role'],
    'POST /': ['role'],
    'POST /bulk/invite': ['role'],
    'POST /bulk/invite/upload': ['role'],
  },
};

const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

// Express routers match paths case-insensitively by default.
const underPrefix = (path: string, prefix: string) => {
  const p = path.toLowerCase();
  const pre = prefix.toLowerCase();
  return p === pre || p.startsWith(`${pre}/`);
};

const isInternalOrPublic = (path: string) =>
  path.startsWith('/internal/') || path.startsWith('/public/');

// No token may change organization configuration: a route that asks a token for
// config:write must refuse tokens before it gets that far.
const CONFIG_WRITE = 'config:write';

/** Index of the first scope check in a chain that asks for `scope`, or -1. */
const scopeCheckIndex = (handlers: unknown[], scope: string) =>
  handlers.findIndex((h) => {
    const required = (h as { requiredScopes?: readonly string[] })
      .requiredScopes;
    return Array.isArray(required) && required.includes(scope);
  });

/** Index of the field guard in a chain, or -1. */
const fieldGuardIndex = (handlers: unknown[], fields: string[]) =>
  handlers.findIndex((h) => {
    const covered = (h as { sessionOnlyFields?: string[] }).sessionOnlyFields;
    return Array.isArray(covered) && fields.every((f) => covered.includes(f));
  });

/** Stable text of an allowlist, nested lists and empty-allowed keys included. */
const allowlistText = (
  allowed: Record<string, unknown>,
  emptyAllowed: readonly string[] = [],
) =>
  JSON.stringify({
    allowed: Object.keys(allowed)
      .sort()
      .map((k) => [
        k,
        Array.isArray(allowed[k])
          ? [...(allowed[k] as string[])].sort()
          : allowed[k],
      ]),
    emptyAllowed: [...emptyAllowed].sort(),
  });

/** Index of the allowlist guard in a chain whose allowlist is exactly `spec`, or -1. */
const allowlistGuardIndex = (handlers: unknown[], spec: AllowlistSpec) =>
  handlers.findIndex((h) => {
    const guard = h as {
      sessionOnlyAllowlist?: Record<string, unknown>;
      sessionOnlyEmptyAllowed?: string[];
    };
    if (
      guard.sessionOnlyAllowlist === undefined ||
      typeof guard.sessionOnlyAllowlist !== 'object'
    )
      return false;
    return (
      allowlistText(
        guard.sessionOnlyAllowlist,
        guard.sessionOnlyEmptyAllowed,
      ) === allowlistText(spec.allowed, spec.emptyAllowed)
    );
  });

/**
 * Routes that should be session-only but are not, and expected routes that are
 * missing. `authenticate` is the stand-in the router was built with: the session
 * guard has to come straight after it, before any scope or admin check.
 */
export function identityControlViolations(
  chains: RouteChain[],
  spec: IdentityControlSpec,
  authenticate: unknown,
): string[] {
  const problems: string[] = [];
  const ids = new Set(chains.map((c) => c.id));
  for (const id of [
    ...spec.routes,
    ...Object.keys(spec.fieldRoutes),
    ...Object.keys(spec.allowlistRoutes ?? {}),
  ]) {
    if (!ids.has(id)) problems.push(`${id}: expected route not found`);
  }
  for (const chain of chains) {
    const configWrite = scopeCheckIndex(chain.handlers, CONFIG_WRITE);
    if (configWrite >= 0) {
      const guard = chain.handlers.indexOf(requireSessionAuth);
      const at = chain.handlers.indexOf(authenticate);
      if (guard < 0 || guard > configWrite) {
        problems.push(
          `${chain.id}: asks a token for config:write without requireSessionAuth before it`,
        );
      } else if (at < 0 || at > guard) {
        // The guard needs the signed-in user; ahead of authenticate it refuses everyone.
        problems.push(
          `${chain.id}: requireSessionAuth runs before authenticate`,
        );
      }
    }
    const inClass =
      !READ_METHODS.has(chain.method) &&
      !isInternalOrPublic(chain.path) &&
      (spec.everyWrite === true ||
        spec.prefixes.some((p) => underPrefix(chain.path, p)) ||
        (spec.patterns ?? []).some((re) => re.test(chain.path)) ||
        spec.routes.includes(chain.id));
    const fields = spec.fieldRoutes[chain.id];
    if (inClass) {
      const at = chain.handlers.indexOf(authenticate);
      if (at < 0) problems.push(`${chain.id}: no authenticate step`);
      else if (spec.routerLevelAuth === true && at < chain.ownStart) {
        if (chain.handlers[chain.ownStart] !== requireSessionAuth) {
          problems.push(
            `${chain.id}: requireSessionAuth is not the route's first handler`,
          );
        }
      } else if (chain.handlers[at + 1] !== requireSessionAuth) {
        problems.push(
          `${chain.id}: requireSessionAuth does not directly follow authenticate`,
        );
      }
    } else if (spec.allowlistRoutes?.[chain.id]) {
      const at = chain.handlers.indexOf(authenticate);
      const guard = allowlistGuardIndex(
        chain.handlers,
        spec.allowlistRoutes[chain.id]!,
      );
      if (at < 0 || guard <= at || guard >= chain.handlers.length - 1) {
        problems.push(
          `${chain.id}: no allowlist guard with exactly ${allowlistText(spec.allowlistRoutes[chain.id]!.allowed, spec.allowlistRoutes[chain.id]!.emptyAllowed)} between authenticate and the handler`,
        );
      }
    } else if (fields) {
      const at = chain.handlers.indexOf(authenticate);
      const guard = fieldGuardIndex(chain.handlers, fields);
      if (at < 0 || guard <= at || guard >= chain.handlers.length - 1) {
        problems.push(
          `${chain.id}: no session guard for ${fields.join(', ')} between authenticate and the handler`,
        );
      }
    }
  }
  return problems;
}

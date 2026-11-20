/**
 * The one place that logs an outbound HTTP call: one `upstream.call.end` line per attempt, for the
 * service commands' fetch (`upstreamFetch`) and for any instrumented axios instance
 * (`installUpstreamLogging`). Calls to a registered PipesHub service also carry the request id.
 * No URL, query, header, body or error message is logged: the route and the class say what and why.
 */
import axios, { AxiosAdapter, AxiosInstance } from 'axios';
import {
  HEADER_REQUEST_ID,
  injectRequestHeaders,
} from '../context/request-context';
import { InternalServerError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { classifyError, levelFor } from '../utils/error-class';

const EVENT = 'upstream.call.end';
const EXTERNAL = 'external';
const MAX_CAUSE_DEPTH = 8;

interface RegisteredBase {
  name: string;
  origin: string;
  path: string;
}

let registered: RegisteredBase[] = [];

const parseUrl = (url: string | undefined): URL | undefined => {
  if (url === undefined || url === '') return undefined;
  try {
    return new URL(url);
  } catch {
    return undefined;
  }
};

type UpstreamBases = Record<string, ReadonlyArray<string | undefined>>;

const toRegistered = (bases: UpstreamBases): RegisteredBase[] =>
  Object.entries(bases).flatMap(([name, urls]) =>
    urls.flatMap((url) => {
      const parsed = parseUrl(url);
      return parsed
        ? [
            {
              name,
              origin: parsed.origin,
              path: parsed.pathname.replace(/\/+$/, ''),
            },
          ]
        : [];
    }),
  );

const longestPathFirst = (a: RegisteredBase, b: RegisteredBase): number =>
  b.path.length - a.path.length;

/**
 * The PipesHub services by name, each with its base URLs; replaces what was registered. A URL matches a
 * base on the same origin whose path is a whole-segment prefix of its own; the longest base path wins.
 */
export function registerUpstreams(bases: UpstreamBases): void {
  registered = toRegistered(bases).sort(longestPathFirst);
}

/** Like `registerUpstreams`, but every base registered before stays registered too. */
export function addUpstreams(bases: UpstreamBases): void {
  const key = (b: RegisteredBase): string => `${b.name} ${b.origin}${b.path}`;
  const known = new Set(registered.map(key));
  registered = [
    ...registered,
    ...toRegistered(bases).filter((b) => !known.has(key(b))),
  ].sort(longestPathFirst);
}

export interface UpstreamTarget {
  /** A registered service's name, or `external`. */
  upstream: string;
  /** A PipesHub service: the path with every id-like segment as `:id`. Anything else: the host only. */
  route: string;
  internal: boolean;
  /** A `health` path segment: a probe, logged at DEBUG whatever its outcome. */
  health: boolean;
}

const WORD_SEGMENT = /^(?:v\d+|[a-z][a-z_-]{0,39})$/;
const HEX_ID = /^[0-9a-f]{8,}$/;

// shortcut: a lowercase word a user chose (a skill name) stays readable; pass an explicit route if one ever carries personal data.
const templatePath = (pathname: string): string =>
  pathname
    .split('/')
    .map((segment) =>
      segment === '' || (WORD_SEGMENT.test(segment) && !HEX_ID.test(segment))
        ? segment
        : ':id',
    )
    .join('/');

const underBase = (pathname: string, base: RegisteredBase): boolean =>
  pathname === base.path || pathname.startsWith(`${base.path}/`);

// The path as a server that percent-decodes before routing sees it, dot segments resolved: `..%2F` must not climb out of a base.
const decodedPath = (pathname: string): string | undefined => {
  try {
    const decoded = decodeURIComponent(pathname).replace(
      /[?#]/g,
      encodeURIComponent,
    );
    return new URL(`http://h${decoded}`).pathname;
  } catch {
    return undefined;
  }
};

const registeredBase = ({
  origin,
  pathname,
}: URL): RegisteredBase | undefined => {
  const decoded = decodedPath(pathname);
  return registered.find(
    (b) =>
      b.origin === origin &&
      underBase(pathname, b) &&
      decoded !== undefined &&
      underBase(decoded, b),
  );
};

export function upstreamTarget(url: string): UpstreamTarget {
  const parsed = parseUrl(url);
  if (!parsed) {
    return { upstream: EXTERNAL, route: '-', internal: false, health: false };
  }
  const { pathname, host } = parsed;
  const health = pathname.split('/').includes('health');
  const base = registeredBase(parsed);
  return base
    ? {
        upstream: base.name,
        route: templatePath(pathname),
        internal: true,
        health,
      }
    : { upstream: EXTERNAL, route: host, internal: false, health };
}

export interface UpstreamCall {
  readonly method: string;
  readonly target: UpstreamTarget;
  readonly attempt: number;
  readonly started: bigint;
  ended: boolean;
}

const startUpstreamCall = (
  method: string,
  url: string,
  attempt: number,
): UpstreamCall => ({
  method: method.toUpperCase(),
  target: upstreamTarget(url),
  attempt,
  started: process.hrtime.bigint(),
  ended: false,
});

/** The first string `code` in the error's cause chain: undici's `ECONNREFUSED`, `UND_ERR_SOCKET`, axios's own. */
const networkCode = (error: unknown): string | undefined => {
  let link = error;
  for (let depth = 0; depth < MAX_CAUSE_DEPTH; depth += 1) {
    if (link === null || typeof link !== 'object') return undefined;
    const { code, cause } = link as { code?: unknown; cause?: unknown };
    if (typeof code === 'string') return code;
    link = cause;
  }
  return undefined;
};

/** Writes the call's line, once. `status` when an answer came; `error` when it failed (midway, for a stream). */
export function endUpstreamCall(
  call: UpstreamCall,
  result: { status?: number; error?: unknown },
): void {
  if (call.ended) return;
  call.ended = true;
  const { status, error } = result;
  const errorClass =
    error !== undefined
      ? classifyError(error)
      : status !== undefined && status >= 400
        ? classifyError(status)
        : undefined;
  const level =
    errorClass === undefined || call.target.health
      ? 'debug'
      : levelFor(errorClass);
  try {
    Logger.getInstance({ service: 'UpstreamCall' })[level](EVENT, {
      upstream: call.target.upstream,
      method: call.method,
      route: call.target.route,
      status,
      duration_ms: Math.round(
        Number(process.hrtime.bigint() - call.started) / 1e6,
      ),
      attempt: call.attempt,
      outcome: errorClass === undefined ? 'success' : 'failure',
      error_class: errorClass,
      network_code: status === undefined ? networkCode(error) : undefined,
      event_name: EVENT,
    });
  } catch {
    // A log line that cannot be written must never fail the call it describes.
  }
}

const refusals = new WeakSet();

const refuse = (message: string): InternalServerError => {
  const error = new InternalServerError(message);
  refusals.add(error);
  return error;
};

/**
 * Whether `error` is this module's refusal of a destination. It answers the same on every attempt, and after a
 * redirect the request was already sent, so a retry must not send it again.
 */
export const isUpstreamRefusal = (error: unknown): boolean =>
  typeof error === 'object' && error !== null && refusals.has(error);

const INVALID_URL = 'Blocked request: URL is not valid';

/**
 * The URL, normalised, when it may be sent: an absolute `http:`/`https:` URL without credentials, under a
 * registered PipesHub service base. Anything else throws, and no message carries the URL.
 */
export function checkUpstreamUrl(url: string): string {
  const parsed = parseUrl(url);
  if (!parsed) {
    throw refuse(INVALID_URL);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    // Fixed text: a redirect's Location chooses the scheme.
    throw refuse('Blocked request: URL scheme must be http or https');
  }
  if (parsed.username !== '' || parsed.password !== '') {
    throw refuse('Blocked request: URL must not contain embedded credentials');
  }
  const base = registeredBase(parsed);
  if (!base) {
    throw refuse('Blocked request: not a registered PipesHub service');
  }
  // parsed.href (no credentials, so it starts with its origin), with the scheme, host and port taken from the
  // registered base's own string.
  return `${base.origin}${parsed.href.slice(parsed.origin.length)}`;
}

// A Location resolved against the URL that answered. A malformed one is refused like any invalid URL: the
// parser's own error would carry the Location text.
const redirectTarget = (location: string, from: string): string => {
  let resolved: string;
  try {
    resolved = new URL(location, from).href;
  } catch {
    throw refuse(INVALID_URL);
  }
  return checkUpstreamUrl(resolved);
};

const REDIRECTS = new Set([301, 302, 303, 307, 308]);
// fetch's own limit.
const MAX_REDIRECTS = 20;

// The headers fetch (undici) drops when a redirect changes origin.
const CROSS_ORIGIN_DROPPED = [
  'authorization',
  'proxy-authorization',
  'cookie',
  'host',
];

// The headers fetch (undici) drops when a redirect turns the request into a GET without a body.
const BODY_HEADERS = [
  'content-encoding',
  'content-language',
  'content-location',
  'content-type',
  'content-length',
];

// What fetch would send next: a 303, or a 301/302 after a POST, becomes a GET without a body or body
// headers, and a hop to another origin carries no credentials.
const redirectedInit = (
  status: number,
  init: RequestInit,
  crossOrigin: boolean,
): RequestInit => {
  const method = (init.method ?? 'GET').toUpperCase();
  const toGet =
    (status === 303 && method !== 'GET' && method !== 'HEAD') ||
    ((status === 301 || status === 302) && method === 'POST');
  const dropped = [
    ...(toGet ? BODY_HEADERS : []),
    ...(crossOrigin ? CROSS_ORIGIN_DROPPED : []),
  ];
  const next = toGet ? { ...init, method: 'GET', body: undefined } : init;
  if (dropped.length === 0 || next.headers === undefined) return next;
  const headers = new Headers(next.headers);
  for (const name of dropped) headers.delete(name);
  return { ...next, headers };
};

const sendOnce = async (
  url: string,
  init: RequestInit,
  attempt: number,
  holdEnd?: (call: UpstreamCall) => void,
): Promise<Response> => {
  const call = startUpstreamCall(init.method ?? 'GET', url, attempt);
  let response: Response;
  try {
    response = await fetch(url, { ...init, redirect: 'manual' });
  } catch (error) {
    endUpstreamCall(call, { error });
    throw error;
  }
  if (holdEnd && response.ok && response.body) holdEnd(call);
  else endUpstreamCall(call, { status: response.status });
  return response;
};

/**
 * `fetch` to a registered PipesHub service (`checkUpstreamUrl`; a refused URL is never sent and writes no
 * line), with a call line per request. A redirect is followed as fetch would, but only to a URL that passes
 * the same check (a connector route answers a missing trailing slash with a 307). The line ends at the
 * answer's headers, unless `holdEnd` is given and the answer is a 2xx with a body: the call is then handed
 * to it, and the caller ends it.
 */
export async function upstreamFetch(
  url: string,
  init: RequestInit,
  attempt: number = 1,
  holdEnd?: (call: UpstreamCall) => void,
): Promise<Response> {
  let target = checkUpstreamUrl(url);
  let request = init;
  for (let hops = 0; ; hops += 1) {
    const response = await sendOnce(target, request, attempt, holdEnd);
    const location = REDIRECTS.has(response.status)
      ? response.headers.get('location')
      : null;
    if (location === null) return response;
    // Not read any further, so its connection goes back to the pool.
    await response.body?.cancel();
    if (hops === MAX_REDIRECTS) {
      throw refuse('Blocked request: too many redirects');
    }
    const next = redirectTarget(location, target);
    const crossOrigin = new URL(next).origin !== new URL(target).origin;
    // As fetch does: a same-origin request never follows a redirect to another origin.
    if (crossOrigin && init.mode === 'same-origin') {
      throw refuse(
        'Blocked request: cross-origin redirect in same-origin mode',
      );
    }
    request = redirectedInit(response.status, request, crossOrigin);
    target = next;
  }
}

const wrappers = new WeakSet<AxiosAdapter>();

/**
 * Wraps the instance's adapter: every attempt (each axios-retry retry re-enters it) writes its call line,
 * and a call to a registered PipesHub service carries the request id. Idempotent, also on an instance
 * created from an instrumented one (it inherits the wrapper).
 */
export function installUpstreamLogging(instance: AxiosInstance): void {
  const current = instance.defaults.adapter;
  if (typeof current === 'function' && wrappers.has(current)) return;
  const inner = axios.getAdapter(current);
  const wrapper: AxiosAdapter = async (config) => {
    const retry = (config as { 'axios-retry'?: { retryCount?: number } })[
      'axios-retry'
    ];
    const call = startUpstreamCall(
      config.method ?? 'get',
      axios.getUri(config),
      (retry?.retryCount ?? 0) + 1,
    );
    if (call.target.internal) {
      for (const [name, value] of Object.entries(injectRequestHeaders({}))) {
        // The root id always wins (a header copied from the client must not reach the service); a caller's relay is kept.
        config.headers.set(name, value, name === HEADER_REQUEST_ID);
      }
    }
    try {
      const response = await inner(config);
      endUpstreamCall(call, { status: response.status });
      return response;
    } catch (error) {
      // A status that validateStatus refused is an answer, not a transport failure.
      const status = (error as { response?: { status?: unknown } } | null)
        ?.response?.status;
      endUpstreamCall(
        call,
        typeof status === 'number' ? { status } : { error },
      );
      throw error;
    }
  };
  wrappers.add(wrapper);
  instance.defaults.adapter = wrapper;
}

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

/**
 * The PipesHub services by name, each with its base URLs; replaces what was registered. A URL matches a
 * base on the same origin whose path is a whole-segment prefix of its own; the longest base path wins.
 */
export function registerUpstreams(
  bases: Record<string, ReadonlyArray<string | undefined>>,
): void {
  registered = Object.entries(bases)
    .flatMap(([name, urls]) =>
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
    )
    .sort((a, b) => b.path.length - a.path.length);
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

export function upstreamTarget(url: string): UpstreamTarget {
  const parsed = parseUrl(url);
  if (!parsed) {
    return { upstream: EXTERNAL, route: '-', internal: false, health: false };
  }
  const { origin, pathname, host } = parsed;
  const health = pathname.split('/').includes('health');
  const base = registered.find(
    (b) =>
      b.origin === origin &&
      (pathname === b.path || pathname.startsWith(`${b.path}/`)),
  );
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

/**
 * `fetch`, with the attempt's call line. The line ends at the answer's headers, unless `holdEnd` is
 * given and the answer is a 2xx with a body: the call is then handed to it, and the caller ends it.
 */
export async function upstreamFetch(
  url: string,
  init: RequestInit,
  attempt: number = 1,
  holdEnd?: (call: UpstreamCall) => void,
): Promise<Response> {
  const call = startUpstreamCall(init.method ?? 'GET', url, attempt);
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    endUpstreamCall(call, { error });
    throw error;
  }
  if (holdEnd && response.ok && response.body) holdEnd(call);
  else endUpstreamCall(call, { status: response.status });
  return response;
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

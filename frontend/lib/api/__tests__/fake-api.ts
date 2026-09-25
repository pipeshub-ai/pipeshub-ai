/** Fakes the backend behind `apiClient` at the axios adapter, the network boundary. */
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { apiClient } from '../axios-instance';

export interface FakeReply {
  status: number;
  data?: unknown;
  headers?: Record<string, string>;
}

type Route = FakeReply | FakeReply[] | ((config: InternalAxiosRequestConfig) => FakeReply | Promise<FakeReply>);

export interface SentRequest {
  method: string;
  url: string;
  body: unknown;
  config: InternalAxiosRequestConfig;
}

/**
 * Answers `"METHOD /path"` keys (query string ignored). An array answers one
 * reply per call and repeats its last. Unrouted requests get a 404.
 */
export function fakeApi(routes: Record<string, Route>) {
  const sent: SentRequest[] = [];
  const calls = new Map<string, number>();
  apiClient.defaults.adapter = async (config) => {
    const method = (config.method ?? 'get').toUpperCase();
    const url = (config.url ?? '').split('?')[0];
    const key = `${method} ${url}`;
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    sent.push({ method, url, body, config });
    const route = routes[key];
    const n = calls.get(key) ?? 0;
    calls.set(key, n + 1);
    const reply: FakeReply =
      route === undefined
        ? { status: 404, data: { message: `No fake for ${key}` } }
        : typeof route === 'function'
          ? await route(config)
          : Array.isArray(route)
            ? route[Math.min(n, route.length - 1)]
            : route;
    const response = {
      data: reply.data ?? {},
      status: reply.status,
      statusText: '',
      headers: new AxiosHeaders(reply.headers ?? {}),
      config,
    };
    if (reply.status >= 200 && reply.status < 300) return response;
    throw new AxiosError(`Request failed with status code ${reply.status}`, 'ERR_BAD_RESPONSE', config, null, response);
  };
  return {
    sent,
    count: (key: string) => sent.filter((r) => `${r.method} ${r.url}` === key).length,
  };
}

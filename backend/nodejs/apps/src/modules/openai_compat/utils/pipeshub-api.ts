/**
 * Calls this server's own routes with the caller's token, so the OpenAI files,
 * vector store and project endpoints keep every permission and scope check of
 * the knowledge base, retrieval and project routes they map onto.
 */
import { upstreamFetch } from '../../../libs/http/upstream-call';
import { injectRequestHeaders } from '../../../libs/context/request-context';
import {
  ChatRunError,
  codeForStatus,
  parseJson,
  upstreamErrorMessage,
} from './chat-run';

export interface ApiCaller {
  /** `appConfig.esBackend`: this Node API's own origin. */
  baseUrl: string;
  authorization: string;
}

export interface ApiRequest {
  method?: string;
  query?: Record<string, string | number | boolean | undefined>;
  json?: unknown;
  form?: FormData;
  accept?: string;
}

export async function callApi(
  caller: ApiCaller,
  path: string,
  request: ApiRequest = {},
): Promise<Response> {
  const url = new URL(`${caller.baseUrl.replace(/\/$/, '')}/api/v1${path}`);
  for (const [key, value] of Object.entries(request.query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  const headers: Record<string, string> = {
    Authorization: caller.authorization,
    Accept: request.accept ?? 'application/json',
  };
  let body: string | FormData | undefined;
  if (request.form !== undefined) {
    body = request.form;
  } else if (request.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(request.json);
  }
  // Through upstreamFetch: the inner request carries this request's id, and the call gets its line.
  return upstreamFetch(url.href, {
    method: request.method ?? 'GET',
    headers: injectRequestHeaders(headers),
    body,
  });
}

/** Throws the upstream error, keeping its status and message, as an OpenAI-shaped one. */
export async function failUnlessOk(
  response: Response,
  what: string,
  notFoundCode = 'not_found',
): Promise<void> {
  if (response.ok) return;
  const raw = await response.text().catch(() => '');
  const jsonText = raw.trimStart().startsWith('{')
    ? raw
    : (/^data: ?(.*)$/m.exec(raw)?.[1] ?? '');
  throw new ChatRunError(
    upstreamErrorMessage(
      parseJson(jsonText),
      `${what} failed (${String(response.status)})`,
    ),
    response.status,
    response.status === 404 ? notFoundCode : codeForStatus(response.status),
    true,
  );
}

export async function apiJson(
  caller: ApiCaller,
  path: string,
  request: ApiRequest = {},
  what = 'Request',
  notFoundCode = 'not_found',
): Promise<unknown> {
  const response = await callApi(caller, path, request);
  await failUnlessOk(response, what, notFoundCode);
  const text = await response.text();
  return text === '' ? null : parseJson(text);
}

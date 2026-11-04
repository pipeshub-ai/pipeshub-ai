/**
 * Which client sent a request, read from its root request id. The SDKs stamp
 * `sdk-<lang>-` (`sdk-py-`, `sdk-ts-`), the local MCP server `mcp-`, and the
 * gateway gives hosted `/mcp` requests `mcp-` too (requestContextMiddleware).
 * Everything else (the web app's `<userId>-`, `anon-`, `sys-`) is `other`.
 * Mirrors `app/telemetry/client.py`.
 *
 * The values become metric labels, so each comes from a fixed set.
 */

export type ClientKind = 'sdk' | 'mcp' | 'other';
export type SdkLanguage = 'py' | 'ts' | 'go' | 'other' | 'none';

export interface ClientInfo {
  client: ClientKind;
  sdk: SdkLanguage;
}

const SDK_LANGUAGES: ReadonlySet<string> = new Set(['py', 'ts', 'go']);
const SDK_PREFIX = /^sdk-([a-z]+)-/;

export function classifyRequestId(requestId: string | undefined): ClientInfo {
  if (!requestId) return { client: 'other', sdk: 'none' };
  if (requestId.startsWith('mcp-')) return { client: 'mcp', sdk: 'none' };
  const match = SDK_PREFIX.exec(requestId);
  if (match) {
    const lang = match[1] as string;
    return { client: 'sdk', sdk: SDK_LANGUAGES.has(lang) ? (lang as SdkLanguage) : 'other' };
  }
  return { client: 'other', sdk: 'none' };
}

import type { IncomingHttpHeaders } from 'http';
import type {
  CallerClient,
  ClientChannel,
  RequestClient,
  SdkLanguage,
} from '../platform-events/events';

/** Lower-cased domain part of an email, or `"unknown"` when absent. */
export function emailDomain(email: string | undefined | null): string {
  if (!email || !email.includes('@')) return 'unknown';
  return email.split('@').pop()?.trim().toLowerCase() || 'unknown';
}

/** The first product token of a User-Agent (`claude-code/2.1.0`), never the
 *  whole header, which can carry machine and OS details. */
function productToken(header: unknown): string | undefined {
  if (typeof header !== 'string') return undefined;
  const token = header.trim().split(/\s+/)[0] ?? '';
  // Name with at most one `/version`: no `@` (an address) and no extra path.
  return /^(?=.{1,80}$)[\w.+-]+(?:\/[\w.+-]+)?$/.test(token)
    ? token
    : undefined;
}

/** A channel tag (`X-PipesHub-Channel`, or `?ref=` on a signup link), set by
 *  PipesHub's own clients and links. Anything else is dropped rather than
 *  stored as typed. */
export function channelOf(value: unknown): string | undefined {
  return typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,39}$/.test(value)
    ? value
    : undefined;
}

/** Which client and which of our channels sent a request. */
export function requestClient(headers: IncomingHttpHeaders): RequestClient {
  const userAgent = productToken(headers['user-agent']);
  const channel = channelOf(headers['x-pipeshub-channel']);
  return {
    ...(userAgent !== undefined ? { userAgent } : {}),
    ...(channel !== undefined ? { channel } : {}),
  };
}

/** Where a signup came from: the link's `ref` tag when it is well formed,
 *  otherwise `direct` with the referring site's domain (never its full URL). */
export function signupSource(body: {
  ref?: unknown;
  referrerDomain?: unknown;
}): { channel: string; referrerDomain?: string } {
  const channel = channelOf(body.ref);
  if (channel !== undefined) return { channel };
  const domain =
    typeof body.referrerDomain === 'string'
      ? body.referrerDomain.toLowerCase()
      : '';
  return /^(?=.{1,253}$)[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(domain)
    ? { channel: 'direct', referrerDomain: domain }
    : { channel: 'direct' };
}

// ---- which kind of client is calling ---------------------------------------

export const HEADER_CLIENT_CHANNEL = 'x-pipeshub-channel';
/** `<name>/<version>`, set by PipesHub's SDKs and MCP server (`pipeshub-sdk-python/2.0.1`). */
export const HEADER_CLIENT = 'x-pipeshub-client';

const UNKNOWN = 'unknown';
/** Longer headers are not parsed at all. */
const MAX_HEADER_LENGTH = 512;

const CHANNELS: ReadonlySet<string> = new Set<ClientChannel>([
  'web',
  'sdk',
  'mcp',
  'api',
  'slack',
  'cli',
]);

// A Map, not an object: a lookup by a client-sent name must not find `constructor`.
const SDK_LANGUAGES: ReadonlyMap<string, SdkLanguage> = new Map([
  ['typescript', 'typescript'],
  ['python', 'python'],
  ['go', 'go'],
]);

/** The prefix each SDK and the MCP server put on the request ids they create. */
const REQUEST_ID_PREFIXES: readonly (readonly [string, Identified])[] = [
  ['sdk-ts-', { channel: 'sdk', sdkLanguage: 'typescript' }],
  ['sdk-py-', { channel: 'sdk', sdkLanguage: 'python' }],
  ['sdk-go-', { channel: 'sdk', sdkLanguage: 'go' }],
  ['mcp-', { channel: 'mcp' }],
];

/** What one header could tell about the caller. */
interface Identified {
  channel: 'sdk' | 'mcp';
  sdkLanguage?: SdkLanguage;
  version?: string;
  generatorVersion?: string;
}

/** One header value of sane size, or undefined (absent, repeated, oversized). */
function headerText(value: unknown): string | undefined {
  return typeof value === 'string' && value.length <= MAX_HEADER_LENGTH
    ? value.trim()
    : undefined;
}

/**
 * A release version in the shapes our packages use (`2.0.1`, `v2.0.1`,
 * `2.0.1-rc.1`, `2.0.1rc1`, `2.0.1.dev3`), without the leading `v`; undefined
 * for anything else. The pre-release words are a closed list, so a version can
 * never carry free text.
 */
export function releaseVersion(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length > 32) return undefined;
  const match =
    /^v?(\d{1,4}\.\d{1,4}\.\d{1,4})((?:[-.]?(?:alpha|beta|rc|dev|a|b)(?:\.?\d{1,4})?)?)$/.exec(
      value,
    );
  return match ? `${match[1]}${match[2]}` : undefined;
}

/** `pipeshub-sdk-<language>/<version>` or `pipeshub-mcp/<version>`. */
function fromClientHeader(value: unknown): Identified | undefined {
  const text = headerText(value);
  if (text === undefined) return undefined;
  const match = /^pipeshub-(mcp|sdk-([a-z]{1,16}))\/(\S{1,32})$/.exec(text);
  if (!match) return undefined;
  const version = releaseVersion(match[3]) ?? UNKNOWN;
  return match[1] === 'mcp'
    ? { channel: 'mcp', version }
    : {
        channel: 'sdk',
        sdkLanguage: SDK_LANGUAGES.get(match[2] ?? '') ?? UNKNOWN,
        version,
      };
}

/**
 * The User-Agent the generated clients send:
 * `speakeasy-sdk/<target> <sdk version> <generator version> <api version> <package>`.
 */
function fromGeneratedUserAgent(text: string | undefined): Identified | undefined {
  if (text === undefined) return undefined;
  const match =
    /^speakeasy-sdk\/([a-z-]{1,24}) (\S{1,32}) (\S{1,32}) \S{1,32} \S{1,128}$/.exec(
      text,
    );
  if (!match) return undefined;
  const target = match[1] ?? '';
  const generatorVersion = releaseVersion(match[3]);
  return {
    channel: target.startsWith('mcp-') ? 'mcp' : 'sdk',
    ...(target.startsWith('mcp-')
      ? {}
      : { sdkLanguage: SDK_LANGUAGES.get(target) ?? UNKNOWN }),
    version: releaseVersion(match[2]) ?? UNKNOWN,
    ...(generatorVersion !== undefined ? { generatorVersion } : {}),
  };
}

function fromRequestId(value: unknown): Identified | undefined {
  const text = headerText(value);
  if (text === undefined) return undefined;
  return REQUEST_ID_PREFIXES.find(([prefix]) => text.startsWith(prefix))?.[1];
}

/**
 * Which kind of client sent a request. In order of trust:
 *
 * 1. `X-PipesHub-Client`, which our SDKs and MCP server set from their real
 *    package version;
 * 2. the generated clients' User-Agent (absent in browsers, and its version is
 *    whatever the generator last wrote);
 * 3. the request-id prefix, which gives the SDK language but no version. It is
 *    only believed when the User-Agent is absent or a browser's: services pass
 *    a request id on to each other, so a server-side caller that carries one
 *    is not necessarily the SDK that created it.
 *
 * `X-PipesHub-Channel`, when it is one of the known channels, then decides the
 * channel. Without any of these a browser is `web` and everything else `api`.
 */
export function classifyCaller(headers: IncomingHttpHeaders): CallerClient {
  const userAgent = headerText(headers['user-agent']);
  const browser = userAgent !== undefined && userAgent.startsWith('Mozilla/');
  const generated = fromGeneratedUserAgent(userAgent);
  const identified =
    fromClientHeader(headers[HEADER_CLIENT]) ??
    generated ??
    (userAgent === undefined || userAgent === '' || browser
      ? fromRequestId(headers['x-request-id'])
      : undefined);

  const stated = headerText(headers[HEADER_CLIENT_CHANNEL]);
  const channel: ClientChannel =
    stated !== undefined && CHANNELS.has(stated)
      ? (stated as ClientChannel)
      : (identified?.channel ?? (browser ? 'web' : 'api'));

  if (identified === undefined || identified.channel !== channel) {
    return { channel };
  }
  // The generator version only ever comes from the User-Agent, and only counts
  // when that User-Agent describes the same client.
  const generatorVersion =
    generated?.channel === channel &&
    generated.sdkLanguage === identified.sdkLanguage
      ? generated.generatorVersion
      : undefined;
  const generator = generatorVersion !== undefined ? { generatorVersion } : {};
  return channel === 'sdk'
    ? {
        channel,
        sdkLanguage: identified.sdkLanguage ?? UNKNOWN,
        sdkVersion: identified.version ?? UNKNOWN,
        ...generator,
      }
    : {
        channel,
        mcpServerVersion: identified.version ?? UNKNOWN,
        ...generator,
      };
}

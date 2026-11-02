import dns from 'dns';
import https from 'https';
import net from 'net';
import { InvalidClientError } from '../../../libs/errors/oauth.errors';

/**
 * OAuth Client ID Metadata Documents (draft-ietf-oauth-client-id-metadata-document): a client
 * whose `client_id` is an https URL publishes its metadata there, and the authorization server
 * fetches it instead of the client registering first.
 */

const MAX_DOCUMENT_BYTES = 16 * 1024;
const FETCH_TIMEOUT_MS = 5000;
// The stores accept client ids of at most 200 characters (`goodKey`).
const MAX_CLIENT_ID_LENGTH = 200;

export interface ClientMetadataDocument {
  client_id: string;
  redirect_uris: string[];
  client_name?: string;
  client_uri?: string;
  logo_uri?: string;
  policy_uri?: string;
  tos_uri?: string;
  grant_types?: string[];
  token_endpoint_auth_method?: string;
  scope?: string;
}

export function cimdEnabled(): boolean {
  return process.env.PIPESHUB_ENABLE_CIMD !== 'false';
}

/** A client id that names a metadata document rather than a registered client. */
export function isClientIdMetadataUrl(clientId: unknown): clientId is string {
  return typeof clientId === 'string' && clientId.startsWith('https://');
}

/**
 * The URL rules of the draft: https, a path, no credentials, no fragment, no dot segments.
 * Requiring the canonical form (`href === clientId`) also rejects dot segments and any other
 * spelling the parser would rewrite, so the id compared later is the URL that was fetched.
 */
export function parseClientIdUrl(clientId: string): URL {
  if (clientId.length > MAX_CLIENT_ID_LENGTH) {
    throw new InvalidClientError('client_id URL is too long');
  }
  let url: URL;
  try {
    url = new URL(clientId);
  } catch {
    throw new InvalidClientError('client_id is not a valid URL');
  }
  if (
    url.protocol !== 'https:' ||
    url.username !== '' ||
    url.password !== '' ||
    url.hash !== '' ||
    url.pathname === '/' ||
    url.href !== clientId
  ) {
    throw new InvalidClientError(
      'client_id must be a canonical https URL with a path and no fragment or credentials',
    );
  }
  return url;
}

const blocked = new net.BlockList();
for (const [prefix, bits] of [
  ['0.0.0.0', 8],
  ['10.0.0.0', 8],
  ['100.64.0.0', 10],
  ['127.0.0.0', 8],
  ['169.254.0.0', 16],
  ['172.16.0.0', 12],
  ['192.0.0.0', 24],
  ['192.168.0.0', 16],
  ['198.18.0.0', 15],
  ['224.0.0.0', 4],
  ['240.0.0.0', 4],
] as const) {
  blocked.addSubnet(prefix, bits, 'ipv4');
}
for (const [prefix, bits] of [
  ['::', 128],
  ['::1', 128],
  ['64:ff9b::', 96],
  ['fc00::', 7],
  ['fe80::', 10],
  ['ff00::', 8],
] as const) {
  blocked.addSubnet(prefix, bits, 'ipv6');
}

/** Loopback, private, link-local and other non-public addresses, which the server must never fetch for a client. */
export function isBlockedAddress(address: string): boolean {
  const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(address)?.[1];
  if (mapped !== undefined) return blocked.check(mapped, 'ipv4');
  const family = net.isIP(address);
  if (family === 0) return true;
  return blocked.check(address, family === 4 ? 'ipv4' : 'ipv6');
}

type LookupCallback = (
  err: Error | null,
  address: string | dns.LookupAddress[],
  family?: number,
) => void;

// Checked when the socket connects, so a name that resolves differently on a second lookup cannot slip through.
const guardedLookup = ((
  hostname: string,
  options: dns.LookupOptions,
  callback: LookupCallback,
): void => {
  dns.lookup(hostname, { ...options, all: true }, (err, list) => {
    if (err) {
      callback(err, '');
      return;
    }
    const [first] = list;
    if (!first || list.some((a) => isBlockedAddress(a.address))) {
      callback(new Error(`${hostname} resolves to a non-public address`), '');
      return;
    }
    if (options.all === true) {
      callback(null, list);
    } else {
      callback(null, first.address, first.family);
    }
  });
}) as net.LookupFunction;

type Getter = (url: URL) => Promise<string>;

const httpsGet: Getter = (url) =>
  new Promise((resolve, reject) => {
    const host = url.hostname.replace(/^\[|\]$/g, '');
    // An address in the URL never reaches `lookup`, so it is checked here.
    if (net.isIP(host) !== 0 && isBlockedAddress(host)) {
      reject(new Error(`${host} is a non-public address`));
      return;
    }
    const req = https.get(
      url,
      {
        lookup: guardedLookup,
        headers: { accept: 'application/json' },
        timeout: FETCH_TIMEOUT_MS,
      },
      (res) => {
        // Redirects are not followed: the document must live at the client id itself.
        if (res.statusCode !== 200) {
          res.resume();
          reject(new Error(`HTTP ${String(res.statusCode)}`));
          return;
        }
        const chunks: Buffer[] = [];
        let size = 0;
        res.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > MAX_DOCUMENT_BYTES) {
            req.destroy(new Error('document is too large'));
            return;
          }
          chunks.push(chunk);
        });
        res.on('end', () => {
          resolve(Buffer.concat(chunks).toString('utf8'));
        });
        res.on('error', reject);
      },
    );
    req.on('timeout', () => {
      req.destroy(new Error('timed out'));
    });
    req.on('error', reject);
  });

let getter: Getter = httpsGet;

/** Tests replace the network call; `undefined` restores it. */
export function setClientMetadataGetter(fn?: Getter): void {
  getter = fn ?? httpsGet;
}

const isStringList = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((x) => typeof x === 'string');

const optionalString = (v: unknown): string | undefined =>
  typeof v === 'string' && v.length > 0 ? v : undefined;

/** Fetches and checks the document a client id names. Every failure is an `InvalidClientError`. */
export async function fetchClientMetadataDocument(
  clientId: string,
): Promise<ClientMetadataDocument> {
  const url = parseClientIdUrl(clientId);
  let body: string;
  try {
    body = await getter(url);
  } catch (error) {
    throw new InvalidClientError(
      `Could not fetch the client metadata document: ${(error as Error).message}`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    throw new InvalidClientError('The client metadata document is not JSON');
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new InvalidClientError(
      'The client metadata document is not a JSON object',
    );
  }
  const doc = parsed as Record<string, unknown>;
  if (doc.client_id !== clientId) {
    throw new InvalidClientError(
      'The client metadata document names a different client_id',
    );
  }
  if ('client_secret' in doc || 'client_secret_expires_at' in doc) {
    throw new InvalidClientError(
      'A client metadata document must not contain a client secret',
    );
  }
  const authMethod = doc.token_endpoint_auth_method ?? 'none';
  if (authMethod !== 'none') {
    throw new InvalidClientError(
      `token_endpoint_auth_method ${JSON.stringify(authMethod)} is not supported for metadata document clients`,
    );
  }
  const redirectUris = doc.redirect_uris;
  if (!isStringList(redirectUris) || redirectUris.length === 0) {
    throw new InvalidClientError(
      'The client metadata document must list redirect_uris',
    );
  }
  const grantTypes = doc.grant_types;
  if (grantTypes !== undefined && !isStringList(grantTypes)) {
    throw new InvalidClientError('grant_types must be a list of strings');
  }
  return {
    client_id: clientId,
    redirect_uris: redirectUris,
    client_name: optionalString(doc.client_name),
    client_uri: optionalString(doc.client_uri),
    logo_uri: optionalString(doc.logo_uri),
    policy_uri: optionalString(doc.policy_uri),
    tos_uri: optionalString(doc.tos_uri),
    grant_types: grantTypes,
    token_endpoint_auth_method: 'none',
    scope: optionalString(doc.scope),
  };
}

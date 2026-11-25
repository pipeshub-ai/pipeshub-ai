/**
 * Which customers call the API through a PipesHub SDK or the PipesHub MCP
 * server, in which language and which release.
 *
 * Names follow the usage contract: the counter is `pipeshub.usage.sdk.requests`
 * (`sdk_language`, `sdk_version`, `outcome`) in its Prometheus spelling, and the
 * event is `sdk.client.seen`. `channel`, `org` and `domain` are added the way
 * this build's other metrics carry them.
 *
 * Nothing here is typed by the caller: the classification is already closed
 * sets and release-shaped versions (see `classifyCaller`), and the number of
 * distinct versions per language is capped on top of that.
 */
import type { PlatformEvents } from '../../../libs/platform-events/events';
import { SERVICE_NAME } from '../constants';
import { recordEvent } from '../event-buffer';
import { metricsBackend } from '../metrics-backend';

type CompletedRequest = PlatformEvents['http.request.completed'];

const sdkRequests = metricsBackend.createCounter({
  name: 'pipeshub_usage_sdk_requests_total',
  help: 'API requests made through a PipesHub SDK or the PipesHub MCP server',
  labelNames: [
    'service',
    'channel',
    'sdk_language',
    'sdk_version',
    'outcome',
    'org',
    'domain',
  ],
});

const UNKNOWN = 'unknown';
const OTHER = 'other';

/** Distinct versions kept per language; later ones are counted as `other`. */
export const MAX_VERSIONS_PER_LANGUAGE = 20;
/** Distinct (organisation, client) pairs remembered per day for `sdk.client.seen`. */
export const MAX_FIRST_SEEN_KEYS = 5000;

const versionsSeen = new Map<string, Set<string>>();

function boundVersion(language: string, version: string): string {
  if (version === UNKNOWN) return UNKNOWN;
  let seen = versionsSeen.get(language);
  if (seen === undefined) {
    seen = new Set();
    versionsSeen.set(language, seen);
  }
  if (seen.has(version)) return version;
  if (seen.size >= MAX_VERSIONS_PER_LANGUAGE) return OTHER;
  seen.add(version);
  return version;
}

let firstSeenDay = '';
const firstSeen = new Set<string>();

/** True the first time `key` is seen in a UTC day. When the set is full, false: nothing more is reported that day. */
function firstToday(key: string): boolean {
  const day = new Date().toISOString().slice(0, 10);
  if (day !== firstSeenDay) {
    firstSeenDay = day;
    firstSeen.clear();
  }
  if (firstSeen.has(key) || firstSeen.size >= MAX_FIRST_SEEN_KEYS) return false;
  firstSeen.add(key);
  return true;
}

/**
 * Counts a finished request that came through an SDK or the MCP server, and
 * reports the first one of the day per organisation and client. Requests from
 * any other channel are left to the HTTP metrics.
 */
export function recordClientRequest(request: CompletedRequest): void {
  const { client } = request;
  if (client.channel !== 'sdk' && client.channel !== 'mcp') return;

  const sdk = client.channel === 'sdk';
  // The MCP server is not an SDK in a language of its own: `other`, told apart by `channel`.
  const language = sdk ? (client.sdkLanguage ?? UNKNOWN) : OTHER;
  const version =
    (sdk ? client.sdkVersion : client.mcpServerVersion) ?? UNKNOWN;
  const org = request.orgId ?? UNKNOWN;
  const domain = request.emailDomain === '' ? UNKNOWN : request.emailDomain;

  sdkRequests.inc({
    service: SERVICE_NAME,
    channel: client.channel,
    sdk_language: language,
    sdk_version: boundVersion(sdk ? language : 'mcp', version),
    outcome: request.outcome,
    org,
    domain,
  });

  if (firstToday(`${org}|${client.channel}|${language}|${version}`)) {
    recordEvent('sdk.client.seen', {
      orgId: org,
      domain,
      channel: client.channel,
      ...(sdk
        ? { sdk_language: language, sdk_version: version }
        : { mcp_server_version: version }),
    });
  }
}

/** Forgets the versions and clients seen so far. For tests. */
export function resetClientMetrics(): void {
  versionsSeen.clear();
  firstSeen.clear();
  firstSeenDay = '';
}

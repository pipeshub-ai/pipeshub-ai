/**
 * C9.04, C20.48: the internal-only services' metrics as one Prometheus exposition.
 *
 * The egress proxy, the Model Gateway and the Tool Gateway listen on no published port; each serves its process's
 * registry at `/metrics` to the worker credential, and Node's control-plane route merges them here. Every sample is
 * labelled with the service it came from, a family's HELP and TYPE are written once, and a series that carries an
 * org-identifying label is dropped: the scrape leaves the deployment's trust boundary with no tenant in it.
 */

export const SERVICE_LABEL = 'harness_service';
export const SCRAPE_UP_METRIC = 'pipeshub_harness_metrics_scrape_up';
export const PROMETHEUS_TEXT = 'text/plain; version=0.0.4; charset=utf-8';
export const METRICS_PATH = '/metrics';

/** One service's answer; `null`: it could not be read (down, refused, too slow, too large). */
export interface Scrape {
  service: string;
  body: string | null;
}

export interface MetricsTarget {
  service: string;
  url: string;
}

const ORG_LABELS = new Set([
  'org',
  'orgid',
  'tenant',
  'tenantid',
  'organization',
  'organizationid',
]);

/** Whether any of `names` is an org id by any of its spellings (`org`, `orgId`, `org_id`, `tenant_id` …). */
export function carriesOrgLabel(names: readonly string[]): boolean {
  return names.some((name) =>
    ORG_LABELS.has(name.toLowerCase().replace(/_/g, '')),
  );
}

interface Sample {
  name: string;
  labels: Array<[string, string]>;
  rest: string;
}

const NAME = /^[a-zA-Z_:][a-zA-Z0-9_:]*/;
const LABEL_NAME = /^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"/;
const SUFFIXES = [
  '_bucket',
  '_sum',
  '_count',
  '_total',
  '_created',
  '_gsum',
  '_gcount',
  '_info',
];

/** A sample line, its label values kept in their escaped form; `null` for a line that is none. */
function parseSample(line: string): Sample | null {
  const name = NAME.exec(line)?.[0];
  if (name === undefined) return null;
  let at = name.length;
  const labels: Array<[string, string]> = [];
  if (line[at] === '{') {
    at += 1;
    for (;;) {
      const rest = line.slice(at);
      if (/^\s*}/.test(rest)) {
        at += rest.indexOf('}') + 1;
        break;
      }
      const label = LABEL_NAME.exec(rest);
      if (label === null) return null;
      at += label[0].length;
      let value = '';
      while (at < line.length && line[at] !== '"') {
        if (line[at] === '\\') {
          value += line.slice(at, at + 2);
          at += 2;
        } else {
          value += line.charAt(at);
          at += 1;
        }
      }
      if (at >= line.length) return null;
      at += 1;
      labels.push([label[1] ?? '', value]);
      const comma = /^\s*,/.exec(line.slice(at));
      if (comma !== null) at += comma[0].length;
    }
  }
  const rest = line.slice(at).trim();
  return rest === '' ? null : { name, labels, rest };
}

function formatSample(sample: Sample): string {
  const labels = sample.labels
    .map(([name, value]) => `${name}="${value}"`)
    .join(',');
  return `${sample.name}{${labels}} ${sample.rest}`;
}

function familyOf(sample: string, current: string | null): string {
  if (
    current !== null &&
    (sample === current || SUFFIXES.some((s) => sample === current + s))
  ) {
    return current;
  }
  return sample;
}

interface Family {
  help: string | null;
  type: string | null;
  owner: string;
  samples: string[];
}

function escapeLabelValue(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n');
}

/** The scrapes as one exposition, then `SCRAPE_UP_METRIC` by service (1 read, 0 not). */
export function mergeExpositions(scrapes: readonly Scrape[]): string {
  const families = new Map<string, Family>();
  const family = (name: string, service: string): Family => {
    let found = families.get(name);
    if (found === undefined) {
      found = { help: null, type: null, owner: service, samples: [] };
      families.set(name, found);
    }
    return found;
  };
  for (const { service, body } of scrapes) {
    if (body === null) continue;
    const tag = escapeLabelValue(service);
    // A family another service typed differently: this service's samples of it are dropped, keeping the text valid.
    const conflicting = new Set<string>();
    let current: string | null = null;
    for (const line of body.split('\n')) {
      const meta = /^#\s+(HELP|TYPE)\s+(\S+)\s?(.*)$/.exec(line);
      if (meta !== null) {
        const [, kind, name, text] = meta as unknown as [
          string,
          string,
          string,
          string,
        ];
        current = name;
        if (name === SCRAPE_UP_METRIC) continue;
        const target = family(name, service);
        if (kind === 'HELP') {
          target.help ??= text;
        } else if (target.type === null) {
          target.type = text;
        } else if (target.type !== text) {
          conflicting.add(name);
        }
        continue;
      }
      if (line.trim() === '' || line.startsWith('#')) continue;
      const sample = parseSample(line);
      if (sample === null) continue;
      const name = familyOf(sample.name, current);
      if (name === SCRAPE_UP_METRIC || conflicting.has(name)) continue;
      if (carriesOrgLabel(sample.labels.map(([label]) => label))) continue;
      sample.labels = [
        ...sample.labels.filter(([label]) => label !== SERVICE_LABEL),
        [SERVICE_LABEL, tag],
      ];
      family(name, service).samples.push(formatSample(sample));
    }
  }
  const out: string[] = [];
  for (const [name, { help, type, samples }] of families) {
    if (samples.length === 0) continue;
    if (help !== null) out.push(`# HELP ${name} ${help}`);
    if (type !== null) out.push(`# TYPE ${name} ${type}`);
    out.push(...samples);
  }
  out.push(
    `# HELP ${SCRAPE_UP_METRIC} Whether the control plane read the service's metrics (1) or not (0)`,
  );
  out.push(`# TYPE ${SCRAPE_UP_METRIC} gauge`);
  for (const { service, body } of scrapes) {
    out.push(
      `${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="${escapeLabelValue(service)}"} ${body === null ? '0' : '1'}`,
    );
  }
  return `${out.join('\n')}\n`;
}

function metricsUrl(raw: string | undefined): string | null {
  if (raw === undefined || raw.trim() === '') return null;
  try {
    return new URL(METRICS_PATH, new URL(raw.trim()).origin).toString();
  } catch {
    return null;
  }
}

/**
 * Where each service listens, as the deployment configures it (the variables Node also writes into
 * `/services/endpoints`). The sandbox broker answers at `SANDBOX_BROKER_BACKEND`; the Tool Gateway is the worker's
 * listener, `HARNESS_TOOL_GATEWAY_URL`'s origin; an unconfigured service is not scraped.
 */
export function metricsTargetsFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): MetricsTarget[] {
  const candidates: Array<[string, string | null]> = [
    ['egress_proxy', metricsUrl(env['EGRESS_PROXY_CONTROL_BACKEND'])],
    ['model_gateway', metricsUrl(env['MODEL_GATEWAY_BACKEND'])],
    ['sandbox_broker', metricsUrl(env['SANDBOX_BROKER_BACKEND'])],
    [
      'tool_gateway',
      metricsUrl(env['HARNESS_TOOL_GATEWAY_URL']) ??
        metricsUrl(env['TOOL_GATEWAY_BACKEND']),
    ],
  ];
  return candidates.flatMap(([service, url]) =>
    url === null ? [] : [{ service, url }],
  );
}

/** The families of `text` whose name starts with one of `prefixes`, HELP, TYPE and samples; nothing else. */
export function keepFamilies(
  text: string,
  prefixes: readonly string[],
): string {
  const kept = (name: string): boolean =>
    prefixes.some((prefix) => name.startsWith(prefix));
  const out: string[] = [];
  for (const line of text.split('\n')) {
    const meta = /^#\s+(HELP|TYPE)\s+([a-zA-Z_:][a-zA-Z0-9_:]*)/.exec(line);
    const name = meta !== null ? meta[2] : NAME.exec(line)?.[0];
    if (name !== undefined && kept(name)) out.push(line);
  }
  return out.length > 0 ? `${out.join('\n')}\n` : '';
}

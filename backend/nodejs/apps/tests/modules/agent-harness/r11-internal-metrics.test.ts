/**
 * Round 11 item B (C9.04, C20.48): one control-plane `/metrics` for the internal-only services.
 *
 * R11-MX-6 `GET …/internal/metrics` takes the scraper's credential only (SEC-C-10): none, a wrong one, a JWT-shaped bearer are 401
 *          and no upstream is asked.
 * R11-MX-7 it scrapes each configured service with the worker credential and answers one valid exposition: every
 *          sample labelled with its service, each family's HELP and TYPE once, families of one name merged.
 * R11-MX-8 an upstream that is down, refuses or hangs past the timeout is `scrape_up 0` and never fails the scrape.
 * R11-MX-9 no series that carries an org-identifying label (org, orgId, org_id, tenant …) is ever passed on.
 * R11-MX-10 the services' addresses come from the deployment's environment: the egress proxy's control plane, the
 *          Model Gateway and the Tool Gateway's listener (`HARNESS_TOOL_GATEWAY_URL`'s origin).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { createServer, IncomingMessage, Server, ServerResponse } from 'node:http';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_METRICS_TARGETS,
} from '../../../src/modules/agent-harness/binders/tokens';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  carriesOrgLabel,
  mergeExpositions,
  metricsTargetsFromEnv,
  SCRAPE_UP_METRIC,
  SERVICE_LABEL,
} from '../../../src/modules/agent-harness/domain/metrics-exposition';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';

const CREDENTIAL = 'r11-worker-credential';

const EGRESS = [
  '# HELP pipeshub_harness_egress_bytes_total Bytes a run moved',
  '# TYPE pipeshub_harness_egress_bytes_total counter',
  'pipeshub_harness_egress_bytes_total{alias="tunnel",direction="up"} 12.0',
  '# HELP process_open_fds Open file descriptors',
  '# TYPE process_open_fds gauge',
  'process_open_fds 9.0',
  '',
].join('\n');
const MODEL = [
  '# HELP pipeshub_harness_gateway_request_duration_seconds Time until a gateway answered',
  '# TYPE pipeshub_harness_gateway_request_duration_seconds histogram',
  'pipeshub_harness_gateway_request_duration_seconds_bucket{gateway="model",route="messages",le="0.5"} 1.0',
  'pipeshub_harness_gateway_request_duration_seconds_bucket{gateway="model",route="messages",le="+Inf"} 2.0',
  'pipeshub_harness_gateway_request_duration_seconds_sum{gateway="model",route="messages"} 1.25',
  'pipeshub_harness_gateway_request_duration_seconds_count{gateway="model",route="messages"} 2.0',
  '# HELP process_open_fds Open file descriptors',
  '# TYPE process_open_fds gauge',
  'process_open_fds 11.0',
  '# HELP http_requests_total Requests by org',
  '# TYPE http_requests_total counter',
  'http_requests_total{service="query",route="/x",org="org-secret-1"} 4.0',
  'http_requests_total{service="query",route="/y",orgId="org-secret-2"} 1.0',
  'http_requests_total{service="query",route="/z",tenant_id="org-secret-3"} 1.0',
  'http_requests_total{service="query",route="/ok",label_with_brace="a}b, c=\\"d\\""} 2.0',
  '',
].join('\n');

interface Upstream {
  url: string;
  seen: Array<string | undefined>;
  close(): Promise<void>;
}

async function upstream(answer: (req: IncomingMessage, res: ServerResponse) => void): Promise<Upstream> {
  const seen: Array<string | undefined> = [];
  const server: Server = createServer((req, res) => {
    seen.push(req.headers.authorization);
    answer(req, res);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${String(port)}/metrics`,
    seen,
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}

const serving = (body: string) => (req: IncomingMessage, res: ServerResponse) => {
  if (req.headers.authorization !== `Bearer ${CREDENTIAL}` || req.url !== '/metrics') {
    res.writeHead(401).end();
    return;
  }
  res.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4' }).end(body);
};

const authority = {
  verifyWorker: async (credential: string | undefined): Promise<void> => {
    if (credential !== CREDENTIAL) throw new HarnessUnauthorizedError('credential');
  },
  workerCredential: async (): Promise<string> => 'phw_the-worker-credential',
  // SEC-C-10: the route admits the scraper's credential, and Node asks each service with it too.
  verifyRole: async (roles: readonly string[], credential: string | undefined): Promise<string> => {
    if (!roles.includes('scrape') || credential !== CREDENTIAL) throw new HarnessUnauthorizedError('credential');
    return 'scrape';
  },
  credentialFor: async (role: string): Promise<string | null> => (role === 'scrape' ? CREDENTIAL : null),
  verifyAttemptToken: async () => {
    throw new HarnessUnauthorizedError('credential');
  },
};

async function harnessState(targets: Array<{ service: string; url: string }>): Promise<{ base: string; close(): Promise<void> }> {
  const container = new Container();
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
  container.bind(AGENT_HARNESS_METRICS_TARGETS).toConstantValue({ targets: () => targets, timeoutMs: 300 });
  const app = express();
  app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-argument handler as error middleware
  app.use((error: { statusCode?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    res.status(error.statusCode ?? 500).json({});
  });
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const { port } = server.address() as AddressInfo;
  return {
    base: `http://127.0.0.1:${String(port)}${AGENT_HARNESS_INTERNAL_PREFIX}`,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

/** The exposition's families in order, each with its HELP/TYPE count and samples. */
function families(text: string): Map<string, { help: number; type: string[]; samples: string[] }> {
  const found = new Map<string, { help: number; type: string[]; samples: string[] }>();
  let current = '';
  for (const line of text.split('\n').filter((l) => l !== '')) {
    const meta = /^# (HELP|TYPE) (\S+) ?(.*)$/.exec(line);
    if (meta) {
      current = meta[2]!;
      const family = found.get(current) ?? { help: 0, type: [], samples: [] };
      if (meta[1] === 'HELP') family.help += 1;
      else family.type.push(meta[3]!);
      found.set(current, family);
      continue;
    }
    found.get(current)!.samples.push(line);
  }
  return found;
}

describe('Round 11: one control-plane metrics route for the internal-only services (C9.04)', () => {
  it('R11-MX-6: only the scraper\'s credential reads it, and no upstream is asked otherwise', async () => {
    const egress = await upstream(serving(EGRESS));
    const state = await harnessState([{ service: 'egress_proxy', url: egress.url }]);
    try {
      for (const authorization of [undefined, 'Bearer wrong', 'Bearer eyJhbGciOiJIUzI1NiJ9.e30.x']) {
        const reply = await fetch(`${state.base}/metrics`, {
          headers: authorization ? { authorization } : {},
        });
        expect(reply.status, authorization).to.equal(401);
        expect(await reply.text()).not.to.include('pipeshub_harness_egress');
      }
      expect(egress.seen).to.deep.equal([]);
    } finally {
      await state.close();
      await egress.close();
    }
  });

  it('R11-MX-7, R11-MX-8, R11-MX-9: one exposition, labelled by service, a dead upstream down, no org series', async () => {
    const egress = await upstream(serving(EGRESS));
    const model = await upstream(serving(MODEL));
    const hung = await upstream(() => undefined);
    const refusing = await upstream((_req, res) => res.writeHead(401).end());
    const state = await harnessState([
      { service: 'egress_proxy', url: egress.url },
      { service: 'model_gateway', url: model.url },
      { service: 'tool_gateway', url: hung.url },
      { service: 'other_gateway', url: refusing.url },
      { service: 'gone', url: 'http://127.0.0.1:1/metrics' },
    ]);
    try {
      const reply = await fetch(`${state.base}/metrics`, { headers: { authorization: `Bearer ${CREDENTIAL}` } });
      expect(reply.status).to.equal(200);
      expect(reply.headers.get('content-type')).to.match(/^text\/plain; version=0\.0\.4/);
      expect(reply.headers.get('cache-control')).to.equal('no-store');
      const text = await reply.text();
      // Each upstream was asked with the worker credential.
      expect(egress.seen).to.deep.equal([`Bearer ${CREDENTIAL}`]);
      expect(model.seen).to.deep.equal([`Bearer ${CREDENTIAL}`]);
      const found = families(text);
      for (const [name, family] of found) {
        expect(family.help, name).to.equal(1);
        expect(family.type, name).to.have.length(1);
      }
      expect(found.get('process_open_fds')!.samples).to.deep.equal([
        `process_open_fds{${SERVICE_LABEL}="egress_proxy"} 9.0`,
        `process_open_fds{${SERVICE_LABEL}="model_gateway"} 11.0`,
      ]);
      expect(found.get('pipeshub_harness_gateway_request_duration_seconds')!.samples).to.have.length(4);
      expect(found.get('pipeshub_harness_gateway_request_duration_seconds')!.samples[0]).to.equal(
        `pipeshub_harness_gateway_request_duration_seconds_bucket{gateway="model",route="messages",le="0.5",${SERVICE_LABEL}="model_gateway"} 1.0`,
      );
      expect(found.get('pipeshub_harness_egress_bytes_total')!.samples).to.deep.equal([
        `pipeshub_harness_egress_bytes_total{alias="tunnel",direction="up",${SERVICE_LABEL}="egress_proxy"} 12.0`,
      ]);
      // R11-MX-9: the org-labelled series are gone; the family keeps its one clean series, quoted braces intact.
      expect(text).not.to.include('org-secret');
      expect(found.get('http_requests_total')!.samples).to.deep.equal([
        `http_requests_total{service="query",route="/ok",label_with_brace="a}b, c=\\"d\\"",${SERVICE_LABEL}="model_gateway"} 2.0`,
      ]);
      // R11-MX-8
      expect(found.get(SCRAPE_UP_METRIC)!.type).to.deep.equal(['gauge']);
      expect(found.get(SCRAPE_UP_METRIC)!.samples).to.deep.equal([
        `${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="egress_proxy"} 1`,
        `${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="model_gateway"} 1`,
        `${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="tool_gateway"} 0`,
        `${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="other_gateway"} 0`,
        `${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="gone"} 0`,
      ]);
    } finally {
      await state.close();
      await Promise.all([egress.close(), model.close(), hung.close(), refusing.close()]);
    }
  });

  it('R11-MX-9: every spelling of an org label is recognised, and nothing else is', () => {
    for (const name of ['org', 'orgId', 'org_id', 'ORG', 'orgid', 'tenant', 'tenantId', 'tenant_id', 'organization', 'organizationId', 'organization_id']) {
      expect(carriesOrgLabel([name]), name).to.equal(true);
    }
    for (const name of ['origin', 'organic', 'route', 'service', 'outcome', 'reason']) {
      expect(carriesOrgLabel([name]), name).to.equal(false);
    }
    const merged = mergeExpositions([{ service: 'a', body: 'x_total{orgId="o1"} 1\nx_total{route="r"} 2\n' }]);
    expect(merged).not.to.include('o1');
    expect(merged).to.include(`x_total{route="r",${SERVICE_LABEL}="a"} 2`);
  });

  it('R11-MX-7: a family two services type differently keeps the first type and drops the other\'s samples', () => {
    const merged = families(
      mergeExpositions([
        { service: 'a', body: '# HELP m M\n# TYPE m counter\nm 1\n' },
        { service: 'b', body: '# HELP m M\n# TYPE m gauge\nm 2\n' },
      ]),
    );
    expect(merged.get('m')).to.deep.equal({ help: 1, type: ['counter'], samples: [`m{${SERVICE_LABEL}="a"} 1`] });
  });

  it('R11-MX-10: the targets come from the deployment environment', () => {
    expect(
      metricsTargetsFromEnv({
        EGRESS_PROXY_CONTROL_BACKEND: 'http://egress-proxy:8098',
        MODEL_GATEWAY_BACKEND: 'http://model-gateway:8096/',
        HARNESS_TOOL_GATEWAY_URL: 'http://tool-gateway:8098/harness/tool-gateway',
        TOOL_GATEWAY_BACKEND: 'http://tool-gateway:8099',
      }),
    ).to.deep.equal([
      { service: 'egress_proxy', url: 'http://egress-proxy:8098/metrics' },
      { service: 'model_gateway', url: 'http://model-gateway:8096/metrics' },
      { service: 'tool_gateway', url: 'http://tool-gateway:8098/metrics' },
    ]);
    expect(metricsTargetsFromEnv({ TOOL_GATEWAY_BACKEND: 'http://tool-gateway:8099' })).to.deep.equal([
      { service: 'tool_gateway', url: 'http://tool-gateway:8099/metrics' },
    ]);
    expect(metricsTargetsFromEnv({ MODEL_GATEWAY_BACKEND: 'not a url' })).to.deep.equal([]);
  });
});

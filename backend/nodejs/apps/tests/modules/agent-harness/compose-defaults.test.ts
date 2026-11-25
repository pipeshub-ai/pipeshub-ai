import { expect } from 'chai';
import * as fs from 'fs';
import * as path from 'path';
import {
  CORE_SCHEMA,
  defineMappingTag,
  defineScalarTag,
  defineSequenceTag,
  load as loadYaml,
  mergeTag,
} from 'js-yaml';

const composeDir = path.resolve(
  __dirname,
  '../../../../../../deployment/docker-compose',
);

type Service = {
  stop_grace_period?: string;
  environment?: string[] | Record<string, string>;
  depends_on?: Record<string, { condition?: string }>;
  extends?: { file?: string; service?: string };
};
type ComposeFile = { services: Record<string, Service> };

// Compose's `!reset` tag, which js-yaml does not know, on every kind of node (js-yaml 5's tag API), and merge keys.
const never = (): boolean => false;
const composeSchema = CORE_SCHEMA.withTags(
  mergeTag,
  defineScalarTag('!reset', { resolve: () => null, identify: never }),
  defineSequenceTag<null>('!reset', {
    create: () => null,
    addItem: () => undefined,
    identify: never,
  }),
  defineMappingTag<null>('!reset', {
    create: () => null,
    addPair: () => '',
    has: never,
    keys: () => [],
    get: () => undefined,
    identify: never,
  }),
);

const load = (file: string): ComposeFile =>
  loadYaml(fs.readFileSync(path.join(composeDir, file), 'utf8'), {
    schema: composeSchema,
  }) as ComposeFile;

const environment = (service: Service | undefined): Record<string, string> => {
  const raw = service?.environment ?? {};
  if (!Array.isArray(raw)) return raw;
  return Object.fromEntries(
    raw.map((entry) => {
      const index = entry.indexOf('=');
      return [entry.slice(0, index), entry.slice(index + 1)];
    }),
  );
};

/** What `${NAME:-default}` renders to when NAME is unset. */
const rendered = (value: string | undefined): string | undefined =>
  value?.replace(/\$\{[A-Z0-9_]+:-([^}]*)\}/g, '$1');

describe('agent harness Compose defaults', () => {
  const base = environment(load('docker-compose.yml').services['pipeshub-ai']);
  const overlay = environment(
    load('docker-compose.agent-harness.yml').services['pipeshub-ai'],
  );

  it('switches the harness on in the base file (ADR-OPS-06 as amended 2026-11-20); orgs turn it on in Labs', () => {
    expect(rendered(base.AGENT_HARNESS_RELAY_ENABLED)).to.equal('true');
    expect(rendered(base.AGENT_HARNESS_WORKER_ENABLED)).to.equal('true');
  });

  it('runs MongoDB as a replica set in the base file', () => {
    expect(rendered(base.REPLICA_SET_AVAILABLE)).to.equal('true');
    expect(rendered(base.MONGO_URI)).to.contain('replicaSet=rs0');
  });

  it('enables both through the agent-harness overlay', () => {
    const merged = { ...base, ...overlay };
    expect(merged.AGENT_HARNESS_RELAY_ENABLED).to.equal('true');
    expect(merged.AGENT_HARNESS_WORKER_ENABLED).to.equal('true');
  });

  it('exposes no relay or worker tuning knobs', () => {
    const knobs = Object.keys({ ...base, ...overlay }).filter((name) =>
      /^AGENT_HARNESS_(RELAY|WORKER)_(?!ENABLED$)/.test(name),
    );
    expect(knobs).to.deep.equal([]);
  });
});

// The CI integration and e2e stacks test the shipped topology: the base file's
// replica set, and the harness processes on, so a durable run can go end to end.
for (const file of [
  'docker-compose.integration.neo4j.yml',
  'docker-compose.integration.arango.yml',
]) {
  describe(`agent harness on the ${file} CI stack`, () => {
    const services = load(file).services;
    const app = environment(services['pipeshub-ai']);

    it("runs the base file's MongoDB replica set", () => {
      expect(services['mongodb']?.extends).to.deep.equal({
        file: 'docker-compose.yml',
        service: 'mongodb',
      });
    });

    it('connects the app to it with majority writes, once it is healthy', () => {
      expect(rendered(app.MONGO_URI)).to.contain('replicaSet=rs0');
      expect(rendered(app.MONGO_URI)).to.contain('w=majority');
      expect(rendered(app.REPLICA_SET_AVAILABLE)).to.equal('true');
      expect(services['pipeshub-ai']?.depends_on?.['mongodb']?.condition).to.equal(
        'service_healthy',
      );
    });

    it('starts the harness relay and worker', () => {
      expect(rendered(app.AGENT_HARNESS_RELAY_ENABLED)).to.equal('true');
      expect(rendered(app.AGENT_HARNESS_WORKER_ENABLED)).to.equal('true');
    });
  });
}

// RU-1f: a worker's drain commits through Node, so the process monitor stops
// the drainers first and waits for them; the container's stop grace covers it.
describe('agent harness drain on container stop', () => {
  // The image's process monitor (main moved it out of the Dockerfile).
  const dockerfile = fs.readFileSync(
    path.join(composeDir, '../process_monitor.sh'),
    'utf8',
  );
  const cleanup = dockerfile.slice(dockerfile.indexOf('cleanup() {'));
  const waitS = Number(/^DRAIN_WAIT_S=(\d+)$/m.exec(dockerfile)?.[1]);

  it('stops the harness worker and the query service before Node', () => {
    const drain = cleanup.indexOf(
      'stop_and_wait "$AGENT_HARNESS_WORKER_PID" "$QUERY_PID"',
    );
    expect(drain).to.be.greaterThan(-1);
    expect(cleanup.indexOf('kill "$NODEJS_PID"')).to.be.greaterThan(drain);
    expect(cleanup).not.to.contain('kill "$AGENT_HARNESS_WORKER_PID"');
  });

  it('waits past the worker drain bound (30 s plus a release)', () => {
    expect(waitS).to.be.at.least(35);
  });

  for (const file of [
    'docker-compose.yml',
    'docker-compose.build.neo4j.yml',
    'docker-compose.integration.neo4j.yml',
    'docker-compose.integration.arango.yml',
  ]) {
    it(`gives the app container of ${file} a stop grace longer than the wait`, () => {
      const grace = load(file).services['pipeshub-ai']?.stop_grace_period ?? '10s';
      expect(Number(/^(\d+)s$/.exec(grace)?.[1])).to.be.greaterThan(waitS);
    });
  }
});

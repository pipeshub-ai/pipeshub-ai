import { expect } from 'chai';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import yaml from 'js-yaml';
import { probeJournalDurability } from '../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { flagMatrix, matrixFlag, REPO_ROOT } from '../modules/agent-harness/support/flag-matrix';

/**
 * C-16, what the matrix claims about shipped deployments and editors, held to
 * the code that decides it. FM-10 (OP-10): the journal rule per Compose file
 * and Helm preset is what `probeJournalDurability` answers on the MongoDB that
 * file deploys. FM-11 (AD-13): who edits which approvals section is what the
 * org admin's approvals route writes.
 */

const HELM = join(REPO_ROOT, 'deployment/helm/pipeshub-ai');
const COMPOSE = join(REPO_ROOT, 'deployment/docker-compose/docker-compose.yml');
const FLAGS_DOC = join(REPO_ROOT, 'docs/agent-harness-v2/FLAGS.md');

type Values = Record<string, unknown>;

const isMap = (value: unknown): value is Values =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Helm's merge of a later values file over an earlier one: maps merge, `null` removes the key. */
const overlay = (base: Values, top: Values): Values => {
  const merged: Values = { ...base };
  for (const [key, value] of Object.entries(top)) {
    if (value === null) delete merged[key];
    else if (isMap(value) && isMap(merged[key])) merged[key] = overlay(merged[key] as Values, value);
    else merged[key] = value;
  }
  return merged;
};

const valuesFile = (name: string): Values =>
  (yaml.load(readFileSync(join(HELM, name), 'utf8')) as Values | undefined) ?? {};

/** A recorded chain's values: every preset is layered on the chart's own values.yaml, as `helm install -f` does. */
const helmValues = (chain: string): Values =>
  chain
    .split(' + ')
    .reduce(
      (merged, name) => overlay(merged, valuesFile(name)),
      chain === 'values.yaml' ? {} : valuesFile('values.yaml'),
    );

/** The `hello` the MongoDB a Helm values set deploys answers. */
const helmHello = (values: Values): Record<string, unknown> => {
  const mongo = (values['mongodb'] ?? {}) as Values;
  const replicaSet = (mongo['architecture'] ?? 'standalone') === 'replicaset';
  const setName = mongo['replicaSetName'];
  // Only the Bitnami subchart (mongodb.enabled) can run an arbiter; the built-in StatefulSet has none.
  const arbiter =
    mongo['enabled'] === true && ((mongo['arbiter'] ?? {}) as Values)['enabled'] === true;
  return {
    ...(replicaSet ? { setName: typeof setName === 'string' ? setName : 'rs0' } : {}),
    arbiters: arbiter ? ['arbiter-0:27017'] : [],
  };
};

/** The `hello` Compose's mongodb service answers. */
const composeHello = (): Record<string, unknown> => {
  const doc = yaml.load(readFileSync(COMPOSE, 'utf8')) as Values;
  const services = doc['services'] as Record<string, Values>;
  const command = JSON.stringify(services['mongodb']?.['command'] ?? '');
  const replSet = /--replSet["',\s]+([A-Za-z0-9_-]+)/.exec(command);
  const arbiters = Object.keys(services).filter((name) => /arbiter/i.test(name));
  return { ...(replSet ? { setName: replSet[1] } : {}), arbiters };
};

/** A database whose `hello` answers `hello` and whose majority write is acknowledged. */
const answering = (hello: Record<string, unknown>) => ({
  admin: () => ({ command: async () => hello }),
  collection: () => ({ updateOne: async () => ({ acknowledged: true }) }),
});

interface ShippedJournal {
  compose: string;
  helm: Record<string, string>;
}

const shippedJournal = (): ShippedJournal => {
  const need = flagMatrix()
    .features.find((f) => f.name === 'durable_chat')
    ?.requires.find((n) => n.condition === 'journal_durable') as
    | { shipped?: ShippedJournal }
    | undefined;
  if (need?.shipped === undefined) throw new Error('durable_chat.journal_durable has no shipped map');
  return need.shipped;
};

const docRow = (label: string, reason: string): string =>
  `| ${label} | \`${reason}\` | ${reason === 'majority' ? 'on' : 'off'} |`;

const helmLabel = (chain: string): string =>
  `Helm, ${chain.split(' + ').map((name) => `\`${name}\``).join(' + ')}`;

describe('C-16 shipped truth', () => {
  describe('FM-10 (OP-10): the journal rule at each shipped MongoDB topology', () => {
    it('OP-10: Compose deploys a set the probe calls durable, as the matrix says', async () => {
      const probed = await probeJournalDurability(answering(composeHello()) as never);
      expect(probed.reason).to.equal(shippedJournal().compose);
    });

    it('OP-10: every Helm preset is recorded, layered as its own file says', () => {
      const presets = readdirSync(HELM).filter((name) => /^values(-.+)?\.yaml$/.test(name));
      const recorded = Object.keys(shippedJournal().helm).flatMap((chain) => chain.split(' + '));
      expect([...new Set(recorded)].sort()).to.deep.equal(presets.sort());
      // A preset the matrix layers on another says so in its own header.
      for (const chain of Object.keys(shippedJournal().helm).filter((c) => c.includes(' + '))) {
        const [base, top] = chain.split(' + ') as [string, string];
        expect(readFileSync(join(HELM, top), 'utf8'), chain).to.include(`Layer this after ${base}`);
      }
    });

    for (const [chain, expected] of Object.entries(shippedJournal().helm)) {
      it(`OP-10: Helm ${chain} deploys a set the probe answers ${expected}`, async () => {
        const probed = await probeJournalDurability(answering(helmHello(helmValues(chain))) as never);
        expect(probed.reason).to.equal(expected);
      });
    }

    it('OP-10: the chart decides an arbiter as this test models it', () => {
      // the app env (and its write concern) lives in the shared appEnv define since main moved it out of deployment.yaml
      const appEnv = readFileSync(join(HELM, 'templates/_helpers.tpl'), 'utf8');
      expect(appEnv).to.include(
        '$hasArbiter := and .Values.mongodb.enabled .Values.mongodb.arbiter.enabled',
      );
      expect(readFileSync(join(HELM, 'templates/mongodb-statefulset.yaml'), 'utf8')).not.to.match(/arbiter/i);
    });

    it('OP-10: FLAGS.md renders the table and no longer claims every Helm default', () => {
      const doc = readFileSync(FLAGS_DOC, 'utf8');
      const shipped = shippedJournal();
      expect(doc).to.include(docRow('Compose (`docker-compose.yml`)', shipped.compose));
      for (const [chain, reason] of Object.entries(shipped.helm)) {
        expect(doc, chain).to.include(docRow(helmLabel(chain), reason));
      }
      expect(doc).not.to.include('hold at defaults on Compose and Helm');
    });
  });

  describe('FM-11 (AD-13): who edits which approvals section', () => {
    const flag = () =>
      matrixFlag('/services/agentHarness/approvals') as ReturnType<typeof matrixFlag> & {
        changedBy: string;
        editors: { orgAdmin: string[]; operator: string[] };
      };
    const section = (path: string): string => path.split('[')[0]!;

    it("AD-13: the operator edits every section the schema holds; no org admin edits any", () => {
      const { orgAdmin, operator } = flag().editors;
      expect(orgAdmin).to.deep.equal([]);
      const schema = JSON.parse(
        readFileSync(join(REPO_ROOT, 'contracts/harness/ext/pipeshub/approvals-document.schema.json'), 'utf8'),
      ) as { properties: Record<string, unknown> };
      expect(operator.map(section).sort()).to.deep.equal(Object.keys(schema.properties).sort());
    });

    it('AD-13: changedBy and FLAGS.md name every section and its editor', () => {
      const { changedBy, editors } = flag();
      for (const path of [...editors.orgAdmin.filter((p) => !p.startsWith('updated')), ...editors.operator]) {
        expect(changedBy, path).to.include(path);
      }
      expect(changedBy).to.match(/^operator /);
      expect(readFileSync(FLAGS_DOC, 'utf8')).to.include(
        `| \`/services/agentHarness/approvals\` | config | deployment | \`unset\` | ${changedBy} |`,
      );
    });
  });
});

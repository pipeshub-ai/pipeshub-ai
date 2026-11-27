/**
 * WP-114 under DOC_STORE=postgres: the boot step that builds the control plane's Mongo indexes leaves the database
 * alone and reports the store ready (the migrator owns the PostgreSQL schema), and the legacy-settings migration CLIs
 * take no Mongo options there: they connect as the application does (WP114-FU-CLIS; the PostgreSQL run itself is
 * `operator-clis.pg.itest.ts`).
 */
import { expect } from 'chai';
import { preparePolicyStore } from '../../../src/modules/agent-harness/adapters/configuration/policy-backing';
import { runCapabilityMigrationCli } from '../../../src/modules/agent-harness/ops/capability-migration-cli';

describe('WP-114 control plane boot on PostgreSQL', () => {
  const saved = process.env.DOC_STORE;
  beforeEach(() => {
    process.env.DOC_STORE = 'postgres';
  });
  afterEach(() => {
    if (saved === undefined) delete process.env.DOC_STORE;
    else process.env.DOC_STORE = saved;
  });

  it('preparePolicyStore builds no Mongo index and reports the store ready', async () => {
    const info: unknown[][] = [];
    const errors: unknown[][] = [];
    let asked = 0;
    await preparePolicyStore(
      { info: (...args: unknown[]) => info.push(args), error: (...args: unknown[]) => errors.push(args) },
      () => {
        asked += 1;
        return undefined;
      },
    );
    expect(asked).to.equal(0);
    expect(errors).to.deep.equal([]);
    expect(info).to.deep.equal([['Control plane store ready: PostgreSQL schema', { store: 'postgres' }]]);
  });

  it('the capability migration needs no --mongo-uri or --db and connect as the application does', async () => {
    const connected: unknown[][] = [];
    const out: string[] = [];
    const capability = await runCapabilityMigrationCli(
      ['dry-run', '--ai-backend', 'http://unused', '--secrets-file', '/unused'],
      (options) => {
        connected.push([options['mongo-uri'], options['db']]);
        return Promise.resolve({
          migration: { run: () => Promise.resolve([]), runFor: () => Promise.resolve([]) },
          close: () => Promise.resolve(),
        });
      },
      (line) => out.push(line),
      (line) => out.push(`stderr: ${line}`),
    );
    expect(capability).to.equal(0);
    expect(connected).to.deep.equal([[undefined, undefined]]);
    expect(out.filter((line) => line.startsWith('stderr'))).to.deep.equal([]);
  });
});

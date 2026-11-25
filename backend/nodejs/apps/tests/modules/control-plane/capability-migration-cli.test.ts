/**
 * MG-1 / DEP-18 (CPD-7): the operator's entry point. Usage errors exit 2 and
 * connect to nothing; a run prints one JSON report per org (every org without
 * --org) and exits 1 when an org failed or held anything back.
 */
import { expect } from 'chai';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { CapabilityMigration } from '../../../src/modules/control-plane';
import { connectCapabilityMigration, runCapabilityMigrationCli } from '../../../src/modules/agent-harness/ops/capability-migration-cli';
import { LegacyFacts, legacyFacts, migrationDeps } from './support/capability-world';
import { ORG_A, ORG_B, rejection } from './support/cp-objects';
import { InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';
import { auditedControlPlane } from './support/in-memory';

const ARGS = ['--mongo-uri', 'mongodb://x', '--db', 'd', '--ai-backend', 'http://q', '--secrets-file', '/s.json'];

function backend(facts: LegacyFacts) {
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const world = {
    installations,
    grants,
    ledger: new InMemoryCapabilityLedger(installations, grants),
    transaction: auditedControlPlane().transaction,
    configEntries: async () => 0,
  };
  let closed = false;
  return {
    world,
    closed: () => closed,
    connect: async () => ({
      migration: new CapabilityMigration(migrationDeps(world, facts)),
      close: async () => {
        closed = true;
      },
    }),
  };
}

describe('CPD-7 capability migration CLI', () => {
  it('a usage error exits 2 and connects to nothing', async () => {
    let connected = false;
    const errors: string[] = [];
    const connect = async () => {
      connected = true;
      throw new Error('unreachable');
    };
    expect(await runCapabilityMigrationCli(['migrate', ...ARGS], connect, () => undefined, (l) => errors.push(l))).to.equal(2);
    expect(await runCapabilityMigrationCli(['apply', '--db', 'd'], connect, () => undefined, (l) => errors.push(l))).to.equal(2);
    expect(connected).to.equal(false);
    expect(errors[1]).to.contain('--mongo-uri');
  });

  it('a usage error is written to stderr where no writer is given', async () => {
    const written: string[] = [];
    const stderr = sinon.stub(process.stderr, 'write').callsFake((chunk) => {
      written.push(String(chunk));
      return true;
    });
    try {
      expect(await runCapabilityMigrationCli(['migrate', ...ARGS])).to.equal(2);
    } finally {
      stderr.restore();
    }
    expect(written).to.deep.equal(['the command is dry-run, apply or revert\n']);
  });

  it('MG-1: dry-run reports every org, writes nothing, exits 1 on holds and closes the connection', async () => {
    const facts = new LegacyFacts();
    facts.orgs.set(ORG_A, legacyFacts());
    facts.orgs.set(ORG_B, legacyFacts({ toolsetInstances: [], agents: [] }));
    const b = backend(facts);
    const lines: string[] = [];
    expect(await runCapabilityMigrationCli(['dry-run', ...ARGS], b.connect, (l) => lines.push(l))).to.equal(1);
    expect(lines.map((l) => (JSON.parse(l) as { orgId: string; mode: string }).orgId)).to.deep.equal([ORG_A, ORG_B]);
    expect(await b.world.installations.listForOrg(ORG_A, {})).to.deep.equal([]);
    expect(b.closed()).to.equal(true);
  });

  it('apply on one org with nothing held exits 0', async () => {
    const facts = new LegacyFacts();
    facts.orgs.set(ORG_B, legacyFacts({ toolsetInstances: [], agents: [] }));
    const b = backend(facts);
    const lines: string[] = [];
    expect(await runCapabilityMigrationCli(['apply', ...ARGS, '--org', ORG_B], b.connect, (l) => lines.push(l))).to.equal(0);
    expect(lines).to.have.length(1);
    expect((await b.world.installations.listForOrg(ORG_B, {})).length).to.equal(2);
  });

  describe('the production backend', () => {
    let dir: string;
    beforeEach(() => {
      dir = mkdtempSync(join(tmpdir(), 'cpd7-cli-'));
    });
    afterEach(() => {
      sinon.restore();
      rmSync(dir, { recursive: true, force: true });
    });
    const options = (secrets: unknown) => {
      const path = join(dir, 'secrets.json');
      writeFileSync(path, JSON.stringify(secrets));
      return { 'mongo-uri': 'mongodb://x', db: 'd', 'ai-backend': 'http://q', 'secrets-file': path };
    };

    it('refuses a secrets file without both service secrets before it connects to anything', async () => {
      const connect = sinon.stub(mongoose, 'connect');
      const refused = await rejection(connectCapabilityMigration(options({ jwtSecret: 'j' })));
      expect((refused as Error).message).to.contain('{jwtSecret, scopedJwtSecret}');
      expect(connect.called).to.equal(false);
    });

    it('connects to the database named, building no index on its own, and stops when there is no connection', async () => {
      const connect = sinon.stub(mongoose, 'connect').resolves(mongoose);
      const refused = await rejection(connectCapabilityMigration(options({ jwtSecret: 'j', scopedJwtSecret: 's' })));
      expect((refused as Error).message).to.equal('No database connection');
      expect(connect.firstCall.args).to.deep.equal(['mongodb://x', { dbName: 'd', autoIndex: false }]);
    });
  });
});

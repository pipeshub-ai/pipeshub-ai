/**
 * MG: CPD-7's capability migration and dual-write sync, run on the in-memory
 * stores (`capability-migration.test.ts`) and on the replica set
 * (`capability-migration.mongo.itest.ts`, MG-9).
 */
import { expect } from 'chai';
import { CapabilityMigration } from '../../../../src/modules/control-plane/application/capability-migration';
import { skillInstallationId } from '../../../../src/modules/control-plane/application/capability-plan';
import { releaseItemsOf } from '../../../../src/modules/control-plane/domain/release-items';
import { CUSTOM_MCP, JIRA, LegacyFacts, legacyFacts, LINEAR, migrationDeps, type CapabilityWorld } from './capability-world';
import { digest, directTx, ORG_A, ORG_B } from './cp-objects';

export function describeCapabilityMigrationCases(name: string, make: () => Promise<CapabilityWorld>): void {
  describe(`capability migration (MG): ${name}`, () => {
    let world: CapabilityWorld;
    let source: LegacyFacts;
    let migration: CapabilityMigration;

    beforeEach(async () => {
      world = await make();
      source = new LegacyFacts();
      source.orgs.set(ORG_A, legacyFacts());
      migration = new CapabilityMigration(migrationDeps(world, source));
    });

    const installationIds = async (orgId = ORG_A) => (await world.installations.listForOrg(orgId, {})).map((inst) => inst.id);

    it('MG-1 / DEP-18: a dry run reports what apply would write and writes nothing', async () => {
      const [report] = await migration.runFor('dry-run', [ORG_A]);
      expect(report).to.include({ created: 8, updated: 0, unchanged: 0, failed: null });
      expect(report!.holds.map((hold) => hold.reason).sort()).to.deep.equal(['type_keyed_toolset', 'unknown_toolset_type']);
      expect(await installationIds()).to.deep.equal([]);
      expect(await world.grants.get(ORG_A, 'agent:a7')).to.equal(null);
      expect(await world.configEntries(ORG_A)).to.equal(0);
      expect(await world.ledger.entries(ORG_A)).to.deep.equal([]);
    });

    it('MG-1: a whole run (the registered data migration) covers every org the deployment lists', async () => {
      source.orgs.set(ORG_B, legacyFacts({ toolsetInstances: [], agents: [] }));
      expect((await migration.dryRun()).map((report) => report.orgId)).to.deep.equal([ORG_A, ORG_B]);
      expect(await installationIds(ORG_B)).to.deep.equal([]);
      expect((await migration.apply()).map((report) => report.mode)).to.deep.equal(['apply', 'apply']);
      expect((await installationIds(ORG_B)).length).to.equal(2);
      await migration.revert();
      expect(await installationIds(ORG_B)).to.deep.equal([]);
    });

    it('MG-2: each toolset instance becomes an org installation with its id kept and the release tools approved', async () => {
      await migration.runFor('apply', [ORG_A]);
      const jira = (await world.installations.get(ORG_A, JIRA))!;
      const item = releaseItemsOf('toolset').find((candidate) => candidate.name === 'jira')!;
      expect(jira).to.include({ kind: 'toolset', item: item.ref, owner: 'org', visibility: 'org', status: 'enabled', credentialMode: 'per_user' });
      expect(jira.approved?.descriptorSetDigest).to.equal(item.descriptorSetDigest);
      expect(Object.keys(jira.approved!.tools).sort()).to.deep.equal(
        (item.descriptors.kind === 'tool' ? item.descriptors.items : []).map((tool) => tool.name).sort(),
      );
      expect((await world.installations.get(ORG_A, 'calc-1'))?.credentialMode).to.equal('none');
      expect(await world.installations.get(ORG_A, 'odd-1')).to.equal(null);
    });

    it('MG-3: an MCP instance with a manifest is approved for exactly its tools; without one it is not approved', async () => {
      await migration.runFor('apply', [ORG_A]);
      const linear = (await world.installations.get(ORG_A, LINEAR))!;
      expect(linear.item).to.equal(releaseItemsOf('mcp').find((item) => item.name === 'linear')!.ref);
      expect(linear.approved).to.deep.include({ tools: { list_issues: digest('a'), create_issue: digest('b') }, by: 'user:u-admin', at: 21 });
      const custom = (await world.installations.get(ORG_A, CUSTOM_MCP))!;
      expect([custom.item, custom.approved, custom.status]).to.deep.equal([`mcp:org/${CUSTOM_MCP}@1`, null, 'enabled']);
    });

    it('MG-4: agent edges become one grant per agent; a type-keyed toolset node is held, not guessed', async () => {
      const [report] = await migration.runFor('apply', [ORG_A]);
      expect((await world.grants.get(ORG_A, 'agent:a7'))?.entries).to.deep.equal(
        [
          { installation: JIRA, tools: ['search_issues'], required: false },
          { installation: LINEAR, tools: null, required: false },
          { installation: skillInstallationId('data-analysis'), tools: null, required: false },
          { installation: skillInstallationId('my-notes'), tools: null, required: false },
        ].sort((a, b) => (a.installation < b.installation ? -1 : 1)),
      );
      expect((await world.grants.get(ORG_A, 'agent:a8'))?.entries).to.deep.equal([{ installation: CUSTOM_MCP, tools: ['ping'], required: false }]);
      expect(report!.holds).to.deep.include({ reason: 'type_keyed_toolset', object: 'agent:a7/n2' });
    });

    it("MG-5: a builtin skill is the org's; a custom skill is its author's and visible to them", async () => {
      await migration.runFor('apply', [ORG_A]);
      const builtin = (await world.installations.get(ORG_A, skillInstallationId('data-analysis')))!;
      expect(builtin).to.include({ owner: 'org', visibility: 'org', item: releaseItemsOf('skill').find((item) => item.name === 'data-analysis')!.ref });
      const custom = (await world.installations.get(ORG_A, skillInstallationId('my-notes')))!;
      expect(custom).to.include({ owner: 'user:u1', visibility: 'owner', item: 'skill:user/my-notes@0.1.0' });
      expect(custom.approved?.tools).to.deep.equal({ 'my-notes': digest('d') });
    });

    it('MG-6: a second apply writes nothing', async () => {
      await migration.runFor('apply', [ORG_A]);
      const entries = await world.configEntries(ORG_A);
      const [again] = await migration.runFor('apply', [ORG_A]);
      expect(again).to.include({ created: 0, updated: 0, removed: 0, unchanged: 8, failed: null });
      expect(await world.configEntries(ORG_A)).to.equal(entries);
    });

    it('MG-7: revert removes what apply wrote; an object changed since is drifted and kept', async () => {
      await migration.runFor('apply', [ORG_A]);
      const linear = (await world.installations.get(ORG_A, LINEAR))!;
      await world.transaction.run({ orgId: ORG_A, actor: 'user:u-admin' }, async (tx) => {
        await world.installations.replace(tx, { ...linear, visibility: 'team', owner: 'team:t1' }, linear.revision);
        return { result: null, change: { scope: `org:${ORG_A}`, object: { type: 'installation', id: LINEAR }, revisionFrom: 1, revisionTo: 2, changeDigest: digest('e'), changed: ['visibility'] } };
      });
      const [report] = await migration.runFor('revert', [ORG_A]);
      expect(report!.drifted).to.deep.equal([{ object: 'installation', id: LINEAR, revision: 1 }]);
      expect(await installationIds()).to.deep.equal([LINEAR]);
      expect(await world.grants.get(ORG_A, 'agent:a7')).to.equal(null);
      expect(await world.ledger.entries(ORG_A)).to.deep.equal([{ object: 'installation', id: LINEAR, revision: 1 }]);
      const [reapplied] = await migration.runFor('apply', [ORG_A]);
      expect(reapplied!.failed).to.equal(null);
    });

    it('MG-8: every object write is audited once and lands with its ledger entry', async () => {
      const [report] = await migration.runFor('apply', [ORG_A]);
      expect(await world.configEntries(ORG_A)).to.equal(report!.created);
      expect((await world.ledger.entries(ORG_A)).length).to.equal(report!.created);
    });

    it('MG-10 / CST-13: a legacy source that fails for one org stops that org only; a rerun completes it', async () => {
      source.orgs.set(ORG_B, legacyFacts({ agents: [] }));
      source.failing.add(ORG_A);
      const [failed, done] = await migration.runFor('apply', [ORG_A, ORG_B]);
      expect(failed).to.include({ failed: 'legacy_source_unavailable', created: 0 });
      expect(await installationIds()).to.deep.equal([]);
      expect(done!.failed).to.equal(null);
      expect((await installationIds(ORG_B)).length).to.equal(4);
      source.failing.clear();
      expect((await migration.runFor('apply', [ORG_A]))[0]).to.include({ failed: null, created: 8 });
    });

    it('MG-2: org B never receives org A instances, and a source answering for another org is refused', async () => {
      await migration.runFor('apply', [ORG_A]);
      expect(await installationIds(ORG_B)).to.deep.equal([]);
      const lying = { page: async (_orgId: string, options: Parameters<LegacyFacts['page']>[1]) => source.page(ORG_A, options) };
      const [report] = await new CapabilityMigration(migrationDeps(world, lying)).runFor('apply', [ORG_B]);
      expect(report!.failed).to.equal('legacy_source_refused');
      expect(await installationIds(ORG_B)).to.deep.equal([]);
    });

    it('DW-1: syncing an agent writes its grant and its skills only; a deleted agent loses its grant', async () => {
      await migration.syncAgent(ORG_A, 'a8');
      expect((await world.grants.get(ORG_A, 'agent:a8'))?.entries).to.have.length(1);
      expect(await installationIds()).to.deep.equal([]);
      source.orgs.set(ORG_A, legacyFacts({ agents: legacyFacts().agents.filter((agent) => agent.agentKey !== 'a8') }));
      await migration.syncAgent(ORG_A, 'a8');
      expect((await world.grants.get(ORG_A, 'agent:a8'))?.entries).to.deep.equal([]);
    });

    it('DW-2: syncing installations follows instance edits and marks a deleted instance removed', async () => {
      await migration.syncInstallations(ORG_A);
      expect(await world.grants.get(ORG_A, 'agent:a7')).to.equal(null);
      const facts = legacyFacts();
      source.orgs.set(ORG_A, { ...facts, mcpInstances: facts.mcpInstances.filter((mcp) => mcp.instanceId !== CUSTOM_MCP) });
      const report = await migration.syncInstallations(ORG_A);
      expect(report).to.include({ removed: 1 });
      expect((await world.installations.get(ORG_A, CUSTOM_MCP))?.status).to.equal('removed');
    });

    it("SYNCFIX1: a personal toolset instance is its owner's installation, and goes when it is deleted", async () => {
      const facts = legacyFacts();
      const personal = { instanceId: 'mine-1', instanceName: 'My Jira', toolsetType: 'jira', authType: 'API_TOKEN', createdBy: 'u2', updatedAt: 13, ownerId: 'u2' };
      source.orgs.set(ORG_A, { ...facts, toolsetInstances: [...facts.toolsetInstances, personal] });
      expect(await migration.syncInstallations(ORG_A)).to.include({ failed: null });
      expect(await world.installations.get(ORG_A, 'mine-1')).to.include({ kind: 'toolset', owner: 'user:u2', visibility: 'owner', status: 'enabled' });
      expect(await world.installations.get(ORG_A, JIRA)).to.include({ owner: 'org', visibility: 'org' });
      source.orgs.set(ORG_A, facts);
      expect(await migration.syncInstallations(ORG_A)).to.include({ removed: 1 });
      expect((await world.installations.get(ORG_A, 'mine-1'))?.status).to.equal('removed');
      expect((await world.installations.get(ORG_A, JIRA))?.status).to.equal('enabled');
    });

    it('a write the schema refuses is held and reported; the rest of the org lands', async () => {
      const facts = legacyFacts();
      source.orgs.set(ORG_A, { ...facts, mcpInstances: [{ ...facts.mcpInstances[1]!, instanceId: CUSTOM_MCP, manifest: null, typeId: null }], agents: [{ ...facts.agents[1]!, mcpServers: [{ instanceId: 'x'.repeat(129), tools: null }] }] });
      const [report] = await migration.runFor('apply', [ORG_A]);
      expect(report!.holds.map((hold) => hold.reason)).to.include('invalid_object');
      expect(await world.installations.get(ORG_A, CUSTOM_MCP)).to.not.equal(null);
    });

    it('an installation the migration did not write is never marked removed or reverted', async () => {
      const foreign = { ...(await (async () => {
        await migration.runFor('apply', [ORG_A]);
        return (await world.installations.get(ORG_A, LINEAR))!;
      })()), id: 'foreign-1' };
      await world.installations.create(directTx(ORG_A), foreign);
      await migration.runFor('apply', [ORG_A]);
      expect((await world.installations.get(ORG_A, 'foreign-1'))?.status).to.equal('enabled');
      await migration.runFor('revert', [ORG_A]);
      expect(await installationIds()).to.deep.equal(['foreign-1']);
    });

    describe('CPD10-56: the org is recorded as migrated', () => {
      /** Facts the plan holds nothing of: only a whole org is recorded. */
      const whole = () => {
        const facts = legacyFacts();
        return {
          ...facts,
          toolsetInstances: facts.toolsetInstances.filter((instance) => instance.toolsetType !== 'no-such-toolset'),
          agents: facts.agents.map((agent) => ({ ...agent, toolsets: agent.toolsets.filter((toolset) => toolset.instanceId !== null) })),
        };
      };

      beforeEach(() => {
        source.orgs.set(ORG_A, whole());
      });

      it('apply records it in one audited write after the objects; a second apply writes nothing', async () => {
        const [report] = await migration.runFor('apply', [ORG_A]);
        expect(report).to.include({ failed: null, migrated: true });
        expect(report!.holds).to.deep.equal([]);
        expect(await world.ledger.migrated(ORG_A)).to.equal(true);
        expect(await world.configEntries(ORG_A)).to.equal(report!.created + 1);
        const [again] = await migration.runFor('apply', [ORG_A]);
        expect(again).to.include({ created: 0, updated: 0, migrated: true });
        expect(await world.configEntries(ORG_A)).to.equal(report!.created + 1);
      });

      it('an org with nothing to migrate is recorded too', async () => {
        const [report] = await migration.runFor('apply', [ORG_B]);
        expect(report).to.include({ created: 0, failed: null, migrated: true });
        expect(await world.ledger.migrated(ORG_B)).to.equal(true);
        expect(await world.configEntries(ORG_B)).to.equal(1);
        expect(await world.ledger.migrated(ORG_A)).to.equal(false);
      });

      it('a dry run records nothing and reports the org as it is', async () => {
        expect((await migration.runFor('dry-run', [ORG_A]))[0]).to.include({ migrated: false });
        expect(await world.ledger.migrated(ORG_A)).to.equal(false);
        await migration.runFor('apply', [ORG_A]);
        expect((await migration.runFor('dry-run', [ORG_A]))[0]).to.include({ migrated: true });
      });

      it('revert removes the row in its own audited write, before the objects go', async () => {
        const [applied] = await migration.runFor('apply', [ORG_A]);
        const before = await world.configEntries(ORG_A);
        const [report] = await migration.runFor('revert', [ORG_A]);
        expect(report).to.include({ failed: null, migrated: false, reverted: applied!.created });
        expect(await world.ledger.migrated(ORG_A)).to.equal(false);
        expect(await world.configEntries(ORG_A)).to.equal(before + applied!.created + 1);
        const [again] = await migration.runFor('revert', [ORG_A]);
        expect(again).to.include({ reverted: 0, migrated: false });
        expect(await world.configEntries(ORG_A)).to.equal(before + applied!.created + 1);
      });

      it('a revert that stops part-way has already taken the row back: an org is never recorded over a half-removed set', async () => {
        await migration.runFor('apply', [ORG_A]);
        const ledger = Object.assign(Object.create(world.ledger) as CapabilityWorld['ledger'], {
          undo: async () => {
            throw new Error('store down');
          },
        });
        const [report] = await new CapabilityMigration({ ...migrationDeps(world, source), ledger }).runFor('revert', [ORG_A]);
        expect(report).to.include({ reverted: 0, migrated: false });
        expect(report!.failed).to.not.equal(null);
        expect(await world.ledger.migrated(ORG_A)).to.equal(false);
      });

      it('an org the plan held something of, or that failed, is not recorded: its legacy pins stay the authority', async () => {
        source.orgs.set(ORG_A, legacyFacts());
        const [held] = await migration.runFor('apply', [ORG_A]);
        expect(held!.holds).to.not.deep.equal([]);
        expect(held).to.include({ failed: null, migrated: false });
        source.orgs.set(ORG_B, whole());
        source.failing.add(ORG_B);
        expect((await migration.runFor('apply', [ORG_B]))[0]).to.include({ migrated: false });
        expect([await world.ledger.migrated(ORG_A), await world.ledger.migrated(ORG_B)]).to.deep.equal([false, false]);
      });

      it('a later apply that holds something leaves a recorded org recorded: only revert takes the row back', async () => {
        await migration.runFor('apply', [ORG_A]);
        const entries = await world.configEntries(ORG_A);
        source.orgs.set(ORG_A, { ...whole(), toolsetInstances: legacyFacts().toolsetInstances });
        const [report] = await migration.runFor('apply', [ORG_A]);
        expect(report!.holds.map((hold) => hold.reason)).to.deep.equal(['unknown_toolset_type']);
        expect(report).to.include({ migrated: true });
        expect(await world.configEntries(ORG_A)).to.equal(entries);
      });

      it('the dual-write sync of one agent or of the instances never records an org, and leaves a recorded one recorded', async () => {
        expect(await migration.syncAgent(ORG_A, 'a8')).to.include({ failed: null, migrated: null });
        expect(await migration.syncInstallations(ORG_A)).to.include({ failed: null, migrated: null });
        expect(await world.ledger.migrated(ORG_A)).to.equal(false);
        await migration.runFor('apply', [ORG_A]);
        const entries = await world.configEntries(ORG_A);
        await migration.syncAgent(ORG_A, 'a8');
        await migration.syncInstallations(ORG_A);
        expect(await world.configEntries(ORG_A)).to.equal(entries);
        expect(await world.ledger.migrated(ORG_A)).to.equal(true);
      });
    });
  });
}

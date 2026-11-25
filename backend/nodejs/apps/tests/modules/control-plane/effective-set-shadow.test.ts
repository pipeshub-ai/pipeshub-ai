/**
 * CPD-7 shadow (05 §1 rule 6): SH-1 the `CONTROL_PLANE_PIN` flag; SH-2 a
 * planted difference is counted per dimension and the legacy pin is left as
 * it was; SH-3 `off` reads nothing; SH-4 a store failure is counted, never
 * thrown into admission.
 */
import { expect } from 'chai';
import { CanonicalEffectiveSetResolver } from '../../../src/modules/control-plane/adapters/effective-set-resolver';
import {
  InvalidControlPlanePinError,
  parseControlPlanePin,
  type ControlPlanePin,
} from '../../../src/modules/control-plane/application/control-plane-pin';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import {
  capabilitiesMigrated,
  EffectiveSetShadow,
  type ShadowSubject,
} from '../../../src/modules/control-plane/application/effective-set-shadow';
import type { LegacyPin, ShadowDimension } from '../../../src/modules/control-plane/domain/shadow-diff';
import type { InstallationStore } from '../../../src/modules/control-plane/ports/installation-store';
import { aGrant, anEntry, anInstallation, directTx, instId, ORG_A } from './support/cp-objects';
import { InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore, snapshotsOver } from './support/in-memory-stores';
import { RecordingLog } from './support/in-memory';

class Counter {
  readonly counts = new Map<ShadowDimension, number>();
  inc(dimension: ShadowDimension): void {
    this.counts.set(dimension, (this.counts.get(dimension) ?? 0) + 1);
  }
}

const SUBJECT: ShadowSubject = { orgId: ORG_A, actorId: 'u3', agentKey: 'a7', agentOwner: 'u1' };
const NOTES = 'skill_notes';

async function world(mode: ControlPlanePin = 'shadow', installations: InstallationStore = new InMemoryInstallationStore()) {
  const grants = new InMemoryGrantStore();
  const counter = new Counter();
  const log = new RecordingLog();
  const shadow = new EffectiveSetShadow({ mode, snapshots: snapshotsOver(installations, grants), resolver: new CanonicalEffectiveSetResolver(), counter, log });
  const tx = directTx(ORG_A);
  await installations.create(tx, anInstallation());
  await installations.create(tx, anInstallation({ id: instId(2), owner: 'user:u9', visibility: 'owner' }));
  await installations.create(tx, anInstallation({ id: instId(3), credentialMode: 'per_user' }));
  await installations.create(
    tx,
    anInstallation({ id: NOTES, kind: 'skill', item: 'skill:user/notes@1.0.0', owner: 'user:u1', visibility: 'owner', config: { name: 'notes' }, approved: { descriptorSetDigest: anInstallation().approved!.descriptorSetDigest, tools: { notes: anInstallation().approved!.descriptorSetDigest }, approvalRef: null, at: 1, by: 'system' } }),
  );
  await grants.put(tx, aGrant({ entries: [anEntry(instId(1), ['list_issues']), anEntry(instId(2)), anEntry(instId(3)), anEntry(NOTES)] }), 0);
  return { shadow, counter, log, grants, installations };
}

const agreeing = (): LegacyPin => ({
  toolsetTools: [],
  mcpTools: [`mcp@${instId(1)}/list_issues`, `mcp@${instId(2)}/list_issues`, `mcp@${instId(3)}/create_issue`],
  skills: ['notes'],
});

describe('CPD-7 effective-set shadow', () => {
  it('SH-1: CONTROL_PLANE_PIN unset is on (C4.04), reads its three values in any case, and refuses anything else', () => {
    const on = { AGENT_HARNESS_WORKER_ENABLED: 'true' };
    expect(parseControlPlanePin(on)).to.equal('on');
    expect(parseControlPlanePin({ ...on, CONTROL_PLANE_PIN: ' ' })).to.equal('on');
    expect(parseControlPlanePin({ CONTROL_PLANE_PIN: 'OFF' })).to.equal('off');
    expect(parseControlPlanePin({ CONTROL_PLANE_PIN: 'Shadow' })).to.equal('shadow');
    expect(parseControlPlanePin({ CONTROL_PLANE_PIN: 'on' })).to.equal('on');
    expect(() => parseControlPlanePin({ CONTROL_PLANE_PIN: 'yes' })).to.throw(InvalidControlPlanePinError);
  });

  it('SH-2: only the visibility difference is counted when the rest agrees; a credential the run lacks is no difference', async () => {
    const { shadow, counter } = await world();
    expect(await shadow.observe(SUBJECT, agreeing())).to.deep.equal(['visibility']);
    expect([...counter.counts]).to.deep.equal([['visibility', 1]]);
  });

  it("SH-2: the agent owner's skill is shared, so a colleague's run agrees on it", async () => {
    const { shadow } = await world();
    const pin = { ...agreeing(), mcpTools: agreeing().mcpTools.filter((tool) => !tool.includes(instId(2))) };
    expect(await shadow.observe(SUBJECT, pin)).to.deep.equal([]);
  });

  it('SH-2: each planted difference is counted under its dimension, and the legacy pin is not changed', async () => {
    const { shadow, counter } = await world();
    const pin: LegacyPin = {
      toolsetTools: [`jira@${instId(9)}/search_issues`],
      mcpTools: [`mcp@${instId(1)}/create_issue`],
      skills: ['someone-elses'],
    };
    const frozen = JSON.stringify(pin);
    expect(await shadow.observe(SUBJECT, Object.freeze(pin))).to.deep.equal(['mcp', 'skill', 'toolset']);
    expect(JSON.stringify(pin)).to.equal(frozen);
    expect(counter.counts.get('mcp')).to.equal(1);
  });

  it('SH-2: an approval difference names approval', async () => {
    const installations = new InMemoryInstallationStore();
    const { shadow } = await world('shadow', installations);
    const linear = (await installations.get(ORG_A, instId(1)))!;
    await installations.replace(directTx(ORG_A), { ...linear, approved: null }, linear.revision);
    expect(await shadow.observe(SUBJECT, agreeing())).to.include('approval');
  });

  it('SH-3: off reads nothing and counts nothing', async () => {
    const installations = new InMemoryInstallationStore();
    const { shadow, counter } = await world('off', installations);
    const before = installations.queries;
    expect(await shadow.observe(SUBJECT, { toolsetTools: ['x@y/z'], mcpTools: [], skills: [] })).to.deep.equal([]);
    expect(installations.queries).to.equal(before);
    expect(counter.counts.size).to.equal(0);
  });

  it('SH-4: a snapshot that cannot be read is counted as unavailable and logged once; it never throws', async () => {
    const broken = new InMemoryInstallationStore();
    const { shadow, counter, log } = await world('shadow', broken);
    broken.listForOrg = () => Promise.reject(new Error('mongo down'));
    expect(await shadow.observe(SUBJECT, agreeing())).to.deep.equal(['unavailable']);
    expect(counter.counts.get('unavailable')).to.equal(1);
    expect(log.lines.filter(([level]) => level === 'warn')).to.have.length(1);
  });

  describe('CPD-10 R3: what a migrated org holds ready', () => {
    const HEADS = { head: async () => 1, heads: async () => ({ org: 1, deployment: 0 }) };

    async function migratedWorld(mode: ControlPlanePin, migrated: boolean) {
      const { grants, installations } = await world();
      const ledger = new InMemoryCapabilityLedger(installations as InMemoryInstallationStore, grants);
      if (migrated) await ledger.markMigrated(directTx(ORG_A));
      const counter = new Counter();
      const load = policySnapshotLoader({ heads: HEADS, capabilities: { installations, grants, ledger } });
      const shadow = new EffectiveSetShadow({ mode, snapshots: { snapshot: load }, resolver: new CanonicalEffectiveSetResolver(), counter, log: new RecordingLog() });
      return { shadow, counter, load };
    }

    it('CPD10-55: the review names each ready installation with the tools the set holds; an unavailable one is not named', async () => {
      const { shadow } = await migratedWorld('on', true);
      const { granted, notVisible } = await shadow.review(SUBJECT, agreeing());
      expect([...granted!].map(([installation, tools]) => [installation, [...tools]])).to.deep.equal([
        [instId(1), ['list_issues']],
        [NOTES, ['notes']],
      ]);
      expect([...notVisible!]).to.deep.equal([instId(2)]);
    });

    it('CPD10-54: an org that is not migrated has no such answer, in any mode: its definition stays the authority', async () => {
      for (const mode of ['shadow', 'on'] as const) {
        expect((await (await migratedWorld(mode, false)).shadow.review(SUBJECT, agreeing())).granted).to.equal(null);
      }
      expect((await (await migratedWorld('off', true)).shadow.review(SUBJECT, agreeing())).granted).to.equal(null);
    });

    it('the row changes nothing the shadow counts: a migrated org is compared as before', async () => {
      const plain = await migratedWorld('shadow', false);
      const marked = await migratedWorld('shadow', true);
      expect(await marked.shadow.observe(SUBJECT, agreeing())).to.deep.equal(await plain.shadow.observe(SUBJECT, agreeing()));
      expect([...marked.counter.counts]).to.deep.equal([...plain.counter.counts]);
    });

    it("capabilitiesMigrated reads the snapshot's own org layer and no other", async () => {
      const { load } = await migratedWorld('on', true);
      const snapshot = await load(ORG_A, 'a7', 'u3');
      expect(capabilitiesMigrated(snapshot)).to.equal(true);
      expect(capabilitiesMigrated({ orgId: 'org-b', layers: snapshot.layers })).to.equal(false);
      expect(capabilitiesMigrated(await (await migratedWorld('on', false)).load(ORG_A, 'a7', 'u3'))).to.equal(false);
    });
  });
});

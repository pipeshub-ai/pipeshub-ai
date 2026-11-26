/**
 * CPD10-60 (assigned item 4): an owner-shared item in a child run, through
 * the production child service. A parent is admitted under `on` for a
 * colleague of the agent's owner, with a skill the owner shared through the
 * agent (an owner-visible installation); the worker spawns a child through
 * `ChildRunService` (never `ChildPinning.pin` called by the test), and the
 * child's stored pin names the parent's owner and holds the skill ready. A
 * parent whose definition names no owner holds it `not_visible`, and so does
 * its child.
 *
 * The child's spec as this test's admission and spawn produced it is the
 * shared vector `contracts/control-plane/vectors/child-owner-pin.json`
 * (written with WRITE_CHILD_OWNER_VECTORS=1, compared otherwise): Python's
 * `test_cpd10_child_owner.py` feeds those bytes to the production assembler
 * (CPD10-61). Ids are the Python assembly world's, so the worker half needs
 * no translation.
 */
import { expect } from 'chai';
import { readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { canonicalDigest } from '../../../src/libs/utils/canonical-json';
import { pinDigest } from '../../../src/modules/control-plane/application/pin';
import type { ResolvedRunConfig } from '../../../src/modules/control-plane';
import { authorityWorld } from '../control-plane/support/authority-world';
import { aGrant, anEntry, anInstallation } from '../control-plane/support/cp-objects';
import { agentDefinition } from './support/agent-definition';

const VECTOR_PATH = join(__dirname, '../../../../../../contracts/control-plane/vectors/child-owner-pin.json');
const VECTOR = JSON.parse(readFileSync(VECTOR_PATH, 'utf8')) as {
  description: string;
  skill: { name: string; body: string; bundleDigest: string };
  cases: Array<{ id: string; owner: string | null; spec: unknown }>;
};
const ROW = 'rollout.CONTROL_PLANE_CAPABILITIES';
const AGENT = 'agent-1';
const COLLEAGUE = 'user-1';
const OWNER = 'u1';
const NOTES = `skill_${VECTOR.skill.name}`;
/** Every Labs stage on, as an org that uses skills has them: the worker reads its stages from the pin. */
const STAGES = { read: async () => ({ ENABLE_SKILLS: true, ENABLE_USER_CONTEXT: true, ENABLE_KNOWLEDGE_PUBLICATIONS: true }) };

async function tree(owner: string | null) {
  const w = authorityWorld(
    'on',
    (agentKey) =>
      agentDefinition({
        agentKey,
        toolsets: [],
        mcpServers: [],
        webSearch: null,
        knowledge: [{ connectorId: 'kb-1', isKb: true }],
        skills: [{ name: VECTOR.skill.name, version: '1.0.0', bundleDigest: VECTOR.skill.bundleDigest }],
        maxChildren: 4,
        ...(owner === null ? {} : { owner }),
      }),
    STAGES,
  );
  const approved = anInstallation().approved!;
  await w.write(async (tx) => {
    await w.installations.create(
      tx,
      anInstallation({
        id: NOTES,
        kind: 'skill',
        item: `skill:user/${VECTOR.skill.name}@1.0.0`,
        owner: `user:${OWNER}`,
        visibility: 'owner',
        config: { name: VECTOR.skill.name },
        approved: { ...approved, tools: { [VECTOR.skill.name]: VECTOR.skill.bundleDigest } },
      }),
    );
    await w.grants.put(tx, aGrant({ subject: `agent:${AGENT}`, entries: [anEntry(NOTES)] }), 0);
    await w.ledger.markMigrated(tx);
  });
  const parent = await w.admit(COLLEAGUE, AGENT);
  const child = await w.spawn(parent.run);
  return { w, parent, child };
}

const states = (pin: ResolvedRunConfig | null) =>
  pin!.effectiveSet.items.map((item) => [item.installation, item.state, item.reason, item.provenance.visibleVia]);

describe('CPD10-60: an owner-shared skill in a child run, through ChildRunService', () => {
  const written: typeof VECTOR.cases = [];

  it("the child's stored pin has its parent's scope.agentOwner and holds the owner's skill ready", async () => {
    const { parent, child } = await tree(OWNER);
    expect(parent.pin!.scope.agentOwner).to.equal(OWNER);
    expect(parent.skills).to.deep.equal([VECTOR.skill.name]);
    expect(states(parent.pin)).to.deep.equal([[NOTES, 'ready', null, 'owner']]);

    expect(child.pin!.scope).to.deep.equal({ agentKey: AGENT, projectId: null, agentOwner: OWNER });
    expect(states(child.pin)).to.deep.equal([[NOTES, 'ready', null, 'owner']]);
    expect(child.pin!.effectiveSet.items[0]!.tools).to.deep.equal([
      { toolId: `${VECTOR.skill.name}@${NOTES}/${VECTOR.skill.name}`, descriptorDigest: VECTOR.skill.bundleDigest },
    ]);
  });

  it("the child inherits the org's migrated row from its parent's pin, and its stored pin is the one in its spec", async () => {
    const { parent, child } = await tree(OWNER);
    expect(parent.pin!.effectiveConfig.dimensions[ROW]?.value).to.equal(true);
    expect(child.pin!.effectiveConfig.dimensions[ROW]).to.deep.equal(parent.pin!.effectiveConfig.dimensions[ROW]);
    const { digest, ...fields } = child.pin!;
    expect(pinDigest(fields)).to.equal(digest);
    const extension = (child.spec as { extensions: Record<string, { resolved?: ResolvedRunConfig; skills: Array<{ name: string }> }> }).extensions['x.pipeshub']!;
    expect(canonicalDigest(extension.resolved)).to.equal(canonicalDigest(child.pin));
    expect(extension.skills.map((skill) => skill.name)).to.deep.equal([VECTOR.skill.name]);
  });

  it('a parent without an owner holds the skill not_visible, admission takes it out, and its child holds it not_visible too', async () => {
    const { parent, child } = await tree(null);
    expect(parent.pin!.scope).to.deep.equal({ agentKey: AGENT, projectId: null });
    expect(parent.skills).to.deep.equal([]);
    expect(states(parent.pin)).to.deep.equal([[NOTES, 'unavailable', 'not_visible', null]]);
    expect(child.pin!.scope).to.deep.equal({ agentKey: AGENT, projectId: null });
    expect(states(child.pin)).to.deep.equal([[NOTES, 'unavailable', 'not_visible', null]]);
  });

  for (const vector of VECTOR.cases) {
    it(`CPD10-61 (${vector.id}): the child's spec is the bytes the shared vector holds for the worker`, async () => {
      const { child } = await tree(vector.owner);
      // Run ids and times are this admission's own; nothing the worker assembles from reads them.
      const spec = JSON.parse(JSON.stringify(child.spec)) as { budgets: { calendarDeadline: unknown } };
      spec.budgets.calendarDeadline = '2027-01-01T00:00:00.000Z';
      written.push({ ...vector, spec });
      if (process.env.WRITE_CHILD_OWNER_VECTORS !== '1') expect(spec).to.deep.equal(vector.spec);
    });
  }

  after(() => {
    if (process.env.WRITE_CHILD_OWNER_VECTORS === '1' && written.length === VECTOR.cases.length) {
      writeFileSync(VECTOR_PATH, `${JSON.stringify({ ...VECTOR, cases: written }, null, 2)}\n`);
    }
  });
});

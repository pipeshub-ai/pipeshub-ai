/**
 * SH-2, SH-3 at admission (CPD-7): the agent authority's answer passes
 * through the control plane's shadow unchanged, on every path that asks it;
 * the shadow compares what the definition grants with the effective set.
 */
import { expect } from 'chai';
import { readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { CanonicalEffectiveSetResolver, EffectiveSetShadow, type ShadowDimension } from '../../../src/modules/control-plane';
import type { EffectiveSet } from '../../../src/modules/control-plane/domain/objects';
import {
  ControlPlaneReviewedAgentAccess,
  legacyPinOf,
  onlyGranted,
  withoutHidden,
} from '../../../src/modules/agent-harness/application/control-plane-agent-access';
import type { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../../../src/modules/agent-harness/domain/rollout';
import { agentDefinition } from '../agent-harness/support/agent-definition';
import { authorityWorld } from './support/authority-world';
import { aBinding, aGrant, anEntry, anInstallation, bindingId, directTx, instId, ORG_A } from './support/cp-objects';
import { InMemoryGrantStore, InMemoryInstallationStore, snapshotsOver } from './support/in-memory-stores';
import { RecordingLog } from './support/in-memory';

const definition = (overrides: Partial<AgentRunDefinition> = {}): AgentRunDefinition => ({
  agentKey: 'a7',
  definitionVersion: 1,
  models: [],
  defaultReasoningEffort: null,
  toolsets: [
    { name: 'jira', instanceId: 'jira-1', tools: [{ name: 'search_issues', fullName: 'jira.search_issues' }] },
    { name: 'calculator', instanceId: null, tools: [{ name: 'add', fullName: 'calculator.add' }] },
  ],
  mcpServers: [{ instanceId: instId(1), name: 'Linear', typeId: 'linear', tools: [{ name: 'list_issues', fullName: 'mcp.list_issues' }] }],
  knowledge: [],
  skills: [{ name: 'notes', version: '1.0.0' }],
  webSearch: null,
  owner: 'u1',
  ...overrides,
});

async function world(mode: 'off' | 'shadow' | 'on', options: { failing?: boolean } = {}) {
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const counts: ShadowDimension[] = [];
  const tx = directTx(ORG_A);
  await installations.create(tx, anInstallation());
  await installations.create(tx, anInstallation({ id: 'jira-1', kind: 'toolset', item: 'toolset:pipeshub/jira@1', approved: { descriptorSetDigest: anInstallation().approved!.descriptorSetDigest, tools: { search_issues: anInstallation().approved!.descriptorSetDigest }, approvalRef: null, at: 1, by: 'system' } }));
  await installations.create(tx, anInstallation({ id: 'skill_notes', kind: 'skill', item: 'skill:user/notes@1.0.0', owner: 'user:u1', visibility: 'owner', config: { name: 'notes' }, approved: { descriptorSetDigest: anInstallation().approved!.descriptorSetDigest, tools: { notes: anInstallation().approved!.descriptorSetDigest }, approvalRef: null, at: 1, by: 'system' } }));
  await installations.create(tx, anInstallation({ id: instId(2), owner: 'user:u9', visibility: 'owner' }));
  await grants.put(tx, aGrant({ entries: [anEntry('jira-1'), anEntry(instId(1), ['list_issues']), anEntry(instId(2)), anEntry('skill_notes')] }), 0);
  if (options.failing === true) installations.listForOrg = () => Promise.reject(new Error('mongo down'));
  const shadow = new EffectiveSetShadow({
    mode,
    snapshots: snapshotsOver(installations, grants),
    resolver: new CanonicalEffectiveSetResolver(),
    counter: { inc: (dimension) => counts.push(dimension) },
    log: new RecordingLog(),
  });
  return { shadow, counts, installations };
}

describe('CPD-7 the agent authority under the control-plane shadow', () => {
  const request = { orgId: ORG_A, actorId: 'u3', agentKey: 'a7', authorization: 'Bearer x' };

  it('legacyPinOf names instance-bound toolset tools, MCP tools and skills; a platform toolset is not a catalog item', () => {
    expect(legacyPinOf(definition())).to.deep.equal({
      toolsetTools: ['jira@jira-1/search_issues'],
      mcpTools: [`mcp@${instId(1)}/list_issues`],
      skills: ['notes'],
    });
  });

  it("SH-2: an agreeing definition is counted nowhere and returned unchanged, the owner's skill shared", async () => {
    const { shadow, counts } = await world('shadow');
    const answer = definition();
    const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow);
    expect(await access.authorizeRun(request)).to.equal(answer);
    await access.idle();
    expect(counts).to.deep.equal([]);
  });

  it('SH-2: a definition granting what the control plane does not is counted, and still returned unchanged', async () => {
    const { shadow, counts } = await world('shadow');
    const answer = definition({ owner: undefined });
    const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow);
    expect(await access.authorizeRun(request)).to.equal(answer);
    await access.idle();
    expect(counts).to.deep.equal(['visibility']);
  });

  it('SH-2: the answer does not wait for the comparison: a control plane that never answers delays nothing', async () => {
    const { shadow, counts, installations } = await world('shadow');
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const list = installations.listForOrg.bind(installations);
    installations.listForOrg = async (...args) => {
      await held;
      return list(...args);
    };
    const answer = definition({ owner: undefined });
    const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow);
    expect(await access.authorizeRun(request)).to.equal(answer);
    expect(counts).to.deep.equal([]);
    release();
    await access.idle();
    expect(counts).to.deep.equal(['visibility']);
  });

  it('SH-3: off asks the control plane nothing; a denial passes through as null', async () => {
    const { shadow, installations } = await world('off');
    const before = installations.queries;
    const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => definition() }, shadow);
    await access.authorizeRun(request);
    expect(installations.queries).to.equal(before);
    const denied = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => null }, (await world('shadow')).shadow);
    expect(await denied.authorizeRun(request)).to.equal(null);
  });

  const hiddenServer = { instanceId: instId(2), name: 'Theirs', typeId: null, tools: [{ name: 'list_issues', fullName: 'mcp.list_issues' }] };

  it('VIS-1: with the control plane on, an MCP server the actor may not see is taken out, the rest kept', async () => {
    const { shadow } = await world('on');
    const answer = definition({ mcpServers: [...definition().mcpServers, hiddenServer] });
    const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow);
    const reviewed = (await access.authorizeRun(request))!;
    expect(reviewed.mcpServers.map((server) => server.instanceId)).to.deep.equal([instId(1)]);
    expect(reviewed.toolsets).to.deep.equal(answer.toolsets);
    expect(reviewed.skills).to.deep.equal(answer.skills);
  });

  it("VIS-1: an author's skill stays for a colleague running the author's agent, and goes when the owner is unknown", async () => {
    const { shadow } = await world('on');
    const shared = await new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => definition() }, shadow).authorizeRun(request);
    expect(shared!.skills.map((skill) => skill.name)).to.deep.equal(['notes']);
    const unknownOwner = await new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => definition({ owner: undefined }) }, shadow).authorizeRun(request);
    expect(unknownOwner!.skills).to.deep.equal([]);
  });

  it('VIS-1: in shadow the same definition is returned whole', async () => {
    const { shadow } = await world('shadow');
    const answer = definition({ mcpServers: [...definition().mcpServers, hiddenServer] });
    expect(await new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow).authorizeRun(request)).to.equal(answer);
  });

  it('VIS-1: with the control plane on and unable to answer, admission fails closed', async () => {
    const { shadow } = await world('on', { failing: true });
    const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => definition() }, shadow);
    const refused = await access.authorizeRun(request).catch((error: unknown) => error);
    expect((refused as Error).name).to.equal('HarnessUnavailableError');
  });

  describe('the system assistant (CPD-7 open item 3: no grant is migrated for it)', () => {
    const assistantRequest = { ...request, agentKey: ASSISTANT_AGENT_DEFINITION_KEY };
    const assistant = (): AgentRunDefinition => definition({ agentKey: ASSISTANT_AGENT_DEFINITION_KEY, owner: undefined });

    it('FU2-1: its MCP servers are not compared with a grant that does not exist, so no mismatch is counted', async () => {
      const { shadow, counts, installations } = await world('shadow');
      const before = installations.queries;
      const answer = assistant();
      const access = new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow);
      expect(await access.authorizeRun(assistantRequest)).to.equal(answer);
      await access.idle();
      expect(counts).to.deep.equal([]);
      expect(installations.queries).to.equal(before);
    });

    it('FU2-1: under on it is returned whole and does not wait on the control plane', async () => {
      const { shadow } = await world('on', { failing: true });
      const answer = assistant();
      expect(await new ControlPlaneReviewedAgentAccess({ authorizeRun: async () => answer }, shadow).authorizeRun(assistantRequest)).to.equal(answer);
    });
  });

  it('withoutHidden keeps platform toolsets (no instance) and drops by instance and skill installation', () => {
    const narrowed = withoutHidden(definition(), new Set(['jira-1', 'skill_notes']));
    expect(narrowed.toolsets.map((t) => t.name)).to.deep.equal(['calculator']);
    expect(narrowed.skills).to.deep.equal([]);
    expect(narrowed.mcpServers).to.have.length(1);
  });
});

/**
 * CPD-10 R3 (CPD10-53..55): the shared table `effective-set-authority.json`,
 * the admission side. Each case's world is built in the control plane's
 * stores, its definition admitted under `on` through a real admission and
 * claim, and the spec the claim resolves and the pin beside it are the
 * vector's. Python reads the same file for the worker side.
 */
interface AuthorityVector {
  definition: {
    toolsets: Array<{ name: string; instanceId: string | null; tools: string[] }>;
    mcpServers: Array<{ instanceId: string; tools: string[] }>;
    skills: string[];
    knowledge: AgentRunDefinition['knowledge'];
  };
  cases: Array<{
    id: string;
    migrated: boolean;
    world: { granted: string[]; bound: string[] };
    pin: { rollout: boolean | null; effectiveSet: EffectiveSet } | null;
    expected: { toolIds: string[]; mcpInstances: string[]; skills: string[] };
  }>;
}

const AUTHORITY_PATH = join(__dirname, '../../../../../../contracts/control-plane/vectors/effective-set-authority.json');
const AUTHORITY = JSON.parse(readFileSync(AUTHORITY_PATH, 'utf8')) as AuthorityVector;
const ROW = 'rollout.CONTROL_PLANE_CAPABILITIES';

const tool = (owner: string, name: string) => ({ name, fullName: `${owner}.${name}` });
const vectorDefinition = (agentKey: string): AgentRunDefinition =>
  agentDefinition({
    agentKey,
    owner: 'u1',
    toolsets: AUTHORITY.definition.toolsets.map((toolset) => ({ ...toolset, tools: toolset.tools.map((name) => tool(toolset.name, name)) })),
    mcpServers: AUTHORITY.definition.mcpServers.map((server) => ({
      instanceId: server.instanceId,
      name: 'Linear',
      typeId: 'linear',
      tools: server.tools.map((name) => tool(`mcp_${server.instanceId}`, name)),
    })),
    skills: AUTHORITY.definition.skills.map((name) => ({ name, version: '1.0.0' })),
    knowledge: AUTHORITY.definition.knowledge,
    webSearch: null,
  });

async function authorityCase(mode: 'shadow' | 'on', vector: AuthorityVector['cases'][number]) {
  const w = authorityWorld(mode, vectorDefinition);
  const approved = anInstallation().approved!;
  await w.write(async (tx) => {
    await w.installations.create(
      tx,
      anInstallation({ id: 'jira-1', kind: 'toolset', item: 'toolset:pipeshub/jira@1', credentialMode: 'per_user', approved: { ...approved, tools: { search_issues: approved.tools.list_issues!, create_issue: approved.tools.create_issue! } } }),
    );
    await w.installations.create(tx, anInstallation({ id: instId(1), credentialMode: 'per_user' }));
    await w.installations.create(tx, anInstallation({ id: instId(2) }));
    await w.installations.create(tx, anInstallation({ id: 'skill_notes', kind: 'skill', item: 'skill:pipeshub/notes@1.0.0', config: { name: 'notes' }, approved: { ...approved, tools: { notes: approved.descriptorSetDigest } } }));
    await w.grants.put(tx, aGrant({ entries: vector.world.granted.map((installation) => anEntry(installation)) }), 0);
    for (const [index, installation] of vector.world.bound.entries()) {
      await w.bindings.upsert(tx, aBinding({ id: bindingId(index + 1), installation, owner: 'user:u3' }), null);
    }
    if (vector.migrated) await w.ledger.markMigrated(tx);
  });
  return { w, admitted: await w.admit() };
}

describe('CPD-10 R3: the authority for a run\'s items is the org\'s migrated row (effective-set-authority.json)', () => {
  const written: AuthorityVector['cases'] = [];

  for (const vector of AUTHORITY.cases) {
    it(`${vector.id}: under on, the admitted spec grants exactly the vector's items and the stored pin is the vector's`, async () => {
      const { admitted } = await authorityCase('on', vector);
      expect([...admitted.toolIds].sort()).to.deep.equal(vector.expected.toolIds);
      expect([...admitted.mcpInstances].sort()).to.deep.equal(vector.expected.mcpInstances);
      expect(admitted.skills).to.deep.equal(vector.expected.skills);
      expect(admitted.knowledge).to.deep.include({ apps: ['app-drive'], kb: ['kb-1'] });
      const pin = {
        rollout: (admitted.pin!.effectiveConfig.dimensions[ROW]?.value as boolean | undefined) ?? null,
        effectiveSet: admitted.pin!.effectiveSet,
      };
      written.push({ ...vector, pin });
      if (process.env.WRITE_AUTHORITY_VECTORS !== '1') expect(pin).to.deep.equal(vector.pin);
      expect(pin.rollout).to.equal(vector.migrated ? true : null);
    });

    it(`${vector.id}: in shadow the definition is admitted whole, whatever the row says`, async () => {
      const { admitted } = await authorityCase('shadow', vector);
      expect(admitted.toolIds).to.have.length(5);
      expect([admitted.mcpInstances.length, admitted.skills]).to.deep.equal([2, ['notes']]);
    });
  }

  after(() => {
    if (process.env.WRITE_AUTHORITY_VECTORS === '1' && written.length === AUTHORITY.cases.length) {
      writeFileSync(AUTHORITY_PATH, `${JSON.stringify({ ...AUTHORITY, cases: written }, null, 2)}\n`);
    }
  });

  it('CPD10-53: revoking a grant after the org is migrated takes its items out of the next admission, and unrecording the org brings the definition back', async () => {
    const { w } = await authorityCase('on', AUTHORITY.cases.find((vector) => vector.id === 'CPD10-55')!);
    expect((await w.admit()).toolIds).to.include('jira@jira-1/search_issues');
    await w.write((tx) => w.grants.put(tx, aGrant({ entries: [], revision: 2 }), 1));
    expect((await w.admit()).toolIds).to.deep.equal(['calculator@default/add']);
    await w.write((tx) => w.ledger.unmarkMigrated(tx));
    expect((await w.admit()).toolIds).to.have.length(5);
  });

  it('CPD10-55: a ready item is narrowed to the tools the set holds, and a toolset left with none goes', () => {
    const narrowed = onlyGranted(vectorDefinition('a7'), new Map([['jira-1', new Set(['search_issues'])], [instId(1), new Set<string>()]]));
    expect(narrowed.toolsets.map((toolset) => [toolset.name, toolset.tools.map((t) => t.name)])).to.deep.equal([
      ['jira', ['search_issues']],
      ['calculator', ['add']],
    ]);
    expect([narrowed.mcpServers, narrowed.skills]).to.deep.equal([[], []]);
    expect(narrowed.knowledge).to.deep.equal(AUTHORITY.definition.knowledge);
  });
});

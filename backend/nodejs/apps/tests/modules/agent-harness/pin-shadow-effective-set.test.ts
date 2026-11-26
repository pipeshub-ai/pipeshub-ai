/**
 * PSC-C5 (CPD-6 with CPD-7): the shadow gate's replay corpus, the effective
 * set included. What the corpus's agent grants today (its definition) is
 * migrated as CPD-7's capability plan writes it; the production snapshot
 * loader and resolver then answer for every agent admission of the routing
 * vectors and the replay scenarios, and the effective-set shadow must count
 * nothing. A planted difference shows the row can see one.
 */
import { expect } from 'chai';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { legacyPinOf } from '../../../src/modules/agent-harness/application/control-plane-agent-access';
import type { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { canonicalDigest } from '../../../src/libs/utils/canonical-json';
import {
  CanonicalEffectiveSetResolver,
  EffectiveSetShadow,
  releaseItemsOf,
  ResolvedSnapshotCache,
  type ShadowDimension,
} from '../../../src/modules/control-plane';
import { planCapabilities } from '../../../src/modules/control-plane/application/capability-plan';
import { digest, directTx } from '../control-plane/support/cp-objects';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { RecordingLog } from '../control-plane/support/in-memory';
import { InMemoryGrantStore, InMemoryInstallationStore } from '../control-plane/support/in-memory-stores';
import { agentDefinition } from './support/agent-definition';
import { inputOf, ROUTING_VECTORS } from './support/routing-vectors';

const REPLAY_SCENARIOS = join(__dirname, '../../../../../python/tests/harness_replay/scenarios');
const ORG = 'org-corpus';
const OWNER = 'u-owner';

/** The legacy facts the graph would hold for a definition: its instances, its edges and its skills. */
const legacyFactsOf = (definition: AgentRunDefinition) => ({
  toolsetInstances: definition.toolsets
    .filter((toolset) => toolset.instanceId !== null)
    .map((toolset) => ({
      instanceId: toolset.instanceId as string,
      instanceName: toolset.name,
      toolsetType: toolset.name,
      // No credential: CPD-8 binds them, and the shadow sets a missing one aside (D7), which would hide every tool.
      authType: 'NONE',
      createdBy: OWNER,
      updatedAt: 1,
    })),
  mcpInstances: definition.mcpServers.map((server) => ({
    instanceId: server.instanceId,
    name: server.name,
    typeId: server.typeId,
    authMode: 'none',
    createdBy: OWNER,
    updatedAt: 1,
    manifest: {
      tools: Object.fromEntries(server.tools.map((tool) => [tool.name, { digest: digest('a'), effect: 'read' as const }])),
      approvedBy: OWNER,
      approvedAt: 1,
    },
  })),
  agents: [
    {
      agentKey: definition.agentKey,
      createdBy: OWNER,
      toolsets: definition.toolsets
        .filter((toolset) => toolset.instanceId !== null)
        .map((toolset, index) => ({
          nodeKey: `n${String(index)}`,
          instanceId: toolset.instanceId,
          toolsetType: toolset.name,
          tools: toolset.tools.map((tool) => tool.name),
        })),
      mcpServers: definition.mcpServers.map((server) => ({ instanceId: server.instanceId, tools: server.tools.map((tool) => tool.name) })),
      skills: definition.skills.map((skill) => ({ name: skill.name, version: skill.version ?? '1', builtin: true, createdBy: null, bundleDigest: digest('c') })),
    },
  ],
});

async function world(definition: AgentRunDefinition) {
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const plan = planCapabilities({ orgId: ORG, ...legacyFactsOf(definition), release: releaseItemsOf, digest: canonicalDigest });
  const tx = directTx(ORG);
  for (const installation of plan.installations) await installations.create(tx, installation);
  for (const grant of plan.grants) await grants.put(tx, grant, 0);
  const heads = new InMemoryHeadRevisions();
  const counts: ShadowDimension[] = [];
  const shadow = new EffectiveSetShadow({
    mode: 'shadow',
    snapshots: new ResolvedSnapshotCache({ load: policySnapshotLoader({ heads, capabilities: { installations, grants } }), heads }),
    resolver: new CanonicalEffectiveSetResolver(),
    counter: { inc: (dimension) => counts.push(dimension) },
    log: new RecordingLog(),
  });
  const set = async (actorId: string) =>
    new CanonicalEffectiveSetResolver().resolve(
      { orgId: ORG, actorId, agentKey: definition.agentKey, agentOwner: definition.owner ?? null, projectId: null, chatMode: 'agent', surface: 'web', request: { schemaVersion: 1, scope: { kind: 'request' } } },
      { schemaVersion: 1, dimensions: {} },
      await policySnapshotLoader({ heads, capabilities: { installations, grants } })(ORG, definition.agentKey, actorId),
    );
  return { plan, shadow, counts, set };
}

/** Every agent admission of the corpus: the named routing vectors and the replay scenarios an agent answers. */
function corpusAgents(): Array<{ label: string; definition: AgentRunDefinition }> {
  const rows: Array<{ label: string; definition: AgentRunDefinition }> = [];
  for (const vector of ROUTING_VECTORS.vectors) {
    const input = inputOf(vector.input);
    if (input.kind !== 'agent') continue;
    const engine = input.engine === 'none' ? null : input.engine;
    rows.push({
      label: vector.id,
      definition: agentDefinition({ agentKey: 'agent-1', owner: OWNER, ...(engine !== null ? { engineProfile: { engines: [engine] } } : {}) }),
    });
  }
  for (const file of readdirSync(REPLAY_SCENARIOS).filter((name) => name.endsWith('.yaml'))) {
    if (/^route:\s*chat\b/m.test(readFileSync(join(REPLAY_SCENARIOS, file), 'utf8'))) continue;
    rows.push({ label: file, definition: agentDefinition({ agentKey: 'agent-1', owner: OWNER }) });
  }
  return rows;
}

describe('PSC-C5: the effective-set shadow over the replay corpus counts no mismatch', () => {
  it("the corpus's agent migrates whole: nothing is held", async () => {
    const { plan, set } = await world(agentDefinition({ agentKey: 'agent-1', owner: OWNER }));
    expect(plan.holds).to.deep.equal([]);
    expect(plan.installations.length).to.equal(3);
    // Nothing is set aside as a missing credential, so every tool of the corpus is compared.
    const resolved = await set('u-colleague');
    if ('code' in resolved) return expect.fail(resolved.code);
    expect(resolved.items.map((item) => [item.kind, item.state, item.tools.length]).sort()).to.deep.equal([
      ['mcp', 'ready', 1],
      ['skill', 'ready', 1],
      ['toolset', 'ready', 2],
    ]);
  });

  it('every agent admission of the routing vectors and the replay scenarios, for the owner and for a colleague', async () => {
    const rows = corpusAgents();
    expect(rows.length).to.be.greaterThan(10);
    const found: Array<[string, string, ShadowDimension]> = [];
    for (const { label, definition } of rows) {
      const { shadow, counts } = await world(definition);
      for (const actorId of [OWNER, 'u-colleague']) {
        await shadow.observe({ orgId: ORG, actorId, agentKey: definition.agentKey, agentOwner: definition.owner ?? null }, legacyPinOf(definition));
        for (const dimension of counts.splice(0)) found.push([label, actorId, dimension]);
      }
    }
    expect(found).to.deep.equal([]);
  });

  it('the row sees a planted difference: a tool the definition grants and the migrated grant does not', async () => {
    const definition = agentDefinition({ agentKey: 'agent-1', owner: OWNER });
    const { shadow, counts } = await world(definition);
    const legacy = legacyPinOf(definition);
    await shadow.observe(
      { orgId: ORG, actorId: OWNER, agentKey: 'agent-1', agentOwner: OWNER },
      { ...legacy, mcpTools: [...legacy.mcpTools, `mcp@${definition.mcpServers[0]!.instanceId}/never_granted`] },
    );
    expect(counts).to.deep.equal(['mcp']);
  });
});

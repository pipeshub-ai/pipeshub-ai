/**
 * C4.08 (AD-09..AD-12): an agent and its engine profile as one versioned,
 * schema-checked document; an import is a new agent of the importer's, in
 * the importer's org, landing whole or not at all. Where no agent layer is
 * kept, an agent travels without policy sections. The query service's half
 * is a fake (its routes are tested in Python).
 */
import { SECRET_MASK } from '../../../src/modules/agent-harness/contracts/vectors/token-patterns';
import { VECTOR_EXAMPLES } from './support/canary-scan';
import { expect } from 'chai';
import {
  AgentConfigRefusedError,
  AgentConfigTransfer,
  type AgentDefinitions,
  type ExportedDefinition,
  type ImportedDefinition,
} from '../../../src/modules/agent-harness/application/agent-config-transfer';
import {
  type AgentConfigDocument,
  type PortableAgent,
} from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub';
import {
  readPolicyLayer,
  type Authorizer,
  type PolicyLayer,
} from '../../../src/modules/control-plane';
import { auditedControlPlane } from '../control-plane/support/in-memory';
import { InMemoryPolicyStore } from '../control-plane/support/in-memory-stores';
import { without } from './support/without';

const ORG_A = 'org-a';
const ORG_B = 'org-b';
const ALICE = { orgId: ORG_A, userId: 'u-alice' };
const MALLORY = { orgId: ORG_B, userId: 'u-mallory' };

const AGENT: PortableAgent = {
  name: 'Support triage',
  description: 'Sorts new tickets',
  startMessage: 'Hi!',
  systemPrompt: 'You triage support tickets.',
  instructions: null,
  tags: ['support'],
  sendUserContext: true,
  webSearch: null,
  defaultReasoningEffort: 'low',
  engineProfile: null,
  models: [{ provider: 'openai', modelName: 'gpt-x' }],
  roleModels: {},
  toolsets: [],
  mcpServers: [],
  knowledge: [],
  skills: [],
  gates: null,
};

/** The query service as Node sees it: each org's agents, created as the caller. */
class FakeDefinitions implements AgentDefinitions<string> {
  readonly agents = new Map<
    string,
    { orgId: string; owner: string; agent: PortableAgent }
  >();
  readonly imported: PortableAgent[] = [];
  readonly removed: string[] = [];
  private next = 0;

  constructor(
    private readonly credentials: Map<
      string,
      { orgId: string; userId: string }
    >,
  ) {}

  async export(
    credential: string,
    agentKey: string,
  ): Promise<ExportedDefinition> {
    const who = this.credentials.get(credential);
    const found = this.agents.get(agentKey);
    if (who === undefined || found === undefined || found.orgId !== who.orgId) {
      throw Object.assign(
        new Error("Agent not found or you don't have access to it"),
        { status: 404 },
      );
    }
    return {
      agent: found.agent,
      source: { agentVersion: 1, digest: null },
      excluded: ['sharing'],
    };
  }

  async import(
    credential: string,
    agent: PortableAgent,
  ): Promise<ImportedDefinition> {
    const who = this.credentials.get(credential)!;
    this.imported.push(agent);
    this.next += 1;
    const agentKey = `${who.orgId}-agent-${String(this.next)}`;
    this.agents.set(agentKey, { orgId: who.orgId, owner: who.userId, agent });
    return {
      agentKey,
      name: agent.name,
      bound: [],
      rebind: [],
      warnings: null,
    };
  }

  async remove(_credential: string, agentKey: string): Promise<void> {
    this.removed.push(agentKey);
    this.agents.delete(agentKey);
  }
}

/** Members read; an agent's editors write its layer (RoleAuthorizer's agent-editor rule), here its owner. */
class OwnerAuthorizer implements Authorizer {
  denyWrites = false;
  constructor(private readonly definitions: FakeDefinitions) {}

  async check(
    actor: { orgId: string; userId: string },
    action: string,
    resource: { orgId: string; owner?: string },
  ) {
    if (resource.orgId !== actor.orgId)
      return { allow: false as const, reason: 'wrong_org' };
    if (action === 'policy.read') return { allow: true as const };
    const agent = this.definitions.agents.get(
      String(resource.owner).slice('agent:'.length),
    );
    return !this.denyWrites && agent?.owner === actor.userId
      ? { allow: true as const }
      : { allow: false as const, reason: 'not_authorized' };
  }
}

/** Parses as the Mongo store does (`readPolicyLayer`), so a layer the store would refuse fails here too. */
const strictLayer = (raw: unknown): PolicyLayer => {
  const layer = readPolicyLayer(raw);
  if (layer === null) throw new Error('not a policy layer');
  return layer;
};

function world() {
  const store = new InMemoryPolicyStore(strictLayer);
  const audit = auditedControlPlane();
  const credentials = new Map([
    ['bearer-alice', ALICE],
    ['bearer-mallory', MALLORY],
  ]);
  const definitions = new FakeDefinitions(credentials);
  const authorizer = new OwnerAuthorizer(definitions);
  const enginesChecked: string[][] = [];
  let refuseEngines = false;
  const transfer = new AgentConfigTransfer<string>({
    definitions,
    policy: null,
    authorizer,
    assertEnginesUsable: async (_orgId, engines) => {
      enginesChecked.push([...engines]);
      if (refuseEngines) throw new Error('engine not usable');
    },
    now: () => 1_793_404_800_000,
  });
  const seed = async (
    orgId: string,
    scope: string,
    layer: Omit<PolicyLayer, 'schemaVersion' | 'scope'>,
  ) => {
    const [kind, id] = scope.split(':') as [string, string];
    await audit.transaction.run({ orgId, actor: 'system' }, async (tx) => {
      await store.writeMany(tx, [
        {
          scope,
          layer: {
            schemaVersion: 1,
            scope: { kind, id },
            ...layer,
          } as PolicyLayer,
          expectedRevision: 0,
        },
      ]);
      return {
        result: null,
        change: {
          scope,
          object: { type: 'policy-layer', id: scope },
          revisionFrom: 0,
          revisionTo: 1,
          changeDigest: 'sha256:' + '0'.repeat(64),
          beforeDigest: 'sha256:' + '0'.repeat(64),
          changed: ['seed'],
          narrowing: false,
        },
      };
    });
  };
  return {
    store,
    audit,
    definitions,
    authorizer,
    transfer,
    seed,
    enginesChecked,
    refuseEngines: () => {
      refuseEngines = true;
    },
  };
}

async function refusal(promise: Promise<unknown>): Promise<Error> {
  try {
    await promise;
  } catch (error) {
    return error as Error;
  }
  throw new Error('expected a refusal');
}

const document = (
  overrides: Partial<AgentConfigDocument> = {},
): AgentConfigDocument => ({
  format: 'pipeshub.agent-config',
  schemaVersion: 2,
  exportedAt: 1,
  source: { agentVersion: 1, digest: null },
  agent: AGENT,
  policy: {},
  excluded: [],
  ...overrides,
});

describe('C4.08 agent config export and import (AD-09..AD-12)', () => {
  it('AD-09: who cannot read the agent gets the query service refusal, and its policy is never read', async () => {
    const w = world();
    w.definitions.agents.set('a1', {
      orgId: ORG_A,
      owner: ALICE.userId,
      agent: AGENT,
    });
    await w.seed(ORG_A, 'agent:a1', { budgets: { maxTurns: 3 } });
    const queries = w.store.queries;

    const error = await refusal(
      w.transfer.export(MALLORY, 'bearer-mallory', 'a1'),
    );

    expect((error as { status?: number }).status).to.equal(404);
    expect(w.store.queries).to.equal(queries);
  });

  it('AD-09: without a policy store the export carries no policy', async () => {
    const w = world();
    w.definitions.agents.set('a1', {
      orgId: ORG_A,
      owner: ALICE.userId,
      agent: AGENT,
    });

    expect(
      (await w.transfer.export(ALICE, 'bearer-alice', 'a1')).policy,
    ).to.deep.equal({});
  });

  it('AD-10: ids and owners in the document are refused before anything is written', async () => {
    const w = world();
    for (const raw of [
      { ...document(), orgId: ORG_A },
      { ...document(), agent: { ...AGENT, createdBy: 'u-alice' } },
      {
        ...document(),
        agent: {
          ...AGENT,
          models: [{ modelKey: 'm-1', provider: 'openai', modelName: 'gpt-x' }],
        },
      },
      { ...document(), policy: { triggers: {} } },
      { ...document(), policy: { approvals: { writes: 'allow' } } },
    ]) {
      const error = await refusal(
        w.transfer.import(MALLORY, 'bearer-mallory', raw),
      );
      expect(error).to.be.instanceOf(AgentConfigRefusedError);
      expect((error as AgentConfigRefusedError).code).to.equal(
        'AGENT_CONFIG_INVALID',
      );
    }
    expect(w.definitions.imported).to.deep.equal([]);
  });

  it('AD-11: a schema version this build does not know is refused by name', async () => {
    const w = world();

    const error = await refusal(
      w.transfer.import(MALLORY, 'bearer-mallory', {
        ...document(),
        schemaVersion: 3,
      }),
    );

    expect(error.message).to.equal(
      'This export is schema version 3; this PipesHub reads versions 1 and 2',
    );
    expect(w.definitions.imported).to.deep.equal([]);
  });

  it('AD-11: where no agent layer is kept, the agent imports without its policy sections and the result names each', async () => {
    const w = world();

    const imported = await w.transfer.import(
      MALLORY,
      'bearer-mallory',
      document({ policy: { budgets: { maxTurns: 3 }, models: { primary: 'openai:gpt-x' } } }),
    );

    expect(imported.policy).to.deep.equal([]);
    expect(imported.notImported).to.deep.equal([
      { section: 'budgets', reason: 'not_offered' },
      { section: 'models', reason: 'not_offered' },
    ]);
    expect(w.definitions.imported).to.have.length(1);
  });


  it('AD-11: an engine the org cannot use stops the import before the agent is created', async () => {
    const w = world();
    w.refuseEngines();

    const error = await refusal(
      w.transfer.import(
        MALLORY,
        'bearer-mallory',
        document({
          agent: {
            ...AGENT,
            engineProfile: { engines: ['claude-code'], mode: 'plan' },
          },
        }),
      ),
    );

    expect(error.message).to.equal('engine not usable');
    expect(w.enginesChecked).to.deep.equal([['claude-code']]);
    expect(w.definitions.imported).to.deep.equal([]);
  });

  it('AD-10: an import that leaves nothing out says so', async () => {
    const result = await world().transfer.import(MALLORY, 'bearer-mallory', document());
    expect(result.notImported).to.deep.equal([]);
  });

  it('SEC-CR-07, AD-09, AD-10 (round 9): a secret pasted into an agent neither leaves in its export nor lands with an import', async () => {
    const secret = VECTOR_EXAMPLES[0]!.secret;
    const w = world();
    w.definitions.agents.set('a1', {
      orgId: ORG_A,
      owner: ALICE.userId,
      agent: { ...AGENT, systemPrompt: `You triage tickets. Use key ${secret} for the API.`, tags: ['support', secret] },
    });
    const exported = await w.transfer.export(ALICE, 'bearer-alice', 'a1');
    expect(JSON.stringify(exported)).to.not.include(secret);
    expect(exported.agent.systemPrompt).to.include(SECRET_MASK).and.include('You triage tickets.');

    const carried = document({ agent: { ...AGENT, startMessage: `Hi! token: ${secret}` } });
    const result = await w.transfer.import(MALLORY, 'bearer-mallory', JSON.parse(JSON.stringify(carried)));
    expect(JSON.stringify(w.definitions.imported)).to.not.include(secret);
    expect(w.definitions.agents.get(result.agentKey)?.agent.startMessage).to.include(SECRET_MASK);
  });
});

const GATES = {
  planApproval: 'required' as const,
  verify: {
    checks: [{ kind: 'file' as const, path: 'report.md' }],
    onFail: 'flag' as const,
  },
};

describe('WP-56: an agent\'s own gates travel with its export (C4.08 x WP-51)', () => {
  it('WP56-GATES-4: the export is schema version 2 and carries the gates; nothing names them as left out', async () => {
    const w = world();
    w.definitions.agents.set('a1', {
      orgId: ORG_A,
      owner: ALICE.userId,
      agent: { ...AGENT, gates: GATES },
    });

    const exported = await w.transfer.export(ALICE, 'bearer-alice', 'a1');

    expect(exported.schemaVersion).to.equal(2);
    expect(exported.agent.gates).to.deep.equal(GATES);
    expect(exported.excluded).to.not.include('gates');
  });

  it('WP56-GATES-4: an import hands the gates to the new agent, and a round trip keeps them', async () => {
    const w = world();
    w.definitions.agents.set('a1', {
      orgId: ORG_A,
      owner: ALICE.userId,
      agent: { ...AGENT, gates: GATES },
    });
    const first = await w.transfer.export(ALICE, 'bearer-alice', 'a1');

    const result = await w.transfer.import(MALLORY, 'bearer-mallory', first);
    const second = await w.transfer.export(MALLORY, 'bearer-mallory', result.agentKey);

    expect(w.definitions.imported[0]?.gates).to.deep.equal(GATES);
    expect(second.agent.gates).to.deep.equal(GATES);
  });

  it('WP56-GATES-5: a version 1 export (before gates travelled) still imports, as an agent with no gates', async () => {
    const w = world();
    const v1Agent = without(AGENT, 'gates');

    await w.transfer.import(MALLORY, 'bearer-mallory', {
      ...document(),
      schemaVersion: 1,
      agent: v1Agent,
      excluded: ['gates'],
    });

    expect(w.definitions.imported[0]?.gates).to.equal(null);
  });

  it('WP56-GATES-5: a version 2 export without gates, or a version 1 export carrying them, is refused', async () => {
    const w = world();
    const v1Agent = without(AGENT, 'gates');

    for (const raw of [
      { ...document(), agent: v1Agent },
      { ...document(), schemaVersion: 1, agent: { ...AGENT, gates: GATES } },
    ]) {
      const error = await refusal(w.transfer.import(MALLORY, 'bearer-mallory', raw));
      expect(error).to.be.instanceOf(AgentConfigRefusedError);
      expect((error as AgentConfigRefusedError).code).to.equal('AGENT_CONFIG_INVALID');
    }
    expect(w.definitions.imported).to.deep.equal([]);
  });

  it('WP56-GATES-2: gates that are not an agent\'s are refused in plain words before anything is created', async () => {
    const w = world();

    for (const gates of [
      { dryRun: true },
      { planApproval: 'always' },
      { verify: { checks: [], onFail: 'flag' } },
      { verify: { checks: [{ kind: 'file', path: '../secrets' }], onFail: 'flag' } },
    ]) {
      const error = await refusal(
        w.transfer.import(MALLORY, 'bearer-mallory', {
          ...document(),
          agent: { ...AGENT, gates },
        }),
      );
      expect((error as AgentConfigRefusedError).code).to.equal('AGENT_CONFIG_INVALID');
      expect((error as AgentConfigRefusedError).field).to.match(/^agent\.gates/);
      expect(error.message).to.match(/^This is not an agent export PipesHub can import \(agent\.gates/);
    }
    expect(w.definitions.imported).to.deep.equal([]);
  });
});

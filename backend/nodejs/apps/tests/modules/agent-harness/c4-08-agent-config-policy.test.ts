/**
 * C4.08 (AD-10, AD-11) where the deployment keeps agent layers: an import's policy sections are checked against the
 * layers above before the agent exists, written as the importer after, and an agent whose policy cannot land is
 * removed again; an export carries the layer's portable sections and names `approvals` when the layer withholds them.
 * The query service and the policy store are fakes that record what they were asked.
 */
import { expect } from 'chai';
import {
  AgentConfigRefusedError,
  AgentConfigTransfer,
  type AgentDefinitions,
  type ExportedDefinition,
  type ImportedDefinition,
} from '../../../src/modules/agent-harness/application/agent-config-transfer';
import type { AgentPolicySections, PortableSections } from '../../../src/modules/agent-harness/ports/agent-policy-sections';
import type { AgentConfigDocument, PortableAgent } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub';
import type { Authorizer } from '../../../src/modules/control-plane';

const ALICE = { orgId: 'org-a', userId: 'u-alice' };

const AGENT: PortableAgent = {
  name: 'Support triage',
  description: '',
  startMessage: '',
  systemPrompt: '',
  instructions: null,
  tags: [],
  sendUserContext: true,
  webSearch: null,
  defaultReasoningEffort: null,
  engineProfile: null,
  models: [],
  roleModels: {},
  toolsets: [],
  mcpServers: [],
  knowledge: [],
  skills: [],
  gates: null,
};

class Definitions implements AgentDefinitions<string> {
  readonly removed: string[] = [];

  async export(): Promise<ExportedDefinition> {
    return { agent: AGENT, source: { agentVersion: 3, digest: null }, excluded: ['sharing'] };
  }

  async import(): Promise<ImportedDefinition> {
    return { agentKey: 'agent-1', name: AGENT.name, bound: [], rebind: [], warnings: null };
  }

  async remove(_credential: string, agentKey: string): Promise<void> {
    this.removed.push(agentKey);
  }
}

class Policy implements AgentPolicySections {
  readonly checked: Array<[string, string, PortableSections]> = [];
  readonly created: Array<[string, string, PortableSections]> = [];
  failCreate = false;
  withheld: string[] = [];

  offers(section: string): boolean {
    return section === 'budgets';
  }

  async check(orgId: string, scope: string, sections: PortableSections): Promise<void> {
    this.checked.push([orgId, scope, sections]);
  }

  async portable(): Promise<{ sections: PortableSections; withheld: readonly string[] }> {
    return { sections: { budgets: { maxTurns: 4 } }, withheld: this.withheld };
  }

  async create(ctx: { orgId: string; actor: string }, scope: string, sections: PortableSections): Promise<number> {
    if (this.failCreate) throw new Error('layer write failed');
    this.created.push([`${ctx.orgId}/${ctx.actor}`, scope, sections]);
    return 1;
  }
}

class Allow implements Authorizer {
  readonly asked: string[] = [];
  deny = false;

  async check(_actor: { orgId: string; userId: string }, action: string) {
    this.asked.push(action);
    return this.deny ? { allow: false as const, reason: 'not_authorized' } : { allow: true as const };
  }
}

const doc = (policy: Record<string, unknown>): AgentConfigDocument => ({
  format: 'pipeshub.agent-config',
  schemaVersion: 2,
  exportedAt: 1,
  source: { agentVersion: 1, digest: null },
  agent: AGENT,
  policy,
  excluded: [],
});

function world(authorizer: Authorizer | null = new Allow()) {
  const definitions = new Definitions();
  const policy = new Policy();
  const transfer = new AgentConfigTransfer<string>({
    definitions,
    policy,
    authorizer,
    assertEnginesUsable: async () => undefined,
    now: () => 7,
  });
  return { definitions, policy, transfer };
}

async function refused(promise: Promise<unknown>): Promise<AgentConfigRefusedError> {
  try {
    await promise;
  } catch (error) {
    expect(error).to.be.instanceOf(AgentConfigRefusedError);
    return error as AgentConfigRefusedError;
  }
  throw new Error('expected a refusal');
}

describe('C4.08 with agent layers kept: policy sections move with the agent', () => {
  it('AD-11: offered sections are checked before the agent exists and written as the importer after; the rest are named', async () => {
    const authorizer = new Allow();
    const w = world(authorizer);
    const imported = await w.transfer.import(ALICE, 'bearer', doc({ budgets: { maxTurns: 3 }, models: { primary: 'openai:gpt-x' } }));
    expect(imported.policy).to.deep.equal(['budgets']);
    expect(imported.notImported).to.deep.equal([{ section: 'models', reason: 'not_offered' }]);
    expect(w.policy.checked).to.deep.equal([['org-a', 'agent:import-check', { budgets: { maxTurns: 3 } }]]);
    expect(w.policy.created).to.deep.equal([['org-a/user:u-alice', 'agent:agent-1', { budgets: { maxTurns: 3 } }]]);
    expect(authorizer.asked).to.deep.equal(['policy.write:budgets']);
    expect(w.definitions.removed).to.deep.equal([]);
  });

  it('AD-10: an importer who may not set the policy gets a refusal and the new agent is removed', async () => {
    const authorizer = new Allow();
    authorizer.deny = true;
    const w = world(authorizer);
    const error = await refused(w.transfer.import(ALICE, 'bearer', doc({ budgets: { maxTurns: 2 } })));
    expect(error.code).to.equal('AGENT_CONFIG_FORBIDDEN');
    expect(w.definitions.removed).to.deep.equal(['agent-1']);
    expect(w.policy.created).to.deep.equal([]);
  });

  it('AD-10: a policy write that fails removes the agent it was for, and the failure surfaces', async () => {
    const w = world();
    w.policy.failCreate = true;
    try {
      await w.transfer.import(ALICE, 'bearer', doc({ budgets: { maxTurns: 1 } }));
      expect.fail('a failed layer write must surface');
    } catch (error) {
      expect((error as Error).message).to.equal('layer write failed');
    }
    expect(w.definitions.removed).to.deep.equal(['agent-1']);
  });

  it('a deployment that cannot check who may write the policy refuses, and removes the agent', async () => {
    const w = world(null);
    const error = await refused(w.transfer.import(ALICE, 'bearer', doc({ budgets: { maxTurns: 1 } })));
    expect(error.code).to.equal('AGENT_CONFIG_POLICY_UNAVAILABLE');
    expect(w.definitions.removed).to.deep.equal(['agent-1']);
  });

  it("AD-09: an export carries the layer's portable sections, and names approvals when the layer withholds them", async () => {
    const w = world();
    w.policy.withheld = ['approvals'];
    const exported = await w.transfer.export(ALICE, 'bearer', 'agent-1');
    expect(exported.policy).to.deep.equal({ budgets: { maxTurns: 4 } });
    expect(exported.excluded).to.deep.equal(['approvals', 'sharing']);
    expect(exported.exportedAt).to.equal(7);
  });

  it('AD-09: an export whose policy may not be read is refused', async () => {
    const authorizer = new Allow();
    authorizer.deny = true;
    const error = await refused(world(authorizer).transfer.export(ALICE, 'bearer', 'agent-1'));
    expect(error.code).to.equal('AGENT_CONFIG_FORBIDDEN');
  });
});

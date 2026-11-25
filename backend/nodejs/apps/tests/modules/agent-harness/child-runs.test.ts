import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  ChildGrantRequest,
  childCompletedEvent,
  childFeatures,
  childGrantDigest,
  childGrants,
  childRunIdFor,
  childRunSpec,
  CHILD_MAX_DEPTH,
  MAX_CHILDREN_PER_RUN,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { DEEP_MAX_CHILDREN } from '../../../src/modules/agent-harness/domain/rollout';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import {
  PinnedRunSpec,
  pinRunSpec,
  resolveRunSpec,
  RUN_SPEC_BUDGETS,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { workerRun } from '../../../src/modules/agent-harness/domain/steering';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { agentDefinition } from './support/agent-definition';
import { goldenSpecSubject } from './support/golden-run-spec';

interface GrantCase {
  id: string;
  behaviour: string;
  depth: number;
  parent?: { sandboxProfile?: RunSpec['grants']['sandboxProfile']; egress?: string };
  request: ChildGrantRequest;
  expect: {
    refusal: { code: string; dimension: string; items: string[] } | null;
    grants?: Record<string, unknown>;
  };
}

const table = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/child-grants.json'),
    'utf8',
  ),
) as {
  maxDepth: number;
  parentSpec: RunSpec;
  cases: GrantCase[];
  childRunIds: Array<{
    orgId: string;
    parentRunId: string;
    invocationId: string;
    childRunId: string;
  }>;
};

const parentOf = (patch: GrantCase['parent']): RunSpec => {
  const spec = structuredClone(table.parentSpec);
  if (patch?.sandboxProfile !== undefined) {
    spec.grants.sandboxProfile = patch.sandboxProfile;
  }
  if (patch?.egress !== undefined) spec.grants.egress = patch.egress;
  return spec;
};

const DURABLE = capabilitySnapshot(
  { ...LEAST_CAPABILITIES, 'journal.durable': true },
  1,
);

const parentPin = (): PinnedRunSpec => {
  const pinned = pinRunSpec(goldenSpecSubject(), DURABLE);
  return {
    ...pinned,
    spec: { ...pinned.spec, budgets: { ...pinned.spec.budgets, maxChildren: 4 } },
  };
};

describe('child runs: grants ⊆ parent (P6.3, shared table)', () => {
  it('the table and this build agree on the deepest level', () => {
    expect(table.maxDepth).to.equal(CHILD_MAX_DEPTH);
  });

  for (const testCase of table.cases) {
    it(`${testCase.id}: ${testCase.behaviour}`, () => {
      const decision = childGrants(
        parentOf(testCase.parent),
        testCase.request,
        testCase.depth,
      );
      expect(decision.refusal).to.deep.equal(testCase.expect.refusal);
      if (testCase.expect.grants !== undefined) {
        expect(decision.grants).to.deep.equal(testCase.expect.grants);
      }
    });
  }
});

describe('child runs: spec and identity', () => {
  it('CS-1: the child spec is the parent pin narrowed, as a child on the loop', () => {
    const parent = parentPin();
    const decision = childGrants(
      parent.spec,
      { tools: ['jira@inst-42/search_issues'], maxTurns: 5 },
      1,
    );
    if (decision.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(parent, decision.grants);
    expect(runSpecSchema.safeParse(child.spec).success).to.equal(true);
    expect(child.spec.runKind).to.equal('child');
    expect(child.spec.executor).to.deep.equal({ kind: 'loop' });
    expect(child.spec.principalRef).to.equal(parent.spec.principalRef);
    expect(child.spec.model).to.deep.equal(parent.spec.model);
    expect(child.spec.budgets).to.deep.equal({
      ...parent.spec.budgets,
      maxTurns: 5,
      maxChildren: 0,
    });
    expect(child.capabilitiesDigest).to.equal(parent.capabilitiesDigest);
    expect(child.spec.extensions?.['x.pipeshub']?.['via']).to.equal('pat');
    expect(childGrantDigest(child.spec)).to.equal(childGrantDigest(child.spec));
    expect(childGrantDigest(child.spec)).to.not.equal(
      childGrantDigest(parent.spec),
    );
  });

  it("SS2 FW-3: a child of a shared session's run does not inherit the parent's history audience", () => {
    const pinned = parentPin();
    const audience = { sessionRole: 'collaborator', ownerRef: 'p_owner', ownRuns: ['run-0'] };
    const parent: PinnedRunSpec = {
      ...pinned,
      spec: {
        ...pinned.spec,
        extensions: {
          ...pinned.spec.extensions,
          'x.pipeshub': { ...pinned.spec.extensions?.['x.pipeshub'], audience },
        },
      },
    } as PinnedRunSpec;
    const decision = childGrants(parent.spec, { tools: [], maxTurns: 3 }, 1);
    if (decision.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(parent, decision.grants);
    expect(child.spec.extensions?.['x.pipeshub']).to.not.have.property('audience');
    expect(child.spec.principalRef).to.equal(parent.spec.principalRef);
  });

  it('CS-3 (found while building): a core spec without the product extension still gets a child', () => {
    const parent = parentPin();
    const core: PinnedRunSpec = {
      ...parent,
      spec: { ...parent.spec, extensions: {} },
    };
    const decision = childGrants(core.spec, { mounts: ['/workspace'] }, 1);
    expect(decision.refusal).to.deep.equal({
      code: 'CHILD_GRANT_TOO_LARGE',
      dimension: 'mounts',
      items: ['/workspace'],
    });
    const plain = childGrants(core.spec, {}, 1);
    if (plain.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(core, plain.grants);
    expect(runSpecSchema.safeParse(child.spec).success).to.equal(true);
    expect(child.spec.extensions).to.deep.equal({});
    expect(childGrantDigest(child.spec)).to.match(/^sha256:/);
  });

  it('CG-8: MCP servers follow the granted tools', () => {
    const parent = parentPin();
    const without = childGrants(parent.spec, { tools: ['web@serper/web_search'] }, 1);
    const withMcp = childGrants(parent.spec, { tools: ['mcp@mcp-9/list_prs'] }, 1);
    if (without.grants === null || withMcp.grants === null) {
      expect.fail('expected grants');
    }
    expect(
      childRunSpec(parent, without.grants).spec.extensions?.['x.pipeshub']?.['mcp'],
    ).to.deep.equal([]);
    expect(
      (
        childRunSpec(parent, withMcp.grants).spec.extensions?.['x.pipeshub']?.[
          'mcp'
        ] as Array<{ instanceId: string }>
      ).map((server) => server.instanceId),
    ).to.deep.equal(['mcp-9']);
  });

  it('CS-2: the child run id is derived from org, parent and invocation', () => {
    const id = childRunIdFor('org-1', 'run-1', 't1.0');
    expect(id).to.equal(childRunIdFor('org-1', 'run-1', 't1.0'));
    expect(id).to.match(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(childRunIdFor('org-1', 'run-1', 't1.1')).to.not.equal(id);
    expect(childRunIdFor('org-1', 'run-2', 't1.0')).to.not.equal(id);
    expect(childRunIdFor('org-2', 'run-1', 't1.0')).to.not.equal(id);
    for (const vector of table.childRunIds) {
      expect(
        childRunIdFor(vector.orgId, vector.parentRunId, vector.invocationId),
      ).to.equal(vector.childRunId);
    }
  });

  it('child.completed: an answer carries the worker result; anything else Node failure result', () => {
    const result = {
      resultRef: {
        ref: 'inline:e30=',
        hash: `sha256:${'a'.repeat(64)}`,
        size: 2,
      },
      labels: { integrity: 'internal' as const },
    };
    const answered = childCompletedEvent(
      { runId: 'c1' },
      { status: 'completed', outcome: 'answer', result },
    );
    expect(answered.data).to.deep.equal({
      childRunId: 'c1',
      resultRef: result.resultRef,
      labels: result.labels,
      outcome: 'answered',
    });
    const asked = childCompletedEvent(
      { runId: 'c1' },
      { status: 'completed', outcome: 'needs_input', result },
    );
    expect(asked.data['outcome']).to.equal('needs_input');
    const failed = childCompletedEvent(
      { runId: 'c1' },
      { status: 'failed', errorType: 'Not A Machine Name' },
    );
    expect(failed.data).to.include({ outcome: 'failed', errorType: 'worker_failed' });
    for (const event of [answered, asked, failed]) {
      expect(() =>
        validateJournalAppend({
          orgId: 'org-1',
          sessionId: 's-1',
          streamId: 's-1',
          runId: 'run-1',
          writer: 'node',
          events: [event],
        }),
      ).to.not.throw();
    }
  });

  it('CH-4: the worker never sees Node child bookkeeping (its run model is strict)', () => {
    const run = {
      ...goldenSpecSubject().run,
      parent: { runId: 'p', invocationId: 't1.0', depth: 1 },
      children: { spawned: 1, open: 1 },
      suspendedFor: 'join' as const,
    } satisfies HarnessRun;
    const seen = workerRun(run) as unknown as Record<string, unknown>;
    expect(seen).to.not.have.any.keys('parent', 'children', 'suspendedFor');
  });
});

describe('AD-1: maxChildren per agent definition', () => {
  it('pins 0 without a definition grant and the definition value with one', () => {
    const subject = goldenSpecSubject();
    expect(resolveRunSpec(subject, DURABLE).budgets.maxChildren).to.equal(
      RUN_SPEC_BUDGETS.maxChildren,
    );
    expect(RUN_SPEC_BUDGETS.maxChildren).to.equal(0);
    expect(
      resolveRunSpec(
        { ...subject, agentDefinition: agentDefinition({ maxChildren: 3 }) },
        DURABLE,
      ).budgets.maxChildren,
    ).to.equal(3);
    expect(
      resolveRunSpec({ ...subject, agentDefinition: null }, DURABLE).budgets
        .maxChildren,
    ).to.equal(0);
  });

  it('NK-3 (K-L): a deep run pins the mode default unless the definition says otherwise', () => {
    const subject = goldenSpecSubject();
    const inMode = (chatMode: string, maxChildren?: number) =>
      resolveRunSpec(
        {
          ...subject,
          run: {
            ...subject.run,
            input: {
              ...subject.run.input!,
              model: { ...subject.run.input!.model, chatMode },
            },
          },
          agentDefinition: agentDefinition(
            maxChildren === undefined ? {} : { maxChildren },
          ),
        },
        DURABLE,
      ).budgets.maxChildren;
    expect(DEEP_MAX_CHILDREN).to.equal(MAX_CHILDREN_PER_RUN);
    expect(inMode('deep')).to.equal(DEEP_MAX_CHILDREN);
    expect(inMode('DEEP')).to.equal(DEEP_MAX_CHILDREN);
    expect(inMode('deep', 2)).to.equal(2);
    expect(inMode('deep', 0)).to.equal(0);
    expect(inMode('auto')).to.equal(0);
    expect(inMode('react')).to.equal(0);
    expect(inMode('auto', 3)).to.equal(3);
  });
});

describe('NK-1 (K-L): a child pins its sub-agent definition', () => {
  const definition = {
    role: 'jira',
    domainAgent: 'jira_agent',
    toolNames: ['jira_agent'],
  };

  it('pins the request definition, never the parent one, and requires deep_mode', () => {
    const parent = parentPin();
    const decision = childGrants(parent.spec, { maxTokens: 250_000 }, 1);
    if (decision.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(parent, decision.grants, definition);
    expect(runSpecSchema.safeParse(child.spec).success).to.equal(true);
    expect(child.spec.extensions?.['x.pipeshub']?.['subAgent']).to.deep.equal(
      definition,
    );
    expect(child.spec.budgets.maxTokens).to.equal(250_000);
    expect(childFeatures(child)).to.deep.equal(['child_runs', 'deep_mode']);

    const grandchild = childRunSpec(child, decision.grants);
    expect(grandchild.spec.extensions?.['x.pipeshub']).to.not.have.property(
      'subAgent',
    );
    expect(childFeatures(grandchild)).to.deep.equal(['child_runs']);
  });

  it('refuses a definition on a core spec, which has no product extension to pin it in', () => {
    const parent = parentPin();
    const core: PinnedRunSpec = {
      ...parent,
      spec: { ...parent.spec, extensions: {} },
    };
    const plain = childGrants(core.spec, {}, 1);
    if (plain.grants === null) expect.fail('expected a grant');
    expect(() => childRunSpec(core, plain.grants!, definition))
      .to.throw()
      .with.property('code', 'SPEC_UNRESOLVABLE');
  });

  it('the grant digest names the token share', () => {
    const parent = parentPin();
    const a = childGrants(parent.spec, { maxTokens: 250_000 }, 1);
    const b = childGrants(parent.spec, { maxTokens: 500_000 }, 1);
    if (a.grants === null || b.grants === null) expect.fail('expected grants');
    expect(childGrantDigest(childRunSpec(parent, a.grants).spec)).to.not.equal(
      childGrantDigest(childRunSpec(parent, b.grants).spec),
    );
  });
});

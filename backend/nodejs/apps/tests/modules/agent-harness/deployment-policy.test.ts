/**
 * CPD-6 CPD6-14: the deployment's own layers of a pin. The flow rule from the
 * environment by the shared vector (`flow-settings.json`, read by Python's
 * `flow_rule` too); the approvals document's `default` and `agents[*]`, which
 * fail closed where the worker does.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { InvalidEnvFlagError } from '../../../src/libs/utils/env-flags';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { orgPolicyLayers } from './support/org-policy-layers';
import { flowSettings } from '../../../src/modules/agent-harness/config';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import {
  deploymentFlow,
  deploymentLayers,
  todayApprovals,
} from '../../../src/modules/agent-harness/domain/deployment-policy';
import { PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import type { EffectiveApprovals, FlowRule } from '../../../src/modules/agent-harness/domain/approvals';
import { approvalsWithin } from '../../../src/modules/agent-harness/domain/policy/approval-rules';
import type { PolicyLayer } from '../../../src/modules/control-plane';

interface FlowCase {
  id: string;
  env: Record<string, string>;
  rule?: FlowRule | null;
  invalid?: string[];
  refusesToStart?: boolean;
}

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/flow-settings.json'), 'utf8'),
) as { cases: FlowCase[] };

const ORG = 'org-1';
const AGENT = 'a7';

/** The `approvals` dimension a run of (ORG, agent) resolves to from the layers a pin folds for it. */
const pinnedApprovals = (layers: ReadonlyMap<string, PolicyLayer>, agentKey: string | null): unknown => {
  const scopes = [
    'deployment',
    'deployment:flow',
    ...(agentKey !== null ? [`deployment:agent:${agentKey}`] : []),
    `org:${ORG}`,
    ...(agentKey !== null ? [`agent:${agentKey}`] : []),
  ];
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    ...scopes.flatMap((scope) => {
      const layer = layers.get(scope);
      return layer === undefined ? [] : [{ kind: layer.scope.kind, values: layer }];
    }),
  ]);
  expect(resolved.refusal).to.equal(null);
  return resolved.config?.dimensions['approvals']?.value ?? { classes: { read: 'allow', workspace: 'allow', write: 'allow', execute: 'allow' }, tools: {} };
};

describe('CPD-6 the deployment layer of a pin (CPD6-14)', () => {
  for (const vector of VECTORS.cases) {
    it(`${vector.id}: the flow rule of ${JSON.stringify(vector.env)}`, () => {
      if (vector.refusesToStart === true) {
        // A refusal to start is the hardened profile's (ADR-OPS-07); compatibility reads the default with a warning.
        expect(() => flowSettings({ ...vector.env, PIPESHUB_HARDENED: 'true' })).to.throw(InvalidEnvFlagError, 'HARNESS_FLOW_GUARD');
        return;
      }
      expect(deploymentFlow(flowSettings(vector.env))).to.deep.equal({ rule: vector.rule, invalid: vector.invalid });
    });
  }

  // The deployment's own sections only: an edition that keeps the org's rules in the document pins them with its
  // own layers and checks the same parity in its own tests.
  const DOCUMENTS: Array<[string, unknown]> = [
    ['no document', null],
    ['the operator asks for writes everywhere', { default: { writes: 'ask' } }],
    ['the operator denies one tool of one agent', { agents: { [AGENT]: { tools: { 'jira@i1/create_issue': 'deny' } } } }],
    [
      'every deployment layer set',
      {
        default: { writes: 'deny', flow: { level: 'deny', above: 'private' } },
        agents: { [AGENT]: { classes: { workspace: 'ask' }, tools: { t1: 'deny' }, flow: { level: 'ask' } } },
      },
    ],
    ['a default the schema refuses', { default: { writes: 'sometimes' } }],
    ['an agent rule the schema refuses', { agents: { [AGENT]: { tools: 7 } } }],
    ['a document refused as a whole', { default: { writes: 'allow' }, extra: true }],
    ['a document that is not an object', 'x'],
    ['agents that are not a map', { agents: 'x' }],
  ];
  const FLOWS: Array<FlowRule | null> = [null, { level: 'ask', above: 'public' }, { level: 'deny', above: 'private' }];

  for (const [name, document] of DOCUMENTS) {
    for (const flow of FLOWS) {
      for (const agentKey of [null, AGENT]) {
        it(`the pin decides as the worker does today: ${name}, flow ${flow?.level ?? 'off'}, ${agentKey ?? 'no agent'}`, async () => {
          const approvals = new InMemoryConfigDocumentStore<unknown>();
          if (document !== null) await approvals.replace(null, document);
          const layers = await orgPolicyLayers({ approvals, flow })(ORG);
          const pinned = pinnedApprovals(layers, agentKey) as EffectiveApprovals;
          const today = todayApprovals(document, ORG, agentKey, flow);
          expect(approvalsWithin(pinned, today), 'never looser than the worker').to.equal(true);
          expect(pinned).to.deep.equal(today);
        });
      }
    }
  }

  it('the deployment layers are keyed for the pin and the live layer: default, flow, one per agent', () => {
    const layers = deploymentLayers(
      { default: { writes: 'ask' }, agents: { a1: { writes: 'deny' }, a2: null } },
      { level: 'ask', above: 'public' },
    );
    expect([...layers.keys()].sort()).to.deep.equal(['deployment', 'deployment:agent:a1', 'deployment:flow']);
    expect(layers.get('deployment:flow')).to.deep.equal({
      schemaVersion: 1,
      scope: { kind: 'deployment' },
      approvals: { flow: { level: 'ask', above: 'public' } },
    });
    expect(deploymentLayers(null, null).size).to.equal(0);
  });

  it('with the policy store as the record the deployment layers still come from the approvals document', async () => {
    const approvals = new InMemoryConfigDocumentStore<unknown>();
    await approvals.replace(null, ({ default: { writes: 'ask' }, orgs: { [ORG]: { writes: 'deny' } } }));
    const stored: PolicyLayer = { schemaVersion: 1, scope: { kind: 'org', id: ORG }, approvals: { classes: { read: 'ask' } } };
    const layers = await orgPolicyLayers({
      approvals,
      flow: null,
      store: {
        scopesOf: async (_orgId: string, kind: string) => (kind === 'org' ? [`org:${ORG}`] : []),
        readMany: async () =>
          new Map([
            [`org:${ORG}`, { status: 'ok', layer: stored, revision: 1 }],
            ['agent:gone', { status: 'ok', layer: null, revision: 0 }],
            ['agent:bad', { status: 'unavailable', code: 'CP_UNAVAILABLE' }],
          ]),
      } as never,
    })(ORG);
    expect([...layers.keys()].sort()).to.deep.equal(['deployment', `org:${ORG}`]);
    expect(layers.get(`org:${ORG}`)).to.deep.equal(stored);
  });
});
